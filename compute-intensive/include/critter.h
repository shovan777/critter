/* ===========================================================================
 * Critter unit 3 — compute-bound MPC for machine-room / HVAC thermal control
 *
 * Wire formats, configuration, and the public API.
 * See docs/mpc-design.md for what the states and inputs mean physically.
 * ======================================================================== */
#ifndef CRITTER_H
#define CRITTER_H

#include <stdint.h>
#include <stddef.h>

/* --- problem size -------------------------------------------------------
 * CRIT_N is THE workload dial. Condensing cost grows as N^3 at constant
 * boundary bytes, so arithmetic intensity rises with N. Keep the working
 * set inside the Pi 5's 512 KB private L2 (N <= ~160) so the measured
 * FLOP/cycle is independent of what the other two units are doing.
 */
#ifndef CRIT_N
#define CRIT_N        96          /* horizon steps                          */
#endif
#define CRIT_NX        3          /* per zone: T_air, T_mass, d_hat         */
#define CRIT_NU        2          /* per zone: cooling demand, damper       */
#define CRIT_NY        1          /* measured: T_air                        */
#define CRIT_M   (CRIT_N * CRIT_NU)   /* QP decision dimension              */

#ifndef CRIT_ADMM_K
#define CRIT_ADMM_K   25          /* FIXED iterations — no early exit, ever */
#endif

typedef double creal;             /* fp64 is mandatory: kappa(H) ~ 2e3*N^2  */

/* --- physical plant -----------------------------------------------------
 * Two-node lumped RC. Cooling enters as a CONDUCTANCE, never as constant
 * power: the constant-power form has identical eigenvalues in both
 * compressor phases and makes the whole identification degenerate.
 *
 *   C_air  dT_air/dt  = UA_env (T_out - T_air) + UA_mass (T_mass - T_air)
 *                     + Q_int - u_cool UA_coil (T_air - T_sup)
 *                     - u_damp UA_econ (T_air - T_out)
 *   C_mass dT_mass/dt = UA_mass (T_air - T_mass)
 */
typedef struct {
    creal c_air;        /* J/K   air + fast-responding contents             */
    creal c_mass;       /* J/K   slab, walls, rack steel                    */
    creal ua_env;       /* W/K   envelope conductance to outside            */
    creal ua_mass;      /* W/K   air <-> thermal mass coupling              */
    creal ua_coil;      /* W/K   evaporator coil at full demand             */
    creal ua_econ;      /* W/K   economiser at 100% damper                  */
    creal q_int;        /* W     internal load (IT / occupancy)             */
    creal t_sup;        /* degC  supply air when cooling (outlet_temp)      */
    creal p_cool_w;     /* W     electrical draw at full cooling demand     */
    creal p_fan_w;      /* W     fan/standby draw                           */
} crit_plant_t;

/* --- observation: what unit 2 hands us each tick ----------------------- */
typedef struct {
    uint64_t t_unix_s;
    uint32_t seq;
    uint8_t  valid;             /* 0 = stale/bad, do not trust t_air        */
    uint8_t  _pad[3];

    creal    t_air;             /* degC  MEASURED room air                  */
    creal    t_out;             /* degC  outside / condenser inlet          */
    creal    t_sup;             /* degC  supply air (evaporator outlet)     */
    creal    q_int;             /* W     internal load estimate             */

    creal    t_ref;             /* degC  setpoint                           */
    creal    t_hi;              /* degC  soft cap (ASHRAE A1 upper = 27)    */

    /* horizon forecasts, index 0 = now ---------------------------------- */
    creal    t_out_fc[CRIT_N];  /* degC  outdoor forecast                   */
    creal    q_int_fc[CRIT_N];  /* W     internal-load forecast             */
} crit_obs_t;

/* --- estimator state (ours, not the memory unit's) --------------------- */
typedef struct {
    creal x[CRIT_NX];           /* [T_air, T_mass, d_hat]                   */
    creal l_gain[CRIT_NX];      /* steady-state Kalman gain, precomputed    */
    uint32_t n_updates;
    uint8_t  initialised;
    uint8_t  _pad[3];
} crit_est_t;

/* --- cost weights ------------------------------------------------------ */
typedef struct {
    creal q_track;      /* (T_air - T_ref)^2                                */
    creal q_cap;        /* NOT YET IMPLEMENTED: soft cap above t_hi. The cap
                         * is currently monitored (plan.margin_c) but not
                         * penalised — a hinge needs a slack variable. v1.   */
    creal r_energy;     /* electrical energy — fitted from active_power     */
    creal r_rate;       /* |du| smoothness / compressor wear                */
    creal rho;          /* ADMM penalty. 13 iters at rho=10, N=128          */
} crit_cost_t;

/* --- performance counters: identical field set in all three units ------ */
typedef struct {
    uint64_t ns_wall;
    uint64_t cycles;
    uint64_t instructions;
    double   flops_analytic;    /* closed form — never a hardware counter   */
    double   bytes_boundary;    /* compulsory traffic, for the roofline     */
    uint64_t checksum;          /* anti-DCE sink                            */
    uint32_t admm_iters;
    uint32_t counters_valid;    /* 0 => no PMU on this host                 */
} crit_perf_t;

/* --- the plan we emit -------------------------------------------------- */
typedef struct {
    uint32_t seq;
    uint16_t horizon;
    uint8_t  status;            /* 0 ok, 1 clamped, 2 stale, 3 rejected     */
    uint8_t  _pad;

    creal    u_now[CRIT_NU];    /* the command to apply now                 */
    creal    u_sched[CRIT_N][CRIT_NU];
    creal    t_pred[CRIT_N];

    creal    t_peak;            /* max predicted T_air over horizon, degC   */
    creal    margin_c;          /* min(t_hi - t_pred), degC                 */
    creal    energy_kwh;        /* predicted cooling energy over horizon    */
    creal    j_opt;             /* final QP objective                       */
    creal    lead_time_s;       /* when margin first drops below 1 degC     */

    crit_perf_t perf;
} crit_plan_t;

/* --- workspace: one aligned arena, no malloc on the compute path ------- */
typedef struct {
    creal *Gh;      /* (N*NY) x M   prediction matrix, sqrt(Q)-scaled       */
    creal *H;       /* M x M        Hessian (Cholesky overwrites in place)  */
    creal *g;       /* M            gradient                                */
    creal *yfree;   /* N*NY         free response                           */
    creal *U, *Z, *Y, *rhs;   /* M each — ADMM state                        */
    creal *Ad, *Bd; /* NX*NX, NX*NU  discretised plant                      */
    void  *arena;
    size_t arena_bytes;
} crit_ws_t;

/* --- API --------------------------------------------------------------- */
int    crit_ws_alloc(crit_ws_t *ws);
void   crit_ws_free (crit_ws_t *ws);

void   crit_plant_default(crit_plant_t *p);
void   crit_discretise(const crit_plant_t *p, creal dt_s,
                       creal *Ad, creal *Bd);

void   crit_est_init  (crit_est_t *e, const crit_plant_t *p, creal t_air0);
void   crit_est_update(crit_est_t *e, const crit_ws_t *ws,
                       const crit_obs_t *o, const creal u_prev[CRIT_NU],
                       creal dt_s);

void   crit_mpc_step(crit_ws_t *ws, const crit_plant_t *p,
                     const crit_cost_t *c, const crit_est_t *e,
                     const crit_obs_t *o, creal dt_s, crit_plan_t *out);

double crit_mpc_flops(void);   /* closed form; host and target must agree  */

/* --- perf harness ------------------------------------------------------ */
void     crit_perf_begin(crit_perf_t *pf);
void     crit_perf_end  (crit_perf_t *pf);
uint64_t crit_fnv1a(const void *buf, size_t n, uint64_t seed);
double   crit_peak_probe(double *out_flops_per_cycle);

#endif /* CRITTER_H */
