/*
 * Host-Test PST-Diag 1 (ohne Hardware):
 *   #-Zeilenparser gemischt mit Einzelzeichen und 8-Byte-Befehlen der STM-LabVIEW,
 *   D-Telegramm, #RATE, #GET/#SET/#DEFAULTS/#SAVE, Einstellungs-Ablage (CRC/Version,
 *   leerer Flash), Flanken-Oszi mit Trigger (Doppelimpuls 39 %, Aussetzer, Sprung).
 *
 *   cc -std=c11 -Iinclude -Itest/stub src/protocol.c src/diag.c src/settings.c src/burst.c \
 *      src/fmt.c test/test_diag.c -lm -o /tmp/td && /tmp/td
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "analog.h"
#include "board.h"
#include "burst.h"
#include "capture.h"
#include "diag.h"
#include "egt.h"
#include "env_sensor.h"
#include "protocol.h"
#include "serial.h"
#include "settings.h"
#include "settings_store.h"

static int failures, checks;

static void check(const char *name, int ok)
{
    checks++;
    printf("%s %s\n", ok ? "OK  " : "FAIL", name);
    if (!ok)
        failures++;
}

static void check_str(const char *name, const char *got, const char *want)
{
    int ok = strcmp(got, want) == 0;
    check(name, ok);
    if (!ok)
        printf("     got:  \"%s\"\n     want: \"%s\"\n", got, want);
}

/* ------------------------------------------------------------ Attrappen */

static uint32_t tick;
static char outb[1 << 20];
static size_t outn;
static const char *rx;
static size_t rx_pos, rx_len;
static uint32_t rx_tick, rate_mhz, tx_free = 4096, applied;
static int streaming_at_save = -1;
static burst_t bu;

uint32_t HAL_GetTick(void) { return tick; }
bool serial_write(const void *d, size_t n)
{
    memcpy(outb + outn, d, n);
    outn += n;
    outb[outn] = '\0';
    return true;
}
bool serial_write_str(const char *s) { return serial_write(s, strlen(s)); }
int serial_read(void) { return rx_pos < rx_len ? (unsigned char)rx[rx_pos++] : -1; }
uint32_t serial_last_rx_tick(void) { return rx_tick; }
bool serial_take_break(void) { return false; }
uint32_t serial_tx_dropped(void) { return 0; }
uint32_t serial_tx_free(void) { return tx_free; }
bool egt_get(unsigned ch, float *t) { *t = ch ? 612.25f : 655.75f; return true; }
bool egt_enabled(unsigned ch) { (void)ch; return true; }
bool env_read(float *t, float *p, float *h) { *t = 21.46f; *p = 96512.4f; *h = 45.25f; return true; }
void env_init(void) {}
env_type_t env_type(void) { return ENV_BME280; }
unsigned env_address(void) { return 0x76; }
uint16_t analog_read(unsigned ch) { return ch == 1 ? 2048 : 1024; }
float analog_to_afr(uint16_t raw) { return 10.0f + 10.0f * raw / 4096.0f; }
const char *board_clock_source(void) { return "Test"; }
void capture_set_rate_mhz(uint32_t r) { rate_mhz = r; }
void capture_get_stats(capture_stats_t *s) { memset(s, 0, sizeof *s); }
float capture_timer_hz(void) { return 90e6f; }
void capture_apply_settings(void) { applied++; }
void capture_burst_start(const burst_cfg_t *c) { burst_arm(&bu, c); }
void capture_burst_stop(void) { burst_stop(&bu); }
const burst_t *capture_burst(void) { return &bu; }

/* Flash-Attrappe: gleiche Ablage-Logik wie settings_store.c, aber im RAM */
#define FLASH_SIZE (8u * SETTINGS_SLOT)
static uint8_t flash[FLASH_SIZE];
static int erases;

const char *settings_store_save(const settings_t *s, bool busy)
{
    streaming_at_save = busy;
    if (busy)
        return "messung laeuft";
    settings_t tmp;
    int slot;
    settings_scan(flash, FLASH_SIZE, &tmp, &slot);
    if (slot < 0) {
        memset(flash, 0xFF, FLASH_SIZE);
        erases++;
        slot = 0;
    }
    settings_serialize(s, flash + slot * SETTINGS_SLOT);
    return NULL;
}

/* ------------------------------------------------------------ Hilfen */

static void take(char *dst, size_t n)
{
    snprintf(dst, n, "%s", outb);
    outn = 0;
    outb[0] = '\0';
}

/* Bytes am Stueck senden, dann Sendepause (> 3 ms) */
static void send_raw(const char *c, size_t n)
{
    rx = c; rx_pos = 0; rx_len = n; rx_tick = tick;
    protocol_poll();
    tick += 10;
    protocol_poll();
}
static void send_cmd(const char *c) { send_raw(c, strlen(c)); }

static capture_frame_t mkframe(void)
{
    capture_frame_t f;
    memset(&f, 0, sizeof f);
    f.rate_hz = 60.0f;
    f.ign_hz = 50.0f;
    f.roll_hz = 725.5f;
    f.tick_us = 1.0f / 90.0f;
    f.ign_w.hz = 50.0f;
    f.ign_w.n = 1;
    f.ign_w.periods = 1;
    f.ign_w.pmin = f.ign_w.pmax = 1800000u;      /* 20 ms */
    f.ign_rej = 1;
    f.roll_w.hz = 725.5f;
    f.roll_w.n = 12;
    f.roll_w.periods = 12;
    f.roll_w.pmin = 124000u;                     /* 1377,8 us */
    f.roll_w.pmax = 124200u;                     /* 1380,0 us */
    return f;
}

static int count_char(const char *s, char c)
{
    int n = 0;
    for (; *s; s++)
        n += *s == c;
    return n;
}

static int count_lines_prefix(const char *s, const char *pre)
{
    int n = 0;
    size_t pl = strlen(pre);
    const char *p = s;
    while (*p) {
        if (strncmp(p, pre, pl) == 0)
            n++;
        const char *e = strchr(p, '\n');
        if (!e)
            break;
        p = e + 1;
    }
    return n;
}

/* ------------------------------------------------------------ Tests */

static void test_parser(void)
{
    char o[4096];
    puts("--- #-Parser gemischt mit Einzelzeichen und 8-Byte-Befehlen");

    send_cmd("#?\r\n");
    take(o, sizeof o);
    check_str("#? -> #CAP", o,
              "#CAP;proto=1;fw=PST-STM32 1.1.0;tick_ns=11.111;ch=I,R;burst_max=8192;modes=N,D;set=1\r\n");

    /* Einzelzeichen-Befehle wie bisher */
    send_cmd("e\n");
    take(o, sizeof o);
    check_str("'e' unveraendert", o, "21.5;96512;45.3\r\n");

    /* Zeichen in einer #-Zeile werden nicht als e/m/s/v ausgewertet */
    send_cmd("m");
    check("'m' startet", protocol_streaming());
    take(o, sizeof o);
    send_cmd("#SET timeout_ign_ms 500\n");          /* enthaelt e, m, s */
    take(o, sizeof o);
    check("#-Zeile mit e/m/s stoppt nicht", protocol_streaming());
    check_str("#SET-Antwort", o, "#OK;SET;timeout_ign_ms=500\r\n");
    send_cmd("s");
    check("'s' stoppt", !protocol_streaming());

    /* Reihenfolge: Zeichen vor dem '#' werden zuerst ausgefuehrt */
    send_cmd("e#MODE N\n");
    take(o, sizeof o);
    check_str("'e' vor #-Zeile im selben Block", o, "21.5;96512;45.3\r\n#OK;MODE;N\r\n");

    /* 8-Byte-Befehl direkt nach einer #-Zeile (\r\n wird verschluckt) */
    send_cmd("#MODE N\r\ntttttttt");
    take(o, sizeof o);
    check_str("#-Zeile + 8-Byte-Befehl am Stueck", o, "#OK;MODE;N\r\nt_ok\r\n");
    send_cmd("eeeeeeee");
    take(o, sizeof o);
    check_str("8-Byte 'eeeeeeee' unveraendert", o, "21.5;96512;45.3\r\n");
    send_cmd("m3101001");
    check("8-Byte 'mRabcdef' startet STM-LabVIEW (66,67 Hz)", protocol_streaming() && rate_mhz == 66667);
    capture_frame_t f = mkframe();
    protocol_on_frame(&f);
    check("STM-LabVIEW-Telegramm 20 Byte binaer", outn == 20);
    take(o, sizeof o);
    send_cmd("#?\n");
    take(o, sizeof o);
    check("#? waehrend STM-LabVIEW", strncmp(o, "#CAP;", 5) == 0);
    send_cmd("s");

    /* 'v' und '?' (Einzelzeichen) bleiben, '#?' ist etwas anderes */
    send_cmd("?");
    take(o, sizeof o);
    check("'?' = Versionsinfo wie bisher", strncmp(o, "PST-STM32 Version 1.1.0 vom ", 28) == 0);
    send_cmd("v#?\n");
    take(o, sizeof o);
    check("'v' dann '#?' im selben Block", strncmp(o, "PST-STM32 Version", 17) == 0 && strstr(o, "\r\n#CAP;") != NULL);

    /* Zeile in mehreren Stuecken, Gross/Klein */
    send_cmd("#mo");
    send_cmd("de d");
    send_cmd("\n");
    take(o, sizeof o);
    check_str("Zeile in Stuecken, klein geschrieben", o, "#OK;MODE;D\r\n");
    send_cmd("#MODE N\n");
    take(o, sizeof o);

    send_cmd("#FOO\n");
    take(o, sizeof o);
    check_str("unbekannter Befehl", o, "#ERR;?;unbekannt\r\n");
    send_cmd("#SET 0123456789012345678901234567890123456789012345678901234567890123\n");
    take(o, sizeof o);
    check_str("Zeile > 63 Zeichen", o, "#ERR;?;zu lang\r\n");

    /* Angefangene Zeile ohne Ende verfaellt nach 1 s, danach wirkt 'e' wieder */
    send_cmd("#MO");
    tick += 1100;
    send_cmd("e");
    take(o, sizeof o);
    check_str("unvollstaendige #-Zeile verfaellt nach 1 s", o, "21.5;96512;45.3\r\n");
}

static void test_mode_d(void)
{
    char o[8192];
    puts("--- Modus D, #RUN, #RATE, D-Telegramm");

    send_cmd("#MODE X\n");
    take(o, sizeof o);
    check_str("#MODE ungueltig", o, "#ERR;MODE;ungueltig\r\n");
    send_cmd("#MODE D\n");
    send_cmd("#RUN 1\n");
    take(o, sizeof o);
    check_str("#MODE D, #RUN 1", o, "#OK;MODE;D\r\n#OK;RUN;1\r\n");
    check("#RUN 1 startet mit 60 Hz", protocol_streaming() && rate_mhz == 60000);

    tick = 123456;
    capture_frame_t f = mkframe();
    protocol_on_frame(&f);
    take(o, sizeof o);
    check_str("D-Telegramm Format", o,
              "D;1;123456;60.00;50.000;725.500;656;nan;612;1;20000.0;20000.0;1;12;1377.8;1380.0;0;0\r\n");
    check("D-Telegramm 18 Felder", count_char(o, ';') == 17);

    f = mkframe();
    f.ign_w.timeout = 1;
    f.ign_w.hz = 0.0f;
    f.ign_w.n = 0;
    f.ign_w.pmin = f.ign_w.pmax = 0;
    f.roll_w.stale = 1;
    protocol_on_frame(&f);
    take(o, sizeof o);
    check_str("D-Telegramm Timeout Zuendung + Rolle stale (Flags 21)", o,
              "D;2;123456;60.00;0.000;725.500;656;nan;612;0;0.0;0.0;1;12;1377.8;1380.0;0;21\r\n");

    g_set.v[S_AFR_SRC] = 2;
    f = mkframe();
    protocol_on_frame(&f);
    take(o, sizeof o);
    check("D-Telegramm AFR aus PA1 = 12.50", strstr(o, ";656;12.50;612;") != NULL);
    g_set.v[S_AFR_SRC] = 0;

    /* Kein Autostopp im Modus D */
    f = mkframe();
    for (int i = 0; i < 60 * 6; i++) {
        f.ign_hz = f.roll_hz = 0.0f;
        protocol_on_frame(&f);
    }
    take(o, sizeof o);
    check("Modus D: kein Autostopp nach 6 s ohne Signal", protocol_streaming());

    send_cmd("#RATE 100\n");
    take(o, sizeof o);
    check_str("#RATE 100", o, "#OK;RATE;100\r\n");
    check("#RATE wirkt sofort (100 Hz)", rate_mhz == 100000);
    send_cmd("#RATE 101\n");
    send_cmd("#RATE 5\n");
    send_cmd("#RATE abc\n");
    take(o, sizeof o);
    check_str("#RATE ausserhalb 10..100", o,
              "#ERR;RATE;bereich 10-100\r\n#ERR;RATE;bereich 10-100\r\n#ERR;RATE;bereich 10-100\r\n");
    send_cmd("#RATE 60\n");
    send_cmd("#RUN 0\n");
    take(o, sizeof o);
    check("#RUN 0 stoppt", !protocol_streaming() && strstr(o, "#OK;RUN;0\r\n") != NULL);

    /* Zurueck in N: normales Telegramm byte-identisch */
    send_cmd("#MODE N\n");
    send_cmd("m");
    take(o, sizeof o);
    f = mkframe();
    protocol_on_frame(&f);
    take(o, sizeof o);
    check_str("Modus N: normales Telegramm wie 1.0.0", o, "1;60.00;50.00;725.50;656;0.00;612\n");
    send_cmd("s");
    take(o, sizeof o);
}

static void test_settings_cmd(void)
{
    char o[8192];
    puts("--- #GET/#SET/#DEFAULTS/#SAVE");

    send_cmd("#GET\n");
    take(o, sizeof o);
    check("#GET: je Einstellung eine Zeile", count_lines_prefix(o, "#SET;") == S_COUNT);
    check("#GET endet mit #OK;GET", strlen(o) > 9 && strcmp(o + strlen(o) - 9, "#OK;GET\r\n") == 0);
    check("Standard edge_ign=R", strstr(o, "#SET;edge_ign=R\r\n") != NULL);
    check("Standard ign_lockout_pct=25", strstr(o, "#SET;ign_lockout_pct=25\r\n") != NULL);
    check("Standard ign_win_lo_pct=0 (Erwartungsfenster aus)", strstr(o, "#SET;ign_win_lo_pct=0\r\n") != NULL);
    check("Standard rate_hz=60", strstr(o, "#SET;rate_hz=60\r\n") != NULL);
    check("Standard timeout_roll_ms=1000", strstr(o, "#SET;timeout_roll_ms=1000\r\n") != NULL);
    check("Standard afr_fs=20.00", strstr(o, "#SET;afr_fs=20.00\r\n") != NULL);

    applied = 0;
    send_cmd("#SET ign_lockout_pct 70\n");
    take(o, sizeof o);
    check_str("#SET ign_lockout_pct 70 (ersetzt Variante dp70)", o, "#OK;SET;ign_lockout_pct=70\r\n");
    check("#SET wendet sofort an", applied == 1 && g_set.v[S_IGN_LOCKOUT_PCT] == 70);
    send_cmd("#SET edge_roll f\n");
    send_cmd("#SET afr_0v 9.5\n");
    send_cmd("#GET afr_0v\n");
    take(o, sizeof o);
    check_str("#SET Flanke/Komma, #GET einzeln", o,
              "#OK;SET;edge_roll=F\r\n#OK;SET;afr_0v=9.50\r\n#SET;afr_0v=9.50\r\n#OK;GET\r\n");
    send_cmd("#SET ign_lockout_pct 96\n");
    send_cmd("#SET rate_hz 1000\n");
    send_cmd("#SET foo 1\n");
    send_cmd("#SET pullup\n");
    take(o, sizeof o);
    check_str("#SET Fehler", o,
              "#ERR;SET;ungueltiger wert\r\n#ERR;SET;ungueltiger wert\r\n"
              "#ERR;SET;unbekannter schluessel\r\n#ERR;SET;ungueltiger wert\r\n");
    check("ungueltiger Wert aendert nichts", g_set.v[S_IGN_LOCKOUT_PCT] == 70);

    /* Speichern: nicht waehrend einer Messung */
    send_cmd("m");
    send_cmd("#SAVE\n");
    take(o, sizeof o);
    check_str("#SAVE waehrend Messung -> Fehler", o, "#ERR;SAVE;messung laeuft\r\n");
    send_cmd("s");
    send_cmd("#SAVE\n");
    take(o, sizeof o);
    check_str("#SAVE", o, "#OK;SAVE\r\n");

    settings_t ld;
    int next;
    check("gespeicherte Einstellungen gefunden", settings_scan(flash, FLASH_SIZE, &ld, &next));
    check("geladen: ign_lockout_pct 70, edge_roll F, afr_0v 9.50",
          ld.v[S_IGN_LOCKOUT_PCT] == 70 && ld.v[S_EDGE_ROLL] == 0 && ld.v[S_AFR_0V] == 950);
    check("naechster freier Platz 1", next == 1);

    send_cmd("#DEFAULTS\n");
    take(o, sizeof o);
    check_str("#DEFAULTS", o, "#OK;DEFAULTS\r\n");
    check("#DEFAULTS: wieder 25 % / R", g_set.v[S_IGN_LOCKOUT_PCT] == 25 && g_set.v[S_EDGE_ROLL] == 1);
    check("#DEFAULTS fluechtig (Flash unveraendert)", settings_scan(flash, FLASH_SIZE, &ld, &next) && ld.v[S_IGN_LOCKOUT_PCT] == 70);

    /* Sektor voll -> loeschen und von vorn */
    for (int i = 0; i < 8; i++)
        send_cmd("#SAVE\n");
    take(o, sizeof o);
    check("Bereich voll: einmal geloescht, letzter Datensatz gilt",
          erases == 1 && settings_scan(flash, FLASH_SIZE, &ld, &next) && ld.v[S_IGN_LOCKOUT_PCT] == 25 && next == 1);
}

static void test_serialization(void)
{
    puts("--- Einstellungs-Ablage (Version, CRC, leerer Flash)");
    static uint8_t region[4 * SETTINGS_SLOT];
    settings_t s, d, ld;
    int next;

    memset(region, 0xFF, sizeof region);
    memset(&ld, 0x55, sizeof ld);
    check("leerer Flash: nichts gefunden", !settings_scan(region, sizeof region, &ld, &next));
    settings_defaults(&d);
    check("leerer Flash: Standardwerte, frei ab 0", memcmp(&ld, &d, sizeof d) == 0 && next == 0);

    settings_defaults(&s);
    s.v[S_IGN_LOCKOUT_PCT] = 70;
    s.v[S_RATE_HZ] = 100;
    uint8_t rec[SETTINGS_SLOT];
    settings_serialize(&s, rec);
    check("Datensatz: Kennung PSTS", rec[0] == 'P' && rec[1] == 'S' && rec[2] == 'T' && rec[3] == 'S');
    check("Datensatz: Version 1", rec[4] == 1 && rec[5] == 0);
    check("Datensatz: Rest geloescht (0xFF)", rec[SETTINGS_SLOT - 1] == 0xFF);
    check("Datensatz ruecklesbar", settings_deserialize(rec, &d) && memcmp(&d, &s, sizeof s) == 0);

    uint8_t bad[SETTINGS_SLOT];
    memcpy(bad, rec, sizeof bad);
    bad[8 + 4 * S_IGN_LOCKOUT_PCT] ^= 1;
    check("CRC falsch -> verworfen", !settings_deserialize(bad, &d));
    memcpy(bad, rec, sizeof bad);
    bad[4] = 2;
    check("andere Version -> verworfen", !settings_deserialize(bad, &d));

    /* aelterer Datensatz mit weniger Schluesseln: Rest = Standard */
    memcpy(bad, rec, sizeof bad);
    bad[6] = S_IGN_LOCKOUT_PCT + 1;
    {
        size_t n = 8u + 4u * bad[6];
        uint32_t crc = settings_crc32(bad, n);
        bad[n] = (uint8_t)crc; bad[n + 1] = (uint8_t)(crc >> 8);
        bad[n + 2] = (uint8_t)(crc >> 16); bad[n + 3] = (uint8_t)(crc >> 24);
    }
    check("weniger Schluessel: vorhandene gelesen, Rest Standard",
          settings_deserialize(bad, &d) && d.v[S_IGN_LOCKOUT_PCT] == 70 && d.v[S_RATE_HZ] == 60);

    /* Wert ausserhalb des Bereichs -> Standard */
    settings_t w = s;
    w.v[S_HW_FILTER] = 99;
    settings_serialize(&w, bad);
    check("Wert ausserhalb -> Standardwert", settings_deserialize(bad, &d) && d.v[S_HW_FILTER] == 15 && d.v[S_RATE_HZ] == 100);

    /* Fremddaten im ersten Platz, dann gueltig, dann defekt */
    memset(region, 0xFF, sizeof region);
    memset(region, 0x12, 16);
    memcpy(region + SETTINGS_SLOT, rec, SETTINGS_SLOT);
    memcpy(region + 2 * SETTINGS_SLOT, rec, SETTINGS_SLOT);
    region[2 * SETTINGS_SLOT + 9] ^= 0xFF;                 /* abgebrochenes Schreiben */
    check("Fremddaten/defekter Datensatz: letzter gueltiger gilt",
          settings_scan(region, sizeof region, &ld, &next) && ld.v[S_RATE_HZ] == 100 && next == 3);
    memcpy(region + 3 * SETTINGS_SLOT, rec, SETTINGS_SLOT);
    settings_scan(region, sizeof region, &ld, &next);
    check("Bereich voll -> -1 (loeschen)", next == -1);

    check("CRC32-Pruefwert (\"123456789\" = CBF43926)", settings_crc32("123456789", 9) == 0xCBF43926u);

    char buf[16];
    settings_defaults(&s);
    settings_format_value(&s, S_EDGE_IGN, buf, sizeof buf);
    check_str("Format Flanke", buf, "R");
    check("parse 'F' -> 0", settings_parse_value(&s, S_EDGE_IGN, "F") && s.v[S_EDGE_IGN] == 0);
    check("parse '10.567' -> 1056", settings_parse_value(&s, S_AFR_0V, "10.567") && s.v[S_AFR_0V] == 1056);
    check("parse '-1' ungueltig", !settings_parse_value(&s, S_RATE_HZ, "-1"));
}

/* ------------------------------------------------------------ Burst */

#define F_TIM 90000000.0

static void feed(burst_t *b, unsigned ch, unsigned lvl, double t)
{
    burst_edge(b, ch, lvl, (uint32_t)(int64_t)llround(t * F_TIM), (uint32_t)(t * 1000.0));
}

static void test_burst_parse(void)
{
    puts("--- #BURST Parameter");
    burst_cfg_t c;
    check("A 800 B T20 P400", burst_parse("A 800 B T20 P400", &c) == NULL && c.ch_mask == 3 && c.n == 800 &&
                                  c.edge == BURST_EDGE_B && c.trig_pct == 20 && c.pre == 400);
    check("Standard pre = n/2", burst_parse("I 100 r t15", &c) == NULL && c.pre == 50 && c.edge == BURST_EDGE_R);
    check("ohne Trigger pre = 0", burst_parse("R 10 F", &c) == NULL && c.trig_pct == 0 && c.pre == 0);
    check("Kanal falsch", burst_parse("X 10 F", &c) != NULL);
    check("n = 0", burst_parse("I 0 F", &c) != NULL);
    check("n > burst_max", burst_parse("I 8193 F", &c) != NULL);
    check("n = burst_max", burst_parse("I 8192 F", &c) == NULL);
    check("Flanke falsch", burst_parse("I 10 Q", &c) != NULL);
    check("T0", burst_parse("I 10 F T0", &c) != NULL);
    check("pre >= n", burst_parse("I 10 F T20 P10", &c) != NULL);
    check("Muell", burst_parse("I 10 F X", &c) != NULL);
}

static void run_trigger(const char *name, int kind)
{
    /* 100 Hz Zuendung (6000 1/min), steigende Flanken, Trigger 20 %, n = 40, pre = 20 */
    burst_cfg_t c;
    burst_parse("I 40 R T20 P20", &c);
    burst_arm(&bu, &c);
    const double T = 0.01;
    double tp = 0.1, t_event = -1.0;
    feed(&bu, 0, 1, tp);
    for (int i = 1; i < 200 && bu.state != BURST_DONE; i++) {
        double per = T;
        if (kind == 0 && i == 60) {                /* Doppelimpuls bei 39 % */
            t_event = tp + 0.39 * T;
            feed(&bu, 0, 1, t_event);
        }
        if (kind == 1 && i == 60)                  /* Aussetzer */
            per = 2.0 * T;
        if (kind == 2 && i >= 60)                  /* Sprung auf 130 Hz */
            per = T / 1.3;
        double tn = tp + per;
        if (kind != 0 && i == 60)
            t_event = tn;
        feed(&bu, 0, 1, tn);
        tp = tn;
    }
    char nm[96];
    snprintf(nm, sizeof nm, "%s: ausgeloest, fertig", name);
    check(nm, bu.state == BURST_DONE);
    snprintf(nm, sizeof nm, "%s: trig_idx = pre = 20", name);
    check(nm, burst_trig_idx(&bu) == 20);
    unsigned ch, lvl;
    uint32_t ts, ts0;
    burst_entry(&bu, 20, &ch, &lvl, &ts);
    snprintf(nm, sizeof nm, "%s: Eintrag 20 = ausloesende Flanke", name);
    check(nm, ts == (uint32_t)(int64_t)llround(t_event * F_TIM) && ch == 0 && lvl == 1);
    int mono = 1;
    burst_entry(&bu, 0, &ch, &lvl, &ts0);
    for (uint32_t i = 1; i < 40; i++) {
        burst_entry(&bu, i, &ch, &lvl, &ts);
        mono &= (int32_t)(ts - ts0) > 0;
        ts0 = ts;
    }
    snprintf(nm, sizeof nm, "%s: 40 Eintraege zeitlich aufsteigend", name);
    check(nm, mono);
}

static void test_burst_engine(void)
{
    puts("--- Flanken-Oszi: Trigger");
    run_trigger("Doppelimpuls 39 %", 0);
    run_trigger("Aussetzer", 1);
    run_trigger("Sprung 100 -> 130 Hz", 2);

    /* Kein Trigger bei gleichmaessigem Signal mit +-5 % Jitter */
    burst_cfg_t c;
    burst_parse("I 40 R T20", &c);
    burst_arm(&bu, &c);
    double t = 0;
    unsigned r = 1;
    for (int i = 0; i < 500; i++) {
        r = r * 1103515245u + 12345u;
        t += 0.01 * (1.0 + 0.05 * ((int)((r >> 16) % 201u) - 100) / 100.0);
        feed(&bu, 0, 1, t);
    }
    check("gleichmaessig mit 5 % Jitter: kein Trigger (T20)", bu.state == BURST_ARMED);
    check("verworfen wird nichts (lost 0)", bu.lost == 0);

    /* Trigger ueber den 32-bit-Ueberlauf */
    burst_parse("R 16 F T20 P8", &c);
    burst_arm(&bu, &c);
    uint32_t ts = 0xFFFFFFFFu - 20u * 90000u;
    for (int i = 0; i < 30 && bu.state != BURST_DONE; i++) {
        burst_edge(&bu, 1, 0, ts, 0);
        ts += i == 20 ? 45000u : 90000u;           /* bei i = 21 halbe Periode */
    }
    check("Trigger ueber 32-bit-Ueberlauf", bu.state == BURST_DONE);

    /* Sofort, beide Kanaele, beide Flanken; Pegel und Kanal je Eintrag */
    burst_parse("A 6 B", &c);
    burst_arm(&bu, &c);
    burst_edge(&bu, 0, 1, 100, 0);
    burst_edge(&bu, 1, 1, 150, 0);
    burst_edge(&bu, 0, 0, 200, 0);
    burst_edge(&bu, 1, 0, 250, 0);
    burst_edge(&bu, 0, 1, 300, 0);
    burst_edge(&bu, 1, 1, 350, 0);
    burst_edge(&bu, 0, 0, 400, 0);                 /* nach Ende: ignoriert */
    unsigned ch, lvl;
    uint32_t t6;
    burst_entry(&bu, 3, &ch, &lvl, &t6);
    check("A/B sofort: fertig nach 6, Eintrag 3 = R/0/250", bu.state == BURST_DONE && ch == 1 && lvl == 0 && t6 == 250);

    /* Kanal- und Flankenauswahl */
    burst_parse("I 2 F", &c);
    burst_arm(&bu, &c);
    burst_edge(&bu, 1, 0, 10, 0);                  /* Rolle: nicht gewaehlt */
    burst_edge(&bu, 0, 1, 20, 0);                  /* steigend: nicht gewaehlt */
    burst_edge(&bu, 0, 0, 30, 0);
    check("Kanal/Flanke gefiltert", bu.count == 1);

    /* Abstand > 23 s -> lost */
    burst_edge(&bu, 0, 0, 40, 30000);
    check("Abstand > 2^31 Ticks wird als lost gezaehlt", bu.state == BURST_DONE && bu.lost == 1);
}

static void test_burst_output(void)
{
    char o[16384];
    puts("--- #BURST ueber das Protokoll, Telegramme aussetzen");
    burst_stop(&bu);

    send_cmd("#BURST I 3 R\n");
    take(o, sizeof o);
    check_str("#BURST ohne Trigger -> ARMED", o, "#OK;BURST;ARMED;ch=I;n=3;edge=R;trig=0;pre=0\r\n");
    send_cmd("#BURST I 3 R\n");
    take(o, sizeof o);
    check_str("zweiter #BURST -> aktiv", o, "#ERR;BURST;aktiv\r\n");
    send_cmd("#BURST STOP\n");
    take(o, sizeof o);
    check_str("#BURST STOP", o, "#OK;BURST;STOP\r\n");
    send_cmd("#BURST Z 3 R\n");
    take(o, sizeof o);
    check_str("#BURST Fehler", o, "#ERR;BURST;kanal\r\n");

    send_cmd("#BURST A 800 B T20 P400\n");
    take(o, sizeof o);
    check_str("#BURST mit Trigger (Beispiel der Spezifikation)", o,
              "#OK;BURST;ARMED;ch=A;n=800;edge=B;trig=20;pre=400\r\n");
    send_cmd("#BURST STOP\n");
    take(o, sizeof o);

    send_cmd("#MODE D\n");
    send_cmd("#RUN 1\n");
    send_cmd("#BURST R 3 F\n");
    take(o, sizeof o);
    capture_frame_t f = mkframe();
    protocol_on_frame(&f);
    take(o, sizeof o);
    check("Flag 8 (Burst aktiv) im D-Telegramm", strlen(o) > 4 && strcmp(o + strlen(o) - 4, ";8\r\n") == 0);

    burst_edge(&bu, 1, 0, 1000, 0);
    burst_edge(&bu, 1, 0, 2000, 0);
    burst_edge(&bu, 1, 0, 4294967295u, 0);
    tx_free = 0;                                    /* Sendepuffer voll: Ausgabe wartet */
    protocol_poll();
    protocol_on_frame(&f);                          /* Zyklus 2: wird ausgesetzt */
    take(o, sizeof o);
    check_str("waehrend der Ausgabe kein Telegramm", o, "");
    tx_free = 4096;
    protocol_poll();
    take(o, sizeof o);
    check_str("Burst-Ausgabe", o,
              "#BURST;BEGIN;n=3;tick_ns=11.111;trig_idx=-1;ch=R\r\n"
              "B;0;R;0;1000\r\nB;1;R;0;2000\r\nB;2;R;0;4294967295\r\n"
              "#BURST;END;n=3;lost=0\r\n");
    protocol_on_frame(&f);
    take(o, sizeof o);
    check("naechstes D-Telegramm: Zyklus 3, Flag 40 (verdraengt)",
          strncmp(o, "D;3;", 4) == 0 && strcmp(o + strlen(o) - 5, ";40\r\n") == 0);
    protocol_on_frame(&f);
    take(o, sizeof o);
    check("danach Flags wieder 0", strcmp(o + strlen(o) - 4, ";0\r\n") == 0);
    send_cmd("#RUN 0\n");
    send_cmd("#MODE N\n");
    take(o, sizeof o);
}

int main(void)
{
    memset(flash, 0xFF, sizeof flash);             /* geloeschter Flash */
    settings_defaults(&g_set);
    protocol_init();
    test_parser();
    test_mode_d();
    test_settings_cmd();
    test_serialization();
    test_burst_parse();
    test_burst_engine();
    test_burst_output();
    printf("\n%s (%d Pruefungen, %d Fehler)\n", failures ? "FEHLGESCHLAGEN" : "ALLE TESTS OK", checks, failures);
    return failures ? 1 : 0;
}
