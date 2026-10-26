// pst_core.cpp - Hardwareunabhaengiger Kern des PST-Mega-Sketches 3.1.0
// (siehe pst_core.h). Keine AVR-Header, keine dynamischen Objekte.
#include "pst_core.h"
#include <string.h>
#include <math.h>

// ==========================================================================
// Kanal
// ==========================================================================

void pst_chan_ring_clear(struct PstChan *c)
{
  c->ring_n = 0;
  c->ring_pos = 0;
  c->ring_sum = 0;
}

void pst_chan_init(struct PstChan *c)
{
  memset(c, 0, sizeof(*c));
  c->ring_size = 1;
  c->timeout_ticks = 500UL * PST_TICKS_PER_MS;
  c->win_pmin = 0xFFFFFFFFUL;
}

void pst_chan_config(struct PstChan *c, uint8_t edge, uint8_t ring, uint16_t timeout_ms,
                     uint32_t min_period_us, uint8_t lockout_pct)
{
  uint8_t lvl = (edge == 'R') ? 1 : 0;
  if (ring < 1) ring = 1;
  if (ring > PST_RING_MAX) ring = PST_RING_MAX;
  if (lvl != c->meas_lvl) {           // andere Flanke: Messung neu beginnen
    c->meas_lvl = lvl;
    c->have_last = 0;
    c->last_p = 0;
    c->win_periods = 0;
    pst_chan_ring_clear(c);
  }
  if (ring != c->ring_size) {
    c->ring_size = ring;
    pst_chan_ring_clear(c);
  }
  c->timeout_ticks = (uint32_t)timeout_ms * PST_TICKS_PER_MS;
  c->min_ticks = min_period_us * PST_TICKS_PER_US;
  c->lock_q = (uint8_t)(((uint16_t)lockout_pct * 256u) / 100u);
}

uint8_t pst_chan_timeout(struct PstChan *c, uint32_t now)
{
  if (c->have_last && (uint32_t)(now - c->last_ts) > c->timeout_ticks) {
    c->have_last = 0;
    c->last_p = 0;
    c->win_periods = 0;
    c->restart = 1;
    pst_chan_ring_clear(c);
  }
  return c->have_last ? 0 : 1;
}

void pst_chan_take(struct PstChan *c, struct PstWin *w)
{
  w->edges = c->win_edges;
  w->rej = c->win_rej;
  w->periods = c->win_periods;
  w->span = c->win_periods ? (uint32_t)(c->win_end - c->win_start) : 0;
  w->pmin = c->win_pmax ? c->win_pmin : 0;
  w->pmax = c->win_pmax;
  w->timeout = c->have_last ? 0 : 1;
  w->restart = c->restart;
  w->ring_n = c->ring_n;
  w->ring_sum = c->ring_sum;

  c->win_edges = 0;
  c->win_rej = 0;
  c->win_periods = 0;
  c->win_pmin = 0xFFFFFFFFUL;
  c->win_pmax = 0;
  c->restart = 0;
}

uint32_t pst_hz_ring(const struct PstWin *w)
{
  if (w->timeout || w->ring_n == 0 || w->ring_sum == 0) return 0;
  uint64_t num = (uint64_t)w->ring_n * PST_F_CPU + (w->ring_sum >> 1);
  return (uint32_t)(num / w->ring_sum);
}

uint32_t pst_mhz_window(uint16_t periods, uint32_t span)
{
  if (!periods || !span) return 0;
  uint64_t num = (uint64_t)periods * PST_F_CPU * 1000ULL + (span >> 1);
  uint64_t r = num / span;
  return (r > 0xFFFFFFFFULL) ? 0xFFFFFFFFUL : (uint32_t)r;
}

uint32_t pst_diag_freq(struct PstDiagChan *d, const struct PstWin *w, uint8_t *stale)
{
  *stale = 0;
  if (w->restart) d->last_mhz = 0;
  if (w->timeout) {
    d->last_mhz = 0;
    return 0;
  }
  if (w->periods) {
    d->last_mhz = pst_mhz_window(w->periods, w->span);
    return d->last_mhz;
  }
  *stale = 1;
  return d->last_mhz;
}

// ==========================================================================
// Burst
// ==========================================================================

void pst_burst_arm(struct PstBurst *b, struct PstChan *chans, uint8_t chmask, uint16_t n,
                   uint8_t edge, uint8_t trig_pct, uint16_t pre)
{
  if (n < 1) n = 1;
  if (n > PST_BURST_MAX) n = PST_BURST_MAX;
  b->state = PST_B_IDLE;
  b->chmask = chmask;
  b->n = n;
  b->edge = edge;
  b->trig_pct = trig_pct;
  b->trig_q = (uint16_t)(((uint32_t)trig_pct * 256u) / 100u);
  b->pre = trig_pct ? ((pre < n) ? pre : (uint16_t)(n - 1)) : 0;
  b->wr = 0;
  b->total = 0;
  b->post_left = 0;
  b->triggered = 0;
  b->lost = 0;
  for (uint8_t k = 0; k < 2; k++) {
    struct PstChan *c = &chans[k];
    for (uint8_t l = 0; l < 2; l++) {
      c->bt_have[l] = 0;
      c->bt_n[l] = 0;
      c->bt_pos[l] = 0;
      c->bt_sum[l] = 0;
    }
  }
  b->state = PST_B_REC;
}

void pst_burst_isr(struct PstBurst *b, struct PstChan *c, uint8_t ch,
                   uint32_t ts, uint8_t lvl, uint8_t missed)
{
  if (missed && b->lost != 0xFFFFu) b->lost++;
  if (b->edge == 'B' || (uint8_t)(b->edge == 'R') == lvl)
    pst_burst_edge(b, c, ch, ts, lvl);
}

uint8_t pst_chan_needs_toggle(const struct PstBurst *b, const struct PstChan *c, uint8_t ch)
{
  if (b->state != PST_B_REC || !(b->chmask & (1u << ch))) return 0;
  if (b->edge == 'B') return 1;
  return ((b->edge == 'R') ? 1 : 0) != c->meas_lvl;
}

// ==========================================================================
// Ausgabe / Formatierung
// ==========================================================================

void pst_out_char(struct PstOut *o, char c)
{
  if (o->len + 1 < o->cap) o->buf[o->len++] = c;
  if (o->cap) o->buf[o->len] = 0;
}

void pst_out_str(struct PstOut *o, const char *s)
{
  while (*s) pst_out_char(o, *s++);
}

void pst_out_u32(struct PstOut *o, uint32_t v)
{
  char t[11];
  uint8_t i = 0;
  do { t[i++] = (char)('0' + v % 10u); v /= 10u; } while (v);
  while (i) pst_out_char(o, t[--i]);
}

void pst_out_i32(struct PstOut *o, int32_t v)
{
  if (v < 0) { pst_out_char(o, '-'); pst_out_u32(o, (uint32_t)(-(v + 1)) + 1u); }
  else pst_out_u32(o, (uint32_t)v);
}

void pst_out_hex(struct PstOut *o, uint32_t v, uint8_t upper)
{
  char t[8];
  uint8_t i = 0;
  const char *dig = upper ? "0123456789ABCDEF" : "0123456789abcdef";
  do { t[i++] = dig[v & 15u]; v >>= 4; } while (v);
  while (i) pst_out_char(o, t[--i]);
}

// Nachbildung von Print::printFloat (Arduino AVR 1.8.x / PlatformIO 5.x),
// auf AVR ist double == float -> hier durchgehend float rechnen.
void pst_out_float(struct PstOut *o, float number, uint8_t digits)
{
  if (isnan(number)) { pst_out_str(o, "nan"); return; }
  if (isinf(number)) { pst_out_str(o, "inf"); return; }
  if (number > 4294967040.0f) { pst_out_str(o, "ovf"); return; }
  if (number < -4294967040.0f) { pst_out_str(o, "ovf"); return; }
  if (number < 0.0f) { pst_out_char(o, '-'); number = -number; }
  float rounding = 0.5f;
  for (uint8_t i = 0; i < digits; ++i) rounding /= 10.0f;
  number += rounding;
  unsigned long int_part = (unsigned long)number;
  float remainder = number - (float)int_part;
  pst_out_u32(o, (uint32_t)int_part);
  if (digits > 0) pst_out_char(o, '.');
  while (digits-- > 0) {
    remainder *= 10.0f;
    unsigned int toPrint = (unsigned int)(remainder);
    pst_out_u32(o, toPrint);
    remainder -= (float)toPrint;
  }
}

void pst_out_fixed(struct PstOut *o, uint32_t v, uint8_t dec)
{
  uint32_t div = 1;
  for (uint8_t i = 0; i < dec; i++) div *= 10u;
  pst_out_u32(o, v / div);
  if (!dec) return;
  pst_out_char(o, '.');
  uint32_t f = v % div;
  for (uint32_t d = div / 10u; d; d /= 10u) {
    pst_out_char(o, (char)('0' + (f / d) % 10u));
  }
}

void pst_out_period_us(struct PstOut *o, uint32_t ticks)
{
  if (!ticks) { pst_out_char(o, '0'); return; }
  // 1 Tick = 1/16 us -> Zehntel-us = ticks*10/16 = ticks*5/8 (gerundet)
  uint32_t tenth = (uint32_t)(((uint64_t)ticks * 5u + 4u) / 8u);
  pst_out_fixed(o, tenth, 1);
}

static void out_egt_afr(struct PstOut *o, const struct PstSensors *s, uint8_t diag)
{
  if (s->thermo) pst_out_float(o, s->egt_c, 0);
  else pst_out_u32(o, s->lambda_temp);
  pst_out_char(o, ';');
  pst_out_float(o, s->afr100 / 100, 2);
  if (s->thermo) {
    pst_out_char(o, ';');
    pst_out_u32(o, s->lambda_temp);
  } else if (diag) {
    pst_out_str(o, ";nan");
  }
}

size_t pst_fmt_normal(char *out, size_t cap, uint16_t cycle, float rate,
                      uint32_t hz_ign, uint32_t hz_roll, const struct PstSensors *s)
{
  struct PstOut o = { out, 0, cap };
  pst_out_u32(&o, cycle);
  pst_out_char(&o, ';');
  pst_out_float(&o, rate, 2);
  pst_out_char(&o, ';');
  pst_out_u32(&o, hz_ign);
  pst_out_char(&o, ';');
  pst_out_u32(&o, hz_roll);
  pst_out_char(&o, ';');
  out_egt_afr(&o, s, 0);
  pst_out_char(&o, '\n');
  return o.len;
}

static void out_chan_stats(struct PstOut *o, const struct PstWin *w)
{
  pst_out_u32(o, w->edges);
  pst_out_char(o, ';');
  pst_out_period_us(o, w->pmin);
  pst_out_char(o, ';');
  pst_out_period_us(o, w->pmax);
  pst_out_char(o, ';');
  pst_out_u32(o, w->rej);
  pst_out_char(o, ';');
}

size_t pst_fmt_diag(char *out, size_t cap, const struct PstDiag *d, const struct PstSensors *s)
{
  struct PstOut o = { out, 0, cap };
  pst_out_str(&o, "D;");
  pst_out_u32(&o, d->cycle);
  pst_out_char(&o, ';');
  pst_out_u32(&o, d->t_ms);
  pst_out_char(&o, ';');
  pst_out_float(&o, d->rate, 2);
  pst_out_char(&o, ';');
  pst_out_fixed(&o, d->ign_mhz, 3);
  pst_out_char(&o, ';');
  pst_out_fixed(&o, d->roll_mhz, 3);
  pst_out_char(&o, ';');
  out_egt_afr(&o, s, 1);
  pst_out_char(&o, ';');
  out_chan_stats(&o, &d->ign);
  out_chan_stats(&o, &d->roll);
  pst_out_hex(&o, d->flags, 1);          // Grossbuchstaben wie F446 1.1.0
  pst_out_str(&o, "\r\n");
  return o.len;
}

static char burst_ch_letter(uint8_t mask)
{
  return (mask == 3) ? 'A' : ((mask == 2) ? 'R' : 'I');
}

size_t pst_fmt_burst_begin(char *out, size_t cap, const struct PstBurst *b)
{
  struct PstOut o = { out, 0, cap };
  pst_out_str(&o, "#BURST;BEGIN;n=");
  pst_out_u32(&o, b->n);
  pst_out_str(&o, ";tick_ns=62.5;trig_idx=");
  if (b->trig_pct && b->triggered) pst_out_u32(&o, b->pre);
  else pst_out_str(&o, "-1");
  pst_out_str(&o, ";ch=");
  pst_out_char(&o, burst_ch_letter(b->chmask));
  pst_out_str(&o, "\r\n");
  return o.len;
}

size_t pst_fmt_burst_line(char *out, size_t cap, const struct PstBurst *b, uint16_t idx)
{
  struct PstOut o = { out, 0, cap };
  uint16_t pos = (uint16_t)(pst_burst_first(b) + idx);
  if (pos >= b->n) pos = (uint16_t)(pos - b->n);
  pst_out_str(&o, "B;");
  pst_out_u32(&o, idx);
  pst_out_char(&o, ';');
  pst_out_char(&o, pst_bit_get(b->chbits, pos) ? 'R' : 'I');
  pst_out_char(&o, ';');
  pst_out_char(&o, pst_bit_get(b->lvlbits, pos) ? '1' : '0');
  pst_out_char(&o, ';');
  pst_out_u32(&o, b->ts[pos]);
  pst_out_str(&o, "\r\n");
  return o.len;
}

size_t pst_fmt_burst_end(char *out, size_t cap, const struct PstBurst *b)
{
  struct PstOut o = { out, 0, cap };
  pst_out_str(&o, "#BURST;END;n=");
  pst_out_u32(&o, b->n);
  pst_out_str(&o, ";lost=");
  pst_out_u32(&o, b->lost);
  pst_out_str(&o, "\r\n");
  return o.len;
}

// ==========================================================================
// Einstellungen
// ==========================================================================

static const char *const set_keys[PST_SET_COUNT] = {
  "edge_ign", "edge_roll", "timeout_ign_ms", "timeout_roll_ms", "pullup", "thermo",
  "bme_addr", "afr_addr", "ring_ign", "ring_roll", "ign_lockout_pct",
  "ign_min_period_us", "roll_min_period_us"
};

void pst_settings_defaults(struct PstSettings *s)
{
  s->edge_ign = 'F';             // 3.0.0: ICES4 = 0 -> fallende Flanke
  s->edge_roll = 'F';
  s->timeout_ign_ms = 500;
  s->timeout_roll_ms = 1000;
  s->pullup = 1;
  s->thermo = 0;                 // wie 3.0.0 (bool thermo = false)
  s->bme_addr = 0x76;            // wie 3.0.0 (BMEADDRESS 0x76)
  s->afr_addr = 1;               // Spartan 2
  s->ring_ign = 5;               // wie RINGSIZE4
  s->ring_roll = 50;             // wie RINGSIZE5
  s->ign_lockout_pct = 0;        // Filter aus (wie 3.0.0)
  s->ign_min_period_us = 0;
  s->roll_min_period_us = 0;
}

int pst_setting_find(const char *key)
{
  for (uint8_t i = 0; i < PST_SET_COUNT; i++)
    if (strcmp(key, set_keys[i]) == 0) return i;
  return -1;
}

size_t pst_setting_format(const struct PstSettings *s, uint8_t idx, char *out, size_t cap)
{
  struct PstOut o = { out, 0, cap };
  if (cap) out[0] = 0;
  if (idx >= PST_SET_COUNT) return 0;
  pst_out_str(&o, set_keys[idx]);
  pst_out_char(&o, '=');
  switch (idx) {
    case 0: pst_out_char(&o, (char)s->edge_ign); break;
    case 1: pst_out_char(&o, (char)s->edge_roll); break;
    case 2: pst_out_u32(&o, s->timeout_ign_ms); break;
    case 3: pst_out_u32(&o, s->timeout_roll_ms); break;
    case 4: pst_out_u32(&o, s->pullup); break;
    case 5: pst_out_u32(&o, s->thermo); break;
    case 6: pst_out_str(&o, "0x"); pst_out_hex(&o, s->bme_addr, 0); break;
    case 7: pst_out_u32(&o, s->afr_addr); break;
    case 8: pst_out_u32(&o, s->ring_ign); break;
    case 9: pst_out_u32(&o, s->ring_roll); break;
    case 10: pst_out_u32(&o, s->ign_lockout_pct); break;
    case 11: pst_out_u32(&o, s->ign_min_period_us); break;
    case 12: pst_out_u32(&o, s->roll_min_period_us); break;
  }
  return o.len;
}

// Dezimal- oder Hex-Zahl ("0x..") streng parsen
static int parse_u32(const char *s, uint32_t *v, uint8_t hex_default)
{
  uint32_t r = 0;
  uint8_t base = hex_default ? 16 : 10;
  if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) { base = 16; s += 2; }
  if (!*s) return -1;
  for (; *s; s++) {
    uint8_t d;
    char c = *s;
    if (c >= '0' && c <= '9') d = (uint8_t)(c - '0');
    else if (base == 16 && c >= 'a' && c <= 'f') d = (uint8_t)(c - 'a' + 10);
    else if (base == 16 && c >= 'A' && c <= 'F') d = (uint8_t)(c - 'A' + 10);
    else return -1;
    if (r > (0xFFFFFFFFUL - d) / base) return -1;
    r = r * base + d;
  }
  *v = r;
  return 0;
}

static int parse_edge(const char *s, uint8_t *e, uint8_t allow_b)
{
  if (!s[0] || s[1]) return -1;
  char c = s[0];
  if (c >= 'a' && c <= 'z') c = (char)(c - 32);
  if (c == 'F' || c == 'R' || (allow_b && c == 'B')) { *e = (uint8_t)c; return 0; }
  return -1;
}

static int in_range(uint32_t v, uint32_t lo, uint32_t hi) { return v >= lo && v <= hi; }

int pst_setting_set(struct PstSettings *s, uint8_t idx, const char *val)
{
  uint32_t v = 0;
  uint8_t e;
  if (idx <= 1) {
    if (parse_edge(val, &e, 0)) return -1;
    if (idx == 0) s->edge_ign = e; else s->edge_roll = e;
    return 0;
  }
  if (parse_u32(val, &v, idx == 6)) return -1;
  switch (idx) {
    case 2: if (!in_range(v, 10, 3000)) return -1; s->timeout_ign_ms = (uint16_t)v; break;
    case 3: if (!in_range(v, 10, 3000)) return -1; s->timeout_roll_ms = (uint16_t)v; break;
    case 4: if (v > 1) return -1; s->pullup = (uint8_t)v; break;
    case 5: if (v > 1) return -1; s->thermo = (uint8_t)v; break;
    case 6: if (!in_range(v, 0x08, 0x77)) return -1; s->bme_addr = (uint8_t)v; break;
    case 7: if (!in_range(v, 1, 127)) return -1; s->afr_addr = (uint8_t)v; break;
    case 8: if (!in_range(v, 1, PST_RING_MAX)) return -1; s->ring_ign = (uint8_t)v; break;
    case 9: if (!in_range(v, 1, PST_RING_MAX)) return -1; s->ring_roll = (uint8_t)v; break;
    case 10: if (v > 95) return -1; s->ign_lockout_pct = (uint8_t)v; break;
    case 11: if (v > 1000000UL) return -1; s->ign_min_period_us = v; break;
    case 12: if (v > 1000000UL) return -1; s->roll_min_period_us = v; break;
    default: return -1;
  }
  return 0;
}

uint16_t pst_crc16(const uint8_t *d, size_t n)
{
  uint16_t crc = 0xFFFF;              // CRC-16/CCITT-FALSE
  while (n--) {
    crc ^= (uint16_t)(*d++) << 8;
    for (uint8_t i = 0; i < 8; i++)
      crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
  }
  return crc;
}

static void put32(uint8_t *p, uint32_t v)
{
  p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
static uint32_t get32(const uint8_t *p)
{
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

#define EE_DATA_LEN 21

void pst_settings_pack(const struct PstSettings *s, uint8_t img[PST_EE_SIZE])
{
  memset(img, 0, PST_EE_SIZE);
  img[0] = 'P'; img[1] = 'M';
  img[2] = PST_EE_VERSION;
  img[3] = EE_DATA_LEN;
  uint8_t *d = img + 4;
  d[0] = s->edge_ign;
  d[1] = s->edge_roll;
  d[2] = (uint8_t)s->timeout_ign_ms;  d[3] = (uint8_t)(s->timeout_ign_ms >> 8);
  d[4] = (uint8_t)s->timeout_roll_ms; d[5] = (uint8_t)(s->timeout_roll_ms >> 8);
  d[6] = s->pullup;
  d[7] = s->thermo;
  d[8] = s->bme_addr;
  d[9] = s->afr_addr;
  d[10] = s->ring_ign;
  d[11] = s->ring_roll;
  d[12] = s->ign_lockout_pct;
  put32(d + 13, s->ign_min_period_us);
  put32(d + 17, s->roll_min_period_us);
  uint16_t crc = pst_crc16(img, PST_EE_SIZE - 2);
  img[PST_EE_SIZE - 2] = (uint8_t)crc;
  img[PST_EE_SIZE - 1] = (uint8_t)(crc >> 8);
}

uint8_t pst_settings_unpack(struct PstSettings *s, const uint8_t img[PST_EE_SIZE])
{
  struct PstSettings t;
  pst_settings_defaults(s);
  if (img[0] != 'P' || img[1] != 'M' || img[2] != PST_EE_VERSION || img[3] != EE_DATA_LEN) return 0;
  uint16_t crc = pst_crc16(img, PST_EE_SIZE - 2);
  if (img[PST_EE_SIZE - 2] != (uint8_t)crc || img[PST_EE_SIZE - 1] != (uint8_t)(crc >> 8)) return 0;
  const uint8_t *d = img + 4;
  t.edge_ign = d[0];
  t.edge_roll = d[1];
  t.timeout_ign_ms = (uint16_t)(d[2] | (d[3] << 8));
  t.timeout_roll_ms = (uint16_t)(d[4] | (d[5] << 8));
  t.pullup = d[6];
  t.thermo = d[7];
  t.bme_addr = d[8];
  t.afr_addr = d[9];
  t.ring_ign = d[10];
  t.ring_roll = d[11];
  t.ign_lockout_pct = d[12];
  t.ign_min_period_us = get32(d + 13);
  t.roll_min_period_us = get32(d + 17);
  // Plausibilitaet: jeden Wert ueber den #SET-Pfad pruefen
  struct PstSettings chk;
  pst_settings_defaults(&chk);
  char buf[40];                       // laengster Eintrag: roll_min_period_us=1000000
  for (uint8_t i = 0; i < PST_SET_COUNT; i++) {
    size_t n = pst_setting_format(&t, i, buf, sizeof(buf));
    char *eq = (char *)memchr(buf, '=', n);
    if (!eq || pst_setting_set(&chk, i, eq + 1)) return 0;
  }
  *s = chk;
  return 1;
}

// ==========================================================================
// Kommando-Parser
// ==========================================================================

void pst_parser_init(struct PstParser *p)
{
  p->len = 0;
  p->in_line = 0;
  p->overflow = 0;
  p->buf[0] = 0;
}

int pst_parser_feed(struct PstParser *p, char c, char *ch)
{
  if (p->in_line) {
    if (c == '\r' || c == '\n') {
      p->in_line = 0;
      p->buf[p->len] = 0;
      if (p->overflow) { p->overflow = 0; p->len = 0; return PST_P_LONG; }
      return PST_P_LINE;
    }
    if (p->len < PST_LINE_MAX) p->buf[p->len++] = c;
    else p->overflow = 1;
    return PST_P_NONE;
  }
  if (c == '#') {
    p->in_line = 1;
    p->overflow = 0;
    p->len = 0;
    p->buf[p->len++] = c;
    return PST_P_NONE;
  }
  if (c == '\r' || c == '\n') return PST_P_NONE;
  *ch = c;
  return PST_P_CHAR;
}

// ==========================================================================
// Befehle
// ==========================================================================

void pst_cmd_init(struct PstCmd *c)
{
  memset(c, 0, sizeof(*c));
  c->mode = 'N';
  pst_settings_defaults(&c->set);
}

char pst_cmd_char(struct PstCmd *c, char ch)
{
  if (ch == 'm') {                     // wie 3.0.0: Messung umschalten
    if (c->mess) { c->mess = 0; c->req_cycle_reset = 1; }
    else c->mess = 1;
    return 'm';
  }
  if (ch == 'v' || ch == 'e') return ch;
  return 0;
}

static void err(struct PstOut *o, const char *cmd, const char *why)
{
  pst_out_str(o, "#ERR;");
  pst_out_str(o, cmd);
  pst_out_char(o, ';');
  pst_out_str(o, why);
  pst_out_str(o, "\r\n");
}

static void ok(struct PstOut *o, const char *what)
{
  pst_out_str(o, "#OK;");
  pst_out_str(o, what);
  pst_out_str(o, "\r\n");
}

static uint8_t upcase_eq(const char *a, const char *b)
{
  for (; *a && *b; a++, b++) {
    char x = *a;
    if (x >= 'a' && x <= 'z') x = (char)(x - 32);
    if (x != *b) return 0;
  }
  return *a == *b;
}

static void cmd_burst(struct PstCmd *c, char **tok, uint8_t nt, struct PstOut *o)
{
  if (nt == 2 && upcase_eq(tok[1], "STOP")) {
    c->req_burst_stop = 1;
    ok(o, "BURST;STOP");
    return;
  }
  if (nt < 4) { err(o, "BURST", "Syntax"); return; }
  if (c->burst_busy) { err(o, "BURST", "Ausgabe laeuft"); return; }
  uint8_t mask;
  char chc = tok[1][0];
  if (chc >= 'a' && chc <= 'z') chc = (char)(chc - 32);
  if (tok[1][1]) chc = 0;
  if (chc == 'I') mask = 1;
  else if (chc == 'R') mask = 2;
  else if (chc == 'A') mask = 3;
  else { err(o, "BURST", "Kanal"); return; }
  uint32_t n;
  if (parse_u32(tok[2], &n, 0) || n < 1 || n > PST_BURST_MAX) { err(o, "BURST", "n"); return; }
  uint8_t edge;
  if (parse_edge(tok[3], &edge, 1)) { err(o, "BURST", "Flanke"); return; }
  uint32_t trig = 0, pre = 0xFFFFFFFFUL;
  for (uint8_t i = 4; i < nt; i++) {
    char k = tok[i][0];
    uint32_t v;
    if (parse_u32(tok[i] + 1, &v, 0)) { err(o, "BURST", "Syntax"); return; }
    if (k == 'T' || k == 't') {
      if (v < 1 || v > 200) { err(o, "BURST", "Trigger"); return; }
      trig = v;
    } else if (k == 'P' || k == 'p') {
      pre = v;
    } else { err(o, "BURST", "Syntax"); return; }
  }
  if (!trig) pre = 0;
  else if (pre == 0xFFFFFFFFUL) pre = n / 2;
  else if (pre >= n) { err(o, "BURST", "Vorlauf"); return; }
  c->req_burst = 1;
  c->b_chmask = mask;
  c->b_edge = edge;
  c->b_trig = (uint8_t)trig;
  c->b_n = (uint16_t)n;
  c->b_pre = (uint16_t)pre;
  pst_out_str(o, "#OK;BURST;ARMED;ch=");
  pst_out_char(o, chc);
  pst_out_str(o, ";n=");
  pst_out_u32(o, n);
  pst_out_str(o, ";edge=");
  pst_out_char(o, (char)edge);
  pst_out_str(o, ";trig=");
  pst_out_u32(o, trig);
  pst_out_str(o, ";pre=");
  pst_out_u32(o, pre);
  pst_out_str(o, "\r\n");
}

size_t pst_cmd_line(struct PstCmd *c, const char *line, char *out, size_t outsz)
{
  struct PstOut o = { out, 0, outsz };
  if (outsz) out[0] = 0;
  char buf[PST_LINE_MAX + 1];
  strncpy(buf, line, PST_LINE_MAX);
  buf[PST_LINE_MAX] = 0;

  // in Woerter zerlegen (Leerzeichen/Tab); "key=wert" bei #SET ebenfalls trennen
  char *tok[8];
  uint8_t nt = 0;
  char *s = buf;
  while (*s && nt < 8) {
    while (*s == ' ' || *s == '\t') *s++ = 0;
    if (!*s) break;
    tok[nt++] = s;
    while (*s && *s != ' ' && *s != '\t') s++;
  }
  if (nt == 0 || tok[0][0] != '#') { err(&o, "?", "unbekannt"); return o.len; }
  const char *cmd = tok[0] + 1;

  if (upcase_eq(cmd, "?")) {
    pst_out_str(&o, "#CAP;proto=1;fw=" PST_FW_NAME ";tick_ns=62.5;ch=I,R;burst_max=");
    pst_out_u32(&o, PST_BURST_MAX);
    pst_out_str(&o, ";modes=N,D;set=1\r\n");
  } else if (upcase_eq(cmd, "MODE")) {
    uint8_t m = 0;
    if (nt == 2 && upcase_eq(tok[1], "N")) m = 'N';
    if (nt == 2 && upcase_eq(tok[1], "D")) m = 'D';
    if (!m) { err(&o, "MODE", "ungueltig"); return o.len; }
    c->mode = m;
    pst_out_str(&o, "#OK;MODE;");
    pst_out_char(&o, (char)m);
    pst_out_str(&o, "\r\n");
  } else if (upcase_eq(cmd, "RUN")) {
    if (nt != 2 || tok[1][1] || (tok[1][0] != '0' && tok[1][0] != '1')) {
      err(&o, "RUN", "ungueltig");
      return o.len;
    }
    if (tok[1][0] == '1') c->mess = 1;
    else { if (c->mess) c->req_cycle_reset = 1; c->mess = 0; }
    ok(&o, c->mess ? "RUN;1" : "RUN;0");
  } else if (upcase_eq(cmd, "RATE")) {
    err(&o, "RATE", "nicht unterstuetzt");
  } else if (upcase_eq(cmd, "GET")) {
    c->req_get = 1;                      // Liste kommt vom Sketch
  } else if (upcase_eq(cmd, "SET")) {
    const char *key = 0, *val = 0;
    if (nt == 3) { key = tok[1]; val = tok[2]; }
    else if (nt == 2) {
      char *eq = strchr(tok[1], '=');
      if (eq) { *eq = 0; key = tok[1]; val = eq + 1; }
    }
    if (!key) { err(&o, "SET", "Syntax"); return o.len; }
    int idx = pst_setting_find(key);
    if (idx < 0) { err(&o, "SET", "unbekannter Schluessel"); return o.len; }
    if (pst_setting_set(&c->set, (uint8_t)idx, val)) { err(&o, "SET", "ungueltiger Wert"); return o.len; }
    c->req_apply = 1;
    char kv[32];
    pst_setting_format(&c->set, (uint8_t)idx, kv, sizeof(kv));
    pst_out_str(&o, "#OK;SET;");
    pst_out_str(&o, kv);
    pst_out_str(&o, "\r\n");
  } else if (upcase_eq(cmd, "SAVE")) {
    // wie F446 1.1.0: nicht waehrend Messung/Burst (EEPROM-Schreiben)
    if (c->mess || c->burst_active) { err(&o, "SAVE", "messung laeuft"); return o.len; }
    c->req_save = 1;                     // #OK;SAVE nach dem Schreiben
  } else if (upcase_eq(cmd, "DEFAULTS")) {
    pst_settings_defaults(&c->set);
    c->req_apply = 1;
    ok(&o, "DEFAULTS");
  } else if (upcase_eq(cmd, "BURST")) {
    cmd_burst(c, tok, nt, &o);
  } else {
    err(&o, "?", "unbekannt");
  }
  return o.len;
}

// ==========================================================================
// Antworten auf 'v' und 'e' (Format wie 3.0.0, Serial.println -> "\r\n")
// ==========================================================================

size_t pst_fmt_version(char *out, size_t cap, const struct PstSettings *s)
{
  struct PstOut o = { out, 0, cap };
  pst_out_str(&o, PST_VERSION_STR ";\r\n");
  pst_out_str(&o, "Ringspeicher Zuendsignal:");
  pst_out_u32(&o, s->ring_ign);
  pst_out_str(&o, ";\r\nRingspeicher Rolle:");
  pst_out_u32(&o, s->ring_roll);
  pst_out_str(&o, ";\r\nAdresse BME280:");
  pst_out_hex(&o, s->bme_addr, 1);
  pst_out_str(&o, ";\r\nThermoelement (1=ja 0=nein):");
  pst_out_u32(&o, s->thermo ? 1 : 0);
  pst_out_str(&o, ";\r\n");
  return o.len;
}

size_t pst_fmt_env(char *out, size_t cap, float temp, float press, float hum)
{
  struct PstOut o = { out, 0, cap };
  pst_out_float(&o, temp, 1);
  pst_out_char(&o, ';');
  pst_out_float(&o, press, 0);
  pst_out_char(&o, ';');
  pst_out_float(&o, hum, 1);
  pst_out_str(&o, "\r\n");
  return o.len;
}
