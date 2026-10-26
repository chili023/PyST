/*
 * Laufzeit-Einstellungen (PST-Diag 1, ab Firmware 1.1.0).
 *
 * Ersetzt die Kompilier-Schalter aus config.h zur Laufzeit: die Werte in
 * config.h sind nur noch die Standardwerte. Lesen/Setzen per #GET/#SET,
 * dauerhaft per #SAVE (Flash, letzter Sektor), siehe README.md.
 *
 * Dieses Modul ist hardwareunabhaengig (Host-Test: test/test_diag.c). Das
 * eigentliche Flash-Schreiben steht in settings_store.c.
 */
#ifndef SETTINGS_H
#define SETTINGS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Reihenfolge = Ablage im Flash. Neue Schluessel NUR hinten anfuegen. */
typedef enum {
    S_EDGE_IGN = 0,         /* Flanke Zuendung: 1 = steigend (R), 0 = fallend (F) */
    S_EDGE_ROLL,            /* Flanke Rolle */
    S_PULLUP,               /* interne Pull-ups an PA0/PB9 */
    S_HW_FILTER,            /* Digitalfilter des Timers 0..15 */
    S_IGN_MIN_PERIOD_US,    /* Sperrzeit Zuendung */
    S_ROLL_MIN_PERIOD_US,   /* Sperrzeit Rolle */
    S_IGN_LOCKOUT_PCT,      /* Doppelimpulsfilter Zuendung (IGN_REL_LOCKOUT_PCT) */
    S_ROLL_LOCKOUT_PCT,     /* Doppelimpulsfilter Rolle */
    S_IGN_WIN_LO_PCT,       /* Erwartungsfenster Zuendung untere Grenze, 0 = aus (Plan 2.2) */
    S_IGN_WIN_HI_PCT,       /* Erwartungsfenster obere Grenze ("Luecke") */
    S_IGN_MIN_PERIODS,
    S_ROLL_MIN_PERIODS,
    S_MAX_AVG_SPAN_MS,
    S_TIMEOUT_IGN_MS,
    S_TIMEOUT_ROLL_MS,
    S_RATE_HZ,              /* Telegrammrate Arduino-Protokoll / Modus D */
    S_AUTOSTOP_S,
    S_FREQ_DEC,             /* Nachkommastellen im normalen Telegramm */
    S_EGT1,
    S_EGT2,
    S_AFR_SRC,              /* 0 = aus, 1 = PB0, 2 = PA1 */
    S_AFR_0V,               /* AFR bei 0 V, x100 */
    S_AFR_FS,               /* AFR bei 3,3 V, x100 */
    S_COUNT
} setting_id_t;

typedef struct {
    int32_t v[S_COUNT];
} settings_t;

/* Aktive Einstellungen (vom Interrupt nur gelesen). */
extern settings_t g_set;

#define SET(id) (g_set.v[(id)])

void settings_defaults(settings_t *s);

/* Telegrammrate: zulaessiger Bereich fuer #RATE und rate_hz (115200 Baud) */
#define RATE_MIN_HZ 10
#define RATE_MAX_HZ 100

/* Schluessel/Wert als Text. Liefert die Kennung oder -1. */
int settings_find(const char *key);
const char *settings_key(int id);
/* Wert parsen und pruefen; false = ungueltig (s bleibt unveraendert). */
bool settings_parse_value(settings_t *s, int id, const char *text);
/* Wert in kanonischer Form ("R", "25", "10.00", ...) nach buf (min. 16 Byte). */
void settings_format_value(const settings_t *s, int id, char *buf, size_t len);

/* ---------------------------------------------- Ablage (Flash-Protokoll) */
/* Die Einstellungen werden als Datensaetze fester Groesse hintereinander in
 * einen geloeschten Flash-Bereich geschrieben (Protokoll statt Ueberschreiben):
 * Loeschen ist nur noetig, wenn der Bereich voll ist. Gueltig ist der letzte
 * Datensatz mit Kennung, Version und CRC32. */
#define SETTINGS_MAGIC    0x53545350u   /* "PSTS" */
#define SETTINGS_VERSION  1u
#define SETTINGS_SLOT     256u          /* Bytes je Datensatz */

uint32_t settings_crc32(const void *data, size_t len);

/* Datensatz erzeugen (SETTINGS_SLOT Bytes, Rest 0xFF). */
void settings_serialize(const settings_t *s, uint8_t rec[SETTINGS_SLOT]);
/* Datensatz pruefen und uebernehmen. Fehlende (neuere) Schluessel behalten den
 * Standardwert, Werte ausserhalb des Bereichs ebenfalls. */
bool settings_deserialize(const uint8_t rec[SETTINGS_SLOT], settings_t *s);

/* Bereich durchsuchen: letzter gueltiger Datensatz nach *s (sonst Standardwerte),
 * *next_free = Index des naechsten freien Platzes oder -1 (voll -> loeschen).
 * Rueckgabe: true = gespeicherte Einstellungen gefunden. */
bool settings_scan(const uint8_t *region, size_t size, settings_t *s, int *next_free);

#endif /* SETTINGS_H */
