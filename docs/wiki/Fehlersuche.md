# Fehlersuche (FAQ)

Erst einmal: **Einstellungen › Verbindung › Prüfen › Alles prüfen** drücken. Das Protokoll (**Protokoll kopieren**)
hilft auch bei Fragen in den [Issues](https://github.com/chili023/PyST/issues).

## Start und Installation

**PyST startet nicht (macOS, nach ZIP-Download).**
Gatekeeper blockt die Skripte. Siehe [INSTALL.md](https://github.com/chili023/PyST/blob/main/INSTALL.md)
(`xattr` oder „Dennoch öffnen“).

**Nach einem Update fehlt ein Paket (z. B. `cryptography`).**
Ordner `PyST/.venv` löschen und neu starten. Das Startskript installiert alles neu.

**Linux: Qt startet nicht.**
Qt 6 braucht `libxcb-cursor0` (`sudo apt install python3-venv libxcb-cursor0`), siehe [Installation](Installation.md).

**Das Fenster passt nicht auf den Bildschirm.**
**Ansicht › Größe (wirkt nach Neustart)** auf *Automatisch* oder einen kleineren Wert, dann **Jetzt neu starten**.
Siehe [Darstellung](Darstellung.md).

## Verbindung

**Kein Port in der Liste / „Verbinden“ geht nicht (Linux).**
Benutzer in die Gruppe `dialout` aufnehmen, ab- und wieder anmelden. Dann **↻** unter Einstellungen › Verbindung.

**Windows: Nucleo oder Mega-Nachbau wird nicht erkannt.**
ST-LINK- bzw. CH340-Treiber installieren ([INSTALL.md](https://github.com/chili023/PyST/blob/main/INSTALL.md)).

**PyST verbindet nicht von selbst.**
Automatisch verbindet PyST nur STM32 (F446, Nucleo) und Original-Arduino-Mega mit PST-Firmware. Die PyST-Basis und
Mega-Nachbauten mit anderem USB-Chip verbindet man von Hand (Port wählen, **Verbinden**). Nach einem **Trennen** von
Hand bleibt PyST getrennt. Prüfen, ob **Messboard automatisch verbinden (Prüfung alle 5 s)** angehakt ist.

**Die Zange am USB wird nicht automatisch verbunden.**
Das ist Absicht (galvanische Verbindung zum PC). Port der Zange unter Einstellungen › Verbindung wählen und
**Verbinden**. ⚠️ Nie am laufenden Motor.

**Statusleiste: „Anmeldung an der Basis gescheitert“ (WLAN).**
PyST hat keinen gültigen Schlüssel der Basis, oder die Basis hat einen **Neuen Schlüssel** vergeben. Einmal per USB
verbinden, dann wieder **PC per WLAN verbinden**. Eine abgelehnte Anmeldung wiederholt PyST nicht von selbst.

**Statusleiste: „im Bootloader – Firmware unter Einstellungen › Firmware aufspielen“.**
Die Basis hat keine gültige Firmware (z. B. abgebrochenes Update). Unter [Firmware und Updates](Firmware-und-Updates.md)
neu aufspielen.

**Statusleiste: „nicht verbunden – Zange aus oder nicht gekoppelt“.**
Zange einschalten (T1 halten). Ist sie neu, koppeln: **Koppeln …** und an der Zange T1 + T2 3 s halten. Nach einem
Wechsel des WLAN-Kanals sucht die Zange die Basis neu.

## Lauf

**START tut nichts, es kommt eine Meldung zu den Parametern.**
*n Stop* muss größer als *n vom Gas* sein (oder 0). Übersetzung, Rollengeber, Zündimpulse, Trägheit und Filter
müssen größer als 0 sein.

**„Kein Laufende nach 120 s – abgebrochen“.**
PyST hat kein Laufende gefunden. *n vom Gas* liegt vermutlich unter der Haltedrehzahl, oder die Drehzahl ist nie
gefallen. *n vom Gas* über die Haltedrehzahl setzen oder *n Stop* einschalten.

**Die Kurve fängt zu spät an oder ist zu kurz.**
Vor dem START den Motor ruhig **unter** *n vom Gas* halten. PyST schneidet den Vorlauf bis zur letzten Verzögerung
unter *n vom Gas* ab. Siehe [Rechenweg](Rechenweg.md#anfang-und-ende-der-kurve).

**Die Leistung ist viel zu hoch oder zu niedrig.**
Meist stimmt die **Übersetzung** nicht. Sie geht quadratisch ein. Neu einmessen oder live abgleichen
([Übersetzung](Übersetzung.md)). Zweite Ursache: Rollenträgheit oder Rollengeber falsch.

**Die Übersetzung aus der Zündung ist doppelt oder halb so groß.**
**Zündung [Imp/U]** ist falsch eingestellt, oder der Zündabnehmer zählt doppelt. Die Zangentaste „Übersetzung
messen“ lehnt solche Werte ab.

**Die Kurve ist zackig.**
Spalte **Fehler** in der Laufliste ansehen. Hoher Anteil = Störungen an Rolle oder Zündung. In der Auswertung die
Fehlerkanäle zeigen. Für die Suche: [Oszi und Dauermessung](Oszi-und-Dauermessung.md). Sonst die Filter prüfen
(Standard 1,75 s / 0,75 s).

**Ein alter Lauf mit 20 Hz sieht anders aus als ein neuer mit 60 Hz.**
Bei Filtern in Messpunkten wirken sie bei 60 Hz nur ein Drittel so lang. Filter in **Sekunden** einstellen.

**Funkenmessung: der Lauf rechnet mit dem alten Wert.**
Das Zündsignal war nicht brauchbar. Dann nimmt PyST den eingestellten Wert. ⚠️ Funkenmessung ist noch nicht am
Prüfstand bestätigt.

**Vor START kommt „Setup geändert“.**
Die SETUP-WERTE wurden geändert, aber nicht gespeichert. Speichern, nur für diesen Lauf nehmen oder abbrechen.
Abschaltbar mit **Bei geändertem Setup vor START nachfragen**.

**Klima zeigt „noch nicht gelesen“.**
PyST liest nur zwischen den Läufen (beim START, von Hand, im Leerlauf alle 5 min bei offenem Reiter Messen). Unter
Einstellungen › Rolle/Lauf › Klima **Klima jetzt lesen**.

## Zange und Tasten

**Die Zangentasten tun nichts in PyST.**
Zange ab Firmware 0.1.3, Basis ab 0.2.3 nötig. Gesperrt sind Neuer Lauf und Übersetzung beim Flashen, ohne
Messboard, im Lauf, bei Oszi-Aufnahme, bei einer Prüfung oder wenn ein Dialog offen ist. Das Band über den Reitern
nennt den Grund. Die Zange zeigt dann das Fehlerbild.

**„Als Hauptzange“ und „Tasten prüfen“ sind grau.**
Die Firmware hat dafür noch keinen Befehl. Bis dahin ist die erste verbundene Zange die Hauptzange.

## Lambda und ECU

**„Lambda: 12 V zu niedrig“.**
Die Bordspannung liegt unter der 12-V-Grenze. Batterie laden oder Grenze unter Einstellungen › Lambda prüfen.

**„ECU verbunden – keine passende .ini“ bzw. „ECU: .ini passt nicht zur Firmware“.**
Unter Einstellungen › ECU **Vom Steuergerät (USB-Laufwerk) holen** oder **Datei wählen …**. Die .ini hängt an der
Firmware-Version des Steuergeräts.

**Live-Kachel zeigt „ECU fehlt“.**
Die Kachel zeigt einen ECU-Kanal, aber es ist keine ECU verbunden.

## Firmware

**„avrdude nicht gefunden“.**
Arduino-IDE oder PlatformIO installieren, Linux `apt install avrdude`, Mac `brew install avrdude`.

**„st-flash nicht gefunden“.**
Mac `brew install stlink`, Linux `apt install stlink-tools`.

**Eine eigene Firmware-Datei wird abgelehnt.**
Die Signatur fehlt, passt nicht oder der Schlüssel ist unbekannt. Unbekannte Schlüssel gehen nur mit **erzwingen**.
Auch dann kann der Bootloader des Geräts die Datei ablehnen. Siehe
[Firmware und Updates](Firmware-und-Updates.md#signaturprüfung-allgemein).

## Daten

**Wo liegen meine Läufe?**
In `~/PyST` (Windows `C:\Users\<Name>\PyST`). Den Pfad der Datenbank zeigt Einstellungen › Verbindung unten.

**Ein Mittelwert fehlt in der .pyst-Datei bzw. im PDF.**
Das ist in 1.4.1 so: Mittelwerte kommen nicht in `.pyst`-Dateien und nicht in die PDF-Tabelle.

**Ich habe einen Lauf gelöscht.**
**Löschen** in der Laufliste fragt nach („Messdateien im PyST-Datenordner werden mitgelöscht.“) und entfernt dann auch
die Messdateien. Ein Rückgängig gibt es in der Oberfläche nicht. Regelmäßig `~/PyST` sichern.

**Fahrzeug gelöscht – wo sind die Läufe?**
Die Läufe bleiben. In der Auswertung unter Fahrzeug **ohne Fahrzeug**.
