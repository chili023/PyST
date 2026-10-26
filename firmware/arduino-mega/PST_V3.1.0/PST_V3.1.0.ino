/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
//                               #Open Source Pruefstandssoftware auf Basis von Arduino Mega und LabVIEW                            //
/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
//
// PST-Mega 3.1.0 vom 26.10.2026 - mit Oszi-/Diagnosemodus "PST-Diag 1" (docs/04_PST_Diag_1.md)
//
// benoetigt die Bibliotheken SparkFunBME280, Adafruit_MAX31855 (+ Adafruit_BusIO), alle als ZIP in
// firmware/arduino-mega/libraries/. Die Dateien pst_core.h/.cpp gehoeren zum Sketch (gleicher Ordner).
//
// Einstellungen (BME280-Adresse, Thermoelement, Ringspeicher, Flanke, Timeouts, Filter, Pull-ups)
// werden NICHT mehr im Quelltext geaendert, sondern ueber die serielle Schnittstelle:
//   #GET  /  #SET <key> <wert>  /  #SAVE (EEPROM)  /  #DEFAULTS
// Standardwerte = Verhalten 3.0.0 (BME280 0x76, thermo 0, Ringspeicher 5/50, fallende Flanke).
//
// Anschluss: Zuendung ICP4 = PL0 = Pin 49 (Timer4), Rolle ICP5 = PL1 = Pin 48 (Timer5),
//            BME280 + Spartan2 (AFR) an I2C (Pin 20/21), MAX31855 CS Pin 53, Pin 2 = high waehrend Telegramm.
//
// ---------------------------------- Changelog 3.1.0 gegenueber 3.0.0 ----------------------------------
// Protokoll:
//  - Normalmodus im Format byte-identisch zu 3.0.0 (v, e, m; Telegramm zyklus;rate;f_zuend;f_rolle;...
//    mit ganzen Hz, Zeilenende '\n'). Nur die Antwort auf 'v' traegt die neue Versionsnummer.
//  - Neu: PST-Diag 1 (#?, #MODE N|D, #RUN, #BURST, #GET/#SET/#SAVE/#DEFAULTS, D-Telegramm, Burst).
// Korrekturen:
//  1. Es wurde nur jede zweite Periode gemessen (Start-/End-Flanken-Paare in den CAPT-ISRs).
//     Jetzt Zeitstempel je Flanke, Periode = Differenz aufeinanderfolgender Flanken -> jede Periode.
//  2. Der Ringspeicher behielt alte Werte, wenn kein Signal mehr kam (Messung4/5 blieb 1).
//     Jetzt Frequenz 0 nach Timeout (Standard 500 ms Zuendung, 1000 ms Rolle), Ringspeicher wird geleert.
//  3. Pull-ups wurden ueber DDRD/PORTD bzw. DDRB/PORTB mit Bitnummern PL0/PL1 gesetzt (falscher Port).
//     Jetzt DDRL/PORTL: PL0 (Pin 49) und PL1 (Pin 48) als Eingang, Pull-up per Einstellung pullup (Std. 1).
//     Ausserdem entfaellt DDRD=0xff/PORTD=0 (schaltete PD0..PD7 inkl. I2C/Serial1 auf Ausgang, I2C-Pull-ups aus).
//  4. AFR ueber I2C (Spartan) ohne Timeout: Wire.setWireTimeout(5 ms) - ein fehlender oder haengender
//     Sensor haelt den Mega nicht mehr auf.
//  5. freqsum4/5 wurden nie auf 0 gesetzt (alter Mittelwert ging als Messwert ein, Teiler n+1).
//     Jetzt sauberes Mittel: f = Anzahl Perioden * 16 MHz / Summe der Perioden im Ringspeicher,
//     auf ganze Hz gerundet (3.0.0: abgeschnitten).
//  6. Ueberlaufzaehler war 16 bit (int) und wurde mit 16-bit-Rechnung verschoben. Jetzt 32-bit-Zeitstempel
//     aus ICR + 16-bit-Ueberlaufzaehler (Race Capture/Overflow wie bisher ueber TOV-Abfrage).
//  7. ovlTIM2 (32 bit) wurde in loop() ohne Sperre gelesen -> jetzt 8 bit (atomar).
//  8. BME280 wird beim Start initialisiert (statt 110 ms Wartezeit beim ersten 'e').
//  9. Befehle: jedes empfangene Zeichen wird ausgewertet (3.0.0 wertete bei mehreren Zeichen im Puffer
//     nur das letzte aus). Kein String-Objekt mehr, keine blockierende Ausgabe (eigener Sendepuffer).
//  10. Timer4 und Timer5 laufen synchron (gemeinsame Zeitbasis fuer Burst mit beiden Kanaelen).
//
// Grenzen (16 MHz, Taktzahlen im Simulator gemessen: test/avr_bench/run_bench.sh):
//  - Capture-ISR ca. 400 Takte (25 us) je Flanke, mit Filter ca. 530 -> max. ca. 40 000 (30 000)
//    Flanken/s beider Kanaele zusammen. Waehrend eines Bursts edge=B mit Trigger bis ca. 1080 Takte
//    -> ca. 15 000 Flanken/s. Praxis: Zuendung < 300 Hz, Rolle wenige kHz -> unkritisch.
//  - edge=B: Impulse muessen laenger als ca. 7 us sein (Zeit bis ICES umgeschaltet ist; laeuft gerade
//    ein anderer Interrupt, bis ca. 15 us), sonst wird die Gegenflanke verpasst (Burst: lost).
//  - RAM: Burst-Puffer 800 Flanken = 3,4 KB (4 Byte Zeitstempel + 2 Bit Kanal/Pegel), gesamt statisch
//    ca. 6,2 KB von 8 KB.
////////////////////////////////////////////////////////////////////////////////////////////////////////

#include <stdint.h>
#include <util/atomic.h>
#include <avr/pgmspace.h>
#include <avr/eeprom.h>
#include <SPI.h>
#include <Wire.h>
#include <SparkFunBME280.h>
#include <Adafruit_MAX31855.h>
#include "pst_core.h"

//-------------------SETUP---------------------------------------
uint32_t baud = 115200;
int comlevel = 1;                      // Telegramm alle comlevel Ueberlaeufe von Timer2 (ca. 49..61 Hz)
#define MAXCS 53                       // CS Pin fuer MAX31855
#define EE_ADDR 0                      // Einstellungen im EEPROM ab Adresse 0
#define WIRE_TIMEOUT_US 5000UL

int p_cor = 0;   // Korrekturoffset fuer die Werte vom BME280
int t_cor = 0;
int h_cor = 0;

//AFR
#define Pump_Current16_to_Lambda_Start 0 // To get index for Pump_Current16_to_Lambda Array for we take Pump_Current16 and subtract Pump_Current16_to_Lambda_Start
#define Pump_Current16_to_Lambda_Size 409 //size of Pump_Current16_to_Lambda Array
#define Ri_to_Temperature_C_Start 113 // To get index for Ri_to_Temperature_C Array for we take Ri and subtract Ri_to_Temperature_C_Start
#define Ri_to_Temperature_C_Size 75 //size of Ri_to_Temperature_C Array
//lookup table to convert Pump_Current16 to Lambda
const PROGMEM unsigned int Pump_Current16_to_Lambda[]={376,379,381,384,387,390,393,395,398,401,403,406,409,411,414,417,419,422,424,427,430,432,435,437,440,442,445,447,450,452,455,457,460,462,464,467,469,472,474,476,479,481,483,486,488,490,493,495,497,499,502,504,506,508,511,513,515,517,520,522,524,526,528,530,533,535,537,539,541,543,546,548,550,552,554,556,558,560,562,564,567,569,571,573,575,577,579,581,583,585,587,589,591,593,595,598,600,602,604,606,608,610,612,614,616,618,620,622,624,626,628,630,632,634,636,638,640,642,644,646,648,650,653,655,657,659,661,663,665,667,669,671,673,675,677,679,681,684,686,688,690,692,694,696,698,700,703,705,707,709,711,713,715,718,720,722,724,726,729,731,733,735,737,740,742,744,746,749,751,753,756,758,760,763,765,767,770,772,774,777,779,781,784,786,789,791,794,796,799,801,804,806,809,811,814,816,819,821,824,827,829,832,834,837,840,842,845,848,851,853,856,859,862,864,867,870,873,876,879,881,884,887,890,893,896,899,902,905,908,911,914,917,920,923,927,930,933,936,939,942,946,949,952,955,959,962,965,969,972,976,979,982,986,989,993,996,1000,1007,1015,1023,1031,1039,1047,1056,1064,1073,1082,1090,1099,1108,1118,1127,1136,1146,1156,1166,1176,1186,1196,1207,1217,1228,1239,1250,1262,1273,1285,1297,1309,1321,1334,1346,1359,1372,1386,1399,1413,1427,1442,1456,1471,1486,1502,1517,1533,1550,1566,1583,1600,1618,1636,1654,1673,1692,1711,1731,1752,1772,1794,1815,1837,1860,1883,1907,1931,1956,1981,2007,2033,2061,2088,2117,2146,2176,2207,2239,2271,2305,2339,2374,2411,2448,2486,2526,2567,2608,2652,2696,2742,2790,2839,2889,2942,2996,3052,3110,3171,3233,3298,3365,3435,3508,3584,3663,3745,3831,3920,4014,4112,4215,4323,4436,4555,4680,4812,4952,5099,5255,5420,5596,5783,5983,6196,6425,6670,6935,7220,7530,7866,8232,8634,9076,9564,10106,10713,11394,12167,13050,14068,15256,16659,18342,20399,22967,26268,30664,36811,46012,61299};
//lookup table to convert Ri to Sensor Temperature[C]
const PROGMEM unsigned int Ri_to_Temperature_C[]={820,818,817,815,814,813,811,810,808,807,805,804,803,801,800,799,798,796,795,794,792,791,790,789,788,786,785,784,783,782,781,780,779,777,776,775,774,773,772,771,770,769,768,767,766,765,764,763,762,761,760,759,758,758,757,756,755,754,753,752,751,751,750,749,748,747,746,745,745,744,743,742,741,741,740};
byte Val_Array[10];
unsigned int iafr=0;
byte No_I2C_Response_Flag=1;
struct I2C_Struct{
  byte I2C_Addr;
  byte ID8;
  unsigned int Pump_Current16;
  byte Ri;
  byte Status8;
};
double Afr;
I2C_Struct I2C_Data; //This Struct holds the I2C data in the same form that it is on Spartan 2
struct Processed_Data_Struct{
  byte I2C_Addr;
  byte ID;
  float Lambda;
  unsigned int Temperature_C;
  byte Status;
};
Processed_Data_Struct Processed_Data; //This Struct hold the final Processed Data

//------------------- Messung ------------------------------------
PstChan  g_ch[2];                      // [0] Zuendung (Timer4), [1] Rolle (Timer5)
PstBurst g_burst;
volatile uint16_t g_ovf4 = 0;
volatile uint16_t g_ovf5 = 0;
volatile uint8_t  ovlTIM2 = 0;
PstDiagChan g_dI = {0}, g_dR = {0};

uint32_t multi;
uint16_t cycle = 0;
double   c = 0;                        // EGT MAX31855
double   Temp, P, Hum;

//------------------- Kommandos / Ausgabe ------------------------
PstCmd    g_cmd;
PstParser g_parser;
uint8_t   g_flags = 0;                 // Merker fuer D-Telegramm (Bit 2 und 6)
uint8_t   g_applied_bme = 0;
uint8_t   g_applied_pullup = 0xFF;

// Sendepuffer: Telegramme und Antworten werden hier abgelegt und ohne Warten
// an Serial weitergegeben (Serial.availableForWrite). Reserve fuer Telegramm
// und Antworten, Burst-/GET-Ausgabe fuellt nur bis OUT_SZ-OUT_RESERVE.
#define OUT_SZ      384
#define OUT_RESERVE 176
static char     g_out[OUT_SZ];
static uint16_t g_oh = 0, g_ot = 0;

// Jobs
static uint8_t  g_get_active = 0, g_get_idx = 0;
static uint8_t  g_save_active = 0, g_save_pos = 0;
static uint8_t  g_save_img[PST_EE_SIZE];
static uint8_t  g_bout_active = 0;
static int16_t  g_bout_idx = 0;
static uint16_t g_bmon_total = 0;
static uint32_t g_bmon_ms = 0;

Adafruit_MAX31855 thermocouple(MAXCS);
BME280 mySensor;

//////////////////////////////////////////////////////////////////
//------------------- Sendepuffer -------------------------------//
//////////////////////////////////////////////////////////////////
static uint16_t out_used() { return (g_oh >= g_ot) ? (g_oh - g_ot) : (uint16_t)(OUT_SZ - g_ot + g_oh); }
static uint16_t out_free() { return OUT_SZ - 1 - out_used(); }

static bool out_put(const char *s, size_t n)
{
  if (n > out_free()) { g_flags |= PST_FL_LOSS; return false; }
  while (n--) {
    g_out[g_oh] = *s++;
    if (++g_oh >= OUT_SZ) g_oh = 0;
  }
  return true;
}

static void out_drain()
{
  int room = Serial.availableForWrite();
  while (room > 0 && g_oh != g_ot) {
    uint16_t chunk = (g_oh > g_ot) ? (g_oh - g_ot) : (OUT_SZ - g_ot);
    if (chunk > (uint16_t)room) chunk = room;
    Serial.write((const uint8_t *)&g_out[g_ot], chunk);
    g_ot += chunk;
    if (g_ot >= OUT_SZ) g_ot = 0;
    room -= chunk;
  }
}

//////////////////////////////////////////////////////////////////
//------------------- Zeitbasis / Kanaele -----------------------//
//////////////////////////////////////////////////////////////////
static uint32_t now_ts4()   // nur mit gesperrten Interrupts
{
  uint16_t t = TCNT4;
  return pst_ts_from_capture(t, g_ovf4, (TIFR4 & _BV(TOV4)) ? 1 : 0);
}
static uint32_t now_ts5()
{
  uint16_t t = TCNT5;
  return pst_ts_from_capture(t, g_ovf5, (TIFR5 & _BV(TOV5)) ? 1 : 0);
}

// ICES je Kanal setzen: Messflanke oder (Burst) Umschalten. Interrupts gesperrt.
static void update_ices()
{
  g_ch[0].toggle = pst_chan_needs_toggle(&g_burst, &g_ch[0], PST_CH_I);
  g_ch[1].toggle = pst_chan_needs_toggle(&g_burst, &g_ch[1], PST_CH_R);
  if (!g_ch[0].toggle) {
    uint8_t want = g_ch[0].meas_lvl ? _BV(ICES4) : 0;
    if ((TCCR4B & _BV(ICES4)) != want) { TCCR4B ^= _BV(ICES4); TIFR4 = _BV(ICF4); }
  }
  if (!g_ch[1].toggle) {
    uint8_t want = g_ch[1].meas_lvl ? _BV(ICES5) : 0;
    if ((TCCR5B & _BV(ICES5)) != want) { TCCR5B ^= _BV(ICES5); TIFR5 = _BV(ICF5); }
  }
}

static void apply_settings()
{
  const PstSettings &s = g_cmd.set;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pst_chan_config(&g_ch[0], s.edge_ign, s.ring_ign, s.timeout_ign_ms, s.ign_min_period_us, s.ign_lockout_pct);
    pst_chan_config(&g_ch[1], s.edge_roll, s.ring_roll, s.timeout_roll_ms, s.roll_min_period_us, 0);
    update_ices();
  }
  if (s.pullup != g_applied_pullup) {
    DDRL &= ~(_BV(PL0) | _BV(PL1));             // ICP4/ICP5 Eingang
    if (s.pullup) PORTL |= (_BV(PL0) | _BV(PL1));
    else PORTL &= ~(_BV(PL0) | _BV(PL1));
    g_applied_pullup = s.pullup;
  }
  if (s.bme_addr != g_applied_bme) {
    mySensor.settings.I2CAddress = s.bme_addr;
    mySensor.begin();                            // 2 ms
    g_applied_bme = s.bme_addr;
  }
}

//////////////////////////////////////////////////////////////////
//------------------- BME280 / AFR / EGT ------------------------//
//////////////////////////////////////////////////////////////////
void BME280read() {
  Temp = mySensor.readTempC();
  Temp = Temp + t_cor;
  P = mySensor.readFloatPressure();
  P = P + p_cor;
  Hum = mySensor.readFloatHumidity();
  Hum = Hum + h_cor;
}

void afr() {
      Wire.beginTransmission(g_cmd.set.afr_addr); //Setup communication with device @ I2C Address = 1
      Wire.write(0); //This will tell Spartan that OEM that we want to start a read from Memory Address 0
      Wire.endTransmission();     // end transmission
      Wire.requestFrom(g_cmd.set.afr_addr, (uint8_t)6); //Read 6 Bytes from Device
      iafr=0;
      No_I2C_Response_Flag=1;
      while (Wire.available() && iafr < sizeof(Val_Array)) //Dump the I2C data into an Array
      {
        Val_Array[iafr] = Wire.read();
        No_I2C_Response_Flag=0;
        iafr++;
      }
      while (Wire.available()) Wire.read();

      I2C_Data.I2C_Addr=Val_Array[0];
      I2C_Data.ID8=Val_Array[1];
      I2C_Data.Pump_Current16=(Val_Array[2]<<8) + Val_Array[3];
      I2C_Data.Ri=Val_Array[4];
      I2C_Data.Status8=Val_Array[5];

      Processed_Data.I2C_Addr=I2C_Data.I2C_Addr;
      Processed_Data.ID=I2C_Data.ID8;
      iafr=I2C_Data.Pump_Current16-Pump_Current16_to_Lambda_Start;
      // make sure array index is within array bounds (iafr ist unsigned wie in 3.0.0)
      if (iafr>(Pump_Current16_to_Lambda_Size-1))
      {
          iafr=Pump_Current16_to_Lambda_Size-1;
      }
      Processed_Data.Lambda=((float)pgm_read_word_near(Pump_Current16_to_Lambda+iafr))/1000; //use Pump_Current16 as index to Pump_Current16_to_Lambda lookup table and then Divide by 1000 to get actual Lambda

      iafr=I2C_Data.Ri-Ri_to_Temperature_C_Start;// Index has to be justified to the right by Ri_to_Temperature_C_Start
      // make sure array index is within array bounds (Ri < 113 -> Unterlauf -> letzter Wert, wie 3.0.0)
      if (iafr>(Ri_to_Temperature_C_Size-1))
      {
          iafr=Ri_to_Temperature_C_Size-1;
      }
      Processed_Data.Temperature_C=pgm_read_word_near(Ri_to_Temperature_C+iafr); //Use the Justified Ri as index to Ri_to_Temaperature_C lookup table to get get Sensor Temperature[C]
      Processed_Data.Status=I2C_Data.Status8;

      if (No_I2C_Response_Flag==1)
      {
      Afr = 2000;
      }
      if (No_I2C_Response_Flag==0)
      {
      Afr = Processed_Data.Lambda*1470;
      }
}

void egt() {
  if (g_cmd.set.thermo) {
    c = thermocouple.readCelsius();
  }
}

//////////////////////////////////////////////////////////////////
//------------------- Setup -------------------------------------//
//////////////////////////////////////////////////////////////////
void setup() {
  pinMode(2, OUTPUT);

  // Einstellungen laden (Version + CRC, sonst Standardwerte)
  pst_cmd_init(&g_cmd);
  pst_parser_init(&g_parser);
  {
    uint8_t img[PST_EE_SIZE];
    eeprom_read_block(img, (const void *)EE_ADDR, PST_EE_SIZE);
    pst_settings_unpack(&g_cmd.set, img);
  }

  Wire.begin(); // Initialize I2C
  Wire.setWireTimeout(WIRE_TIMEOUT_US, true);

  pst_chan_init(&g_ch[0]);
  pst_chan_init(&g_ch[1]);
  memset(&g_burst, 0, sizeof(g_burst));

  cli();
  //Timer2      8-bit: Telegrammtakt
  TCCR2A = 0x00;
  TCCR2B = 0x00;
  TIMSK2 = 0x00;
  //Timer4/5    16-bit: Input Capture ohne Vorteiler (62,5 ns)
  TCCR4A = 0x00; TCCR4B = 0x00; TCCR4C = 0x00; TIMSK4 = 0x00;
  TCCR5A = 0x00; TCCR5B = 0x00; TCCR5C = 0x00; TIMSK5 = 0x00;

  TCCR4B = _BV(ICNC4) | _BV(CS40);    // Noise Canceller, kein Vorteiler, fallende Flanke
  TCCR5B = _BV(ICNC5) | _BV(CS50);
  // Timer4 und Timer5 gleich stellen: Zwischen den wirksamen Schreibzugriffen
  // (jeweils Low-Byte) liegen genau 4 Takte -> TCNT5 = 4.
  asm volatile(
    "sts %[t4h], __zero_reg__ \n\t"
    "sts %[t4l], __zero_reg__ \n\t"
    "sts %[t5h], __zero_reg__ \n\t"
    "sts %[t5l], %[four]      \n\t"
    :: [t4h] "n" (_SFR_MEM_ADDR(TCNT4H)), [t4l] "n" (_SFR_MEM_ADDR(TCNT4L)),
       [t5h] "n" (_SFR_MEM_ADDR(TCNT5H)), [t5l] "n" (_SFR_MEM_ADDR(TCNT5L)),
       [four] "r" ((uint8_t)4));
  g_ovf4 = 0;
  g_ovf5 = 0;
  TIFR4 = _BV(ICF4) | _BV(TOV4);
  TIFR5 = _BV(ICF5) | _BV(TOV5);
  TIMSK4 = _BV(TOIE4) | _BV(ICIE4);
  TIMSK5 = _BV(TOIE5) | _BV(ICIE5);

  TIMSK2 = _BV(TOIE2);                         // Timer 2 Overflow
  TCCR2B = _BV(CS22) | _BV(CS21) | _BV(CS20);  // 1024 prescaler
  sei();

  multi = comlevel * 256;

  //----------SERIAL---------
  Serial.begin(baud);

  //----------BME280--------------
  mySensor.settings.commInterface = I2C_MODE;
  mySensor.settings.I2CAddress = g_cmd.set.bme_addr;
  mySensor.settings.runMode = 3; //Normal mode
  mySensor.settings.tStandby = 0;
  mySensor.settings.tempOverSample = 1;
  mySensor.settings.pressOverSample = 1;
  mySensor.settings.humidOverSample = 1;
  g_applied_bme = 0;                   // erzwingt begin() in apply_settings()

  apply_settings();                    // Kanaele, Flanken, Pull-ups, BME280
}

//////////////////////////////////////////////////////////////////
//------------------- Befehle -----------------------------------//
//////////////////////////////////////////////////////////////////
static void handle_requests()
{
  PstCmd &k = g_cmd;
  if (k.req_cycle_reset) { cycle = 0; k.req_cycle_reset = 0; }
  if (k.req_apply) { apply_settings(); k.req_apply = 0; }
  if (k.req_get) { g_get_active = 1; g_get_idx = 0; k.req_get = 0; }
  if (k.req_save) {
    pst_settings_pack(&k.set, g_save_img);
    g_save_active = 1;
    g_save_pos = 0;
    k.req_save = 0;
  }
  if (k.req_burst_stop) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      g_burst.state = PST_B_IDLE;
      update_ices();
    }
    g_bout_active = 0;
    k.req_burst_stop = 0;
  }
  if (k.req_burst) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      pst_burst_arm(&g_burst, g_ch, k.b_chmask, k.b_n, k.b_edge, k.b_trig, k.b_pre);
      update_ices();
    }
    g_bout_active = 0;
    g_bmon_total = 0;
    g_bmon_ms = millis();
    k.req_burst = 0;
  }
}

static void handle_char(char ch)
{
  char buf[140];
  size_t n;
  switch (pst_cmd_char(&g_cmd, ch)) {
    case 'v':
      n = pst_fmt_version(buf, sizeof(buf), &g_cmd.set);
      out_put(buf, n);
      break;
    case 'e':
      BME280read();
      n = pst_fmt_env(buf, sizeof(buf), Temp, P, Hum);
      out_put(buf, n);
      break;
    default:
      break;
  }
  handle_requests();
}

static void serial_in()
{
  // nur so viele Zeichen, wie Antworten Platz haben -> Rest bleibt im Empfangspuffer
  while (Serial.available() > 0 && out_free() >= 170) {
    char ch = 0;
    int r = pst_parser_feed(&g_parser, (char)Serial.read(), &ch);
    if (r == PST_P_CHAR) {
      handle_char(ch);
    } else if (r == PST_P_LINE) {
      char reply[96];
      uint8_t mode_before = g_cmd.mode;
      g_cmd.burst_busy = g_bout_active;
      g_cmd.burst_active = (g_burst.state != PST_B_IDLE) || g_bout_active;
      size_t n = pst_cmd_line(&g_cmd, g_parser.buf, reply, sizeof(reply));
      if (n) out_put(reply, n);
      if (g_cmd.mode != mode_before) g_flags = 0;
      handle_requests();
    } else if (r == PST_P_LONG) {
      static const char e[] = "#ERR;?;zu lang\r\n";
      out_put(e, sizeof(e) - 1);
    }
  }
}

//////////////////////////////////////////////////////////////////
//------------------- Jobs (nicht blockierend) ------------------//
//////////////////////////////////////////////////////////////////
static void jobs()
{
  char buf[48];
  size_t n;

  // EEPROM: ein Byte je Durchlauf, nur wenn der EEPROM bereit ist
  if (g_save_active && eeprom_is_ready()) {
    eeprom_update_byte((uint8_t *)(EE_ADDR + g_save_pos), g_save_img[g_save_pos]);
    if (++g_save_pos >= PST_EE_SIZE) {
      static const char ok[] = "#OK;SAVE\r\n";
      if (out_put(ok, sizeof(ok) - 1)) g_save_active = 0;
      else g_save_pos = PST_EE_SIZE - 1;       // naechster Durchlauf: nochmal versuchen
    }
  }

  // #GET-Liste
  while (g_get_active && out_free() > OUT_RESERVE / 2 + sizeof(buf)) {
    if (g_get_idx < PST_SET_COUNT) {
      memcpy(buf, "#SET;", 5);
      n = 5 + pst_setting_format(&g_cmd.set, g_get_idx, buf + 5, sizeof(buf) - 7);
      buf[n++] = '\r';
      buf[n++] = '\n';
      out_put(buf, n);
      g_get_idx++;
    } else {
      static const char ok[] = "#OK;GET\r\n";
      out_put(ok, sizeof(ok) - 1);
      g_get_active = 0;
    }
  }

  // Burst: Ueberwachung waehrend der Aufnahme (Luecke >= 2^31 Ticks = 134 s -> lost)
  if (g_burst.state == PST_B_REC) {
    uint16_t tot;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { tot = g_burst.total; }
    if (tot != g_bmon_total) { g_bmon_total = tot; g_bmon_ms = millis(); }
    else if (tot && millis() - g_bmon_ms > 130000UL) {
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { if (g_burst.lost != 0xFFFF) g_burst.lost++; }
      g_bmon_ms = millis();
    }
  }

  // Burst fertig -> Ausgabe starten
  if (g_burst.state == PST_B_DONE && !g_bout_active) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { update_ices(); }
    g_bout_active = 1;
    g_bout_idx = -1;
  }
  while (g_bout_active && out_free() > OUT_RESERVE + sizeof(buf)) {
    if (g_bout_idx < 0) n = pst_fmt_burst_begin(buf, sizeof(buf), &g_burst);
    else if (g_bout_idx < (int16_t)g_burst.n) n = pst_fmt_burst_line(buf, sizeof(buf), &g_burst, g_bout_idx);
    else {
      n = pst_fmt_burst_end(buf, sizeof(buf), &g_burst);
      g_bout_active = 0;
      g_burst.state = PST_B_IDLE;
    }
    out_put(buf, n);
    g_bout_idx++;
  }
}

//////////////////////////////////////////////////////////////////
//------------------- Telegramm ---------------------------------//
//////////////////////////////////////////////////////////////////
static void telegram()
{
  if (!(ovlTIM2 >= comlevel && g_cmd.mess)) return;

  if (g_bout_active) {
    // Burst-Ausgabe verdraengt das Telegramm (PST-Diag 1: Flag Bit 6)
    cycle++;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      ovlTIM2 = 0;
      TIFR2 = _BV(TOV2);
      TCNT2 = 0x00;
    }
    g_flags |= PST_FL_DISPLACED;
    return;
  }

  digitalWrite(2, HIGH);
  afr();
  egt();
  cycle++;

  float timer;
  timer = TCNT2 + multi;
  timer = F_CPU / timer;
  timer = timer / 1024;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    ovlTIM2 = 0;
    TIFR2 = _BV(TOV2);   //Clear Overflow FLAG
    TCNT2 = 0x00;        //Set Timer2 to 0
  }

  PstWin wi, wr;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pst_chan_timeout(&g_ch[0], now_ts4());
    pst_chan_timeout(&g_ch[1], now_ts5());
    pst_chan_take(&g_ch[0], &wi);
    pst_chan_take(&g_ch[1], &wr);
  }
  uint8_t st_i, st_r;
  uint32_t mhz_i = pst_diag_freq(&g_dI, &wi, &st_i);
  uint32_t mhz_r = pst_diag_freq(&g_dR, &wr, &st_r);

  PstSensors s;
  s.thermo = g_cmd.set.thermo;
  s.egt_c = c;
  s.lambda_temp = Processed_Data.Temperature_C;
  s.afr100 = Afr;

  char buf[144];
  size_t n;
  if (g_cmd.mode == 'D') {
    PstDiag d;
    d.cycle = cycle;
    d.t_ms = millis();
    d.rate = timer;
    d.ign_mhz = mhz_i;
    d.roll_mhz = mhz_r;
    d.ign = wi;
    d.roll = wr;
    uint8_t f = g_flags;
    if (wi.timeout) f |= PST_FL_TO_IGN;
    if (wr.timeout) f |= PST_FL_TO_ROLL;
    if (st_i) f |= PST_FL_ST_IGN;
    if (st_r) f |= PST_FL_ST_ROLL;
    if (g_burst.state != PST_B_IDLE || g_bout_active) f |= PST_FL_BURST;
    d.flags = f;
    n = pst_fmt_diag(buf, sizeof(buf), &d, &s);
    if (out_put(buf, n)) g_flags = 0;
  } else {
    n = pst_fmt_normal(buf, sizeof(buf), cycle, timer, pst_hz_ring(&wi), pst_hz_ring(&wr), &s);
    out_put(buf, n);
  }
  digitalWrite(2, LOW);
}

//////////////////////////////////////////////////////////////////
//------------------- MAIN --------------------------------------//
//////////////////////////////////////////////////////////////////
void loop() {
  out_drain();
  serial_in();

  // Timeout laufend pruefen (Zeitstempel laufen nach 268 s ueber)
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pst_chan_timeout(&g_ch[0], now_ts4());
    pst_chan_timeout(&g_ch[1], now_ts5());
  }

  jobs();
  telegram();
  out_drain();
}

//////////////////////////////////////////////////////////////////
//------------------- Interrupts --------------------------------//
//////////////////////////////////////////////////////////////////
ISR(TIMER2_OVF_vect) {
  if (ovlTIM2 != 0xFF) ovlTIM2++;
}

// Capture-/Ueberlauf-ISRs von Timer4/5: pst_isr.h (auch vom Taktzahl-Test test/avr_bench benutzt)
#include "pst_isr.h"
