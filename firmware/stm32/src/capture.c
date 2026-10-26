#include "capture.h"

#include "config.h"
#include "freq_calc.h"
#include "settings.h"
#include "stm32f4xx_hal.h"

#define FRAME_QUEUE_SIZE 16u   /* Reserve, falls die Hauptschleife kurz blockiert */

static edge_chan_t ign;
static edge_chan_t roll;

typedef struct {
    uint32_t t;                /* Zeitstempel der Telegrammgrenze */
    uint32_t ign_rej, roll_rej;/* Verwerf-Zaehler zu diesem Zeitpunkt */
} frame_mark_t;

static volatile frame_mark_t frame_q[FRAME_QUEUE_SIZE];
static volatile uint32_t frame_q_head;
static uint32_t frame_q_tail;
static volatile uint32_t frame_period;
static volatile uint32_t frames_dropped;

static frame_mark_t last_frame;
static bool have_last_frame;
static float timer_hz;
static float hold_ign, hold_roll;          /* letzte bekannte Frequenz (Modus D) */

/* Flanken-Oszi */
static burst_t g_burst;
static uint8_t edge_cfg[2];                /* konfigurierte Flanke: 1 = steigend */
static volatile uint8_t both[2];           /* Kanal laeuft auf beiden Flanken */
static volatile uint8_t level[2];          /* Pegel nach der letzten Flanke */
static volatile uint32_t last_raw[2];      /* Zeitstempel der letzten Rohflanke */

static uint32_t us_to_ticks(uint32_t us) { return (uint32_t)((uint64_t)timer_hz * us / 1000000u); }
static uint32_t ms_to_ticks(uint32_t ms) { return (uint32_t)((uint64_t)timer_hz * ms / 1000u); }

float capture_timer_hz(void) { return timer_hz; }

void capture_set_rate_mhz(uint32_t rate_mhz)
{
    frame_period = (uint32_t)((uint64_t)timer_hz * 1000u / rate_mhz);
}

static unsigned pin_level(unsigned ch)
{
    return ch == 0 ? (GPIOA->IDR & GPIO_PIN_0) != 0u : (GPIOB->IDR & GPIO_PIN_9) != 0u;
}

static bool burst_running(void)
{
    burst_state_t st = g_burst.state;
    return st == BURST_ARMED || st == BURST_FILLING;
}

/* Capture-Flanken aus Einstellungen und laufendem Burst. Nur mit gesperrtem
 * TIM2-Interrupt bzw. aus dem Interrupt aufrufen. */
static void apply_polarity(void)
{
    uint32_t ccer = TIM2->CCER & ~(TIM_CCER_CC1P | TIM_CCER_CC1NP | TIM_CCER_CC2P | TIM_CCER_CC2NP);
    static const uint32_t p[2] = {TIM_CCER_CC1P, TIM_CCER_CC2P};
    static const uint32_t np[2] = {TIM_CCER_CC1NP, TIM_CCER_CC2NP};
    bool run = burst_running();
    for (unsigned ch = 0; ch < 2; ch++) {
        bool b = run && (g_burst.cfg.ch_mask & (1u << ch)) &&
                 (g_burst.cfg.edge == BURST_EDGE_B || g_burst.cfg.edge != edge_cfg[ch]);
        if (b) {
            ccer |= p[ch] | np[ch];            /* beide Flanken */
            if (!both[ch])
                level[ch] = (uint8_t)pin_level(ch);
        } else if (!edge_cfg[ch]) {
            ccer |= p[ch];                     /* fallend */
        }
        both[ch] = b;
    }
    TIM2->CCER = ccer;
}

static void setup_channel(edge_chan_t *c, uint32_t min_us, uint32_t timeout_ms,
                          uint32_t rel_pct, uint32_t min_periods)
{
    c->min_ticks = us_to_ticks(min_us);
    c->timeout_ticks = ms_to_ticks(timeout_ms);
    c->max_span_ticks = ms_to_ticks((uint32_t)SET(S_MAX_AVG_SPAN_MS));
    c->rel_pct = rel_pct;
    c->min_periods = min_periods;
}

static void restart_channel(edge_chan_t *c)
{
    /* neue Folge: Flanken vor der Aenderung nicht mehr verwenden */
    c->seg_start = c->head;
    c->last_period = 0u;
    c->prev_period = 0u;
    c->p_hat = 0u;
    c->win_out = 0u;
}

void capture_apply_settings(void)
{
    NVIC_DisableIRQ(TIM2_IRQn);

    setup_channel(&ign, (uint32_t)SET(S_IGN_MIN_PERIOD_US), (uint32_t)SET(S_TIMEOUT_IGN_MS),
                  (uint32_t)SET(S_IGN_LOCKOUT_PCT), (uint32_t)SET(S_IGN_MIN_PERIODS));
    setup_channel(&roll, (uint32_t)SET(S_ROLL_MIN_PERIOD_US), (uint32_t)SET(S_TIMEOUT_ROLL_MS),
                  (uint32_t)SET(S_ROLL_LOCKOUT_PCT), (uint32_t)SET(S_ROLL_MIN_PERIODS));
    if (ign.win_lo_pct != (uint32_t)SET(S_IGN_WIN_LO_PCT)) {
        ign.p_hat = 0u;
        ign.win_out = 0u;
    }
    ign.win_lo_pct = (uint32_t)SET(S_IGN_WIN_LO_PCT);
    ign.win_hi_pct = (uint32_t)SET(S_IGN_WIN_HI_PCT);
    roll.win_lo_pct = 0u;

    uint8_t e0 = (uint8_t)SET(S_EDGE_IGN), e1 = (uint8_t)SET(S_EDGE_ROLL);
    if (e0 != edge_cfg[0]) restart_channel(&ign);
    if (e1 != edge_cfg[1]) restart_channel(&roll);
    edge_cfg[0] = e0;
    edge_cfg[1] = e1;

    /* Digitalfilter: IC1F/IC2F (Kanal kurz aus) */
    uint32_t f = (uint32_t)SET(S_HW_FILTER) & 15u;
    uint32_t ccmr1 = (1u << TIM_CCMR1_CC1S_Pos) | (f << TIM_CCMR1_IC1F_Pos) |
                     (1u << TIM_CCMR1_CC2S_Pos) | (f << TIM_CCMR1_IC2F_Pos);
    if (TIM2->CCMR1 != ccmr1) {
        uint32_t en = TIM2->CCER & (TIM_CCER_CC1E | TIM_CCER_CC2E);
        TIM2->CCER &= ~en;
        TIM2->CCMR1 = ccmr1;
        TIM2->CCER |= en;
    }

    /* Pull-ups PA0/PB9 */
    uint32_t pu = SET(S_PULLUP) ? 1u : 0u;
    GPIOA->PUPDR = (GPIOA->PUPDR & ~(3u << (0 * 2))) | (pu << (0 * 2));
    GPIOB->PUPDR = (GPIOB->PUPDR & ~(3u << (9 * 2))) | (pu << (9 * 2));

    apply_polarity();
    NVIC_EnableIRQ(TIM2_IRQn);
}

void capture_init(void)
{
    /* TIM2 haengt an APB1; bei APB1-Teiler != 1 laeuft der Timer mit 2*PCLK1 */
    uint32_t pclk1 = HAL_RCC_GetPCLK1Freq();
    timer_hz = (float)((RCC->CFGR & RCC_CFGR_PPRE1_2) ? 2u * pclk1 : pclk1);

    capture_set_rate_mhz((uint32_t)SET(S_RATE_HZ) * 1000u);

    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_TIM2_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = SET(S_PULLUP) ? GPIO_PULLUP : GPIO_NOPULL;
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
    TIM2->CCMR1 = 0;
    TIM2->CCMR2 = 0;
    TIM2->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E;
    edge_cfg[0] = (uint8_t)SET(S_EDGE_IGN);
    edge_cfg[1] = (uint8_t)SET(S_EDGE_ROLL);
    capture_apply_settings();                  /* Filter, Flanken, Kanalparameter */
    TIM2->EGR = TIM_EGR_UG;
    TIM2->SR = 0;
    TIM2->CCR3 = frame_period;
    TIM2->DIER = TIM_DIER_CC1IE | TIM_DIER_CC2IE | TIM_DIER_CC3IE;

    HAL_NVIC_SetPriority(TIM2_IRQn, 0, 0);     /* hoechste Prioritaet */
    HAL_NVIC_EnableIRQ(TIM2_IRQn);
    TIM2->CR1 = TIM_CR1_CEN;
}

static void on_edge(unsigned ch, edge_chan_t *c, uint32_t t, bool overcapture)
{
    unsigned lvl;
    if (both[ch]) {
        /* beide Flanken: Pegel wechselt mit jeder (gefilterten) Flanke; nach
         * einer verpassten Flanke den Pin lesen (bestmoeglich) */
        lvl = overcapture ? pin_level(ch) : !level[ch];
        level[ch] = (uint8_t)lvl;
        last_raw[ch] = t;
    } else {
        lvl = edge_cfg[ch];
    }
    if (overcapture) {
        c->overcaptures++;
        if (burst_running())
            g_burst.lost++;
    }
    if (lvl == edge_cfg[ch])
        edge_push(c, t);
    if (burst_running() && burst_edge(&g_burst, ch, lvl, t, HAL_GetTick()))
        apply_polarity();                      /* fertig: zurueck auf normale Flanke */
}

/* Pegel bei beiden Flanken nachfuehren: ist der Pin ueber laenger als die
 * Filterzeit (max. 2,8 us) stabil und keine Flanke anhaengig, gilt der Pinpegel. */
#define RESYNC_TICKS 400u                      /* 4,4 us */

static void resync_levels(void)
{
    if (!both[0] && !both[1])
        return;
    uint32_t c0 = TIM2->CNT;
    unsigned s1[2] = {pin_level(0), pin_level(1)};
    while (TIM2->CNT - c0 < RESYNC_TICKS) {
    }
    uint32_t sr = TIM2->SR;
    static const uint32_t flag[2] = {TIM_SR_CC1IF, TIM_SR_CC2IF};
    for (unsigned ch = 0; ch < 2; ch++) {
        if (both[ch] && !(sr & flag[ch]) && pin_level(ch) == s1[ch] &&
            c0 - last_raw[ch] > RESYNC_TICKS)
            level[ch] = (uint8_t)s1[ch];
    }
}

void TIM2_IRQHandler(void)
{
    uint32_t sr = TIM2->SR;

    /* Lesen von CCRx loescht CCxIF */
    if (sr & TIM_SR_CC1IF)
        on_edge(0, &ign, TIM2->CCR1, (sr & TIM_SR_CC1OF) != 0u);
    if (sr & TIM_SR_CC2IF)
        on_edge(1, &roll, TIM2->CCR2, (sr & TIM_SR_CC2OF) != 0u);
    if (sr & (TIM_SR_CC1OF | TIM_SR_CC2OF)) {
        if ((sr & TIM_SR_CC1OF) && !(sr & TIM_SR_CC1IF)) ign.overcaptures++;
        if ((sr & TIM_SR_CC2OF) && !(sr & TIM_SR_CC2IF)) roll.overcaptures++;
        TIM2->SR = ~(sr & (TIM_SR_CC1OF | TIM_SR_CC2OF));
    }

    if (sr & TIM_SR_CC3IF) {
        TIM2->SR = ~TIM_SR_CC3IF;
        uint32_t t = TIM2->CCR3;
        uint32_t next = t + frame_period;
        if ((int32_t)(TIM2->CNT - next) >= 0) {
            /* Interrupt kam zu spaet (z.B. CPU stand beim Flash-Loeschen):
             * verpasste Telegrammgrenzen ueberspringen statt 47 s zu warten */
            uint32_t missed = (TIM2->CNT - t) / frame_period;
            next = t + (missed + 1u) * frame_period;
            frames_dropped += missed;
        }
        TIM2->CCR3 = next;
        uint32_t h = frame_q_head;
        if (h - frame_q_tail < FRAME_QUEUE_SIZE) {
            volatile frame_mark_t *m = &frame_q[h % FRAME_QUEUE_SIZE];
            m->t = t;
            m->ign_rej = ign.rejected;
            m->roll_rej = roll.rejected;
            frame_q_head = h + 1u;
        } else {
            frames_dropped++;
        }
        resync_levels();
    }
}

bool capture_poll(capture_frame_t *out)
{
    if (frame_q_tail == frame_q_head)
        return false;

    frame_mark_t m;
    {
        volatile frame_mark_t *q = &frame_q[frame_q_tail % FRAME_QUEUE_SIZE];
        m.t = q->t;
        m.ign_rej = q->ign_rej;
        m.roll_rej = q->roll_rej;
    }
    frame_q_tail++;

    if (!have_last_frame) {
        last_frame = m;
        have_last_frame = true;
        return false;                          /* erstes Intervall unvollstaendig */
    }
    uint32_t t0 = last_frame.t, t1 = m.t;
    out->ign_rej = m.ign_rej - last_frame.ign_rej;
    out->roll_rej = m.roll_rej - last_frame.roll_rej;
    last_frame = m;

    __disable_irq();
    uint32_t ign_head = ign.head, ign_seg = ign.seg_start;
    uint32_t roll_head = roll.head, roll_seg = roll.seg_start;
    __enable_irq();

    out->rate_hz = timer_hz / (float)(t1 - t0);
    out->ign_hz = edge_freq(&ign, ign_head, ign_seg, t0, t1, timer_hz);
    out->roll_hz = edge_freq(&roll, roll_head, roll_seg, t0, t1, timer_hz);
    edge_window(&ign, ign_head, ign_seg, t0, t1, timer_hz, &hold_ign, &out->ign_w);
    edge_window(&roll, roll_head, roll_seg, t0, t1, timer_hz, &hold_roll, &out->roll_w);
    out->tick_us = 1e6f / timer_hz;
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
    s->ign_gaps = ign.gaps;
}

void capture_burst_start(const burst_cfg_t *cfg)
{
    NVIC_DisableIRQ(TIM2_IRQn);
    burst_arm(&g_burst, cfg);
    apply_polarity();
    NVIC_EnableIRQ(TIM2_IRQn);
}

void capture_burst_stop(void)
{
    NVIC_DisableIRQ(TIM2_IRQn);
    burst_stop(&g_burst);
    apply_polarity();
    NVIC_EnableIRQ(TIM2_IRQn);
}

const burst_t *capture_burst(void) { return &g_burst; }
