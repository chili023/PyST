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
/* 1 = Watchdog fuer Flash-Loeschen auf ca. 30 s verlaengern, 0 = wieder ca. 1 s */
void board_watchdog_long(int on);

#endif /* BOARD_H */
