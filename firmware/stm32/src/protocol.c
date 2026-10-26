#include "protocol.h"

#include <stdbool.h>
#include <string.h>

#include "analog.h"
#include "board.h"
#include "config.h"
#include "diag.h"
#include "egt.h"
#include "env_sensor.h"
#include "fmt.h"
#include "serial.h"
#include "settings.h"
#include "stm32f4xx_hal.h"

#define BURST_GAP_MS 3u      /* Pause, die einen Befehl abschliesst */
#define LINE_MAX 63u         /* #-Zeile: max. Zeichen nach dem '#' */
#define LINE_TIMEOUT_MS 1000u /* unvollstaendige #-Zeile verwerfen */

typedef enum { PROTO_ASCII, PROTO_LEGACY } proto_t;

static bool streaming;
static proto_t proto = PROTO_ASCII;
static uint16_t cycle;
static uint32_t idle_frames;
static bool activity_seen;

/* Einstellungen der STM-LabVIEW ("m"-Befehl mit 7 Ziffern) */
static struct {
    bool egt1, egt2, afr_adc1, afr_adc2;
} legacy;

/* Telegrammraten der STM-LabVIEW, Index = erste Ziffer nach 'm' [mHz] */
static const uint32_t legacy_rate_mhz[6] = {20000, 33333, 50000, 66667, 100000, 133333};

static uint8_t burst[16];
static unsigned burst_len;

/* #-Zeilen (PST-Diag 1) */
static char line_buf[LINE_MAX + 1u];
static unsigned line_len;
static bool in_line, line_overflow, skip_lf;
static uint32_t line_tick;

static void send(const fmt_t *f) { serial_write(f->buf, f->len); }

static void start(proto_t p, uint32_t rate_mhz)
{
    if (!streaming || proto != p)
        cycle = 0;                /* laeuft schon: Zyklen durchzaehlen (COM-Fehlerrate) */
    proto = p;
    capture_set_rate_mhz(rate_mhz);
    streaming = true;
    idle_frames = 0;
    activity_seen = false;
}

static void stop(void)
{
    streaming = false;
    cycle = 0;
}

int protocol_streaming(void) { return streaming; }

static uint32_t ascii_rate_mhz(void) { return (uint32_t)SET(S_RATE_HZ) * 1000u; }

void protocol_run(int on)
{
    if (on)
        start(PROTO_ASCII, ascii_rate_mhz());
    else
        stop();
}

void protocol_settings_changed(void)
{
    /* STM-LabVIEW behaelt ihre eigene Rate */
    if (!(streaming && proto == PROTO_LEGACY))
        capture_set_rate_mhz(ascii_rate_mhz());
}

/* ------------------------------------------------------------ Antworten */

static void reply_env(void)
{
    float t, p, h;
    if (env_read(&t, &p, &h)) {
        t += ENV_T_OFFSET_C;
        p += ENV_P_OFFSET_PA;
        h += ENV_H_OFFSET_PCT;
    } else {
        /* Kein Sensor: Normbedingungen DIN 70020 -> Korrekturfaktor 1,000 */
        t = 20.0f;
        p = 101325.0f;
        h = 0.0f;
    }
    fmt_t f;
    fmt_reset(&f);
    fmt_fixed(&f, t, 1);
    fmt_char(&f, ';');
    fmt_fixed(&f, p, 0);
    fmt_char(&f, ';');
    fmt_fixed(&f, h, 1);
    fmt_str(&f, "\r\n");
    send(&f);
}

static void line(const char *label, const char *value)
{
    fmt_t f;
    fmt_reset(&f);
    fmt_str(&f, label);
    fmt_str(&f, value);
    fmt_str(&f, ";\r\n");
    send(&f);
}

static void reply_version(void)
{
    fmt_t f;
    capture_stats_t s;
    capture_get_stats(&s);

    line("PST-STM32 Version " FW_VERSION " vom ", FW_DATE);
    line("Takt:", board_clock_source());

    fmt_reset(&f);
    fmt_str(&f, "Messfrequenz [Hz]:");
    fmt_u32(&f, (uint32_t)SET(S_RATE_HZ));
    fmt_str(&f, ";\r\n");
    send(&f);

    fmt_reset(&f);
    fmt_str(&f, "Zuendung: min. Perioden ");
    fmt_u32(&f, (uint32_t)SET(S_IGN_MIN_PERIODS));
    fmt_str(&f, ", Sperrzeit [us] ");
    fmt_u32(&f, (uint32_t)SET(S_IGN_MIN_PERIOD_US));
    fmt_str(&f, ", Doppelimpulsfilter [%] ");
    fmt_u32(&f, (uint32_t)SET(S_IGN_LOCKOUT_PCT));
    fmt_str(&f, ";\r\n");
    send(&f);

    fmt_reset(&f);
    fmt_str(&f, "Rolle: min. Perioden ");
    fmt_u32(&f, (uint32_t)SET(S_ROLL_MIN_PERIODS));
    fmt_str(&f, ", Sperrzeit [us] ");
    fmt_u32(&f, (uint32_t)SET(S_ROLL_MIN_PERIOD_US));
    fmt_str(&f, ";\r\n");
    send(&f);

    fmt_reset(&f);
    fmt_str(&f, "Klimasensor:");
    if (env_type() == ENV_NONE) {
        env_init();
    }
    switch (env_type()) {
    case ENV_BME280: fmt_str(&f, "BME280 @0x"); fmt_hex8(&f, (uint8_t)env_address()); break;
    case ENV_BMP280: fmt_str(&f, "BMP280 @0x"); fmt_hex8(&f, (uint8_t)env_address()); break;
    default: fmt_str(&f, "keiner (sende 20 degC / 1013,25 mbar)"); break;
    }
    fmt_str(&f, ";\r\n");
    send(&f);

    float t;
    fmt_reset(&f);
    fmt_str(&f, "Thermoelement 1/2 erkannt (1=ja 0=nein):");
    fmt_u32(&f, egt_get(0, &t));
    fmt_char(&f, '/');
    fmt_u32(&f, egt_get(1, &t));
    fmt_str(&f, ";\r\n");
    send(&f);

    line("AFR-Quelle:", SET(S_AFR_SRC) == 1 ? "ADC_CH1 (PB0)" : SET(S_AFR_SRC) == 2 ? "ADC_CH2 (PA1)" : "keine");

    fmt_reset(&f);
    fmt_str(&f, "Impulse Zuendung/Rolle:");
    fmt_u32(&f, s.ign_edges);
    fmt_char(&f, '/');
    fmt_u32(&f, s.roll_edges);
    fmt_str(&f, " verworfen:");
    fmt_u32(&f, s.ign_rejected);
    fmt_char(&f, '/');
    fmt_u32(&f, s.roll_rejected);
    fmt_str(&f, ";\r\n");
    send(&f);
}

/* ------------------------------------------------------------ Befehle */

static void ascii_command(char c)
{
    switch (c) {
    case 'e':
        stop();                   /* wie Mega: vor dem Lauf keine Telegramme */
        reply_env();
        break;
    case 'm':
        if (M_TOGGLES && streaming && proto == PROTO_ASCII)
            stop();
        else
            start(PROTO_ASCII, ascii_rate_mhz());
        break;
    case 's':
        stop();
        break;
    case 'v':
    case '?':
        reply_version();
        break;
    default:
        break;                    /* \r, \n, Leerzeichen, Unbekanntes ignorieren */
    }
}

static bool is_digit(uint8_t c) { return c >= '0' && c <= '9'; }

/* 8-Byte-Befehle der STM-LabVIEW. true = erkannt und ausgefuehrt. */
static bool legacy_command(const uint8_t *b, unsigned n)
{
    if (n != 8)
        return false;

    switch (b[0]) {
    case 't':
        proto = PROTO_LEGACY;
        serial_write_str("t_ok\r\n");
        return true;
    case 'e':
        proto = PROTO_LEGACY;
        stop();
        reply_env();
        return true;
    case 'l':
        /* Lambda-Heizung: Board noch nicht unterstuetzt -> immer "aus" */
        proto = PROTO_LEGACY;
        serial_write_str("l1_0\r\n");
        return true;
    case 'm':
        for (unsigned i = 1; i < 8; i++)
            if (!is_digit(b[i]))
                return false;
        {
            unsigned r = (unsigned)(b[1] - '0');
            legacy.egt1 = b[2] == '1';
            legacy.egt2 = b[3] == '1';
            /* b[4], b[5] = CJ125 Lambda 1/2: Board noch nicht unterstuetzt */
            legacy.afr_adc1 = b[6] == '1';
            legacy.afr_adc2 = b[7] == '1';
            start(PROTO_LEGACY, legacy_rate_mhz[r < 6 ? r : 1]);
        }
        return true;
    default:
        return false;
    }
}

static void handle_burst(void)
{
    if (legacy_command(burst, burst_len))
        return;
    for (unsigned i = 0; i < burst_len; i++)
        ascii_command((char)burst[i]);
}

void protocol_init(void)
{
    burst_len = 0;
    in_line = false;
    diag_init();
    stop();
}

/* Zeichen einer #-Zeile; \r oder \n schliesst sie ab */
static void line_char(int b)
{
    if (b == '\r' || b == '\n') {
        in_line = false;
        skip_lf = b == '\r';
        if (line_overflow) {
            diag_line_too_long();
        } else {
            while (line_len && line_buf[line_len - 1u] == ' ')
                line_len--;
            line_buf[line_len] = '\0';
            diag_line(line_buf);
        }
    } else if (line_len < LINE_MAX) {
        line_buf[line_len++] = (char)b;
    } else {
        line_overflow = true;
    }
}

void protocol_poll(void)
{
    int b;
    while ((b = serial_read()) >= 0) {
        /* PST-Diag 1: '#' beginnt eine Zeile, die bis \n getrennt geparst wird.
         * LabVIEW sendet nie '#'; deren Befehle laufen unveraendert weiter. */
        if (in_line && HAL_GetTick() - line_tick >= LINE_TIMEOUT_MS)
            in_line = false;              /* angefangene Zeile ohne Ende verwerfen */
        if (in_line) {
            line_char(b);
            line_tick = HAL_GetTick();
            continue;
        }
        if (skip_lf && b == '\n') {
            skip_lf = false;              /* \r\n: \n gehoert noch zur Zeile */
            continue;
        }
        skip_lf = false;
        if (b == '#') {
            if (burst_len) {              /* vorher empfangene Zeichen zuerst (Reihenfolge) */
                handle_burst();
                burst_len = 0;
            }
            in_line = true;
            line_overflow = false;
            line_len = 0;
            line_tick = HAL_GetTick();
            continue;
        }
        if (burst_len < sizeof burst)
            burst[burst_len++] = (uint8_t)b;
        else
            ascii_command((char)b);   /* sehr lange Eingabe: zeichenweise */
    }
    if (burst_len && HAL_GetTick() - serial_last_rx_tick() >= BURST_GAP_MS) {
        handle_burst();
        burst_len = 0;
    }

    if (in_line && HAL_GetTick() - line_tick >= LINE_TIMEOUT_MS)
        in_line = false;                  /* angefangene Zeile ohne Ende verwerfen */

    if (BREAK_STOPS_STREAM && serial_take_break() && proto == PROTO_ASCII)
        stop();

    diag_poll();
}

/* ------------------------------------------------------------ Telegramme */

static float afr_value(unsigned src)
{
    return src ? analog_to_afr(analog_read(src)) : 0.0f;
}

static void send_ascii(const capture_frame_t *fr)
{
    float egt1 = 0.0f, egt2 = 0.0f;
    egt_get(0, &egt1);
    egt_get(1, &egt2);

    fmt_t f;
    fmt_reset(&f);
    fmt_u32(&f, cycle);
    fmt_char(&f, ';');
    fmt_fixed(&f, fr->rate_hz, 2);
    fmt_char(&f, ';');
    fmt_fixed(&f, fr->ign_hz, (unsigned)SET(S_FREQ_DEC));
    fmt_char(&f, ';');
    fmt_fixed(&f, fr->roll_hz, (unsigned)SET(S_FREQ_DEC));
    fmt_char(&f, ';');
    fmt_fixed(&f, egt1, 0);
    fmt_char(&f, ';');
    fmt_fixed(&f, afr_value((unsigned)SET(S_AFR_SRC)), 2);
    fmt_char(&f, ';');
    fmt_fixed(&f, egt2, 0);
    fmt_char(&f, '\n');
    send(&f);
}

static uint16_t u16_scaled(float v, float scale)
{
    float x = v * scale + 0.5f;
    if (x < 0.0f) return 0;
    if (x > 65535.0f) return 65535;
    return (uint16_t)x;
}

static void put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void send_legacy(const capture_frame_t *fr)
{
    float egt1 = 0.0f, egt2 = 0.0f;
    if (legacy.egt1) egt_get(0, &egt1);
    if (legacy.egt2) egt_get(1, &egt2);

    uint16_t adc1 = analog_read(1);
    uint16_t adc2 = analog_read(2);
    float afr1 = 0.0f, afr2 = 0.0f;
    if (legacy.afr_adc1) afr1 = analog_to_afr(adc1);
    if (legacy.afr_adc2) afr2 = analog_to_afr(adc2);
    if (!legacy.afr_adc1) afr1 = afr2;
    if (!legacy.afr_adc2) afr2 = afr1;

    uint8_t d[20];
    put_u16(&d[0], cycle);
    put_u16(&d[2], u16_scaled(fr->rate_hz, 256.0f));
    put_u16(&d[4], u16_scaled(fr->ign_hz, 8.0f));
    put_u16(&d[6], u16_scaled(fr->roll_hz, 8.0f));
    put_u16(&d[8], u16_scaled(egt1, 16.0f));
    put_u16(&d[10], u16_scaled(egt2, 16.0f));
    put_u16(&d[12], u16_scaled(afr1, 1024.0f));
    put_u16(&d[14], u16_scaled(afr2, 1024.0f));
    put_u16(&d[16], (uint16_t)(adc1 * 16u));
    put_u16(&d[18], (uint16_t)(adc2 * 16u));
    serial_write(d, sizeof d);
}

void protocol_on_frame(const capture_frame_t *fr)
{
    if (!streaming)
        return;

    cycle++;                      /* erstes Telegramm = 1, wie beim Mega */
    bool mode_d = proto == PROTO_ASCII && diag_mode_d();
    if (diag_output_busy())
        diag_note_displaced();    /* Flanken-Ausgabe laeuft: Telegramm aussetzen */
    else if (mode_d)
        diag_send_frame(fr, cycle);
    else if (proto == PROTO_ASCII)
        send_ascii(fr);
    else
        send_legacy(fr);

    /* Autostopp nur im Normalmodus (Ersatz fuer den Reset des Mega, LabVIEW) */
    uint32_t autostop_s = (uint32_t)SET(S_AUTOSTOP_S);
    if (autostop_s && !mode_d) {
        bool active = fr->ign_hz > 0.0f || fr->roll_hz > 0.0f;
        if (active) {
            activity_seen = true;
            idle_frames = 0;
        } else if (activity_seen) {
            float limit = (float)autostop_s * fr->rate_hz;
            if (++idle_frames >= (uint32_t)limit)
                stop();
        }
    }
}
