/*
 * Einstellungen im Flash (Sektor 7, 128 KB ab 0x08060000, siehe config.h).
 *
 * Datensaetze werden hintereinander in den geloeschten Sektor geschrieben
 * (je ca. 0,5 ms). Geloescht wird nur, wenn der Sektor voll ist (alle 512
 * Speicherungen) oder fremde Daten enthaelt: das dauert beim F446 typ. 1 s,
 * max. 2 s, die CPU steht in der Zeit (Watchdog wird dafuer verlaengert).
 * Deshalb wird waehrend einer laufenden Messung/Aufnahme nicht gespeichert.
 */
#ifndef SETTINGS_STORE_H
#define SETTINGS_STORE_H

#include <stdbool.h>

#include "settings.h"

/* Beim Start: gespeicherte Einstellungen nach g_set, sonst Standardwerte.
 * true = gespeicherte Einstellungen gefunden. */
bool settings_store_load(void);

/* Speichern. NULL = ok, sonst Grund fuer #ERR;SAVE;<grund>. */
const char *settings_store_save(const settings_t *s, bool busy);

#endif /* SETTINGS_STORE_H */
