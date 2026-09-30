# Installation

1. [PyST installieren](#1-pyst-installieren) – Mac, Linux/Raspberry Pi, Windows
2. [Messelektronik flashen](#2-messelektronik-flashen) – STM32 (Nucleo-F446RE) **oder** Arduino Mega
3. [Verbinden und erster Test](#3-verbinden-und-erster-test)

---

## 1. PyST installieren

Benötigt: **Python 3.9 oder neuer**. Die Startskripte legen beim ersten Start eine eigene
Python-Umgebung (`PyST/.venv`) an und installieren `pyserial`, `numpy`, `PySide6`, `pyqtgraph`
(ca. 200 MB, einmalig 1–2 Minuten, Internet nötig).

### macOS

```bash
cd PyST
./start_mac.command
```
oder im Finder doppelklicken (beim ersten Mal: Rechtsklick → Öffnen, wegen Gatekeeper).
Falls `python3` fehlt: `xcode-select --install` oder Python von python.org.
Der Nucleo erscheint als `/dev/cu.usbmodem…`, ein Mega als `/dev/cu.usbmodem…` oder `/dev/cu.usbserial…`.

### Linux / Raspberry Pi (Raspberry Pi OS 64 bit, Ubuntu, Debian)

```bash
sudo apt install python3-venv libxcb-cursor0      # libxcb-cursor0 braucht Qt 6
sudo usermod -aG dialout $USER                    # Zugriff auf die serielle Schnittstelle
# einmal ab- und wieder anmelden
cd PyST
./start_linux.sh
```
Nucleo/Mega: `/dev/ttyACM0`, Mega-Nachbau mit CH340: `/dev/ttyUSB0`.

### Windows 10/11

1. Python von <https://www.python.org/downloads/> installieren, **„Add python.exe to PATH“ anhaken**.
2. Im Ordner `PyST` **`start_windows.bat`** doppelklicken.

Serielle Treiber: Nucleo → ST-LINK-Treiber (ST **STSW-LINK009**) falls kein COM-Port erscheint;
Mega-Nachbau → CH340-Treiber. Der Port heißt `COM3`, `COM4`, …

### Ohne Hardware ausprobieren

```bash
./start_mac.command --sim          # spielt den echten Lauf 161802 (26.05.26) ab
./start_mac.command --sim --synth  # synthetischer Motor
```
In der Port-Liste stehen außerdem `SIM:mega` (verhält sich wie der Mega-Sketch) und „Lauf-Datei im
Simulator abspielen …“ (jede LabVIEW-XML).

### Tests

```bash
cd PyST
./.venv/bin/python -m unittest discover -s tests -v      # Windows: .venv\Scripts\python ...
```

---

## 2. Messelektronik flashen

### Variante A: STM32 Nucleo-F446RE mit PST-STM32-Firmware (empfohlen)

**Fertige Firmware** liegt in `firmware/stm32/bin/`:

| Datei | Wann |
|---|---|
| `PST-STM32_v1.0.0.bin` | Standard (Doppelimpulsfilter Zündung 25 %) |
| `PST-STM32_v1.0.0_dp70.bin` | Zündsignal liefert Störimpulse (z.B. 2 Impulse pro Umdrehung) – nur Einzylinder/gleichmäßige Zündfolge |

**Flashen – auf allen Systemen gleich:** Nucleo per USB anschließen, es erscheint ein Laufwerk
**NODE_F446RE**. Die `.bin`-Datei darauf kopieren/ziehen. Die LED auf dem ST-LINK blinkt, danach startet
die neue Firmware. Fertig.

Alternativen:
- **Mac/Linux:** `st-flash --connect-under-reset write PST-STM32_v1.0.0.bin 0x08000000`
  (`brew install stlink` bzw. `sudo apt install stlink-tools`)
- **Windows:** STM32CubeProgrammer, Verbindung „Under Reset“, Adresse 0x08000000
- **Selbst bauen** (PlatformIO, alle Systeme): `cd firmware/stm32 && pio run -t upload`
  (Variante: `pio run -e nucleo_f446re_dp70 -t upload`)

> Vorher die alte Firmware sichern:
> `st-flash --connect-under-reset read alt.bin 0x08000000 0x80000`.
> `--connect-under-reset` ist nötig, weil PA13 (SWDIO) als CS für EGT1 genutzt wird.

Prüfen: `python3 firmware/stm32/tools/pst_monitor.py` (braucht `pip install pyserial`) oder in
PyST verbinden → unter „Verbindung“ steht „PST-STM32 Version 1.0.0“.

Einstellungen (Messrate, Filter, EGT, AFR-Eingang …): `firmware/stm32/include/config.h`, dann neu bauen.
Details und Anschlussbelegung: [firmware/stm32/README.md](firmware/stm32/README.md).

### Variante B: Arduino Mega 2560 mit Sketch 3.0.0

1. [Arduino IDE 2](https://www.arduino.cc/en/software) installieren (Mac/Linux/Windows).
2. Bibliotheken: *Sketch → Bibliothek einbinden → .ZIP-Bibliothek hinzufügen* – alle drei aus
   `firmware/arduino-mega/libraries/` (SparkFun BME280, Adafruit MAX31855, Adafruit BusIO).
   Alternativ über den Bibliotheksverwalter mit denselben Namen.
3. `firmware/arduino-mega/240328_PST_V3.0.0/240328_PST_V3.0.0.ino` öffnen.
4. Oben im Sketch anpassen: `BMEADDRESS` (0x76 oder 0x77 je nach Platine), `thermo = true`, wenn ein
   MAX31855-Thermoelement angeschlossen ist, `RINGSIZE5` passend zum Rollengeber.
5. *Werkzeuge → Board: „Arduino Mega or Mega 2560“*, Port wählen, **Hochladen**.
6. Test im seriellen Monitor (115200 Baud, „Neue Zeile“): `v` → Version, `e` → Klimadaten, `m` → Messung an/aus.

PyST erkennt den Mega automatisch (er antwortet mit „Version 3.0.0“) und berücksichtigt, dass `m`
beim Mega die Messung umschaltet. Der Mega startet beim Öffnen des Ports neu (DTR) – PyST wartet
dafür 2 s.

---

## 3. Verbinden und erster Test

1. PyST starten → Reiter **Einstellungen** → Port wählen → **Verbinden**.
2. Prüfstandsdaten eintragen: Rollengeber (Inkr/U), Rollenumfang, Trägheit J, Zündimpulse/U,
   Übersetzung, n vom Gas (über Haltedrehzahl). Filter in Sekunden passen sich der Messfrequenz selbst an.
3. Reiter **Messen**: Drehzahl halten → **START (F1)** → bei „GO“ Vollgas → Gas weg.
4. Läufe landen in `~/PyST/` (Windows: `C:\Users\<Name>\PyST\`), Datenbank `pyst.db`.
   Gibt es von der Vorversion noch `~/SimpleDyno/` (mit `simpledyno.db`), wird dieser Ordner weiter benutzt.

Kleiner Bildschirm? **Ansicht → Größe** (Automatisch/100/90/80/70 %), wirkt nach Neustart.

LabVIEW weiter nutzen: LabVIEW 3.2.1 von
<https://github.com/gruaGit/WildBugChilGru/releases/tag/v3.2.1> (nur Windows; LabVIEW Runtime 2014 SP1
und NI-VISA nötig). Mit der STM32-Firmware bei 60 Hz die Filter auf ca. MA 105 / dq 45 stellen.
