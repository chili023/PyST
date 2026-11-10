# PyST 1.4.2 – Release Notes

Neue Version des PC-Programms PyST (Mac, Linux/Raspberry Pi, Windows), Stand 09.11.2026.
Sie setzt die Rückmeldungen aus dem ersten Test der neuen Oberfläche von 1.4.1 um. Alle Funktionen mit Bildern stehen
im **[Wiki](https://github.com/chili023/PyST/wiki)**.

![PyST 1.4.2 – Messen mit Fähnchen am Cursor](bilder/v142_messen_cursor.png)

## Neu in 1.4.2

**1. Läufe hinzufügen mit „＋“ (Messen › Seitenleiste Läufe)**
- Der Knopf **＋** oben in der Seitenleiste öffnet das Fenster **Läufe hinzufügen**. Darin stehen alle gespeicherten
  Läufe und Mittelwerte (Ø, fett), wählbar nach Fahrzeug (Standard: das aktuelle, auch „alle Fahrzeuge“), Art (alle /
  nur Messungen / nur Mittelwerte) und mit Suche.
- Die gewählten Läufe liegen danach **gesperrt** (Schloss) auf freien Plätzen im Diagramm. Sind nicht genug Plätze
  frei, sagt PyST das, statt etwas zu verdrängen.
- Der Knopf **Referenz laden …** ist weg. Mittelwerte kommen jetzt ebenfalls über „＋“ ins Diagramm.

![Fenster „Läufe hinzufügen“](bilder/v142_laeufe_hinzufuegen.png)

**2. Nachfrage vor START bei unverändertem Setup**
- Ist in SETUP-WERTE nichts geändert, fragt START kurz **„Setup unverändert – Änderungen eintragen?“**. Das Fenster
  zeigt die SETUP-WERTE als Felder.
- Knöpfe: **Abbrechen** · **Übernehmen und starten** (erst nach einer Änderung, die Werte gelten für diesen Lauf) ·
  **Unverändert starten** (Standard, Enter oder F1).
- Sind die SETUP-WERTE schon geändert, startet der Lauf ohne Frage; die Werte gelten für diesen Lauf.
- Der frühere Dialog „Setup geändert“ (Neues Setup speichern / Nur für diesen Lauf) entfällt. Ein neues Setup legt man
  wie bisher mit **Als neues Setup speichern …** in der Zeile SETUP-WERTE an.
- Der Schalter heißt jetzt **Vor START nachfragen, ob sich das Setup geändert hat** (Einstellungen › Rolle/Lauf,
  Standard an). Die Zangentaste **Neuer Lauf** startet immer ohne Nachfrage.

![START-Nachfrage](bilder/v142_start_nachfrage_geaendert.png)

**3. Groß über dem Diagramm**
- **Anweisungen groß** (Standard an): groß und farbig über dem Diagramm, mit dem Grund darunter, immer in derselben
  Schriftgröße (lange Texte brechen um):
  - **GO – Vollgas!** (grün) bis die Messung beginnt (*n vom Gas*), dann ist sie weg.
  - **Während der Messung steht nichts Großes über der Kurve.** Nur das Statusfeld oben zeigt „Messung …“.
  - Am Laufende ohne Ausrollversuch **GAS WEG!** (rot, mindestens 2 s), danach gleich das große Ergebnis.
  - Mit Ausrollversuch **GAS WEG – KUPPLUNG ZIEHEN!** (rot), bis das Ausrollen fertig ist, dann das große Ergebnis.
  - Aus: nur das große „GAS WEG“ wie bisher.
- **Ergebnis groß** (Standard an): nach dem Lauf Pmax und Mmax groß in der Mitte, mit Farbe, Uhrzeit und Setup des
  Laufs. Weg mit START, Klick oder Esc.
- **Legende im Diagramm** (Standard aus): Farbe, Datum/Zeit und Setup bzw. Notiz aller Läufe. Mittelwerte stehen
  immer in der Legende.
- Alle drei unter Einstellungen › Rolle/Lauf › **Anzeige beim Messen** und im Menü **Ansicht**.

| Laufende: GAS WEG | danach das Ergebnis |
|---|---|
| ![GAS WEG](bilder/v142_messen_gas_weg.png) | ![Ergebnis groß](bilder/v142_messen_ergebnis_gross.png) |

**4. Fähnchen am Cursor statt Cursorzeile**
- Die Zeile „Werte am Cursor“ unter dem Diagramm ist weg. Statt dessen zeigt jede Kurve an der Cursor-Drehzahl ein
  Fähnchen mit ihrem Wert in der Farbe des Laufs (gefüllt Leistung, umrandet Drehmoment).
- Die Live-Kacheln rechts zeigen dabei die Werte des letzten Laufs an der Cursor-Drehzahl, oben steht
  „LAUF hh:mm bei n 1/min“. Maus aus dem Diagramm: wieder live.

**5. Rechte Spalte**
- Bis zu **12 Live-Felder** (vorher 5). Ab 5 Feldern stehen sie zweispaltig und kompakt; bei 1280 × 800 passen
  8 Felder ohne Rollen.
- **UMGEBUNG** steht in einer Zeile, **ERGEBNIS** kompakt nebeneinander.

**6. Rollen-Kalibrierung und Aufteilung der Verluste (Einstellungen › Rolle/Lauf, Gruppe „Verluste – Ausrollversuch“)**
- **Einmal** messen, wie schnell die Rolle **von allein** ausrollt: Knopf **Rollen-Kalibrierung (Assistent) …**. Ein
  Fenster führt in fünf Schritten durch den Versuch ohne Leistungslauf: Vorbereitung, Beschleunigen (Rolle auf die
  Zieldrehzahl), Rad von der Rolle, Messung, Ergebnis. Wiederholen nur nach einem Umbau an der Rolle.
- Das Ergebnis (Verluste der Rolle allein: Lager, Luft) wird mit **Als Rollen-Kalibrierung speichern** für den
  Prüfstand abgelegt, nicht je Fahrzeug, mit Datum, R² und Ordner der Rohdaten. Es geht **nicht** in M0/M1.
  Die Gruppe zeigt „Rollen-Kalibrierung: vom …, Verlust bei … 1/min = … Nm / … PS“ bzw. „noch nicht kalibriert“.
- Der **Ausrollversuch nach dem Lauf** (Kupplung ziehen) misst wie bisher die Verluste **gesamt** (Rolle, Reifen,
  Kette, Getriebeausgang); **Übernehmen** trägt sie wie bisher in M0/M1/m2 ein (Motor = Rad + Verluste). Mit
  Rollen-Kalibrierung teilt PyST sie auf: **gesamt = Rolle + Reifen/Kette/Getriebe**, als Tabelle in Nm und PS (bei
  Pmax, bei n1, oben) und als drei Kurven. Fällt der Anteil Reifen/Kette/Getriebe rechnerisch unter 0, steht ein
  Hinweis dabei.
- Im Ergebnis eines Laufs steht mit Kalibrierung zusätzlich **Reifen an Rolle** (Rad + Verlust der Rolle) und der
  **Antriebsverlust** (Motor − Reifen an Rolle): Ergebniszeile, `lauf.json`, PDF-Bericht, Kanäle in der Auswertung.
  Die Kurven Rad und Motor bleiben wie bisher.
- Zum Ausprobieren: Simulator-Port `SIM:rolle` für die Kalibrierung, `SIM:ausrollen` für den Lauf mit Kupplung (beide
  mit derselben Rolle).

| Kalibrierung, Schritt 5: Ergebnis | Ausrollversuch nach dem Lauf: Aufteilung |
|---|---|
| ![Assistent Schritt 5](bilder/v142_ausroll_5_ergebnis.png) | ![Aufteilung der Verluste](bilder/v142_ausroll_aufteilung.png) |

**7. Software & Firmware (früher Einstellungen › Firmware)**
- Neue Kachel **PyST (Software)** ganz oben: installierte und verfügbare PyST-Version, Datenordner und Knopf zur
  Release-Seite. PyST installiert sich weiterhin nicht selbst.
- Geräte, die nicht angeschlossen sind, stehen ausgegraut mit gestricheltem Rand; ihr Knopf ist aus und sagt im
  Tooltip, was zu tun ist.
- Die Tabelle im Expertenmodus ist schmaler: ein Knopf **Erstflash …**, der Weg steht im Tooltip.

![Software & Firmware](bilder/v142_einst_firmware.png)

**8. Ø automatisch mitteln (Messen › Seitenleiste Läufe)**
- Neuer Haken **Ø automatisch mitteln** oben in der Seitenleiste Läufe neben **＋** (auch im Menü **Ansicht**,
  Standard aus). Er wirkt sofort. Ist er an, mittelt PyST
  die Läufe im Diagramm, die dasselbe Setup haben wie der neueste (mindestens 2, Mittelwerte zählen nicht), und
  zeichnet den Mittelwert dick mit Band min–max, z. B. „Ø 3 Läufe · Serie HD 135 (automatisch)“.
- Er belegt keinen der 5 Plätze, wird nach jedem Lauf, beim Sperren, Ein-/Ausblenden und Löschen von Läufen und beim
  Setup-Wechsel neu gerechnet und nicht von selbst gespeichert. Fähnchen am Cursor und ERGEBNIS (Zeile anklicken)
  zeigen ihn wie andere Kurven.
- Er steht oben in der Seitenleiste Läufe unter **Ø AUTOMATISCH**. **speichern** an seiner Zeile legt einen echten
  Mittelwert-Lauf an (wie Auswertung › Mittelwert bilden), der dann über „＋“ wählbar ist.
- Der wirkungslose Haken „im Messfenster als Referenz anbieten“ im Fenster **Mittelwert bilden** ist weg: jeder
  gespeicherte Mittelwert steht im Fenster „Läufe hinzufügen“.

- Im PDF-Bericht steht er wie im Diagramm (Kurve mit Band) und in der Tabelle mit Pmax/Mmax und den Quellläufen
  (siehe Punkt 10).

![Ø automatisch mitteln](bilder/v142_messen_auto_mittel.png)

**9. Seitenleiste Läufe: Breite ziehen**
- Die Breite der Seitenleiste **Läufe** lässt sich jetzt auch aufgeklappt (nicht angepinnt) ändern: am rechten Rand
  ziehen (Mauszeiger ↔, kleiner Steg in der Mitte). Beim Ziehen klappt die Liste nicht zu.
- Angepinnt zieht man wie bisher am Teiler. Es gibt eine Breite für beide Zustände (240 bis 900 px), PyST merkt sie
  sich (`"laeufe_leiste"` in `einstellungen.json`). Dasselbe gilt für die Seitenleiste „Läufe & Kanäle“ der Auswertung.

**10. PDF-Bericht unten rechts: was im Diagramm steht**
- Neuer Knopf **PDF-Bericht** unten rechts in der Statusleiste. Er druckt genau das, was gerade im Diagramm zu sehen
  ist: die angehakten Läufe auf den Plätzen (gesperrte mit „Platz gesperrt“), Referenzen (Mittelwerte) mit Band und
  Quellläufen, den **automatischen Mittelwert** („Ø 4 Läufe · Serie HD 135 (automatisch)“ mit Pmax/Mmax und den
  Quellläufen), eine noch nicht gespeicherte Kurve, die aktuellen Achsgrenzen und die Kurven oben/unten. Dazu wie
  bisher Fahrzeug/Setup (jetzt mit den Setup-Werten des Laufs), Klima und Ergebnis.
- Eine Kopfzeile sagt, woher das Diagramm kommt („Diagramm aus Messen · Drehzahl 4.000–12.000 1/min · oben: … ·
  unten: … · Achsgrenzen …“). Passt die Tabelle nicht auf eine Seite, geht sie auf der nächsten weiter.
- Ist der Reiter **Auswertung** offen, druckt der Knopf deren Felder mit den dort angezeigten Läufen („Diagramm aus der
  Auswertung“). **Datei › PDF-Bericht …** und der Knopf **PDF-Bericht …** der Auswertung drucken wie bisher das
  Diagramm aus Messen, jetzt ebenfalls mit Mittelwerten. Dateiname und Öffnen danach wie bisher.

![PDF-Bericht aus Messen](bilder/v142_pdf_messen.png)

**11. Tag/Nacht als kleine Knöpfe**
- Der Umschalter unten rechts besteht jetzt aus drei kleinen Piktogramm-Knöpfen **☀** (Tag), **☾** (Nacht) und **◐**
  (Automatisch). Die Wahl ist blau hinterlegt, der Tooltip nennt sie. Das spart Platz für die Zustandsfelder.

![Statusleiste unten rechts](bilder/v142_statusleiste.png)

**12. Ordner mit Unterordnern importieren**
- **Datei › Ordner importieren (mit Unterordnern) …** (Ctrl+Umschalt+I) und in der Auswertung **Importieren ›
  Ordner mit Unterordnern …** (daneben **Dateien …** wie bisher) holen ganze Sammlungen alter Messungen auf einmal:
  LabVIEW-XML der alten Software (PST 3.x), PyST-Laufordner mit `lauf.json` (auch älterer PyST-Versionen, ein Ordner
  = ein Lauf, `lauf.xml` darin zählt nicht extra), `.pyst`-Dateien und Terminal-Mitschnitte.
- Vor dem Import eine **Vorschau**: Datei/Ordner, Datum, „erkannt als“ und Status. Läufe, die schon in der Datenbank
  stehen oder im Ordner doppelt liegen, sind abgewählt; Unlesbares steht mit Grund da. Zuordnung zu Fahrzeug und Setup
  (Standard: das aktuelle). Terminal-Mitschnitte fragen **einmal** nach den Prüfstandsdaten.
- Fortschritt mit **Abbrechen**, am Ende ein Bericht: importiert, übersprungen, Fehler mit Grund.
- Der Import nutzt dieselbe Logik wie der Datei-Import; einzelne Dateien und Drag & Drop bleiben wie bisher.

![Ordner importieren: Vorschau](bilder/v142_import_ordner.png)

## Hinweise und Annahmen

- **Rollen-Kalibrierung:** Mit abgehobenem Rad misst der Assistent nur die Verluste der Rolle. Sie gelten für den
  Prüfstand (Einstellungen, `"rollen_kalibrierung"` in `einstellungen.json`) und werden bei jedem Lauf in `lauf.json`
  mitgeschrieben (`rk_m0` … `rk_m2`). Ältere Läufe ohne diese Felder laden wie bisher, ohne Aufteilung. Die
  Aufteilung des Ausrollversuchs nutzt immer die **aktuelle** Kalibrierung; die Trägheit von Rad und Getriebe bleibt
  wie bisher unberücksichtigt.
- **Ø automatisch mitteln:** „Dasselbe Setup“ heißt gleiches Fahrzeug, gleiches Setup und gleiche Setup-Werte; ein
  Lauf mit Werten „nur für diesen Lauf“ zählt als anderes Setup. Ein Lauf ohne Auto-Speichern zählt erst nach dem
  Speichern. Gemittelt wird wie in Auswertung › Mittelwert bilden (Raster 50 1/min, oben 300 1/min weg).
- **Ordner-Import, „schon vorhanden“:** gleicher Name und gleiche Quelle (LabVIEW, PyST, Terminal), Datum höchstens
  2 Minuten auseinander, derselbe Ordner wie ein Lauf der Datenbank oder eine Datei gleichen Inhalts (auch
  umbenannt). „Doppelt im Ordner“: gleicher Inhalt mehrmals im Ordner. Mittelwert-Ordner werden erkannt, aber nicht
  übernommen.
- **Löschen in Messen:** Markierte Läufe (anklicken, Strg/Umschalt) löscht **Löschen** bzw. Rechtsklick ›
  **Löschen …** nach Rückfrage samt Messdateien. Danach rückt ein älterer Lauf auf den freien Platz nach.
- Die neuen Funktionen sind im Simulator und mit automatischen Tests geprüft, **an der Hardware noch nicht**.

## Update von 1.4.1

1. Ordner mit `git pull` aktualisieren oder das ZIP neu laden.
2. Wie gewohnt starten (`start_mac.command`, `start_linux.sh`, `start_windows.bat`). Neue Abhängigkeiten gibt es nicht.
3. Datenbank, Läufe und Einstellungen in `~/PyST` bleiben erhalten. Die neuen Schalter starten mit ihrem Standard
   (Ergebnis groß an, Anweisungen groß an, Legende aus, Nachfrage vor START an, Ø automatisch mitteln aus).
4. Von 1.2 oder älter: zuerst die Hinweise in den [Release Notes 1.4.1](RELEASE_NOTES_1.4.1.md#update-von-12).

## Noch nicht an der Hardware bestätigt

- Rollen-Kalibrierung mit abgehobenem Rad und die Aufteilung der Verluste.
- Aus 1.4.1: Mega und F446 aus PyST flashen, Übersetzungsart **Funkenmessung**, Zangentasten im Abgleichfenster,
  Updates im Format 2 und Firmware 0.4.0 von Basis, Zange und Basis-ESP.

Bitte Rückmeldungen über die [Issues](https://github.com/chili023/PyST/issues).

## Bekannte Einschränkungen

- Mittelwert-Läufe gehen noch nicht in `.pyst`-Dateien mit; der Ordner-Import übernimmt keine Mittelwert-Ordner
  (Quellläufe importieren und neu mitteln).
- „Als Hauptzange“ und „Tasten prüfen“ sind ausgegraut, weil die Zangen-Firmware dafür noch keinen Befehl hat.
- PyST steuert den Motor nicht. „n Stop“ und die große Anweisung zeigen nur **GAS WEG**.
- Die Zange **nie am laufenden Motor per USB anschließen**: Sie ist dann galvanisch mit dem PC verbunden.

## Tests

Automatische Tests unter Linux mit Python 3 und PySide6, dazu Oberflächen-Rauchtests im Simulator, in Tag und Nacht
und bei 1280 × 800. Stand der Freigabe: 633 Tests bestanden, 1 übersprungen.

Der Stand 1.4.1 liegt unter dem Tag `v1.4.1`.
