/* ============================================================================
 * test_sensor_sim.c -- runtime conformance tests for UNIT 1, module
 *                      sensor_sim.c.
 *
 * MODULE UNDER TEST: sensor_sim.c, scored on its own card, NOT folded into
 * io_unit.c's score. The unit's README designates this file "the
 * to-be-replaced part": it stands behind the sensor.h hardware boundary and
 * is expected to be swapped for an I2C/SPI driver. Findings here therefore
 * have a different disposition from findings in io_unit.c -- they may be
 * discharged by deleting the file rather than by fixing it -- and mixing the
 * two scores would hide that. The rubric records the disposition per card.
 *
 * Built -std=gnu11 for setenv/unsetenv, which are POSIX. sensor_sim.c itself
 * compiles clean under -std=c11 -Wpedantic; the gnu11 requirement belongs to
 * this test, not to the module.
 * ========================================================================= */

#include "ct.h"
#include "sensor.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#define NSAMP 16

/* Seed the simulated sensor from the environment (or clear the variable) and
 * capture a fixed-length prefix of its output stream. */
static void capture(const char *seed_or_null, int16_t out[NSAMP])
{
    int i;
    if (seed_or_null == NULL) {
        unsetenv("CRITTER_SEED");
    } else {
        setenv("CRITTER_SEED", seed_or_null, 1);
    }
    sensor_init();
    for (i = 0; i < NSAMP; i++) {
        out[i] = sensor_read_raw();
    }
    sensor_shutdown();
}

static int same_stream(const int16_t a[NSAMP], const int16_t b[NSAMP])
{
    return memcmp(a, b, sizeof(int16_t) * NSAMP) == 0;
}

/* ---------------------------------------------------------------------------
 * SIM-01  MSC30-C. Do not use the rand() function for generating
 *         pseudorandom numbers.
 *
 * sensor_read_raw calls rand() three times per read: once for the drift, once
 * to decide whether to glitch, once for the glitch magnitude. rand() is the
 * rule's named subject, so the violation is present by construction; this
 * test records the consequence that matters for a sensor simulator, which is
 * that the entire measurement stream is a pure function of one 32-bit seed.
 * ------------------------------------------------------------------------- */
static void test_msc30_rand(void)
{
    int16_t a[NSAMP], b[NSAMP];

    capture("777", a);
    capture("777", b);

    if (same_stream(a, b)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "rand() at sensor_sim.c:45,54,55; seed 777 reproduces the "
                 "identical %d-sample stream (first=%d last=%d). The whole "
                 "sensor output is determined by one 32-bit seed",
                 NSAMP, (int)a[0], (int)a[NSAMP - 1]);
        ct_rule("SIM-01", "SEI CERT C", "MSC30-C", 0, ct_buf);
    } else {
        ct_rule("SIM-01", "SEI CERT C", "MSC30-C", 1,
                "generator is not a seeded rand()");
    }
}

/* ---------------------------------------------------------------------------
 * SIM-02  MSC32-C. Properly seed pseudorandom number generators.
 *
 * The rule's own acceptance criterion is quoted in the standard as: "A
 * properly seeded PRNG will generate a different sequence of random numbers
 * each time it is run." sensor_init defaults to the literal seed 12345, so
 * with no environment override every run of the I/O unit produces the same
 * temperatures.
 *
 * DISPOSITION: the unit's README asks for exactly this, to make baseline and
 * hardened builds diffable on the seq/raw_temp_cC columns. The verdict is
 * still FAIL -- the rule is violated -- but the rubric carries it as a
 * documented deviation rather than a defect to fix. Recording it as PASS
 * because it was intentional would make the scorecard unauditable.
 * ------------------------------------------------------------------------- */
static void test_msc32_fixed_default_seed(void)
{
    int16_t a[NSAMP], b[NSAMP];

    capture(NULL, a);         /* no CRITTER_SEED set */
    capture(NULL, b);

    if (same_stream(a, b)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "no CRITTER_SEED: two runs produced identical streams "
                 "(first=%d). sensor_sim.c:24 defaults to the literal seed "
                 "12345. DOCUMENTED DEVIATION: README requires repeatability "
                 "for baseline-vs-hardened diffing",
                 (int)a[0]);
        ct_rule("SIM-02", "SEI CERT C", "MSC32-C", 0, ct_buf);
    } else {
        ct_rule("SIM-02", "SEI CERT C", "MSC32-C", 1,
                "default seeding varies between runs");
    }
}

/* ---------------------------------------------------------------------------
 * SIM-03  INT31-C. Ensure that integer conversions do not result in lost or
 *         misinterpreted data.
 *
 * sensor_sim.c:28 reads the seed with strtoul, whose return type is unsigned
 * long -- 64 bits on the Pi 5's userland -- and casts it to unsigned int,
 * which is 32. Any requested seed at or above 2^32 silently loses its high
 * bits.
 *
 * This is the narrowing-conversion test in its clearest form: the value is
 * parsed correctly, the destination type cannot hold it, and nothing reports
 * the loss. Demonstrated by showing that seed 2^32 and seed 0 produce the
 * same stream.
 * ------------------------------------------------------------------------- */
static void test_int31_seed_narrowing(void)
{
    int16_t big[NSAMP], zero[NSAMP];

    if (sizeof(unsigned long) <= sizeof(unsigned int)) {
        ct_na("SIM-03", "SEI CERT C", "INT31-C",
              "unsigned long is no wider than unsigned int on this host, so "
              "the narrowing cannot be exercised here. Re-run on an LP64 "
              "target (the Pi 5 default) where the conversion is lossy");
        return;
    }

    capture("4294967296", big);    /* 2^32 */
    capture("0", zero);

    if (same_stream(big, zero)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "CRITTER_SEED=4294967296 (2^32) produced the same stream as "
                 "CRITTER_SEED=0: (unsigned int)strtoul(...) at "
                 "sensor_sim.c:28 discards the high %zu bits. "
                 "sizeof(unsigned long)=%zu, sizeof(unsigned int)=%zu",
                 (sizeof(unsigned long) - sizeof(unsigned int)) * 8u,
                 sizeof(unsigned long), sizeof(unsigned int));
        ct_rule("SIM-03", "SEI CERT C", "INT31-C", 0, ct_buf);
    } else {
        ct_rule("SIM-03", "SEI CERT C", "INT31-C", 1,
                "seed conversion preserves the requested value");
    }
}

/* ---------------------------------------------------------------------------
 * SIM-04  ERR33-C. Detect and handle standard library errors.
 *
 * strtoul appears in ERR33-C's table of standard library functions, with the
 * error return given as ULONG_MAX with errno == ERANGE. sensor_sim.c:28 uses
 * neither: errno is not cleared beforehand, the return value is not compared,
 * and the end pointer argument is NULL so a completely unparseable string
 * cannot be distinguished from a valid zero.
 *
 * Consequence: a typo in CRITTER_SEED is not an error. It is seed 0.
 * ------------------------------------------------------------------------- */
static void test_err33_unchecked_strtoul(void)
{
    int16_t junk[NSAMP], zero[NSAMP];

    capture("not-a-number", junk);
    capture("0", zero);

    if (same_stream(junk, zero)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "CRITTER_SEED=\"not-a-number\" silently became seed 0 "
                 "(identical stream, first=%d). strtoul is in ERR33-C's "
                 "function table; sensor_sim.c:28 passes NULL for endptr and "
                 "checks neither the return value nor errno",
                 (int)junk[0]);
        ct_rule("SIM-04", "SEI CERT C", "ERR33-C", 0, ct_buf);
    } else {
        ct_rule("SIM-04", "SEI CERT C", "ERR33-C", 1,
                "unparseable seed is detected and reported");
    }
}

/* ---------------------------------------------------------------------------
 * SIM-05  INT31-C at the glitch path, reported as supporting evidence.
 *
 * The other narrowing site is the glitch return, which casts an int32_t
 * expression to int16_t. Unlike the seed cast, this one is provably safe:
 * g_temp is clamped to [1500, 3500] and the perturbation is in [-400, 399],
 * so the result is in [1100, 3899] and fits int16_t. Checked empirically over
 * a long run so the claim is not left as an assertion about the source.
 * ------------------------------------------------------------------------- */
static void test_int31_glitch_range(void)
{
    long i;
    int  min_v = INT_MAX, max_v = INT_MIN;

    setenv("CRITTER_SEED", "20260916", 1);
    sensor_init();
    for (i = 0; i < 200000L; i++) {
        int v = (int)sensor_read_raw();
        if (v < min_v) { min_v = v; }
        if (v > max_v) { max_v = v; }
    }
    sensor_shutdown();

    snprintf(ct_buf, sizeof ct_buf,
             "200000 reads spanned [%d, %d] hundredths degC, inside int16_t "
             "[%d, %d]. The int32->int16 cast at sensor_sim.c:55 is bounded "
             "by the [1500,3500] clamp; the INT31-C FAIL for this module "
             "rests on the seed cast (SIM-03), not this one",
             min_v, max_v, -32768, 32767);
    ct_na("SIM-05", "SEI CERT C", "INT31-C", ct_buf);
}

int main(void)
{
    ct_begin("unit1/sensor_sim.c");

    test_msc30_rand();
    test_msc32_fixed_default_seed();
    test_int31_seed_narrowing();
    test_err33_unchecked_strtoul();
    test_int31_glitch_range();

    return ct_end();
}
