# Läufe weitergeben

Läufe an Kunden, Kollegen oder einen zweiten PC geben, und Läufe aus anderen Quellen (LabVIEW, Terminal-Mitschnitt)
übernehmen.

## Weitergeben

**Datei › Läufe weitergeben (.pyst) …** (Ctrl+Shift+S) öffnet das Fenster **„Läufe weitergeben“**.

### Welche Läufe?

| Auswahl | Läufe |
|---|---|
| **in der Liste markierte (n)** | in der Laufliste markiert |
| **angehakte / sichtbare (n)** | im Diagramm angehakt |
| **alle des Fahrzeugs „…“ (n)** | alle Läufe des gewählten Fahrzeugs |
| **alle vom Tag** | alle Läufe eines Tages (Tag aus der Liste wählen) |

### Format

| Format | Für wen |
|---|---|
| **eine .pyst-Datei (öffnet in PyST, alle Messdaten, Fahrzeug/Setup)** | anderer PC mit PyST |
| **Ordner mit je einer .pyst-Datei pro Lauf** | dasselbe, einzeln |
| **HTML-Ansicht – eine Datei, öffnet in jedem Browser ohne PyST** | Kunde zu Hause, Handy |

Direkt als HTML: **Datei › Als HTML-Ansicht exportieren (ohne PyST ansehbar) …** (Ctrl+H).

### .pyst-Datei

- Enthält alle Messdaten, Parameter, Fahrzeug und Setup der Läufe.
- Beim Öffnen legt PyST Fahrzeug und Setup an oder nimmt ein gleichnamiges, das es schon gibt.
- Läufe, die schon in der Datenbank sind (gleicher Name, Datum und Quelle), übernimmt PyST nicht noch einmal.
- Mittelwerte kommen nicht in die `.pyst`-Datei (Stand 1.4.2).

### HTML-Ansicht

Eine einzige Datei. Läuft offline in jedem Browser, auch auf dem Handy.

- Oben groß: **Pmax** in PS und kW, **Mmax**, Fahrzeug, Datum, Klima, Anteil der Fehler.
- Diagramm Leistung und Drehmoment über der Drehzahl.
- Bis zu zwei zuschaltbare Kanäle (soweit im Lauf: Lambda, AFR, EGT 1, EGT 2, Drehzahl Zündung, Geschwindigkeit,
  Übersetzung gefiltert, Drehzahl Rolle, Fehler Rolle, Fehler Zündung).
- Werte unter dem Cursor, Zoom durch Ziehen, Knopf **Zoom zurück**.
- Bei mehreren Läufen: Auswahl des Laufs und optional ein Vergleichslauf.

## Öffnen und importieren

| Weg | Wirkung |
|---|---|
| **Datei › Öffnen / importieren (.pyst, Läufe, Mitschnitte) …** (Ctrl+O) | Datei wählen |
| **Datei › Läufe importieren …** (Ctrl+I) | dasselbe für Läufe |
| **Importieren › Dateien …** in der Auswertung | dasselbe |
| **Datei › Ordner importieren (mit Unterordnern) …** (Ctrl+Umschalt+I) oder **Importieren › Ordner mit Unterordnern …** | ganzer Ordner mit Unterordnern, mit Vorschau und Duplikaterkennung, siehe [Auswertung › Ordner importieren](Auswertung.md#ordner-importieren) |
| Datei ins Fenster ziehen (Drag & Drop) | öffnet die Datei |
| Start mit Datei, z. B. `./start_linux.sh lauf.pyst` | öffnet die Datei in der Auswertung |

### Unterstützte Dateien

| Datei | Inhalt |
|---|---|
| `*.pyst` | PyST-Läufe mit Fahrzeug und Setup |
| `*.xml` | LabVIEW-Lauf (PST 3.2.1) |
| `*.json` | PyST `lauf.json` |
| `*.log`, `*.txt`, `*.csv` | Rohmitschnitt aus einem Terminalprogramm |

### Rohmitschnitt

Ein Terminal-Mitschnitt enthält nur die Telegramme (ggf. Klima und Firmware-Version), aber keine Prüfstandsdaten.
PyST fragt deshalb im Fenster **„Import Rohmitschnitt“** nach:

| Feld | |
|---|---|
| Rollengeber [Inkr/U] | |
| Rollenumfang [m] | |
| Rollenträgheit J [kgm²] | |
| Zündung [Imp/U] | |
| Übersetzung nKW/nRolle | |

Vorbelegt sind die aktuellen Einstellungen. Knopf **Importieren**. Später änderbar mit **Mit aktuellen Einstellungen
neu rechnen**.

### LabVIEW-Läufe

Eine LabVIEW-XML bringt ihre Prüfstandsdaten mit. Die Filter stehen dort in Messpunkten. Zum Vergleich mit LabVIEW
siehe [Rechenweg](Rechenweg.md).

## Experten

- Eine `.pyst`-Datei ist ein ZIP-Archiv mit `inhalt.json` (Übersicht, Fahrzeug, Setup) und je Lauf einem Ordner mit
  den gespeicherten Dateien (`roh.csv`, `lauf.json`, `kurve.csv` …).
- Die HTML-Datei bettet die Plot-Bibliothek uPlot 1.6.31 (MIT-Lizenz, Lizenztext in der Datei) und die Daten ein.
  Je Lauf und Kanal höchstens 3000 Punkte (Dauermessungen werden ausgedünnt).
- Kommandozeile: `python -m pyst recalc datei.xml` bzw. `python -m pyst compare`, siehe [Kommandozeile](Kommandozeile.md).
