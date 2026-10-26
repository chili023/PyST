# PyST – Rollenprüfstand: PC-Software & STM32-Messelektronik

Open-Source-Leistungsprüfstand (Rollenprüfstand):

- **PyST** – PC-Software in Python/Qt für **Mac, Linux (Raspberry Pi) und Windows**.
  Rechnet wie LabVIEW 3.2.1 (an echten Läufen geprüft: gleiche Pmax und n(Pmax)), mit Datenbank für
  Fahrzeuge/Setups/Läufe, Auswerter im Stil von MegaLogViewer, PDF/CSV-Export, Import von LabVIEW-Läufen.
- **PST-STM32-Firmware** – neue Firmware für die Messelektronik auf **STM32F446RE (Nucleo-64)**.
  Spricht das Protokoll des Arduino-Mega-Sketches → läuft mit **LabVIEW 3.2.1**, mit der
  **STM-LabVIEW 0.2.0** (automatisch erkannt) und mit **PyST**.
- **Oszi-/Diagnosemodus [PST-Diag 1](docs/04_PST_Diag_1.md)** (Mega 3.1.0, PST-STM32 1.1.0, PyST 1.3):
  Dauermessung, Linienschreiber, Ereignis-Trigger, Flanken-Ansicht und Geräte-Einstellungen – per Befehl
  umschaltbar, ohne Neu-Flashen; nach jedem Neustart wieder LabVIEW-kompatibel.
- **Läufe weitergeben**: als `.pyst`-Datei oder als einzelne HTML-Datei, die offline in jedem Browser läuft.
- **Arduino-Mega-Sketch 3.1.0** – der bisherige Mega-Aufbau läuft ebenfalls mit PyST; 3.1.0 misst jede
  Periode, meldet 0 Hz nach Signalausfall und hat den Oszi-/Diagnosemodus [PST-Diag 1](docs/04_PST_Diag_1.md).

![PyST – Messen](docs/bilder/pyst_messen.png)

## Was läuft womit

| Messelektronik | LabVIEW 3.2.1 | STM-LabVIEW 0.2.0 | PyST |
|---|---|---|---|
| STM32 + PST-STM32-Firmware 1.0 | ✅ am Prüfstand | ✅ Protokoll geprüft¹ | ✅ am Prüfstand |
| STM32 + PST-STM32-Firmware 1.1 | ✅ Format byte-identisch⁴ | ✅ Format byte-identisch⁴ | ✅ + PST-Diag 1 (ab PyST 1.3) |
| Arduino Mega + Sketch 3.0.0 | ✅ (Original) | Arduino-Modus² | ✅ im Simulator geprüft³ |
| Arduino Mega + Sketch 3.1.0 | ✅ Format byte-identisch⁴ | Arduino-Modus² | ✅ + PST-Diag 1 (ab PyST 1.3) |

¹ 8-Byte-Befehle und 20-Byte-Binärtelegramm per Host-Test gegen die aus LabVIEW rekonstruierte Auswertung.
² Umschalter „Arduino<>STM“ der STM-LabVIEW, nicht getestet.
³ `SIM:mega` bildet den Sketch nach (Umschalten mit `m`, ganze Hz, 61 Hz); am echten Mega noch zu bestätigen.
⁴ Normales Telegramm per Host-Test geprüft (F446: gegen 1.0.0; Mega: zusätzlich im AVR-Simulator gegen
  Arduino `Serial.print`); am echten Gerät mit LabVIEW noch zu bestätigen.

## Download

Diese Version (Beta, noch nicht an allen Geräten geprüft) liegt im Branch **`release/1.3-beta`**:
auf GitHub den Branch `release/1.3-beta` wählen → **Code → Download ZIP**, oder

```bash
git clone -b release/1.3-beta https://github.com/chili023/PyST.git
```

## Schnellstart

→ Ausführlich: **[INSTALL.md](INSTALL.md)**

```bash
# PyST (Mac/Linux) – richtet sich beim ersten Start selbst ein
cd PyST
./start_mac.command          # Mac   (Linux: ./start_linux.sh, Windows: start_windows.bat)
./start_mac.command --sim    # ohne Hardware: Simulator spielt einen echten Lauf ab
```

STM32 flashen: `firmware/stm32/bin/PST-STM32_v1.1.0.bin` (bisher `PST-STM32_v1.0.0.bin`) auf das Laufwerk **NODE_F446RE** ziehen.

## Inhalt

```
PyST/            PC-Software (Python 3.9+, PySide6, pyqtgraph)
firmware/stm32/        Firmware STM32F446RE (PlatformIO), fertige .bin in firmware/stm32/bin/
firmware/arduino-mega/ Arduino-Mega-Sketch 3.1.0 (PlatformIO/Arduino IDE), 3.0.0 + Bibliotheken
docs/                  Roadmap, Zündsignal-Diagnose, Firmware-Messung, Protokoll v2, PST-Diag 1
```

## Dokumentation

- [INSTALL.md](INSTALL.md) – Installation Mac / Linux / Windows, Flashen STM32 und Mega
- [PyST/README.md](PyST/README.md) – Bedienung, Rechenweg, Kommandozeile
- [firmware/stm32/README.md](firmware/stm32/README.md) – Firmware, Protokoll, Anschlussbelegung, Einstellungen
- [PyST/TESTPLAN.md](PyST/TESTPLAN.md) – Vergleichstest alt/neu
- [docs/04_PST_Diag_1.md](docs/04_PST_Diag_1.md) – Oszi-/Diagnosemodus PST-Diag 1 (Befehle, Telegramme, Burst)
- [docs/ROADMAP.md](docs/ROADMAP.md) – Weiterentwicklung (Protokoll v2, rusEFI, Wirbelstrombremse)

## Wichtige Hinweise

- **Filter:** LabVIEW zählt Messpunkte (Mega ≈ 61 Hz, STM32 60 Hz, alte STM-Firmware 20 Hz) – MA/dq 35/15
  bei 20 Hz entsprechen bei 60 Hz 105/45. PyST stellt die Filter standardmäßig in **Sekunden** ein
  (1,75 s / 0,75 s = LabVIEW 35/15 bei 20 Hz) und rechnet sie für jede Messfrequenz um; der Modus
  „Messpunkte (wie LabVIEW)“ bleibt wählbar.
- **„n vom Gas“** ist die Schwelle, ab der ein Laufende (fallende Drehzahl) erkannt wird – sie muss über
  der Haltedrehzahl liegen.
- PyST steuert den Motor nicht. „n Stop“ beendet nur die Aufzeichnung und zeigt **GAS WEG**.

## Herkunft und Lizenz

PyST ist aus dem Open-Source-Projekt [WildBugChilGru](https://github.com/gruaGit/WildBugChilGru)
(Arduino Mega + LabVIEW) hervorgegangen; Rechenweg und Mega-Protokoll sind daraus übernommen.

GNU General Public License v3 – siehe [LICENSE](LICENSE).
