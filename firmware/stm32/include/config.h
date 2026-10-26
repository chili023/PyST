/*
 * PST-STM32 – Benutzer-Konfiguration
 *
 * Alle Einstellungen, die man fuer den eigenen Pruefstand anpassen kann,
 * stehen hier. Nach einer Aenderung neu bauen und flashen (siehe README.md).
 *
 * Ab 1.1.0 (PST-Diag 1) sind die meisten Werte nur noch STANDARDWERTE: sie
 * lassen sich zur Laufzeit per "#SET <schluessel> <wert>" aendern und mit
 * "#SAVE" im Flash speichern (Schluessel in Klammern, Liste: README.md).
 */
#ifndef CONFIG_H
#define CONFIG_H

#define FW_VERSION          "1.1.0"
#define FW_DATE             "26.10.26"

/* ------------------------------------------------------------------ PC --- */
#define PC_BAUD             115200u   /* LabVIEW 3.2.1 ist fest auf 115200 */

/* Telegrammrate im Arduino-Protokoll (LabVIEW 3.2.1). Der Mega lief mit ca.
 * 61 Hz (comlevel 1). Der Wert wird exakt eingehalten und als "Messfrequenz"
 * uebertragen; LabVIEW rechnet daraus dT = 1/Messfrequenz. */
#define FRAME_RATE_HZ       60u       /* (rate_hz) 10..100 */

/* Nachkommastellen fuer Zuend- und Rollenfrequenz im ASCII-Telegramm.
 * Der Mega sendete ganze Hz. LabVIEW liest mit Systemdezimaltrennzeichen:
 * auf deutschem Windows wird am Punkt abgeschnitten (= Verhalten wie Mega),
 * auf englischem Windows kommt die volle Aufloesung an. 0 = wie Mega. */
#define ASCII_FREQ_DECIMALS 2         /* (freq_dec) */

/* 'm' schaltet beim Arduino-Sketch die Messung um (Toggle). LabVIEW sendet
 * 'm' aber nur zum START und verlaesst sich darauf, dass der Mega beim
 * Oeffnen des Ports per DTR neu startet. Der Nucleo startet dabei NICHT neu,
 * deshalb bedeutet 'm' hier "Messung starten" (laeuft sie schon: weiterlaufen).
 * Gestoppt wird mit 's', 'e', Break oder automatisch (AUTOSTOP_S).
 * 1 = altes Toggle-Verhalten (nur fuer eigene Tools sinnvoll). */
#define M_TOGGLES           0

/* Automatischer Stopp der Datenausgabe, wenn nach Beginn einer Messung
 * Zuendung UND Rolle so viele Sekunden ohne Signal waren. Ersetzt den
 * Reset, den der Mega beim naechsten Port-Oeffnen bekommen haette, damit die
 * Klimadaten-Abfrage des naechsten Laufs nicht in laufende Telegramme faellt.
 * 0 = aus. */
#define AUTOSTOP_S          5u        /* (autostop_s), nur Modus N */

/* Ein Break-Signal auf der seriellen Leitung (z.B. VISA Clear) stoppt die
 * Ausgabe wie ein Reset. Ob der ST-LINK Breaks weiterreicht, haengt von
 * seiner Firmware ab – schadet aber nicht. */
#define BREAK_STOPS_STREAM  1

/* ------------------------------------------------ Drehzahlerfassung ------ */
/* Eingaenge: Zuendung PA0 (TIM2_CH1), Rolle PB9 (TIM2_CH2), 11,1 ns Aufloesung.
 * Flanke: 1 = steigend (wie bisher), 0 = fallend. */
#define IGN_EDGE_RISING     1         /* (edge_ign  R/F) */
#define ROLL_EDGE_RISING    1         /* (edge_roll R/F) */
#define INPUT_PULLUP        0         /* (pullup) 1 = interne Pull-ups an PA0/PB9 */

/* Digitaler Hardware-Eingangsfilter des Timers (0..15). 15 = Pegel muss
 * ca. 2,8 us stabil sein -> unterdrueckt kurze Zuendstoerungen. */
#define INPUT_HW_FILTER     15u       /* (hw_filter) */

/* Sperrzeit: Impulse, die schneller als diese Zeit nach dem letzten
 * gueltigen Impuls kommen, werden verworfen (Prellen, Stoerungen). */
#define IGN_MIN_PERIOD_US   500u      /* (ign_min_period_us) = 120000 1/min bei 1 Imp/U */
#define ROLL_MIN_PERIOD_US  20u       /* (roll_min_period_us) */

/* Doppelimpulsfilter Zuendung: Impulse, die frueher als X % der (groesseren der
 * beiden letzten) Periodendauer kommen, werden verworfen.
 *  25 % = nur Prellen/Nachschwinger kurz nach dem Impuls; laesst auch ungleich-
 *         maessige Zweizylinder (180/540 Grad) durch.
 *  70 % = zusaetzlich ein Stoerimpuls pro Umdrehung irgendwo zwischen 30 % und
 *         70 % der Periode (so bei den Laeufen vom 26.05.26: 2. Impuls bei ~142 Grad).
 *         Nur fuer 1-Zylinder bzw. gleichmaessige Zuendfolge!
 *  0 = aus.  (ign_lockout_pct; ab 1.1.0 einfach "#SET ign_lockout_pct 70" + "#SAVE"
 *  statt eigener Variante; pio run -e nucleo_f446re_dp70 baut sie weiterhin) */
#ifndef IGN_REL_LOCKOUT_PCT
#define IGN_REL_LOCKOUT_PCT 25u
#endif
#define ROLL_REL_LOCKOUT_PCT 0u       /* (roll_lockout_pct) */

/* Erwartungsfenster Zuendung (Plan 2.2, docs/02_Firmware_Messung.md):
 * P = geglaettete Periode. Impuls frueher als LO % von P -> verworfen,
 * spaeter als HI % -> gueltig, aber "Luecke". 3x hintereinander ausserhalb
 * -> neu einrasten. LO = 0: aus (Verhalten wie 1.0.0). Nur fuer 1-Zylinder
 * bzw. gleichmaessige Zuendfolge. */
#define IGN_WIN_LO_PCT      0u        /* (ign_win_lo_pct) z.B. 70 */
#define IGN_WIN_HI_PCT      150u      /* (ign_win_hi_pct) */

/* Mittelung: Frequenz = Anzahl Perioden / Zeit ueber alle Impulse eines
 * Telegramm-Intervalls (exakt, keine Rundung pro Impuls). Kommen weniger
 * Impulse, wird bis auf mindestens so viele Perioden zurueckgegriffen,
 * jedoch nicht weiter als MAX_AVG_SPAN_MS. */
#define IGN_MIN_PERIODS     2u        /* (ign_min_periods) */
#define ROLL_MIN_PERIODS    1u        /* (roll_min_periods) */
#define MAX_AVG_SPAN_MS     250u      /* (max_avg_span_ms) */

/* Kein Impuls laenger als diese Zeit -> Frequenz 0. */
#define IGN_TIMEOUT_MS      500u      /* (timeout_ign_ms) unter 120 1/min bei 1 Imp/U = 0 */
#define ROLL_TIMEOUT_MS     1000u     /* (timeout_roll_ms) */

/* ------------------------------------------------ Klimasensor ------------ */
/* BME280 oder BMP280 an I2C1 (PB8 = SCL, PB7 = SDA), Adresse 0x76 oder 0x77
 * wird automatisch erkannt. Korrekturoffsets wie im Arduino-Sketch. */
#define ENV_T_OFFSET_C      0.0f
#define ENV_P_OFFSET_PA     0.0f
#define ENV_H_OFFSET_PCT    0.0f

/* ------------------------------------------------ Abgastemperatur -------- */
/* MAX6675 an SPI1 (PB3 SCK, PB4 MISO). EGT1-CS liegt auf PA13 = SWDIO!
 * Ist EGT1 aktiv, wird PA13 erst 2 s nach dem Start umkonfiguriert, damit
 * ein Debugger noch verbinden kann. Flashen per Drag&Drop/ST-LINK geht immer. */
#define EGT1_ENABLE         1         /* (egt1) CS = PA13 */
#define EGT2_ENABLE         1         /* (egt2) CS = PC6  */

/* ------------------------------------------------ AFR ueber Analogeingang  */
/* Externer Breitband-Controller mit Analogausgang an ADC_CH1 (PB0) oder
 * ADC_CH2 (PA1). Max. 3,3 V am Pin! 0 = kein AFR (Feld wird 0.00). */
#define AFR_SOURCE          0         /* (afr_src) 0 = aus, 1 = PB0, 2 = PA1 */
#define AFR_AT_0V           10.0f     /* (afr_0v) Kennlinie wie alte STM-Firmware: */
#define AFR_AT_FULLSCALE    20.0f     /* (afr_fs) 0 V = AFR 10, 3,3 V = AFR 20     */

/* ------------------------------------------------ Diagnose (PST-Diag 1) -- */
/* Flanken-Oszi (#BURST): max. Anzahl Flanken je Aufnahme (5 Byte RAM je Flanke). */
#define BURST_MAX           8192u

/* Flash-Sektor fuer die Einstellungen: Sektor 7 (letzter, 128 KB) ab 0x08060000.
 * Die Firmware muss davor enden (wird beim #SAVE geprueft). */
#define SETTINGS_FLASH_ADDR   0x08060000u
#define SETTINGS_FLASH_SIZE   0x20000u
#define SETTINGS_FLASH_SECTOR 7u

#endif /* CONFIG_H */
