/* ============================================================================
 * test_critter_mem.c -- runtime conformance tests for UNIT 2 (memory unit).
 *
 * MODULE UNDER TEST: critter_mem.c, and nothing else. generate_temps.py is
 * excluded per the unit's own design record; no other unit's sources are
 * linked into this binary.
 *
 * HOW THIS REACHES THE CODE
 * -------------------------
 * Every function in critter_mem.c is `static`, so it has no external linkage
 * and cannot be called from another translation unit. Rather than modify the
 * artifact under review -- which would invalidate the review -- the source is
 * #included into this test file with its entry point renamed:
 *
 *     #define main critter_mem_main
 *     #include "critter_mem.c"
 *
 * The bytes of critter_mem.c are unchanged. Renaming main is done by the
 * preprocessor here, in the test, not in the unit.
 *
 * WHY SOME RULES ARE TESTED HERE AND OTHERS IN check_static.py
 * ------------------------------------------------------------
 * A rule is tested at runtime when a violation produces observable behaviour
 * (a wrong value, an out-of-bounds write, a lost data set). A rule is tested
 * statically when it constrains the SHAPE of the source and has no runtime
 * signature at all -- assertion density, function length, function pointers.
 * NASA P10 Rule 2 is the clearest case: it is violated when a tool *cannot
 * prove* a bound, which is a property of the text, not of any execution.
 * ========================================================================= */

#include "ct.h"

#include <stdint.h>
#include <limits.h>
#include <math.h>

/* The artifact under review, included verbatim with its entry point renamed. */
#define main critter_mem_main
#include "critter_mem.c"
#undef main

/* ---------------------------------------------------------------------------
 * MEM-01  ARR30-C. Do not form or use out-of-bounds pointers or array
 *         subscripts.
 *
 * summarize_by_month() computes  month_index = readings[i].month - 1  and uses
 * it to subscript a 12-element array, with no range check. parse_line()
 * accepts any integer sscanf will convert, so a log line reading
 * "13/01/2024 00:00:00,20.0" drives the subscript to 12, and "00/..." drives
 * it to -1.
 *
 * DETECTION METHOD: guard slots. The summary array handed to the unit is the
 * interior of a 14-element array, so subscripts -1 and 12 land on slots we
 * own and can inspect afterwards. This matters for rigour: the test observes
 * the out-of-bounds access WITHOUT itself committing undefined behaviour,
 * because arena[0] and arena[13] are inside the same array object as the
 * twelve slots the unit believes it was given.
 * ------------------------------------------------------------------------- */
#define GUARD_COUNT (-12345L)

static void test_arr30_month_subscript(void)
{
    MonthlySummary arena[NUM_MONTHS + 2];
    MonthlySummary *summary = &arena[1];   /* unit sees indices 0..11      */
    Reading rd[2];
    int i;
    int low_clobbered, high_clobbered;

    for (i = 0; i < NUM_MONTHS + 2; i++) {
        arena[i].count   = GUARD_COUNT;
        arena[i].sum     = -1.0;
        arena[i].minimum = -1.0;
        arena[i].maximum = -1.0;
    }

    /* month = 13 -> month_index = 12 -> one slot past the end */
    rd[0].month = 13; rd[0].day = 1; rd[0].year = 2024;
    rd[0].hour = 0; rd[0].minute = 0; rd[0].second = 0;
    rd[0].temperature = 20.0;

    /* month = 0 -> month_index = -1 -> one slot before the start */
    rd[1].month = 0; rd[1].day = 1; rd[1].year = 2024;
    rd[1].hour = 0; rd[1].minute = 0; rd[1].second = 0;
    rd[1].temperature = 21.0;

    summarize_by_month(rd, 2, summary);

    low_clobbered  = (arena[0].count  != GUARD_COUNT);
    high_clobbered = (arena[13].count != GUARD_COUNT);

    if (low_clobbered || high_clobbered) {
        snprintf(ct_buf, sizeof ct_buf,
                 "month=13 wrote summary[12]:%s  month=0 wrote summary[-1]:%s"
                 "  (12-element array, no range check at critter_mem.c:319)",
                 high_clobbered ? "yes" : "no",
                 low_clobbered  ? "yes" : "no");
        ct_rule("MEM-01", "SEI CERT C", "ARR30-C", 0, ct_buf);
    } else {
        ct_rule("MEM-01", "SEI CERT C", "ARR30-C", 1,
                "month field range-checked before subscripting");
    }
}

/* Confirm the month field really is unvalidated on the way in, so MEM-01 is
 * reachable from a log file and not only from a synthetic struct. */
static void test_parse_accepts_impossible_dates(void)
{
    Reading r;
    int ok = parse_line("13/45/2024 99:99:99,20.0\n", &r);

    if (ok && (r.month == 13 || r.day == 45 || r.hour == 99)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "parse_line accepted month=%d day=%d hour=%d min=%d sec=%d;"
                 " 7 sscanf conversions is the only acceptance criterion",
                 r.month, r.day, r.hour, r.minute, r.second);
        ct_rule("MEM-02", "NASA P10", "Rule 7", 0, ct_buf);
    } else {
        ct_rule("MEM-02", "NASA P10", "Rule 7", 1,
                "parse_line validates timestamp field ranges");
    }
}

/* ---------------------------------------------------------------------------
 * MEM-03  INT30-C. Ensure that unsigned integer operations do not wrap.
 *
 * critter_mem.c:471 allocates  (size_t)line_count * sizeof(Reading)  with no
 * check on the product. size_t is unsigned, so the multiplication wraps
 * silently rather than trapping.
 *
 * On the Critter's actual target (Pi 5, 64-bit userland) size_t is 64 bits and
 * the wrap needs ~5.8e17 readings, which is unreachable. The unit's own design
 * record calls the finding "purely theoretical" on that basis. This test
 * exists to show the finding is a property of the TYPE, not of the board: it
 * models a 32-bit size_t, which is what the same source gets on a 32-bit ARM
 * userland, and shows the product reaching zero.
 *
 * This is the "the value does not fit the type it is stored in" class of test:
 * the arithmetic is correct, the destination type is too narrow, and nothing
 * in the code notices.
 * ------------------------------------------------------------------------- */
static void test_int30_allocation_size_wrap(void)
{
    /* Model of size_t on a 32-bit userland. */
    typedef uint32_t size32_t;
    const size32_t SIZE32_MAX = 0xFFFFFFFFu;

    const size32_t elem = (size32_t)sizeof(Reading);
    const long     n    = 134217728L;            /* 2^27 readings           */

    size32_t as_written = (size32_t)n * elem;    /* what the unit computes  */
    int      guard_fires = ((size32_t)n > SIZE32_MAX / elem);

    /* sizeof(Reading) is 32 under both LP64 and ILP32, so the wrap point is
     * the same number of readings on either ABI. Assert it rather than
     * assuming it. */
    if (elem != 32u) {
        ct_rule("MEM-03", "SEI CERT C", "INT30-C", 0,
                ct_evl("unexpected sizeof(Reading)", (long)elem));
        return;
    }

    if (as_written == 0u && guard_fires) {
        snprintf(ct_buf, sizeof ct_buf,
                 "32-bit size_t: %ld readings x %u B wraps to %u; a guard "
                 "(n > SIZE_MAX/elem) would have caught it. malloc then "
                 "succeeds on a 0-byte request and read_readings walks off "
                 "the buffer. critter_mem.c:471,472 perform no such check",
                 n, (unsigned)elem, (unsigned)as_written);
        ct_rule("MEM-03", "SEI CERT C", "INT30-C", 0, ct_buf);
    } else {
        ct_rule("MEM-03", "SEI CERT C", "INT30-C", 1,
                "allocation size is checked against overflow");
    }
}

/* ---------------------------------------------------------------------------
 * MEM-04  EXP34-C. Do not dereference null pointers.
 *
 * Expected to PASS. All three allocation sites in critter_mem.c check for
 * NULL before use. This test forces the compute_median() allocation to fail
 * and confirms the function returns instead of dereferencing.
 *
 * A suite that only ever reports violations cannot discriminate, so cases the
 * unit gets right are tested with the same machinery as cases it gets wrong.
 * ------------------------------------------------------------------------- */
static void test_exp34_median_alloc_failure(void)
{
    /* Large enough that malloc must fail, small enough that count*sizeof
     * (double) does not itself overflow size_t and quietly succeed. */
    const long huge = (long)(LONG_MAX / 32);
    double m;

    fprintf(stderr, "[test] expecting one \"out of memory\" line below:\n");
    m = compute_median(NULL, huge);   /* readings never touched on this path */

    if (m == 0.0) {
        ct_rule("MEM-04", "SEI CERT C", "EXP34-C", 1,
                "compute_median returns on allocation failure without "
                "dereferencing the null result");
    } else {
        ct_rule("MEM-04", "SEI CERT C", "EXP34-C", 0,
                ct_ev1("unexpected return on allocation failure", m));
    }

    /* Same event, different rule: the value it returns on failure is 0.0,
     * which is a physically plausible Celsius median. The caller at
     * critter_mem.c:526 stores it and prints it; no return code, no sentinel,
     * no errno. NASA P10 Rule 7 requires the caller to check the return value
     * of a nonvoid function, and here there is nothing checkable to check. */
    snprintf(ct_buf, sizeof ct_buf,
             "compute_median signals allocation failure by returning 0.0 "
             "degC, indistinguishable from a valid median; caller at "
             "critter_mem.c:526 has no way to detect it");
    ct_rule("MEM-05", "NASA P10", "Rule 7", 0, ct_buf);
}

/* ---------------------------------------------------------------------------
 * MEM-06  NASA P10 Rule 7 -- "each called function must check the validity of
 *         all parameters provided by the caller."
 *
 * remove_outliers() divides by the std_dev it is handed and never checks it.
 * A stuck sensor reporting one constant value gives std_dev == 0.0, so every
 * z-score is 0.0/0.0 == NaN, and NaN <= 3.0 is false for every reading.
 *
 * Consequence: the unit discards the ENTIRE data set and reports it as
 * outlier removal. For a machine-room temperature monitor, a stuck sensor is
 * the failure mode most worth surviving, and this is the input that erases
 * the log.
 * ------------------------------------------------------------------------- */
static void test_rule7_zero_std_dev(void)
{
    enum { N = 8 };
    Reading src[N], kept[N];
    double mean, sd, kept_mean;
    long i, k;

    for (i = 0; i < N; i++) {
        src[i].month = (int)i + 1; src[i].day = 1; src[i].year = 2024;
        src[i].hour = 0; src[i].minute = 0; src[i].second = 0;
        src[i].temperature = 22.0;          /* stuck sensor */
    }

    mean = compute_mean(src, N);
    sd   = compute_std_dev(src, N, mean);
    k    = remove_outliers(src, N, mean, sd, kept);

    if (sd == 0.0 && k != N) {
        kept_mean = compute_mean(kept, k);        /* 0.0/0.0 when k == 0 */
        snprintf(ct_buf, sizeof ct_buf,
                 "constant input: std_dev=%.1f, z=NaN, kept %ld of %d "
                 "readings; downstream mean=%s. remove_outliers does not "
                 "validate std_dev (critter_mem.c:260)",
                 sd, k, (int)N, isnan(kept_mean) ? "NaN" : "finite");
        ct_rule("MEM-06", "NASA P10", "Rule 7", 0, ct_buf);
    } else {
        ct_rule("MEM-06", "NASA P10", "Rule 7", 1,
                ct_ev2("std_dev", sd, "kept", (double)k));
    }
}

/* ---------------------------------------------------------------------------
 * MEM-07  FLP30-C. Do not use floating-point variables as loop counters.
 *         Expected to PASS -- every loop in the unit counts with long or int.
 *
 * MEM-08  INT33-C. Ensure that division and remainder operations do not
 *         result in divide-by-zero errors. Recorded NA, with the reason,
 *         because this is where the two standards leave a hole.
 * ------------------------------------------------------------------------- */
static void test_flp30_and_the_int33_gap(void)
{
    ct_rule("MEM-07", "SEI CERT C", "FLP30-C", 1,
            "loop counters are long/int throughout; verified by inspection "
            "of all 9 loops and by check_static.py");

    ct_na("MEM-08", "SEI CERT C", "INT33-C",
          "every division by a possibly-zero count in this unit "
          "(compute_mean:181, compute_std_dev:194, summary mean:382,412) is "
          "floating-point. INT33-C constrains integer division only. The "
          "2016 edition contains no rule for float divide-by-zero: FLP03-C "
          "appears only as a cross-reference inside FLP32-C, not as a rule. "
          "Covered here by NASA P10 Rule 7 (MEM-06) instead");
}

int main(void)
{
    ct_begin("unit2/memory");

    test_arr30_month_subscript();
    test_parse_accepts_impossible_dates();
    test_int30_allocation_size_wrap();
    test_exp34_median_alloc_failure();
    test_rule7_zero_std_dev();
    test_flp30_and_the_int33_gap();

    return ct_end();
}