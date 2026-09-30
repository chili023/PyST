#include "capture.h"

#include "config.h"
#include "freq_calc.h"
#include "stm32f4xx_hal.h"

#define FRAME_QUEUE_SIZE 16u   /* Reserve, falls die Hauptschleife kurz blockiert */

static edge_chan_t ign;
static edge_chan_t roll;

static volatile uint32_t frame_q[FRAME_QUEUE_SIZE];
static volatile uint32_t frame_q_head;
static uint32_t frame_q_tail;
static volatile uint32_t frame_period;
static volatile uint32_t frames_dropped;

static uint32_t last_frame_ts;
static bool have_last_frame;
static float timer_hz;

static uint32_t us_to_ticks(uint32_t us) { return (uint32_t)((uint64_t)timer_hz * us / 1000000u); }
static uint32_t ms_to_ticks(uint32_t ms) { return (uint32_t)((uint64_t)timer_hz * ms / 1000u); }

float capture_timer_hz(void) { return timer_hz; }

void capture_set_rate_mhz(uint32_t rate_mhz)
{
    frame_period = (uint32_t)((uint64_t)timer_hz * 1000u / rate_mhz);
}

static void setup_channel(edge_chan_t *c, uint32_t min_us, uint32_t timeout_ms,
                          uint32_t rel_pct, uint32_t min_periods)
{
    c->min_ticks = us_to_ticks(min_us);
    c->timeout_ticks = ms_to_ticks(timeout_ms);
    c->max_span_ticks = ms_to_ticks(MAX_AVG_SPAN_MS);
    c->rel_pct = rel_pct;
    c->min_periods = min_periods;
}

void capture_init(void)
{
    /* TIM2 haengt an APB1; bei APB1-Teiler != 1 laeuft der Timer mit 2*PCLK1 */
    uint32_t pclk1 = HAL_RCC_GetPCLK1Freq();
    timer_hz = (float)((RCC->CFGR & RCC_CFGR_PPRE1_2) ? 2u * pclk1 : pclk1);

    setup_channel(&ign, IGN_MIN_PERIOD_US, IGN_TIMEOUT_MS, IGN_REL_LOCKOUT_PCT, IGN_MIN_PERIODS);
    setup_channel(&roll, ROLL_MIN_PERIOD_US, ROLL_TIMEOUT_MS, ROLL_REL_LOCKOUT_PCT, ROLL_MIN_PERIODS);
    capture_set_rate_mhz(FRAME_RATE_HZ * 1000u);

    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_TIM2_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = INPUT_PULLUP ? GPIO_PULLUP : GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Alternate = GPIO_AF1_TIM2;
    g.Pin = GPIO_PIN_0;                        /* PA0 = TIM2_CH1 Zuendung */
    HAL_GPIO_Init(GPIOA, &g);
    g.Pin = GPIO_PIN_9;                        /* PB9 = TIM2_CH2 Rolle */
    HAL_GPIO_Init(GPIOB, &g);

    TIM2->CR1 = 0;
    TIM2->PSC = 0;
    TIM2->ARR = 0xFFFFFFFFu;
    /* CH1/CH2: Input Capture auf TI1/TI2 mit Digitalfilter, CH3: Output Compare "frozen" */
    TIM2->CCMR1 = (1u << TIM_CCMR1_CC1S_Pos) | (INPUT_HW_FILTER << TIM_CCMR1_IC1F_Pos) |
                  (1u << TIM_CCMR1_CC2S_Pos) | (INPUT_HW_FILTER << TIM_CCMR1_IC2F_Pos);
    TIM2->CCMR2 = 0;
    TIM2->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E |
                 (IGN_EDGE_RISING ? 0u : TIM_CCER_CC1P) |
                 (ROLL_EDGE_RISING ? 0u : TIM_CCER_CC2P);
    TIM2->EGR = TIM_EGR_UG;
    TIM2->SR = 0;
    TIM2->CCR3 = frame_period;
    TIM2->DIER = TIM_DIER_CC1IE | TIM_DIER_CC2IE | TIM_DIER_CC3IE;

    HAL_NVIC_SetPriority(TIM2_IRQn, 0, 0);     /* hoechste Prioritaet */
    HAL_NVIC_EnableIRQ(TIM2_IRQn);
    TIM2->CR1 = TIM_CR1_CEN;
}

void TIM2_IRQHandler(void)
{
    uint32_t sr = TIM2->SR;

    /* Lesen von CCRx loescht CCxIF */
    if (sr & TIM_SR_CC1IF)
        edge_push(&ign, TIM2->CCR1);
    if (sr & TIM_SR_CC2IF)
        edge_push(&roll, TIM2->CCR2);
    if (sr & (TIM_SR_CC1OF | TIM_SR_CC2OF)) {
        if (sr & TIM_SR_CC1OF) ign.overcaptures++;
        if (sr & TIM_SR_CC2OF) roll.overcaptures++;
        TIM2->SR = ~(sr & (TIM_SR_CC1OF | TIM_SR_CC2OF));
    }

    if (sr & TIM_SR_CC3IF) {
        TIM2->SR = ~TIM_SR_CC3IF;
        uint32_t t = TIM2->CCR3;
        TIM2->CCR3 = t + frame_period;
        uint32_t h = frame_q_head;
        if (h - frame_q_tail < FRAME_QUEUE_SIZE) {
            frame_q[h % FRAME_QUEUE_SIZE] = t;
            frame_q_head = h + 1u;
        } else {
            frames_dropped++;
        }
    }
}

bool capture_poll(capture_frame_t *out)
{
    if (frame_q_tail == frame_q_head)
        return false;

    uint32_t t1 = frame_q[frame_q_tail % FRAME_QUEUE_SIZE];
    frame_q_tail++;

    if (!have_last_frame) {
        last_frame_ts = t1;
        have_last_frame = true;
        return false;                          /* erstes Intervall unvollstaendig */
    }
    uint32_t t0 = last_frame_ts;
    last_frame_ts = t1;

    __disable_irq();
    uint32_t ign_head = ign.head, ign_seg = ign.seg_start;
    uint32_t roll_head = roll.head, roll_seg = roll.seg_start;
    __enable_irq();

    out->rate_hz = timer_hz / (float)(t1 - t0);
    out->ign_hz = edge_freq(&ign, ign_head, ign_seg, t0, t1, timer_hz);
    out->roll_hz = edge_freq(&roll, roll_head, roll_seg, t0, t1, timer_hz);
    return true;
}

void capture_get_stats(capture_stats_t *s)
{
    s->ign_edges = ign.head;
    s->roll_edges = roll.head;
    s->ign_rejected = ign.rejected;
    s->roll_rejected = roll.rejected;
    s->ign_overcaptures = ign.overcaptures;
    s->roll_overcaptures = roll.overcaptures;
    s->frames_dropped = frames_dropped;
}
