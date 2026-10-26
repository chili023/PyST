#include "settings.h"

#include <string.h>

#include "config.h"

settings_t g_set;

typedef enum { T_INT, T_EDGE, T_X100 } stype_t;

typedef struct {
    const char *key;
    int32_t def, min, max;
    stype_t type;
} sdesc_t;

#define X100(f) ((int32_t)((f) * 100.0f + ((f) >= 0 ? 0.5f : -0.5f)))

/* Standardwerte = bisherige Kompilier-Schalter aus config.h */
static const sdesc_t desc[S_COUNT] = {
    [S_EDGE_IGN]           = {"edge_ign", IGN_EDGE_RISING, 0, 1, T_EDGE},
    [S_EDGE_ROLL]          = {"edge_roll", ROLL_EDGE_RISING, 0, 1, T_EDGE},
    [S_PULLUP]             = {"pullup", INPUT_PULLUP, 0, 1, T_INT},
    [S_HW_FILTER]          = {"hw_filter", INPUT_HW_FILTER, 0, 15, T_INT},
    [S_IGN_MIN_PERIOD_US]  = {"ign_min_period_us", IGN_MIN_PERIOD_US, 0, 100000, T_INT},
    [S_ROLL_MIN_PERIOD_US] = {"roll_min_period_us", ROLL_MIN_PERIOD_US, 0, 100000, T_INT},
    [S_IGN_LOCKOUT_PCT]    = {"ign_lockout_pct", IGN_REL_LOCKOUT_PCT, 0, 95, T_INT},
    [S_ROLL_LOCKOUT_PCT]   = {"roll_lockout_pct", ROLL_REL_LOCKOUT_PCT, 0, 95, T_INT},
    [S_IGN_WIN_LO_PCT]     = {"ign_win_lo_pct", IGN_WIN_LO_PCT, 0, 95, T_INT},
    [S_IGN_WIN_HI_PCT]     = {"ign_win_hi_pct", IGN_WIN_HI_PCT, 100, 1000, T_INT},
    [S_IGN_MIN_PERIODS]    = {"ign_min_periods", IGN_MIN_PERIODS, 1, 64, T_INT},
    [S_ROLL_MIN_PERIODS]   = {"roll_min_periods", ROLL_MIN_PERIODS, 1, 64, T_INT},
    [S_MAX_AVG_SPAN_MS]    = {"max_avg_span_ms", MAX_AVG_SPAN_MS, 10, 2000, T_INT},
    [S_TIMEOUT_IGN_MS]     = {"timeout_ign_ms", IGN_TIMEOUT_MS, 10, 10000, T_INT},
    [S_TIMEOUT_ROLL_MS]    = {"timeout_roll_ms", ROLL_TIMEOUT_MS, 10, 10000, T_INT},
    [S_RATE_HZ]            = {"rate_hz", FRAME_RATE_HZ, RATE_MIN_HZ, RATE_MAX_HZ, T_INT},
    [S_AUTOSTOP_S]         = {"autostop_s", AUTOSTOP_S, 0, 255, T_INT},
    [S_FREQ_DEC]           = {"freq_dec", ASCII_FREQ_DECIMALS, 0, 3, T_INT},
    [S_EGT1]               = {"egt1", EGT1_ENABLE, 0, 1, T_INT},
    [S_EGT2]               = {"egt2", EGT2_ENABLE, 0, 1, T_INT},
    [S_AFR_SRC]            = {"afr_src", AFR_SOURCE, 0, 2, T_INT},
    [S_AFR_0V]             = {"afr_0v", X100(AFR_AT_0V), 0, 10000, T_X100},
    [S_AFR_FS]             = {"afr_fs", X100(AFR_AT_FULLSCALE), 0, 10000, T_X100},
};

void settings_defaults(settings_t *s)
{
    for (int i = 0; i < S_COUNT; i++)
        s->v[i] = desc[i].def;
}

int settings_find(const char *key)
{
    for (int i = 0; i < S_COUNT; i++)
        if (strcmp(key, desc[i].key) == 0)
            return i;
    return -1;
}

const char *settings_key(int id)
{
    return (id >= 0 && id < S_COUNT) ? desc[id].key : "?";
}

static bool in_range(int id, int32_t v) { return v >= desc[id].min && v <= desc[id].max; }

/* Dezimalzahl ohne Vorzeichen, bis zu 'frac' Nachkommastellen -> x 10^frac */
static bool parse_num(const char *t, unsigned frac, int32_t *out)
{
    int64_t v = 0;
    unsigned digits = 0, fdigits = 0;
    bool point = false;
    for (; *t; t++) {
        if (*t >= '0' && *t <= '9') {
            if (point) {
                if (fdigits == frac)
                    continue;          /* weitere Nachkommastellen abschneiden */
                fdigits++;
            }
            v = v * 10 + (*t - '0');
            if (v > 100000000)
                return false;
            digits++;
        } else if (*t == '.' && !point && frac) {
            point = true;
        } else {
            return false;
        }
    }
    if (!digits)
        return false;
    for (; fdigits < frac; fdigits++)
        v *= 10;
    *out = (int32_t)v;
    return true;
}

bool settings_parse_value(settings_t *s, int id, const char *text)
{
    int32_t v;
    if (id < 0 || id >= S_COUNT)
        return false;
    switch (desc[id].type) {
    case T_EDGE:
        if ((text[0] == 'R' || text[0] == 'r' || text[0] == '1') && !text[1])
            v = 1;
        else if ((text[0] == 'F' || text[0] == 'f' || text[0] == '0') && !text[1])
            v = 0;
        else
            return false;
        break;
    case T_X100:
        if (!parse_num(text, 2, &v))
            return false;
        break;
    default:
        if (!parse_num(text, 0, &v))
            return false;
        break;
    }
    if (!in_range(id, v))
        return false;
    s->v[id] = v;
    return true;
}

static size_t put_u32(char *buf, size_t pos, size_t len, uint32_t v)
{
    char tmp[11];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    while (n && pos + 1 < len)
        buf[pos++] = tmp[--n];
    return pos;
}

void settings_format_value(const settings_t *s, int id, char *buf, size_t len)
{
    size_t p = 0;
    if (!len)
        return;
    if (id < 0 || id >= S_COUNT) {
        buf[0] = '\0';
        return;
    }
    int32_t v = s->v[id];
    switch (desc[id].type) {
    case T_EDGE:
        if (len > 1)
            buf[p++] = v ? 'R' : 'F';
        break;
    case T_X100:
        p = put_u32(buf, p, len, (uint32_t)v / 100u);
        if (p + 3 < len) {
            buf[p++] = '.';
            buf[p++] = (char)('0' + ((uint32_t)v / 10u) % 10u);
            buf[p++] = (char)('0' + (uint32_t)v % 10u);
        }
        break;
    default:
        p = put_u32(buf, p, len, (uint32_t)v);
        break;
    }
    buf[p] = '\0';
}

/* ------------------------------------------------------------ Ablage */

uint32_t settings_crc32(const void *data, size_t len)
{
    const uint8_t *p = data;
    uint32_t crc = 0xFFFFFFFFu;
    while (len--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static void wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* Aufbau: Kennung(4) Version(2) Anzahl(2) Werte(4*Anzahl) CRC32(4) */
#define HDR 8u

void settings_serialize(const settings_t *s, uint8_t rec[SETTINGS_SLOT])
{
    memset(rec, 0xFF, SETTINGS_SLOT);
    wr32(rec, SETTINGS_MAGIC);
    rec[4] = (uint8_t)SETTINGS_VERSION;
    rec[5] = (uint8_t)(SETTINGS_VERSION >> 8);
    rec[6] = (uint8_t)S_COUNT;
    rec[7] = 0;
    for (unsigned i = 0; i < S_COUNT; i++)
        wr32(&rec[HDR + 4u * i], (uint32_t)s->v[i]);
    size_t n = HDR + 4u * S_COUNT;
    wr32(&rec[n], settings_crc32(rec, n));
}

bool settings_deserialize(const uint8_t rec[SETTINGS_SLOT], settings_t *s)
{
    if (rd32(rec) != SETTINGS_MAGIC)
        return false;
    unsigned ver = rec[4] | (unsigned)rec[5] << 8;
    unsigned cnt = rec[6] | (unsigned)rec[7] << 8;
    if (ver != SETTINGS_VERSION || cnt == 0 || HDR + 4u * cnt + 4u > SETTINGS_SLOT)
        return false;
    size_t n = HDR + 4u * cnt;
    if (rd32(&rec[n]) != settings_crc32(rec, n))
        return false;

    settings_defaults(s);
    for (unsigned i = 0; i < cnt && i < S_COUNT; i++) {
        int32_t v = (int32_t)rd32(&rec[HDR + 4u * i]);
        if (in_range((int)i, v))
            s->v[i] = v;
    }
    return true;
}

static bool erased(const uint8_t *p, size_t n)
{
    while (n--)
        if (*p++ != 0xFFu)
            return false;
    return true;
}

bool settings_scan(const uint8_t *region, size_t size, settings_t *s, int *next_free)
{
    size_t slots = size / SETTINGS_SLOT;
    bool found = false;
    int last_used = -1;
    settings_t tmp;

    settings_defaults(s);
    for (size_t i = 0; i < slots; i++) {
        const uint8_t *rec = region + i * SETTINGS_SLOT;
        if (erased(rec, SETTINGS_SLOT))
            continue;
        last_used = (int)i;
        if (settings_deserialize(rec, &tmp)) {
            *s = tmp;
            found = true;
        }
    }
    /* Nur hinter dem letzten belegten Platz schreiben (Reihenfolge = Alter) */
    *next_free = (size_t)(last_used + 1) < slots ? last_used + 1 : -1;
    return found;
}
