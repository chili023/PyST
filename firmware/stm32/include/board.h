/*
 * Takt, sichere Pin-Grundzustaende, LED und Watchdog der PST-Platine
 * (Nucleo-F446RE + Aufsteckplatine v2xx).
 */
#ifndef BOARD_H
#define BOARD_H

void board_init(void);
const char *board_clock_source(void);

void board_led(int on);
void board_watchdog_start(void);
void board_watchdog_kick(void);

#endif /* BOARD_H */
