/*
 * Abgastemperatur: 2x MAX6675 an SPI1 (PB3 SCK, PB4 MISO),
 * CS EGT1 = PA13 (SWDIO!), CS EGT2 = PC6. Aufloesung 0,25 degC.
 * Der MAX6675 braucht 220 ms pro Wandlung -> es wird alle 250 ms gelesen.
 */
#ifndef EGT_H
#define EGT_H

#include <stdbool.h>

void egt_init(void);
void egt_poll(void);                /* in der Hauptschleife aufrufen */

/* Letzter Messwert [degC]. false = Kanal aus, Thermoelement offen oder keine Antwort. */
bool egt_get(unsigned ch, float *t_c);

bool egt_enabled(unsigned ch);

#endif /* EGT_H */
