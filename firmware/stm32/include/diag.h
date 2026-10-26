/*
 * PST-Diag 1 (Oszi-/Diagnosemodus), Spezifikation: docs/04_PST_Diag_1.md
 *
 * #-Zeilenbefehle (#?, #MODE, #RUN, #RATE, #GET, #SET, #SAVE, #DEFAULTS,
 * #BURST), Diagnose-Telegramm "D;..." und Ausgabe der Flanken-Aufnahmen.
 * Nach jedem Start gilt Modus N (normal, byte-identisch zu 1.0.0).
 */
#ifndef DIAG_H
#define DIAG_H

#include <stdbool.h>
#include <stdint.h>

#include "capture.h"

void diag_init(void);
/* Eine empfangene Zeile ohne fuehrendes '#' und ohne Zeilenende. */
void diag_line(const char *line);
/* Zeile war laenger als 63 Zeichen */
void diag_line_too_long(void);
bool diag_mode_d(void);
void diag_send_frame(const capture_frame_t *fr, uint16_t cycle);
/* true, solange eine Flanken-Aufnahme ausgegeben wird (Telegramme aussetzen) */
bool diag_output_busy(void);
void diag_note_displaced(void);
/* in der Hauptschleife: Ausgabe der Flanken-Aufnahme fortsetzen */
void diag_poll(void);

#endif /* DIAG_H */
