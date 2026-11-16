# PyST 1.4.3 – Release Notes

Fehlerbehebung zu 1.4.2, Stand 16.11.2026. Keine neue Firmware nötig (Mega 3.2.0, PST-STM32 1.2.0, Basis 0.4.0
bleiben).

## Behoben

**1. Leistungslauf während einer Dauermessung stockte und wurde nicht beendet**
- Läuft im Reiter „Oszi / Dauermessung“ eine Dauermessung, schaltet PyST das Messboard in den Diagnose-Modus D. Ein
  Leistungslauf, der in dieser Zeit gestartet wurde, lief bisher ebenfalls im Modus D statt mit dem normalen,
  LabVIEW-identischen Telegramm. Beim Arduino Mega 3.2.0 unter Windows stockte der Lauf und endete nicht.
- Jetzt schaltet jeder Leistungslauf das Gerät in seinen Standardmodus (Mega und F446: normal, PyST-Basis mit Zange:
  wie bisher D). Nach dem Lauf schaltet die Dauermessung von selbst wieder auf D und zeichnet ohne Lücke weiter.
- Die Dauermessung stößt die Ausgabe des Messboards nicht mehr an, solange ein Lauf läuft, und nimmt dem Lauf dabei
  keine Telegramme mehr weg.

**2. Klima 0 °C / 500 mbar**
- Antwortet der Klimasensor nicht, meldet der Mega „0.0;0;0.0“. PyST hat das bisher als Messwert übernommen; der
  Druck wurde auf 500 mbar begrenzt und die Leistung damit falsch korrigiert.
- Jetzt gelten nur Werte im Messbereich des BME280 (−40 … 85 °C, 300 … 1100 mbar). Sonst rechnet der Lauf mit den
  eingetragenen Werten und sagt „kein Sensorwert“.
- Ursache auf der Hardware-Seite prüfen: BME280 und Spartan 2 (AFRBoard) hängen am selben I²C-Bus (Mega Pin 20/21).
  Meldet der Mega gleichzeitig AFR 20,0, antwortet auch der Spartan nicht – dann ist meist der ganze Bus gestört
  (z. B. Spartan ohne 12 V, Pull-ups, Steckverbindung).

**3. Dauermessung ohne Telegramm ließ sich nicht speichern**
- Kam während einer Dauermessung kein einziges Telegramm an, brach das Eintragen mit „KeyError 'n_rolle_1_min'“ ab.
  Jetzt sagt PyST, dass vom Messboard keine Daten kamen, und legt keinen leeren Lauf an.

**4. Geräte-Einstellungen:** `afr_addr` (I²C-Adresse des Spartan 2, Standard 1) hat jetzt einen Erklärtext.

## Abhilfe mit 1.4.2

Bis zum Update: vor dem Start der Dauermessung im Reiter „Oszi / Dauermessung“ den Haken „Diagnose-Telegramm
(Modus D)“ herausnehmen. Liefert der Klimasensor nichts, unter Einstellungen › Rolle/Lauf „Klima vor jedem Lauf
messen“ ausschalten und Temperatur/Druck eintragen.
