# Installation

Die ausführliche Anleitung steht in **[INSTALL.md](https://github.com/chili023/PyST/blob/main/INSTALL.md)** im Repository.
Hier die Kurzfassung.

## Voraussetzungen

- **Python 3.9 oder neuer.**
- Beim ersten Start legt das Startskript eine eigene Python-Umgebung `PyST/.venv` an und installiert die Pakete aus
  `requirements.txt` (unter anderem PySide6, pyqtgraph, numpy, pyserial, cryptography). Dafür braucht es einmal Internet.

## Starten

| System | Start |
|---|---|
| macOS | `start_mac.command` im Ordner `PyST` (Doppelklick oder `bash start_mac.command`) |
| Linux / Raspberry Pi | `./start_linux.sh` |
| Windows 10/11 | `start_windows.bat` doppelklicken |

Ohne Hardware ausprobieren: mit `--sim` starten, siehe [Erste Schritte](Erste-Schritte.md).

## Hinweise je System

- **macOS:** Nach dem Laden als ZIP blockt Gatekeeper die Skripte. Lösung in
  [INSTALL.md](https://github.com/chili023/PyST/blob/main/INSTALL.md) (Befehl `xattr` oder „Dennoch öffnen“).
- **Linux:** `python3-venv` und `libxcb-cursor0` installieren und den Benutzer in die Gruppe `dialout` aufnehmen,
  danach ab- und wieder anmelden. Sonst darf PyST die serielle Schnittstelle nicht öffnen.
- **Windows:** Bei der Python-Installation „Add python.exe to PATH“ anhaken. Für den Nucleo ggf. den ST-LINK-Treiber,
  für Mega-Nachbauten den CH340-Treiber installieren.

## Daten

- Alles liegt im Ordner **`~/PyST`** (Windows: `C:\Users\<Name>\PyST`): Datenbank `pyst.db`, Einstellungen
  `einstellungen.json`, Messdaten in `messungen/`.
- Datenordner älterer Versionen übernimmt PyST beim ersten Start automatisch.
- Den Pfad der Datenbank zeigt **Einstellungen › Verbindung** unten.

## Update

1. Im Ordner `git pull` ausführen oder das ZIP neu laden.
2. Wie gewohnt starten. Fehlt ein neues Paket (z. B. `cryptography`), den Ordner `PyST/.venv` löschen und neu starten.
3. Datenbank, Läufe und Einstellungen in `~/PyST` bleiben erhalten. Vorher sichern schadet nicht.

PyST meldet beim Start eine neue Version im Band über den Reitern („Neue PyST-Version … verfügbar“). Installiert wird
dabei nichts.

## Experten

- Tests: `./.venv/bin/python -m pytest tests` bzw. `python -m unittest discover -s tests -v` im Ordner `PyST`.
- Die Messelektronik (Nucleo-F446RE oder Arduino Mega) flashen: [INSTALL.md](https://github.com/chili023/PyST/blob/main/INSTALL.md)
  Abschnitt 2, oder direkt aus PyST unter [Firmware und Updates](Firmware-und-Updates.md).
