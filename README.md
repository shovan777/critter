# critter

This is the capstone project for the embedded system design course (ECE2160) at UPitt. The aim is to build a firmware for HVAC systems.

This repo is **unit 3**, the compute-bound unit: a condensed dense model-predictive
controller that decides the cooling schedule for a machine room and predicts where
its temperature is heading.

## Build

```sh
make            # builds bin/critter
```

No dependencies beyond libc and libm. C11, no C++.

Default flags are `-O3 -ffast-math -fno-math-errno -std=gnu11`, plus
`-march=armv8.2-a+fp16+dotprod -mtune=cortex-a76` on aarch64 and
`-march=native -mprefer-vector-width=128` on an x86 dev box. The 128-bit
constraint on x86 is deliberate: it makes FLOP/cycle directly comparable to the
Pi's NEON, so fractions of peak transfer even though absolute rates do not.

**Do not drop to bare `-std=c11`.** It silently disables FMA contraction in GCC —
a flat 2× loss with no warning.

## Run

```sh
./bin/critter --peak-probe     # measure the fp64 FMA ceiling — run this FIRST
./bin/critter --flops          # FLOP model and working-set size, no timing
./bin/critter --bench 31       # time one MPC step: median, IQR, % of peak
./bin/critter --demo 8         # 8-hour closed-loop run against the plant model
./bin/critter --demo 8 14      # ...with outdoor mean 14 C instead of 33 C
```

Start with `--peak-probe`. It should report **~7.7 fp64 FLOP/cycle (96% of 8)**.
That number is the denominator of every percentage the project reports, so if it
comes back low, stop and fix that before measuring anything else — a probe that
returns a plausible-but-wrong ceiling rescales every result silently. See
`docs/notes.md` §1 for two ways it read low during development.

`--demo` takes an outdoor mean so you can see both control regimes. At 33 °C the
damper stays shut (opening it would heat the room); at 14 °C it opens to ~0.31 and
the compressor backs off, cutting energy roughly in half.

## Tuning the workload

`CRIT_N`, the horizon length, is the main dial. Condensing is `O(N³)` in FLOPs
while the data crossing the unit boundary stays `O(1)`, so arithmetic intensity
*rises* with N — turning it up makes the unit more compute-bound, not less.

```sh
make clean && make OPT="-O3 -ffast-math -fno-math-errno -DCRIT_N=128"
./bin/critter --bench 31
```

Measured on an x86 dev box, 128-bit fp64 (absolute times will differ on a Pi 5;
the % of peak is what transfers):

| `CRIT_N` | M | MFLOP/step | working set | median | % of 8 FLOP/cyc |
|---|---|---|---|---|---|
| 32 | 64 | 0.66 | 48 KB | 103 µs | 24.1 |
| 48 | 96 | 1.73 | 108 KB | 126 µs | 33.2 |
| 64 | 128 | 3.51 | 192 KB | 310 µs | 27.5 |
| **96** | **192** | **9.85** | **432 KB** | **738 µs** | **32.6** |
| 128 | 256 | 21.00 | 768 KB | 2912 µs | 27.4 |
| 160 | 320 | 38.26 | 1200 KB | 2854 µs | 33.0 |
| 192 | 384 | 62.95 | 1728 KB | 4907 µs | 31.7 |

**95× the work across that range, and the efficiency stays flat at 24–33%.** No
cliff — which is the point: on a Pi 5 the fp64 roofline ridge is only ~1.6
FLOP/byte, so clearing it is easy and staying clear of it is easier still.

Working set is `48·N²` bytes. The Pi 5 has **512 KB of private L2 per core**, so
**N ≤ 104 keeps the whole problem in private cache** and your measured FLOP/cycle
is provably independent of what the I/O and memory units are doing on the other
cores. N=96 is the default for exactly that reason. Past N≈104 you spill into the
2 MB shared L3 and the other two units start contending with you — which is a
legitimate experiment, just label it as one.

### Other dials

| Knob | Effect on FLOPs | Notes |
|---|---|---|
| `-DCRIT_N=<n>` | `~N³` | The main dial. Also *fidelity* — a longer horizon is genuinely better control, not busywork. |
| `-DCRIT_ADMM_K=<k>` | linear in K | Constraint-solver iterations, default 25. Cheap to raise, but it inflates the *least* efficient part of the pipeline, so it lowers your % of peak. Raise N instead. |
| `CRIT_NU` (in the header) | `~NU²` on the Hessian | Number of actuators. Physically determined — 2 here (compressor, damper). Not a free dial. |

Prefer `CRIT_N`. Iteration count is the knob a reviewer will read as manufactured
work; horizon length is the one that also makes the controller better.

### Runtime knobs (no rebuild)

Cost weights are environment overrides, so the analysis phase can sweep without
recompiling:

```sh
CRIT_R_ENERGY=5.0 CRIT_R_RATE=0.25 CRIT_Q_TRACK=1.0 CRIT_RHO=10.0 \
  ./bin/critter --demo 8 14
```

These must stay commensurate with the tracking term (~5.4 on the Hessian
diagonal at `q_track=1`) or they are numerically invisible. `docs/notes.md` §2
has the arithmetic and the energy-vs-comfort sweep.

## Layout

| path | |
|---|---|
| `include/critter.h` | wire formats, configuration, the public API |
| `src/mpc.c` | **the hot code** — condensing, SYRK, Cholesky, ADMM |
| `src/thermal.c` | plant model, discretisation, Kalman estimator |
| `src/perf.c` | cycle counters, peak probe, anti-dead-code checksum |
| `src/main.c` | driver and timing harness |
| `docs/mpc-design.md` | states, inputs, estimator, data — what the controller *is* |
| `docs/build-plan.md` | hour-by-hour build and measurement plan |
| `docs/roofline-analysis.md` | why dense condensed beats sparse KKT, with numbers |
| `docs/notes.md` | **things the plan got wrong**, found by writing the code |
| `data/` | recorded HVAC dataset (3 months, 5-min, MIT licence) |
