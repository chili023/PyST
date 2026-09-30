#include "fmt.h"

void fmt_reset(fmt_t *f) { f->len = 0; f->buf[0] = '\0'; }

void fmt_char(fmt_t *f, char c)
{
    if (f->len + 1 < sizeof f->buf) {
        f->buf[f->len++] = c;
        f->buf[f->len] = '\0';
    }
}

void fmt_str(fmt_t *f, const char *s)
{
    while (*s)
        fmt_char(f, *s++);
}

static void fmt_u64(fmt_t *f, uint64_t v)
{
    char tmp[21];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    while (n)
        fmt_char(f, tmp[--n]);
}

void fmt_u32(fmt_t *f, uint32_t v) { fmt_u64(f, v); }

void fmt_hex8(fmt_t *f, uint8_t v)
{
    static const char hex[] = "0123456789ABCDEF";
    fmt_char(f, hex[v >> 4]);
    fmt_char(f, hex[v & 15u]);
}

void fmt_fixed(fmt_t *f, float v, unsigned decimals)
{
    if (v != v) {                              /* NaN */
        fmt_str(f, "nan");
        return;
    }
    if (v < 0.0f) {
        fmt_char(f, '-');
        v = -v;
    }
    uint64_t scale = 1;
    for (unsigned i = 0; i < decimals; i++)
        scale *= 10u;
    if (v > 1e12f)
        v = 1e12f;
    uint64_t x = (uint64_t)((double)v * (double)scale + 0.5);
    fmt_u64(f, x / scale);
    if (decimals) {
        fmt_char(f, '.');
        uint64_t frac = x % scale;
        for (uint64_t d = scale / 10u; d; d /= 10u) {
            fmt_char(f, (char)('0' + frac / d));
            frac %= d;
        }
    }
}
