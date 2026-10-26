/*
 * Drehzahlerfassung und Telegrammtakt auf TIM2 (32 bit, 90 MHz):
 *   CH1 = Zuendung (PA0), CH2 = Rolle (PB9), CH3 = Telegrammtakt (ohne Pin).
 * Impulse und Telegrammgrenzen liegen damit auf derselben Zeitbasis.
 * Ab 1.1.0 zusaetzlich: Diagnose je Intervall (Modus D) und Flanken-Oszi
 * (#BURST, dabei ggf. Capture auf beiden Flanken).
 */
#ifndef CAPTURE_H
#define CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

#include "burst.h"
#include "freq_calc.h"

typedef struct {
    float rate_hz;      /* tatsaechliche Telegrammrate dieses Intervalls */
    float ign_hz;       /* Zuendimpulse pro Sekunde */
    float roll_hz;      /* Rollengeber-Impulse pro Sekunde */
    /* Diagnose (Modus D) */
    edge_win_t ign_w, roll_w;
    uint32_t ign_rej, roll_rej;   /* im Intervall verworfene Flanken */
    float tick_us;                /* Dauer eines Timer-Ticks [us] */
} capture_frame_t;

typedef struct {
    uint32_t ign_edges, roll_edges;
    uint32_t ign_rejected, roll_rejected;
    uint32_t ign_overcaptures, roll_overcaptures;
    uint32_t frames_dropped;
    uint32_t ign_gaps;
} capture_stats_t;

void capture_init(void);
float capture_timer_hz(void);
/* Telegrammrate aendern (wirkt ab dem naechsten Intervall). */
void capture_set_rate_mhz(uint32_t rate_mhz);
/* Einstellungen (g_set) auf Kanaele, Flanken, Filter und Pull-ups anwenden. */
void capture_apply_settings(void);
/* Liefert true, wenn ein Telegramm-Intervall abgeschlossen ist. */
bool capture_poll(capture_frame_t *out);
void capture_get_stats(capture_stats_t *s);

/* Flanken-Oszi */
void capture_burst_start(const burst_cfg_t *cfg);
void capture_burst_stop(void);           /* Aufnahme abbrechen bzw. nach Ausgabe freigeben */
const burst_t *capture_burst(void);

#endif /* CAPTURE_H */
