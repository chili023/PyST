/*
 * WildBugChilGru PST – Messelektronik auf STM32F446RE (Nucleo-64)
 *
 * Erfasst Zuend- und Rollenfrequenz, Klimadaten und Abgastemperatur und
 * sendet sie im Protokoll des Arduino-Mega-Sketches an LabVIEW 3.x.
 * Die private STM-LabVIEW 0.2.0 wird ebenfalls bedient (Auto-Erkennung).
 */
#include "analog.h"
#include "board.h"
#include "capture.h"
#include "config.h"
#include "egt.h"
#include "env_sensor.h"
#include "protocol.h"
#include "serial.h"
#include "stm32f4xx_hal.h"

static void led_update(void)
{
    /* Blinkt langsam = bereit, schnell = Messung laeuft */
    uint32_t period = protocol_streaming() ? 200u : 1000u;
    board_led((HAL_GetTick() % period) < period / 2u);
}

int main(void)
{
    board_init();
    serial_init(PC_BAUD);
    env_init();
    egt_init();
    analog_init();
    protocol_init();
    capture_init();
    board_watchdog_start();

    for (;;) {
        board_watchdog_kick();
        protocol_poll();

        capture_frame_t frame;
        while (capture_poll(&frame))
            protocol_on_frame(&frame);

        egt_poll();
        led_update();
    }
}
