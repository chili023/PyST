#include "env_sensor.h"

#include "stm32f4xx_hal.h"

#define REG_ID        0xD0
#define REG_RESET     0xE0
#define REG_CTRL_HUM  0xF2
#define REG_CTRL_MEAS 0xF4
#define REG_CONFIG    0xF5
#define REG_DATA      0xF7
#define CHIP_BMP280   0x58
#define CHIP_BME280   0x60
#define I2C_TIMEOUT   10u

static I2C_HandleTypeDef hi2c1;
static env_type_t type;
static uint8_t addr;

static struct {
    uint16_t T1; int16_t T2, T3;
    uint16_t P1; int16_t P2, P3, P4, P5, P6, P7, P8, P9;
    uint8_t H1, H3; int16_t H2, H4, H5; int8_t H6;
} cal;

static bool rd(uint8_t reg, uint8_t *buf, uint16_t n)
{
    return HAL_I2C_Mem_Read(&hi2c1, (uint16_t)(addr << 1), reg, 1, buf, n, I2C_TIMEOUT) == HAL_OK;
}

static bool wr(uint8_t reg, uint8_t val)
{
    return HAL_I2C_Mem_Write(&hi2c1, (uint16_t)(addr << 1), reg, 1, &val, 1, I2C_TIMEOUT) == HAL_OK;
}

static void i2c_init(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_I2C1_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin = GPIO_PIN_7 | GPIO_PIN_8;           /* PB7 = SDA, PB8 = SCL */
    g.Mode = GPIO_MODE_AF_OD;
    g.Pull = GPIO_PULLUP;
    g.Speed = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF4_I2C1;
    HAL_GPIO_Init(GPIOB, &g);

    hi2c1.Instance = I2C1;
    hi2c1.Init.ClockSpeed = 100000;
    hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    HAL_I2C_Init(&hi2c1);
}

static bool probe(void)
{
    static const uint8_t addrs[] = {0x76, 0x77};
    for (unsigned i = 0; i < sizeof addrs; i++) {
        uint8_t id = 0;
        addr = addrs[i];
        if (!rd(REG_ID, &id, 1))
            continue;
        if (id == CHIP_BME280) { type = ENV_BME280; return true; }
        if (id == CHIP_BMP280) { type = ENV_BMP280; return true; }
    }
    type = ENV_NONE;
    return false;
}

static bool setup(void)
{
    uint8_t c[26];
    if (!wr(REG_RESET, 0xB6))
        return false;
    HAL_Delay(5);
    if (!rd(0x88, c, 26))
        return false;
    cal.T1 = (uint16_t)(c[1] << 8 | c[0]);
    cal.T2 = (int16_t)(c[3] << 8 | c[2]);
    cal.T3 = (int16_t)(c[5] << 8 | c[4]);
    cal.P1 = (uint16_t)(c[7] << 8 | c[6]);
    cal.P2 = (int16_t)(c[9] << 8 | c[8]);
    cal.P3 = (int16_t)(c[11] << 8 | c[10]);
    cal.P4 = (int16_t)(c[13] << 8 | c[12]);
    cal.P5 = (int16_t)(c[15] << 8 | c[14]);
    cal.P6 = (int16_t)(c[17] << 8 | c[16]);
    cal.P7 = (int16_t)(c[19] << 8 | c[18]);
    cal.P8 = (int16_t)(c[21] << 8 | c[20]);
    cal.P9 = (int16_t)(c[23] << 8 | c[22]);
    cal.H1 = c[25];

    if (type == ENV_BME280) {
        uint8_t h[7];
        if (!rd(0xE1, h, 7))
            return false;
        cal.H2 = (int16_t)(h[1] << 8 | h[0]);
        cal.H3 = h[2];
        cal.H4 = (int16_t)((int8_t)h[3] * 16 | (h[4] & 0x0F));
        cal.H5 = (int16_t)((int8_t)h[5] * 16 | (h[4] >> 4));
        cal.H6 = (int8_t)h[6];
        if (!wr(REG_CTRL_HUM, 0x01))           /* Feuchte x1 (vor CTRL_MEAS schreiben) */
            return false;
    }
    /* Standby 62,5 ms, IIR-Filter x4, Temperatur x2, Druck x16, Normal-Modus */
    if (!wr(REG_CONFIG, (1u << 5) | (2u << 2)))
        return false;
    return wr(REG_CTRL_MEAS, (2u << 5) | (5u << 2) | 3u);
}

void env_init(void)
{
    i2c_init();
    if (probe() && !setup())
        type = ENV_NONE;
}

env_type_t env_type(void) { return type; }
unsigned env_address(void) { return addr; }

bool env_read(float *t_c, float *p_pa, float *h_pct)
{
    if (type == ENV_NONE) {
        env_init();                            /* evtl. nachtraeglich angesteckt */
        if (type == ENV_NONE)
            return false;
        HAL_Delay(100);                        /* erste Messung abwarten */
    }

    uint8_t d[8];
    if (!rd(REG_DATA, d, type == ENV_BME280 ? 8 : 6)) {
        type = ENV_NONE;
        return false;
    }
    int32_t adc_p = (int32_t)d[0] << 12 | (int32_t)d[1] << 4 | d[2] >> 4;
    int32_t adc_t = (int32_t)d[3] << 12 | (int32_t)d[4] << 4 | d[5] >> 4;

    /* Kompensation nach Bosch-Datenblatt (Gleitkomma-Variante) */
    double v1 = ((double)adc_t / 16384.0 - (double)cal.T1 / 1024.0) * (double)cal.T2;
    double v2 = (double)adc_t / 131072.0 - (double)cal.T1 / 8192.0;
    v2 = v2 * v2 * (double)cal.T3;
    double t_fine = v1 + v2;
    *t_c = (float)(t_fine / 5120.0);

    v1 = t_fine / 2.0 - 64000.0;
    v2 = v1 * v1 * (double)cal.P6 / 32768.0;
    v2 = v2 + v1 * (double)cal.P5 * 2.0;
    v2 = v2 / 4.0 + (double)cal.P4 * 65536.0;
    v1 = ((double)cal.P3 * v1 * v1 / 524288.0 + (double)cal.P2 * v1) / 524288.0;
    v1 = (1.0 + v1 / 32768.0) * (double)cal.P1;
    if (v1 == 0.0)
        return false;
    double p = 1048576.0 - (double)adc_p;
    p = (p - v2 / 4096.0) * 6250.0 / v1;
    v1 = (double)cal.P9 * p * p / 2147483648.0;
    v2 = p * (double)cal.P8 / 32768.0;
    *p_pa = (float)(p + (v1 + v2 + (double)cal.P7) / 16.0);

    *h_pct = 0.0f;
    if (type == ENV_BME280) {
        int32_t adc_h = (int32_t)d[6] << 8 | d[7];
        double h = t_fine - 76800.0;
        h = ((double)adc_h - ((double)cal.H4 * 64.0 + (double)cal.H5 / 16384.0 * h)) *
            ((double)cal.H2 / 65536.0 *
             (1.0 + (double)cal.H6 / 67108864.0 * h * (1.0 + (double)cal.H3 / 67108864.0 * h)));
        h = h * (1.0 - (double)cal.H1 * h / 524288.0);
        if (h > 100.0) h = 100.0;
        if (h < 0.0) h = 0.0;
        *h_pct = (float)h;
    }
    return true;
}
