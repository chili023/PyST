#include "egt.h"

#include "config.h"
#include "stm32f4xx_hal.h"

#define READ_INTERVAL_MS 250u
#define PA13_DELAY_MS    2000u   /* SWD nach Reset noch 2 s erreichbar lassen */

static SPI_HandleTypeDef hspi1;
static float value[2];
static bool valid[2];
static bool pa13_ready;
static uint32_t last_read;

static const bool enabled[2] = {EGT1_ENABLE, EGT2_ENABLE};

bool egt_enabled(unsigned ch) { return ch < 2 && enabled[ch]; }

static void cs(unsigned ch, GPIO_PinState s)
{
    if (ch == 0)
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_13, s);
    else
        HAL_GPIO_WritePin(GPIOC, GPIO_PIN_6, s);
}

void egt_init(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_SPI1_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin = GPIO_PIN_3 | GPIO_PIN_4 | GPIO_PIN_5;    /* SCK, MISO, MOSI */
    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF5_SPI1;
    HAL_GPIO_Init(GPIOB, &g);

    /* MAX6675: max. 4,3 MHz, Daten aendern sich mit fallender Flanke -> Mode 0 */
    hspi1.Instance = SPI1;
    hspi1.Init.Mode = SPI_MODE_MASTER;
    hspi1.Init.Direction = SPI_DIRECTION_2LINES;
    hspi1.Init.DataSize = SPI_DATASIZE_16BIT;
    hspi1.Init.CLKPolarity = SPI_POLARITY_LOW;
    hspi1.Init.CLKPhase = SPI_PHASE_1EDGE;
    hspi1.Init.NSS = SPI_NSS_SOFT;
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_32;   /* 90 MHz / 32 = 2,8 MHz */
    hspi1.Init.FirstBit = SPI_FIRSTBIT_MSB;
    hspi1.Init.TIMode = SPI_TIMODE_DISABLE;
    hspi1.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
    HAL_SPI_Init(&hspi1);
    /* CS-Pins werden in board_init() bereits auf High (inaktiv) gesetzt,
     * PA13 erst verzoegert in egt_poll(). */
}

static void configure_pa13(void)
{
    GPIO_InitTypeDef g = {0};
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_13, GPIO_PIN_SET);
    g.Pin = GPIO_PIN_13;
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOA, &g);
    pa13_ready = true;
}

static void read_channel(unsigned ch)
{
    uint16_t raw = 0;
    cs(ch, GPIO_PIN_RESET);
    HAL_StatusTypeDef st = HAL_SPI_Receive(&hspi1, (uint8_t *)&raw, 1, 5);
    cs(ch, GPIO_PIN_SET);                        /* startet naechste Wandlung */

    /* Bit 15 immer 0, Bit 2 = Thermoelement offen, Bit 1 = Geraete-ID (0).
     * 0xFFFF bzw. 0x0000 = kein Chip am Bus. */
    if (st != HAL_OK || raw == 0xFFFFu || raw == 0u || (raw & 0x8002u) != 0u || (raw & 0x0004u)) {
        valid[ch] = false;
        return;
    }
    value[ch] = (float)(raw >> 3) * 0.25f;
    valid[ch] = true;
}

void egt_poll(void)
{
    uint32_t now = HAL_GetTick();

    if (enabled[0] && !pa13_ready && now >= PA13_DELAY_MS)
        configure_pa13();

    if (now - last_read < READ_INTERVAL_MS)
        return;
    last_read = now;

    if (enabled[0] && pa13_ready)
        read_channel(0);
    if (enabled[1])
        read_channel(1);
}

bool egt_get(unsigned ch, float *t_c)
{
    if (!egt_enabled(ch) || !valid[ch])
        return false;
    *t_c = value[ch];
    return true;
}
