/* ===========================================================================
 * Driver and timing harness. This is harness, not product — the only
 * heap allocation outside the workspace arena lives here.
 * ======================================================================== */
#include "critter.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

static int cmp_d(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

/* A plausible summer day, shaped like the recorded data: outdoor swinging
 * 24 -> 42 degC, internal load following occupancy. */
static creal g_tout_mean = 33.0;   /* --demo <hours> [outdoor mean degC] */

static void make_obs(crit_obs_t *o, creal t_air, creal hour, uint32_t seq)
{
    memset(o, 0, sizeof *o);
    o->seq = seq; o->valid = 1;
    o->t_air = t_air;
    o->t_ref = 22.5;
    o->t_hi  = 27.0;
    o->t_sup = 12.5;
    for (int k = 0; k < CRIT_N; k++) {
        creal h = hour + (creal)k * 30.0 / 3600.0;
        o->t_out_fc[k] = g_tout_mean - 9.0 * cos((h - 16.0) * 3.14159265 / 12.0);
        o->q_int_fc[k] = (h > 8.0 && h < 20.0) ? 2400.0 : 700.0;
    }
    o->t_out = o->t_out_fc[0];
    o->q_int = o->q_int_fc[0];
}

static void usage(void)
{
    puts("critter unit 3 — condensed dense MPC\n"
         "  --peak-probe      measure the fp64 FMA ceiling (H3 gate)\n"
         "  --bench [reps]    time one MPC step, report median/IQR\n"
         "  --demo [hours]    closed-loop run against the plant model\n"
         "  --flops           print the closed-form FLOP model and exit");
}

int main(int argc, char **argv)
{
    if (argc < 2) { usage(); return 1; }

    if (!strcmp(argv[1], "--peak-probe")) {
        double fpc = 0.0, gfs = crit_peak_probe(&fpc);
        printf("peak probe: %.2f GFLOP/s", gfs);
        if (fpc > 0.0) printf("   %.2f fp64 FLOP/cycle  (%.1f%% of 8)",
                              fpc, 100.0 * fpc / 8.0);
        else           printf("   [no PMU: cycles unavailable]");
        puts("");
        if (fpc > 0.0 && fpc < 6.0)
            puts("WARNING: below 6 FLOP/cyc — check -mcpu / accumulator count.\n"
                 "This number is the denominator of every percentage we report.");
        return 0;
    }

    if (!strcmp(argv[1], "--flops")) {
        printf("N=%d NU=%d M=%d ADMM_K=%d\n", CRIT_N, CRIT_NU, CRIT_M, CRIT_ADMM_K);
        printf("analytic FLOP/step: %.0f (%.2f MFLOP)\n",
               crit_mpc_flops(), crit_mpc_flops() / 1e6);
        printf("Gh  %zu KB   H  %zu KB   total working set ~%zu KB\n",
               (size_t)CRIT_N * CRIT_M * sizeof(creal) / 1024,
               (size_t)CRIT_M * CRIT_M * sizeof(creal) / 1024,
               ((size_t)CRIT_N * CRIT_M + (size_t)CRIT_M * CRIT_M)
                   * sizeof(creal) / 1024);
        return 0;
    }

    crit_ws_t ws;
    if (crit_ws_alloc(&ws) != 0) { fprintf(stderr, "alloc failed\n"); return 1; }

    crit_plant_t plant; crit_plant_default(&plant);
    crit_discretise(&plant, 30.0, ws.Ad, ws.Bd);

    /* Weights must be commensurate with the SYRK contribution to the
     * Hessian diagonal (~5.4 at q_track=1), or the input costs are
     * numerically invisible and the controller degenerates into pure
     * setpoint tracking. Overridable so the analysis phase can sweep. */
    crit_cost_t cost = { .q_track = 1.0, .q_cap = 50.0, .r_energy = 5.0,
                         .r_rate = 0.25, .rho = 10.0 };
    if (getenv("CRIT_R_ENERGY")) cost.r_energy = atof(getenv("CRIT_R_ENERGY"));
    if (getenv("CRIT_R_RATE"))   cost.r_rate   = atof(getenv("CRIT_R_RATE"));
    if (getenv("CRIT_Q_TRACK"))  cost.q_track  = atof(getenv("CRIT_Q_TRACK"));
    if (getenv("CRIT_RHO"))      cost.rho      = atof(getenv("CRIT_RHO"));
    crit_est_t est; crit_est_init(&est, &plant, 24.0);

    crit_obs_t  obs;
    crit_plan_t plan;

    if (!strcmp(argv[1], "--bench")) {
        int reps = (argc > 2) ? atoi(argv[2]) : 31;
        if (reps < 1) reps = 31;
        double *us = malloc((size_t)reps * sizeof *us);
        double  fpc_sum = 0.0; int fpc_n = 0;
        uint64_t ck = 0;

        make_obs(&obs, 24.0, 14.0, 0);
        for (int r = 0; r < reps; r++) {
            crit_mpc_step(&ws, &plant, &cost, &est, &obs, 30.0, &plan);
            us[r] = (double)plan.perf.ns_wall / 1000.0;
            if (plan.perf.counters_valid & 1u) {
                fpc_sum += plan.perf.flops_analytic / (double)plan.perf.cycles;
                fpc_n++;
            }
            if (r == 0) ck = plan.perf.checksum;
            else if (plan.perf.checksum != ck)
                fprintf(stderr, "WARNING: checksum moved between reps\n");
        }
        qsort(us, (size_t)reps, sizeof *us, cmp_d);
        double med = us[reps/2], q1 = us[reps/4], q3 = us[(3*reps)/4];
        printf("N=%d M=%d K=%d   %.0f FLOP/step\n",
               CRIT_N, CRIT_M, CRIT_ADMM_K, plan.perf.flops_analytic);
        printf("median %.1f us   IQR [%.1f, %.1f]   %.2f GFLOP/s\n",
               med, q1, q3, plan.perf.flops_analytic / (med * 1e3));
        if (fpc_n) {
            double fpc = fpc_sum / fpc_n;
            printf("fp64 FLOP/cycle %.2f  (%.1f%% of the 8 ceiling)\n",
                   fpc, 100.0 * fpc / 8.0);
        } else {
            printf("fp64 FLOP/cycle: [no PMU]\n");
        }
        printf("checksum %016llx   status %u\n",
               (unsigned long long)ck, plan.status);
        free(us);
        crit_ws_free(&ws);
        return 0;
    }

    if (!strcmp(argv[1], "--demo")) {
        double hours = (argc > 2) ? atof(argv[2]) : 6.0;
        if (argc > 3) g_tout_mean = atof(argv[3]);
        int steps = (int)(hours * 3600.0 / 30.0);
        creal t_air = 25.5, t_mass = 25.0;      /* the true plant state */
        creal u_prev[CRIT_NU] = { 0.0, 0.0 };
        double e_kwh = 0.0;

        printf("%6s %7s %7s %7s %7s %7s %8s\n",
               "hour", "T_air", "T_out", "u_cool", "damper", "margin", "kWh");
        for (int s = 0; s < steps; s++) {
            double hour = 6.0 + (double)s * 30.0 / 3600.0;
            make_obs(&obs, t_air, hour, (uint32_t)s);
            crit_est_update(&est, &ws, &obs, u_prev, 30.0);
            crit_mpc_step(&ws, &plant, &cost, &est, &obs, 30.0, &plan);

            u_prev[0] = plan.u_now[0];
            u_prev[1] = plan.u_now[1];

            /* advance the true plant one step (conductance cooling) */
            creal q = plant.ua_env  * (obs.t_out - t_air)
                    + plant.ua_mass * (t_mass    - t_air)
                    + obs.q_int
                    - u_prev[0] * plant.ua_coil * (t_air - obs.t_sup)
                    - u_prev[1] * plant.ua_econ * (t_air - obs.t_out);
            creal qm = plant.ua_mass * (t_air - t_mass);
            t_air  += q  * 30.0 / plant.c_air;
            t_mass += qm * 30.0 / plant.c_mass;
            e_kwh  += (u_prev[0] * plant.p_cool_w + plant.p_fan_w) * 30.0 / 3.6e6;

            if (s % 60 == 0)
                printf("%6.2f %7.2f %7.2f %7.3f %7.3f %7.2f %8.4f\n",
                       hour, (double)t_air, (double)obs.t_out,
                       (double)u_prev[0], (double)u_prev[1],
                       (double)plan.margin_c, e_kwh);
        }
        printf("\nfinal T_air %.2f C   energy %.4f kWh over %.1f h\n",
               (double)t_air, e_kwh, hours);
        crit_ws_free(&ws);
        return 0;
    }

    usage();
    crit_ws_free(&ws);
    return 1;
}
