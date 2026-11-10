// Sitzung wie PyST sie mit dem Mega fuehrt, mit dem Kern und der Reihenfolge aus loop() (PST_V3.2.0.ino):
// #?, #AI 10, 'm', Telegramme N, #MODE D, Telegramme D, #AI 0. Je ms: ADC-Ergebnisse uebernehmen
// (ca. 9 Wandlungen, 104 us), alle 16 ms ein Telegramm, dann die A-Zeile, wenn sie faellig ist.
// Ausgabe auf stdout; PyST/tests/test_firmware_analog.py schickt sie durch den Parser von PyST.
//   g++ -std=c++11 -o host_session host_session.cpp ../pst_core.cpp && ./host_session
#include "../pst_core.h"
#include <stdio.h>
#include <string.h>

static PstCmd cmd;
static PstAdc adc;
static PstAiClock clk;
static uint8_t adc_on;
static uint32_t ms = 0xFFFFFC18UL;          // millis() laeuft nach 1 s ueber
static uint16_t cycle;

static void put(const char *b, size_t n) { fwrite(b, 1, n, stdout); }

static void line(const char *l)
{
  char out[160];
  put(out, pst_cmd_line(&cmd, l, out, sizeof(out)));
  if (cmd.req_ai) {                          // wie handle_requests()
    pst_ai_start(&clk, ms);
    if (cmd.ai_hz && !adc_on) pst_adc_reset(&adc);
    adc_on = cmd.ai_hz ? 1 : 0;
    cmd.req_ai = 0;
  }
}

// Eingang k liegt auf 1023 - 60 k (A0 = 4,995 V ... A15 = 0,601 V)
static uint16_t raw_of(uint8_t ch) { return (uint16_t)(1023 - 60 * ch); }

static void run(int n_ms)
{
  for (int i = 0; i < n_ms; i++) {
    ms++;
    if (adc_on)
      for (int k = 0; k < 9; k++) pst_adc_result(&adc, raw_of(adc.ch));
    if (cmd.mess && ms % 16 == 0) {
      char b[160];
      size_t n;
      PstSensors s; s.thermo = 0; s.egt_c = 0; s.lambda_temp = 740; s.afr100 = 1470;
      cycle++;
      if (cmd.mode == 'D') {
        PstDiag d; memset(&d, 0, sizeof(d));
        d.cycle = cycle; d.t_ms = ms; d.rate = 61.04f; d.ign_mhz = 50000; d.roll_mhz = 725500;
        d.ign.edges = 1; d.ign.periods = 1; d.ign.pmin = d.ign.pmax = 320000;
        d.roll.edges = 12; d.roll.periods = 12; d.roll.pmin = 22040; d.roll.pmax = 22060;
        n = pst_fmt_diag(b, sizeof(b), &d, &s);
      } else {
        n = pst_fmt_normal(b, sizeof(b), cycle, 61.04f, 50, 726, &s);
      }
      put(b, n);
    }
    if (adc_on && pst_ai_due(&clk, cmd.ai_hz, ms)) {
      char b[PST_AI_LINE_MAX];
      put(b, pst_fmt_analog(b, sizeof(b), ms, &adc));
    }
  }
}

int main()
{
  pst_cmd_init(&cmd);
  line("#?");
  line("#AI 10");
  pst_cmd_char(&cmd, 'm');
  run(1500);
  line("#MODE D");
  run(1000);
  line("#AI 0");
  run(500);
  return 0;
}
