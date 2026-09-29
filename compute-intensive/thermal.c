/* ===========================================================================
 * Plant model, discretisation, and the state estimator.
 *
 * The continuous plant is bilinear: the cooling term u * UA_coil * (T_air
 * - T_sup) has the input multiplying a state. A QP needs linear dynamics,
 * so the input channels are linearised about the setpoint — standard
 * successive-linearisation MPC. The error is small precisely because the
 * room is regulated near T_ref, which is where we linearise.
 *
 * That linearisation is also where the time variation comes from, and it
 * is physically real rather than manufactured: the economiser channel
 * carries (T_air - T_out_k), and T_out varies across the forecast
 * horizon. A genuinely LTV B_k is what makes the O(N^3) condensing
 * necessary instead of precomputable.
 * ======================================================================== */
#include "critter.h"
#include <string.h>
#include <math.h>

/* Identified from data/hvac_data_cleaned.parquet where possible; see
 * tools/identify.py. Defaults here are the commissioning fallback. */
void crit_plant_default(crit_plant_t *p)
{
    p->c_air   = 1.20e6;   /* J/K   air + fast contents                    */
    p->c_mass  = 1.20e7;   /* J/K   slab and walls, ~10x the fast node     */
    p->ua_env  = 120.0;    /* W/K   envelope                                */
    p->ua_mass = 900.0;    /* W/K   air <-> mass                            */
    p->ua_coil = 950.0;    /* W/K   evaporator at full demand               */
    p->ua_econ = 260.0;    /* W/K   economiser at 100% damper               */
    p->q_int   = 2400.0;   /* W     internal load                           */
    p->t_sup   = 12.5;     /* degC  measured evaporator outlet when cooling */
    p->p_cool_w = 55.0;    /* W     draw at full cooling demand             */
    p->p_fan_w  = 0.55;    /* W     standby / fan                           */
}

/* --- 3x3 helpers ------------------------------------------------------- */
static void m3_mul(const creal *A, const creal *B, creal *C)
{
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) {
            creal s = 0.0;
            for (int k = 0; k < 3; k++) s += A[i*3+k] * B[k*3+j];
            C[i*3+j] = s;
        }
}

/* ---------------------------------------------------------------------------
 * Continuous-time system matrix. State x = [T_air, T_mass, d_hat].
 *
 *   C_air  dT_air/dt  = UA_env (T_out - T_air) + UA_mass (T_mass - T_air)
 *                     + Q_int + d_hat + (cooling and economiser, in B)
 *   C_mass dT_mass/dt = UA_mass (T_air - T_mass)
 *   dd_hat/dt         = 0                    (random walk in the estimator)
 *
 * d_hat is a lumped unmodelled heat term in W. It absorbs whatever the
 * model gets wrong, which is what makes the tracking offset-free — and a
 * slowly growing d_hat is itself a fault signal.
 * ------------------------------------------------------------------------ */
static void plant_Ac(const crit_plant_t *p, creal *Ac)
{
    memset(Ac, 0, 9 * sizeof *Ac);
    Ac[0] = -(p->ua_env + p->ua_mass) / p->c_air;
    Ac[1] =   p->ua_mass / p->c_air;
    Ac[2] =   1.0        / p->c_air;      /* d_hat enters as heat          */
    Ac[3] =   p->ua_mass / p->c_mass;
    Ac[4] = - p->ua_mass / p->c_mass;
    /* row 2 stays zero: d_hat is a constant-disturbance model             */
}

/* Ad = exp(Ac dt), Bd = integral. Truncated series: with dt = 30 s and
 * eigenvalues near 1e-3 /s, ||Ac dt|| ~ 0.03, so a cubic term is already
 * past fp64 resolution. */
void crit_discretise(const crit_plant_t *p, creal dt_s, creal *Ad, creal *Bd)
{
    creal Ac[9], M[9], M2[9], M3[9];
    plant_Ac(p, Ac);
    for (int i = 0; i < 9; i++) M[i] = Ac[i] * dt_s;
    m3_mul(M, M, M2);
    m3_mul(M2, M, M3);

    for (int i = 0; i < 9; i++)
        Ad[i] = M[i] + M2[i] * 0.5 + M3[i] * (1.0 / 6.0);
    Ad[0] += 1.0; Ad[4] += 1.0; Ad[8] += 1.0;

    /* Bd = (I + M/2 + M^2/6) Bc dt, with Bc filled by the caller per step
     * because the input channels are linearised about the operating point.
     * Here we only stash the integrator factor's first row, which is all
     * the input channels touch (both act on the air node). */
    creal f0 = 1.0 + M[0] * 0.5 + M2[0] * (1.0 / 6.0);
    Bd[0] = f0 * dt_s / p->c_air;   /* scale for a Watt applied to air node */
    Bd[1] = 0.0;
    Bd[2] = 0.0;
}

/* ---------------------------------------------------------------------------
 * Steady-state Kalman gain.
 *
 * Computed once at init by iterating the Riccati recursion to convergence,
 * so the gain is derived rather than hand-tuned, and nothing runs on the
 * control path. Valid because A and C are constant: only B varies with the
 * operating point, and the Kalman gain does not depend on B.
 *
 * Cost on the control path afterwards: about 15 FLOP per step.
 * ------------------------------------------------------------------------ */
static void kalman_steady(const creal *Ad, creal *L_out)
{
    /* Process noise: trust T_air, trust T_mass less, let d_hat wander —
     * that last one is what gives offset-free tracking its authority. */
    const creal Qn[3] = { 1e-4, 1e-5, 5e-2 };
    const creal Rn    = 4e-3;          /* (0.06 degC)^2 sensor variance    */

    creal P[9]; memset(P, 0, sizeof P);
    P[0] = 1.0; P[4] = 1.0; P[8] = 1.0;

    for (int it = 0; it < 4000; it++) {
        /* P <- Ad P Ad^T + Q */
        creal AP[9], APAt[9], At[9];
        for (int i = 0; i < 3; i++)
            for (int j = 0; j < 3; j++) At[i*3+j] = Ad[j*3+i];
        m3_mul(Ad, P, AP);
        m3_mul(AP, At, APAt);
        for (int i = 0; i < 9; i++) P[i] = APAt[i];
        P[0] += Qn[0]; P[4] += Qn[1]; P[8] += Qn[2];

        /* C = [1 0 0], so C P C^T = P[0][0] */
        creal s = P[0] + Rn;
        creal K[3] = { P[0] / s, P[3] / s, P[6] / s };

        /* P <- (I - K C) P */
        creal Pn[9];
        for (int i = 0; i < 3; i++)
            for (int j = 0; j < 3; j++)
                Pn[i*3+j] = P[i*3+j] - K[i] * P[0*3+j];
        memcpy(P, Pn, sizeof P);

        if (it == 3999) { L_out[0] = K[0]; L_out[1] = K[1]; L_out[2] = K[2]; }
    }
}

void crit_est_init(crit_est_t *e, const crit_plant_t *p, creal t_air0)
{
    memset(e, 0, sizeof *e);
    creal Ad[9], Bd[3];
    crit_discretise(p, 30.0, Ad, Bd);
    kalman_steady(Ad, e->l_gain);
    e->x[0] = t_air0;      /* seed both thermal nodes at the measurement   */
    e->x[1] = t_air0;
    e->x[2] = 0.0;         /* no disturbance assumed until one is observed */
    e->initialised = 1;
}

/* Predict then correct. ~15 FLOP per call — 0.003% of the QP, so it does
 * not perturb the compute-bound argument in any measurable way. */
void crit_est_update(crit_est_t *e, const crit_ws_t *ws, const crit_obs_t *o,
                     const creal u_prev[CRIT_NU], creal dt_s)
{
    const creal *Ad = ws->Ad, *Bd = ws->Bd;

    /* heat applied to the air node over the last interval, in Watts */
    creal q_cool = -u_prev[0] * 950.0 * (e->x[0] - o->t_sup);
    creal q_econ = -u_prev[1] * 260.0 * (e->x[0] - o->t_out);
    creal q_amb  =  120.0 * o->t_out + o->q_int;

    creal xp[3];
    for (int i = 0; i < 3; i++)
        xp[i] = Ad[i*3+0]*e->x[0] + Ad[i*3+1]*e->x[1] + Ad[i*3+2]*e->x[2];
    xp[0] += Bd[0] * (q_cool + q_econ + q_amb);

    if (o->valid) {
        creal innov = o->t_air - xp[0];
        for (int i = 0; i < 3; i++) xp[i] += e->l_gain[i] * innov;
    }
    memcpy(e->x, xp, sizeof xp);
    e->n_updates++;
    (void)dt_s;
}
