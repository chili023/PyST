# Schritt 1 – Vergleichstest und Zündsignal-Diagnose

Ablauf der Vergleichsmessung (alt/neu, LabVIEW/PyST): siehe
[`PyST/TESTPLAN.md`](../PyST/TESTPLAN.md). Hier zusätzlich: was wir über das
Zündsignal herausfinden wollen und welche Daten wir danach brauchen.

## Ausgangslage

In allen sechs Läufen vom 26.05.26 liefert der Zündeingang pro Kurbelwellenumdrehung meist zwei Impulse:
den echten und einen zweiten bei ~142° (≈ 39 % der Periode). In 20–50 % der Telegramme fehlt der
zweite. Der Rolleneingang läuft durch dieselbe Firmware-Kette (TIM2, gleiche Rechnung) und dieselbe
Eingangsschaltung (6N137) und ist sauber → Ursache liegt vor dem Eingang: Zündabgriff, Leitung,
Signalform (Überschwinger/Nachschwingen) im Zusammenspiel mit der Schaltschwelle.

## Checkliste am Prüfstand

### Vorher
- [ ] Aktuelle Firmware sichern: `st-flash --connect-under-reset read ~/pst_firmware_backup_alt.bin 0x08000000 0x80000`
- [ ] Beide Firmware-Varianten gebaut: `pio run` (Standard) und `pio run -e nucleo_f446re_dp70`
- [ ] PyST auf dem Mac startet, Simulator-Lauf funktioniert
- [ ] Oszilloskop (wenn vorhanden), Tastkopf 10:1

### Messungen zum Zündsignal
1. **Neue Firmware Standard (25 %)**, Motor konstant ~3000 und ~6000 1/min:
   „Motor (Zündung)“ vs. „Motor (Rolle × i)“ notieren. `v` senden → Zeile „Impulse Zündung/Rolle … verworfen“.
2. **Variante dp70 (70 %)**, gleiche Drehzahlen: Zündung muss = Rolle × i sein (Vario: bis auf Übersetzung).
   „verworfen“ ≈ Anzahl Störimpulse → Anteil Störimpulse = verworfen / Impulse.
3. **Eingänge tauschen** (Zündsignal an PB9, Rolle an PA0): Wandert der Fehler mit dem Signal → Signal.
   Bleibt er am Eingang → Eingangsstufe.
4. **Oszilloskop**
   - vor der Eingangsschaltung (Zündabgriff) und an PA0 (nach Optokoppler)
   - Trigger auf den Zündimpuls, Zeitbasis so, dass eine ganze Umdrehung sichtbar ist
   - suchen: zweiter Impuls ~142° danach; Breite, Höhe, Form von beiden
5. **Abgriff variieren** (falls möglich): Zange weiter weg vom Zylinder / anderes Kabel / Masseführung,
   geschirmte Leitung → ändert sich der Anteil der Störimpulse?

### Mitbringen
- alle LabVIEW-XML (Test A, B1, B2) und PyST-Ordner (Test C)
- Fotos/Screenshots vom Oszilloskop, Notizen zu Abgriff und Leitungsführung
- Ausgabe von `v` für beide Firmware-Varianten

## Entscheidungen nach dem Test

| Ergebnis | Nächster Schritt |
|---|---|
| dp70 liefert saubere Drehzahl | vorerst dp70 für diesen Prüfstand; Plausibilitätsfilter (Schritt 2) als Dauerlösung |
| Störimpuls hängt am Signal | Aufbereitung verbessern (Schritt 4: Komparator mit adaptiver Schwelle + Hardware-Totzeit), kurzfristig andere Abgriffstelle |
| Störimpuls hängt am Eingang | Eingangsstufe prüfen (Vorwiderstand, Schwelle, Masse), dann erneut messen |
| rusEFI vorhanden | Drehzahl künftig von der ECU über CAN (Schritt 3/5) |
