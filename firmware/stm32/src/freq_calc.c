#include "freq_calc.h"

void edge_push(edge_chan_t *c, uint32_t t)
{
    if (c->head != 0u) {
        uint32_t dt = t - c->last_ts;          /* unsigned: 32-bit-Ueberlauf egal */
        if (dt < c->min_ticks) {
            c->rejected++;
            return;
        }
        if (dt > c->timeout_ticks) {
            /* Pause: neue Folge beginnen, alte Perioden nicht mehr verwenden */
            c->seg_start = c->head;
            c->last_period = 0u;
            c->prev_period = 0u;
        } else {
            /* Doppelimpulsfilter: Bezug ist die groessere der beiden letzten gueltigen Perioden.
             * Kommt pro Umdrehung ein Stoerimpuls an fester Stelle (z.B. bei 40 % der Periode),
             * rastet der Filter so nach spaetestens zwei Umdrehungen auf den Hauptimpuls ein. */
            uint32_t ref = c->last_period > c->prev_period ? c->last_period : c->prev_period;
            if (c->rel_pct != 0u && ref != 0u && (uint64_t)dt * 100u < (uint64_t)ref * c->rel_pct) {
                c->rejected++;
                return;
            }
            c->prev_period = c->last_period;
            c->last_period = dt;
        }
    }
    c->ts[c->head & EDGE_RING_MASK] = t;
    c->last_ts = t;
    c->head++;
}

static inline uint32_t ts_at(const edge_chan_t *c, uint32_t idx)
{
    return c->ts[idx & EDGE_RING_MASK];
}

float edge_freq(const edge_chan_t *c, uint32_t head, uint32_t seg_start,
                uint32_t t0, uint32_t t1, float timer_hz)
{
    /* aeltester sicher lesbarer Index (Reserve gegen Ueberschreiben waehrend
     * der Berechnung) */
    uint32_t lo = seg_start;
    if (head - lo > EDGE_RING_SIZE - 16u)
        lo = head - (EDGE_RING_SIZE - 16u);

    /* j = 1 + Index der juengsten Flanke mit ts <= t1 */
    uint32_t j = head;
    while (j > lo && (int32_t)(ts_at(c, j - 1u) - t1) > 0)
        j--;
    if (j == lo)
        return 0.0f;                           /* keine Flanke in dieser Folge */

    uint32_t i_last = j - 1u;
    uint32_t t_last = ts_at(c, i_last);
    uint32_t age = t1 - t_last;
    if (age > c->timeout_ticks)
        return 0.0f;

    /* n = Flanken im Intervall (t0, t1] */
    uint32_t k = j;
    while (k > lo && (int32_t)(ts_at(c, k - 1u) - t0) > 0)
        k--;
    uint32_t n = j - k;

    uint32_t avail = i_last - lo;              /* Perioden vor der juengsten Flanke */
    uint32_t p = n > c->min_periods ? n : c->min_periods;
    if (p > avail)
        p = avail;
    while (p > n && p > 1u && t_last - ts_at(c, i_last - p) > c->max_span_ticks)
        p--;
    if (p == 0u)
        return 0.0f;                           /* nur eine einzelne Flanke */

    uint32_t span = t_last - ts_at(c, i_last - p);
    float f = (float)p * timer_hz / (float)span;

    /* Keine neue Flanke: die Drehzahl kann hoechstens 1/Alter sein
     * -> faellt beim Ausrollen stetig gegen 0 statt stehen zu bleiben. */
    if (n == 0u && age > 0u) {
        float f_age = timer_hz / (float)age;
        if (f_age < f)
            f = f_age;
    }
    return f;
}
