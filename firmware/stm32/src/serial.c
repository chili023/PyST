#include "serial.h"

#include <string.h>

#include "stm32f4xx_hal.h"

#define TX_SIZE 4096u
#define RX_SIZE 256u

static uint8_t tx_buf[TX_SIZE];
static volatile uint32_t tx_head, tx_tail;
static uint8_t rx_buf[RX_SIZE];
static volatile uint32_t rx_head, rx_tail;
static volatile uint32_t rx_tick;
static volatile bool break_seen;
static uint32_t tx_dropped;

void serial_init(uint32_t baud)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART2_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin = GPIO_PIN_2 | GPIO_PIN_3;
    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = GPIO_PULLUP;
    g.Speed = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &g);

    USART2->CR1 = 0;
    USART2->BRR = (HAL_RCC_GetPCLK1Freq() + baud / 2u) / baud;   /* Oversampling 16 */
    USART2->CR2 = 0;
    USART2->CR3 = USART_CR3_EIE;
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE;

    HAL_NVIC_SetPriority(USART2_IRQn, 2, 0);
    HAL_NVIC_EnableIRQ(USART2_IRQn);
}

void USART2_IRQHandler(void)
{
    uint32_t sr = USART2->SR;

    if (sr & (USART_SR_RXNE | USART_SR_ORE | USART_SR_FE | USART_SR_NE)) {
        uint8_t b = (uint8_t)USART2->DR;       /* SR dann DR lesen loescht Fehlerflags */
        if ((sr & USART_SR_FE) && b == 0u) {
            break_seen = true;                 /* Break = Rahmenfehler mit lauter Nullen */
        } else if (sr & USART_SR_RXNE) {
            uint32_t h = rx_head;
            if (h - rx_tail < RX_SIZE) {
                rx_buf[h % RX_SIZE] = b;
                rx_head = h + 1u;
            }
            rx_tick = HAL_GetTick();
        }
    }

    if ((USART2->CR1 & USART_CR1_TXEIE) && (sr & USART_SR_TXE)) {
        uint32_t t = tx_tail;
        if (t == tx_head) {
            USART2->CR1 &= ~USART_CR1_TXEIE;
        } else {
            USART2->DR = tx_buf[t % TX_SIZE];
            tx_tail = t + 1u;
        }
    }
}

bool serial_write(const void *data, size_t len)
{
    const uint8_t *p = data;
    uint32_t h = tx_head;
    if (TX_SIZE - (h - tx_tail) < len) {
        tx_dropped++;
        return false;
    }
    for (size_t i = 0; i < len; i++)
        tx_buf[(h + i) % TX_SIZE] = p[i];
    __DMB();
    tx_head = h + (uint32_t)len;
    USART2->CR1 |= USART_CR1_TXEIE;            /* atomar genug: ISR setzt nur zurueck, wenn leer */
    return true;
}

bool serial_write_str(const char *s)
{
    return serial_write(s, strlen(s));
}

int serial_read(void)
{
    uint32_t t = rx_tail;
    if (t == rx_head)
        return -1;
    int b = rx_buf[t % RX_SIZE];
    rx_tail = t + 1u;
    return b;
}

uint32_t serial_last_rx_tick(void) { return rx_tick; }

bool serial_take_break(void)
{
    bool b = break_seen;
    break_seen = false;
    return b;
}

uint32_t serial_tx_dropped(void) { return tx_dropped; }
