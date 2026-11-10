/*
 * Analogeingaenge ADC_CH1 (PB0) und ADC_CH2 (PA1), z.B. fuer den
 * 0..3,3-V-Ausgang eines externen Breitband-Lambda-Controllers.
 * Ab 1.2.0 tastet ADC1 beide Eingaenge ununterbrochen per DMA ab (ohne
 * Interrupt); analog_read() wartet nicht mehr. In PST-Diag 1 heissen die
 * Eingaenge ADC1 (PB0) und ADC2 (PA1), siehe "#AI" in diag.c.
 */
#ifndef ANALOG_H
#define ANALOG_H

#include <stdint.h>

void analog_init(void);

/* Gemittelter Rohwert 0..4095 (letzte 16 Wandlungen, ca. 0,7 ms). ch: 1 = PB0, 2 = PA1. */
uint16_t analog_read(unsigned ch);

/* Rohwert -> AFR nach den Einstellungen afr_0v / afr_fs (Standard: config.h) */
float analog_to_afr(uint16_t raw);

#endif /* ANALOG_H */
