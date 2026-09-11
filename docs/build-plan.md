> **Provenance.** Produced by an automated analysis pass and copied here verbatim.
> Numbers marked *measured* were measured on an x86 development box, not on Pi 5 hardware —
> the fraction-of-peak ratios transfer to Cortex-A76, the absolute rates do not.
> See [mpc-design.md](mpc-design.md) for the control problem this plan builds,
> including one correction to the `critter_obs_t` struct in §2 (state estimation is ours,
> not the memory unit's).

# Critter Compute Unit — Dense Condensed MPC on Pi 5
## 2-Day Build Plan + 5-Day Measurement Plan

---

## 0. The design in one paragraph

Multi-zone machine-room thermal MPC. `nz` zones × 2 RC states (air node + mass node), cooling entering as a **conductance** `s_z·UA_c·(T_air,z − T_supply)` so the plant is genuinely **bilinear** and each control step must **re-linearize**. That re-linearization is what makes the expensive work unavoidable and *data-independent*: every tick rebuilds the condensed prediction matrix Φ, forms `H = Φᵀ Q̄ Φ + R̄` (a **SYRK** — the highest arithmetic-intensity kernel in the whole design), does one dense **Cholesky**, then runs a **fixed** number of ADMM iterations (two triangular solves each) with box constraints `0 ≤ s ≤ 1`. No branching on data, no integer decisions, no sparse factorization, no adaptive termination. Cost is a closed-form function of `(N, nz, K)` and nothing else.

**Why this beats the MIQP that failed:** cost is *fault-invariant*. The MIQP's node count collapsed 120,000× under the fault it was meant to detect. Here, a degraded coil (`ua_scale = 0.4`) changes the *numbers* in A_k and B_k and changes *nothing* about the instruction stream. You will measure this and it is a headline result.

**Sizing** (defaults `nz=4`, `N=48`, `dt=30 s`, `K=60`): decision vector `n = N·nu = 192`, ≈ **16.5 MFLOP/tick**, ≈ **1.2 ms** at 6 FLOP/cyc. At `N=160`: `n=640`, ≈ **411 MFLOP**, ≈ **28 ms**. A 25× dynamic range across the headline sweep.

---

## 1. File layout

```
critter/mpc/
├── Makefile                 ~45 lines   3 build variants, objdump gate
├── critter.h               ~120 lines   ABI structs + all prototypes (ONE header)
├── thermal_model.c          ~95 lines   hardcoded RC params, linearize, discretize
├── lin_ref.c               ~110 lines   portable scalar kernels (correctness oracle)
├── lin_neon.c              ~135 lines   hand-vectorized fp64 NEON kernels
├── mpc.c                   ~165 lines   condense + Cholesky-ADMM solve + FLOP model
│                        ────────────
│                         ~625 lines     <-- THE SHIPPED UNIT
├── perfcnt.c                ~85 lines   perf_event_open self-monitoring (harness)
└── main.c                  ~130 lines   CSV replay, sweep driver, anti-DCE (harness)

critter/tools/
├── fit_rc.py                           one-shot offline param fit -> params.h
├── oracle.py                           numpy + OSQP validation oracle
└── plot.py                             matplotlib, reads the sweep CSVs

critter/data/
└── scenario_*.csv                      replay inputs (6 h, 30 s cadence)
```

Do not promise "under 600 lines" for the whole tree — the deliverable *unit* is ~625; the *harness* is another ~215 and is not part of the unit. Say that in the paper.

**One header, not seven.** Header proliferation is a real time sink for a junior dev. `critter.h` carries everything.

### What goes in each file

| File | Contents |
|---|---|
| `critter.h` | `critter_obs_t`, `critter_plan_t`, `critter_perf_t`, `mpc_cfg_t`, `mpc_ws_t`, `typedef double creal`, kernel prototypes, `MPC_FLOPS()` |
| `thermal_model.c` | Hardcoded `C_a, C_m, G_am, G_env, G_zz, UA_c, T_sup`; `model_f()` (nonlinear RHS); `model_linearize()` → `A_k,B_k,c_k` by analytic Jacobian; `model_disc3()` → 3rd-order Taylor discretization |
| `lin_ref.c` | `syrk_lt`, `chol_lt`, `trsv_l`, `trsv_lt`, `gemv_t`, `gemm_small` — plain triple loops, `restrict`-annotated. **This file is the numerical ground truth and the `novec`/`autovec` build variants.** |
| `lin_neon.c` | Same five functions, `#if` -guarded, `<arm_neon.h>`, 4×4 register tile with 8 `float64x2_t` accumulators, k-blocked |
| `mpc.c` | `mpc_ws_alloc()` (one 64B-aligned arena), `mpc_build()` (Φ, y_free, H, g), `mpc_solve()` (Cholesky + K ADMM iters), `mpc_flops()`, `mpc_step()` (the one public entry point) |
| `perfcnt.c` | Group of ≤6 PMU counters via `perf_event_open(pid=0, cpu=-1)`, `exclude_kernel=1`; graceful degradation to `CLOCK_MONOTONIC_RAW` only |
| `main.c` | Arg parse (`--N --K --nz --ua --reps --variant --tag --csv`), CSV replay, repetition loop with per-rep input perturbation, checksum sink, CSV emit, `--selftest`, `--peak-probe`, `--dump-h` |

---

## 2. The structs (real, compilable C)

```c
/* ============================ critter.h ============================ */
#ifndef CRITTER_H
#define CRITTER_H
#include <stdint.h>
#include <stddef.h>

#define CRIT_MAX_ZONES  8
#define CRIT_MAX_N    256          /* caps the OUTPUT struct only          */
#define CRIT_NS_PER_ZONE 2         /* T_air, T_mass                        */

typedef double creal;              /* -DCRITTER_F32 swaps to float (stretch) */

/* -------- INPUT: what the memory-bound unit hands over each tick -------- */
typedef struct {
    uint64_t t_unix_ms;            /* timestamp of newest contributing sample */
    uint32_t seq;                  /* monotonically increasing tick id        */
    uint8_t  n_zones;              /* <= CRIT_MAX_ZONES                       */
    uint8_t  valid_mask;           /* bit z set => zone z reading is fresh    */
    uint16_t staleness_ms;         /* age of oldest contributing sample       */

    /* plant state -------------------------------------------------------- */
    float t_air [CRIT_MAX_ZONES];  /* degC, measured zone air                 */
    float t_mass[CRIT_MAX_ZONES];  /* degC, slow node, ESTIMATED by mem unit  */
    float t_ambient;               /* degC, plenum / outside                  */
    float t_supply;                /* degC, CRAC supply air                   */
    float q_it_kw[CRIT_MAX_ZONES]; /* kW, IT heat load estimate               */

    /* nearly-free health signals from the memory unit --------------------- */
    float duty_cycle;              /* 0..1, compressor on-fraction, last hour */
    float cycle_period_s;          /* mean on+off period, s                   */
    float ua_scale;                /* 1.0 = nameplate coil; <1 = fouled/low charge */

    /* setpoints ----------------------------------------------------------- */
    float t_ref[CRIT_MAX_ZONES];   /* degC target                             */
    float t_hi [CRIT_MAX_ZONES];   /* degC soft cap (ASHRAE A1 upper = 27)    */
} critter_obs_t;

/* -------- PERF: identical field set in all three Critter units ---------- */
typedef struct {
    uint64_t ns_wall;              /* CLOCK_MONOTONIC_RAW, always valid       */
    uint64_t cycles, instructions; /* PERF_TYPE_HARDWARE                      */
    uint64_t inst_spec;            /* r1B                                     */
    uint64_t ase_spec;             /* r74  <- vectorization evidence          */
    uint64_t vfp_spec;             /* r75                                     */
    uint64_t stall_backend;        /* r24                                     */
    uint64_t aux0, aux1;           /* run-B/run-C cache events, event id in tag*/
    double   flops_analytic;       /* closed form, see mpc_flops()            */
    double   bytes_dram_model;     /* compulsory boundary bytes, for roofline */
    uint64_t checksum;             /* anti-DCE sink over the whole plan       */
    uint32_t admm_iters;           /* fixed K, echoed for the record          */
    uint32_t counters_valid;       /* bitmask; 0 => PMU unavailable this run  */
    int32_t  cpu_temp_mC;          /* /sys/class/thermal/thermal_zone0/temp   */
    uint32_t throttled;            /* vcgencmd get_throttled, 0 = clean run   */
} critter_perf_t;

/* -------- OUTPUT: the plan ---------------------------------------------- */
typedef struct {
    uint32_t seq;                  /* echoes critter_obs_t.seq                */
    uint16_t horizon_N;
    uint8_t  n_zones;
    uint8_t  status;               /* 0 ok, 1 u clamped, 2 stale input,
                                      3 model rejected (||Ah|| too large)     */
    float    dt_s;

    /* the decision -------------------------------------------------------- */
    float u_sched[CRIT_MAX_N][CRIT_MAX_ZONES]; /* 0..1 coil modulation        */
    float t_pred [CRIT_MAX_N][CRIT_MAX_ZONES]; /* degC predicted zone air     */

    /* scalars the operator and the paper actually read -------------------- */
    float u_now[CRIT_MAX_ZONES];   /* == u_sched[0][*]; the thing you apply   */
    float t_peak_pred;             /* max over horizon and zones, degC        */
    float margin_c;                /* min(t_hi - t_pred) over horizon, degC   */
    float cool_energy_kwh;         /* integral of commanded cooling           */
    float j_opt;                   /* final QP objective value                */
    float kkt_primal, kkt_dual;    /* residuals after the fixed K iterations  */
    float lead_time_s;             /* first time index where margin_c < 1 C   */

    critter_perf_t perf;
} critter_plan_t;

/* -------- CONFIG + WORKSPACE -------------------------------------------- */
typedef struct {
    int   N, nz, K;                /* horizon, zones, fixed ADMM iterations   */
    creal dt_s;                    /* 30.0                                    */
    creal q_track, r_input, r_rate;/* cost weights                            */
    creal rho, sigma, alpha;       /* ADMM: 1.0, 1e-6, 1.6                    */
} mpc_cfg_t;

typedef struct {                   /* ONE 64B-aligned arena, sized at init    */
    void  *arena; size_t arena_bytes;
    int    n, m, nx, nu, ny;       /* n=N*nu, m=N*ny, nx=2*nz                 */
    creal *Ak, *Bk, *ck;           /* N * (nx*nx, nx*nu, nx)                  */
    creal *Phi;                    /* m x n, row-major, ROW-SCALED by sqrt(Q) */
    creal *yfree, *H, *g, *L;      /* m ; n*n ; n ; n*n (chol in place of H)  */
    creal *U, *Ut, *z, *y, *rhs;   /* n each, ADMM state (warm-started)       */
} mpc_ws_t;

int    mpc_ws_alloc (mpc_ws_t*, const mpc_cfg_t*);
void   mpc_ws_free  (mpc_ws_t*);
void   mpc_step     (mpc_ws_t*, const mpc_cfg_t*,
                     const critter_obs_t*, critter_plan_t*);
double mpc_flops    (const mpc_cfg_t*);
#endif
```

`critter_plan_t` is 256×8×4×2 ≈ 16 KB. **Allocate it static or on the heap, never on the stack.**

### FLOP model (put this in `mpc.c` verbatim — the paper's numbers must be reproducible)

```c
double mpc_flops(const mpc_cfg_t *c){
    double N=c->N, nu=c->nz, ny=c->nz, nx=2.0*c->nz, K=c->K;
    double n=N*nu, m=N*ny;
    double f_lin  = N*(4.0*nx*nx + 20.0*nx);              /* Jacobians       */
    double f_disc = N*(3.0*2.0*nx*nx*nx);                 /* Taylor-3        */
    double f_phi  = 2.0*nx*nx*nu*N*(N-1)/2.0              /* A_k * V         */
                  + 2.0*ny*nx*nu*N*(N+1)/2.0;             /* C * V           */
    double f_free = 2.0*nx*nx*N;
    double f_syrk = n*n*m;                                /* lower tri SYRK  */
    double f_grad = 2.0*m*n;
    double f_chol = n*n*n/3.0;
    double f_admm = K*(2.0*n*n + 8.0*n);                  /* 2 trsv + vecops */
    return f_lin+f_disc+f_phi+f_free+f_syrk+f_grad+f_chol+f_admm;
}
```

`bytes_dram_model` = `sizeof(critter_obs_t) + sizeof(critter_plan_t) + max(0, arena_bytes − 2MB)`. Report DRAM-level AI (huge) **and** L1-refill-derived AI (from `L1D_CACHE_REFILL × 64`) — the second is the informative one and is still far above the ridge.

---

## 3. Day 1 — Correctness and the harness (hours 1–8)

> **Day-1 philosophy: at the end of hour 8 you must have a complete, submittable result set with zero NEON code.** Day 2 is strictly additive. If Day 2 is a total loss you still have a paper.

### H1 — Skeleton that builds and runs on the Pi
Create the tree, write `Makefile` and `critter.h` in full, stub `mpc_step()` so it fills `plan` with a deterministic pattern. `make VAR=novec && ./build/novec/critter_mpc --selftest` must print and exit 0.
**Checkpoint H1:** it compiles on the actual Pi 5 with `-mcpu=cortex-a76` and runs. Toolchain risk is retired in hour 1, not hour 15.

### H2 — Thermal model
Hardcode params from `tools/fit_rc.py` output (already produced offline). Per zone:

```
C_a·dTa_z/dt = Q_it_z + (Tm_z−Ta_z)G_am + (Tamb−Ta_z)G_env
             + Σ_{z' adj}(Ta_z'−Ta_z)G_zz − s_z·(ua_scale·UA_c)·(Ta_z − T_sup)
C_m·dTm_z/dt = (Ta_z − Tm_z)·G_am
```

`model_linearize()` builds A_k, B_k analytically (the `s_z·UA_c/C_a` term lands on the `Ta_z` diagonal of A and the `−(UA_c/C_a)(Ta_bar_z − T_sup)` on the B column) and computes the affine offset **numerically** as `c_k = f(x̄,ū) − A_k x̄ − B_k ū` so there is no algebra to get wrong. Discretize with 3rd-order Taylor `Ad = I + Ah + (Ah)²/2 + (Ah)³/6`; assert `‖Ah‖_∞ ≤ 0.25` and set `status=3` otherwise.
**Checkpoint H2:** free response over 60 steps from a step input matches `tools/oracle.py` (`scipy.linalg.expm`) to < 1e-9.

### H3 — Measurement harness, BEFORE the solver
`perfcnt.c` + the peak probe. Write the probe first because it validates the harness against a *known* answer:

```c
/* 8 independent chains, 32 fp64 flops per iteration. Named vars, not an array. */
double peak_probe_f64(long iters){
    float64x2_t a0=vdupq_n_f64(1),a1=vdupq_n_f64(2),a2=vdupq_n_f64(3),a3=vdupq_n_f64(4);
    float64x2_t a4=vdupq_n_f64(5),a5=vdupq_n_f64(6),a6=vdupq_n_f64(7),a7=vdupq_n_f64(8);
    float64x2_t b=vdupq_n_f64(1.0000001), c=vdupq_n_f64(0.9999999);
    for(long t=0;t<iters;t++){
        a0=vfmaq_f64(a0,b,c); a1=vfmaq_f64(a1,b,c); a2=vfmaq_f64(a2,b,c); a3=vfmaq_f64(a3,b,c);
        a4=vfmaq_f64(a4,b,c); a5=vfmaq_f64(a5,b,c); a6=vfmaq_f64(a6,b,c); a7=vfmaq_f64(a7,b,c);
    }
    return vaddvq_f64(a0)+vaddvq_f64(a1)+vaddvq_f64(a2)+vaddvq_f64(a3)
         + vaddvq_f64(a4)+vaddvq_f64(a5)+vaddvq_f64(a6)+vaddvq_f64(a7);
}
```

**Checkpoint H3 (hard gate):** `./critter_mpc --peak-probe` reports **7.6–8.0 fp64 FLOP/cyc**. If you get ≈4.0 you have 4 chains not 8, or the compiler spilled. If you get ≈2.0 your `-mcpu` is wrong. **Do not proceed past hour 3 until this number is right — it is the denominator of every percentage in your paper.**

`perf_event_open` sketch:

```c
static int open_ctr(uint32_t type, uint64_t cfg, int gfd){
    struct perf_event_attr a; memset(&a,0,sizeof a);
    a.type=type; a.size=sizeof a; a.config=cfg;
    a.disabled = (gfd==-1); a.exclude_kernel=1; a.exclude_hv=1; a.inherit=0;
    a.read_format = PERF_FORMAT_GROUP|PERF_FORMAT_ID
                  | PERF_FORMAT_TOTAL_TIME_ENABLED|PERF_FORMAT_TOTAL_TIME_RUNNING;
    return (int)syscall(__NR_perf_event_open,&a,0,-1,gfd,0);
}
```
Open ≤ **6 programmable events** (A76 has 6 PMEVCNTR + a dedicated cycle counter — a 7th event silently multiplexes). Always check `time_enabled == time_running`; if not, set `counters_valid=0`. If `perf_event_open` returns −1, degrade to wall clock and keep going.

### H4 — Scalar kernels
`lin_ref.c`: `syrk_lt`, `chol_lt` (Crout form, row-major, vectorizable inner dots), `trsv_l` (column-axpy form), `trsv_lt` (row-dot form), `gemv_t`, `gemm_small`. Choose the *axpy/dot* formulations now — they are the ones that vectorize on Day 2, so the Day-2 port is mechanical.
**Checkpoint H4:** `--test-lin` checks each kernel against a naive reference on random SPD matrices, n ∈ {7, 8, 64, 192}, max rel err < 1e-12. Odd n proves your tail handling works before NEON makes tails painful.

### H5 — Condensing
Build Φ by propagating an `nx × nu` impulse block forward: `V = B_j`, emit `C·V` into block-row `j+1`, then `V ← A_{j+1}·V`, repeat. Row-scale Φ by `sqrt(q_track)` in place so `H = Φᵀ Φ + R̄` is a **pure SYRK**. Add the Δu rate penalty as a block-tridiagonal contribution `r_rate·DᵀD` (~12 lines). Add `(rho + sigma)·I` to the diagonal.
**Checkpoint H5:** `--dump-h` writes H and g as raw f64; `oracle.py` builds the same matrices in numpy from the same params. `max|ΔH|/|H| < 1e-10`.
**This checkpoint is the single most schedule-protective thing in the plan.** It converts "is my MPC right?" from a two-day debugging swamp into a diff.

### H6 — Cholesky + ADMM
Because the only constraints are box constraints on `u`, the OSQP constraint matrix is `A = I` and the whole thing collapses to:

```
L = chol(H)                                    /* once per tick */
for k = 1..K:
    rhs = sigma*U - g + rho*(z - y)
    Ut  = L^-T (L^-1 rhs)                      /* 2 triangular solves */
    U   = alpha*Ut + (1-alpha)*U
    z   = clamp(U + y, 0, 1)
    y   = y + U - z
```

Warm-start `U, z, y` from the previous tick (3 lines, physically correct, free). **K is fixed. There is no residual-based early exit.**
**Checkpoint H6:** with bounds widened to ±1e9, `U*` equals `numpy.linalg.solve(H, -g)` to 1e-8. With real bounds it matches OSQP to 1e-5 after K=200.

### H7 — Closed loop
Receding-horizon driver over a 6-hour replay scenario. Plot zone temps and `u`.
**Checkpoint H7:** temps track setpoint; at `ua_scale = 0.5` the controller saturates and pre-cools ahead of the load ramp rather than after it. This is the "it is a real controller" gate.

### H8 — FREEZE AND BANK
`git tag v0-scalar`. Run the full N sweep with `VAR=novec` and `VAR=autovec`. Copy the CSVs **off the Pi**.
**Checkpoint H8:** you have a complete result set. Day 2 can now fail without costing you the assignment.

---

## 4. Day 2 — Performance (hours 9–16)

### H9 — The SYRK micro-kernel
4×4 register tile, **8 `float64x2_t` accumulators** (A76 FMA latency ≈4 cyc, throughput 2/cyc ⇒ you need exactly 8 chains in flight):

```c
/* C_lower[n x n] += A[m x n]^T * A ; row-major A, lda = n */
static inline void syrk_tile4x4(int k0,int k1,const creal *restrict A,int lda,
                                int i0,int j0,creal *restrict C,int ldc)
{
    float64x2_t c00=vdupq_n_f64(0),c01=vdupq_n_f64(0),c10=vdupq_n_f64(0),c11=vdupq_n_f64(0);
    float64x2_t c20=vdupq_n_f64(0),c21=vdupq_n_f64(0),c30=vdupq_n_f64(0),c31=vdupq_n_f64(0);
    const creal *ap = A + (size_t)k0*lda;
    for(int k=k0;k<k1;++k, ap+=lda){
        float64x2_t b0=vld1q_f64(ap+j0),   b1=vld1q_f64(ap+j0+2);
        float64x2_t a0=vld1q_f64(ap+i0),   a1=vld1q_f64(ap+i0+2);
        c00=vfmaq_laneq_f64(c00,b0,a0,0);  c01=vfmaq_laneq_f64(c01,b1,a0,0);
        c10=vfmaq_laneq_f64(c10,b0,a0,1);  c11=vfmaq_laneq_f64(c11,b1,a0,1);
        c20=vfmaq_laneq_f64(c20,b0,a1,0);  c21=vfmaq_laneq_f64(c21,b1,a1,0);
        c30=vfmaq_laneq_f64(c30,b0,a1,1);  c31=vfmaq_laneq_f64(c31,b1,a1,1);
    }
    /* accumulate into C[i0+r][j0..j0+3] for r = 0..3 */
}
```

Per k-step: **8 FMAs = 32 flops in 4 cycles = exactly 8 FLOP/cyc**, with only 4 loads (1/cyc, well under the 2 load-AGU limit) and 12 of 32 vector registers used.

**The k-blocking is not optional** — without it the outer loops stream all of Φ once per tile and you will measure ~2 FLOP/cyc and not understand why:

```c
for(int kb=0; kb<m; kb+=64)                 /* 64 rows of Phi -> ~100 KB, L2-resident */
  for(int i0=0; i0<n; i0+=4)
    for(int j0=0; j0<=i0; j0+=4)
      syrk_tile4x4(kb, min(kb+64,m), Phi, n, i0, j0, H, n);
```

### H10 — objdump gate + measure SYRK alone
**Checkpoint H10 (hard gate):** isolated in-cache SYRK ≥ **5.5 fp64 FLOP/cyc** (69% of peak). If < 3.0 you have a blocking or spill problem — fix it here, before touching any other kernel. **Set a hard stop: if you are not ≥3× the scalar version by the end of hour 11, take fallback (b) in §6 and move on.**

### H11 — Cholesky + triangular solves
Vectorize the Crout inner dot (4 accumulators is enough — Cholesky is only ~15% of FLOPs), the forward solve as a NEON axpy, the transpose solve as a NEON dot. ~50 lines total. Re-run `--test-lin` after each.

### H12 — `gemv_t` and the Φ-build inner loop
Measure whole-solve FLOP/cyc.
**Checkpoint H12:** whole-solve at N=96 lands in **4–6 FLOP/cyc**. Do *not* chase 8.0 for the whole solve — the Θ(n²) triangular solves cannot reach it, and the microbenchmark-vs-application gap is a genuinely good paper paragraph, not a failure.

### H13 — Per-kernel profile decomposition
Five `region_t` counters (a second perf fd read, or `CLOCK_MONOTONIC_RAW` if the PMU is uncooperative) around Φ-build, SYRK, Cholesky, ADMM, and everything else.
**Checkpoint H13:** you can state the % of cycles in each of the five kernels at N=48 and at N=160, and explain why the split moves (SYRK and Cholesky are Θ(n³); Φ-build and ADMM are Θ(n²), so the *effective* scaling exponent is ≈2.7, not 3.0 — measure and report it).

### H14 — The three-variant matrix
Same source, three binaries via `-DLIN_BACKEND`:

| Variant | Flags | What it proves |
|---|---|---|
| `novec` | `-O3 -fno-tree-vectorize -DLIN_BACKEND_REF` | scalar floor |
| `autovec` | `-O3 -DLIN_BACKEND_REF -fopt-info-vec-optimized` | what GCC does unaided |
| `neon` | `-O3 -DLIN_BACKEND_NEON` | hand-vectorized |

Save the `-fopt-info-vec-missed` log — the *reasons* GCC declined to vectorize are paper content.

### H15 — Run the full sweep
Kick off the sweep script (§5). Watch thermals while it runs (~30–45 min).

### H16 — Freeze
`git tag v1`. Write `README.md` with the exact reproduction commands. Back up all CSVs off the Pi. Verify the Day-1 `v0-scalar` results are still on disk.

---

## 5. Build flags and the objdump verification

### Makefile core

```make
CC     ?= gcc
STD     = -std=gnu11                 # NOT -std=c11: ISO mode forces -ffp-contract=off
ARCH    = -mcpu=cortex-a76           # sets arch AND tune; not -mtune
OPT     = -O3 -ffp-contract=fast -fno-math-errno -fno-trapping-math -funroll-loops
WARN    = -Wall -Wextra -Wvla -Wno-unused-parameter
CFLAGS  = $(STD) $(ARCH) $(OPT) $(WARN) -MMD -MP $(EXTRA)
LDLIBS  = -lm
```

**Deliberately NOT used:**
- `-ffast-math` — it permits reduction reassociation, which (a) breaks bit-comparison against the Python oracle and (b) makes the autovec-vs-hand comparison unfair and run-to-run unstable. `-ffp-contract=fast` alone gives you FMA without that.
- `-march=native` / `-mcpu=native` — works on-device but silently produces a different binary if you ever cross-compile. Pin the core name.
- LTO — a whole class of "where did my function go" objdump confusion for zero measured benefit here.

### Run-environment pinning (put this in `run.sh`, use it for every measurement)

```bash
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
sudo sysctl -w kernel.perf_event_paranoid=0        # persist in /etc/sysctl.d/99-perf.conf
taskset -c 2 chrt -f 50 ./build/neon/critter_mpc "$@"
cat /sys/class/thermal/thermal_zone0/temp; vcgencmd get_throttled   # log both, every run
```

### The objdump check — run it as a Makefile target, not by hand

```make
verify: build/neon/lin_neon.o build/novec/lin_ref.o
	@objdump -d --no-show-raw-insn build/neon/lin_neon.o \
	  | awk '/<syrk_lt_neon>:/,/^$$/' > build/syrk.asm
	@echo "-- 128-bit fp64 FMA (want >= 8):"; grep -cE '\bfmla[[:space:]]+v[0-9]+\.2d' build/syrk.asm
	@echo "-- scalar fp64 FMA in hot loop (want 0):"; grep -cE '\bfmadd[[:space:]]+d[0-9]+' build/syrk.asm
	@echo "-- q-reg loads (want >= 4):"; grep -cE '\b(ldr|ldp)[[:space:]]+q[0-9]+' build/syrk.asm
	@echo "-- vector spills to stack (want EMPTY):"; grep -E '\bstr[[:space:]]+q[0-9]+, \[sp' build/syrk.asm || echo "  none - good"
	@echo "-- contraction reached the scalar file (want > 0):"
	@objdump -d build/novec/lin_ref.o | grep -cE '\bfmadd|\bfmsub|\bfnmadd'
```

**Negative control — do this once and put it in the paper as a two-row table:**

```bash
make clean && make VAR=novec EXTRA=-ffp-contract=off verify   # expect 0 fmadd, paired fmul+fadd
make clean && make VAR=novec verify                           # expect >0 fmadd
```
That single before/after is the cleanest possible evidence that your flags did what you claim.

**Runtime confirmation** (belt and braces — objdump proves the instruction is *present*, `ase_spec` proves it *executed*):
```bash
perf stat -e r11,r1B,r74,r75 taskset -c 2 ./build/neon/critter_mpc --N 96 --reps 20
# ase_spec/inst_spec should jump from ~0.02 (novec) to ~0.35+ (neon)
```

---

## 6. Anti-dead-code-elimination (do this in H3, not later)

Three independent defenses, all required:

1. **Perturb the input every rep** — `obs.t_air[0] += 1e-9f * (float)rep;` A loop-invariant call gets hoisted out of the repetition loop and you measure 1 solve instead of 50.
2. **Consume the output** — XOR-fold all of `critter_plan_t` into `perf.checksum` and *print it*.
3. **Compiler barrier** — `__asm__ volatile("" :: "r"(&plan) : "memory");` after each solve.

**Two automatic sanity gates in `main.c`:**
- `--reps 10` vs `--reps 50` must give total cycles that scale ~5×. If not, DCE.
- `flops_analytic / cycles` must land in **[0.3, 8.2]** for fp64. 40 FLOP/cyc means the work vanished; 0.05 means something is pathologically wrong. Abort with a loud message either way.

---

## 7. The 5 analysis days

### Day 3 — Sweeps and the sparse-QP verification

**Sweep 1 (headline): horizon N ∈ {8, 16, 24, 32, 48, 64, 96, 128, 160}**, `nz=4`, `K=60`, 50 reps, report median and min.
- Fig 1a: cycles vs N, log-log, with fitted exponent (expect ≈2.7) and the analytic Θ(N³)+Θ(N²) curve overlaid.
- Fig 1b: FLOP/cyc vs N, with the measured 8.0 peak as a horizontal line and **vertical lines at the N where `8n²` crosses 512 KB (L2) and 2 MB (L3)**.
- Fig 1c: **roofline**, both AI definitions (DRAM-compulsory and L1-refill-derived), ridge at 1.55 fp64 FLOP/byte.
- Fig 1d: **closed-loop cost J and peak overtemp vs N** — this is what makes N a *fidelity* knob and not a replica knob. Expect J to fall steeply to N≈60 (≈30 min, the mass time constant) then flatten. Say explicitly: *the horizon that the controller actually needs is the horizon that costs the most.*

**Sweep 2: backend variant × N.** Three curves on Fig 1b. Report the hand-vs-auto ratio and quote the `-fopt-info-vec-missed` reasons.

**Sweep 3: K ∈ {10, 20, 40, 60, 100}** at N=96. Separates the Θ(n²) ADMM from the Θ(n³) build. Second panel: KKT residual vs K — the second fidelity axis, and the justification for a *fixed* K.

**Sweep 4: nz ∈ {1, 2, 4, 8}** at fixed N=64. Changes nx, nu, ny together.

**Day 3, ~1 hour: verify the sparse-QP claim instead of asserting it.** Install OSQP on the Pi **outside the deliverable** (explicitly allowed as an offline oracle), hand it the *identical* problem in sparse KKT form, and `perf stat` it:
```bash
perf stat -e r11,r08,r1B,r74,r10,r24 taskset -c 2 python3 tools/oracle.py --osqp --N 96
```
Report OSQP's FLOP/cyc, branch MPKI and backend-stall % next to yours. This converts "suspected memory-latency-bound" into a measured, cited number for ~1 hour of work, and it is one of the strongest results in the paper.

### Day 4 — The compute-bound proof and the three-way contrast

**Four independent lines of evidence. This is the spine of the paper.**

1. **Arithmetic intensity.** Analytic AI ≈ 825 FLOP/byte at N=48 (DRAM level); L1-refill-derived AI still ≫ the 1.55 ridge. Plot both.
2. **Fraction of measured peak.** 4–6 of 8.0 fp64 FLOP/cyc = 50–75%. Compare against the 0.48 FLOP/cyc (1.5%) the MIQP measured — a **10× improvement in machine utilization on the same silicon**.
3. **Sensitivity, the strongest and cheapest test.** Two experiments:
   - **Clock scaling:** `1.5, 1.8, 2.1, 2.4 GHz`. Pi 5's LPDDR4X clock is independent of the CPU clock, so a compute-bound unit gives **slope 1.0 on log(time) vs log(1/f)** and a memory-bound unit gives slope < 1. Run all three Critter units.
   - **Bandwidth co-runner:** 3 threads of a streaming triad over 256 MB pinned to cores 0/1/3 while the unit runs on core 2. Expect **< 5% slowdown** for MPC and 2–3× for the memory unit.
   - **Control for thermals:** re-run the co-runner test with 3 copies of `--peak-probe` instead (same heat, no bandwidth pressure). If the compute co-runner also slows you, you measured throttling, not contention. Log `get_throttled` on every row and discard non-zero rows.
4. **Microarchitectural fingerprint.** Low branch MPKI, low `stall_backend`, high `ase_spec/inst_spec`.

**Fault-invariance (the anti-MIQP result).** Sweep `ua_scale ∈ {1.0, 0.85, 0.7, 0.55, 0.4}` at fixed N. Report cycles as mean ± σ/µ. **Expect σ/µ < 1%.** Put this directly beside the MIQP's 120,000× node collapse. The claim: *a fixed-iteration dense QP has worst-case-equals-average-case cost, which is what a real-time embedded controller requires; a branch-and-bound solver does not.*

### perf events — three runs, ≤6 programmable events each (A76 has 6 counters + cycle counter; a 7th silently multiplexes)

| Run | Events | Derived |
|---|---|---|
| A: pipeline | `cycles, instructions, inst_spec, ase_spec, vfp_spec, stall_backend` | IPC, FLOP/cyc, **vector fraction** = ase_spec/inst_spec, backend-stall % |
| B: cache | `cycles, l1d_cache, l1d_cache_refill, l2d_cache, l2d_cache_refill, ll_cache_miss_rd` | L1/L2 MPKI, **measured AI** = flops/(ll_cache_miss_rd×64) |
| C: control | `cycles, bus_access, mem_access, br_mis_pred_retired, stall_frontend, dp_spec` | DRAM GB/s, **branch MPKI** |

Raw codes if symbolic names are missing: `INST_SPEC=r1B, ASE_SPEC=r74, VFP_SPEC=r75, STALL_BACKEND=r24, STALL_FRONTEND=r23, L1D_CACHE=r04, L1D_CACHE_REFILL=r03, L2D_CACHE=r16, L2D_CACHE_REFILL=r17, BUS_ACCESS=r19, MEM_ACCESS=r13, BR_MIS_PRED_RETIRED=r22, DP_SPEC=r73`. Run `perf list | grep -i a76` first. **`ase_spec`/`vfp_spec` count speculatively-executed operations, not FLOPs** — they are vectorization evidence only; FLOPs come from `mpc_flops()`.

**A76 has no `FP_SCALE_OPS_SPEC` / `FP_FIXED_OPS_SPEC`** (those arrived with SVE-era cores). There is no hardware FLOP counter on this chip. Analytic FLOP counting is not a shortcut, it is the only option — say so in the methodology section.

### The three-way contrast — one table, identical rows

| Metric | I/O unit | Memory unit | **MPC unit** |
|---|---|---|---|
| IPC | | | |
| fp64 FLOP/cyc (% of 8.0 peak) | | | |
| Measured AI (FLOP/byte, LL-miss) | | | |
| Backend stall % | | | |
| Branch MPKI | | | |
| L2 MPKI | | | |
| DRAM GB/s (% of 12.4) | | | |
| **Δ runtime, bandwidth co-runner** | | | **< 5%** |
| **Δ runtime, 2.4 → 1.5 GHz (slope)** | | | **≈ 1.0** |
| **σ/µ of cycles across fault severity** | | | **< 1%** |

Plus: all three units as three points on **one roofline chart**, and a normalized "sensitivity fingerprint" bar chart of the three Δ-rows. Those two figures carry the whole three-unit argument.

*Optional, high value if a USB power meter is available:* add a "mJ per control decision" row. Skip if it means buying hardware.

### Days 5–7 — Writing
Figures are already made. Reserve Day 7 morning for one re-run of any measurement a reviewer would question.

---

## 8. DE-RISKING

### Risk 1 — The NEON micro-kernel doesn't hit peak (most likely)
**Why:** register spills, missing k-blocking, tail handling for `n % 4 ≠ 0`, or `vfmaq_laneq_f64` fighting the register allocator.
**Primary mitigation:** the `v0-scalar` tag at hour 8, with the full sweep already banked. NEON is strictly additive.
**Fallbacks, in order:**
- (a) Drop 4×4 → 4×2 tile (4 accumulators). Ceiling drops to ~4 FLOP/cyc but it always works.
- (b) **Abandon intrinsics.** Add `restrict`, `__builtin_assume_aligned(p,64)`, and `#pragma GCC unroll 4` to the scalar SYRK and let the auto-vectorizer do it. The paper then reports **autovec vs novec** instead of hand vs autovec — still a completely valid and publishable comparison, and the `-fopt-info-vec-missed` log becomes the analysis.
- (c) Hand-vectorize SYRK **only**; leave Cholesky and TRSV scalar. SYRK is 43% of FLOPs at N=48 and 64% at N=160.
**Hard stop:** if hand-NEON SYRK is not ≥3× scalar by the end of **hour 11**, take fallback (b) and spend the recovered hours on H13's profile decomposition instead.

### Risk 2 — The controller misbehaves (oscillates, saturates, temps run away)
**Why:** discretization step too large, weights untuned, rate penalty too weak, bad RC params.
**Mitigation:** H5's oracle diff catches the *math*; H7 catches the *tuning*. They fail differently, so you know which one you have.
**Fallbacks, in order:** drop the Δu rate penalty; raise `r_input` until it is sluggish-but-stable; collapse to `nz=1` (nx=2, nu=1) and recover problem size by raising N.
**The relief valve:** *the compute result does not depend on the control being well-tuned.* A sluggish controller executes the identical instruction stream and produces the identical FLOP count. If you are out of time, ship a conservatively-tuned controller, state the tuning limitation in one sentence, and keep every performance number. Only Fig 1d (J vs N) needs a well-behaved controller, and a sluggish one still shows the right *shape*.

### Risk 3 — `perf_event_open` is unavailable, or multiplexing silently
**Why:** `perf_event_paranoid=3`, missing `linux-perf` package, or >6 events in a group.
**Mitigation:** the harness must produce the headline number with **zero** PMU counters — `CLOCK_MONOTONIC_RAW` + analytic FLOPs is sufficient for FLOP/s. Counters are enrichment.
**Fallbacks, in order:** `sudo sysctl kernel.perf_event_paranoid=0`; external `perf stat` around the binary with a `--reps 0` baseline subtracted; and if the PMU is genuinely dead, **the clock-scaling and co-runner experiments establish compute-boundedness with no counters at all** — they are timing experiments. Never let a counter problem block a sweep.

### Risk 4 (brief) — Thermal throttling corrupts a 5-day campaign
Log `thermal_zone0/temp` and `vcgencmd get_throttled` into **every CSV row**; discard rows with `throttled != 0`; use an active cooler; insert 2 s idle between reps; re-run the first configuration at the end of each sweep as a drift check.

### If Day 2 goes badly — cut in this exact order
1. fp32 variant (it was always optional)
2. Cholesky/TRSV NEON — keep SYRK only
3. `nz` sweep — fix `nz=4`
4. `K` sweep — fix `K=60`
5. The entire NEON path — ship scalar, reframe §"vectorization" as an auto-vectorization analysis
6. Multi-zone → single zone, recover size via N

**Never cut, under any circumstance:** the **N sweep**, the **clock-scaling test**, the **bandwidth co-runner test**, the **fault-invariance sweep**. Those four are the paper. Everything else is supporting material.

---

## 9. What to SKIP deliberately

| Skipped | Why, in one line |
|---|---|
| Online system identification / RLS / EKF parameter update | Scope creep that eats a day; parameters come from one offline `fit_rc.py` run and are hardcoded. |
| Sparse QP / sparse LDLᵀ KKT factorization | Pointer chasing and indirect indexing make it memory-latency-bound — it is precisely the thing you are arguing against, and you *measure* it via OSQP offline on Day 3 rather than building one. |
| Any integer / binary decision (compressor staging) | Measured trap: 0.48 FLOP/cyc, latency-bound on branches, and its cost is *anti*-correlated with the fault. |
| Adaptive ρ, residual-based early termination | Makes cost data-dependent, destroying the determinism and fault-invariance results that are your best content. (Warm-starting is kept — it improves quality at *fixed* cost.) |
| Hard temperature constraints / slack variables | With box constraints on `u` only, the QP is **always feasible**; adding state constraints adds an infeasibility-handling story worth zero paper content. `t_hi` is reported as `margin_c`, not enforced. |
| Multi-threading (OpenMP / pthreads) | The roofline argument is a single-core argument; 4 cores changes the ridge and muddies the memory story. Optionally add one 4-thread bar at the very end. |
| Constant-power cooling model | Known trap — identical eigenvalues in both compressor phases, so the model cannot distinguish the fault it exists to detect. |
| Kalman filter / state estimation | The memory unit supplies `t_mass`. If it doesn't, use a 3-line complementary filter, not an EKF. |
| SVE / SME / fp16 arithmetic | A76 has no SVE; fp16 Cholesky will fail numerically and cost you a day proving it. |
| CMake, meson, autotools | A 45-line Makefile builds 7 files. Build-system fighting is exactly the schedule risk you cannot absorb. |
| Unit-test framework (Unity, CMocka) | `assert()` behind a `--selftest` flag does the same job in zero setup time. |
| Compile-time-constant N (`#define N 48`) | Nine specialized binaries confound the sweep with code-layout differences; keep N a runtime argument with one arena allocation, and say so in the methodology. |
| LTO, PGO | Obscures objdump verification and buys nothing when 90% of cycles live in five hand-written kernels. |
| Real HVAC hardware in the loop | CSV replay only. Determinism is worth more to this paper than realism. |

---

### Sources
- [Linux perf Cortex-A76 PMU event definitions](https://github.com/torvalds/linux/tree/master/tools/perf/pmu-events/arch/arm64/arm/cortex-a76)
- [Using Perf to enable PMU functionality on Armv8-A CPUs](https://developer.arm.com/community/arm-community-blogs/b/architectures-and-processors-blog/posts/p2-perf-pmu-feature-armv8-cpus)
- [BCM2712 — Raspberry Pi documentation](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/processors/bcm2712.adoc)
- [Raspberry Pi performance monitoring / PMU (kernel)](https://deepwiki.com/raspberrypi/linux/7-performance-monitoring)
- [perf_events FAQ — paranoid levels and self-monitoring](https://web.eece.maine.edu/~vweaver/projects/perf_events/faq.html)