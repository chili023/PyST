#include "diag.h"

#include <math.h>
#include <string.h>

#include "analog.h"
#include "burst.h"
#include "config.h"
#include "egt.h"
#include "fmt.h"
#include "protocol.h"
#include "serial.h"
#include "settings.h"
#include "settings_store.h"
#include "stm32f4xx_hal.h"

#define FW_NAME "PST-STM32 " FW_VERSION

/* Platz im Sendepuffer, den die Flanken-Ausgabe fuer Antworten freilaesst */
#define TX_RESERVE 1024u

/* Diagnose-Flags (Feld <flags>) */
#define FL_IGN_TIMEOUT  0x01u
#define FL_ROLL_TIMEOUT 0x02u
#define FL_LOSS         0x04u
#define FL_BURST        0x08u
#define FL_IGN_STALE    0x10u
#define FL_ROLL_STALE   0x20u
#define FL_DISPLACED    0x40u

static bool mode_d;
static bool displaced;
static uint32_t loss_ref;                  /* Summe der Verlustzaehler beim letzten D-Telegramm */

typedef enum { OUT_IDLE, OUT_BEGIN, OUT_LINES, OUT_END } out_t;
static out_t out;
static uint32_t out_i;

void diag_init(void)
{
    mode_d = false;                        /* Modus wird nie gespeichert */
    displaced = false;
    out = OUT_IDLE;
}

bool diag_mode_d(void) { return mode_d; }
void diag_note_displaced(void) { displaced = true; }

bool diag_output_busy(void)
{
    return out != OUT_IDLE || capture_burst()->state == BURST_DONE;
}

/* ------------------------------------------------------------ Hilfen */

static void send(fmt_t *f)
{
    fmt_str(f, "\r\n");
    serial_write(f->buf, f->len);
}

static void reply2(const char *a, const char *b)
{
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, a);
    fmt_str(&f, b);
    send(&f);
}

static void err(const char *cmd, const char *why)
{
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, "#ERR;");
    fmt_str(&f, cmd);
    fmt_char(&f, ';');
    fmt_str(&f, why);
    send(&f);
}

static void fmt_hex(fmt_t *f, uint32_t v)
{
    static const char hex[] = "0123456789ABCDEF";
    char tmp[8];
    int n = 0;
    do {
        tmp[n++] = hex[v & 15u];
        v >>= 4;
    } while (v);
    while (n)
        fmt_char(f, tmp[--n]);
}

static void fmt_tick_ns(fmt_t *f)
{
    fmt_fixed(f, 1e9f / capture_timer_hz(), 3);
}

static char up(char c) { return (c >= 'a' && c <= 'z') ? (char)(c - 32) : c; }

/* Befehlswort vergleichen (Gross/Klein egal); liefert Argumente oder NULL */
static const char *match(const char *line, const char *word)
{
    size_t n = strlen(word);
    for (size_t i = 0; i < n; i++)
        if (up(line[i]) != word[i])
            return NULL;
    if (line[n] == '\0')
        return line + n;
    if (line[n] != ' ')
        return NULL;
    while (line[n] == ' ')
        n++;
    return line + n;
}

static bool busy(void)
{
    burst_state_t st = capture_burst()->state;
    return protocol_streaming() || st != BURST_IDLE;
}

static void apply_settings(void)
{
    capture_apply_settings();
    protocol_settings_changed();
}

/* ------------------------------------------------------------ Befehle */

static void cmd_cap(void)
{
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, "#CAP;proto=1;fw=" FW_NAME ";tick_ns=");
    fmt_tick_ns(&f);
    fmt_str(&f, ";ch=I,R;burst_max=");
    fmt_u32(&f, BURST_MAX);
    fmt_str(&f, ";modes=N,D;set=1");
    send(&f);
}

static void reply_setting(const char *prefix, int id)
{
    char val[16];
    settings_format_value(&g_set, id, val, sizeof val);
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, prefix);
    fmt_str(&f, settings_key(id));
    fmt_char(&f, '=');
    fmt_str(&f, val);
    send(&f);
}

static void cmd_get(const char *a)
{
    if (*a) {
        int id = settings_find(a);
        if (id < 0) {
            err("GET", "unbekannter schluessel");
            return;
        }
        reply_setting("#SET;", id);
    } else {
        for (int id = 0; id < S_COUNT; id++)
            reply_setting("#SET;", id);
    }
    serial_write_str("#OK;GET\r\n");
}

static void cmd_set(const char *a)
{
    char key[32];
    size_t n = 0;
    while (a[n] && a[n] != ' ' && n < sizeof key - 1u) {
        key[n] = a[n];
        n++;
    }
    key[n] = '\0';
    const char *val = a + n;
    while (*val == ' ')
        val++;

    int id = settings_find(key);
    if (id < 0) {
        err("SET", "unbekannter schluessel");
        return;
    }
    if (!*val || !settings_parse_value(&g_set, id, val)) {
        err("SET", "ungueltiger wert");
        return;
    }
    apply_settings();
    reply_setting("#OK;SET;", id);
}

static void cmd_mode(const char *a)
{
    char m = up(a[0]);
    if ((m != 'N' && m != 'D') || a[1]) {
        err("MODE", "ungueltig");
        return;
    }
    mode_d = m == 'D';
    reply2("#OK;MODE;", mode_d ? "D" : "N");
}

static void cmd_run(const char *a)
{
    if ((a[0] != '0' && a[0] != '1') || a[1]) {
        err("RUN", "ungueltig");
        return;
    }
    protocol_run(a[0] == '1');
    reply2("#OK;RUN;", a[0] == '1' ? "1" : "0");
}

static void cmd_rate(const char *a)
{
    settings_t tmp = g_set;
    if (!settings_parse_value(&tmp, S_RATE_HZ, a)) {
        err("RATE", "bereich 10-100");      /* 115200 Baud tragen ca. 100 D-Telegramme/s */
        return;
    }
    g_set.v[S_RATE_HZ] = tmp.v[S_RATE_HZ];
    apply_settings();
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, "#OK;RATE;");
    fmt_u32(&f, (uint32_t)SET(S_RATE_HZ));
    send(&f);
}

static void cmd_burst(const char *a)
{
    if (match(a, "STOP")) {
        capture_burst_stop();
        out = OUT_IDLE;
        serial_write_str("#OK;BURST;STOP\r\n");
        return;
    }
    if (capture_burst()->state != BURST_IDLE || out != OUT_IDLE) {
        err("BURST", "aktiv");
        return;
    }
    burst_cfg_t cfg;
    const char *why = burst_parse(a, &cfg);
    if (why) {
        err("BURST", why);
        return;
    }
    static const char chn[4] = {'?', 'I', 'R', 'A'};
    static const char edn[3] = {'F', 'R', 'B'};
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, "#OK;BURST;ARMED;ch=");
    fmt_char(&f, chn[cfg.ch_mask & 3u]);
    fmt_str(&f, ";n=");
    fmt_u32(&f, cfg.n);
    fmt_str(&f, ";edge=");
    fmt_char(&f, edn[cfg.edge]);
    fmt_str(&f, ";trig=");
    fmt_u32(&f, cfg.trig_pct);
    fmt_str(&f, ";pre=");
    fmt_u32(&f, cfg.pre);
    send(&f);
    capture_burst_start(&cfg);
}

void diag_line_too_long(void) { err("?", "zu lang"); }

void diag_line(const char *line)
{
    const char *a;
    if (strcmp(line, "?") == 0)
        cmd_cap();
    else if ((a = match(line, "MODE")))
        cmd_mode(a);
    else if ((a = match(line, "RUN")))
        cmd_run(a);
    else if ((a = match(line, "RATE")))
        cmd_rate(a);
    else if ((a = match(line, "GET")))
        cmd_get(a);
    else if ((a = match(line, "SET")))
        cmd_set(a);
    else if ((a = match(line, "SAVE")) && !*a) {
        const char *why = settings_store_save(&g_set, busy());
        if (why)
            err("SAVE", why);
        else
            serial_write_str("#OK;SAVE\r\n");
    } else if ((a = match(line, "DEFAULTS")) && !*a) {
        settings_defaults(&g_set);
        apply_settings();
        serial_write_str("#OK;DEFAULTS\r\n");
    } else if ((a = match(line, "BURST")))
        cmd_burst(a);
    else
        err("?", "unbekannt");
}

/* ------------------------------------------------------------ Telegramm D */

static void fmt_us(fmt_t *f, uint32_t ticks, float tick_us)
{
    fmt_fixed(f, (float)ticks * tick_us, 1);
}

static void fmt_chan(fmt_t *f, const edge_win_t *w, uint32_t rej, float tick_us)
{
    fmt_char(f, ';');
    fmt_u32(f, w->n);
    fmt_char(f, ';');
    fmt_us(f, w->pmin, tick_us);
    fmt_char(f, ';');
    fmt_us(f, w->pmax, tick_us);
    fmt_char(f, ';');
    fmt_u32(f, rej);
}

static uint32_t loss_counter(void)
{
    capture_stats_t s;
    capture_get_stats(&s);
    return s.frames_dropped + s.ign_overcaptures + s.roll_overcaptures + serial_tx_dropped();
}

void diag_send_frame(const capture_frame_t *fr, uint16_t cycle)
{
    float egt1, egt2;
    if (!egt_get(0, &egt1)) egt1 = (float)NAN;
    if (!egt_get(1, &egt2)) egt2 = (float)NAN;
    unsigned src = (unsigned)SET(S_AFR_SRC);
    float afr = src ? analog_to_afr(analog_read(src)) : (float)NAN;

    uint32_t flags = 0;
    if (fr->ign_w.timeout) flags |= FL_IGN_TIMEOUT;
    if (fr->roll_w.timeout) flags |= FL_ROLL_TIMEOUT;
    if (fr->ign_w.stale) flags |= FL_IGN_STALE;
    if (fr->roll_w.stale) flags |= FL_ROLL_STALE;
    if (capture_burst()->state != BURST_IDLE || out != OUT_IDLE) flags |= FL_BURST;
    if (displaced) flags |= FL_DISPLACED;
    uint32_t loss = loss_counter();
    if (loss != loss_ref) flags |= FL_LOSS;

    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, "D;");
    fmt_u32(&f, cycle);
    fmt_char(&f, ';');
    fmt_u32(&f, HAL_GetTick());
    fmt_char(&f, ';');
    fmt_fixed(&f, fr->rate_hz, 2);
    fmt_char(&f, ';');
    fmt_fixed(&f, fr->ign_w.hz, 3);
    fmt_char(&f, ';');
    fmt_fixed(&f, fr->roll_w.hz, 3);
    fmt_char(&f, ';');
    fmt_fixed(&f, egt1, 0);
    fmt_char(&f, ';');
    fmt_fixed(&f, afr, 2);
    fmt_char(&f, ';');
    fmt_fixed(&f, egt2, 0);
    fmt_chan(&f, &fr->ign_w, fr->ign_rej, fr->tick_us);
    fmt_chan(&f, &fr->roll_w, fr->roll_rej, fr->tick_us);
    fmt_char(&f, ';');
    fmt_hex(&f, flags);
    fmt_str(&f, "\r\n");
    if (serial_write(f.buf, f.len)) {
        /* Flags erst zuruecksetzen, wenn sie gemeldet wurden */
        displaced = false;
        loss_ref = loss;
    }
}

/* ------------------------------------------------------------ Burst-Ausgabe */

void diag_poll(void)
{
    const burst_t *b = capture_burst();
    if (out == OUT_IDLE) {
        if (b->state != BURST_DONE)
            return;
        out = OUT_BEGIN;
    }
    static const char chn[4] = {'?', 'I', 'R', 'A'};
    while (out != OUT_IDLE && serial_tx_free() >= TX_RESERVE + sizeof(((fmt_t *)0)->buf)) {
        fmt_t f;
        fmt_reset(&f);
        switch (out) {
        case OUT_BEGIN:
            fmt_str(&f, "#BURST;BEGIN;n=");
            fmt_u32(&f, b->cfg.n);
            fmt_str(&f, ";tick_ns=");
            fmt_tick_ns(&f);
            fmt_str(&f, ";trig_idx=");
            {
                int32_t ti = burst_trig_idx(b);
                if (ti < 0)
                    fmt_str(&f, "-1");
                else
                    fmt_u32(&f, (uint32_t)ti);
            }
            fmt_str(&f, ";ch=");
            fmt_char(&f, chn[b->cfg.ch_mask & 3u]);
            out_i = 0;
            out = OUT_LINES;
            break;
        case OUT_LINES: {
            unsigned ch, lvl;
            uint32_t ts;
            burst_entry(b, out_i, &ch, &lvl, &ts);
            fmt_str(&f, "B;");
            fmt_u32(&f, out_i);
            fmt_char(&f, ';');
            fmt_char(&f, ch ? 'R' : 'I');
            fmt_char(&f, ';');
            fmt_char(&f, lvl ? '1' : '0');
            fmt_char(&f, ';');
            fmt_u32(&f, ts);
            if (++out_i >= b->cfg.n)
                out = OUT_END;
            break;
        }
        default:
            fmt_str(&f, "#BURST;END;n=");
            fmt_u32(&f, b->cfg.n);
            fmt_str(&f, ";lost=");
            fmt_u32(&f, b->lost);
            capture_burst_stop();
            out = OUT_IDLE;
            break;
        }
        send(&f);
    }
}
