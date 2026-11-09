# Auswertung

Im Reiter **Auswertung** legt man Läufe übereinander wie in MegaLogViewer, liest Werte am Cursor ab, vergleicht
Setups und bildet Mittelwerte.

![Auswertung](https://raw.githubusercontent.com/chili023/PyST/main/docs/bilder/v141_auswertung.png)

## Kopfzeile

| Element | Funktion |
|---|---|
| **X-Achse** | **Zeit** oder **Motordrehzahl** |
| **Felder** | 1 bis 4 Diagramme untereinander |
| **nur Kurvenbereich** | nur den ausgewerteten Teil des Laufs zeigen (ohne Vorlauf und Ausrollen) |
| **Layout** | gespeicherte Anordnung von Feldern und Kanälen. „(ungespeichert)“ zeigt eine Änderung. Knöpfe **Speichern**, **Speichern unter …**, **Löschen** |
| **Importieren …** | Läufe aus Dateien holen, siehe [Läufe weitergeben](Läufe-weitergeben.md) |
| **PDF-Bericht …** | Bericht der Läufe, die im Reiter Messen angehakt sind |
| **CSV-Export …** | markierter Lauf als CSV. Bei einem Mittelwert: gemittelte Kurven plus min/max/σ |

## Seitenleiste „Läufe & Kanäle“ (links)

### Läufe auswählen

| Element | Funktion |
|---|---|
| **Fahrzeug** | **alle Fahrzeuge**, **ohne Fahrzeug** oder ein Fahrzeug |
| Sortierung | **nach Datum** oder **nach Setup** |
| **Hinzufügen ↓** | markierte Läufe in die Auswertung |
| **Fahrzeug bearbeiten …** | Fahrzeug des Laufs bearbeiten |

### In der Auswertung (Häkchen = anzeigen)

Liste der Läufe in der Auswertung. Knöpfe **Entfernen**, **Alle entfernen**, **Bearbeiten …**.

Rechtsklick: **Setup und Notiz des Laufs …**, **Fahrzeug / Setup bearbeiten …**, **Setup zuordnen** › (Setup wählen
oder **Neues Setup …**), **Aus der Auswertung entfernen**.

### Kanäle je Feld

Für **Feld 1** bis **Feld 4** wählt man die Kanäle (Leistung, Drehmoment, Drehzahlen, Lambda, EGT, Fehlerkanäle,
ECU-Kanäle …).

## Laufleiste „ANGEZEIGTE LÄUFE“

Über den Diagrammen steht je Lauf eine Zeile:

| Spalte | Inhalt |
|---|---|
| **mark.** | Lauf markieren (für Mittelwert, CSV) |
| **anzeigen** + Farbe | Lauf zeigen oder ausblenden |
| **Lauf** | Datum, Uhrzeit, Name |
| **Setup** | Name des Setups |
| Schnellwert-Spalten | die Felder der Zeile SETUP-WERTE des Fahrzeugs (z. B. Hauptdüse, Clip, ZZP) |
| **Pmax**, **Mmax** | Höchstwerte |
| **Notiz (direkt änderbar)** | Notiz anklicken und ändern |
| **Setup …** | Setup des Laufs öffnen. Bei einem Mittelwert steht hier **Quellen ▾** (Liste der Quellläufe) |
| **✕** | Lauf aus der Auswertung nehmen |

Ein Wert, der sich gegenüber dem Lauf darüber geändert hat, ist **gelb** und zeigt einen Pfeil, z. B. „140 ↑ 135“.

Ab 5 Läufen klappt die Leiste zu kleinen Chips zusammen.

## Diagramme

- **Cursor:** Maus bewegen oder ← / → (ein Telegramm weiter).
- **Mausrad:** zoomen. **Ziehen:** verschieben.
- Ein Lauf: je Kanal eine Farbe. Mehrere Läufe: je Lauf eine Farbe, je Kanal eine Linienart.
- Mittelwerte haben eine eigene Farbe (violett) und eine dicke Linie.

## Rechte Seite

### Werte am Cursor

- Kachel **Motordrehzahl am Cursor**.
- Je Kanal eine Kachel mit den Werten aller Läufe.
- **Pmax je Lauf** und **Mmax je Lauf**.

### Setup-Vergleich

![Setup-Vergleich](https://raw.githubusercontent.com/chili023/PyST/main/docs/bilder/v141_setup_vergleich.png)

Alle Setup-Felder der angezeigten Läufe nebeneinander. Unterschiede sind gelb.

## Setup des Laufs …

Das Fenster zeigt **Setup dieses Laufs** mit allen Feldern. Felder, die sich gegenüber dem vorigen Lauf geändert haben,
sind gelb. Dazu **Notiz zum Lauf** und **Als neues Setup kopieren …**. Wird das Setup auch von anderen Läufen benutzt,
warnt PyST, denn eine Änderung gilt dann für alle diese Läufe.

## Mittelwert bilden

![Mittelwert bilden](https://raw.githubusercontent.com/chili023/PyST/main/docs/bilder/v142_mittelwert.png)

Mehrere Läufe mit demselben Setup mitteln. Das glättet Streuung von Lauf zu Lauf.

1. In der Laufleiste mindestens 2 Läufe unter **mark.** anhaken.
2. **Mittelwert bilden …  (n markiert)** drücken.

| Bereich | Inhalt |
|---|---|
| **Quellläufe** | mindestens 2, einzelne kann man abhaken. Gelbe Warnung, wenn die Setups verschieden sind |
| **Drehzahlraster** | Standard 50 1/min |
| **oben weglassen** | Standard 300 1/min (Ende der Kurve, wo die Läufe auseinanderlaufen) |
| **Bereich** | gemeinsamer Drehzahlbereich, steigender Teil der Kurve |
| **Streuband** | **min – max**, **± σ** oder **kein Band** |
| Vorschau | Mittelwert mit Band |
| **Speichern als Lauf** | **Name** (Vorschlag z. B. „Ø 3 Läufe · Serie HD 135“), **Fahrzeug**, **Setup**, **Notiz** |
| **im Messfenster als Referenz anbieten (Messen › Läufe „＋“)** | wirkt in 1.4.2 nicht: jeder gespeicherte Mittelwert steht in Messen im Fenster „Läufe hinzufügen“, mit oder ohne Haken |
| **Mittelwert speichern** / **Abbrechen** | |

Der Mittelwert ist danach ein eigener Lauf. In [Messen](Messen.md#läufe-hinzufügen) holt man ihn über **＋** in der
Seitenleiste Läufe (Art „nur Mittelwerte“) gesperrt ins Diagramm. Dort steht er als dicke Linie mit Streuband und dient
als Referenz für jeden neuen Lauf.

**Grenzen (Stand 1.4.2):**

- Mittelwerte kommen nicht in `.pyst`-Dateien und nicht in die Tabelle des PDF-Berichts.
- Für Mittelwerte gibt es keine Rohdaten-CSV (nur die gemittelten Kurven).
- Einen Mittelwert kann man nicht noch einmal mitteln.

## Export

| Weg | Ergebnis |
|---|---|
| **PDF-Bericht …** (Ctrl+P) | Bericht der sichtbaren Läufe aus Messen: Diagramm, Tabelle, Klima, Setup. Immer in Tag-Farben |
| **CSV-Export …** (Ctrl+E) | markierter Lauf. Trennzeichen Semikolon, öffnet in Excel/LibreOffice |
| Weitergeben (Ctrl+S, Ctrl+H) | siehe [Läufe weitergeben](Läufe-weitergeben.md) |

## Nachtansicht

![Auswertung Nacht](https://raw.githubusercontent.com/chili023/PyST/main/docs/bilder/v141_auswertung_nacht.png)

Siehe [Darstellung](Darstellung.md).

## Experten

- Mittelung: PyST legt alle Quellläufe auf ein gemeinsames Drehzahlraster und mittelt je Rasterpunkt. Das Band ist
  min/max oder die Standardabweichung je Punkt.
- Die Auswertung rechnet Läufe mit den beim Lauf gespeicherten Parametern. Neu rechnen mit den aktuellen Einstellungen
  geht in Messen per Rechtsklick (**Mit aktuellen Einstellungen neu rechnen**).
- Läufe mit Verlusten (M0/M1/m2 ≠ 0) haben zusätzlich die Kanäle „Leistung Rad“ und „Verlustleistung“. „Leistung“ ist dann die Motorleistung ([Ausrollversuch](Ausrollversuch.md)).
