/*
 * Minimaler Text-Puffer fuer Telegramme (ohne printf, keine Heap-Nutzung).
 */
#ifndef FMT_H
#define FMT_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    char buf[160];
    size_t len;
} fmt_t;

void fmt_reset(fmt_t *f);
void fmt_str(fmt_t *f, const char *s);
void fmt_char(fmt_t *f, char c);
void fmt_u32(fmt_t *f, uint32_t v);
void fmt_hex8(fmt_t *f, uint8_t v);
/* Festkomma mit Rundung, Dezimalpunkt '.', wie Arduino Serial.print(x, n) */
void fmt_fixed(fmt_t *f, float v, unsigned decimals);

#endif /* FMT_H */
