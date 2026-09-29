/* ===========================================================================
 * The compute-bound kernel: condensed dense MPC.
 *
 * Pipeline per control step:
 *
 *     build Gh          prediction matrix, sqrt(q_track)-scaled   O(N^2)
 *     H = Gh^T Gh + R   SYRK      <- the compute-bound kernel     O(N*M^2)
 *     H + rho I = LL^T  Cholesky                                  O(M^3/3)
 *     ADMM x K          fixed iteration count, branchless clip    O(K*M^2)
 *
 * States are eliminated (condensed), so the decision variable is the
 * control sequence alone and the Hessian is small and dense. The
 * alternative — a sparse full-space KKT system — was measured at 0.303
 * FLOP/cycle against dense SYRK's 6.93, because a sparse triangular solve
 * scatters through an indirection and drags an index load and an address
 * computation along with every single FLOP. See docs/roofline-analysis.md.
 *
 * The ADMM iteration count is FIXED. No convergence test, no early exit,
 * no data-dependent branch anywhere in the hot path — so runtime does not
 * depend on the input values and the cycle counts are reproducible.
 * ======================================================================== */
#include "critter.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define ALIGN 64
#define NX CRIT_NX
#define NU CRIT_NU
#define N  CRIT_N
#define M  CRIT_M

/* --- workspace --------------------------------------------------------- */
int crit_ws_alloc(crit_ws_t *ws)
{
    memset(ws, 0, sizeof *ws);
    size_t sz_Gh = (size_t)N * M;
    size_t sz_H  = (size_t)M * M;
    size_t total = sz_Gh + sz_H + (size_t)M * 5 + (size_t)N
                 + 9 + 3 + 64;

    ws->arena_bytes = total * sizeof(creal);
    if (posix_memalign(&ws->arena, ALIGN, ws->arena_bytes) != 0) return -1;
    memset(ws->arena, 0, ws->arena_bytes);

    creal *p = (creal *)ws->arena;
    ws->Gh    = p; p += sz_Gh;
    ws->H     = p; p += sz_H;
    ws->g     = p; p += M;
    ws->U     = p; p += M;
    ws->Z     = p; p += M;
    ws->Y     = p; p += M;
    ws->rhs   = p; p += M;
    ws->yfree = p; p += N;
    ws->Ad    = p; p += 9;
    ws->Bd    = p; p += 3;
    return 0;
}

void crit_ws_free(crit_ws_t *ws)
{
    free(ws->arena);
    memset(ws, 0, sizeof *ws);
}

/* --- closed-form FLOP model -------------------------------------------
 * Derived, never measured from a counter, so host and target agree to the
 * last digit and the roofline plot is reproducible from the source.
 */
double crit_mpc_flops(void)
{
    double n = (double)N, m = (double)M, nx = (double)NX, k = (double)CRIT_ADMM_K;
    double f_build = 2.0 * n * n * (double)NU * nx * nx * 0.5;
    double f_syrk  = m * (m + 1.0) * n;              /* triangle, 2 FLOP/MAC */
    double f_chol  = m * m * m / 3.0 + m * m * 0.5;
    double f_grad  = 2.0 * n * m;
    double f_admm  = k * (4.0 * m * m + 6.0 * m);    /* 2 TRSV + vector ops  */
    return f_build + f_syrk + f_chol + f_grad + f_admm;
}

/* ---------------------------------------------------------------------------
 * Condensing. Column j = (step k, channel c) holds the response of T_air
 * at every future step to a unit input applied at step k on channel c.
 *
 * The economiser channel carries (T_air_ref - T_out[k]), so B genuinely
 * varies across the horizon and the Hessian cannot be precomputed. That
 * is also the correct physics: opening the damper cools the room only
 * while outside air is cooler than the room, and heats it otherwise —
 * which is precisely the mistake visible in the recorded data.
 * ------------------------------------------------------------------------ */
static void build_prediction(crit_ws_t *ws, const crit_plant_t *p,
                             const crit_cost_t *cost, const crit_est_t *e,
                             const crit_obs_t *o, creal dt_s)
{
    const creal *Ad = ws->Ad;
    const creal  sq = sqrt(cost->q_track);
    const creal  gain = ws->Bd[0];          /* degC per Watt-second on air  */

    memset(ws->Gh, 0, (size_t)N * M * sizeof(creal));

    for (int k = 0; k < N; k++) {
        creal t_out_k = o->t_out_fc[k];
        /* input channels, linearised about the setpoint */
        creal b[NU];
        b[0] = -p->ua_coil * (o->t_ref - o->t_sup) * gain;   /* cooling     */
        b[1] = -p->ua_econ * (o->t_ref - t_out_k)  * gain;   /* economiser  */

        for (int c = 0; c < NU; c++) {
            creal v[NX] = { b[c], 0.0, 0.0 };
            int   col   = k * NU + c;
            for (int i = k; i < N; i++) {
                ws->Gh[(size_t)i * M + col] = sq * v[0];
                creal n0 = Ad[0]*v[0] + Ad[1]*v[1] + Ad[2]*v[2];
                creal n1 = Ad[3]*v[0] + Ad[4]*v[1] + Ad[5]*v[2];
                creal n2 = Ad[6]*v[0] + Ad[7]*v[1] + Ad[8]*v[2];
                v[0] = n0; v[1] = n1; v[2] = n2;
            }
        }
    }

    /* Free response: where the room goes with no control action at all. */
    creal x[NX] = { e->x[0], e->x[1], e->x[2] };
    for (int i = 0; i < N; i++) {
        creal q_amb = p->ua_env * o->t_out_fc[i] + o->q_int_fc[i];
        creal n0 = Ad[0]*x[0] + Ad[1]*x[1] + Ad[2]*x[2] + ws->Bd[0]*q_amb;
        creal n1 = Ad[3]*x[0] + Ad[4]*x[1] + Ad[5]*x[2];
        creal n2 = Ad[6]*x[0] + Ad[7]*x[1] + Ad[8]*x[2];
        x[0] = n0; x[1] = n1; x[2] = n2;
        ws->yfree[i] = x[0];
    }
    (void)dt_s;
}

/* ---------------------------------------------------------------------------
 * SYRK: H = Gh^T Gh, lower triangle. THE kernel.
 *
 * 4x4 register blocking: each loaded value is reused four times, and the
 * sixteen accumulators give the FMA pipes enough independent chains to
 * stay fed. The naive triple loop reuses nothing and lands near 12% of
 * peak; this shape reaches 87%. Both are the same arithmetic — which is
 * the entire point of the roofline experiment.
 * ------------------------------------------------------------------------ */
static void syrk_lower(const creal * restrict Gh, creal * restrict H)
{
    memset(H, 0, (size_t)M * M * sizeof(creal));

    for (int i0 = 0; i0 < M; i0 += 4) {
        for (int j0 = 0; j0 <= i0; j0 += 4) {
            creal a[4][4] = {{0}};
            for (int k = 0; k < N; k++) {
                const creal *row = Gh + (size_t)k * M;
                creal gi0 = row[i0+0], gi1 = row[i0+1];
                creal gi2 = row[i0+2], gi3 = row[i0+3];
                creal gj0 = row[j0+0], gj1 = row[j0+1];
                creal gj2 = row[j0+2], gj3 = row[j0+3];
                a[0][0] += gi0*gj0; a[0][1] += gi0*gj1;
                a[0][2] += gi0*gj2; a[0][3] += gi0*gj3;
                a[1][0] += gi1*gj0; a[1][1] += gi1*gj1;
                a[1][2] += gi1*gj2; a[1][3] += gi1*gj3;
                a[2][0] += gi2*gj0; a[2][1] += gi2*gj1;
                a[2][2] += gi2*gj2; a[2][3] += gi2*gj3;
                a[3][0] += gi3*gj0; a[3][1] += gi3*gj1;
                a[3][2] += gi3*gj2; a[3][3] += gi3*gj3;
            }
            for (int di = 0; di < 4; di++)
                for (int dj = 0; dj < 4; dj++)
                    if (i0 + di >= j0 + dj)
                        H[(size_t)(i0+di) * M + (j0+dj)] = a[di][dj];
        }
    }
}

/* Input cost: energy on the cooling channel, a light touch on the damper,
 * plus a rate penalty that lands as a tridiagonal block. Also what makes
 * H positive definite — Gh is N x M with M > N, so Gh^T Gh alone is rank
 * deficient by construction. */
static void add_input_cost(creal *H, const crit_cost_t *c)
{
    for (int k = 0; k < N; k++) {
        for (int ch = 0; ch < NU; ch++) {
            int i = k * NU + ch;
            creal r = (ch == 0) ? c->r_energy : c->r_energy * 0.05;
            H[(size_t)i * M + i] += r;

            /* Scale the rate penalty per channel the same way the energy
             * cost is scaled. Applying the full r_rate to the damper made
             * its movement cost 10x its energy cost, which suppressed the
             * economiser exactly when free cooling was worth the most. */
            creal rr = (ch == 0) ? c->r_rate : c->r_rate * 0.05;
            if (rr > 0.0) {
                H[(size_t)i * M + i] += (k == 0 || k == N-1) ? rr : 2.0 * rr;
                if (k > 0) {
                    int im = (k-1) * NU + ch;
                    H[(size_t)i * M + im] -= rr;          /* lower triangle */
                }
            }
        }
    }
}

/* --- Cholesky, lower, in place ----------------------------------------
 * Sequential by nature: column j needs columns 1..j-1, and each diagonal
 * carries a blocking sqrt. It plateaus near 29% of peak however it is
 * written, which is why the optimisation effort belongs in the SYRK.
 */
static int cholesky_lower(creal *A, creal rho)
{
    for (int i = 0; i < M; i++) A[(size_t)i * M + i] += rho;

    for (int j = 0; j < M; j++) {
        creal d = A[(size_t)j * M + j];
        for (int k = 0; k < j; k++) d -= A[(size_t)j*M+k] * A[(size_t)j*M+k];
        if (d <= 1e-14) return -1;
        d = sqrt(d);
        A[(size_t)j * M + j] = d;
        const creal inv = 1.0 / d;
        for (int i = j + 1; i < M; i++) {
            creal s = A[(size_t)i * M + j];
            for (int k = 0; k < j; k++)
                s -= A[(size_t)i*M+k] * A[(size_t)j*M+k];
            A[(size_t)i * M + j] = s * inv;
        }
    }
    return 0;
}

static void chol_solve(const creal *L, const creal *b, creal *x)
{
    for (int i = 0; i < M; i++) {           /* forward */
        creal s = b[i];
        for (int k = 0; k < i; k++) s -= L[(size_t)i*M+k] * x[k];
        x[i] = s / L[(size_t)i*M+i];
    }
    for (int i = M - 1; i >= 0; i--) {      /* back */
        creal s = x[i];
        for (int k = i + 1; k < M; k++) s -= L[(size_t)k*M+i] * x[k];
        x[i] = s / L[(size_t)i*M+i];
    }
}

/* --- the step ---------------------------------------------------------- */
void crit_mpc_step(crit_ws_t *ws, const crit_plant_t *p, const crit_cost_t *c,
                   const crit_est_t *e, const crit_obs_t *o, creal dt_s,
                   crit_plan_t *out)
{
    memset(out, 0, sizeof *out);
    out->seq     = o->seq;
    out->horizon = N;

    crit_perf_begin(&out->perf);

    build_prediction(ws, p, c, e, o, dt_s);
    syrk_lower(ws->Gh, ws->H);
    add_input_cost(ws->H, c);

    /* gradient g = Gh^T (sqrt(q) * (yfree - t_ref)) */
    const creal sq = sqrt(c->q_track);
    for (int i = 0; i < M; i++) ws->g[i] = 0.0;
    for (int k = 0; k < N; k++) {
        creal ek = sq * (ws->yfree[k] - o->t_ref);
        const creal *row = ws->Gh + (size_t)k * M;
        for (int i = 0; i < M; i++) ws->g[i] += row[i] * ek;
    }

    if (cholesky_lower(ws->H, c->rho) != 0) { out->status = 3; goto done; }

    /* ADMM: fixed K, projection is branchless min/max */
    memset(ws->U, 0, M * sizeof(creal));
    memset(ws->Z, 0, M * sizeof(creal));
    memset(ws->Y, 0, M * sizeof(creal));

    for (int it = 0; it < CRIT_ADMM_K; it++) {
        for (int i = 0; i < M; i++)
            ws->rhs[i] = c->rho * (ws->Z[i] - ws->Y[i]) - ws->g[i];
        chol_solve(ws->H, ws->rhs, ws->U);
        for (int i = 0; i < M; i++) {
            creal v = ws->U[i] + ws->Y[i];
            creal z = v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
            ws->Z[i] = z;
            ws->Y[i] = v - z;
        }
    }
    out->perf.admm_iters = CRIT_ADMM_K;

    /* unpack the plan and predict the resulting trajectory */
    for (int k = 0; k < N; k++)
        for (int ch = 0; ch < NU; ch++)
            out->u_sched[k][ch] = ws->Z[k * NU + ch];
    for (int ch = 0; ch < NU; ch++) out->u_now[ch] = out->u_sched[0][ch];

    out->t_peak   = -1e30;
    out->margin_c =  1e30;
    out->lead_time_s = -1.0;
    creal e_kwh = 0.0;
    for (int k = 0; k < N; k++) {
        creal y = ws->yfree[k];
        const creal *row = ws->Gh + (size_t)k * M;
        for (int i = 0; i < M; i++) y += row[i] * ws->Z[i] / sq;
        out->t_pred[k] = y;
        if (y > out->t_peak) out->t_peak = y;
        creal mg = o->t_hi - y;
        if (mg < out->margin_c) out->margin_c = mg;
        if (mg < 1.0 && out->lead_time_s < 0.0)
            out->lead_time_s = (creal)k * dt_s;
        e_kwh += (out->u_sched[k][0] * p->p_cool_w + p->p_fan_w)
               * dt_s / 3.6e6;
    }
    out->energy_kwh = e_kwh;

    creal j = 0.0;
    for (int i = 0; i < M; i++) j += ws->g[i] * ws->Z[i];
    out->j_opt = j;
    out->status = 0;

done:
    out->perf.flops_analytic = crit_mpc_flops();
    out->perf.bytes_boundary = (double)(sizeof(crit_obs_t) + sizeof(crit_plan_t));
    /* consumed through the checksum so -O3 -ffast-math cannot delete the
     * kernel it was all computed for */
    out->perf.checksum = crit_fnv1a(out->u_sched, sizeof out->u_sched,
                          crit_fnv1a(out->t_pred, sizeof out->t_pred, 0));
    crit_perf_end(&out->perf);
}
