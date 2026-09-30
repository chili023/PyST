/* System-Interrupts. TIM2 und USART2 liegen in capture.c bzw. serial.c. */
#include "stm32f4xx_hal.h"

void SysTick_Handler(void)
{
    HAL_IncTick();
}

void NMI_Handler(void)
{
}

/* Bei schweren Fehlern anhalten; der Watchdog startet nach ~1 s neu. */
void HardFault_Handler(void) { for (;;) {} }
void MemManage_Handler(void) { for (;;) {} }
void BusFault_Handler(void) { for (;;) {} }
void UsageFault_Handler(void) { for (;;) {} }
