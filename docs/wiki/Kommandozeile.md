# Kommandozeile

PyST lässt sich auch ohne Oberfläche benutzen: Läufe messen, nachrechnen und vergleichen. Das ist vor allem für
Tests und den Vergleich mit LabVIEW gedacht.

Aufruf im Ordner `PyST` mit der Python-Umgebung des Startskripts, z. B. `./.venv/bin/python -m pyst …`
(Windows: `.venv\Scripts\python -m pyst …`).

## Oberfläche starten

| Aufruf | Wirkung |
|---|---|
| `python -m pyst` oder `python -m pyst gui` | Oberfläche |
| `python -m pyst --sim` | Oberfläche mit Simulator (Port `SIM:161802`, echter Lauf), verbindet sofort |
| `python -m pyst --sim --synth` | Simulator mit synthetischem Motor (Port `SIM`) |
| `python -m pyst --replay datei.xml` | Lauf-Datei im Simulator abspielen |
| `python -m pyst datei.pyst …` | Dateien öffnen und in der Auswertung zeigen |

Die Startskripte geben ihre Argumente weiter: `./start_linux.sh --sim`, `./start_linux.sh lauf.pyst`,
`start_mac.command --sim`, `start_windows.bat --sim`. Dateinamen gelten relativ zum Ordner, in dem man das Skript
aufruft.

## Befehle

| Befehl | Wirkung |
|---|---|
| `python -m pyst ports` | serielle Ports anzeigen |
| `python -m pyst run --port SIM` | Lauf ohne Oberfläche (SIM = Simulator) |
| `python -m pyst run --port SIM:datei.xml` | gespeicherten Lauf (LabVIEW-XML) als Live-Lauf abspielen |
| `python -m pyst recalc datei.xml …` | LabVIEW- bzw. PyST-Läufe nachrechnen |
| `python -m pyst compare a.xml b.xml …` | Läufe tabellarisch vergleichen |

### run

| Option | Bedeutung |
|---|---|
| `--port` | Port oder `SIM`. Ohne Angabe sucht PyST einen Port |
| `--no-climate` | keine Klimaabfrage (dann gleich GO, mit den eingetragenen Werten) |
| `--vehicle` | Fahrzeugdaten (Text) |
| `--dir` | Speicherordner |

Der Ablauf ist derselbe wie in der Oberfläche: `Klima wird gemessen …` (frischer Sensorwert, höchstens 3 s, sonst
gelten die eingetragenen Werte), dann `GO – Vollgas!`, und die Messung beginnt, sobald die Drehzahl `--start-anstieg-s`
lang um mindestens `--start-anstieg-min` steigt (`Laufbeginn erkannt – Messung läuft`). Kommt nach GO 60 s kein
Anstieg, bricht PyST ab.

Während des Laufs schreibt PyST alle 0,5 s eine Zeile mit Zustand, Motordrehzahl (gerechnet und gemessen), km/h,
Messfrequenz und verlorenen Telegrammen. Bei *n Stop* steht `>>> GAS WEG <<<`. Strg+C bricht ab. Am Ende kommen das
Ergebnis und der Speicherordner.

Beim Abspielen einer Datei (`SIM:<Datei>`) nimmt `run` die Prüfstandsdaten aus der Datei und setzt *n Stop* auf aus.

### recalc

Rechnet jede Datei mit ihren eigenen Parametern (oder den Überschreibungen unten) und zeigt PS, Drehzahl, Nm, Drehzahl
und den DIN-Faktor k. Steht im Dateinamen ein LabVIEW-Ergebnis (z. B. `…_32,0_10000.xml`), zeigt PyST es als
Referenz daneben. `-v` zeigt zusätzlich die Parameter.

### compare

Rechnet alle Läufe mit den Parametern des **ersten** Laufs. Klima bleibt je Lauf. Die Filter rechnet PyST dabei auf
gleiche Glättungszeit um, weil MA und dq in Messpunkten zählen (20 Hz alt, 60 Hz neu). `--same-samples` rechnet nicht
um (gleiche Anzahl Messpunkte wie LabVIEW).

## Parameter überschreiben

Für `run`, `recalc` und `compare`. Sie überschreiben die Werte aus der Datei.

| Option | Bedeutung | Standard |
|---|---|---|
| `--inkr` | Rollengeber Inkr/U | 100 |
| `--roll-circ` | Rollenumfang [m] | 1,57 |
| `--inertia` | Rollenträgheit [kgm²] | 13,5 |
| `--imp` | Zündimpulse/U | 1 |
| `--ratio` | Übersetzung nKW/nRolle | 6,85 |
| `--ma` | Gleitender Mittelwert [Messpunkte] | 35 |
| `--dq` | Differenzenquotient [Messpunkte] | 15 |
| `--start-anstieg-s` | Laufbeginn: Drehzahl steigt so lange [s] (nur `run`) | 1,0 |
| `--start-anstieg-min` | Laufbeginn: Anstieg mindestens [1/min] (nur `run`) | 150 |
| `--n-min` | n vom Gas: ab hier Laufende prüfen [1/min] | 4000 |
| `--n-stop` | n Stop [1/min], 0 = aus | 8000 |
| `--m0`, `--m1` | Verlustmoment M0, M1 [Nm] | 0 |
| `--n1` | Rollendrehzahl n1 [1/min] | 1 |
| `--temp-c` | Lufttemperatur [°C] | 20 |
| `--p-mbar` | Luftdruck [mbar] | 1013 |

Sind die Werte nicht plausibel (z. B. *n Stop* nicht über *n vom Gas*), bricht PyST mit einer Meldung ab.

## Experten

- Die Kommandozeile kennt die Filter nur in Messpunkten (`--ma`, `--dq`). Hat ein PyST-Lauf seine Filter in Sekunden
  gespeichert (Standard der Oberfläche), gelten die Sekunden und `--ma`/`--dq` wirken nicht. Auch `compare` rechnet
  Filter in Sekunden nicht um. Siehe [Rechenweg](Rechenweg.md).
- `m2` (quadratischer Verlustanteil) lässt sich hier nicht überschreiben.
- Tests: `./.venv/bin/python -m pytest tests`.
