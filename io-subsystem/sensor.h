#ifndef SENSOR_H
#define SENSOR_H

#include <stdint.h>

/* ============================================================
 *  HARDWARE BOUNDARY
 *
 *  Everything behind this interface represents the physical
 *  temperature sensor. For the prototype it is implemented by
 *  sensor_sim.c, a pure-software simulation.
 *
 *  To move to real hardware, replace the implementation file
 *  (e.g. with an I2C/SPI driver for the actual sensor) while
 *  keeping these three prototypes unchanged. Nothing in the
 *  I/O unit (io_unit.c) should need to change.
 * ============================================================ */

/* Initialise the sensor.
 * Returns 0 on success, non-zero on error. */
int sensor_init(void);

/* Read one raw sample from the sensor.
 * Value is temperature in hundredths of a degree Celsius,
 * e.g. 2237 means 22.37 C. */
int16_t sensor_read_raw(void);

/* Release any resources held by the sensor. */
void sensor_shutdown(void);

#endif /* SENSOR_H */
