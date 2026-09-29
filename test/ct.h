/* ============================================================================
 * ct.h -- Critter conformance-test harness.
 *
 * Zero dependencies: C11 + stdio only. No Unity, no CMocka, no CUnit. The
 * target platform (Raspberry Pi 5, Raspberry Pi OS) ships build-essential
 * and python3 and nothing else, so the suite must build with the same
 * compiler the unit under review is built with.
 *
 * SCOPE NOTE: this harness is TEST SCAFFOLDING and is excluded from the
 * safety review, in the same way generate_temps.py is excluded (see the
 * memory unit's design-decision record, Section 2). Do not count findings
 * in this file against any unit.
 *
 * Every check emits exactly one machine-readable verdict line:
 *
 *   CT|<module>|<test_id>|<standard>|<rule>|<verdict>|<evidence>
 *
 * verdict is one of:
 *   PASS  the module satisfies the rule, and this test demonstrates it
 *   FAIL  the module violates the rule, and this test demonstrates it
 *   NA    the rule has no applicable construct in this module
 *
 * scorecard.py consumes these lines. Nothing else parses program output,
 * so a test that crashes is reported as a missing verdict rather than
 * being silently scored as a pass.
 * ========================================================================= */
#ifndef CT_H
#define CT_H

#include <stdio.h>
#include <string.h>

#define CT_EVID 512

static const char *ct_module = "unset";
static int ct_n_pass = 0;
static int ct_n_fail = 0;
static int ct_n_na   = 0;

static inline void ct_begin(const char *module)
{
    ct_module = module;
    printf("CT_MODULE_BEGIN|%s\n", module);
    fflush(stdout);
}

/* Emit one verdict. 'satisfied' is the answer to "does the module obey the
 * rule?", NOT "did the test program work". A test that successfully
 * demonstrates a violation still reports FAIL, because the verdict is about
 * the artifact under review and not about the test. */
static inline void ct_rule(const char *test_id, const char *standard,
                    const char *rule, int satisfied, const char *evidence)
{
    const char *v = satisfied ? "PASS" : "FAIL";
    if (satisfied) { ct_n_pass++; } else { ct_n_fail++; }
    printf("CT|%s|%s|%s|%s|%s|%s\n",
           ct_module, test_id, standard, rule, v, evidence);
    fflush(stdout);
}

/* The rule has nothing to bite on in this module. Recorded explicitly so the
 * scorecard can distinguish "clean" from "never looked". */
static inline void ct_na(const char *test_id, const char *standard,
                  const char *rule, const char *why)
{
    ct_n_na++;
    printf("CT|%s|%s|%s|%s|NA|%s\n", ct_module, test_id, standard, rule, why);
    fflush(stdout);
}

/* A defect the test demonstrates for which NEITHER standard supplies a rule.
 * Counted separately and excluded from the rule score, because these are
 * exactly the findings a rule-based review cannot surface -- which is the
 * point worth measuring. */
static int ct_n_unmapped = 0;
static inline void ct_unmapped(const char *test_id, const char *evidence)
{
    ct_n_unmapped++;
    printf("CT|%s|%s|(neither)|UNMAPPED|FINDING|%s\n",
           ct_module, test_id, evidence);
    fflush(stdout);
}

static inline int ct_end(void)
{
    printf("CT_MODULE_END|%s|pass=%d|fail=%d|na=%d|unmapped=%d\n",
           ct_module, ct_n_pass, ct_n_fail, ct_n_na, ct_n_unmapped);
    fflush(stdout);
    /* Exit 0 even when rules are violated: a violation is a RESULT, not a
     * harness error. run_all.sh treats a nonzero exit as "the test itself
     * broke", which is a different and more urgent condition. */
    return 0;
}

/* Build a one-line evidence string. Kept as a function rather than a
 * variadic macro so the harness does not itself need __VA_ARGS__. */
static char ct_buf[CT_EVID];
static inline const char *ct_ev2(const char *a, double x, const char *b, double y)
{
    snprintf(ct_buf, sizeof ct_buf, "%s=%.17g %s=%.17g", a, x, b, y);
    return ct_buf;
}
static inline const char *ct_ev1(const char *a, double x)
{
    snprintf(ct_buf, sizeof ct_buf, "%s=%.17g", a, x);
    return ct_buf;
}
static inline const char *ct_evl(const char *a, long x)
{
    snprintf(ct_buf, sizeof ct_buf, "%s=%ld", a, x);
    return ct_buf;
}

#endif /* CT_H */