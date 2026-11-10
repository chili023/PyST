#include "analog.h"

#include "config.h"
#include "settings.h"
#include "stm32f4xx_hal.h"

/*
 * Ab 1.2.0: ADC1 wandelt ununterbrochen beide Eingaenge (Scan PB0 = IN8, PA1 = IN1, je 480 Takte bei
 * 22,5 MHz = 21,9 us, ein Durchgang 43,7 us). DMA2 Stream0 schreibt die Ergebnisse im Kreis in buf[],
 * ohne Interrupt. analog_read() mittelt die letzten SAMPLES Werte eines Eingangs und kehrt sofort zurueck.
 *
 * Bis 1.1.0 stellte analog_read() den ADC fuer jeden Aufruf auf den Kanal um und wartete auf 8 Wandlungen
 * (ca. 175 us je Kanal). Eine zusaetzliche DMA-Abtastung fuer #AI haette denselben ADC1 umgestellt und
 * die Scan-Reihenfolge der DMA zerstoert. Jetzt gibt es nur noch diese eine Abtastung: AFR (afr_src), die
 * STM-LabVIEW (ADC-Rohwerte) und die A-Zeilen (#AI) lesen dieselben Werte. TIM2 (Drehzahl) ist nicht
 * beteiligt, die Capture-ISR bekommt keinen weiteren Interrupt neben sich.
 */

#define SAMPLES 16u                      /* je Eingang gemittelt, ca. 0,7 ms */
#define SCAN 2u                          /* Eingaenge je Durchgang */

static volatile uint16_t buf[SAMPLES * SCAN];  /* [2k] = PB0 (ADC1), [2k+1] = PA1 (ADC2) */

/* ADC aus, DMA neu ausrichten, ADC an und Scan starten. Auch nach einem Ueberlauf: nur mit ADON = 0
 * beginnt der naechste Scan sicher bei Rang 1 (= buf[0]). */
static void start(void)
{
    ADC1->CR2 = 0;
    DMA2_Stream0->CR = 0;
    while (DMA2_Stream0->CR & DMA_SxCR_EN) {
    }
    DMA2->LIFCR = 0x3Du;                 /* Flags von Stream 0 loeschen */
    DMA2_Stream0->PAR = (uint32_t)&ADC1->DR;
    DMA2_Stream0->M0AR = (uint32_t)buf;
    DMA2_Stream0->NDTR = SAMPLES * SCAN;
    DMA2_Stream0->FCR = 0;               /* Direktbetrieb */
    DMA2_Stream0->CR = (0u << DMA_SxCR_CHSEL_Pos)          /* Kanal 0 = ADC1 */
                     | DMA_SxCR_MSIZE_0 | DMA_SxCR_PSIZE_0  /* 16 bit */
                     | DMA_SxCR_MINC | DMA_SxCR_CIRC;       /* Peripherie -> Speicher, im Kreis */
    DMA2_Stream0->CR |= DMA_SxCR_EN;

    ADC1->SR = 0;
    ADC1->CR2 = ADC_CR2_ADON;
    for (volatile unsigned i = 0; i < 1000u; i++) {        /* t_STAB 3 us (>= 20 us bei 180 MHz) */
    }
    ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_CONT | ADC_CR2_DMA | ADC_CR2_DDS;
    ADC1->CR2 |= ADC_CR2_SWSTART;
}

void analog_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_ADC1_CLK_ENABLE();
    __HAL_RCC_DMA2_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_ANALOG;
    g.Pull = GPIO_NOPULL;
    g.Pin = GPIO_PIN_0;
    HAL_GPIO_Init(GPIOB, &g);
    g.Pin = GPIO_PIN_1;
    HAL_GPIO_Init(GPIOA, &g);

    ADC1->CR2 = 0;
    ADC123_COMMON->CCR = ADC_CCR_ADCPRE_0;                  /* PCLK2 / 4 = 22,5 MHz */
    ADC1->CR1 = ADC_CR1_SCAN;                               /* 12 bit */
    ADC1->SMPR2 = (7u << ADC_SMPR2_SMP1_Pos) | (7u << ADC_SMPR2_SMP8_Pos);   /* 480 Takte */
    ADC1->SQR1 = (SCAN - 1u) * ADC_SQR1_L_0;
    ADC1->SQR3 = 8u | (1u << ADC_SQR3_SQ2_Pos);            /* Rang 1 = IN8 (PB0), Rang 2 = IN1 (PA1) */
    start();
    HAL_Delay(2);                                           /* Puffer einmal ganz fuellen (0,7 ms) */
}

uint16_t analog_read(unsigned ch)
{
    if (ADC1->SR & ADC_SR_OVR)
        start();                         /* Ueberlauf (DMA kam nicht nach): Reihenfolge neu ausrichten */
    unsigned k = (ch == 2) ? 1u : 0u;    /* 1 = PB0, 2 = PA1 */
    uint32_t sum = 0;
    for (unsigned i = 0; i < SAMPLES; i++)
        sum += buf[i * SCAN + k];
    return (uint16_t)(sum / SAMPLES);
}

float analog_to_afr(uint16_t raw)
{
    float a0 = (float)SET(S_AFR_0V) / 100.0f, fs = (float)SET(S_AFR_FS) / 100.0f;
    return a0 + (fs - a0) * (float)raw / 4096.0f;
}
