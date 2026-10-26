// pst_isr.h - Capture- und Ueberlauf-ISRs von Timer4 (Zuendung) und Timer5 (Rolle)
// Nur AVR. Wird genau einmal eingebunden: vom Sketch und vom Taktzahl-Test (test/avr_bench).
// Erwartet die Globalen g_ch[2], g_burst, g_ovf4, g_ovf5.
#ifndef PST_ISR_H
#define PST_ISR_H
#include <avr/io.h>
#include <avr/interrupt.h>
#include "pst_core.h"

extern PstChan  g_ch[2];
extern PstBurst g_burst;
extern volatile uint16_t g_ovf4;
extern volatile uint16_t g_ovf5;

ISR(TIMER4_OVF_vect) {
  g_ovf4++;
}

ISR(TIMER5_OVF_vect) {
  g_ovf5++;
}

// Zuendung: ICP4 = PL0 = Pin 49
ISR(TIMER4_CAPT_vect) {
  uint16_t icr = ICR4;
  uint32_t ts = pst_ts_from_capture(icr, g_ovf4, (TIFR4 & _BV(TOV4)) ? 1 : 0);
  uint8_t b = TCCR4B;
  uint8_t lvl = 0;
  if (b & _BV(ICES4)) lvl = 1;                     // Pegel nach der Flanke
  uint8_t missed = 0;
  if (g_ch[PST_CH_I].toggle) {                     // beide Flanken: Richtung umschalten
    TCCR4B = b ^ _BV(ICES4);
    TIFR4 = _BV(ICF4);
    uint8_t pin = 0;
    if (PINL & _BV(PL0)) pin = 1;
    missed = (pin != lvl);                         // Gegenflanke schon vorbei?
  }
  pst_edge(&g_ch[PST_CH_I], &g_burst, PST_CH_I, ts, lvl, missed);
}

// Rolle: ICP5 = PL1 = Pin 48
ISR(TIMER5_CAPT_vect) {
  uint16_t icr = ICR5;
  uint32_t ts = pst_ts_from_capture(icr, g_ovf5, (TIFR5 & _BV(TOV5)) ? 1 : 0);
  uint8_t b = TCCR5B;
  uint8_t lvl = 0;
  if (b & _BV(ICES5)) lvl = 1;
  uint8_t missed = 0;
  if (g_ch[PST_CH_R].toggle) {
    TCCR5B = b ^ _BV(ICES5);
    TIFR5 = _BV(ICF5);
    uint8_t pin = 0;
    if (PINL & _BV(PL1)) pin = 1;
    missed = (pin != lvl);
  }
  pst_edge(&g_ch[PST_CH_R], &g_burst, PST_CH_R, ts, lvl, missed);
}

#endif
