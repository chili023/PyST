/*
 * Host-Test der Frequenzberechnung (laeuft auf Mac/Linux/Windows):
 *   cc -std=c11 -O2 -Iinclude src/freq_calc.c test/test_freq_calc.c -lm -o /tmp/t && /tmp/t
 * Simuliert Impulse mit 90-MHz-Zeitstempeln und Telegramme mit 60 Hz.
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "freq_calc.h"

#define F_TIM 90000000.0
#define RATE 60.0

static int failures;

static void check(const char *name, double got, double want, double tol)
{
    int ok = fabs(got - want) <= tol;
    printf("%-52s %s  got %.4f want %.4f\n", name, ok ? "OK  " : "FAIL", got, want);
    if (!ok)
        failures++;
}

static void chan_setup(edge_chan_t *c, double min_us, double timeout_ms, unsigned rel, unsigned minp)
{
    memset(c, 0, sizeof *c);
    c->min_ticks = (uint32_t)(F_TIM * min_us / 1e6);
    c->timeout_ticks = (uint32_t)(F_TIM * timeout_ms / 1e3);
    c->max_span_ticks = (uint32_t)(F_TIM * 0.25);
    c->rel_pct = rel;
    c->min_periods = minp;
}

/* Simulation: Impulse mit Frequenz f(t) werden bis t_end eingespeist,
 * Telegramme alle 1/RATE ausgewertet. Liefert die letzte Frequenz. */
typedef double (*freq_fn)(double t);

static double run(edge_chan_t *c, freq_fn f, double t_end, uint32_t t_offset,
                  double *out_series, int max_series, int *n_series)
{
    double phase = 0.0, t = 0.0, dt = 1e-6;
    double next_frame = 1.0 / RATE;
    uint32_t prev_frame_ticks = t_offset;
    double last = 0.0;
    int n = 0;
    while (t < t_end) {
        double fr = f(t);
        phase += fr * dt;
        if (phase >= 1.0) {
            phase -= 1.0;
            edge_push(c, t_offset + (uint32_t)(t * F_TIM));
        }
        t += dt;
        if (t >= next_frame) {
            uint32_t t1 = t_offset + (uint32_t)(next_frame * F_TIM);
            last = edge_freq(c, c->head, c->seg_start, prev_frame_ticks, t1, (float)F_TIM);
            if (out_series && n < max_series)
                out_series[n++] = last;
            prev_frame_ticks = t1;
            next_frame += 1.0 / RATE;
        }
    }
    if (n_series)
        *n_series = n;
    return last;
}

static double f_const725(double t) { (void)t; return 725.0; }
static double f_ramp(double t) { return 100.0 + 400.0 * t; }            /* 100 -> 900 Hz in 2 s */
static double f_low(double t) { (void)t; return 7.0; }
static double f_stop(double t) { return t < 1.0 ? 300.0 : 0.0; }
static double f_ign50(double t) { (void)t; return 50.0; }

int main(void)
{
    edge_chan_t c;
    double series[400];
    int n;

    chan_setup(&c, 20, 1000, 0, 1);
    check("Rolle 725 Hz konstant", run(&c, f_const725, 1.0, 0, NULL, 0, NULL), 725.0, 0.05);

    chan_setup(&c, 20, 1000, 0, 1);
    check("Rolle 725 Hz ueber 32-bit-Timerueberlauf", run(&c, f_const725, 1.0, 0xFFFFFFFFu - 45000000u, NULL, 0, NULL), 725.0, 0.05);

    chan_setup(&c, 20, 1000, 0, 1);
    run(&c, f_ramp, 2.0, 0, series, 400, &n);
    /* Mittelwert der Rampe im letzten Intervall (t = 2 - 1/60 .. 2) */
    check("Rampe 100->900 Hz: letztes Intervall", series[n - 1], 100.0 + 400.0 * (2.0 - 0.5 / RATE), 1.0);

    chan_setup(&c, 20, 1000, 0, 1);
    check("Rolle 7 Hz (weniger als 1 Impuls/Telegramm)", run(&c, f_low, 2.0, 0, NULL, 0, NULL), 7.0, 0.05);

    chan_setup(&c, 20, 1000, 0, 1);
    run(&c, f_stop, 2.5, 0, series, 400, &n);
    {
        double v = series[(int)(1.2 * RATE)];     /* 200 ms nach Stopp: faellt, aber noch > 0 */
        check("Stopp: 200 ms danach zwischen 0 und 5 Hz", (v > 0.0 && v <= 5.0) ? 1.0 : 0.0, 1.0, 0.0);
    }
    check("Stopp: nach Timeout exakt 0", series[n - 1], 0.0, 0.0);

    /* Zuendung 50 Hz mit Doppelimpulsen 0,5 ms nach jedem echten Impuls */
    chan_setup(&c, 200, 500, 25, 2);
    {
        uint32_t prev = 0;
        double last = 0.0;
        for (int i = 1; i <= 200; i++) {
            double t = i / 50.0;
            edge_push(&c, (uint32_t)(t * F_TIM));
            edge_push(&c, (uint32_t)((t + 0.0005) * F_TIM));     /* Doppelimpuls (> Sperrzeit) */
            uint32_t t1 = (uint32_t)((t + 0.001) * F_TIM);
            last = edge_freq(&c, c.head, c.seg_start, prev, t1, (float)F_TIM);
            prev = t1;
        }
        check("Zuendung 50 Hz mit Doppelimpulsen", last, 50.0, 0.01);
        /* der allererste Doppelimpuls kommt durch (noch keine Periode bekannt) */
        check("Zuendung: Doppelimpulse verworfen (Anzahl)", (double)c.rejected, 199.0, 0.0);
    }

    chan_setup(&c, 500, 500, 25, 2);
    check("Zuendung 50 Hz = 3000 1/min", run(&c, f_ign50, 1.0, 0, NULL, 0, NULL), 50.0, 0.01);

    /* Muster aus den Laeufen vom 26.05.26: 2. Impuls bei 39 % der Periode, in 70 % der Umdrehungen.
     * 100 Hz = 6000 1/min, Telegramm alle 1/60 s. */
    for (int pct = 25; pct <= 70; pct += 45) {
        chan_setup(&c, 200, 500, (unsigned)pct, 2);
        uint32_t prev = 0;
        double last = 0.0;
        unsigned rnd = 12345;
        const double T = 0.01;
        double next_frame = 1.0 / RATE;
        for (int rev = 0; rev < 300; rev++) {
            double t = 0.1 + rev * T;
            while (next_frame < t) {
                uint32_t t1 = (uint32_t)(next_frame * F_TIM);
                last = edge_freq(&c, c.head, c.seg_start, prev, t1, (float)F_TIM);
                prev = t1;
                next_frame += 1.0 / RATE;
            }
            edge_push(&c, (uint32_t)(t * F_TIM));
            rnd = rnd * 1103515245u + 12345u;
            if ((rnd >> 16) % 10 < 7)
                edge_push(&c, (uint32_t)((t + 0.39 * T) * F_TIM));
        }
        char name[80];
        snprintf(name, sizeof name, "Stoerimpuls bei 39 %%, Filter %d %%: Frequenz", pct);
        if (pct == 70)
            check(name, last, 100.0, 0.05);
        else
            check(name, last > 150.0 ? 1.0 : 0.0, 1.0, 0.0);   /* 25 %: Stoerimpulse kommen durch (erwartet) */
    }

    /* ------------------------------------------ Diagnose-Fenster (Modus D) */
    {
        /* 100 Hz Zuendung, Telegramme mit 60 Hz: Frequenz aus allen Perioden */
        chan_setup(&c, 200, 500, 0, 2);
        edge_win_t w;
        float hold = 0.0f;
        uint32_t prev = 0;
        double next_frame = 1.0 / RATE, hz_min = 1e9, hz_max = 0;
        uint32_t pmin = 0xFFFFFFFFu, pmax = 0;
        int stale = 0;
        for (int i = 1; i <= 100; i++) {
            double t = i * 0.01;
            while (next_frame < t) {
                uint32_t t1 = (uint32_t)(next_frame * F_TIM);
                edge_window(&c, c.head, c.seg_start, prev, t1, (float)F_TIM, &hold, &w);
                if (next_frame > 0.1) {
                    if (w.hz < hz_min) hz_min = w.hz;
                    if (w.hz > hz_max) hz_max = w.hz;
                    if (w.pmin && w.pmin < pmin) pmin = w.pmin;
                    if (w.pmax > pmax) pmax = w.pmax;
                    stale += w.stale;
                }
                prev = t1;
                next_frame += 1.0 / RATE;
            }
            edge_push(&c, (uint32_t)(t * F_TIM));
        }
        check("D: 100 Hz, Frequenz min", hz_min, 100.0, 0.01);
        check("D: 100 Hz, Frequenz max", hz_max, 100.0, 0.01);
        check("D: pmin = 10000 us", pmin / 90.0, 10000.0, 0.1);
        check("D: pmax = 10000 us", pmax / 90.0, 10000.0, 0.1);
        check("D: nie stale (>= 1 Periode je Fenster)", stale, 0, 0);
    }
    {
        /* 7 Hz: Fenster ohne Flanke -> letzter Wert + stale; nach Stopp -> 0 + timeout */
        chan_setup(&c, 20, 1000, 0, 1);
        edge_win_t w;
        float hold = 0.0f;
        uint32_t prev = 0;
        int stale = 0, bad = 0, timeout_end = 0;
        double next_frame = 1.0 / RATE;
        for (int i = 1; i <= 14; i++) {
            double t = i / 7.0;
            while (next_frame < t) {
                uint32_t t1 = (uint32_t)(next_frame * F_TIM);
                edge_window(&c, c.head, c.seg_start, prev, t1, (float)F_TIM, &hold, &w);
                if (next_frame > 0.3) {
                    stale += w.stale;
                    if (fabs(w.hz - 7.0) > 0.01) bad++;
                }
                prev = t1;
                next_frame += 1.0 / RATE;
            }
            edge_push(&c, (uint32_t)(t * F_TIM));
        }
        for (; next_frame < 4.0; next_frame += 1.0 / RATE) {
            uint32_t t1 = (uint32_t)(next_frame * F_TIM);
            edge_window(&c, c.head, c.seg_start, prev, t1, (float)F_TIM, &hold, &w);
            prev = t1;
        }
        timeout_end = w.timeout && w.hz == 0.0f && w.n == 0;
        check("D: 7 Hz meist stale (letzter Wert gehalten)", stale > 80 ? 1 : 0, 1, 0);
        check("D: 7 Hz Wert immer 7,000", bad, 0, 0);
        check("D: nach Stopp > Timeout: 0 + Flag timeout", timeout_end, 1, 0);
    }
    {
        /* Doppelimpuls bei 39 % ohne Filter: D zeigt 200 Hz und pmin/pmax 39/61 % */
        chan_setup(&c, 200, 500, 0, 2);
        edge_win_t w;
        float hold = 0.0f;
        for (int i = 1; i <= 50; i++) {
            double t = i * 0.01;
            edge_push(&c, (uint32_t)(t * F_TIM));
            edge_push(&c, (uint32_t)((t + 0.0039) * F_TIM));
        }
        edge_window(&c, c.head, c.seg_start, (uint32_t)(0.3 * F_TIM), (uint32_t)(0.4001 * F_TIM),
                    (float)F_TIM, &hold, &w);
        check("D: Doppelimpuls ohne Filter: 200 Flanken/s", w.hz, 200.0, 0.5);
        check("D: pmin = 3900 us", w.pmin / 90.0, 3900.0, 0.1);
        check("D: pmax = 6100 us", w.pmax / 90.0, 6100.0, 0.1);
        check("D: n = 20 Flanken in 100 ms", w.n, 20, 0);
    }

    /* ------------------------------------------ Erwartungsfenster (Plan 2.2) */
    {
        /* (a) Stoerimpuls bei 39 % in 70 % der Umdrehungen, Doppelimpulsfilter aus */
        chan_setup(&c, 200, 500, 0, 2);
        c.win_lo_pct = 70;
        c.win_hi_pct = 150;
        uint32_t prev = 0;
        double last = 0.0, next_frame = 1.0 / RATE;
        unsigned rnd = 12345, injected = 0;
        uint32_t rej0 = 0, gaps0 = 0;
        for (int rev = 0; rev < 300; rev++) {
            if (rev == 50) {               /* Einrasten abwarten, danach zaehlen */
                rej0 = c.rejected;
                gaps0 = c.gaps;
                injected = 0;
            }
            double t = 0.1 + rev * 0.01;
            while (next_frame < t) {
                uint32_t t1 = (uint32_t)(next_frame * F_TIM);
                last = edge_freq(&c, c.head, c.seg_start, prev, t1, (float)F_TIM);
                prev = t1;
                next_frame += 1.0 / RATE;
            }
            edge_push(&c, (uint32_t)(t * F_TIM));
            rnd = rnd * 1103515245u + 12345u;
            if ((rnd >> 16) % 10 < 7) {
                edge_push(&c, (uint32_t)((t + 0.0039) * F_TIM));
                injected++;
            }
        }
        check("Fenster 70/150: Stoerimpuls 39 % -> 100 Hz", last, 100.0, 0.05);
        check("Fenster: eingerastet alle Stoerimpulse verworfen", (double)(c.rejected - rej0), (double)injected, 0.0);
        check("Fenster: eingerastet keine Luecken", c.gaps - gaps0, 0, 0);
    }
    {
        /* (b) Aussetzer: jeder 10. Impuls fehlt -> gueltig, als Luecke gezaehlt */
        chan_setup(&c, 200, 500, 0, 2);
        c.win_lo_pct = 70;
        c.win_hi_pct = 150;
        for (int i = 1; i <= 200; i++)
            if (i % 10 != 0)
                edge_push(&c, (uint32_t)(i * 0.01 * F_TIM));
        check("Fenster: Aussetzer als Luecke gezaehlt (19)", c.gaps, 19, 0);
        check("Fenster: Aussetzer nicht verworfen", c.rejected, 0, 0);
        check("Fenster: P bleibt 10 ms", c.p_hat / 90.0, 10000.0, 30.0);
    }
    {
        /* (c) Beschleunigung 3000 -> 12000 1/min (50 -> 200 Hz) in 3 s */
        chan_setup(&c, 200, 500, 0, 2);
        c.win_lo_pct = 70;
        c.win_hi_pct = 150;
        double t = 0.0;
        while (t < 3.0) {
            double f = 50.0 + 50.0 * t;
            t += 1.0 / f;
            edge_push(&c, (uint32_t)(t * F_TIM));
        }
        double f = edge_freq(&c, c.head, c.seg_start, (uint32_t)((t - 0.0166) * F_TIM), (uint32_t)(t * F_TIM), (float)F_TIM);
        check("Fenster: Beschleunigung 50->200 Hz in 3 s, Endwert", f, 200.0, 2.0);
        check("Fenster: Beschleunigung ohne Verwerfen/Luecken", c.rejected + c.gaps, 0, 0);
    }
    {
        /* (d) Drehzahlsprung auf die Haelfte: 2x Luecke, beim 3. Mal neu einrasten */
        chan_setup(&c, 200, 500, 0, 2);
        c.win_lo_pct = 70;
        c.win_hi_pct = 150;
        double t = 0.0;
        for (int i = 0; i < 20; i++) { t += 0.01; edge_push(&c, (uint32_t)(t * F_TIM)); }
        for (int i = 0; i < 20; i++) { t += 0.02; edge_push(&c, (uint32_t)(t * F_TIM)); }
        check("Fenster: Sprung -> 2 Luecken, dann eingerastet", c.gaps, 2, 0);
        check("Fenster: P nach Sprung = 20 ms", c.p_hat / 90.0, 20000.0, 50.0);
    }

    printf("\n%s (%d Fehler)\n", failures ? "FEHLGESCHLAGEN" : "ALLE TESTS OK", failures);
    return failures ? 1 : 0;
}
