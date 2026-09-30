#include "analog.h"

#include "config.h"
#include "stm32f4xx_hal.h"

#define SAMPLES 8u

static ADC_HandleTypeDef hadc1;

void analog_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_ADC1_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_ANALOG;
    g.Pull = GPIO_NOPULL;
    g.Pin = GPIO_PIN_0;
    HAL_GPIO_Init(GPIOB, &g);
    g.Pin = GPIO_PIN_1;
    HAL_GPIO_Init(GPIOA, &g);

    hadc1.Instance = ADC1;
    hadc1.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;    /* 22,5 MHz */
    hadc1.Init.Resolution = ADC_RESOLUTION_12B;
    hadc1.Init.ScanConvMode = DISABLE;
    hadc1.Init.ContinuousConvMode = DISABLE;
    hadc1.Init.DiscontinuousConvMode = DISABLE;
    hadc1.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
    hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
    hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
    hadc1.Init.NbrOfConversion = 1;
    hadc1.Init.DMAContinuousRequests = DISABLE;
    hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
    HAL_ADC_Init(&hadc1);
}

uint16_t analog_read(unsigned ch)
{
    ADC_ChannelConfTypeDef c = {0};
    c.Channel = (ch == 2) ? ADC_CHANNEL_1 : ADC_CHANNEL_8;   /* PA1 = IN1, PB0 = IN8 */
    c.Rank = 1;
    c.SamplingTime = ADC_SAMPLETIME_480CYCLES;
    HAL_ADC_ConfigChannel(&hadc1, &c);

    uint32_t sum = 0;
    for (unsigned i = 0; i < SAMPLES; i++) {
        HAL_ADC_Start(&hadc1);
        if (HAL_ADC_PollForConversion(&hadc1, 2) == HAL_OK)
            sum += HAL_ADC_GetValue(&hadc1);
    }
    HAL_ADC_Stop(&hadc1);
    return (uint16_t)(sum / SAMPLES);
}

float analog_to_afr(uint16_t raw)
{
    return AFR_AT_0V + (AFR_AT_FULLSCALE - AFR_AT_0V) * (float)raw / 4096.0f;
}
