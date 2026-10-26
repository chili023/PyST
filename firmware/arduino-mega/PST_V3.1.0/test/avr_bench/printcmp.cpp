// Vergleich pst_out_float / pst_fmt_normal gegen das echte Arduino Print (AVR, simavr).
// Bestaetigt, dass das normale Telegramm byte-identisch zu Serial.print() in 3.0.0 ist.
#include <Arduino.h>
#include <string.h>
#include "../../pst_core.h"

class BufPrint : public Print {
public:
  char b[160]; size_t n = 0;
  size_t write(uint8_t c) override { if (n < sizeof(b) - 1) b[n++] = c; b[n] = 0; return 1; }
  void clear() { n = 0; b[0] = 0; }
};

static void uputc(char c) { while (!(UCSR0A & _BV(UDRE0))) {} UDR0 = c; }
static void uputs(const char *s) { while (*s) uputc(*s++); }
static void uputu(uint32_t v) { char t[11]; uint8_t i = 0; do { t[i++] = '0' + v % 10; v /= 10; } while (v); while (i) uputc(t[--i]); }

extern "C" void __cxa_pure_virtual(void) { for (;;) {} }

int main(void)
{
  UBRR0 = 8; UCSR0B = _BV(TXEN0); UCSR0A = _BV(TXC0);
  BufPrint p;
  char q[160];
  uint32_t tests = 0, bad = 0;
  // Gleitkommazahlen: Pseudo-Zufall ueber viele Groessenordnungen
  uint32_t x = 12345;
  for (uint16_t i = 0; i < 3000; i++) {
    x = x * 1103515245UL + 12345UL;
    float v = (float)(x >> 8) / (float)(1UL << (x % 24));
    if (x & 1) v = -v;
    for (uint8_t d = 0; d <= 3; d++) {
      p.clear(); p.print((double)v, d);
      PstOut o = { q, 0, sizeof(q) }; pst_out_float(&o, v, d);
      tests++;
      if (strcmp(p.b, q) != 0) { bad++; if (bad < 5) { uputs(p.b); uputc(' '); uputs(q); uputc('\n'); } }
    }
  }
  // Normales Telegramm genau wie 3.0.0 (Serial.print-Folge) gegen pst_fmt_normal
  for (uint16_t i = 0; i < 600; i++) {
    x = x * 1103515245UL + 12345UL;
    uint16_t cycle = (uint16_t)x;
    uint8_t tc = (uint8_t)(x >> 8);
    double timer = tc + 256UL; timer = 16000000L / timer; timer = timer / 1024;
    uint32_t f4 = (x >> 12) & 0x3FF, f5 = (x >> 3) & 0x7FFF;
    bool thermo = x & 0x40000;
    double c = (x & 0x80000) ? NAN : (double)((x >> 5) & 0x7FF) / 4.0;
    unsigned int lt = 740 + (x & 0x3F);
    double Afr = ((float)(376 + ((x >> 7) & 0xFFF)) / 1000) * 1470;
    if (x & 0x100000) Afr = 2000;
    p.clear();
    p.print(cycle, DEC); p.print(";"); p.print(timer, 2); p.print(";"); p.print(f4, DEC); p.print(";");
    p.print(f5, DEC); p.print(";");
    if (thermo) { p.print(c, 0); p.print(";"); } else { p.print(lt); p.print(";"); }
    p.print((Afr) / 100);
    if (thermo) { p.print(";"); p.print(lt); }
    p.print('\n');
    PstSensors s; s.thermo = thermo; s.egt_c = c; s.lambda_temp = lt; s.afr100 = Afr;
    size_t n = pst_fmt_normal(q, sizeof(q), cycle, timer, f4, f5, &s);
    tests++;
    if (n != p.n || memcmp(q, p.b, n) != 0) { bad++; if (bad < 5) { uputs(p.b); uputs(q); } }
  }
  uputs("Vergleich mit Arduino Print: "); uputu(tests); uputs(" Faelle, "); uputu(bad); uputs(" Abweichungen\n");
  // simavr beendet sich bei sleep mit gesperrten Interrupts
  while (!(UCSR0A & _BV(TXC0))) {}
  cli(); SMCR = _BV(SE); __asm__ volatile("sleep");
  for (;;) {}
}
