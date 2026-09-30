/*
 * Drehzahlerfassung und Telegrammtakt auf TIM2 (32 bit, 90 MHz):
 *   CH1 = Zuendung (PA0), CH2 = Rolle (PB9), CH3 = Telegrammtakt (ohne Pin).
 * Impulse und Telegrammgrenzen liegen damit auf derselben Zeitbasis.
 */
#ifndef CAPTURE_H
#define CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    float rate_hz;      /* tatsaechliche Telegrammrate dieses Intervalls */
    float ign_hz;       /* Zuendimpulse pro Sekunde */
    float roll_hz;      /* Rollengeber-Impulse pro Sekunde */
} capture_frame_t;

typedef struct {
    uint32_t ign_edges, roll_edges;
    uint32_t ign_rejected, roll_rejected;
    uint32_t ign_overcaptures, roll_overcaptures;
    uint32_t frames_dropped;
} capture_stats_t;

void capture_init(void);
float capture_timer_hz(void);

/* Telegrammrate aendern (wirkt ab dem naechsten Intervall). */
void capture_set_rate_mhz(uint32_t rate_mhz);

/* Liefert true, wenn ein Telegramm-Intervall abgeschlossen ist. */
bool capture_poll(capture_frame_t *out);

void capture_get_stats(capture_stats_t *s);

#endif /* CAPTURE_H */
