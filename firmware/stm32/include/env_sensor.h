/*
 * Klimasensor BME280 / BMP280 an I2C1 (PB8 = SCL, PB7 = SDA).
 * Typ und Adresse (0x76/0x77) werden automatisch erkannt. Der Sensor misst
 * dauerhaft im Normal-Modus, Abfragen dauern daher nur ~1 ms.
 */
#ifndef ENV_SENSOR_H
#define ENV_SENSOR_H

#include <stdbool.h>

typedef enum { ENV_NONE = 0, ENV_BMP280, ENV_BME280 } env_type_t;

void env_init(void);

/* Temperatur [degC], Druck [Pa], Feuchte [%] (BMP280: 0). false = kein Sensor. */
bool env_read(float *t_c, float *p_pa, float *h_pct);

env_type_t env_type(void);
unsigned env_address(void);

#endif /* ENV_SENSOR_H */
