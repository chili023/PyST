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

    printf("\n%s (%d Fehler)\n", failures ? "FEHLGESCHLAGEN" : "ALLE TESTS OK", failures);
    return failures ? 1 : 0;
}
