// Taktzahl-Messung der Capture-ISR (laeuft im Simulator simavr, siehe run_bench.sh)
// Ruft die echte ISR aus pst_isr.h direkt auf und misst mit Timer1 (ohne Vorteiler).
// Ergebnis ueber UART0. Gemessen wird call..reti; Hardware-Einsprung (4..5 Takte + jmp 3)
// ist etwa gleich teuer wie call (5) -> Wert entspricht der ISR-Dauer +-3 Takte.
#include <avr/io.h>
#include <avr/interrupt.h>
#include <string.h>
#include "../../pst_core.h"

PstChan  g_ch[2];
PstBurst g_burst;
volatile uint16_t g_ovf4 = 0;
volatile uint16_t g_ovf5 = 0;
#include "../../pst_isr.h"

extern "C" void __vector_41(void);   // TIMER4_CAPT

static void uputc(char c) { while (!(UCSR0A & _BV(UDRE0))) {} UDR0 = c; }
static void uputs(const char *s) { while (*s) uputc(*s++); }
static void uputu(uint32_t v) { char t[11]; uint8_t i = 0; do { t[i++] = '0' + v % 10; v /= 10; } while (v); while (i) uputc(t[--i]); }

static uint16_t worst, last;
static uint32_t ts = 100000;

static void one(uint32_t period)
{
  ts += period;
  ICR4 = (uint16_t)ts;
  g_ovf4 = (uint16_t)(ts >> 16);
  uint16_t t0 = TCNT1;
  __vector_41();
  uint16_t t1 = TCNT1;
  cli();
  last = t1 - t0 - 4;          // Overhead der Messung (2x lds TCNT1) abziehen
  if (last > worst) worst = last;
#ifdef TRACE
  uputu(last); uputc(32);
#endif
}

static void run(const char *name, uint16_t count, uint32_t period, uint8_t jitter)
{
  worst = 0;
  for (uint16_t i = 0; i < count; i++) one(period + ((i & 7) == 3 ? jitter * 100UL : 0));
  uputs(name); uputs(": max="); uputu(worst); uputs(" letzte="); uputu(last); uputs(" Takte\n");
}

int main(void)
{
  UBRR0 = 8; UCSR0B = _BV(TXEN0); UCSR0A = _BV(TXC0);
  TCCR1B = _BV(CS10);
  TCCR4B = 0;                         // Timer4 steht (ICR4 wird gesetzt), ICES4 = 0 -> fallend
  pst_chan_init(&g_ch[0]);
  pst_chan_init(&g_ch[1]);
  pst_chan_config(&g_ch[0], 'F', 64, 3000, 0, 0);
  pst_chan_config(&g_ch[1], 'F', 50, 1000, 0, 0);

  run("Messung F, Ring 64, ohne Filter", 200, 20000, 0);
  pst_chan_config(&g_ch[0], 'F', 64, 3000, 10, 25);
  run("Messung F mit Mindestperiode+Doppelimpulsfilter", 200, 20000, 0);

  // Burst beide Flanken mit Trigger (teuerster Pfad: umschalten, aufnehmen, Trigger, Messung)
  pst_burst_arm(&g_burst, g_ch, 3, PST_BURST_MAX, 'B', 20, 700);
  g_ch[0].toggle = pst_chan_needs_toggle(&g_burst, &g_ch[0], 0);
  run("Burst B mit Trigger (vor Ausloesung)", 600, 20000, 0);
  // Ohne Trigger
  pst_burst_arm(&g_burst, g_ch, 1, PST_BURST_MAX, 'B', 0, 0);
  run("Burst B ohne Trigger", 600, 20000, 0);
  // Nach Burst
  g_burst.state = PST_B_IDLE;
  g_ch[0].toggle = 0;
  run("nach Burst (nur Messung)", 100, 20000, 0);
  uputs("ENDE\n");
  cli();
  // simavr beendet sich bei sleep mit gesperrten Interrupts
  while (!(UCSR0A & _BV(TXC0))) {}
  cli(); SMCR = _BV(SE); __asm__ volatile("sleep");
  for (;;) {}
}
