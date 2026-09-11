# Critter Unit 3 — MPC design: states, inputs, estimation, data

Companion to [build-plan.md](build-plan.md). That document says *how* to build it hour by hour;
this one says *what the thing actually is* — the control problem, precisely stated.

Nothing here cuts scope from the build plan. Where this document differs from the plan, it is
adding something the plan left implicit (§3 in particular).

---

## 1. The plant

One machine room ("zone"), lumped two-node RC. Repeated per zone up to `CRIT_MAX_ZONES = 8`.

```
                    Q_IT (servers)
                        │
   T_out ──[UA_env]── T_air ──[UA_mass]── T_mass
                        │
                   [s·UA_coil]
                        │
                    T_supply (CRAC)
```

Continuous-time dynamics:

```
C_air  · dT_air/dt  = UA_env·(T_out − T_air)
                    + UA_mass·(T_mass − T_air)
                    + Q_IT
                    − u · UA_coil · (T_air − T_supply)      ← CONDUCTANCE, not constant power

C_mass · dT_mass/dt = UA_mass·(T_air − T_mass)
```

**The conductance form is load-bearing and is not a style preference.** If cooling enters as a
constant power term `u·Q_cool`, the system matrix `A` is identical whether cooling is on or off,
so the open- and closed-loop time constants are the same by construction. This was verified by
simulation: τ ratio 0.964 both healthy and faulted. With cooling as a conductance, `u` multiplies
a term in `A`, the eigenvalue genuinely moves, and both the control problem and any downstream
fault story are non-degenerate.

Discretised at `dt = 30 s` by matrix exponential (or a few Euler substeps) to give `A_k`, `B_k`.

---

## 2. States, inputs, disturbances — the precise definitions

### 2.1 State vector `x` — 2 per zone, `nx = 2·n_zones`

| # | Symbol | Meaning | Units | Measured? |
|---|---|---|---|---|
| x₁ | `T_air` | Zone air temperature at the rack inlet | °C | **Yes** — this is the sensor Unit 1 reads |
| x₂ | `T_mass` | Bulk thermal-mass temperature (slab, walls, rack steel) | °C | **No — hidden.** See §3 |

Two states, one sensor. That asymmetry is the entire reason §3 exists.

`T_mass` is what gives the room its ride-through: it is the energy buffer that keeps the room
survivable for minutes after cooling stops. A controller that cannot see it cannot reason about
ride-through, which is one of the outputs that makes this unit worth having.

### 2.2 Control input `u` — 1 per zone, `nu = n_zones`

| Symbol | Meaning | Units | Range |
|---|---|---|---|
| `u` | Commanded coil modulation / cooling demand | dimensionless | `0.0 … 1.0` |

`u = 0` is no cooling; `u = 1` is full nameplate coil capacity. It enters the dynamics as
`u · UA_coil · (T_air − T_supply)`.

**On bang-bang plants** — most machine rooms — the physical compressor is on/off, not continuous.
`u` is then interpreted as the **duty fraction over one 30 s control interval**, and a thin PWM /
hysteresis layer downstream converts `u = 0.4` into "on for 12 s of the next 30". This keeps the
optimisation a continuous QP.

That interpretation is not a dodge; it is the standard relaxation, and it is specifically what
lets us avoid the mixed-integer formulation. A branch-and-bound MIQP over the true binary
sequence was measured at **0.48 FLOP/cycle (1.5% of peak)** and is latency-bound — see
[roofline-analysis.md](roofline-analysis.md). Relaxing to duty fraction is both better engineering
and the difference between a compute-bound unit and a branch-bound one.

Constraints, enforced by the ADMM projection step:

```
0 ≤ u_k ≤ 1                    for every step k in the horizon
|u_k − u_{k−1}| ≤ Δu_max       optional rate limit (compressor wear / anti-short-cycle)
```

### 2.3 Measured / forecast disturbances `d` — inputs, but NOT decision variables

These enter the prediction but the controller cannot choose them.

| Symbol | Meaning | Units | Source |
|---|---|---|---|
| `Q_IT` | IT heat load | kW | PDU reading if available, else estimated (§3.3) |
| `T_out` | Outdoor / condenser-inlet / plenum temperature | °C | Second sensor, or a forecast |
| `T_supply` | CRAC supply-air temperature | °C | Sensor, or assumed constant |
| `COP(T_out)` | Coil effectiveness vs outdoor temperature | — | Manufacturer curve, evaluated over the horizon |

**`COP` is what makes the model time-varying, and that matters for more than physics.** With a
strictly time-invariant plant, the condensed Hessian `H` is constant, computable once offline —
so rebuilding it every control step would be make-work, and a reviewer would say so. Letting
`B_k = B · cop(T_out,k)` vary across the forecast horizon makes the `O(N³)` condensing genuinely
necessary, *and* is better control, because a chiller really is less effective on a hot afternoon.

### 2.4 Output and the cost function

Measured output: `y = T_air` (i.e. `C = [1  0]` per zone).

```
minimise   Σ_k   q_track·(T_air,k − T_ref)²        track the setpoint
                + r_input·u_k²                     energy
                + r_rate·(u_k − u_{k−1})²          smoothness / compressor wear
subject to the dynamics and 0 ≤ u ≤ 1
```

`T_hi = 27 °C` (ASHRAE TC 9.9 recommended upper limit) is carried as a soft cap — penalised
heavily rather than hard-constrained, so the QP always stays feasible. A hard constraint that
cannot be met returns no answer at all, which is the worst possible behaviour for a controller
whose job is to tell you the room is in trouble.

---

## 3. State estimation — yes, required

**Short answer: yes, and it belongs in this unit.**

Two states per zone, one sensor per zone. `T_mass` is never measured and must be reconstructed.
Without an estimator you have nothing to initialise the prediction from, and MPC is a
prediction-driven controller — a wrong `x₀` corrupts the entire horizon.

### 3.1 A correction to the build plan

`critter_obs_t` in [build-plan.md](build-plan.md) §2 declares:

```c
float t_mass[CRIT_MAX_ZONES];  /* degC, slow node, ESTIMATED by mem unit */
```

**This should not be the memory unit's job.** Reconstructing `T_mass` from `T_air` requires the
thermal dynamics model — `A`, `B`, `C` — which is *our* model. The memory unit does outlier
removal and summarisation; it has no dynamics model and no reason to acquire one. Handing this
across the boundary means either (a) duplicating our model in their unit, or (b) receiving a
number nobody owns.

**Keep the field in the struct** — it is a useful debug channel and a place for a cold-start
seed — but treat the estimate as ours. The field becomes an *optional hint*, not a dependency.

### 3.2 The estimator: steady-state Kalman filter

```
x̂_k|k-1 = A·x̂_{k-1} + B·u_{k-1} + E·d_{k-1}      predict
x̂_k     = x̂_k|k-1 + L·(y_k − C·x̂_k|k-1)          correct
```

`L` is the **steady-state** Kalman gain, computed **once offline** in Python by solving the
discrete algebraic Riccati equation, then shipped as two constants per zone.

This is valid here because `A` and `C` are constant. Only `B` varies with COP, and the Kalman
gain does not depend on `B`. So there is no covariance propagation on device — no DARE solve at
runtime, no `P` matrix to carry.

**Cost: about 15 FLOP per zone per control step.** Against ~4.7 MFLOP for the QP at N=128, that
is 0.003% of the workload. It is free, and it does not perturb the compute-bound argument in
any measurable way. Roughly 20 lines of C.

### 3.3 Offset-free tracking (the part people forget)

A plain Kalman filter on the nominal model leaves **steady-state offset**: any unmodelled bias —
IT load higher than assumed, a fouled coil, a sensor offset — produces a persistent temperature
error that the controller never removes, because its model says it is already at setpoint.

Standard fix: augment the state with a constant disturbance `d̂` and estimate it too.

```
x_aug = [T_air, T_mass, d̂]        d̂ = lumped unmodelled heat, kW
```

with `d̂_{k+1} = d̂_k` (a random walk in the process noise). The estimator then attributes
persistent mismatch to `d̂` and the controller compensates. This is the Muske–Badgwell /
Pannocchia–Rawlings disturbance-model construction and is the standard reference.

This grows the per-zone state from 2 to 3, which is also why the build plan's `n=3` matters for
the roofline: SYRK cost scales with `n` while Cholesky does not, so `n=3` keeps the efficient
kernel (87% of peak) outweighing the sequential one (29%) by 3:1. **The state that makes the
control correct is the same state that keeps the workload compute-bound.** Worth a sentence in
the paper.

`d̂` has a second use: it *is* a fault signal. A slowly growing `d̂` means the room is absorbing
heat the model cannot account for.

---

## 4. Data — what we actually need

### 4.1 The key point: a controller cannot be evaluated on a recorded trace

This is the difference between this project and every anomaly-detection candidate considered
earlier, and it simplifies the data question enormously.

A recorded temperature trace has *the old controller's actions already baked into it*. The moment
our MPC chooses a different `u`, the real trajectory diverges from the recording and the recording
stops being valid. Closed-loop evaluation requires a **plant you can drive**, not data you can
replay.

So the primary artifact is a simulator, and it is something we write, not something we download.

### 4.2 Required: the plant simulator (~150 lines Python or C)

- 2R2C dynamics exactly as §1, cooling as a **conductance**
- Hysteresis compressor with anti-short-cycle minimum on/off timers (120 s)
- Sensor model: additive noise `N(0, 0.05 °C)`, quantised to the sensor LSB
- Emits `T_air` (what the controller sees) plus **ground truth** `T_mass`, `Q_IT`, fault state
  (what the controller does not see, and what we score against)

Ground truth is the point. It is the only way to answer "did the estimator reconstruct `T_mass`
correctly" — no public dataset can.

Reference parameters for a ~20 m³ / 3 kW equipment room, from the earlier ideation work:

| Symbol | Value | Basis |
|---|---|---|
| `C_air` | 250 kJ/K | 24 kJ/K air + ~226 kJ/K IT mass (~11 kJ/K per 2U server) |
| `C_mass` | 2500 kJ/K | Slab and walls, ~10× the fast node |
| `Q_IT` | 3000 W | → ~0.72 K/min open-loop rise |
| `Q_cool` | 6000 W | 2× oversized → ~50% steady duty |
| Setpoint / deadband | 24 °C / ±1.0 K | Gives a ~5.6 min compressor cycle |
| Anti-short-cycle | 120 s min on / min off | Modelica Buildings `Applications.DataCenters` |
| Thresholds | 27 °C / 32 °C | ASHRAE TC 9.9 recommended / A1 allowable |

### 4.3 Optional: real traces, for the paper's credibility section only

Not needed for the benchmark. Useful for arguing the model class is reasonable:

| Dataset | Use | Get it |
|---|---|---|
| NAB `machine_temperature_system_failure.csv` | Real equipment temperature ending in a real failure | `raw.githubusercontent.com/numenta/NAB/master/data/realKnownCause/` |
| UCI SML2010 | The cheapest real HVAC actuation signal | `archive.ics.uci.edu/static/public/274/sml2010.zip` |
| LBNL FDD RTU | Labelled fault type *and* severity | `fdddata.lbl.gov` (292 MB — skip unless the paper needs it) |

Two loader gotchas if NAB is used: it contains exactly one bad inter-sample gap of −3300 s (a
backward clock jump), and its values are very likely Fahrenheit — though since a time constant is
invariant under an affine unit change, this only matters if absolute °C thresholds are applied.

### 4.4 What the benchmark itself needs: nothing

Worth stating plainly, because it de-risks the schedule. The compute measurement is a **fixed
iteration count with no data-dependent branching**, by design. Runtime does not depend on the
input values. You can benchmark the solver on arbitrary plausible numbers and the FLOP/cycle
result is identical.

Data is needed for the *correctness* figure and the *motivation*, not for the performance result.
If day 2 runs late, the performance campaign is unaffected.

---

## 5. Summary — the control problem in one box

```
STATES (per zone, hidden except T_air)
    x = [ T_air ,  T_mass ,  d̂ ]              °C, °C, kW
         measured  estimated  estimated

CONTROL INPUT (per zone, what we solve for)
    u ∈ [0, 1]     coil modulation / duty fraction over a 30 s interval
                   optional rate limit |Δu| ≤ Δu_max

DISTURBANCES (known or forecast, not chosen)
    Q_IT , T_out , T_supply , COP(T_out)

MEASUREMENT
    y = T_air                                  one sensor per zone

ESTIMATOR
    steady-state Kalman filter, gain precomputed offline
    ~15 FLOP/zone/step, ~20 lines, augmented with d̂ for offset-free tracking

OPTIMISER
    condensed dense QP, horizon N = 96–128, fp64
    H = ĜᵀĜ + R   via SYRK      ← 87% of fp64 peak, the compute-bound kernel
    H + ρI = LLᵀ  via Cholesky  ← 29% of peak, sequential by nature
    dense ADMM, fixed K ≈ 13–30 iterations, branchless min/max projection

OUTPUTS
    u_now            the command to apply, per zone
    u_sched, t_pred  the full horizon plan and predicted trajectory
    margin_c         min headroom to the 27 °C cap, °C
    lead_time_s      when headroom first drops below 1 °C
    d̂                lumped unmodelled heat — doubles as a fault signal
```
