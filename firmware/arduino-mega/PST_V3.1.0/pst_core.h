// pst_core.h - Hardwareunabhaengiger Kern des PST-Mega-Sketches 3.1.0
//
// Enthaelt alles, was ohne AVR-Header testbar ist:
//   - Zeitstempel aus ICR + Ueberlaufzaehler
//   - Flankenverarbeitung je Kanal (wird direkt aus der Capture-ISR aufgerufen)
//   - Burst-Aufnahme mit Trigger (PST-Diag 1, Kapitel 5)
//   - Frequenzrechnung Normal- und Diagnosemodus, Timeout
//   - Einstellungen (#GET/#SET, EEPROM-Abbild mit Version und CRC)
//   - Kommando-Parser (Einzelzeichen v/e/m und #-Zeilen) mit festem Puffer
//   - Telegramm-Formatierung N und D (Gleitkomma wie Arduino Print::printFloat)
//
// Host-Tests: test/ (g++ nativ, siehe test/Makefile).
// Spezifikation: docs/04_PST_Diag_1.md
#ifndef PST_CORE_H
#define PST_CORE_H

#include <stdint.h>
#include <stddef.h>

#define PST_FW_NAME      "PST-Mega 3.1.0"
#define PST_VERSION_STR  "Version 3.1.0 vom 26.10.26"   // Antwort auf 'v'
#define PST_F_CPU        16000000UL      // Timer4/5 ohne Vorteiler: 1 Tick = 62,5 ns
#define PST_TICKS_PER_MS 16000UL
#define PST_TICKS_PER_US 16UL

#define PST_BURST_MAX    800     // Flanken je Burst (5 Byte je Eintrag + Bitfelder)
#define PST_RING_MAX     64      // max. Ringspeicher je Kanal (Normalmodus)
#define PST_LINE_MAX     63      // max. Laenge einer #-Zeile (inkl. '#')
#define PST_TRIG_HIST    8       // Trigger: Mittel der 8 vorherigen Perioden
#define PST_PERIOD_CLAMP 0x04000000UL  // Perioden fuer Trigger-Mittel begrenzen (4,2 s)

// ISR-Pfad immer einbetten (Arduino baut mit -Os, Funktionsaufrufe kosten Takte)
#if defined(__GNUC__)
#define PST_ISR_INLINE static inline __attribute__((always_inline))
#define PST_NOINLINE __attribute__((noinline))
#else
#define PST_ISR_INLINE static inline
#define PST_NOINLINE
#endif

#define PST_CH_I 0               // Zuendung (ICP4, Pin 49)
#define PST_CH_R 1               // Rolle    (ICP5, Pin 48)

// Flags im D-Telegramm
#define PST_FL_TO_IGN    0x01
#define PST_FL_TO_ROLL   0x02
#define PST_FL_LOSS      0x04
#define PST_FL_BURST     0x08
#define PST_FL_ST_IGN    0x10
#define PST_FL_ST_ROLL   0x20
#define PST_FL_DISPLACED 0x40

// --------------------------------------------------------------------------
// Zeitstempel: 16-bit ICR + 16-bit Ueberlaufzaehler -> 32 bit (laeuft nach
// 2^32 Ticks = 268 s ueber). Steht beim Capture ein Ueberlauf noch aus
// (TOV gesetzt, ICR klein), gehoert der Zeitstempel schon zum naechsten
// Ueberlauf (gleiche Logik wie 3.0.0).
PST_ISR_INLINE uint32_t pst_ts_from_capture(uint16_t icr, uint16_t ovf, uint8_t tov_pending)
{
  if (tov_pending && icr < 0x8000u) ovf++;
  return ((uint32_t)ovf << 16) | icr;
}

// --------------------------------------------------------------------------
// Kanal
struct PstChan {
  // Konfiguration (Hauptschleife schreibt mit gesperrten Interrupts)
  uint8_t  meas_lvl;        // gemessene Flanke: 0 = fallend, 1 = steigend
  uint8_t  ring_size;       // 1..PST_RING_MAX
  uint8_t  lock_q;          // Doppelimpulsfilter: pct*256/100 (0 = aus)
  uint8_t  toggle;          // ICES in der ISR umschalten (beide Flanken)
  uint32_t timeout_ticks;
  uint32_t min_ticks;       // Mindestperiode (0 = aus)

  // Messung
  uint8_t  have_last;
  uint8_t  restart;         // Timeout/Neubeginn seit dem letzten Telegramm
  uint32_t last_ts;         // letzte gueltige Flanke
  uint32_t last_p;          // letzte gueltige Periode (0 = unbekannt)
  uint8_t  ring_pos, ring_n;
  uint32_t ring_sum;
  uint32_t ring[PST_RING_MAX];

  // Fenster seit dem letzten Telegramm
  uint16_t win_edges;       // gueltige Flanken
  uint16_t win_rej;         // vom Filter verworfene Flanken
  uint16_t win_periods;     // Perioden fuer die Frequenz (seit win_start)
  uint32_t win_start, win_end;
  uint32_t win_pmin, win_pmax;

  // Trigger-Verlauf je Flankenrichtung (nur fuer Burst)
  uint8_t  bt_have[2];
  uint8_t  bt_n[2], bt_pos[2];
  uint32_t bt_last[2];
  uint32_t bt_sum[2];
  uint32_t bt_hist[2][PST_TRIG_HIST];
};

// Burst-Zustand
#define PST_B_IDLE 0
#define PST_B_REC  1   // Aufnahme laeuft (mit oder ohne Trigger)
#define PST_B_DONE 2   // fertig, wartet auf Ausgabe

struct PstBurst {
  volatile uint8_t state;
  uint8_t  chmask;          // Bit0 = I, Bit1 = R
  uint8_t  edge;            // 'F', 'R', 'B'
  uint8_t  trig_pct;        // 0 = ohne Trigger
  uint16_t trig_q;          // pct*256/100
  uint8_t  triggered;
  uint16_t n, pre, wr, total, post_left;
  uint16_t lost;
  uint32_t ts[PST_BURST_MAX];
  uint8_t  chbits[(PST_BURST_MAX + 7) / 8];
  uint8_t  lvlbits[(PST_BURST_MAX + 7) / 8];
};

PST_ISR_INLINE uint8_t pst_bit_get(const uint8_t *a, uint16_t i) { return (a[i >> 3] >> (i & 7)) & 1u; }
PST_ISR_INLINE void pst_bit_put(uint8_t *a, uint16_t i, uint8_t v)
{
  uint8_t m = (uint8_t)(1u << (i & 7));
  if (v) a[i >> 3] |= m; else a[i >> 3] &= (uint8_t)~m;
}

// Abweichung |p - mittel| > mittel * q/256 ? (q = pct*256/100, ohne Division)
PST_ISR_INLINE uint8_t pst_deviates(uint32_t p, uint32_t mean, uint16_t q)
{
  uint32_t d = (p > mean) ? (p - mean) : (mean - p);
  uint32_t thr = ((mean >> 4) * q) >> 4;
  return d > thr;
}

// Trigger-Verlauf je Kanal/Richtung fuehren, liefert 1 bei Ausloesung
PST_ISR_INLINE uint8_t pst_trig_edge(struct PstChan *c, uint32_t ts, uint8_t lvl, uint16_t q, uint8_t armed)
{
  uint8_t hit = 0;
  if (c->bt_have[lvl]) {
    uint32_t p = ts - c->bt_last[lvl];
    if (p > PST_PERIOD_CLAMP) p = PST_PERIOD_CLAMP;
    if (c->bt_n[lvl] >= PST_TRIG_HIST) {
      if (armed && pst_deviates(p, c->bt_sum[lvl] >> 3, q)) hit = 1;
      c->bt_sum[lvl] -= c->bt_hist[lvl][c->bt_pos[lvl]];
    } else {
      c->bt_n[lvl]++;
    }
    c->bt_hist[lvl][c->bt_pos[lvl]] = p;
    c->bt_sum[lvl] += p;
    c->bt_pos[lvl] = (uint8_t)((c->bt_pos[lvl] + 1) & (PST_TRIG_HIST - 1));
  }
  c->bt_have[lvl] = 1;
  c->bt_last[lvl] = ts;
  return hit;
}

// Eine Flanke in den Burst-Puffer schreiben
PST_ISR_INLINE void pst_burst_edge(struct PstBurst *b, struct PstChan *c, uint8_t ch,
                                  uint32_t ts, uint8_t lvl)
{
  uint16_t w = b->wr;
  b->ts[w] = ts;
  pst_bit_put(b->chbits, w, ch);
  pst_bit_put(b->lvlbits, w, lvl);
  if (++w >= b->n) w = 0;
  b->wr = w;
  if (b->total < 0xFFFFu) b->total++;

  if (!b->trig_pct) {
    if (b->total >= b->n) b->state = PST_B_DONE;
    return;
  }
  if (!b->triggered) {
    // Trigger erst scharf, wenn genug Vorlauf im Puffer ist
    uint8_t armed = (b->total > b->pre);
    if (pst_trig_edge(c, ts, lvl, b->trig_q, armed)) {
      b->triggered = 1;
      b->post_left = (uint16_t)(b->n - b->pre - 1);
      if (b->post_left == 0) b->state = PST_B_DONE;
    }
  } else {
    if (--b->post_left == 0) b->state = PST_B_DONE;
  }
}

// Gueltige Flanke der Messrichtung
PST_ISR_INLINE void pst_meas_edge(struct PstChan *c, uint32_t ts)
{
  if (!c->have_last) {
    c->have_last = 1;
    c->last_ts = ts;
    c->last_p = 0;
    if (c->win_edges != 0xFFFFu) c->win_edges++;
    return;
  }
  uint32_t p = ts - c->last_ts;
  if (p > c->timeout_ticks) {
    // Neubeginn nach Pause: keine Periode, Ringspeicher verwerfen
    c->last_ts = ts;
    c->last_p = 0;
    c->ring_n = 0; c->ring_pos = 0; c->ring_sum = 0;
    c->win_periods = 0;
    c->restart = 1;
    if (c->win_edges != 0xFFFFu) c->win_edges++;
    return;
  }
  if (p < c->min_ticks ||
      (c->lock_q && c->last_p && p < (((c->last_p >> 4) * c->lock_q) >> 4))) {
    if (c->win_rej != 0xFFFFu) c->win_rej++;
    return;
  }
  // Periode uebernehmen
  if (c->win_periods == 0) c->win_start = c->last_ts;
  if (c->win_periods != 0xFFFFu) { c->win_periods++; c->win_end = ts; }
  if (p < c->win_pmin) c->win_pmin = p;
  if (p > c->win_pmax) c->win_pmax = p;
  if (c->win_edges != 0xFFFFu) c->win_edges++;
  c->last_ts = ts;
  c->last_p = p;

  uint8_t pos = c->ring_pos;
  if (c->ring_n >= c->ring_size) c->ring_sum -= c->ring[pos];
  else c->ring_n++;
  c->ring[pos] = p;
  c->ring_sum += p;
  if (++pos >= c->ring_size) pos = 0;
  c->ring_pos = pos;
}

// Burst-Teil der ISR (nur aufgerufen, wenn ein Burst auf dem Kanal aufnimmt).
// Eigene, nicht eingebettete Funktion: so muss die ISR im Normalbetrieb nur
// wenige Register sichern (schneller Messpfad).
void pst_burst_isr(struct PstBurst *b, struct PstChan *c, uint8_t ch,
                   uint32_t ts, uint8_t lvl, uint8_t missed) PST_NOINLINE;

// Aus der Capture-ISR: eine Flanke (lvl = Pegel nach der Flanke).
// missed = 1, wenn beim Umschalten von ICES die Gegenflanke schon vorbei war.
PST_ISR_INLINE void pst_edge(struct PstChan *c, struct PstBurst *b, uint8_t ch,
                            uint32_t ts, uint8_t lvl, uint8_t missed)
{
  if (b->state == PST_B_REC && (b->chmask & (1u << ch)))
    pst_burst_isr(b, c, ch, ts, lvl, missed);
  if (lvl == c->meas_lvl) pst_meas_edge(c, ts);
}

// --------------------------------------------------------------------------
// Hauptschleife (mit gesperrten Interrupts aufrufen)

struct PstWin {
  uint16_t edges, rej, periods;
  uint32_t span;            // Ticks erste -> letzte Flanke der Perioden
  uint32_t pmin, pmax;      // 0 wenn keine Periode
  uint8_t  timeout;
  uint8_t  restart;
  uint8_t  ring_n;
  uint32_t ring_sum;
};

void pst_chan_init(struct PstChan *c);
void pst_chan_config(struct PstChan *c, uint8_t edge, uint8_t ring, uint16_t timeout_ms,
                     uint32_t min_period_us, uint8_t lockout_pct);
void pst_chan_ring_clear(struct PstChan *c);
// Timeout pruefen (now = aktueller Zeitstempel desselben Timers); 1 = Timeout
uint8_t pst_chan_timeout(struct PstChan *c, uint32_t now);
// Fensterwerte holen und Fenster neu beginnen
void pst_chan_take(struct PstChan *c, struct PstWin *w);

// Normalmodus: ganze Hz (gerundet) aus Ringspeicher, 0 bei Timeout
uint32_t pst_hz_ring(const struct PstWin *w);
// Diagnosemodus: mHz aus Fenster; 0 wenn keine Periode
uint32_t pst_mhz_window(uint16_t periods, uint32_t span);

// Diagnose-Frequenz je Kanal mit "zuletzt bekannt"-Logik; setzt stale
struct PstDiagChan { uint32_t last_mhz; };
uint32_t pst_diag_freq(struct PstDiagChan *d, const struct PstWin *w, uint8_t *stale);

// Burst vorbereiten (Interrupts gesperrt); chans = Feld mit 2 Kanaelen
void pst_burst_arm(struct PstBurst *b, struct PstChan *chans, uint8_t chmask, uint16_t n,
                   uint8_t edge, uint8_t trig_pct, uint16_t pre);
// Muss der Kanal beide Flanken sehen (ICES umschalten)?
uint8_t pst_chan_needs_toggle(const struct PstBurst *b, const struct PstChan *c, uint8_t ch);
// Index des aeltesten Eintrags nach Abschluss
static inline uint16_t pst_burst_first(const struct PstBurst *b) { return (b->total >= b->n) ? b->wr : 0; }

// --------------------------------------------------------------------------
// Einstellungen

struct PstSettings {
  uint8_t  edge_ign;        // 'F' / 'R'
  uint8_t  edge_roll;
  uint16_t timeout_ign_ms;
  uint16_t timeout_roll_ms;
  uint8_t  pullup;
  uint8_t  thermo;
  uint8_t  bme_addr;
  uint8_t  afr_addr;
  uint8_t  ring_ign;
  uint8_t  ring_roll;
  uint8_t  ign_lockout_pct;
  uint32_t ign_min_period_us;
  uint32_t roll_min_period_us;
};

#define PST_SET_COUNT 13
#define PST_EE_VERSION 1
#define PST_EE_SIZE   32        // Magic(2) Version(1) Laenge(1) Daten(26) CRC(2)

void pst_settings_defaults(struct PstSettings *s);
// Wert einer Einstellung als "key=wert" (Index 0..PST_SET_COUNT-1); Laenge
size_t pst_setting_format(const struct PstSettings *s, uint8_t idx, char *out, size_t outsz);
// Index zu Schluessel, -1 wenn unbekannt
int pst_setting_find(const char *key);
// 0 = ok, -1 = ungueltiger Wert
int pst_setting_set(struct PstSettings *s, uint8_t idx, const char *val);
uint16_t pst_crc16(const uint8_t *d, size_t n);
void pst_settings_pack(const struct PstSettings *s, uint8_t img[PST_EE_SIZE]);
// 1 = gueltig geladen, 0 = Standardwerte gesetzt
uint8_t pst_settings_unpack(struct PstSettings *s, const uint8_t img[PST_EE_SIZE]);

// --------------------------------------------------------------------------
// Kommando-Parser

struct PstParser {
  char    buf[PST_LINE_MAX + 1];
  uint8_t len;
  uint8_t in_line;
  uint8_t overflow;
};

#define PST_P_NONE   0
#define PST_P_CHAR   1   // Einzelzeichen-Befehl in *ch
#define PST_P_LINE   2   // #-Zeile fertig in p->buf (mit '#')
#define PST_P_LONG   3   // #-Zeile zu lang (verworfen)

void pst_parser_init(struct PstParser *p);
int  pst_parser_feed(struct PstParser *p, char c, char *ch);

// Ergebnis einer #-Zeile; Antwort steht in out (kann leer sein, z. B. #GET)
struct PstCmd {
  uint8_t mode;             // 'N' / 'D' (aktuell, wird ggf. geaendert)
  uint8_t mess;             // Messung laeuft
  uint8_t burst_busy;       // vom Sketch: Burst-Ausgabe laeuft
  uint8_t burst_active;     // vom Sketch: Burst nimmt auf oder gibt aus
  // Anforderungen an den Sketch (vom Sketch zurueckzusetzen)
  uint8_t req_cycle_reset;
  uint8_t req_apply;        // Einstellungen geaendert
  uint8_t req_get;          // #SET-Liste ausgeben, dann #OK;GET
  uint8_t req_save;         // ins EEPROM schreiben, dann #OK;SAVE
  uint8_t req_burst;        // Burst scharf schalten (Parameter unten)
  uint8_t req_burst_stop;
  uint8_t b_chmask, b_edge, b_trig;
  uint16_t b_n, b_pre;
  struct PstSettings set;
};

void pst_cmd_init(struct PstCmd *c);
// Einzelzeichen: liefert 'v', 'e' (Sketch antwortet), 'm' (bereits umgeschaltet) oder 0
char pst_cmd_char(struct PstCmd *c, char ch);
// #-Zeile auswerten, Antwort (mit \r\n) in out; Rueckgabe Laenge
size_t pst_cmd_line(struct PstCmd *c, const char *line, char *out, size_t outsz);

// --------------------------------------------------------------------------
// Formatierung (alle Funktionen haengen an out an, Rueckgabe neue Laenge)

struct PstOut { char *buf; size_t len, cap; };
void pst_out_str(struct PstOut *o, const char *s);
void pst_out_char(struct PstOut *o, char c);
void pst_out_u32(struct PstOut *o, uint32_t v);
void pst_out_i32(struct PstOut *o, int32_t v);
void pst_out_hex(struct PstOut *o, uint32_t v, uint8_t upper);
// wie Arduino Print::printFloat (AVR: double == float)
void pst_out_float(struct PstOut *o, float v, uint8_t digits);
// Festkomma: v / 10^dec
void pst_out_fixed(struct PstOut *o, uint32_t v, uint8_t dec);

struct PstSensors {
  uint8_t  thermo;
  float    egt_c;           // MAX31855
  uint16_t lambda_temp;     // Sondentemperatur Spartan
  float    afr100;          // Afr (x100) wie 3.0.0
};

// Normales Telegramm (byte-identisch zu 3.0.0, Zeilenende nur '\n')
size_t pst_fmt_normal(char *out, size_t cap, uint16_t cycle, float rate,
                      uint32_t hz_ign, uint32_t hz_roll, const struct PstSensors *s);

struct PstDiag {
  uint16_t cycle;
  uint32_t t_ms;
  float    rate;
  uint32_t ign_mhz, roll_mhz;
  struct PstWin ign, roll;
  uint8_t  flags;
};
size_t pst_fmt_diag(char *out, size_t cap, const struct PstDiag *d, const struct PstSensors *s);
// Periode in Ticks -> "us.z" (1 Nachkommastelle), 0 -> "0"
void pst_out_period_us(struct PstOut *o, uint32_t ticks);

// Burst-Zeilen
size_t pst_fmt_burst_begin(char *out, size_t cap, const struct PstBurst *b);
size_t pst_fmt_burst_line(char *out, size_t cap, const struct PstBurst *b, uint16_t idx);
size_t pst_fmt_burst_end(char *out, size_t cap, const struct PstBurst *b);

// Antwort auf 'v' (wie 3.0.0, nur neue Versionsnummer) und 'e'
size_t pst_fmt_version(char *out, size_t cap, const struct PstSettings *s);
size_t pst_fmt_env(char *out, size_t cap, float temp, float press, float hum);

#endif
