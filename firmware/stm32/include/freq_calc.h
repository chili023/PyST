/*
 * Frequenzberechnung aus Flanken-Zeitstempeln (hardwareunabhaengig,
 * damit sie auch auf dem PC getestet werden kann: test/test_freq_calc.c).
 */
#ifndef FREQ_CALC_H
#define FREQ_CALC_H

#include <stdint.h>

#define EDGE_RING_BITS 10u
#define EDGE_RING_SIZE (1u << EDGE_RING_BITS)
#define EDGE_RING_MASK (EDGE_RING_SIZE - 1u)

typedef struct {
    /* vom Interrupt geschrieben */
    uint32_t ts[EDGE_RING_SIZE];      /* Zeitstempel gueltiger Flanken */
    volatile uint32_t head;           /* Anzahl gueltiger Flanken (laeuft durch) */
    volatile uint32_t seg_start;      /* Index der ersten Flanke nach einer Pause */
    volatile uint32_t last_ts;
    volatile uint32_t last_period;
    volatile uint32_t prev_period;    /* vorletzte gueltige Periode (Doppelimpulsfilter) */
    volatile uint32_t rejected;       /* durch Sperrzeit/Doppelimpulsfilter verworfen */
    volatile uint32_t overcaptures;   /* Flanke verpasst (Hardware) */

    /* Parameter in Timer-Ticks */
    uint32_t min_ticks;
    uint32_t timeout_ticks;
    uint32_t max_span_ticks;
    uint32_t rel_pct;
    uint32_t min_periods;
} edge_chan_t;

/* Aus dem Capture-Interrupt aufrufen. */
void edge_push(edge_chan_t *c, uint32_t t);

/* Frequenz [Hz] fuer das Intervall (t0, t1]. head/seg_start muessen
 * konsistent gelesen sein (Interrupts kurz sperren). */
float edge_freq(const edge_chan_t *c, uint32_t head, uint32_t seg_start,
                uint32_t t0, uint32_t t1, float timer_hz);

#endif /* FREQ_CALC_H */
