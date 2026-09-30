/*
 * USART2 (PA2/PA3) = virtueller COM-Port des ST-LINK auf dem Nucleo.
 * Senden und Empfangen laufen ueber Ringpuffer im Interrupt – nichts blockiert.
 */
#ifndef SERIAL_H
#define SERIAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void serial_init(uint32_t baud);

/* Schreibt die Nachricht ganz oder gar nicht (false = Puffer voll, verworfen). */
bool serial_write(const void *data, size_t len);
bool serial_write_str(const char *s);

/* Naechstes empfangenes Byte, -1 wenn keines da. */
int serial_read(void);

/* Millisekunden-Tick des zuletzt empfangenen Bytes. */
uint32_t serial_last_rx_tick(void);

/* Liefert true (einmalig), wenn seit dem letzten Aufruf ein Break empfangen wurde. */
bool serial_take_break(void);

uint32_t serial_tx_dropped(void);

#endif /* SERIAL_H */
