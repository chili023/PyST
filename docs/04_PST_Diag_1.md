# PST-Diag 1 – Oszi-/Diagnosemodus für alle PST-Messelektroniken

Stand 26.10.2026, verbindlich für: Arduino-Mega-Sketch (ab 3.1.0), PST-STM32-Firmware F446 (ab 1.1.0),
Basis-Firmware G473 (ASCII-Kompatibilitätsmodus) und PyST (ab 1.3). Ziel: jede Messelektronik kann ohne
Neu-Flashen zwischen „normal“ (LabVIEW-kompatibel) und „Diagnose/Oszi“ umgeschaltet werden.

## 1 Grundregeln

1. **Nach jedem Start/Reset gilt der Normalmodus** – Ausgabe byte-identisch zum bisherigen Protokoll
   (Mega 3.0.0 bzw. F446 1.0.0). LabVIEW 3.2.1 und STM-LabVIEW 0.2.0 merken nichts.
   Der Modus wird **nie** dauerhaft gespeichert.
2. Bisherige Befehle bleiben unverändert: Einzelzeichen `v`, `e`, `m` (Mega: Umschalten), `s` (F446),
   8-Byte-Befehle der STM-LabVIEW (F446).
3. **Neue Befehle sind Zeilen**: beginnen mit `#`, enden mit `\n` (auch `\r` oder `\r\n` akzeptieren), max. 63 Zeichen.
   Solange eine `#`-Zeile empfangen wird, werden ihre Zeichen **nicht** als Einzelzeichen-Befehle ausgewertet.
   LabVIEW sendet nie `#`.
4. **Antworten** auf `#`-Befehle beginnen immer mit `#`. Fehler: `#ERR;<befehl>;<grund>`.
   Unbekannter Befehl: `#ERR;?;unbekannt`.
5. Zeilen, die das Gerät sendet, beginnen mit: Ziffer (normales Telegramm, wie bisher), `D;` (Diagnose-Telegramm),
   `B;` (Burst-Flanke), `#` (Antwort/Status). Zeilenende `\r\n` (Mega 3.0.0 sendet bei Telegrammen nur `\n` –
   PyST muss beides können).
6. Zahlen ASCII, Dezimalpunkt `.`, Trennzeichen `;`. Unbekannte zusätzliche Felder am Ende muss PyST ignorieren
   (Erweiterbarkeit).

## 2 Fähigkeiten abfragen

`#?` → `#CAP;proto=1;fw=<Name Version>;tick_ns=<Zeitstempel-Auflösung>;ch=I,R;burst_max=<n>;modes=N,D;set=1`

Beispiele: Mega `tick_ns=62.5`, F446 `tick_ns=11.111`, G473 `tick_ns=6.25`.
Antwortet ein Gerät nicht innerhalb von 300 ms mit `#CAP`, ist es ein altes Gerät ohne Diagnose
(Mega 3.0.0 / F446 1.0.0) → PyST nutzt nur die normalen Telegramme (Software-Diagnose, siehe §6).

## 3 Modus

`#MODE N` | `#MODE D` → `#OK;MODE;N|D`

- **N** normales Telegramm (wie bisher).
- **D** Diagnose-Telegramm statt des normalen. Start/Stop der Ausgabe weiterhin mit `m` (Mega) bzw. `m`/`s` (F446).
  Zusätzlich `#RUN 1` / `#RUN 0` → `#OK;RUN;1|0` (eindeutiges Starten/Stoppen, auch beim Mega ohne Umschalt-Logik).
- Telegrammrate wie im Normalmodus (Mega ≈ 49–61 Hz, F446 60 Hz). Optional `#RATE <hz>` (10…1000, soweit die
  Schnittstelle es trägt) → `#OK;RATE;<hz>`; nicht unterstützt → `#ERR;RATE;nicht unterstuetzt`.

## 4 Diagnose-Telegramm (Modus D)

```
D;<zyklus>;<t_ms>;<rate_hz>;<ign_hz>;<roll_hz>;<egt1>;<afr>;<egt2>;<ign_n>;<ign_pmin_us>;<ign_pmax_us>;<ign_rej>;<roll_n>;<roll_pmin_us>;<roll_pmax_us>;<roll_rej>;<flags>
```

| Feld | Bedeutung |
|---|---|
| zyklus | laufender Zähler wie im normalen Telegramm |
| t_ms | Gerätezeit in ms (uint32, läuft über) zum Zeitpunkt des Telegramms |
| rate_hz | Telegrammrate wie im normalen Telegramm |
| ign_hz, roll_hz | Frequenz mit 3 Nachkommastellen, **aus jeder Periode** (nicht nur jeder zweiten): (Anzahl Perioden im Fenster) / (Zeit erste→letzte gültige Flanke). Ist im Fenster < 1 ganze Periode: letzte bekannte Frequenz und Flag „stale“. Länger als Timeout keine Flanke: **0** und Flag „timeout“ |
| egt1, afr, egt2 | wie normales Telegramm (`nan` erlaubt) |
| ign_n, roll_n | Anzahl gültiger Flanken (konfigurierte Flankenrichtung) seit dem letzten Telegramm |
| ign_pmin_us, ign_pmax_us, roll_pmin_us, roll_pmax_us | kürzeste/längste Einzelperiode im Fenster in µs, 1 Nachkommastelle; 0 wenn keine |
| ign_rej, roll_rej | vom Firmware-Filter verworfene Flanken im Fenster (Gerät ohne Filter: 0) |
| flags | Hex ohne Präfix, Bits: 0 Zündung Timeout · 1 Rolle Timeout · 2 Telegramm-/Datenverlust (Puffer voll) · 3 Burst aktiv · 4 Zündung stale · 5 Rolle stale · 6 Burst-Ausgabe hat Telegramme verdrängt |

## 5 Burst – Flanken-Oszi

`#BURST <ch> <n> <edge> [T<pct>] [P<pre>]` startet eine Flankenaufnahme.

| Parameter | Werte |
|---|---|
| ch | `I` Zündung, `R` Rolle, `A` beide (gemeinsamer Puffer, Kanal je Eintrag) |
| n | Anzahl Flanken, 1…burst_max |
| edge | `F` fallend, `R` steigend, `B` beide (→ Pulsbreiten sichtbar) |
| T<pct> | optional **Trigger**: Aufnahme läuft zirkulär; ausgelöst, wenn eine Periode (gleiche Flankenrichtung, gleicher Kanal) um mehr als pct % vom Mittel der 8 vorherigen Perioden abweicht (Sprung, Aussetzer, Doppelimpuls). Ohne T: sofort |
| P<pre> | optional Anzahl Flanken vor dem Trigger (Standard n/2) |

`#BURST STOP` bricht ab → `#OK;BURST;STOP`.

Ablauf und Antworten:
```
#OK;BURST;ARMED;ch=A;n=800;edge=B;trig=20;pre=400      (sofort)
#BURST;BEGIN;n=800;tick_ns=62.5;trig_idx=400;ch=A      (wenn fertig/ausgelöst; trig_idx=-1 ohne Trigger)
B;<idx>;<ch>;<lvl>;<ticks>                              (n Zeilen, idx 0…n-1 zeitlich aufsteigend)
#BURST;END;n=800;lost=0
```
- `ch` = `I` oder `R`; `lvl` = Pegel **nach** der Flanke am MCU-Pin (1 = steigend, 0 = fallend);
  `ticks` = Zeitstempel als **uint32 dezimal** in tick_ns (läuft über – PyST entfaltet; Abstand zweier Einträge
  < 2^31 Ticks garantiert die Firmware, sonst Eintrag `lost` zählen).
- Während der Ausgabe darf die Firmware Telegramme aussetzen (Mega: 115200 Baud). Zyklus zählt weiter,
  Flag Bit 6 im nächsten D-Telegramm.
- Bursts funktionieren in Modus N und D (PyST nutzt sie in D).

## 6 Einstellungen ohne Neu-Flashen

- `#GET` → je Einstellung eine Zeile `#SET;<key>=<wert>`, dann `#OK;GET`.
- `#SET <key> <wert>` → `#OK;SET;<key>=<wert>` (wirkt sofort, flüchtig).
- `#SAVE` → `#OK;SAVE` (EEPROM bzw. Flash; beim Start geladen, mit Prüfsumme/Version, sonst Standardwerte).
- `#DEFAULTS` → `#OK;DEFAULTS` (Standardwerte, flüchtig bis `#SAVE`).
- Schlüssel je Firmware frei, aber diese Namen gemeinsam verwenden, wo vorhanden:
  `edge_ign`, `edge_roll` (F/R), `timeout_ign_ms`, `timeout_roll_ms`, `pullup` (0/1), `thermo` (0/1),
  `bme_addr` (hex), `ring_ign`, `ring_roll`, `ign_lockout_pct`, `ign_min_period_us`, `roll_min_period_us`, `rate_hz`.
- Gespeicherte Einstellungen dürfen das **normale** Telegramm nur so verändern, wie es die Einstellung
  ausdrücklich will (z. B. `thermo`); das Format bleibt.

## 7 PyST-Seite (Kurzfassung)

- Beim Verbinden `#?` senden; bei `#CAP` Diagnose verfügbar, sonst Software-Diagnose aus normalen Telegrammen.
- Oszi-/Messwerkzeug: Dauermessung, Linienschreiber, Software-Trigger auf Ereignisse (für alle Geräte),
  Flanken-Ansicht aus Bursts (für Geräte mit PST-Diag 1).
- Simulator (`SIM:mega`, `SIM`) muss PST-Diag 1 nachbilden, damit alles ohne Hardware testbar ist.

## 8 Präzisierungen (Abgleich F446 1.1.0 / Mega 3.1.0)

Gemeinsam festgelegt, damit PyST beide Geräte gleich behandeln kann:

1. Ohne Trigger: `#OK;BURST;ARMED;ch=..;n=..;edge=..;trig=0;pre=0` und `#BURST;BEGIN;...;trig_idx=-1`.
   Mit Trigger: `trig_idx` = `pre`.
2. `#SAVE` während laufender Telegramme (`m`/`#RUN 1`) oder eines Bursts → `#ERR;SAVE;messung laeuft`.
3. `#RATE` außerhalb des Machbaren → `#ERR;RATE;bereich <min>-<max>`; Gerät ohne einstellbare Rate
   (Mega) → `#ERR;RATE;nicht unterstuetzt`.
4. `flags` im D-Telegramm hexadezimal in **Großbuchstaben** ohne führende Nullen; Frequenzen mit
   3 Nachkommastellen.
5. Frequenz im D-Telegramm: alle Perioden, die im Intervall enden (inkl. der Periode ab der letzten Flanke
   davor), geteilt durch deren Gesamtzeit. „stale“ = keine ganze Periode im Intervall.
6. Ein Autostopp (falls vorhanden) wirkt nur in Modus N.

## 9 Umsetzung Arduino Mega 3.1.0

- `#CAP;proto=1;fw=PST-Mega 3.1.0;tick_ns=62.5;ch=I,R;burst_max=800;modes=N,D;set=1`
- Zeitstempel: Timer4 (Zündung, ICP4 = Pin 49) und Timer5 (Rolle, ICP5 = Pin 48) ohne Vorteiler,
  synchron gestartet (gemeinsame Zeitbasis für `ch=A`, ±1 Tick), 32 bit = ICR + Überlaufzähler.
- Einstellungen (`#GET`-Reihenfolge, Standardwert, Bereich):

  | Schlüssel | Standard | Bereich / Bedeutung |
  |---|---|---|
  | `edge_ign`, `edge_roll` | `F` | `F`/`R` gemessene Flanke (3.0.0: fallend) |
  | `timeout_ign_ms` | 500 | 10…3000, danach Frequenz 0 |
  | `timeout_roll_ms` | 1000 | 10…3000 |
  | `pullup` | 1 | interne Pull-ups an Pin 48/49 |
  | `thermo` | 0 | 1 = MAX31855 vorhanden (ändert das normale Telegramm wie in 3.0.0) |
  | `bme_addr` | `0x76` | `0x08`…`0x77`, Eingabe `77` oder `0x77` |
  | `afr_addr` | 1 | I²C-Adresse Spartan 2 (1…127) |
  | `ring_ign`, `ring_roll` | 5 / 50 | 1…64 Perioden, Mittelung im **Normalmodus** |
  | `ign_lockout_pct` | 0 | 0…95 % Doppelimpulsfilter Zündung (Periode < x % der letzten → verworfen) |
  | `ign_min_period_us`, `roll_min_period_us` | 0 | 0…1 000 000, kürzere Perioden verworfen |

  `rate_hz` gibt es beim Mega nicht (Telegrammtakt über Timer2 wie 3.0.0, ≈ 49–61 Hz).
- D-Telegramm: `egt1;afr;egt2` = bei `thermo=1` Thermoelement; AFR; Sondentemperatur –
  bei `thermo=0` Sondentemperatur; AFR; `nan` (gleiche Werte wie im normalen Telegramm).
- Burst-Ausgabe verdrängt Telegramme (Zyklus zählt weiter, Bit 6); die Fensterwerte (`ign_n` …)
  zählen dann seit dem letzten **gesendeten** Telegramm. `lost` zählt verpasste Gegenflanken bei `edge=B`
  (Impuls kürzer als die Umschaltzeit) und Lücken > 130 s.
- Grenzen (16 MHz, im Simulator gemessen, `test/avr_bench`): ISR ≈ 400 Takte (25 µs) je Flanke im
  Normalbetrieb → max. ≈ 40 000 Flanken/s beider Kanäle zusammen (mit Filter ≈ 30 000/s); während eines
  Bursts mit `edge=B` und Trigger bis 1080 Takte → ≈ 15 000 Flanken/s. Bei `edge=B` muss ein Impuls
  länger als ≈ 7 µs sein (Zeit bis zum Umschalten der Flankenrichtung, mit anderen Interrupts bis ≈ 15 µs),
  sonst wird die Gegenflanke verpasst und in `lost` gezählt.

## 10 Erweiterungen PyST-Basis

Gilt zusätzlich für die PyST-Basis (Funk-Zündzange, WLAN, Steckdosen) und die Zange direkt per USB; PyST ab 1.3
(Branch `feature/pyst-zange`). Alles Bisherige bleibt gültig, PyST ignoriert Unbekanntes. Die Befehle gelten auf
allen Ports der Basis: USB, Debug-UART und **TCP-Port 2323** (WLAN; PyST-Portname `tcp://192.168.4.1:2323` bzw.
`tcp://<name>.local:2323`, `.local` per mDNS).

**`#CAP`** zusätzlich `hw=<Platine>` und `ign=zange` (Zange gekoppelt, `ch=I,R`) bzw. `ign=none` (`ch=R`, Zündung 0,
D-Flag Bit 0). Ändert sich die Kopplung, fragt PyST `#?` neu.

**D-Telegramm**: zwei Felder **angehängt** – `ign_peak_mv` (Mittel der Funken-Spitzenwerte im Fenster, 0 = kein
Funke) und `ign_src` (0 keine, 1 Zange-Ferrit, 2 Zange-Probe). Wegen der Funklatenz gibt die Basis alle Telegramme
mit fester Verzögerung aus (Einstellung `ign_delay_ms`), Rolle und Zündung liegen zeitrichtig im selben Fenster.
`#BURST I|A` liefert die Zündflanken aus den Zeitstempeln der Zange (Masterzeit, `tick_ns=6.25`).
PyST nutzt bei einer Basis mit Zange auch für Leistungsläufe Modus D (Kanal „Zündstärke“).

**Ereigniszeilen** (unaufgefordert, beginnen mit `E;`, `master_ms` = Gerätezeit in ms wie `t_ms`):
`E;<master_ms>;MARK;<quelle>;<text>` · `E;…;ZANGE;verbunden|verloren` · `E;…;DOSE;<nr>;<zustand>`.

**Befehle** (Antworten beginnen mit `#`; Fehler `#ERR;<befehl>;<grund>`). Wo nur der Befehl festgelegt ist, erwartet
PyST die angegebene Antwortform als Schlüssel=Wert-Felder in beliebiger Reihenfolge (*erwartet*):

| Befehl | Antwort |
|---|---|
| `#ZANGE` | `#ZANGE;id=1;mac=..;akku_mv=..;akku_pct=..;vbus=0/1;rssi=..;funken=..;log=..;blitz=0/1/2;phi=<°KW>;fw=..;sync_us=..` (*erwartet zusätzlich* `verbunden=0/1`); je gekoppelter Zange eine Zeile, Abschluss `#OK;ZANGE;n=<Anzahl>`; ohne Zange `#ERR;ZANGE;…` |
| `#ZANGE SET <key> <wert>` / `#ZANGE GET <key>` | nach Quittung der Zange; *erwartet* `#OK;ZANGE;SET;<key>=<wert>` bzw. `#OK;ZANGE;GET;<key>=<wert>` (auch `#ZANGE;<key>=<wert>`) |
| `#ZANGE MARK` · `REC 1` · `REC 0` · `LOG` · `IDENT` · `OFF` | *erwartet* `#OK;ZANGE;<befehl>` |
| `#PAIR 1` / `#PAIR 0` | Kopplungsfenster (30 s) auf/zu, `#OK;PAIR;1/0`; Ergebnis als Ereignis `ZANGE;verbunden` |
| `#PAIR LIST` / `#PAIR DEL <id>` | *erwartet* je Gerät `#PAIR;id=..;mac=..;typ=..;sn=..;fw=..;verbunden=0/1`, dann `#OK;PAIR;LIST` |
| `#NET` | *erwartet* `#NET;mode=AP/STA/OFF;kanal=..;ssid=..;ip=..;clients=..;rssi=..;tcp=0/1;err=0` |
| `#NET AP <kanal> <ssid> <pw>` · `#NET STA <ssid> <pw>` · `#NET OFF` | `#OK;NET;…` (SSID/Passwort ohne Leerzeichen, Zeile ≤ 63 Zeichen) |
| `#DOSE` | *erwartet* je Steckdose `#DOSE;nr=..;typ=..;host=..;an=0/1;p_mw=..;erreichbar=0/1;rest_s=..;fehler=0`, dann `#OK;DOSE` |
| `#DOSE ADD <nr> <typ> <ip/name>` · `#DOSE <nr> 1/0/TOGGLE [sek]` · `#DOSE DEL <nr>` | `#OK;DOSE;…`; `sek` = Rückfallzeit (Totmann) |
| `#DOSE SCAN` | *erwartet* je Fund `#DOSE;SCAN;nr=0;typ=..;host=..;ip=..`, dann `#OK;DOSE;SCAN` |

Steckdosen-Typen: `SHELLY2` (Gen2/3/4, RPC), `SHELLY1` (Gen1), `TASMOTA`, `ESPHOME` (Web-Server-REST), höchstens 8.
Einschalten mit Rückfallzeit, die laufend erneuert wird; je Steckdose „fällt aus“ oder „bleibt an“.
Komfortfunktion, **keine Sicherheitsfunktion**, kein Not-Aus. PyST kann dieselben Typen auch **direkt** per HTTP
schalten, wenn PC und Steckdosen im selben Netz sind (ohne Basis).

**Zange direkt per USB** (ESP32-C3 USB-Serial-JTAG, VID 0x303A / PID 0x1001): Mega-kompatibles ASCII + PST-Diag 1
mit `ch=I`, `tick_ns=1000`, Rolle = 0 → Zündungs-Oszi allein. **USB = galvanische Verbindung** – PyST warnt, nur zum
Auslesen/Testen. Da viele ESP32-Boards dieselbe USB-Kennung haben, entscheidet `#CAP`; PyST verbindet die Zange nicht
automatisch. Log der Zange:

- `#LOG INFO` → *erwartet* `#LOG;head=<nächster idx>;tail=<ältester idx>;n=..` (auch als `#OK;LOG;INFO;…`)
- `#LOG GET <von> <n>` → `n` Zeilen `L;<idx>;<art>;<lokal_us>;…`, *erwartet* danach `#OK;LOG;GET`.
  *Erwartete* Arten: `F` Funke (`;src;flags;peak_mv`, flags Bit 0 = verworfen), `M` Marker (`;taste`, 1–4, 10 = fern),
  `Y` Sync-Paar (`;master_us`), `S` Status (`;akku_mv;akku_pct;flags`). Auch die Namen `FUNKE/MARK/SYNC/STATUS` bzw.
  `1…4` werden verstanden; unbekannte Arten bleiben im Rohlog.

PyST macht daraus eine Dauermessung (50 Hz, Zündfrequenz aus den Funken-Abständen, Zündstärke, Marker, Akku,
Rohlog `zange_log.csv`), die wie jede Messung als `.pyst` weitergegeben werden kann.

**Simulator**: `SIM:basis` (Basis mit gekoppelter Zange und zwei Steckdosen), `SIM:basis:stoerung` (Paketverlust,
Zange zeitweise weg), `SIM:basis:ohne` (Koppeln vorführen), `SIM:zange` (Zange per USB mit Log). Einstellungen
`sim_funkverlust` (%), `sim_zange_weg`, `sim_zange_mark` (je Minute).
