# Schritt 3 – Binärprotokoll v2

Ersetzt für SimpleDyno das ASCII-Mega-Protokoll. Die Firmware bleibt LabVIEW-kompatibel: nach dem
Start spricht sie das Mega-Protokoll und schaltet erst auf v2 um, wenn sie ein gültiges v2-Paket
(`HELLO`) empfängt.

## Anforderungen

- Übertragung über beliebige Byte-Ströme: ST-LINK-COM-Port (bis 921600 Baud), USB direkt am Chip
  (Schritt 4), später auch TCP/WLAN.
- Wiedereinrasten nach Bytefehlern, Prüfsumme, Paketzähler (Verluste erkennbar).
- **Selbstbeschreibend:** Das Gerät meldet seine Kanäle und Parameter; die PC-Software legt sie an.
- Eine Zeitbasis für alle Werte (Mikrocontroller-Zeitstempel).
- Befehle mit Bestätigung; Heartbeat für den Regelbetrieb (WSB).

## Rahmen

```
Paket (vor COBS):  [Typ u8][Seq u8][Nutzdaten ...][CRC-16/CCITT-FALSE u16 LE]
Übertragung:       COBS-kodiert, abgeschlossen mit 0x00
```

- COBS: 0x00 kommt im Paket nie vor → nach jedem Fehler beim nächsten 0x00 wieder synchron.
- Maximale Paketlänge 512 Byte (Nutzdaten), größere Daten (Oszilloskop) in Blöcken.
- Alle Zahlen little endian.

## Pakettypen

| Typ | Richtung | Inhalt |
|---|---|---|
| 0x01 `HELLO` | PC → Gerät | Magic „PSTv2“, gewünschte Protokollversion |
| 0x02 `DEVICE_INFO` | Gerät → PC | Protokollversion, Firmware-Version, Geräte-ID, Taktquelle, Zeitstempel-Einheit (µs) |
| 0x03 `CHANNEL_DESC` | Gerät → PC | je Kanal: ID u8, Datentyp (u16/i16/i32/f32), Skala f32, Offset f32, Gruppe, Einheit, Name |
| 0x04 `PARAM_DESC` | Gerät → PC | je Parameter: ID u16, Typ, Min/Max/Standard, Einheit, Name, Gruppe |
| 0x10 `FRAME` | Gerät → PC | Zeitstempel u32 µs, Zähler u32, dann alle Rahmen-Kanäle in Beschreibungsreihenfolge |
| 0x11 `EDGES` | Gerät → PC | Kanal, Anzahl, Basis-Zeitstempel u32, Deltas u32[] (Flankenmodus/Diagnose) |
| 0x12 `SCOPE` | Gerät → PC | Aufnahme-ID, Blocknummer, Abtastrate, Samples u16[] |
| 0x13 `EVENT` | Gerät → PC | Zeitstempel, Code, Text (Warnungen: „EGT1 offen“, „Autostopp“, „Not-Aus“) |
| 0x20 `CMD` | PC → Gerät | Befehl u8 + Argumente (s.u.) |
| 0x21 `ACK` | Gerät → PC | Seq des Befehls, Ergebnis (OK/Fehlercode) |
| 0x22 `PARAM` | beide | Parameter-ID + Wert (lesen/setzen/melden) |
| 0x30 `HEARTBEAT` | PC → Gerät | alle 100 ms im Regelbetrieb; Ausbleiben > 500 ms → sicherer Zustand |

### Befehle (`CMD`)

| Code | Befehl | Argumente |
|---|---|---|
| 0x01 | `START` | Messrate [Hz] u16 |
| 0x02 | `STOP` | – |
| 0x03 | `GET_CLIMATE` | – (Antwort als `FRAME`-Kanäle oder `EVENT`) |
| 0x04 | `EDGES_ON/OFF` | Kanal-Maske, Dauer [ms] |
| 0x05 | `SCOPE_TRIGGER` | Kanal, Rate, Länge, Triggerflanke |
| 0x06 | `SAVE_PARAMS` | – (Flash) |
| 0x07 | `SET_MODE` | Modus: Trägheit / WSB konst. Drehzahl / WSB konst. Geschwindigkeit / WSB Rampe / WSB Last |
| 0x08 | `SETPOINT` | Sollwert f32 (Drehzahl, km/h, Strom, Last je nach Modus) |
| 0x09 | `REBOOT` | – |

## Kanäle im `FRAME` (Beispiel heutige Platine)

| ID | Name | Typ | Skala | Einheit |
|---|---|---|---|---|
| 1 | Rolle Frequenz | u32 | 0,001 | Hz |
| 2 | Zündung Frequenz | u32 | 0,001 | Hz |
| 3 | Rolle Impulse (Telegramm) | u16 | 1 | – |
| 4 | Zündung verworfen (Telegramm) | u16 | 1 | – |
| 5 | EGT 1 | i16 | 0,25 | °C |
| 6 | EGT 2 | i16 | 0,25 | °C |
| 7 | AFR analog | u16 | 0,001 | AFR |
| 8 | ADC 1 / 2 | u16 | 1 | roh |
| 9 | Lufttemperatur / Druck / Feuchte | i16/u32/u16 | 0,01 / 1 / 0,01 | °C / Pa / % |

Neue Sensoren = neue Kanäle in der Beschreibung. Kanäle mit anderer Rate (z.B. Klima 1 Hz) können in
eigenen `FRAME`-Gruppen laufen (Gruppen-ID im Kopf – bei Bedarf ergänzen).

## Zeitbasis

- Zeitstempel u32 in µs (Überlauf nach 71 min, PC entfaltet), aus TIM2 abgeleitet.
- Rahmen-Zeitstempel = Ende des Messintervalls; Frequenzen gelten für das Intervall davor.
- rusEFI-/CAN-Werte bekommen den Empfangszeitstempel des Mikrocontrollers.

## Datenrate

| Strom | Rate |
|---|---|
| FRAME, 30 Kanäle × 4 Byte, 100 Hz | ~13 kB/s |
| EDGES (Rolle 2700/s + Zündung 200/s, 4 Byte) | ~12 kB/s |
| WSB-Telemetrie 1 kHz × 16 Byte | ~16 kB/s |
| CAN-Weiterleitung rusEFI (~600 Nachrichten/s) | ~8 kB/s |
| **Summe** | **~50 kB/s** |

ST-LINK bei 115200 Baud: 11 kB/s → reicht für v2-FRAME mit ~60 Hz, nicht für mehr.
ST-LINK bei 921600 Baud: ~90 kB/s → reicht für alles außer großen Scope-Aufnahmen.
USB direkt (Full Speed, CDC): ~800 kB/s → Reserve für alles.

## Umsetzung

1. Firmware: `src/proto_v2.c` (COBS, CRC, Pakete), Kanaltabelle, Umschaltung nach `HELLO`.
2. SimpleDyno: `link_v2.py`, Kanäle aus `CHANNEL_DESC` → `channels.py` (Rohdaten-Spalten automatisch).
3. Host-Test: Firmware-Protokoll gegen Python-Decoder (wie `test/check_protocol.py`).
4. Baudrate auf 921600 anheben (ST-LINK), danach USB direkt (Schritt 4).
