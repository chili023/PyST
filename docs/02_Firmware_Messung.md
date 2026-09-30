# Schritt 2 – Messverfahren in der Firmware verbessern (jetzige Hardware)

Alles hier geht mit der vorhandenen Platine (Nucleo-F446RE + PST v2xx). Grundlage bleibt das
Zeitstempel-Verfahren: jede gültige Flanke wird von TIM2 (90 MHz, 11 ns) gestempelt und in einen
Ringpuffer geschrieben (`src/freq_calc.c`, `src/capture.c`).

## 2.1 Rolle: Mittelung über eine volle Umdrehung

**Problem:** Die Zeit zwischen zwei Geberstrichen enthält die Teilungsfehler der Scheibe (Mai-Läufe:
~1 % Rauschen je Messpunkt). Die jetzige Fenster-Mittelung verringert das nur, solange viele Striche im
Fenster liegen.

**Lösung:** Drehzahl = `inkr` Perioden / (t[k] − t[k − inkr]), also die Zeit für genau eine
Umdrehung. Die Summe aller Teilungsfehler einer Umdrehung ist null → Teilungsfehler verschwinden
vollständig, unabhängig von der Telegrammrate.

- Neuer Parameter `ROLL_REV_EDGES` (= Inkremente je Umdrehung, z.B. 100). 0 = bisheriges Verfahren.
- Liegt eine Umdrehung weiter zurück als `MAX_AVG_SPAN_MS` (sehr langsame Rolle), auf das bisherige
  Verfahren zurückfallen.
- Wirkt wie ein gleitender Mittelwert über eine Umdrehung (bei 700 1/min: 86 ms). In SimpleDyno/LabVIEW
  kann MA dann kleiner werden.
- **Test:** Host-Test mit künstlichem Teilungsfehler (z.B. ±2 % je Strich): Ergebnis muss konstant sein.

## 2.2 Zündung: Plausibilitätsfilter mit Periodenverfolgung

Die jetzige Sperrzeit ist relativ zur größeren der beiden letzten Perioden. Robuster ist ein
**Erwartungsfenster** wie in einer ECU:

```
P̂  = geglättete Periode der gültigen Impulse (z.B. gleitend über 4 Perioden)
Impuls nach dt:
  dt < a · P̂           → verwerfen (Störimpuls)            a = 0,70 (einstellbar)
  a·P̂ ≤ dt ≤ b · P̂     → gültig, P̂ nachführen             b = 1,50
  dt > b · P̂           → gültig, aber "Lücke" (Aussetzer/Verzögerung) zählen
3× hintereinander außerhalb → neu einrasten (P̂ = dt)
Pause > Timeout        → neu einrasten
```

- Zählt je Telegramm: gültig, verworfen, Lücken → Diagnose-Kanäle.
- Einstellbar zur Laufzeit (Schritt 3), bis dahin über `config.h`.
- **Test:** Host-Tests mit (a) Störimpuls bei 39 %, (b) Aussetzern, (c) Beschleunigung 3000→12000 in 3 s,
  (d) ungleichmäßigem Zweizylinder – dafür Modus „Impulse je Umdrehung = 2, ungleichmäßig“ vorsehen.

## 2.3 Diagnosemodus „Zündsignal ansehen“ (ohne neue Hardware)

PA0 ist gleichzeitig TIM2_CH1 und **ADC1_IN0**; TIM2 kann auf **beide Flanken** auslösen.

- **Flankenmodus:** auf Befehl beide Flanken stempeln und die Rohzeitstempel (steigend/fallend) für
  z.B. 1 s an den PC schicken → Impulsbreiten, Abstände, Störimpulse sichtbar (Logikanalysator).
- **Abtastmodus:** ADC1 mit DMA auf PA0, ~1 MS/s, 20 ms Fenster, Start auf Flanke → Signal nach dem
  Optokoppler als Kurve (zeigt Prellen, Flankensteilheit, Störimpulse). Das analoge Zündsignal vor der
  Aufbereitung braucht dafür einen eigenen Eingang (Schritt 4).
- In SimpleDyno: Reiter „Signal“ mit Anzeige, Speichern als CSV.

## 2.4 Laufzeit-Einstellungen statt Neu-Flashen

Parameter (Messrate, Flanke, Filter, Sperrzeiten, Autostopp, EGT-Kanäle, AFR-Quelle, Geber-Inkremente)
im Flash ablegen (letzte Flash-Seite / EEPROM-Emulation) und per Befehl lesen/setzen. Im Mega-Protokoll
als Zusatzbefehle, die LabVIEW nie sendet; sauber erst mit Protokoll v2.

## 2.5 Kleinere Punkte

- MAX6675-Leseabstand und -Ergebnis als Diagnose melden (offen / keine Antwort).
- Zähler für verlorene Telegramme (Queue voll), UART-Überläufe → in `v` und später als Kanal.
- `ASCII_FREQ_DECIMALS`, Messrate zur Laufzeit.

## Reihenfolge

1. 2.2 Plausibilitätsfilter (behebt das akute Problem dauerhaft) + Host-Tests
2. 2.1 Umdrehungsmittelung Rolle + Host-Test
3. 2.3 Flankenmodus (Diagnose), danach Abtastmodus
4. 2.4 Laufzeit-Einstellungen (zusammen mit Protokoll v2)
