#include "burst.h"

#include <string.h>

static const char *skip_sp(const char *s)
{
    while (*s == ' ')
        s++;
    return s;
}

static char up(char c) { return (c >= 'a' && c <= 'z') ? (char)(c - 32) : c; }

/* Zahl bis Leerzeichen/Ende; false bei Muell oder > max */
static bool num(const char **ps, uint32_t max, uint32_t *out)
{
    const char *s = *ps;
    uint32_t v = 0;
    if (*s < '0' || *s > '9')
        return false;
    while (*s >= '0' && *s <= '9') {
        v = v * 10u + (uint32_t)(*s++ - '0');
        if (v > max)
            return false;
    }
    if (*s && *s != ' ')
        return false;
    *ps = s;
    *out = v;
    return true;
}

const char *burst_parse(const char *s, burst_cfg_t *cfg)
{
    memset(cfg, 0, sizeof *cfg);
    s = skip_sp(s);
    switch (up(*s)) {
    case 'I': cfg->ch_mask = BURST_CH_I; break;
    case 'R': cfg->ch_mask = BURST_CH_R; break;
    case 'A': cfg->ch_mask = BURST_CH_I | BURST_CH_R; break;
    default: return "kanal";
    }
    s++;
    if (*s != ' ')
        return "kanal";
    s = skip_sp(s);
    if (!num(&s, BURST_MAX, &cfg->n) || cfg->n == 0u)
        return "anzahl";
    s = skip_sp(s);
    switch (up(*s)) {
    case 'F': cfg->edge = BURST_EDGE_F; break;
    case 'R': cfg->edge = BURST_EDGE_R; break;
    case 'B': cfg->edge = BURST_EDGE_B; break;
    default: return "flanke";
    }
    s++;
    if (*s && *s != ' ')
        return "flanke";

    bool have_pre = false;
    uint32_t pre = 0;
    for (s = skip_sp(s); *s; s = skip_sp(s)) {
        char opt = up(*s++);
        uint32_t v;
        if (opt == 'T') {
            if (!num(&s, 1000u, &v) || v == 0u)
                return "trigger";
            cfg->trig_pct = (uint16_t)v;
        } else if (opt == 'P') {
            if (!num(&s, BURST_MAX, &v))
                return "pre";
            pre = v;
            have_pre = true;
        } else {
            return "parameter";
        }
    }
    if (cfg->trig_pct) {
        cfg->pre = have_pre ? pre : cfg->n / 2u;
        if (cfg->pre >= cfg->n)
            return "pre";
    }
    return NULL;
}

void burst_arm(burst_t *b, const burst_cfg_t *cfg)
{
    b->state = BURST_IDLE;
    b->cfg = *cfg;
    b->count = 0;
    b->wpos = 0;
    b->lost = 0;
    b->last_ms = 0;
    memset(b->hist, 0, sizeof b->hist);
    if (cfg->trig_pct) {
        b->remaining = 0;
        b->state = BURST_ARMED;
    } else {
        b->remaining = cfg->n;
        b->state = BURST_FILLING;
    }
}

void burst_stop(burst_t *b) { b->state = BURST_IDLE; }

/* Periode in den Verlauf; true = weicht um mehr als pct % vom Mittel ab */
static bool hist_check(burst_hist_t *h, uint32_t ts, unsigned pct)
{
    bool trig = false;
    if (h->have_last) {
        uint32_t p = ts - h->last_ts;
        if (h->cnt == BURST_HIST) {
            /* |p - sum/8| * 100 > pct * sum/8 */
            uint64_t p8 = (uint64_t)p * BURST_HIST;
            uint64_t diff = p8 > h->sum ? p8 - h->sum : h->sum - p8;
            trig = diff * 100u > (uint64_t)pct * h->sum;
            h->sum -= h->per[h->idx];
        } else {
            h->cnt++;
        }
        h->per[h->idx] = p;
        h->sum += p;
        h->idx = (uint8_t)((h->idx + 1u) % BURST_HIST);
    }
    h->last_ts = ts;
    h->have_last = 1;
    return trig;
}

bool burst_edge(burst_t *b, unsigned ch, unsigned lvl, uint32_t ts, uint32_t now_ms)
{
    burst_state_t st = b->state;
    if (st != BURST_ARMED && st != BURST_FILLING)
        return false;
    if (!(b->cfg.ch_mask & (1u << ch)))
        return false;
    if (b->cfg.edge != BURST_EDGE_B && lvl != b->cfg.edge)
        return false;

    /* Abstand >= 2^31 Ticks: Zeitstempel nicht mehr eindeutig entfaltbar */
    if (b->count && now_ms - b->last_ms > BURST_MAX_GAP_MS)
        b->lost++;
    b->last_ms = now_ms;

    b->ts[b->wpos] = ts;
    b->meta[b->wpos] = (uint8_t)((lvl & 1u) | (ch << 1));
    b->wpos = (b->wpos + 1u == b->cfg.n) ? 0u : b->wpos + 1u;
    b->count++;

    if (st == BURST_ARMED) {
        bool dev = hist_check(&b->hist[ch][lvl & 1u], ts, b->cfg.trig_pct);
        if (dev && b->count > b->cfg.pre) {
            b->remaining = b->cfg.n - b->cfg.pre - 1u;
            b->state = b->remaining ? BURST_FILLING : BURST_DONE;
        }
    } else if (--b->remaining == 0u) {
        b->state = BURST_DONE;
    }
    return b->state == BURST_DONE;
}

void burst_entry(const burst_t *b, uint32_t i, unsigned *ch, unsigned *lvl, uint32_t *ts)
{
    /* fertig: count >= n, aeltester Eintrag liegt am Schreibplatz */
    uint32_t pos = b->wpos + i;
    if (pos >= b->cfg.n)
        pos -= b->cfg.n;
    *ts = b->ts[pos];
    *lvl = b->meta[pos] & 1u;
    *ch = (b->meta[pos] >> 1) & 1u;
}

int32_t burst_trig_idx(const burst_t *b)
{
    return b->cfg.trig_pct ? (int32_t)b->cfg.pre : -1;
}
