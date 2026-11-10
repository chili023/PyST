> Diese Seiten sind eine Kopie des [GitHub-Wikis](https://github.com/chili023/PyST/wiki) (Stand PyST 1.4.2).

# PyST 1.4.2 – Wiki

PyST ist die PC-Software zum Rollenprüfstand. Sie läuft auf **Mac**, **Linux** (auch Raspberry Pi) und **Windows**.
PyST liest die Messelektronik, rechnet Leistung und Drehmoment wie LabVIEW 3.2.1, speichert jeden Lauf in einer
Datenbank und hilft beim Vergleichen von Setups.

![PyST 1.4.2 – Messen während eines Laufs](https://raw.githubusercontent.com/chili023/PyST/main/docs/bilder/v142_messen_lauf.png)

> ⚠️ **PyST steuert den Motor nicht.** Erreicht der Motor *n Stop*, zeigt PyST nur **GAS WEG** – Gas wegnehmen
> muss der Fahrer. Die Funk-Zange **nie per USB anschließen, während der Motor läuft** (galvanische Verbindung zum PC).

## Was PyST kann

| Bereich | Kurz |
|---|---|
| **Messen** | Lauf starten (F1) mit Nachfrage zum Setup, Live-Kurve, bis 12 Live-Kacheln, große Anweisungen (GO, GAS WEG), großes Ergebnis Pmax/Mmax, Fähnchen am Cursor, 5 Plätze im Diagramm mit Schloss, alte Läufe und Mittelwerte über „＋“ |
| **Übersetzung** | von Hand eintragen, einmessen, live mit dem Drehzahlmesser abgleichen, Funkenmessung nach jedem Lauf |
| **Fahrzeuge & Setups** | Datenbank mit Fahrzeugen und Setups (Motor, Vergaser/ECU, Zündung, Antrieb …), Setup-Werte direkt im Messbildschirm ändern |
| **Auswertung** | Läufe überlagern wie in MegaLogViewer, bis zu 4 Felder, Werte am Cursor, Setup-Vergleich, Mittelwert aus mehreren Läufen, PDF- und CSV-Export |
| **Weitergeben** | `.pyst`-Datei (öffnet in PyST) oder HTML-Datei (öffnet in jedem Browser, offline) |
| **Oszi / Dauermessung** | Fehlersuche an Zündung und Rolle: Linienschreiber, Software-Trigger, Ereignisliste, Flanken-Ansicht |
| **Ausrollversuch** | Verluste von Rolle, Rad, Kette bestimmen, dann Motorleistung neben der Radleistung. Einmalige Rollen-Kalibrierung (Rad abgehoben) teilt sie in Rolle und Reifen/Kette/Getriebe auf |
| **PyST-Basis & Zange** | Funk-Zündzange mit Tasten, Lambda-Heizung, WLAN, WLAN-Steckdosen |
| **Software & Firmware** | installierte und neueste PyST-Version; Basis, Zange, Arduino Mega und STM32 (F446) aus PyST heraus aktualisieren |
| **Darstellung** | Tag, Nacht oder Auto, Fenstergröße einstellbar |

## Hauptreiter

| Reiter | Seite im Wiki |
|---|---|
| Messen | [Messen](Messen.md), [Übersetzung](Übersetzung.md) |
| Fahrzeuge & Setups | [Fahrzeuge und Setups](Fahrzeuge-und-Setups.md) |
| Auswertung | [Auswertung](Auswertung.md), [Läufe weitergeben](Läufe-weitergeben.md) |
| Oszi / Dauermessung | [Oszi und Dauermessung](Oszi-und-Dauermessung.md) |
| Einstellungen | [Einstellungen](Einstellungen.md) mit den Unterreitern Rolle/Lauf, Verbindung, Zange, Lambda, ECU, Software & Firmware, WiFi |

Weitere Seiten: [Installation](Installation.md) · [Erste Schritte](Erste-Schritte.md) · [Ausrollversuch](Ausrollversuch.md) ·
[Firmware und Updates](Firmware-und-Updates.md) · [PyST-Basis und Zange](PyST-Basis-und-Zange.md) ·
[Darstellung](Darstellung.md) · [Kommandozeile](Kommandozeile.md) · [Rechenweg](Rechenweg.md) · [Fehlersuche](Fehlersuche.md)

## Neu in 1.4.2

- **Messen, Läufe hinzufügen:** Der Knopf **＋** oben in der Seitenleiste Läufe öffnet ein Auswahlfenster für alte
  Läufe und Mittelwerte (auch anderer Fahrzeuge). Sie liegen danach gesperrt auf Plätzen im Diagramm. Der Knopf
  „Referenz laden …“ ist weg. Siehe [Messen](Messen.md#läufe-hinzufügen).
- **Ø automatisch mitteln:** Haken oben in der Seitenleiste Läufe neben **＋** (auch im Menü Ansicht). PyST mittelt die Läufe im Diagramm mit dem
  Setup des neuesten Laufs, zeichnet den Mittelwert mit Band und rechnet ihn nach jedem Lauf neu, ohne einen Platz zu
  belegen. **speichern** macht daraus einen Mittelwert-Lauf. Siehe [Messen](Messen.md#ø-automatisch-mitteln).
- **START-Nachfrage:** Ist das Setup unverändert, fragt START kurz „Setup unverändert – Änderungen eintragen?“ – mit den
  SETUP-WERTEN als Felder und den Knöpfen **Abbrechen**, **Übernehmen und starten**, **Unverändert starten**. Die
  Zangentaste „Neuer Lauf“ startet ohne Nachfrage. Siehe [Messen](Messen.md#start-nachfrage).
- **Groß über dem Diagramm:** Anweisungen für den Fahrer (GO, GAS WEG, KUPPLUNG ZIEHEN) und nach dem Lauf Pmax/Mmax.
  Schalter unter Einstellungen › Rolle/Lauf › **Anzeige beim Messen** und im Menü Ansicht, dazu **Legende im
  Diagramm** (Standard aus).
- **Fähnchen am Cursor:** Jede Kurve zeigt ihren Wert in der Farbe des Laufs. Die Live-Kacheln rechts zeigen dabei die
  Werte des letzten Laufs an der Cursor-Drehzahl. Die Zeile „Werte am Cursor“ unter dem Diagramm ist weg.
- **Rechte Spalte:** bis 12 Live-Felder, ab 5 zweispaltig und kompakt; UMGEBUNG in einer Zeile, ERGEBNIS kompakt.
- **Rollen-Kalibrierung** (Einstellungen › Rolle/Lauf): einmal das Rad von der Rolle nehmen und messen, wie schnell
  die Rolle allein ausrollt. Danach teilt PyST den Ausrollversuch nach dem Lauf in Rolle und Reifen/Kette/Getriebe
  auf. Siehe [Ausrollversuch](Ausrollversuch.md#rollen-kalibrierung-ausrollen-ohne-rad).
- **Software & Firmware:** neuer Name des Unterreiters, Kachel **PyST (Software)** mit installierter und verfügbarer
  Version. Nicht angeschlossene Geräte sind ausgegraut.
- **PDF-Bericht unten rechts:** Der Knopf **PDF-Bericht** in der Statusleiste druckt, was gerade im Diagramm steht –
  mit Referenzen, Ø automatisch, Achsgrenzen und Kurvenauswahl. Tag/Nacht/Automatisch sind jetzt die kleinen Knöpfe
  **☀ ☾ ◐**. Siehe [Messen](Messen.md#pdf-bericht) und [Darstellung](Darstellung.md).
- **Ordner importieren:** ganze Ordner mit Unterordnern voller alter Messungen (LabVIEW-XML der alten Software,
  PyST-Laufordner, `.pyst`, Terminal-Mitschnitte) mit Vorschau, Duplikaterkennung und Zuordnung zu Fahrzeug/Setup.
  Siehe [Auswertung](Auswertung.md#ordner-importieren).

Alle Änderungen: [Release Notes 1.4.2](https://github.com/chili023/PyST/blob/main/docs/RELEASE_NOTES_1.4.2.md).

## Neu in 1.4.1

- **Neue Reiterstruktur:** fünf Hauptreiter. Die Einstellungen haben sieben Unterreiter. Der frühere Hauptreiter
  *ECU* ist jetzt **Einstellungen › ECU**, der frühere Reiter *Kalibrierung* (Ausrollversuch) steckt in
  **Einstellungen › Rolle/Lauf** als Gruppe „Verluste – Ausrollversuch“.
- **Tag-, Nacht- und Auto-Thema**, umschaltbar unten rechts oder im Menü *Ansicht › Darstellung*.
- **Statusleiste mit drei farbigen Feldern:** PST Basis · PST Zange/Mega · PST/Lambda ECU.
- **Messen:** Kopfzeile mit *Verbinden/Trennen*, *Lauf-Einstellungen …* und *START (F1)*. Im Lauf wird START rot
  zu *Abbrechen (Esc)*. Zeile **SETUP-WERTE** direkt unter der Kopfzeile. Rechts Live-Kacheln je Fahrzeug.
  Läufe als ausklappbare Seitenleiste, **5 Plätze** im Diagramm mit **Schloss**, **Referenz laden …**.
- **Übersetzung:** Fenster **„Übersetzung live abgleichen“** mit Drehzahlmesser und Schritten ±0,05 / ±0,1 / ±1,
  Bedienung auch mit den Zangentasten. Neue Übersetzungsart **Funkenmessung**.
- **Auswertung:** Laufleiste „ANGEZEIGTE LÄUFE“ mit Setup-Spalten und gelb markierten Änderungen,
  **Mittelwert bilden …**, Werte am Cursor als Kacheln.
- **Firmware:** Kundenansicht mit vier Karten und Expertenmodus. Neu: Arduino Mega (avrdude) und STM32 F446 flashen.

### Noch nicht am Prüfstand bestätigt

- Mega und F446 aus PyST flashen.
- Übersetzungsart **Funkenmessung**.
- **Zangentasten** im Fenster „Übersetzung live abgleichen“.
- **Rollen-Kalibrierung** mit abgehobenem Rad und Aufteilung der Verluste (1.4.2).

Diese Funktionen sind nur im Simulator bzw. mit Tests geprüft. Rückmeldungen bitte über die
[Issues](https://github.com/chili023/PyST/issues).
