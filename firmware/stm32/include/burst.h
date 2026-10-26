/*
 * Flanken-Oszi (#BURST, PST-Diag 1): zeichnet Rohflanken (vor den Software-
 * Filtern) mit 32-bit-Zeitstempel auf, wahlweise mit Trigger auf eine Periode,
 * die um mehr als pct % vom Mittel der 8 vorherigen abweicht.
 *
 * Hardwareunabhaengig (Host-Test: test/test_diag.c). burst_edge() wird aus dem
 * TIM2-Interrupt aufgerufen (capture.c), die Ausgabe macht diag.c.
 */
#ifndef BURST_H
#define BURST_H

#include <stdbool.h>
#include <stdint.h>

#include "config.h"

#define BURST_CH_I 1u          /* Zuendung */
#define BURST_CH_R 2u          /* Rolle */

#define BURST_EDGE_F 0u        /* fallend */
#define BURST_EDGE_R 1u        /* steigend */
#define BURST_EDGE_B 2u        /* beide */

#define BURST_HIST 8u          /* Perioden fuer das Trigger-Mittel */
#define BURST_MAX_GAP_MS 23000u   /* < 2^31 Ticks bei 90 MHz (23,8 s) */

typedef enum {
    BURST_IDLE = 0,
    BURST_ARMED,               /* mit Trigger: zirkulaer, wartet auf Ausloesung */
    BURST_FILLING,             /* sofort bzw. nach Ausloesung: Rest aufnehmen */
    BURST_DONE,                /* fertig, Ausgabe durch die Hauptschleife */
} burst_state_t;

typedef struct {
    uint8_t ch_mask;           /* BURST_CH_I | BURST_CH_R */
    uint8_t edge;              /* BURST_EDGE_* */
    uint16_t trig_pct;         /* 0 = ohne Trigger, 1..1000 */
    uint32_t n;                /* Anzahl Flanken 1..BURST_MAX */
    uint32_t pre;              /* Flanken vor dem Trigger */
} burst_cfg_t;

typedef struct {
    uint32_t last_ts;
    uint32_t per[BURST_HIST];
    uint64_t sum;
    uint8_t have_last, cnt, idx;
} burst_hist_t;

typedef struct {
    burst_cfg_t cfg;
    volatile burst_state_t state;
    uint32_t count;            /* geschriebene Eintraege insgesamt */
    uint32_t wpos;             /* naechster Schreibplatz (count % n) */
    uint32_t remaining;        /* Rest nach Ausloesung */
    volatile uint32_t lost;
    uint32_t last_ms;
    burst_hist_t hist[2][2];   /* [Kanal][Pegel] */
    uint32_t ts[BURST_MAX];
    uint8_t meta[BURST_MAX];   /* Bit 0 = Pegel nach der Flanke, Bit 1 = Rolle */
} burst_t;

/* Befehlszeile "<ch> <n> <edge> [T<pct>] [P<pre>]" pruefen. NULL = ok, sonst Grund. */
const char *burst_parse(const char *args, burst_cfg_t *cfg);

void burst_arm(burst_t *b, const burst_cfg_t *cfg);
void burst_stop(burst_t *b);

/* Flanke melden: ch 0 = Zuendung, 1 = Rolle; lvl = Pegel nach der Flanke.
 * Rueckgabe true, wenn die Aufnahme damit fertig ist. */
bool burst_edge(burst_t *b, unsigned ch, unsigned lvl, uint32_t ts, uint32_t now_ms);

/* Fuer die Ausgabe (nur in BURST_DONE): Eintrag i (0 = aeltester). */
void burst_entry(const burst_t *b, uint32_t i, unsigned *ch, unsigned *lvl, uint32_t *ts);
int32_t burst_trig_idx(const burst_t *b);

#endif /* BURST_H */
