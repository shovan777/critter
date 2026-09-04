/* sensor_sim.c
 *
 * Software simulation of the temperature sensor. This file stands
 * in for a real hardware driver during prototyping. When a physical
 * sensor is available, this whole file is swapped out for a driver
 * that implements the same three functions from sensor.h.
 *
 * The simulation produces a slow random walk around room temperature
 * and occasionally emits a glitched reading, so the downstream
 * memory unit has outliers to clean up.
 */

#include "sensor.h"

#include <stdlib.h>

/* current simulated temperature, in hundredths of a degree C */
static int32_t g_temp = 2200;
static int g_ready = 0;

int sensor_init(void)
{
    const char *seed_env = getenv("CRITTER_SEED");
    unsigned int seed = 12345;   /* fixed default => repeatable runs */

    if (seed_env != NULL) {
        seed = (unsigned int) strtoul(seed_env, NULL, 10);
    }

    srand(seed);
    g_temp = 2200;
    g_ready = 1;
    return 0;
}

int16_t sensor_read_raw(void)
{
    int delta;

    /* drift a little on each read */
    delta = (rand() % 11) - 5;
    g_temp += delta;

    /* keep it inside a believable band */
    if (g_temp < 1500)
        g_temp = 1500;
    if (g_temp > 3500)
        g_temp = 3500;

    /* every so often the sensor glitches and reports garbage */
    if ((rand() % 200) == 0) {
        return (int16_t)(g_temp + (rand() % 800) - 400);
    }

    return (int16_t) g_temp;
}

void sensor_shutdown(void)
{
    g_ready = 0;
}
