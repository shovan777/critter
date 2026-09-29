/* ============================================================================
 * test_mpc.c -- runtime conformance tests for UNIT 3, module mpc.c.
 *
 * MODULE UNDER TEST: mpc.c. thermal.c is linked only because mpc.c needs
 * crit_discretise and the estimator to build a well-formed input; findings in
 * thermal.c are scored on its own card (test_thermal.c), never on this one.
 * perf.c is linked for the timing hooks mpc.c calls. main.c is NOT linked --
 * it is labelled "harness, not product" in its own header comment and is
 * scored separately (see check_static.py, card F).
 *
 * BUILD NOTE, WHICH IS ITSELF A FINDING: this binary is built -std=gnu11, not
 * -std=c11, because mpc.c calls posix_memalign, which strict C11 does not
 * declare. Under -std=c11 -Wpedantic the unit emits an implicit-declaration
 * diagnostic. That is recorded against the unit as DCL31-C and NASA P10
 * Rule 10 in check_static.py; the test build uses gnu11 so the remaining
 * rules can be exercised at all.
 * ========================================================================= */

#include "ct.h"
#include "critter.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <unistd.h>
#include <sys/wait.h>

/* Build a well-formed observation: steady outdoor temperature, steady load.
 * Deliberately simpler than main.c's make_obs -- no trigonometry -- so that
 * any NaN appearing in a result came from the unit and not from the fixture. */
static void fixture_obs(crit_obs_t *o, creal t_air)
{
    int k;
    memset(o, 0, sizeof *o);
    o->seq   = 1;
    o->valid = 1;
    o->t_air = t_air;
    o->t_ref = 22.5;
    o->t_hi  = 27.0;
    o->t_sup = 12.5;
    o->t_out = 33.0;
    o->q_int = 2400.0;
    for (k = 0; k < CRIT_N; k++) {
        o->t_out_fc[k] = 33.0;
        o->q_int_fc[k] = 2400.0;
    }
}

static void fixture_cost(crit_cost_t *c)
{
    c->q_track  = 1.0;
    c->q_cap    = 50.0;
    c->r_energy = 5.0;
    c->r_rate   = 0.25;
    c->rho      = 10.0;
}

/* ---------------------------------------------------------------------------
 * MPC-01  FLP32-C. Prevent or detect domain and range errors in math
 *         functions.
 *
 * mpc.c calls sqrt(cost->q_track) twice -- once in build_prediction and once
 * in crit_mpc_step -- with no check that q_track is non-negative. sqrt of a
 * negative argument is a domain error.
 *
 * The weight is caller-supplied and, in the shipped harness, comes straight
 * from atof(getenv("CRIT_Q_TRACK")) with no validation, so a negative value
 * is reachable from the environment.
 *
 * What makes this worse than a NaN: cholesky_lower guards its diagonal with
 * `if (d <= 1e-14) return -1`, and NaN <= 1e-14 is false. The guard does not
 * fire. crit_mpc_step therefore reports status 0 -- "ok" -- while returning a
 * NaN control command. A supervisor checking status alone would apply it.
 * ------------------------------------------------------------------------- */
static void test_flp32_sqrt_domain(void)
{
    crit_ws_t ws;
    crit_plant_t plant;
    crit_cost_t cost;
    crit_est_t est;
    crit_obs_t obs;
    crit_plan_t plan;

    if (crit_ws_alloc(&ws) != 0) {
        ct_rule("MPC-01", "SEI CERT C", "FLP32-C", 0,
                "workspace allocation failed; test inconclusive");
        return;
    }
    crit_plant_default(&plant);
    crit_discretise(&plant, 30.0, ws.Ad, ws.Bd);
    crit_est_init(&est, &plant, 24.0);
    fixture_obs(&obs, 25.5);
    fixture_cost(&cost);

    cost.q_track = -1.0;              /* the domain error */

    crit_mpc_step(&ws, &plant, &cost, &est, &obs, 30.0, &plan);

    if (isnan(plan.u_now[0]) || isnan(plan.t_peak) || isnan(plan.margin_c)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "q_track=-1 -> sqrt(-1) at mpc.c:186,313 -> u_now[0]=NaN, "
                 "and every NaN comparison being false leaves the sentinels "
                 "in place: t_peak=%.3g degC, margin_c=%.3g degC. status=%u "
                 "(\"ok\"). The Cholesky guard d<=1e-14 does not fire on NaN",
                 (double)plan.t_peak, (double)plan.margin_c,
                 (unsigned)plan.status);
        ct_rule("MPC-01", "SEI CERT C", "FLP32-C", 0, ct_buf);

        if (plan.status == 0u) {
            ct_unmapped("MPC-02",
                        "crit_mpc_step returns status=0 (\"ok\") alongside a "
                        "NaN command. Neither standard has a rule requiring a "
                        "status code to be consistent with the payload it "
                        "describes; a reviewer working only from rule lists "
                        "will not be prompted to look for this");
        }
    } else {
        ct_rule("MPC-01", "SEI CERT C", "FLP32-C", 1,
                "sqrt argument validated before use");
    }

    crit_ws_free(&ws);
}

/* ---------------------------------------------------------------------------
 * MPC-03  MEM35-C. Allocate sufficient memory for an object.
 *         Expected to PASS.
 *
 * crit_ws_alloc carves ten sub-arrays out of one posix_memalign'd arena by
 * pointer bumping. Every extent derives from compile-time constants, so the
 * sizing is checkable: this test confirms the last byte of the last sub-array
 * lies inside the arena, and that Gh and H have their full declared extents.
 * ------------------------------------------------------------------------- */
static void test_mem35_arena_extents(void)
{
    crit_ws_t ws;
    const char *base, *limit;
    int ok;

    if (crit_ws_alloc(&ws) != 0) {
        ct_rule("MPC-03", "SEI CERT C", "MEM35-C", 0,
                "workspace allocation failed; test inconclusive");
        return;
    }

    base  = (const char *)ws.arena;
    limit = base + ws.arena_bytes;

    ok = ((const char *)(ws.Gh + (size_t)CRIT_N * CRIT_M) <= limit)
      && ((const char *)(ws.H  + (size_t)CRIT_M * CRIT_M) <= limit)
      && ((const char *)(ws.yfree + CRIT_N)               <= limit)
      && ((const char *)(ws.Ad + 9)                       <= limit)
      && ((const char *)(ws.Bd + 3)                       <= limit)
      && ((const char *)ws.Gh >= base);

    if (ok) {
        snprintf(ct_buf, sizeof ct_buf,
                 "arena %zu B holds Gh(%dx%d) + H(%dx%d) + 5 M-vectors + "
                 "yfree + Ad + Bd with %td B spare; all extents inside "
                 "[base, base+arena_bytes)",
                 ws.arena_bytes, CRIT_N, CRIT_M, CRIT_M, CRIT_M,
                 (ptrdiff_t)(limit - (const char *)(ws.Bd + 3)));
        ct_rule("MPC-03", "SEI CERT C", "MEM35-C", 1, ct_buf);
    } else {
        ct_rule("MPC-03", "SEI CERT C", "MEM35-C", 0,
                ct_evl("sub-array escapes arena; arena_bytes",
                       (long)ws.arena_bytes));
    }

    crit_ws_free(&ws);
}

/* ---------------------------------------------------------------------------
 * MPC-04  NASA P10 Rule 2 -- give all loops a fixed upper bound, statically
 *         provable. Expected to PASS.
 *
 * The ADMM loop runs exactly CRIT_ADMM_K times with no convergence test and
 * no early exit, and every other loop in mpc.c is bounded by N or M, both
 * compile-time constants. This test confirms the iteration count is
 * input-independent by running two very different problems and comparing.
 * Static provability is checked separately in check_static.py; this covers
 * the behavioural half.
 * ------------------------------------------------------------------------- */
static void test_rule2_fixed_iterations(void)
{
    crit_ws_t ws;
    crit_plant_t plant;
    crit_cost_t cost;
    crit_est_t est;
    crit_obs_t obs;
    crit_plan_t easy, hard;

    if (crit_ws_alloc(&ws) != 0) {
        ct_rule("MPC-04", "NASA P10", "Rule 2", 0,
                "workspace allocation failed; test inconclusive");
        return;
    }
    crit_plant_default(&plant);
    crit_discretise(&plant, 30.0, ws.Ad, ws.Bd);
    crit_est_init(&est, &plant, 24.0);
    fixture_cost(&cost);

    fixture_obs(&obs, 22.5);                 /* already at setpoint */
    crit_mpc_step(&ws, &plant, &cost, &est, &obs, 30.0, &easy);

    fixture_obs(&obs, 45.0);                 /* far above the soft cap */
    crit_mpc_step(&ws, &plant, &cost, &est, &obs, 30.0, &hard);

    if (easy.perf.admm_iters == (uint32_t)CRIT_ADMM_K
     && hard.perf.admm_iters == (uint32_t)CRIT_ADMM_K) {
        snprintf(ct_buf, sizeof ct_buf,
                 "T_air=22.5 and T_air=45.0 both ran exactly %u ADMM "
                 "iterations (CRIT_ADMM_K); iteration count is "
                 "input-independent",
                 (unsigned)easy.perf.admm_iters);
        ct_rule("MPC-04", "NASA P10", "Rule 2", 1, ct_buf);
    } else {
        snprintf(ct_buf, sizeof ct_buf,
                 "iteration count varied with input: %u vs %u",
                 (unsigned)easy.perf.admm_iters,
                 (unsigned)hard.perf.admm_iters);
        ct_rule("MPC-04", "NASA P10", "Rule 2", 0, ct_buf);
    }

    crit_ws_free(&ws);
}

/* ---------------------------------------------------------------------------
 * MPC-05  NASA P10 Rule 7 -- "each called function must check the validity of
 *         all parameters provided by the caller."
 *
 * crit_mpc_step takes six pointers and validates none of them. Its first
 * statement after the memset dereferences the observation pointer.
 *
 * DETECTION METHOD: fork. The call is made in a child process so that the
 * expected fault is observed rather than suffered -- the parent reads the
 * child's termination signal and reports a verdict. A test for missing
 * parameter validation cannot run in-process, because the thing it is
 * detecting is a crash.
 * ------------------------------------------------------------------------- */
static void test_rule7_null_parameters(void)
{
    crit_ws_t ws;
    crit_plant_t plant;
    crit_cost_t cost;
    crit_est_t est;
    crit_plan_t plan;
    pid_t pid;
    int status = 0;

    if (crit_ws_alloc(&ws) != 0) {
        ct_rule("MPC-05", "NASA P10", "Rule 7", 0,
                "workspace allocation failed; test inconclusive");
        return;
    }
    crit_plant_default(&plant);
    crit_discretise(&plant, 30.0, ws.Ad, ws.Bd);
    crit_est_init(&est, &plant, 24.0);
    fixture_cost(&cost);

    fflush(NULL);                  /* no duplicated output from the child */
    pid = fork();
    if (pid < 0) {
        ct_rule("MPC-05", "NASA P10", "Rule 7", 0,
                "fork failed; test inconclusive");
        crit_ws_free(&ws);
        return;
    }
    if (pid == 0) {
        /* Child. A compliant implementation rejects the null observation and
         * returns; this one dereferences it. */
        crit_mpc_step(&ws, &plant, &cost, &est, NULL, 30.0, &plan);
        _exit(0);
    }

    if (waitpid(pid, &status, 0) < 0) {
        ct_rule("MPC-05", "NASA P10", "Rule 7", 0,
                "waitpid failed; test inconclusive");
    } else if (WIFSIGNALED(status)) {
        snprintf(ct_buf, sizeof ct_buf,
                 "crit_mpc_step(obs=NULL) terminated by signal %d; the "
                 "observation pointer is dereferenced at mpc.c:299 before any "
                 "validity check. 6 of 6 pointer parameters unvalidated",
                 WTERMSIG(status));
        ct_rule("MPC-05", "NASA P10", "Rule 7", 0, ct_buf);
    } else {
        ct_rule("MPC-05", "NASA P10", "Rule 7", 1,
                "null observation rejected without faulting");
    }

    crit_ws_free(&ws);
}

int main(void)
{
    ct_begin("unit3/mpc.c");

    test_flp32_sqrt_domain();
    test_mem35_arena_extents();
    test_rule2_fixed_iterations();
    test_rule7_null_parameters();

    return ct_end();
}
