# Testplan: alte vs. neue Firmware vs. PyST

Ziel: Mit demselben Fahrzeug, am selben Tag, drei Konfigurationen messen und vergleichen.

| Test | Firmware (STM32) | PC-Software | Rechner |
|---|---|---|---|
| **A** | alte Firmware (die jetzt auf dem Nucleo ist) | alte LabVIEW (STM 0.2.0) | Windows |
| **B1** | neue PST-STM32 1.0 | LabVIEW 3.2.1 von GitHub | Windows |
| **B2** | neue PST-STM32 1.0 | alte LabVIEW (STM 0.2.0) | Windows |
| **C** | neue PST-STM32 1.0 | PyST | Mac (oder Pi) |

B2 ist optional, zeigt aber, ob die neue Firmware auch mit der alten LabVIEW sauber läuft
(sie erkennt deren 8-Byte-Befehle automatisch).

---

## 0. Vorbereitung (einmalig, am Mac)

**Aktuelle Firmware sichern**, bevor irgendetwas geflasht wird. Damit ist Test A jederzeit
wiederholbar, auch wenn die jetzige Firmware nicht exakt `220130.hex` entspricht:

```bash
brew install stlink          # falls st-flash fehlt (ist bei dir schon installiert)
st-flash --connect-under-reset read ~/pst_firmware_backup_alt.bin 0x08000000 0x80000
```

Zurückspielen später mit:
`st-flash --connect-under-reset write ~/pst_firmware_backup_alt.bin 0x08000000`

**PyST testen ohne Prüfstand:** `./start_mac.command --sim` → Verbinden → START.
Ein simulierter Lauf dauert ca. 15 s.

**Einstellungen, in allen Tests gleich** (aus euren Läufen vom 26.05.26):

| Parameter | Wert |
|---|---|
| Rollengeber | 100 Inkr/U |
| Rollenumfang | 1,57 m |
| Rollenträgheit J | 13,5 kgm² |
| Zündung | 1 Imp/U |
| Übersetzung | **fest** 6,85 (bzw. vorher einmal messen und dann überall gleich eintragen) |
| Gleitender Mittelwert / Differenzenquotient | **bei 20 Hz: 35 / 15** (alte Firmware, B2). **Bei 60 Hz: 105 / 45** (B1 LabVIEW 3.2.1, C PyST) – die Filter zählen Messpunkte, nicht Sekunden! Mit 35/15 bei 60 Hz wird die Kurve wellig und Pmax zu hoch (Simulator: 35,0 statt 32,4 PS). |
| n vom Gas | **über der Haltedrehzahl**, z.B. Haltedrehzahl ~3000 → n vom Gas 5000. (In den Mai-Läufen wurde bei 5000–6500 gehalten, deshalb stand dort 8000.) |
| Verlustmoment | 0 / 0 / 1 |
| Gas weg | bei **8000 1/min** in allen Tests (PyST zeigt „GAS WEG!“, in LabVIEW auf den Drehzahlmesser achten) |

> Mit Gas weg bei 8000 endet die Kurve **vor Pmax** (bei euch ~10000 1/min). Für den Vergleich
> ist das egal, weil alle Läufe mit derselben Grenze nachgerechnet werden (`--n-stop 8000`).
> Wer Pmax sehen will: n Stop auf 0 (aus) bzw. 11500 und wie gewohnt Gas wegnehmen.
>
> PyST steuert den Motor nicht. „n Stop“ beendet nur die Aufzeichnung und zeigt
> GAS WEG an. Gas wegnehmen muss immer der Fahrer.

---

## Ablauf je Test (3 Läufe pro Konfiguration)

1. Motor warmfahren, gleicher Gang, gleiche Reifentemperatur wie bei den anderen Tests.
2. Klimadaten: automatisch (Sensor) – in allen Tests gleich lassen.
3. Pro Lauf: Drehzahl ~3000 halten (unter n vom Gas!) → START → bei GO Vollgas → bei 8000 Gas weg.
4. **Auto-Speichern an** (LabVIEW legt `JJMMTT_HHMMSS_PS_n.xml` ab, PyST einen Ordner
   in `~/PyST/messungen/`).
5. Notieren: Pmax, n(Pmax), COM-Fehlerrate, Auffälligkeiten (Aussetzer, springende Drehzahl).

### Test A – alt/alt
Nichts flashen. Alte LabVIEW wie gewohnt. Läufe speichern.

### Test B1 / B2 – neue Firmware
Flashen am Mac (Nucleo per USB):

```bash
cd PST-STM32-Firmware
~/.platformio/penv/bin/pio run -t upload
```

oder `.pio/build/nucleo_f446re/firmware.bin` auf das Laufwerk **NODE_F446RE** ziehen.
Kontrolle: `python3 tools/pst_monitor.py` → muss „PST-STM32 Version 1.0.0“ und Klimadaten zeigen.

Dann Nucleo an den Windows-Rechner, LabVIEW starten, COM-Port wählen, messen.
- B1: LabVIEW 3.2.1 (GitHub, Arduino-Protokoll)
- B2: alte STM-LabVIEW – in „Communication“ auf STM stellen wie bisher

Achtung LabVIEW + neue Firmware: Nach dem Lauf den Motor kurz (5 s) abstellen oder Klima
manuell eintragen – siehe Firmware-README („kein Reset beim Öffnen des Ports“).

### Test C – PyST am Mac
`./start_mac.command` → Port `/dev/cu.usbmodem…` → Verbinden → Einstellungen wie oben → START (F1).
Nach dem Lauf wird automatisch gespeichert (Rohdaten, Kurve, PNG und eine LabVIEW-kompatible `lauf.xml`).

---

## Auswertung

Alle Dateien (LabVIEW-XML aus A/B und PyST-Ordner aus C) auf den Mac kopieren:

**Tabelle, alle Läufe mit identischen Parametern nachgerechnet:**
```bash
./.venv/bin/python -m pyst compare ~/Messungen/A/*.xml ~/Messungen/B1/*.xml ~/PyST/messungen/*/lauf.json --n-min 5000 --n-stop 8000
```

**Jeder Lauf mit seinen eigenen Parametern (prüft: rechnet PyST wie LabVIEW?):**
```bash
./.venv/bin/python -m pyst recalc ~/Messungen/A/*.xml
```
Die Spalte „Referenz(Name)“ zeigt, was LabVIEW selbst ausgerechnet hat. Bei den sechs Läufen vom
26.05.26 stimmt PyST auf 0,1 PS und 1 1/min überein.

**Grafisch:** In PyST „Läufe laden / vergleichen …“, mehrere Dateien wählen,
Haken „mit aktuellen Parametern rechnen“ für einen fairen Vergleich.

### Was wir erwarten / worauf achten

| Beobachtung | Bedeutung |
|---|---|
| Test A: gemessene Motordrehzahl springt stark (z.B. 4695 ↔ 17000 1/min) | bekannter Mittelwertfehler der alten Firmware (in euren Mai-Läufen sichtbar) |
| Test A vs. B: Pmax-Unterschied | Frequenzfehler der alten Firmware wirkt auf die Rollendrehzahl – Größe wollen wir messen |
| B vs. C bei gleicher Firmware: Pmax gleich (± Streuung zwischen Läufen) | PyST rechnet wie LabVIEW |
| Messfrequenz | A: 20–133 Hz (Einstellung STM-LabVIEW), B1/C: 60,00 Hz |
| COM verloren / Fehlerrate | sollte bei neuer Firmware 0 sein |
| Kurvenrauschen | neue Firmware sollte glatter sein (exakte Periodenmessung) |
| Zündung ÷ (Rolle × i) | alte Firmware: Bänder bei ~1,0 / ~1,8 / ~2,7 statt 1,0. Auswertung aller 6 Mai-Läufe: **2. Zündimpuls pro Umdrehung bei ~142°** (Signal/Hardware, nicht Software). In PyST: Reiter Auswertung → Kanal „Übersetzung Zündung/Rolle“. |

### Zündsignal prüfen (Test B)

1. Neue Firmware Standard (`pio run -t upload`, Doppelimpulsfilter 25 %) flashen und bei konstanter Drehzahl
   „Motor (Zündung)“ mit „Motor (Rolle × i)“ vergleichen. Erwartung mit eurem Signal: Zündung ~1,6–2× zu hoch.
2. Variante mit 70-%-Filter flashen: `~/.platformio/penv/bin/pio run -e nucleo_f446re_dp70 -t upload`.
   Jetzt muss „Motor (Zündung)“ = „Motor (Rolle × i)“ sein (bei CVT bis auf die Vario-Übersetzung).
   `v`-Info zeigt „Doppelimpulsfilter [%] 70“.
3. Besser noch an der Ursache ansetzen: Oszilloskop an PA0 / Optokoppler-Ausgang – der Störimpuls sitzt
   ~142° nach dem Zündimpuls (Geber-Flanke, CDI-Ladeimpuls oder Einkopplung des Funkens).

Streuung zwischen drei Läufen derselben Konfiguration zuerst anschauen – nur Unterschiede,
die deutlich größer sind, sind echte Unterschiede zwischen den Konfigurationen.
