# PST-STM32 – Messelektronik für PyST

Neue Firmware für die STM32-Messelektronik (Nucleo-F446RE + PST-Aufsteckplatine).
Sie spricht das **Protokoll des Arduino-Mega-Sketches** und läuft deshalb mit der
aktuellen LabVIEW-Version **3.2.1** des Ursprungsprojekts ohne Änderung an LabVIEW.
Die private STM-LabVIEW 0.2.0 (Binärprotokoll) wird automatisch erkannt und ebenfalls bedient.

Ab **1.1.0** zusätzlich der Oszi-/Diagnosemodus **PST-Diag 1** (Diagnose-Telegramm, Flanken-Oszi
mit Trigger, Einstellungen ohne Neu-Flashen) – Spezifikation: [docs/04_PST_Diag_1.md](../../docs/04_PST_Diag_1.md),
Kurzfassung unten. Nach jedem Start gilt der Normalmodus, die Ausgabe ist byte-identisch zu 1.0.0.

Das Lambda-Board (CJ125) ist in dieser Version bewusst nicht enthalten. Die Heizungsausgänge
werden fest auf AUS gelegt, damit ein eventuell noch gestecktes Board sicher ist.

## Was sich gegenüber der alten STM-Firmware (byteCOM_heater, 01/2022) ändert

| Thema | alt | neu |
|---|---|---|
| Rollen-/Zünddrehzahl | Mittelwertbug: zu hoch (z.B. 700 Hz → ~900 Hz), ohne Signal 1 Hz | exakte Periodenmessung (Impulse ÷ Zeit, 11 ns Auflösung), fällt beim Ausrollen stetig auf 0 |
| Leerlauf | Pufferüberlauf überschreibt RAM | Ringpuffer, kein Überlauf |
| Messfrequenz | per Software-Timer, ±0,2 % | exakt, gleicher Takt wie die Impulsmessung |
| Störungen | kein Filter | Hardware-Eingangsfilter, Sperrzeit, Doppelimpulsfilter, Watchdog |
| Klimasensor | BMP280 fest 0x76, blockiert 100 ms | BME280/BMP280 automatisch, 0x76/0x77, liefert auch Feuchte |
| EGT | MAX6675 1 Kanal, 0,25-°C-Auflösung verworfen | 2 Kanäle, 0,25 °C, Erkennung offenes Thermoelement |
| Protokoll | nur STM-LabVIEW | Arduino-Protokoll (LabVIEW 3.x) **und** STM-LabVIEW |
| Code | eine 1900-Zeilen-`main.c` | Module mit Host-Tests |

## Fertige Firmware

`bin/PST-STM32_v1.1.0.bin` (Standard) und `bin/PST-STM32_v1.1.0_dp70.bin` (Standardwert
Doppelimpulsfilter 70 %, nur für Nutzer ohne PyST/Terminal – sonst `#SET ign_lockout_pct 70` + `#SAVE`),
Prüfsummen in `bin/SHA256SUMS.txt`. Die Dateien von 1.0.0 bleiben zum Zurückgehen liegen.
Flashen: Datei auf das Laufwerk **NODE_F446RE** ziehen (Mac, Linux, Windows) – siehe
[../../INSTALL.md](../../INSTALL.md).

Gespeicherte Einstellungen liegen im letzten Flash-Sektor (Sektor 7, 0x08060000). Drag&Drop und
`st-flash write` löschen normalerweise nur die Sektoren, die die Firmware belegt, die Einstellungen
bleiben dann erhalten; ein Voll-Löschen (`st-flash erase`, STM32CubeProgrammer „Full chip erase“)
setzt alles auf die Standardwerte zurück. Nach dem Flashen mit `#GET` prüfen.

## Bauen und Flashen auf dem Mac (auch Linux/Windows)

Benötigt wird nur [PlatformIO](https://platformio.org) (Kommandozeile oder VS-Code-Erweiterung).

> Hinweis: `platformio.ini` setzt `-DHSE_VALUE=8000000U`. Ohne diese Option verwendet das
> STM32Cube-Framework 25 MHz, die serielle Schnittstelle läuft dann mit falscher Baudrate und alle
> Frequenzen sind um den Faktor 3,125 falsch.

```bash
cd PST-STM32-Firmware
pio run                 # bauen
pio run -t upload       # flashen: kopiert firmware.bin auf das Laufwerk NODE_F446RE
```

Alternativ ohne PlatformIO-Upload:
- `.pio/build/nucleo_f446re/firmware.bin` im Finder auf das Laufwerk **NODE_F446RE** ziehen, oder
- `st-flash --connect-under-reset write .pio/build/nucleo_f446re/firmware.bin 0x08000000`
  (`brew install stlink`). `--connect-under-reset` ist nötig, weil PA13 (SWDIO) als CS für EGT1 dient.

## Testen ohne LabVIEW

```bash
python3 -m pip install pyserial
python3 tools/pst_monitor.py                         # sucht den ST-LINK-Port selbst
python3 tools/pst_monitor.py --imp 1 --inkr 41 --umfang 1.57
```

Zeigt Info, Klimadaten und live Motor-/Rollendrehzahl, Geschwindigkeit, EGT und AFR.
Alternativ im seriellen Monitor (115200 Baud) wie beim Mega `v`, `e`, `m` senden, für PST-Diag 1
z.B. `#?`, `#GET`, `#MODE D` und `#RUN 1` (Zeilenende „Newline“ einstellen).

Host-Tests der Firmware-Logik (ohne Hardware, C-Compiler + Python 3):

```bash
sh test/run_host_tests.sh
```

- `test/test_freq_calc.c` – Frequenzberechnung, Doppelimpulsfilter, Diagnose-Fenster, Erwartungsfenster
- `test/test_protocol.c` + `check_protocol.py` – Normalmodus und STM-LabVIEW wie LabVIEW es auswertet
- `test/test_diag.c` – PST-Diag 1: #-Parser gemischt mit Einzelzeichen/8-Byte-Befehlen,
  D-Telegramm, Einstellungen und Ablage (CRC/Version/leerer Flash), Burst-Trigger

## Anschlussbelegung (unverändert zur PST-Platine)

| Signal | Pin | |
|---|---|---|
| Zündung | PA0 | TIM2_CH1, steigende Flanke |
| Rollengeber | PB9 | TIM2_CH2, steigende Flanke |
| PC-Verbindung | PA2/PA3 | USART2 = ST-LINK-USB, 115200 8N1 |
| BME280/BMP280 | PB8 SCL, PB7 SDA | I2C1 |
| MAX6675 EGT1 / EGT2 | CS PA13 / PC6, SCK PB3, MISO PB4 | SPI1 |
| AFR analog (optional) | PB0 / PA1 | max. 3,3 V |
| Heizung Lambda 1/2 | PA11 / PC7 | fest AUS |
| LED LD2 | PA5 | blinkt langsam = bereit, schnell = Messung |

## Protokoll (Arduino-kompatibel, LabVIEW 3.x)

Befehle (ein Zeichen, `\n` optional):

| Befehl | Antwort |
|---|---|
| `e` | `T;P;H\r\n` – °C (1 Nachkommastelle), Pa (0), % (1). Ohne Sensor: `20.0;101325;0.0` (DIN-Normbedingungen, Korrekturfaktor 1,000) |
| `m` | Messung starten |
| `s` | Messung stoppen (neu) |
| `v` | Version, Takt, Filter, erkannte Sensoren, Impulszähler |

Telegramm, 60 Hz: `Zyklus;Messfrequenz;f_Zünd;f_Rolle;EGT1;AFR;EGT2\n`,
z.B. `123;60.00;50.00;725.50;656;0.00;612`.

LabVIEW 3.2.1 liest Feld 1–4 und bei „AFR+EGT“ zusätzlich Feld 5 (Temperatur) und 6 (AFR);
Feld 7 wird ignoriert. Frequenzen haben 2 Nachkommastellen. Auf einem deutsch eingestellten
Windows liest LabVIEW nur bis zum Punkt, also ganze Hz wie beim Mega.

### Wichtiger Unterschied zum Mega: kein Reset beim Öffnen des Ports

LabVIEW sendet `m` nur **zum Start** eines Laufs und nie zum Stoppen. Beim Mega klappt das, weil
er beim Öffnen des COM-Ports über DTR neu startet. Der ST-LINK des Nucleo hat kein DTR, der
Controller läuft also weiter. Deshalb:

- `m` startet immer (kein Umschalten). Läuft die Messung schon, läuft sie weiter und der Zyklus zählt durch.
- `e` (Klimadaten vor dem Lauf) stoppt eine noch laufende Ausgabe.
- **Autostopp:** Waren nach Messbeginn Zündung und Rolle 5 s ohne Signal, endet die Ausgabe –
  so wie beim Mega nach dem Reset. Einstellbar mit `#SET autostop_s <s>` (Standard: `AUTOSTOP_S` in `include/config.h`).

Folge im Tachomodus: Steht der Motor länger als 5 s, muss man in LabVIEW neu starten.

Grenzfall: Lief der Motor zwischen zwei Läufen durchgehend und ist „Messung Klimadaten“ aktiv,
können beim Start des nächsten Laufs noch Telegramme im Empfangspuffer vor der Klima-Antwort
stehen. LabVIEW würde dann falsche Klimawerte lesen. Abhilfe:
- nach dem Lauf den Motor kurz abstellen (5 s), oder
- Klimadaten manuell eintragen, oder
- einen USB-Seriell-Adapter mit DTR→100 nF→NRST verwenden (Reset wie beim Mega).

Die neue PC-Software wird das sauber lösen.

## Protokoll der STM-LabVIEW 0.2.0 (automatisch erkannt)

8-Byte-Befehle `tttttttt` → `t_ok`, `eeeeeeee` → Klimadaten, `lllllllX` → `l1_0` (keine Heizung),
`mRabcdef` → Start mit Rate R (0–5 = 20/33/50/67/100/133 Hz), EGT1 a, EGT2 b, AFR aus ADC1 e / ADC2 f
(c, d = CJ125, derzeit ohne Funktion). Telegramm: 20 Byte, 10 × uint16 little endian:
Zyklus, Messfrequenz×256, Zündung Hz×8, Rolle Hz×8, EGT1 ×16, EGT2 ×16, AFR1 ×1024, AFR2 ×1024, ADC1 ×16, ADC2 ×16.

## PST-Diag 1 – Oszi-/Diagnosemodus (ab 1.1.0)

Neue Befehle sind Zeilen: `#` … `\n` (auch `\r` oder `\r\n`), max. 63 Zeichen. Die Zeichen einer
`#`-Zeile werden nie als `e`/`m`/`s`/`v` ausgewertet; eine angefangene Zeile ohne Ende verfällt nach 1 s.
LabVIEW sendet nie `#`, Einzelzeichen und die 8-Byte-Befehle der STM-LabVIEW (Erkennung über die
Sendepause) laufen unverändert. Antworten beginnen mit `#`, Fehler: `#ERR;<befehl>;<grund>`,
unbekannter Befehl `#ERR;?;unbekannt`. Befehlswörter dürfen klein geschrieben werden.

| Befehl | Antwort |
|---|---|
| `#?` | `#CAP;proto=1;fw=PST-STM32 1.1.0;tick_ns=11.111;ch=I,R;burst_max=8192;modes=N,D;set=1` |
| `#MODE N` / `#MODE D` | `#OK;MODE;N` / `#OK;MODE;D` – Modus wird nie gespeichert, nach Reset immer N |
| `#RUN 1` / `#RUN 0` | `#OK;RUN;1` / `#OK;RUN;0` – Telegramme starten/stoppen (wie `m`/`s`) |
| `#RATE <hz>` | `#OK;RATE;<hz>`, 10…100 Hz, sonst `#ERR;RATE;bereich 10-100` (wie `#SET rate_hz`, flüchtig) |
| `#GET` | je Einstellung `#SET;<key>=<wert>`, dann `#OK;GET` (`#GET <key>`: nur diese) |
| `#SET <key> <wert>` | `#OK;SET;<key>=<wert>` – wirkt sofort, flüchtig |
| `#SAVE` | `#OK;SAVE` – in den Flash; während laufender Telegramme/Aufnahme `#ERR;SAVE;messung laeuft` |
| `#DEFAULTS` | `#OK;DEFAULTS` – Standardwerte (= config.h), flüchtig bis `#SAVE` |
| `#BURST <ch> <n> <edge> [T<pct>] [P<pre>]` | `#OK;BURST;ARMED;ch=…;n=…;edge=…;trig=…;pre=…` (ohne Trigger `trig=0;pre=0`) |
| `#BURST STOP` | `#OK;BURST;STOP` |

**Diagnose-Telegramm** (Modus D, gleiche Rate wie normal, Zeilenende `\r\n`):

```
D;<zyklus>;<t_ms>;<rate_hz>;<ign_hz>;<roll_hz>;<egt1>;<afr>;<egt2>;<ign_n>;<ign_pmin_us>;<ign_pmax_us>;<ign_rej>;<roll_n>;<roll_pmin_us>;<roll_pmax_us>;<roll_rej>;<flags>
D;812;123456;60.00;50.000;725.500;656;nan;612;1;20000.0;20000.0;1;12;1377.8;1380.0;0;0
```

- `ign_hz`/`roll_hz`: alle Perioden, die im Telegramm-Intervall enden, geteilt durch ihre Gesamtzeit
  (jede Periode zählt, auch bei mehreren Impulsen je Intervall). Endet keine ganze Periode im
  Intervall: letzter Wert + Flag „stale“; länger als `timeout_*_ms` keine Flanke: 0 + Flag „timeout“.
- `*_n` gültige Flanken, `*_pmin_us`/`*_pmax_us` kürzeste/längste Periode, `*_rej` vom Firmware-Filter
  (Sperrzeit, Doppelimpulsfilter, Erwartungsfenster) verworfene Flanken im Intervall.
- `egt1`/`egt2`/`afr`: `nan`, wenn Kanal aus/offen bzw. keine AFR-Quelle.
- `flags` hex: 1 Zündung Timeout · 2 Rolle Timeout · 4 Datenverlust (Telegramm-Queue, Sendepuffer
  oder verpasste Flanke) · 8 Burst aktiv · 10 Zündung stale · 20 Rolle stale · 40 Telegramme wegen
  Burst-Ausgabe ausgesetzt.
- Im Modus D gibt es keinen Autostopp (der ist nur für LabVIEW da).

**Flanken-Oszi** `#BURST`: `ch` = `I` Zündung, `R` Rolle, `A` beide; `n` = 1…8192; `edge` = `F`, `R`
oder `B` (beide → Pulsbreiten). `T<pct>`: zirkulär aufnehmen, auslösen, wenn eine Periode (gleicher
Kanal, gleiche Flankenrichtung) um mehr als pct % vom Mittel der 8 vorherigen abweicht (Doppelimpuls,
Aussetzer, Sprung); `P<pre>` Flanken davor (Standard n/2). Aufgenommen werden die Rohflanken nach
dem Hardware-Filter, also auch die, die die Software-Filter verwerfen. Danach:

```
#BURST;BEGIN;n=800;tick_ns=11.111;trig_idx=400;ch=A      (trig_idx=-1 ohne Trigger)
B;<idx>;<I|R>;<pegel nach der Flanke 0|1>;<ticks uint32>  (n Zeilen, zeitlich aufsteigend)
#BURST;END;n=800;lost=0
```

Zeitstempel: TIM2 mit 90 MHz (11,111 ns), läuft nach 47,7 s über. Liegen zwei Einträge mehr als
23 s auseinander oder wurde eine Flanke verpasst, zählt `lost`. Bei `edge=B` (oder einer anderen
Flanke als der eingestellten) läuft der Capture-Kanal während der Aufnahme auf beiden Flanken; die
normale Messung wertet weiterhin nur die eingestellte Flanke aus. Die Ausgabe dauert bei 115200 Baud
ca. 2 ms je Flanke (8192 Flanken ≈ 17 s); währenddessen werden Telegramme ausgesetzt (Zyklus zählt
weiter, Flag 40 im nächsten D-Telegramm). Bursts gehen in Modus N und D.

## Einstellungen

Die Werte in [`include/config.h`](include/config.h) sind ab 1.1.0 die **Standardwerte**. Zur Laufzeit
ändern mit `#SET <key> <wert>` (z.B. im seriellen Monitor), dauerhaft mit `#SAVE`:

| Schlüssel | Standard | Bereich | Bedeutung (config.h) |
|---|---|---|---|
| `edge_ign`, `edge_roll` | R | R/F | Flanke Zündung/Rolle (`IGN_EDGE_RISING`, `ROLL_EDGE_RISING`) |
| `pullup` | 0 | 0/1 | interne Pull-ups PA0/PB9 (`INPUT_PULLUP`) |
| `hw_filter` | 15 | 0…15 | Digitalfilter Timer, 15 ≈ 2,8 µs (`INPUT_HW_FILTER`) |
| `ign_min_period_us` | 500 | 0…100000 | Sperrzeit Zündung (`IGN_MIN_PERIOD_US`) |
| `roll_min_period_us` | 20 | 0…100000 | Sperrzeit Rolle (`ROLL_MIN_PERIOD_US`) |
| `ign_lockout_pct` | 25 | 0…95 | Doppelimpulsfilter Zündung (`IGN_REL_LOCKOUT_PCT`); 70 ersetzt die Variante dp70 |
| `roll_lockout_pct` | 0 | 0…95 | Doppelimpulsfilter Rolle (`ROLL_REL_LOCKOUT_PCT`) |
| `ign_win_lo_pct` | 0 (aus) | 0…95 | Erwartungsfenster Zündung, untere Grenze (Plan 2.2, z.B. 70) |
| `ign_win_hi_pct` | 150 | 100…1000 | Erwartungsfenster, obere Grenze („Lücke“) |
| `ign_min_periods`, `roll_min_periods` | 2, 1 | 1…64 | Mindestperioden der Mittelung |
| `max_avg_span_ms` | 250 | 10…2000 | max. Rückgriff der Mittelung |
| `timeout_ign_ms`, `timeout_roll_ms` | 500, 1000 | 10…10000 | ohne Impuls → 0 |
| `rate_hz` | 60 | 10…100 | Telegrammrate Arduino-Protokoll und Modus D (`FRAME_RATE_HZ`) |
| `autostop_s` | 5 | 0…255 | Autostopp, nur Modus N (`AUTOSTOP_S`) |
| `freq_dec` | 2 | 0…3 | Nachkommastellen im normalen Telegramm (`ASCII_FREQ_DECIMALS`) |
| `egt1`, `egt2` | 1, 1 | 0/1 | MAX6675-Kanäle (`EGT1_ENABLE`, `EGT2_ENABLE`) |
| `afr_src` | 0 | 0…2 | AFR-Quelle: 0 aus, 1 PB0, 2 PA1 (`AFR_SOURCE`) |
| `afr_0v`, `afr_fs` | 10.00, 20.00 | 0…100.00 | AFR-Kennlinie bei 0 V / 3,3 V |

Gespeicherte Werte ändern das normale Telegramm nur so, wie es die Einstellung will (Rate,
Nachkommastellen, AFR/EGT-Kanäle); das Format bleibt. Weiterhin nur in `config.h`: Sensor-Offsets,
Toggle-Verhalten von `m`, Break-Stopp.

**Erwartungsfenster** (Plan 2.2, Standard aus): P = geglättete Periode (gleitend über 4). Impuls früher
als `ign_win_lo_pct` % von P → verworfen; später als `ign_win_hi_pct` % → gültig, als Lücke gezählt;
3× hintereinander außerhalb → neu einrasten. Nur für 1-Zylinder bzw. gleichmäßige Zündfolge. Beginnt
die Messung mit einem Störimpuls, dauert das Einrasten einige Umdrehungen; zusammen mit
`ign_lockout_pct` verwendbar.

**Ablage im Flash:** Sektor 7 (128 KB ab 0x08060000). Jeder `#SAVE` schreibt einen neuen Datensatz
(256 Byte: Kennung `PSTS`, Format-Version 1, Werte, CRC32) hinter den letzten; beim Start gilt der
letzte gültige, sonst die Standardwerte. Ein Datensatz dauert < 1 ms. Erst wenn der Sektor voll ist
(nach 512 Speicherungen) oder fremde Daten enthält, wird er gelöscht: das dauert beim F446 typ. 1 s,
max. 2 s, die CPU steht so lange (der Watchdog wird dafür verlängert). Deshalb lehnt `#SAVE` während
laufender Telegramme oder einer Burst-Aufnahme ab – vorher `s` bzw. `#RUN 0`.

## Aufbau

```
include/config.h      Benutzer-Einstellungen (ab 1.1.0: Standardwerte)
src/main.c            Hauptschleife
src/capture.c         TIM2: Impulserfassung + Telegrammtakt (Interrupt), Burst-Flanken
src/freq_calc.c       Frequenzberechnung, Diagnose-Fenster, Filter (hardwareunabhängig, getestet)
src/protocol.c        Befehle und Telegramme (Arduino + STM-LabVIEW), #-Zeilen-Erkennung
src/diag.c            PST-Diag 1: #-Befehle, D-Telegramm, Burst-Ausgabe (getestet)
src/burst.c           Flanken-Oszi mit Trigger (hardwareunabhängig, getestet)
src/settings.c        Laufzeit-Einstellungen, Datensatz mit CRC (hardwareunabhängig, getestet)
src/settings_store.c  Flash-Ablage der Einstellungen (Sektor 7)
src/serial.c          USART2 mit Ringpuffern
src/env_sensor.c      BME280/BMP280
src/egt.c             MAX6675 ×2
src/analog.c          ADC für externes AFR-Signal
src/board.c           Takt, sichere Pin-Zustände, Watchdog
tools/pst_monitor.py  Testprogramm für den PC
test/                 Host-Tests
```
