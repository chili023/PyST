/*
 * PST-Diag 1 (Oszi-/Diagnosemodus), Spezifikation: docs/04_PST_Diag_1.md
 *
 * #-Zeilenbefehle (#?, #MODE, #RUN, #RATE, #GET, #SET, #SAVE, #DEFAULTS,
 * #BURST, ab 1.2.0 #AI), Diagnose-Telegramm "D;...", Ausgabe der Flanken-
 * Aufnahmen und Zeilen "A;..." mit den Analogeingaengen.
 * Nach jedem Start gilt Modus N (normal, byte-identisch zu 1.0.0), #AI aus.
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
/* in der Hauptschleife: Ausgabe der Flanken-Aufnahme fortsetzen, A-Zeilen */
void diag_poll(void);
/* A-Zeilen aus (STM-LabVIEW erkannt, Break) bzw. aktueller Takt (0 = aus) */
void diag_ai_off(void);
uint8_t diag_ai_hz(void);

#endif /* DIAG_H */
