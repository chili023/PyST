# PyST 1.4.4 – Release Notes

Stand 16.11.2026. Enthält alles aus 1.4.3. Keine neue Firmware nötig (Mega 3.2.0, PST-STM32 1.2.0, Basis 0.4.0
bleiben).

## Behoben: PS und Nm fast doppelt so hoch

Fall beim Kunden: Der Klimasensor (BME280) antwortete nicht, weil er auf Adresse 0x77 sitzt und das Messboard auf
0x76 fragte. In den Einstellungen standen noch 0 °C / 500 mbar aus der alten Sensor-Antwort „0;0“ (1.4.2). Damit
war der Korrekturfaktor k = 1,96, Leistung und Drehmoment also fast doppelt so hoch. Drehzahl und km/h stimmten.

**Plausibilisierer**
- Korrigiert wird nur mit plausiblem Klima: −30 … 55 °C und 700 … 1100 mbar.
- Kommt kein Sensorwert und das eingetragene Klima ist plausibel, rechnet PyST damit wie bisher.
- Ist das Klima unplausibel, rechnet PyST **ohne Korrektur (k = 1)**.

**Warnung an den Kunden**
- Antwortet der Klimasensor nicht, steht eine Warnung beim Lauf: in der Ergebniszeile, in der Statuszeile und im
  gelben Zustandsfeld neben START statt dem grünen. Die Warnung nennt `bme_addr` (0x76/0x77).
- Bei unplausiblem Klima heißt es zusätzlich „Leistung NICHT korrigiert (k = 1)“.
- Die Warnung steht auch in `lauf.json` (`klima_warnung`).
- Unter Einstellungen › Klima steht in Rot, wenn das eingetragene Klima unplausibel ist.
- PDF-Bericht und HTML-Export schreiben „nicht korrigiert (k = 1)“ statt eines falschen k.

**Alte Läufe**
- Läufe mit unplausiblem Klima rechnet PyST beim Öffnen und beim Import ohne Korrektur neu.
- Die Höchstwerte in der Laufliste werden beim ersten Laden korrigiert.
- Die eingetragenen Werte bleiben in der Lauf-Datei erhalten.

**Abhilfe beim Kunden**
1. Unter Verbindung › Geräte-Einstellungen `bme_addr` auf die Adresse des Sensors setzen (häufig 0x77, wenn SDO
   offen oder auf 3,3 V liegt) und „Im Gerät speichern“ drücken.
2. Unter Einstellungen › Klima Temperatur und Luftdruck richtig eintragen. Sie gelten, wenn der Sensor einmal nicht
   antwortet.

## Neu in der Auswertung

**Skala links**
- Die Skala zeigt die echten Werte der zuletzt angeklickten Kurve.
- Ohne Klick zeigt sie die erste Kurve des Feldes.
- Beschriftung „Kanal [Einheit]“ in der Farbe der Kurve.

**Fähnchen am Cursor**
- Wie in Messen steht an jeder Kurve ein Fähnchen mit dem Wert an der Cursorstelle.
- Bei mehreren Kanälen gleicher Einheit in einem Feld steht der Kanalname davor.
