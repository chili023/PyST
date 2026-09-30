# PST-STM32 – Messelektronik für WildBugChilGru

Neue Firmware für die STM32-Messelektronik (Nucleo-F446RE + PST-Aufsteckplatine).
Sie spricht das **Protokoll des Arduino-Mega-Sketches** und läuft deshalb mit der
aktuellen LabVIEW-Version **WildBugChilGru 3.2.1** von GitHub ohne Änderung an LabVIEW.
Die private STM-LabVIEW 0.2.0 (Binärprotokoll) wird automatisch erkannt und ebenfalls bedient.

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

`bin/PST-STM32_v1.0.0.bin` (Standard) und `bin/PST-STM32_v1.0.0_dp70.bin` (Doppelimpulsfilter 70 %),
Prüfsummen in `bin/SHA256SUMS.txt`. Flashen: Datei auf das Laufwerk **NODE_F446RE** ziehen
(Mac, Linux, Windows) – siehe [../../INSTALL.md](../../INSTALL.md).

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
Alternativ im seriellen Monitor (115200 Baud) wie beim Mega `v`, `e`, `m` senden.

Host-Tests der Firmware-Logik (ohne Hardware):

```bash
cc -std=c11 -O2 -Iinclude src/freq_calc.c test/test_freq_calc.c -lm -o /tmp/tfc && /tmp/tfc
cc -std=c11 -Iinclude -Itest/stub src/protocol.c src/fmt.c test/test_protocol.c -o /tmp/tp && python3 test/check_protocol.py /tmp/tp
```

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
  so wie beim Mega nach dem Reset. Einstellbar in `include/config.h` (`AUTOSTOP_S`).

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

## Einstellungen

Alle Einstellungen stehen in [`include/config.h`](include/config.h): Messrate, Flanken, Filter,
Sperrzeiten, Autostopp, Sensor-Offsets, EGT-Kanäle und die AFR-Kennlinie.

## Aufbau

```
include/config.h      Benutzer-Einstellungen
src/main.c            Hauptschleife
src/capture.c         TIM2: Impulserfassung + Telegrammtakt (Interrupt)
src/freq_calc.c       Frequenzberechnung (hardwareunabhängig, getestet)
src/protocol.c        Befehle und Telegramme (Arduino + STM-LabVIEW)
src/serial.c          USART2 mit Ringpuffern
src/env_sensor.c      BME280/BMP280
src/egt.c             MAX6675 ×2
src/analog.c          ADC für externes AFR-Signal
src/board.c           Takt, sichere Pin-Zustände, Watchdog
tools/pst_monitor.py  Testprogramm für den PC
test/                 Host-Tests
```
