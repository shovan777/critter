# Implementation notes — things the plan got wrong

Found by writing the code. Recorded because the reasoning matters more
than the fix, and because three of these are the kind of error that looks
like a working result.

---

## 1. The peak probe measured a quarter of the real ceiling

The probe is the denominator of every percentage in the paper, so it was
built first and gated on. It failed its own gate twice, for two unrelated
reasons.

**No FMA instruction was emitted at all.** The x86 dev build had
`-mprefer-vector-width=128` but no `-march`. Baseline x86-64 is SSE2,
which has no FMA, so the flag constrained a width that was never enabled.
`objdump` showed zero `vfmadd` of any kind, 91 stack spills, and plain
`mulsd`. Measured 2.13 FLOP/cycle against a ceiling of 8.

This would not have appeared on the Pi: NEON and FMA are baseline in
ARMv8, so the aarch64 build was always correct. A dev-box-only artifact
that silently rescales every number in the paper.

**Then the probe capped at exactly 4.00 FLOP/cycle.** With `-march=native`
GCC emitted 16 *scalar* FMAs and no packed ones — it had unrolled the
chain loop into 16 independent scalars and never re-packed them. 2 scalar
FMA/cycle is the issue limit, so the probe read exactly half the ceiling
and looked plausible.

Fixed by making the width explicit with `vector_size(16)`, which maps to
SSE/AVX-128 on x86 and NEON on aarch64, so one probe serves both targets.

```
        no -march   : 2.13 FLOP/cyc   (26.7% of 8)   zero FMA emitted
        -march only : 4.00 FLOP/cyc   (50.0% of 8)   16 scalar FMA
        vector_size : 7.75 FLOP/cyc   (96.8% of 8)   8 packed FMA
```

The lesson for the write-up: a peak probe that returns a *plausible*
number is the dangerous case. 4.00 is exactly the kind of figure nobody
questions.

---

## 2. The cost weights were three orders of magnitude too small

The plan's weights (`r_energy = 2e-3`, `r_rate = 5e-4`) are numerically
invisible against the tracking term.

`Gh` entries are about 0.24 — the product of `UA_coil` and the per-Watt
air-node gain `dt/C_air` ≈ 2.5e-5. Summed over N=96 steps, the SYRK puts
roughly **5.4 on the H diagonal**. Against that, an input weight of 2e-3
changes nothing.

Caught by a control experiment rather than by inspection: runs at
`r_rate = 0`, `r_rate = 5e-4`, and a per-channel-scaled rate penalty
produced **byte-identical output**. A weight that cannot change the
answer is not a weight.

Consequence: the controller had been doing pure setpoint tracking with no
energy term at all, and so had no reason to prefer the economiser over the
compressor. With `r_energy` rescaled to the same order as the tracking
contribution, the substitution appears:

| `r_energy` | T_air | u_cool | damper | kWh / 8 h |
|---|---|---|---|---|
| 0.0 | 22.50 | 0.165 | 0.049 | 0.0915 |
| 0.1 | 22.50 | 0.162 | 0.059 | 0.0906 |
| 1.0 | 22.51 | 0.139 | 0.137 | 0.0837 |
| 5.0 | 22.55 | 0.090 | 0.309 | 0.0680 |
| 20.0 | 22.65 | 0.068 | 0.394 | 0.0557 |

39% less energy for 0.15 °C of tracking error. Nominal is now
`r_energy = 5.0`; all weights are `CRIT_*` environment overrides so the
sweep is reproducible.

---

## 3. `q_cap` is declared and not implemented

The soft cap above `t_hi` is in `crit_cost_t` and is never used. The cap
is *monitored* — `plan.margin_c` and `plan.lead_time_s` are correct — but
not penalised, because a one-sided hinge is not a plain quadratic and
needs a slack variable.

Left in the struct, marked in the header. It does not affect the current
results because tracking holds the room ~4.4 °C below the cap, but it
would matter the moment the plant cannot hold setpoint, which is exactly
the interesting case. v1.

---

## 4. The economiser sign is the recorded controller's mistake

The damper channel carries `-UA_econ (T_ref - T_out,k)`, so its sign
flips with outdoor temperature: opening the damper cools the room while
outside air is cooler than the room and heats it otherwise. That falls
out of the physics rather than being a rule anyone wrote.

It reproduces correctly in both regimes:

```
  hot day,   outdoor mean 33 C : damper 0.000 throughout   0.1928 kWh / 8 h
  cold night, outdoor mean 14 C: damper 0.309 at T_out=12  0.0680 kWh / 8 h
```

Worth stating in the paper because the recorded dataset shows the
installed controller doing the opposite — at 2022-07-11 11:00–17:00 it
holds the damper at 100% while outside is 34–42 °C, and the room climbs
24.6 → 25.7 °C against a 22 °C setpoint.

---

## Measured, day 1

Dev box, x86 Zen 5, 128-bit-constrained fp64 so FLOP/cycle is comparable
to Cortex-A76. Absolute rates do not transfer; fractions of peak do.

```
peak probe        7.75 fp64 FLOP/cycle      96.8% of the 8 ceiling
MPC step          9.85 MFLOP, 698 us median, IQR [679, 742]
                  2.71 fp64 FLOP/cycle      33.9% of ceiling
working set       432 KB  (Gh 144 + H 288)  — inside Pi 5's 512 KB L2
checksum          stable across all 31 reps
```

**The 33.9% is dominated by the wrong kernel.** The FLOP split is SYRK
36%, Cholesky 24%, ADMM 38% — and the ADMM's 38% runs as triangular
solves, which are serial and measured elsewhere at ~17% of peak. The
roofline analysis already identified the fix: replace the per-iteration
triangular solves with an explicit inverse and a GEMV, moving that 38%
from ~17% to ~75% efficiency.

Predicted aggregate after that change, from the per-kernel efficiencies:

```
    now      3.56/0.87 + 2.38/0.29 + 3.72/0.17  ->  9.87/34.2 = 29%   (measured 34%)
    after    3.56/0.87 + 2.38/0.29 + 3.72/0.75  ->  9.87/17.3 = 57%
```

That is the next change, and it is the same arithmetic — not a cut.
