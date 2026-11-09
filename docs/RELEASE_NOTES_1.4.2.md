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
- **Anweisungen groß** (Standard an): GO – Vollgas!, Messung …, GAS WEG, KUPPLUNG ZIEHEN groß und farbig über dem
  Diagramm, mit dem Grund darunter. Aus: nur das große „GAS WEG“ wie bisher.
- **Ergebnis groß** (Standard an): nach dem Lauf Pmax und Mmax groß in der Mitte, mit Farbe, Uhrzeit und Setup des
  Laufs. Weg mit START, Klick oder Esc.
- **Legende im Diagramm** (Standard aus): Farbe, Datum/Zeit und Setup bzw. Notiz aller Läufe. Mittelwerte stehen
  immer in der Legende.
- Alle drei unter Einstellungen › Rolle/Lauf › **Anzeige beim Messen** und im Menü **Ansicht**.

| GAS WEG | Ergebnis nach dem Lauf |
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

**6. Ausrollversuch-Assistent (Einstellungen › Rolle/Lauf, Gruppe „Verluste – Ausrollversuch“)**
- Knopf **Ausrollversuch (Assistent) …**: ein Fenster führt in fünf Schritten durch den Versuch ohne Leistungslauf:
  Vorbereitung, Beschleunigen (Rolle auf die Zieldrehzahl), Rad von der Rolle, Messung, Ergebnis (Übernehmen, Nochmal).
- Gemessen werden nur die **Verluste der Rolle** (Lager, Luft), weil das Rad abgehoben ist.
- Zum Ausprobieren: Simulator-Port `SIM:rolle`.

| Schritt 2: Beschleunigen | Schritt 5: Ergebnis |
|---|---|
| ![Assistent Schritt 2](bilder/v142_ausroll_2_beschleunigen.png) | ![Assistent Schritt 5](bilder/v142_ausroll_5_ergebnis.png) |

**7. Software & Firmware (früher Einstellungen › Firmware)**
- Neue Kachel **PyST (Software)** ganz oben: installierte und verfügbare PyST-Version, Datenordner und Knopf zur
  Release-Seite. PyST installiert sich weiterhin nicht selbst.
- Geräte, die nicht angeschlossen sind, stehen ausgegraut mit gestricheltem Rand; ihr Knopf ist aus und sagt im
  Tooltip, was zu tun ist.
- Die Tabelle im Expertenmodus ist schmaler: ein Knopf **Erstflash …**, der Weg steht im Tooltip.

![Software & Firmware](bilder/v142_einst_firmware.png)

**8. Ø automatisch mitteln (Messen › Seitenleiste Läufe)**
- Neuer Haken **Ø automatisch mitteln** neben „＋“ (auch im Menü **Ansicht**, Standard aus). Ist er an, mittelt PyST
  die Läufe im Diagramm, die dasselbe Setup haben wie der neueste (mindestens 2, Mittelwerte zählen nicht), und
  zeichnet den Mittelwert dick mit Band min–max, z. B. „Ø 3 Läufe · Serie HD 135 (automatisch)“.
- Er belegt keinen der 5 Plätze, wird nach jedem Lauf, beim Sperren, Ein-/Ausblenden und Löschen von Läufen und beim
  Setup-Wechsel neu gerechnet und nicht von selbst gespeichert. Fähnchen am Cursor und ERGEBNIS (Zeile anklicken)
  zeigen ihn wie andere Kurven.
- **speichern** an seiner Zeile legt einen echten Mittelwert-Lauf an (wie Auswertung › Mittelwert bilden), der dann
  über „＋“ wählbar ist.
- Der wirkungslose Haken „im Messfenster als Referenz anbieten“ im Fenster **Mittelwert bilden** ist weg: jeder
  gespeicherte Mittelwert steht im Fenster „Läufe hinzufügen“.

![Ø automatisch mitteln](bilder/v142_messen_auto_mittel.png)

## Hinweise und Annahmen

- **Ausrollversuch-Assistent:** Mit abgehobenem Rad misst er nur die Verluste der Rolle, nicht Rad, Kette und
  Getriebeausgang. Übernimmt man das Ergebnis, ist der Wert „Motor“ die **Reifenleistung an der Rolle** (Leistung, die
  der Reifen an die Rolle abgibt), noch nicht die Motorleistung. Für die Verluste bis zur Kupplung bleibt der
  Ausrollversuch nach dem Lauf mit gezogener Kupplung.
- **Ø automatisch mitteln:** „Dasselbe Setup“ heißt gleiches Fahrzeug, gleiches Setup und gleiche Setup-Werte; ein
  Lauf mit Werten „nur für diesen Lauf“ zählt als anderes Setup. Ein Lauf ohne Auto-Speichern zählt erst nach dem
  Speichern. Gemittelt wird wie in Auswertung › Mittelwert bilden (Raster 50 1/min, oben 300 1/min weg).
- Die neuen Funktionen sind im Simulator und mit automatischen Tests geprüft, **an der Hardware noch nicht**.

## Update von 1.4.1

1. Ordner mit `git pull` aktualisieren oder das ZIP neu laden.
2. Wie gewohnt starten (`start_mac.command`, `start_linux.sh`, `start_windows.bat`). Neue Abhängigkeiten gibt es nicht.
3. Datenbank, Läufe und Einstellungen in `~/PyST` bleiben erhalten. Die neuen Schalter starten mit ihrem Standard
   (Ergebnis groß an, Anweisungen groß an, Legende aus, Nachfrage vor START an, Ø automatisch mitteln aus).
4. Von 1.2 oder älter: zuerst die Hinweise in den [Release Notes 1.4.1](RELEASE_NOTES_1.4.1.md#update-von-12).

## Noch nicht an der Hardware bestätigt

- Ausrollversuch-Assistent mit abgehobenem Rad.
- Aus 1.4.1: Mega und F446 aus PyST flashen, Übersetzungsart **Funkenmessung**, Zangentasten im Abgleichfenster,
  Updates im Format 2 und Firmware 0.4.0 von Basis, Zange und Basis-ESP.

Bitte Rückmeldungen über die [Issues](https://github.com/chili023/PyST/issues).

## Bekannte Einschränkungen

- Mittelwert-Läufe gehen noch nicht in `.pyst`-Dateien mit und stehen nicht in der PDF-Tabelle.
- „Als Hauptzange“ und „Tasten prüfen“ sind ausgegraut, weil die Zangen-Firmware dafür noch keinen Befehl hat.
- PyST steuert den Motor nicht. „n Stop“ und die große Anweisung zeigen nur **GAS WEG**.
- Die Zange **nie am laufenden Motor per USB anschließen**: Sie ist dann galvanisch mit dem PC verbunden.

## Tests

Automatische Tests unter Linux mit Python 3 und PySide6, dazu Oberflächen-Rauchtests im Simulator, in Tag und Nacht
und bei 1280 × 800. Stand der Freigabe: 616 Tests bestanden, 1 übersprungen.

Der Stand 1.4.1 liegt unter dem Tag `v1.4.1`.
