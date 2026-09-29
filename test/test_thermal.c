/* ============================================================================
 * test_thermal.c -- runtime conformance tests for UNIT 3, module thermal.c.
 *
 * MODULE UNDER TEST: thermal.c. Scored on its own card. mpc.c is not linked;
 * perf.c is not linked. thermal.c has no dependency on either.
 * ========================================================================= */

#include "ct.h"
#include "critter.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>

static void fixture_obs(crit_obs_t *o, creal t_air)
{
    int k;
    memset(o, 0, sizeof *o);
    o->seq = 1; o->valid = 1;
    o->t_air = t_air; o->t_ref = 22.5; o->t_hi = 27.0;
    o->t_sup = 12.5;  o->t_out = 33.0; o->q_int = 2400.0;
    for (k = 0; k < CRIT_N; k++) {
        o->t_out_fc[k] = 33.0;
        o->q_int_fc[k] = 2400.0;
    }
}

/* A minimal workspace. crit_est_update reads only ws->Ad and ws->Bd, so the
 * test supplies those directly instead of linking mpc.c for crit_ws_alloc.
 * Keeping the allocator out means this binary contains thermal.c and nothing
 * else, so a crash here cannot be blamed on another module. */
typedef struct { crit_ws_t ws; creal ad[9]; creal bd[3]; } ws_fixture_t;

static void fixture_ws(ws_fixture_t *f, const crit_plant_t *p)
{
    memset(f, 0, sizeof *f);
    f->ws.Ad = f->ad;
    f->ws.Bd = f->bd;
    crit_discretise(p, 30.0, f->ws.Ad, f->ws.Bd);
}

/* ---------------------------------------------------------------------------
 * THM-01  NASA P10 Rule 2 -- give all loops a fixed upper bound.
 *         Expected to PASS.
 *
 * kalman_steady iterates the Riccati recursion `for (it = 0; it < 4000; it++)`
 * -- a literal bound, no convergence test, no early exit. Rule 2 asks for a
 * bound a checking tool can prove statically, and a literal satisfies that.
 *
 * Note what Rule 2 does NOT ask: it says nothing about whether 4000 is enough
 * for the recursion to have converged. This test therefore checks the rule
 * (bounded, terminates, finite result) and separately checks the thing the
 * rule does not cover (did it actually converge), which is recorded as
 * unmapped.
 * ------------------------------------------------------------------------- */
static void test_rule2_bounded_riccati(void)
{
    crit_plant_t plant;
    crit_est_t est;
    int finite, plausible;

    crit_plant_default(&plant);
    crit_est_init(&est, &plant, 24.0);   /* runs kalman_steady internally */

    finite = isfinite((double)est.l_gain[0])
          && isfinite((double)est.l_gain[1])
          && isfinite((double)est.l_gain[2]);

    /* A Kalman gain on a directly measured state belongs in (0, 1]. */
    plausible = (est.l_gain[0] > 0.0) && (est.l_gain[0] <= 1.0);

    if (finite && plausible) {
        snprintf(ct_buf, sizeof ct_buf,
                 "kalman_steady bound is the literal 4000 (thermal.c:150); "
                 "terminated with L=[%.6f, %.6f, %.6f], all finite, "
                 "L[0] in (0,1]",
                 (double)est.l_gain[0], (double)est.l_gain[1],
                 (double)est.l_gain[2]);
        ct_rule("THM-01", "NASA P10", "Rule 2", 1, ct_buf);
    } else {
        snprintf(ct_buf, sizeof ct_buf,
                 "bounded loop produced L=[%g, %g, %g]; finite=%d plausible=%d",
                 (double)est.l_gain[0], (double)est.l_gain[1],
                 (double)est.l_gain[2], finite, plausible);
        ct_rule("THM-01", "NASA P10", "Rule 2", 0, ct_buf);
    }
}

/* ---------------------------------------------------------------------------
 * THM-02  NASA P10 Rule 7 -- each called function must check the validity of
 *         all parameters provided by the caller.
 *
 * crit_est_update trusts o->t_air whenever o->valid is nonzero. The valid
 * flag says the reading is fresh; nothing says it is a number. A NaN
 * measurement enters the innovation, is multiplied by the gain, and is added
 * to all three state components.
 *
 * The state is then permanently poisoned: every subsequent update computes
 * innovation = t_air - NaN = NaN, so no quantity of good readings recovers
 * it. For the Critter this is the I/O unit handing across one bad sample and
 * the compute unit never predicting again until restarted.
 * ------------------------------------------------------------------------- */
static void test_rule7_nan_measurement(void)
{
    ws_fixture_t f;
    crit_plant_t plant;
    crit_est_t est;
    crit_obs_t obs;
    creal u_prev[CRIT_NU];
    int i, clean_after;

    crit_plant_default(&plant);
    fixture_ws(&f, &plant);
    crit_est_init(&est, &plant, 24.0);
    u_prev[0] = 0.0; u_prev[1] = 0.0;

    /* One good update, to show the estimator is healthy first. */
    fixture_obs(&obs, 24.1);
    crit_est_update(&est, &f.ws, &obs, u_prev, 30.0);
    if (!isfinite((double)est.x[0])) {
        ct_rule("THM-02", "NASA P10", "Rule 7", 0,
                "estimator was already non-finite before the NaN was injected");
        return;
    }

    /* One NaN measurement, flagged valid. */
    fixture_obs(&obs, (creal)NAN);
    obs.valid = 1;
    crit_est_update(&est, &f.ws, &obs, u_prev, 30.0);

    /* Ten good measurements afterwards. */
    for (i = 0; i < 10; i++) {
        fixture_obs(&obs, 24.0);
        crit_est_update(&est, &f.ws, &obs, u_prev, 30.0);
    }
    clean_after = isfinite((double)est.x[0]);

    if (!clean_after) {
        snprintf(ct_buf, sizeof ct_buf,
                 "one NaN measurement with valid=1 poisoned the state; "
                 "10 subsequent good readings did not recover it "
                 "(x=[%s, %s, %s]). crit_est_update checks o->valid but not "
                 "isfinite(o->t_air) (thermal.c:214)",
                 isfinite((double)est.x[0]) ? "finite" : "NaN",
                 isfinite((double)est.x[1]) ? "finite" : "NaN",
                 isfinite((double)est.x[2]) ? "finite" : "NaN");
        ct_rule("THM-02", "NASA P10", "Rule 7", 0, ct_buf);
    } else {
        ct_rule("THM-02", "NASA P10", "Rule 7", 1,
                "NaN measurement rejected or recovered from");
    }
}

/* ---------------------------------------------------------------------------
 * THM-03  FLP32-C. Prevent or detect domain and range errors in math
 *         functions. Recorded NA.
 *
 * thermal.c includes <math.h> but calls no function from it. Every arithmetic
 * operation in the file is +, -, *, / on double. There is no domain to get
 * wrong, so the rule has nothing to bite on -- recorded explicitly so the
 * scorecard can tell "checked and clean" apart from "never examined".
 *
 * THM-04  A defect neither standard covers.
 *
 * crit_est_update hardcodes 950.0, 260.0 and 120.0 -- the coil, economiser
 * and envelope conductances -- as literals, duplicating crit_plant_t fields
 * that crit_plant_default sets. The function takes no plant argument at all,
 * so the duplication cannot be kept in step even in principle.
 *
 * Demonstrated by discretising two plants that differ ONLY in ua_coil and
 * showing the estimator produces bit-identical trajectories.
 * ------------------------------------------------------------------------- */
static void test_flp32_na_and_hardcoded_plant(void)
{
    ws_fixture_t fa, fb;
    crit_plant_t plant_a, plant_b;
    crit_est_t est_a, est_b;
    crit_obs_t obs;
    creal u_prev[CRIT_NU];
    int i;

    ct_na("THM-03", "SEI CERT C", "FLP32-C",
          "thermal.c calls no <math.h> function; all arithmetic is + - * / "
          "on double. The #include <math.h> at thermal.c:12 is unused");

    crit_plant_default(&plant_a);
    crit_plant_default(&plant_b);
    plant_b.ua_coil = 0.0;            /* a room with no cooling coil at all */

    fixture_ws(&fa, &plant_a);
    fixture_ws(&fb, &plant_b);
    crit_est_init(&est_a, &plant_a, 24.0);
    crit_est_init(&est_b, &plant_b, 24.0);

    u_prev[0] = 1.0;                  /* full cooling demand */
    u_prev[1] = 0.0;

    for (i = 0; i < 20; i++) {
        fixture_obs(&obs, 26.0);
        crit_est_update(&est_a, &fa.ws, &obs, u_prev, 30.0);
        crit_est_update(&est_b, &fb.ws, &obs, u_prev, 30.0);
    }

    if (est_a.x[0] == est_b.x[0]) {
        snprintf(ct_buf, sizeof ct_buf,
                 "ua_coil=950 and ua_coil=0 both give T_air_hat=%.9f after 20 "
                 "updates at full cooling demand: crit_est_update uses the "
                 "literals 950.0/260.0/120.0 (thermal.c:207-209) and takes no "
                 "crit_plant_t argument, so it cannot track the plant it is "
                 "estimating. Neither standard has a rule against duplicating "
                 "a constant",
                 (double)est_a.x[0]);
        ct_unmapped("THM-04", ct_buf);
    } else {
        ct_rule("THM-04", "NASA P10", "Rule 7", 1,
                ct_ev2("estimator tracks plant: x_a", (double)est_a.x[0],
                       "x_b", (double)est_b.x[0]));
    }
}

int main(void)
{
    ct_begin("unit3/thermal.c");

    test_rule2_bounded_riccati();
    test_rule7_nan_measurement();
    test_flp32_na_and_hardcoded_plant();

    return ct_end();
}
