// Host-Tests fuer pst_core (g++ nativ, ohne AVR-Header): make -C test
#include "../pst_core.h"
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>
#include <math.h>

static int g_pass = 0, g_fail = 0;
#define CHECK(c) do { if (c) g_pass++; else { g_fail++; printf("FEHLER %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)
#define CHECK_STR(a, b) do { std::string _a(a), _b(b); if (_a == _b) g_pass++; else { g_fail++; \
  printf("FEHLER %s:%d:\n  ist:  \"%s\"\n  soll: \"%s\"\n", __FILE__, __LINE__, esc(_a).c_str(), esc(_b).c_str()); } } while (0)

static std::string esc(const std::string &s)
{
  std::string r;
  for (char c : s) {
    if (c == '\r') r += "\\r"; else if (c == '\n') r += "\\n"; else r += c;
  }
  return r;
}

// --------------------------------------------------------------------------
// Hilfen
static std::string fl(float v, int d)
{
  char b[32]; PstOut o = { b, 0, sizeof(b) }; pst_out_float(&o, v, (uint8_t)d); return std::string(b, o.len);
}

struct Ev { int type; char ch; std::string line; };
static std::vector<Ev> feed(PstParser *p, const char *s, size_t n = (size_t)-1)
{
  std::vector<Ev> r;
  if (n == (size_t)-1) n = strlen(s);
  for (size_t i = 0; i < n; i++) {
    char ch = 0;
    int t = pst_parser_feed(p, s[i], &ch);
    if (t == PST_P_CHAR) r.push_back({t, ch, ""});
    else if (t == PST_P_LINE) r.push_back({t, 0, p->buf});
    else if (t == PST_P_LONG) r.push_back({t, 0, ""});
  }
  return r;
}

static std::string cmd(PstCmd *c, const char *line)
{
  char out[160];
  size_t n = pst_cmd_line(c, line, out, sizeof(out));
  return std::string(out, n);
}

// Kanal-Simulation: Zeitstempel wie die ISR (ICR + Ueberlauf, Start nahe 2^32)
struct Sim {
  PstChan ch[2];
  PstBurst b;
  Sim() {
    pst_chan_init(&ch[0]); pst_chan_init(&ch[1]);
    memset(&b, 0, sizeof(b));
    pst_chan_config(&ch[0], 'F', 5, 500, 0, 0);
    pst_chan_config(&ch[1], 'F', 50, 1000, 0, 0);
  }
  void edge(int k, uint32_t ts, uint8_t lvl, uint8_t missed = 0) { pst_edge(&ch[k], &b, (uint8_t)k, ts, lvl, missed); }
};

// --------------------------------------------------------------------------
static void test_float_print()
{
  // Arduino Print::printFloat (AVR: float)
  CHECK_STR(fl(41.666f, 2), "41.67");
  CHECK_STR(fl(16000000.0f / 317 / 1024, 2), "49.29");
  CHECK_STR(fl(16000000.0f / 318 / 1024, 2), "49.14");
  CHECK_STR(fl(20.0f, 2), "20.00");
  CHECK_STR(fl(1470.0f / 100, 2), "14.70");
  CHECK_STR(fl(NAN, 0), "nan");
  CHECK_STR(fl(INFINITY, 1), "inf");
  CHECK_STR(fl(5e9f, 2), "ovf");
  CHECK_STR(fl(-3.25f, 1), "-3.2");      // float-Rundung wie Arduino AVR (3.3 - 3 = 0.2999)
  CHECK_STR(fl(22.46f, 1), "22.5");
  CHECK_STR(fl(96788.4f, 0), "96788");
  CHECK_STR(fl(1.999f, 2), "2.00");
  CHECK_STR(fl(0.0f, 2), "0.00");
  CHECK_STR(fl(1023.75f, 0), "1024");
}

static void test_fmt_normal()
{
  PstSensors s;
  // Kunden-Mitschnitt 3.0.0 mit thermo=1, Thermoelement offen, ohne Spartan:
  //   "1;41.67;49;869;nan;20.00;740\n"
  s.thermo = 1; s.egt_c = NAN; s.lambda_temp = 740; s.afr100 = 2000;
  char b[160];
  float rate = 16000000UL / (float)(119 + 256); rate = rate / 1024;
  size_t n = pst_fmt_normal(b, sizeof(b), 1, rate, 49, 869, &s);
  CHECK_STR(std::string(b, n), "1;41.67;49;869;nan;20.00;740\n");
  rate = 16000000UL / (float)(61 + 256); rate = rate / 1024;
  n = pst_fmt_normal(b, sizeof(b), 2, rate, 54, 975, &s);
  CHECK_STR(std::string(b, n), "2;49.29;54;975;nan;20.00;740\n");
  // thermo=1 mit Messwert
  s.egt_c = 812.25f; s.afr100 = 1.0f * 1470;
  n = pst_fmt_normal(b, sizeof(b), 65535, 61.04f, 0, 12000, &s);
  CHECK_STR(std::string(b, n), "65535;61.04;0;12000;812;14.70;740\n");
  // thermo=0: 5. Feld = Sondentemperatur, kein 7. Feld (wie 3.0.0)
  s.thermo = 0; s.lambda_temp = 780; s.afr100 = ((float)1250 / 1000) * 1470;
  n = pst_fmt_normal(b, sizeof(b), 7, 49.14f, 55, 880, &s);
  CHECK_STR(std::string(b, n), "7;49.14;55;880;780;18.37\n");   // wie AVR: 18.38f - 18 = 0.37999
}

static void test_fmt_version_env()
{
  PstSettings st; pst_settings_defaults(&st);
  char b[200];
  size_t n = pst_fmt_version(b, sizeof(b), &st);
  CHECK_STR(std::string(b, n),
            "Version 3.1.0 vom 26.10.26;\r\nRingspeicher Zuendsignal:5;\r\nRingspeicher Rolle:50;\r\n"
            "Adresse BME280:76;\r\nThermoelement (1=ja 0=nein):0;\r\n");
  st.bme_addr = 0x77; st.thermo = 1; st.ring_roll = 30;
  n = pst_fmt_version(b, sizeof(b), &st);
  CHECK_STR(std::string(b, n),
            "Version 3.1.0 vom 26.10.26;\r\nRingspeicher Zuendsignal:5;\r\nRingspeicher Rolle:30;\r\n"
            "Adresse BME280:77;\r\nThermoelement (1=ja 0=nein):1;\r\n");
  n = pst_fmt_env(b, sizeof(b), 22.5f, 96788.0f, 52.5f);
  CHECK_STR(std::string(b, n), "22.5;96788;52.5\r\n");
}

static void test_fmt_diag()
{
  PstSensors s; s.thermo = 0; s.egt_c = 0; s.lambda_temp = 740; s.afr100 = 2000;
  PstDiag d; memset(&d, 0, sizeof(d));
  d.cycle = 12; d.t_ms = 123456; d.rate = 49.29f; d.ign_mhz = 55001; d.roll_mhz = 1000500;
  d.ign.edges = 1; d.ign.pmin = 290909; d.ign.pmax = 290909; d.ign.rej = 0;
  d.roll.edges = 20; d.roll.pmin = 15990; d.roll.pmax = 16010; d.roll.rej = 2;
  d.flags = PST_FL_BURST | PST_FL_DISPLACED | PST_FL_ST_ROLL | PST_FL_ST_IGN;
  char b[160];
  size_t n = pst_fmt_diag(b, sizeof(b), &d, &s);
  CHECK_STR(std::string(b, n),
            "D;12;123456;49.29;55.001;1000.500;740;20.00;nan;1;18181.8;18181.8;0;20;999.4;1000.6;2;78\r\n");
  // thermo=1, Timeout, keine Perioden
  s.thermo = 1; s.egt_c = NAN;
  memset(&d, 0, sizeof(d));
  d.flags = PST_FL_TO_IGN | PST_FL_TO_ROLL | PST_FL_LOSS | PST_FL_BURST;
  n = pst_fmt_diag(b, sizeof(b), &d, &s);
  CHECK_STR(std::string(b, n), "D;0;0;0.00;0.000;0.000;nan;20.00;740;0;0;0;0;0;0;0;0;F\r\n");
  // Laengstes Telegramm passt in den Puffer der Hauptschleife (144)
  memset(&d, 0xFF, sizeof(d));
  d.rate = 99999.99f; d.ign.pmin = d.ign.pmax = d.roll.pmin = d.roll.pmax = 3000UL * 16000UL;
  s.egt_c = -1234.0f;
  n = pst_fmt_diag(b, sizeof(b), &d, &s);
  CHECK(n < 140);
  CHECK(b[n - 1] == '\n');
}

static void test_period_us()
{
  char b[32]; PstOut o;
  o = { b, 0, sizeof(b) }; pst_out_period_us(&o, 0); CHECK_STR(std::string(b, o.len), "0");
  o = { b, 0, sizeof(b) }; pst_out_period_us(&o, 16); CHECK_STR(std::string(b, o.len), "1.0");
  o = { b, 0, sizeof(b) }; pst_out_period_us(&o, 17); CHECK_STR(std::string(b, o.len), "1.1");   // 1.0625
  o = { b, 0, sizeof(b) }; pst_out_period_us(&o, 1); CHECK_STR(std::string(b, o.len), "0.1");    // 0.0625
  o = { b, 0, sizeof(b) }; pst_out_period_us(&o, 48000000UL); CHECK_STR(std::string(b, o.len), "3000000.0");
}

// --------------------------------------------------------------------------
static void test_parser()
{
  PstParser p; pst_parser_init(&p);
  // LabVIEW: Einzelzeichen ohne Zeilenende
  auto e = feed(&p, "vem");
  CHECK(e.size() == 3);
  CHECK(e[0].type == PST_P_CHAR && e[0].ch == 'v');
  CHECK(e[1].ch == 'e' && e[2].ch == 'm');
  // Serieller Monitor mit "Neue Zeile" / CRLF
  e = feed(&p, "m\nm\r\n");
  CHECK(e.size() == 2 && e[0].ch == 'm' && e[1].ch == 'm');
  // #-Zeile: Zeichen darin sind keine Einzelbefehle (e, m, v in "#SET thermo 1")
  e = feed(&p, "#SET thermo 1\nv#MODE D\r\nm#RUN 1\r");
  CHECK(e.size() == 5);
  CHECK(e[0].type == PST_P_LINE && e[0].line == "#SET thermo 1");
  CHECK(e[1].type == PST_P_CHAR && e[1].ch == 'v');
  CHECK(e[2].type == PST_P_LINE && e[2].line == "#MODE D");
  CHECK(e[3].type == PST_P_CHAR && e[3].ch == 'm');
  CHECK(e[4].type == PST_P_LINE && e[4].line == "#RUN 1");
  // "\r\n" nach einer Zeile erzeugt nichts Zusaetzliches
  e = feed(&p, "#?\r\n");
  CHECK(e.size() == 1 && e[0].line == "#?");
  // maximal 63 Zeichen (inkl. '#'); 64 -> zu lang, danach wieder normal
  std::string l63 = "#" + std::string(62, 'x');
  e = feed(&p, (l63 + "\n").c_str());
  CHECK(e.size() == 1 && e[0].type == PST_P_LINE && e[0].line == l63);
  std::string l64 = "#" + std::string(63, 'm');
  e = feed(&p, (l64 + "\nv").c_str());
  CHECK(e.size() == 2 && e[0].type == PST_P_LONG && e[1].type == PST_P_CHAR && e[1].ch == 'v');
  // Zeile ueber mehrere Aufrufe verteilt
  e = feed(&p, "#BUR");
  CHECK(e.empty());
  e = feed(&p, "ST STOP\n");
  CHECK(e.size() == 1 && e[0].line == "#BURST STOP");
}

static void test_cmd_chars()
{
  PstCmd c; pst_cmd_init(&c);
  CHECK(pst_cmd_char(&c, 'v') == 'v');
  CHECK(pst_cmd_char(&c, 'e') == 'e');
  CHECK(c.mess == 0);
  CHECK(pst_cmd_char(&c, 'm') == 'm' && c.mess == 1 && !c.req_cycle_reset);
  CHECK(pst_cmd_char(&c, 'm') == 'm' && c.mess == 0 && c.req_cycle_reset);
  CHECK(pst_cmd_char(&c, 'x') == 0);
  CHECK(pst_cmd_char(&c, 's') == 0);     // 's' gibt es nur beim F446
}

static void test_cmd_lines()
{
  PstCmd c; pst_cmd_init(&c);
  CHECK_STR(cmd(&c, "#?"), "#CAP;proto=1;fw=PST-Mega 3.1.0;tick_ns=62.5;ch=I,R;burst_max=800;modes=N,D;set=1\r\n");
  CHECK(c.mode == 'N');
  CHECK_STR(cmd(&c, "#MODE D"), "#OK;MODE;D\r\n");
  CHECK(c.mode == 'D');
  CHECK_STR(cmd(&c, "#mode n"), "#OK;MODE;N\r\n");
  CHECK_STR(cmd(&c, "#MODE X"), "#ERR;MODE;ungueltig\r\n");
  CHECK(c.mode == 'N');
  CHECK_STR(cmd(&c, "#RUN 1"), "#OK;RUN;1\r\n");
  CHECK(c.mess == 1);
  CHECK_STR(cmd(&c, "#RUN 1"), "#OK;RUN;1\r\n");    // eindeutig, kein Umschalten
  CHECK(c.mess == 1 && !c.req_cycle_reset);
  CHECK_STR(cmd(&c, "#RUN 0"), "#OK;RUN;0\r\n");
  CHECK(c.mess == 0 && c.req_cycle_reset);
  CHECK_STR(cmd(&c, "#RUN 2"), "#ERR;RUN;ungueltig\r\n");
  CHECK_STR(cmd(&c, "#RATE 100"), "#ERR;RATE;nicht unterstuetzt\r\n");
  CHECK_STR(cmd(&c, "#FOO"), "#ERR;?;unbekannt\r\n");
  CHECK_STR(cmd(&c, "#"), "#ERR;?;unbekannt\r\n");
  CHECK_STR(cmd(&c, "#GET"), "");
  CHECK(c.req_get);
  CHECK_STR(cmd(&c, "#SAVE"), "");
  CHECK(c.req_save);
  c.req_save = 0; c.mess = 1;
  CHECK_STR(cmd(&c, "#SAVE"), "#ERR;SAVE;messung laeuft\r\n");
  c.mess = 0; c.burst_active = 1;
  CHECK_STR(cmd(&c, "#SAVE"), "#ERR;SAVE;messung laeuft\r\n");
  CHECK(!c.req_save);
  c.burst_active = 0;
  CHECK_STR(cmd(&c, "#SET roll_min_period_us 1000000"), "#OK;SET;roll_min_period_us=1000000\r\n");
  CHECK_STR(cmd(&c, "#DEFAULTS"), "#OK;DEFAULTS\r\n");

  // #SET
  CHECK_STR(cmd(&c, "#SET thermo 1"), "#OK;SET;thermo=1\r\n");
  CHECK(c.set.thermo == 1 && c.req_apply);
  CHECK_STR(cmd(&c, "#SET edge_ign r"), "#OK;SET;edge_ign=R\r\n");
  CHECK_STR(cmd(&c, "#SET bme_addr 77"), "#OK;SET;bme_addr=0x77\r\n");
  CHECK_STR(cmd(&c, "#SET bme_addr 0x76"), "#OK;SET;bme_addr=0x76\r\n");
  CHECK_STR(cmd(&c, "#SET timeout_ign_ms=750"), "#OK;SET;timeout_ign_ms=750\r\n");
  CHECK_STR(cmd(&c, "#SET timeout_ign_ms 5"), "#ERR;SET;ungueltiger Wert\r\n");
  CHECK_STR(cmd(&c, "#SET ring_roll 65"), "#ERR;SET;ungueltiger Wert\r\n");
  CHECK_STR(cmd(&c, "#SET pullup x"), "#ERR;SET;ungueltiger Wert\r\n");
  CHECK_STR(cmd(&c, "#SET edge_ign B"), "#ERR;SET;ungueltiger Wert\r\n");
  CHECK_STR(cmd(&c, "#SET rate_hz 100"), "#ERR;SET;unbekannter Schluessel\r\n");
  CHECK_STR(cmd(&c, "#SET thermo"), "#ERR;SET;Syntax\r\n");
  CHECK_STR(cmd(&c, "#SET ign_min_period_us 99999999999"), "#ERR;SET;ungueltiger Wert\r\n");
  CHECK(c.set.timeout_ign_ms == 750 && c.set.ring_roll == 50);
  CHECK_STR(cmd(&c, "#DEFAULTS"), "#OK;DEFAULTS\r\n");
  CHECK(c.set.thermo == 0 && c.set.edge_ign == 'F' && c.set.timeout_ign_ms == 500);

  // #BURST
  CHECK_STR(cmd(&c, "#BURST A 800 B T20 P400"), "#OK;BURST;ARMED;ch=A;n=800;edge=B;trig=20;pre=400\r\n");
  CHECK(c.req_burst && c.b_chmask == 3 && c.b_n == 800 && c.b_edge == 'B' && c.b_trig == 20 && c.b_pre == 400);
  CHECK_STR(cmd(&c, "#BURST I 100 F T30"), "#OK;BURST;ARMED;ch=I;n=100;edge=F;trig=30;pre=50\r\n");
  CHECK_STR(cmd(&c, "#BURST R 600 R"), "#OK;BURST;ARMED;ch=R;n=600;edge=R;trig=0;pre=0\r\n");
  CHECK(c.b_chmask == 2 && c.b_trig == 0);
  CHECK_STR(cmd(&c, "#BURST R 801 R"), "#ERR;BURST;n\r\n");
  CHECK_STR(cmd(&c, "#BURST R 0 R"), "#ERR;BURST;n\r\n");
  CHECK_STR(cmd(&c, "#BURST X 10 R"), "#ERR;BURST;Kanal\r\n");
  CHECK_STR(cmd(&c, "#BURST I 10 Q"), "#ERR;BURST;Flanke\r\n");
  CHECK_STR(cmd(&c, "#BURST I 10 F T0"), "#ERR;BURST;Trigger\r\n");
  CHECK_STR(cmd(&c, "#BURST I 10 F T20 P10"), "#ERR;BURST;Vorlauf\r\n");
  CHECK_STR(cmd(&c, "#BURST I 10"), "#ERR;BURST;Syntax\r\n");
  c.req_burst_stop = 0;
  CHECK_STR(cmd(&c, "#BURST STOP"), "#OK;BURST;STOP\r\n");
  CHECK(c.req_burst_stop);
  c.burst_busy = 1;
  CHECK_STR(cmd(&c, "#BURST I 10 F"), "#ERR;BURST;Ausgabe laeuft\r\n");
}

static void test_settings()
{
  PstSettings s; pst_settings_defaults(&s);
  const char *expect[PST_SET_COUNT] = {
    "edge_ign=F", "edge_roll=F", "timeout_ign_ms=500", "timeout_roll_ms=1000", "pullup=1", "thermo=0",
    "bme_addr=0x76", "afr_addr=1", "ring_ign=5", "ring_roll=50", "ign_lockout_pct=0",
    "ign_min_period_us=0", "roll_min_period_us=0" };
  char b[40];
  for (uint8_t i = 0; i < PST_SET_COUNT; i++) {
    size_t n = pst_setting_format(&s, i, b, sizeof(b));
    CHECK_STR(std::string(b, n), expect[i]);
  }
  CHECK(pst_setting_find("ring_roll") == 9);
  CHECK(pst_setting_find("x") == -1);

  // EEPROM: Rundreise
  s.edge_ign = 'R'; s.thermo = 1; s.bme_addr = 0x77; s.ring_roll = 30; s.ign_lockout_pct = 25;
  s.ign_min_period_us = 1500; s.roll_min_period_us = 1000000; s.timeout_roll_ms = 2500;
  uint8_t img[PST_EE_SIZE];
  pst_settings_pack(&s, img);
  PstSettings t;
  CHECK(pst_settings_unpack(&t, img) == 1);
  CHECK(t.edge_ign == 'R' && t.edge_roll == 'F' && t.timeout_ign_ms == 500 && t.timeout_roll_ms == 2500);
  CHECK(t.pullup == 1 && t.thermo == 1 && t.bme_addr == 0x77 && t.afr_addr == 1);
  CHECK(t.ring_ign == 5 && t.ring_roll == 30 && t.ign_lockout_pct == 25);
  CHECK(t.ign_min_period_us == 1500 && t.roll_min_period_us == 1000000);
  // leeres EEPROM (0xFF) -> Standardwerte
  uint8_t ff[PST_EE_SIZE]; memset(ff, 0xFF, sizeof(ff));
  CHECK(pst_settings_unpack(&t, ff) == 0);
  CHECK(t.thermo == 0 && t.ring_roll == 50 && t.bme_addr == 0x76);
  // Pruefsumme falsch
  uint8_t bad[PST_EE_SIZE]; memcpy(bad, img, sizeof(bad)); bad[10] ^= 1;
  CHECK(pst_settings_unpack(&t, bad) == 0 && t.ring_roll == 50);
  // andere Version
  memcpy(bad, img, sizeof(bad)); bad[2] = 2;
  CHECK(pst_settings_unpack(&t, bad) == 0);
  // gueltige CRC, aber Wert ausserhalb des Bereichs -> Standardwerte
  memcpy(bad, img, sizeof(bad)); bad[4 + 10] = 200;   // ring_ign
  uint16_t crc = pst_crc16(bad, PST_EE_SIZE - 2);
  bad[PST_EE_SIZE - 2] = (uint8_t)crc; bad[PST_EE_SIZE - 1] = (uint8_t)(crc >> 8);
  CHECK(pst_settings_unpack(&t, bad) == 0 && t.ring_ign == 5);
  // CRC-16/CCITT-FALSE Pruefwert
  CHECK(pst_crc16((const uint8_t *)"123456789", 9) == 0x29B1);
}

// --------------------------------------------------------------------------
static void test_timestamp()
{
  // Capture ohne ausstehenden Ueberlauf
  CHECK(pst_ts_from_capture(0x1234, 5, 0) == 0x00051234UL);
  // TOV steht noch aus, ICR klein -> gehoert zum naechsten Ueberlauf
  CHECK(pst_ts_from_capture(0x0003, 5, 1) == 0x00060003UL);
  // TOV steht aus, ICR gross -> Capture war vor dem Ueberlauf
  CHECK(pst_ts_from_capture(0xFFF0, 5, 1) == 0x0005FFF0UL);
  // Ueberlaufzaehler laeuft ueber (32-bit-Zeitstempel nach 268 s)
  CHECK(pst_ts_from_capture(0x0001, 0xFFFF, 1) == 0x00000001UL);
  // Periode ueber den 32-bit-Ueberlauf
  uint32_t a = pst_ts_from_capture(0xFF00, 0xFFFF, 0);
  uint32_t b = pst_ts_from_capture(0x0100, 0x0000, 0);
  CHECK((uint32_t)(b - a) == 0x200);
}

// Rechteck mit Frequenz f an Kanal k einspeisen, Telegramme alle t_tel Sekunden
static void test_every_period()
{
  Sim s;
  // 55 Hz Zuendung, fallende Flanke, Start nahe 2^32 (Ueberlauf im Test)
  const double f = 55.0;
  const double tick = 1.0 / 16e6;
  uint32_t t0 = 0xFFF00000UL;
  int n_edges = 0;
  PstWin w;
  uint32_t total_periods = 0;
  double t = 0;
  double next_tel = 0.0203;
  PstDiagChan d = {0};
  for (int i = 0; i < 2000; i++) {
    double te = i / f;
    while (next_tel < te) {   // Telegramme dazwischen
      pst_chan_take(&s.ch[0], &w);
      total_periods += w.periods;
      next_tel += 0.0203;
    }
    uint32_t ts = t0 + (uint32_t)llround(te / tick);
    s.edge(0, ts, 0);
    s.edge(0, ts + 8000, 1);   // steigende Flanke wird bei edge_ign=F ignoriert
    n_edges++;
    t = te;
  }
  pst_chan_take(&s.ch[0], &w);
  total_periods += w.periods;
  // jede Periode gezaehlt (3.0.0: nur jede zweite)
  CHECK(total_periods == 1999);
  // Ringspeicher (Normalmodus): 55 Hz
  // Neues Fenster mit ein paar Perioden
  for (int i = 2000; i < 2010; i++) s.edge(0, t0 + (uint32_t)llround(i / f / tick), 0);
  pst_chan_take(&s.ch[0], &w);
  CHECK(w.periods == 10);
  CHECK(pst_hz_ring(&w) == 55);
  uint8_t stale;
  uint32_t mhz = pst_diag_freq(&d, &w, &stale);
  CHECK(mhz == 55000 && !stale);
  CHECK(w.pmin >= 290908 && w.pmax <= 290910);
  (void)t; (void)n_edges;
}

static void test_freq_rounding_and_ring()
{
  Sim s;
  pst_chan_config(&s.ch[1], 'F', 4, 1000, 0, 0);
  // Perioden 16000, 16000, 16001, 15999 -> 1000 Hz
  uint32_t ts = 1000;
  s.edge(1, ts, 0);
  uint32_t per[] = { 16000, 16000, 16001, 15999, 16000, 16000 };
  for (uint32_t p : per) { ts += p; s.edge(1, ts, 0); }
  PstWin w;
  pst_chan_take(&s.ch[1], &w);
  CHECK(w.ring_n == 4);
  CHECK(w.ring_sum == 16001 + 15999 + 16000 + 16000);
  CHECK(pst_hz_ring(&w) == 1000);
  CHECK(w.periods == 6 && w.span == 96000);
  CHECK(pst_mhz_window(w.periods, w.span) == 1000000);
  CHECK(w.pmin == 15999 && w.pmax == 16001);
  // Rundung: 16 MHz / 290909 = 55.0001 Hz; 16 MHz / 33334 = 479.99 -> 480
  PstWin r; memset(&r, 0, sizeof(r)); r.ring_n = 1; r.ring_sum = 33334;
  CHECK(pst_hz_ring(&r) == 480);
  r.ring_sum = 33343;   // 479.86 -> 480
  CHECK(pst_hz_ring(&r) == 480);
  r.ring_sum = 33368;   // 479.50 -> 480 (3.0.0 hatte abgeschnitten: 479)
  CHECK(pst_hz_ring(&r) == 480);
  CHECK(pst_mhz_window(3, 3 * 290909) == 55000);  // 55.0002 -> 55.000
  CHECK(pst_mhz_window(1, 7) == 2285714286UL);
  CHECK(pst_mhz_window(0, 100) == 0);
  // Ringgroesse aendern -> Ring leer
  pst_chan_config(&s.ch[1], 'F', 10, 1000, 0, 0);
  pst_chan_take(&s.ch[1], &w);
  CHECK(w.ring_n == 0 && pst_hz_ring(&w) == 0);
}

static void test_timeout()
{
  Sim s;
  PstWin w;
  PstDiagChan d = {0};
  uint8_t stale;
  // Startzustand: noch keine Flanke -> Timeout, 0 Hz
  CHECK(pst_chan_timeout(&s.ch[0], 12345) == 1);
  pst_chan_take(&s.ch[0], &w);
  CHECK(w.timeout && pst_hz_ring(&w) == 0 && pst_diag_freq(&d, &w, &stale) == 0 && !stale);

  // 50 Hz
  uint32_t ts = 0xFFFF0000UL;
  for (int i = 0; i < 10; i++) { s.edge(0, ts, 0); ts += 320000; }
  uint32_t last = ts - 320000;
  CHECK(pst_chan_timeout(&s.ch[0], last + 100000) == 0);
  pst_chan_take(&s.ch[0], &w);
  CHECK(!w.timeout && pst_hz_ring(&w) == 50);
  CHECK(pst_diag_freq(&d, &w, &stale) == 50000 && !stale);

  // 100 ms ohne Flanke: noch kein Timeout (500 ms), keine Periode -> stale, letzter Wert
  CHECK(pst_chan_timeout(&s.ch[0], last + 1600000) == 0);
  pst_chan_take(&s.ch[0], &w);
  CHECK(pst_diag_freq(&d, &w, &stale) == 50000 && stale);
  CHECK(pst_hz_ring(&w) == 50);
  // 501 ms -> Timeout: 0 Hz, Ringspeicher geleert (3.0.0 behielt den alten Wert)
  CHECK(pst_chan_timeout(&s.ch[0], last + 501UL * 16000) == 1);
  pst_chan_take(&s.ch[0], &w);
  CHECK(w.timeout && w.ring_n == 0 && pst_hz_ring(&w) == 0);
  CHECK(pst_diag_freq(&d, &w, &stale) == 0 && !stale);
  // Signal kommt wieder: erste Flanke bildet keine Periode (keine 501-ms-Periode)
  ts = last + 900UL * 16000;
  s.edge(0, ts, 0);
  pst_chan_take(&s.ch[0], &w);
  CHECK(!w.timeout && w.periods == 0 && w.pmax == 0 && pst_hz_ring(&w) == 0);
  CHECK(pst_diag_freq(&d, &w, &stale) == 0 && stale);
  s.edge(0, ts + 400000, 0);   // 40 Hz
  pst_chan_take(&s.ch[0], &w);
  CHECK(pst_hz_ring(&w) == 40 && pst_diag_freq(&d, &w, &stale) == 40000);

  // Pause > Timeout zwischen zwei Telegrammen (Hauptschleife hat es nicht bemerkt):
  // ISR beginnt neu, alte Frequenz wird nicht als "stale" weitergemeldet
  ts += 400000;
  s.edge(0, ts + 600UL * 16000, 0);
  pst_chan_take(&s.ch[0], &w);
  CHECK(w.restart && w.periods == 0 && w.ring_n == 0);
  CHECK(pst_diag_freq(&d, &w, &stale) == 0 && stale);
  // Rolle: Timeout 1000 ms
  s.edge(1, 1000, 0);
  s.edge(1, 1000 + 16000, 0);
  CHECK(pst_chan_timeout(&s.ch[1], 1000 + 16000 + 999UL * 16000) == 0);
  CHECK(pst_chan_timeout(&s.ch[1], 1000 + 16000 + 1001UL * 16000) == 1);
}

static void test_filter()
{
  Sim s;
  // Doppelimpulsfilter 25 %: Stoerimpuls bei 40 % der Periode wird verworfen
  pst_chan_config(&s.ch[0], 'F', 5, 500, 0, 25);
  uint32_t ts = 5000, P = 290909;
  s.edge(0, ts, 0);
  ts += P; s.edge(0, ts, 0);          // 1. Periode (Referenz)
  s.edge(0, ts + P * 4 / 10, 0);      // Doppelimpuls bei 0.4 P -> ok (>25 %), wird angenommen!
  PstWin w;
  pst_chan_take(&s.ch[0], &w);
  CHECK(w.rej == 0 && w.periods == 2);
  // nach kurzem Impuls ist last_p = 0.4P; naechste regulaere Flanke (0.6P) > 25 % von 0.4P
  Sim s2;
  pst_chan_config(&s2.ch[0], 'F', 5, 500, 0, 50);
  ts = 5000;
  s2.edge(0, ts, 0);
  ts += P; s2.edge(0, ts, 0);
  s2.edge(0, ts + P * 4 / 10, 0);     // < 50 % -> verworfen
  ts += P; s2.edge(0, ts, 0);
  pst_chan_take(&s2.ch[0], &w);
  CHECK(w.rej == 1 && w.periods == 2 && w.edges == 3);
  CHECK(w.pmin == P && w.pmax == P);
  // Mindestperiode 1000 us
  Sim s3;
  pst_chan_config(&s3.ch[1], 'R', 50, 1000, 1000, 0);
  s3.edge(1, 100, 1);
  s3.edge(1, 100 + 15000, 1);         // 937 us -> verworfen
  s3.edge(1, 100 + 17000, 1);         // 1062 us -> ok
  s3.edge(1, 100 + 17000, 0);         // fallend: falsche Richtung, nicht gezaehlt
  pst_chan_take(&s3.ch[1], &w);
  CHECK(w.rej == 1 && w.periods == 1 && w.pmin == 17000 && w.edges == 2);
}

// --------------------------------------------------------------------------
// Burst

static void check_burst_order(const PstBurst *b)
{
  // idx 0..n-1 zeitlich aufsteigend (modulo 2^32)
  char line[48];
  uint32_t prev = 0;
  bool ok = true;
  for (uint16_t i = 0; i < b->n; i++) {
    pst_fmt_burst_line(line, sizeof(line), b, i);
    unsigned idx; char ch; unsigned lvl; unsigned long t;
    if (sscanf(line, "B;%u;%c;%u;%lu", &idx, &ch, &lvl, &t) != 4 || idx != i) ok = false;
    if (i && (int32_t)((uint32_t)t - prev) < 0) ok = false;
    prev = (uint32_t)t;
  }
  CHECK(ok);
}

static uint32_t burst_ts(const PstBurst *b, uint16_t i)
{
  char line[48]; unsigned idx; char ch; unsigned lvl; unsigned long t;
  pst_fmt_burst_line(line, sizeof(line), b, i);
  sscanf(line, "B;%u;%c;%u;%lu", &idx, &ch, &lvl, &t);
  return (uint32_t)t;
}

static void test_burst_no_trigger()
{
  Sim s;
  pst_burst_arm(&s.b, s.ch, 1, 6, 'B', 0, 0);
  CHECK(pst_chan_needs_toggle(&s.b, &s.ch[0], 0) == 1);
  CHECK(pst_chan_needs_toggle(&s.b, &s.ch[1], 1) == 0);
  uint32_t ts = 0xFFFFFF00UL;   // Ueberlauf mitten im Burst
  for (int i = 0; i < 10; i++) {
    s.edge(0, ts, 1); s.edge(1, ts + 3, 0);
    s.edge(0, ts + 40, 0);
    ts += 100;
  }
  CHECK(s.b.state == PST_B_DONE);
  char b[80];
  size_t n = pst_fmt_burst_begin(b, sizeof(b), &s.b);
  CHECK_STR(std::string(b, n), "#BURST;BEGIN;n=6;tick_ns=62.5;trig_idx=-1;ch=I\r\n");
  n = pst_fmt_burst_line(b, sizeof(b), &s.b, 0);
  CHECK_STR(std::string(b, n), "B;0;I;1;4294967040\r\n");
  n = pst_fmt_burst_line(b, sizeof(b), &s.b, 1);
  CHECK_STR(std::string(b, n), "B;1;I;0;4294967080\r\n");
  n = pst_fmt_burst_line(b, sizeof(b), &s.b, 5);
  CHECK_STR(std::string(b, n), "B;5;I;0;4294967280\r\n");
  check_burst_order(&s.b);
  n = pst_fmt_burst_end(b, sizeof(b), &s.b);
  CHECK_STR(std::string(b, n), "#BURST;END;n=6;lost=0\r\n");
  // Messung lief nebenbei weiter (nur fallende Flanken)
  PstWin w; pst_chan_take(&s.ch[0], &w);
  CHECK(w.periods == 9 && w.pmin == 100 && w.pmax == 100);
}

static void test_burst_edges_and_channels()
{
  Sim s;
  // nur fallende Flanken der Rolle; Messung steigend -> beide Flanken noetig (toggle)
  pst_chan_config(&s.ch[1], 'R', 50, 1000, 0, 0);
  pst_burst_arm(&s.b, s.ch, 2, 4, 'F', 0, 0);
  CHECK(pst_chan_needs_toggle(&s.b, &s.ch[1], 1) == 1);
  CHECK(pst_chan_needs_toggle(&s.b, &s.ch[0], 0) == 0);
  for (int i = 0; i < 6; i++) { s.edge(1, 1000 + i * 100, 1); s.edge(1, 1050 + i * 100, 0); s.edge(0, 1010 + i * 100, 0); }
  CHECK(s.b.state == PST_B_DONE);
  char b[80];
  for (int i = 0; i < 4; i++) {
    size_t n = pst_fmt_burst_line(b, sizeof(b), &s.b, (uint16_t)i);
    char exp[48]; snprintf(exp, sizeof(exp), "B;%d;R;0;%d\r\n", i, 1050 + i * 100);
    CHECK_STR(std::string(b, n), exp);
  }
  // gleiche Flanke wie Messung -> kein Umschalten
  pst_burst_arm(&s.b, s.ch, 2, 4, 'R', 0, 0);
  CHECK(pst_chan_needs_toggle(&s.b, &s.ch[1], 1) == 0);

  // beide Kanaele (A), gemeinsamer Puffer, verpasste Gegenflanke -> lost
  Sim a;
  pst_burst_arm(&a.b, a.ch, 3, 8, 'B', 0, 0);
  a.edge(0, 100, 0);
  a.edge(1, 105, 0);
  a.edge(0, 150, 1, 1);   // missed
  a.edge(1, 160, 1);
  a.edge(0, 200, 0);
  a.edge(1, 205, 0);
  a.edge(0, 250, 1);
  a.edge(1, 260, 1);
  CHECK(a.b.state == PST_B_DONE);
  size_t n = pst_fmt_burst_begin(b, sizeof(b), &a.b);
  CHECK_STR(std::string(b, n), "#BURST;BEGIN;n=8;tick_ns=62.5;trig_idx=-1;ch=A\r\n");
  n = pst_fmt_burst_line(b, sizeof(b), &a.b, 1);
  CHECK_STR(std::string(b, n), "B;1;R;0;105\r\n");
  n = pst_fmt_burst_line(b, sizeof(b), &a.b, 2);
  CHECK_STR(std::string(b, n), "B;2;I;1;150\r\n");
  n = pst_fmt_burst_end(b, sizeof(b), &a.b);
  CHECK_STR(std::string(b, n), "#BURST;END;n=8;lost=1\r\n");
  // weitere Flanken nach DONE aendern den Puffer nicht
  a.edge(0, 300, 0);
  CHECK(burst_ts(&a.b, 7) == 260);
}

// Zuendsignal mit Ereignis: liefert Burst nach Trigger
enum { EV_DOUBLE, EV_DROP, EV_STEP, EV_SMALLSTEP };
static void run_trigger(int ev, uint8_t pct, bool expect_trig)
{
  Sim s;
  const uint32_t P = 290909;          // 55 Hz
  const uint16_t N = 40, PRE = 10;
  pst_burst_arm(&s.b, s.ch, 1, N, 'F', pct, PRE);
  uint32_t ts = 0xFFF00000UL;
  uint32_t ev_ts = 0;
  for (int i = 0; i < 200; i++) {
    uint32_t per = P + (uint32_t)((i * 7919) % 1500) - 750;  // +-0,25 % Jitter
    if (i == 100) {
      if (ev == EV_DOUBLE) { ev_ts = ts + P / 3; s.edge(0, ev_ts, 0); }
      if (ev == EV_DROP) { per = 2 * P; ev_ts = ts + per; }
    }
    if (ev == EV_STEP && i >= 100) { per = P * 13 / 10; if (i == 100) ev_ts = ts + per; }
    if (ev == EV_SMALLSTEP && i >= 100) { per = P * 11 / 10; if (i == 100) ev_ts = ts + per; }
    ts += per;
    s.edge(0, ts, 0);
  }
  if (!expect_trig) {
    CHECK(s.b.state == PST_B_REC && !s.b.triggered);
    return;
  }
  CHECK(s.b.state == PST_B_DONE && s.b.triggered);
  char b[80];
  size_t n = pst_fmt_burst_begin(b, sizeof(b), &s.b);
  CHECK_STR(std::string(b, n), "#BURST;BEGIN;n=40;tick_ns=62.5;trig_idx=10;ch=I\r\n");
  CHECK(burst_ts(&s.b, PRE) == ev_ts);     // Ausloeseflanke steht an trig_idx
  if (burst_ts(&s.b, PRE) != ev_ts) printf("  ev=%d: %u statt %u\n", ev, (unsigned)burst_ts(&s.b, PRE), (unsigned)ev_ts);
  check_burst_order(&s.b);
}

static void test_burst_trigger()
{
  run_trigger(EV_DOUBLE, 20, true);      // Doppelimpuls
  run_trigger(EV_DROP, 20, true);        // Aussetzer
  run_trigger(EV_STEP, 20, true);        // Sprung +30 %
  run_trigger(EV_SMALLSTEP, 20, false);  // Sprung +10 % < 20 %
  run_trigger(EV_SMALLSTEP, 5, true);    // ... aber > 5 %

  // Trigger erst nach Vorlauf: Ereignis vor pre Eintraegen loest nicht aus
  Sim s;
  pst_burst_arm(&s.b, s.ch, 1, 100, 'F', 20, 50);
  uint32_t ts = 0;
  for (int i = 0; i < 20; i++) { ts += 1000; s.edge(0, ts, 0); }
  ts += 3000; s.edge(0, ts, 0);          // Aussetzer bei Eintrag 21 (< pre)
  CHECK(!s.b.triggered);
  for (int i = 0; i < 40; i++) { ts += 1000; s.edge(0, ts, 0); }
  CHECK(!s.b.triggered);
  ts += 1500; s.edge(0, ts, 0);          // Eintrag 62 -> Ausloesung
  CHECK(s.b.triggered && s.b.state == PST_B_REC);
  for (int i = 0; i < 49; i++) { ts += 1000; s.edge(0, ts, 0); }
  CHECK(s.b.state == PST_B_DONE);
  CHECK(burst_ts(&s.b, 50) == ts - 49 * 1000);
  CHECK(burst_ts(&s.b, 99) == ts);

  // Trigger bei beiden Flanken: Mittel je Flankenrichtung (Pulsbreite aendert sich nicht)
  Sim t;
  pst_burst_arm(&t.b, t.ch, 1, 20, 'B', 20, 10);
  ts = 0;
  for (int i = 0; i < 30; i++) { t.edge(0, ts, 1); t.edge(0, ts + 100, 0); ts += 1000; }
  CHECK(!t.b.triggered);
  t.edge(0, ts + 500, 1);                // steigende Flanke 50 % spaeter
  CHECK(t.b.triggered);
  // P0 (pre = 0): Ausloeseflanke ist Eintrag 0
  Sim u;
  pst_burst_arm(&u.b, u.ch, 1, 3, 'F', 50, 0);
  ts = 0;
  for (int i = 0; i < 10; i++) { ts += 1000; u.edge(0, ts, 0); }
  ts += 3000; u.edge(0, ts, 0);
  u.edge(0, ts + 1000, 0); u.edge(0, ts + 2000, 0);
  CHECK(u.b.state == PST_B_DONE && burst_ts(&u.b, 0) == ts);
  // n=1 mit Trigger: fertig mit der Ausloeseflanke
  pst_burst_arm(&u.b, u.ch, 1, 1, 'F', 50, 0);
  for (int i = 0; i < 10; i++) { ts += 1000; u.edge(0, ts, 0); }
  ts += 3000; u.edge(0, ts, 0);
  CHECK(u.b.state == PST_B_DONE && burst_ts(&u.b, 0) == ts);
}

static void test_deviates()
{
  // 20 %: q = 51
  CHECK(!pst_deviates(1190, 1000, 51));
  CHECK(pst_deviates(1210, 1000, 51));
  CHECK(pst_deviates(790, 1000, 51));
  // grosse Werte ohne Ueberlauf (Mittel 4 s, Trigger 200 %)
  CHECK(!pst_deviates(PST_PERIOD_CLAMP, PST_PERIOD_CLAMP, 512));
  CHECK(pst_deviates(0, PST_PERIOD_CLAMP, 51));
}

int main()
{
  test_float_print();
  test_fmt_normal();
  test_fmt_version_env();
  test_fmt_diag();
  test_period_us();
  test_parser();
  test_cmd_chars();
  test_cmd_lines();
  test_settings();
  test_timestamp();
  test_every_period();
  test_freq_rounding_and_ring();
  test_timeout();
  test_filter();
  test_burst_no_trigger();
  test_burst_edges_and_channels();
  test_burst_trigger();
  test_deviates();
  printf("%d bestanden, %d fehlgeschlagen\n", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
