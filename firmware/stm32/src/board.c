#include "board.h"

#include "stm32f4xx_hal.h"

static const char *clock_source = "?";

const char *board_clock_source(void) { return clock_source; }

/* 180 MHz aus PLL. Quelle der Reihe nach: 8-MHz-Quarz (wie bisherige
 * Firmware), 8 MHz vom ST-LINK (Bypass), interner RC (+/-1 %, Notbetrieb). */
static int clock_try(uint32_t hse_state)
{
    RCC_OscInitTypeDef o = {0};
    o.PLL.PLLState = RCC_PLL_ON;
    o.PLL.PLLN = 180;
    o.PLL.PLLP = RCC_PLLP_DIV2;
    o.PLL.PLLQ = 8;
    o.PLL.PLLR = 2;
    if (hse_state == RCC_HSE_OFF) {
        o.OscillatorType = RCC_OSCILLATORTYPE_HSI;
        o.HSIState = RCC_HSI_ON;
        o.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
        o.PLL.PLLSource = RCC_PLLSOURCE_HSI;
        o.PLL.PLLM = 8;                        /* 16 MHz / 8 = 2 MHz */
    } else {
        o.OscillatorType = RCC_OSCILLATORTYPE_HSE;
        o.HSEState = hse_state;
        o.PLL.PLLSource = RCC_PLLSOURCE_HSE;
        o.PLL.PLLM = 4;                        /* 8 MHz / 4 = 2 MHz */
    }
    return HAL_RCC_OscConfig(&o) == HAL_OK;
}

static void clock_init(void)
{
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    if (clock_try(RCC_HSE_ON)) {
        clock_source = "HSE-Quarz 8 MHz";
    } else {
        RCC_OscInitTypeDef off = {0};
        off.OscillatorType = RCC_OSCILLATORTYPE_HSE;
        off.HSEState = RCC_HSE_OFF;
        off.PLL.PLLState = RCC_PLL_NONE;
        HAL_RCC_OscConfig(&off);
        if (clock_try(RCC_HSE_BYPASS))
            clock_source = "HSE ST-LINK 8 MHz";
        else if (clock_try(RCC_HSE_OFF))
            clock_source = "HSI intern (ungenau!)";
        else
            return;                            /* bleibt auf HSI 16 MHz ohne PLL */
    }

    HAL_PWREx_EnableOverDrive();

    RCC_ClkInitTypeDef c = {0};
    c.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    c.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    c.AHBCLKDivider = RCC_SYSCLK_DIV1;         /* 180 MHz */
    c.APB1CLKDivider = RCC_HCLK_DIV4;          /* 45 MHz, Timer 90 MHz */
    c.APB2CLKDivider = RCC_HCLK_DIV2;          /* 90 MHz */
    HAL_RCC_ClockConfig(&c, FLASH_LATENCY_5);
}

static void out(GPIO_TypeDef *port, uint32_t pins, GPIO_PinState level)
{
    GPIO_InitTypeDef g = {0};
    HAL_GPIO_WritePin(port, pins, level);
    g.Pin = pins;
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(port, &g);
}

static void safe_pins(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    /* Lambda-Board (spaeter): Heizungen sicher AUS, CJ125 im Reset */
    out(GPIOA, GPIO_PIN_11, GPIO_PIN_RESET);   /* Heater 1 (TIM1_CH4) */
    out(GPIOC, GPIO_PIN_7, GPIO_PIN_RESET);    /* Heater 2 (TIM3_CH2) */
    out(GPIOA, GPIO_PIN_12, GPIO_PIN_RESET);   /* CJ125_1_RESET */

    /* Alle Chip-Selects am SPI1-Bus inaktiv (High), damit nur der gerade
     * gelesene MAX6675 den Bus treibt. PA13 (EGT1) folgt verzoegert. */
    out(GPIOA, GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 | GPIO_PIN_15, GPIO_PIN_SET);
    out(GPIOC, GPIO_PIN_6, GPIO_PIN_SET);      /* EGT2 CS */

    out(GPIOA, GPIO_PIN_5, GPIO_PIN_RESET);    /* LED LD2 */
}

void board_init(void)
{
    HAL_Init();
    clock_init();
    safe_pins();
}

void board_led(int on)
{
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

/* Unabhaengiger Watchdog, ca. 1 s: haengt die Firmware (z.B. durch
 * Zuendstoerungen), startet sie neu statt einzufrieren. */
void board_watchdog_start(void)
{
    IWDG->KR = 0x5555;
    IWDG->PR = 3;                              /* LSI 32 kHz / 32 */
    IWDG->RLR = 1000;                          /* ~1 s */
    IWDG->KR = 0xAAAA;
    IWDG->KR = 0xCCCC;
}

void board_watchdog_kick(void)
{
    IWDG->KR = 0xAAAA;
}
