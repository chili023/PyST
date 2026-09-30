/*
 * Analogeingaenge ADC_CH1 (PB0) und ADC_CH2 (PA1), z.B. fuer den
 * 0..3,3-V-Ausgang eines externen Breitband-Lambda-Controllers.
 */
#ifndef ANALOG_H
#define ANALOG_H

#include <stdint.h>

void analog_init(void);

/* Gemittelter Rohwert 0..4095. ch: 1 = PB0, 2 = PA1. */
uint16_t analog_read(unsigned ch);

/* Rohwert -> AFR nach AFR_AT_0V / AFR_AT_FULLSCALE aus config.h */
float analog_to_afr(uint16_t raw);

#endif /* ANALOG_H */
