# PST – Roadmap

Stand: 29.09.2026. Ziel: Prüfstand unabhängig von Windows (Mac, Linux/Raspberry Pi), mehr Sensoren,
rusEFI-Anbindung und später eine aktiv geregelte Wirbelstrombremse (WSB).

| Schritt | Inhalt | Wo | Status |
|---|---|---|---|
| 1 | [Vergleichstest und Zündsignal-Diagnose](01_Test_und_Zuendsignal.md) | Prüfstand | vorbereitet |
| 2 | [Messverfahren in der Firmware verbessern (jetzige Hardware)](02_Firmware_Messung.md) | `PST-STM32-Firmware` | geplant |
| 3 | [Binärprotokoll v2 mit Kanalbeschreibung](03_Protokoll_v2.md) | Firmware + `PST-SimpleDyno` | geplant |
| 4 | [Neue Messplatine (v3)](04_Hardware_Platine_v3.md) | Hardware | Konzept |
| 5 | [CAN-Sensormodule und WSB-Leistungsteil – Vorbereitung](05_Vorbereitung_CAN_Module_WSB.md) | neues Projekt | Schnittstellen festlegen |
| 5a | [Zahnriementrieb Rolle → Schenck W130](05a_Zahnriemen_Rolle_W130.md) | Mechanik | Vorauslegung |

## Was bis jetzt fertig ist

- **PST-STM32-Firmware 1.0**: Arduino-Mega-kompatibles Protokoll (läuft mit LabVIEW 3.2.1), erkennt die
  STM-LabVIEW 0.2.0 automatisch, exakte Perioden-Messung (11 ns), Sperrzeit und Doppelimpulsfilter,
  Autostopp, Watchdog, BME280/BMP280, 2× MAX6675. Zwei Varianten: Standard (Doppelimpulsfilter 25 %)
  und `nucleo_f446re_dp70` (70 %). Host-Tests für Frequenzberechnung und Protokoll.
- **SimpleDyno** (Python/Qt, Mac/Linux/Windows): rechnet nachweislich wie LabVIEW 3.2.1 (6 echte Läufe:
  gleiche Pmax auf 0,1 PS, gleiche n(Pmax)), Datenbank für Fahrzeuge/Setups/Läufe, Auswerter wie
  MegaLogViewer, Setup-Vergleich, PDF/CSV, Import von LabVIEW-XML, Simulator mit echtem Lauf 161802.

## Erkenntnisse aus den Läufen vom 26.05.2026 (alte Firmware)

| Befund | Bedeutung |
|---|---|
| Zündsignal: meist ein 2. Impuls pro Umdrehung bei ~142° (in allen 6 Läufen gleich) | Fehlauslösung der Zündauswertung / Signalquelle, nicht Software. Rolleneingang mit identischer Kette ist sauber. |
| Rolle: 0 Ausreißer, ~1 % Rauschen je Messpunkt | Einzelperiode zwischen zwei Geberstrichen → Teilungsfehler der Scheibe. Unkritisch nach Filterung. |
| Messfrequenz gemeldet 20,039 statt 20,000 Hz | Konstante 9800 statt 9782,6 in der alten Firmware → Leistung +0,16 % (≈ 0,05 PS). |
| Leistung hängt nicht von der Übersetzung ab | P = J · ω_Rolle · α_Rolle. Übersetzung wirkt nur auf Drehzahlachse und Drehmoment. Absolute Genauigkeit wird von J bestimmt. |
| Filter MA/dq zählen Messpunkte | 35/15 bei 20 Hz entspricht 105/45 bei 60 Hz. Gleiche Zahlen bei 60 Hz → wellige Kurve, Scheinspitze. |
| AFR 10,1 / EGT 1023 °C konstant | Sensoren nicht angeschlossen (ADC in Ruhe, Thermoelement offen). |

## Leitlinien

- **Echtzeit gehört in den Mikrocontroller.** Messung, Regelung (WSB) und Sicherheit laufen dort; der PC
  schickt nur Modus und Sollwerte und zeigt an. Ein hängender PC darf nie einen gefährlichen Zustand erzeugen.
- **Eine Zeitbasis.** Alle Messwerte bekommen einen Zeitstempel vom Mikrocontroller.
- **Selbstbeschreibend.** Neue Sensoren/Kanäle ohne Änderung an der PC-Software (Kanalbeschreibung im Protokoll).
- **Kompatibel bleiben.** Solange LabVIEW im Einsatz ist, spricht die Firmware weiter das Mega-Protokoll.
