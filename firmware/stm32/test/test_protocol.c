/*
 * Host-Test des PC-Protokolls mit Hardware-Attrappen. Gibt die gesendeten
 * Bytes auf stdout aus; test/check_protocol.py wertet sie wie LabVIEW aus.
 *   cc -std=c11 -Iinclude -Itest/stub src/protocol.c src/fmt.c test/test_protocol.c -o /tmp/tp
 */
#include <stdio.h>
#include <string.h>

#include "analog.h"
#include "board.h"
#include "capture.h"
#include "egt.h"
#include "env_sensor.h"
#include "protocol.h"
#include "serial.h"

static uint32_t tick;
static const char *rx;
static size_t rx_pos, rx_len;
static uint32_t rate_mhz;
static uint32_t rx_tick;

uint32_t HAL_GetTick(void) { return tick; }
bool serial_write(const void *d, size_t n) { fwrite(d, 1, n, stdout); return true; }
bool serial_write_str(const char *s) { return serial_write(s, strlen(s)); }
int serial_read(void) { return rx_pos < rx_len ? (unsigned char)rx[rx_pos++] : -1; }
uint32_t serial_last_rx_tick(void) { return rx_tick; }
bool serial_take_break(void) { return false; }
bool egt_get(unsigned ch, float *t) { *t = ch ? 612.25f : 655.75f; return true; }
bool egt_enabled(unsigned ch) { (void)ch; return true; }
bool env_read(float *t, float *p, float *h) { *t = 21.46f; *p = 96512.4f; *h = 45.25f; return true; }
void env_init(void) {}
env_type_t env_type(void) { return ENV_BME280; }
unsigned env_address(void) { return 0x76; }
uint16_t analog_read(unsigned ch) { return ch == 1 ? 2048 : 1024; }
float analog_to_afr(uint16_t raw) { return 10.0f + 10.0f * raw / 4096.0f; }
const char *board_clock_source(void) { return "Test"; }
void capture_set_rate_mhz(uint32_t r) { rate_mhz = r; }
void capture_get_stats(capture_stats_t *s) { memset(s, 0, sizeof *s); }

static void send_cmd(const char *c, size_t n)
{
    rx = c; rx_pos = 0; rx_len = n; rx_tick = tick;
    protocol_poll();
    tick += 10;
    protocol_poll();
}

static void frames(int n, float ign, float roll)
{
    for (int i = 0; i < n; i++) {
        capture_frame_t f = {(float)rate_mhz / 1000.0f, ign, roll};
        protocol_on_frame(&f);
    }
}

int main(int argc, char **argv)
{
    protocol_init();
    if (argc > 1 && strcmp(argv[1], "legacy") == 0) {
        send_cmd("tttttttt", 8);
        send_cmd("eeeeeeee", 8);
        send_cmd("m3101001", 8);
        frames(3, 50.0f, 725.5f);
    } else {
        send_cmd("e\n", 2);
        send_cmd("m\n", 2);
        frames(3, 50.0f, 725.5f);
        send_cmd("m\n", 2);      /* zweiter Lauf ohne Reset: muss weiterlaufen */
        frames(2, 100.0f, 1000.0f);
        frames(5 * 60 + 1, 0.0f, 0.0f);   /* Autostopp nach 5 s ohne Signal */
        frames(2, 50.0f, 725.5f);          /* darf nichts mehr senden */
    }
    return 0;
}
