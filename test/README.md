# Critter safety-guideline test suite

Tests the three Critter units against the **SEI CERT C Coding Standard, 2016
Edition** and the **NASA/JPL Power of 10 rules**, and scores the results.

No external dependencies: a C compiler and `python3`. No test framework, no
analyser to install, no network. The target platform ships `build-essential`
and `python3` and the suite needs nothing else.

The reasoning behind every choice here — why these rules, why six cards, why
each test sits at the layer it does, and how the rubric is computed — is in
`critter_test_plan_and_rubric.pdf`. This file is just how to run it.

## Layout expected

```
critter/
  unit1_io/        io_unit.c  sensor.h  sensor_sim.c
  unit2_mem/       critter_mem.c
  unit3_compute/   critter.h  mpc.c  thermal.c  perf.c  main.c
  tests/           <this directory>
```

Different layout? Override the three path variables:

```
make U1=../io-unit U2=../mem-unit U3=../compute-unit run
```

## Running it

```
make run        # build, run all three layers, print the scorecard   <-- start here
make verdicts   # raw CT| verdict lines, unscored
make runtime    # the four C test binaries only
make static     # structural checks and compile probes only
make dynamic    # end-to-end and integration checks only (~10 s)
make clean
```

`make run` takes about fifteen seconds; the I/O unit integration test runs the
acquisition loop for six real seconds twice, and that is most of it.

To regenerate the PDF from the latest run: `python3 make_report.py`.

## Files

| File | What it does |
|---|---|
| `ct.h` | Verdict-recording harness. One line per check, nothing else. |
| `test_critter_mem.c` | Unit 2 runtime tests. Includes the source to reach its `static` functions. |
| `test_mpc.c` | Unit 3 runtime tests for the MPC kernel. |
| `test_thermal.c` | Unit 3 runtime tests for the plant model and estimator. Linked alone. |
| `test_sensor_sim.c` | Unit 1 runtime tests for the simulated sensor driver. |
| `check_static.py` | Structural rules and compile probes — the rules with no runtime signature. |
| `check_dynamic.py` | End-to-end and integration checks. Writes fault inputs to `faults/`. |
| `scorecard.py` | The rubric. Reads verdict lines, prints per-module scorecards. |
| `make_report.py` | Renders the decision record and rubric breakdown to PDF. |
| `Makefile` | Build and orchestration. |

## How the pieces talk to each other

Every check — C or Python, runtime or static — emits exactly one line per
verdict:

```
CT|<module>|<test_id>|<standard>|<rule>|<verdict>|<evidence>
```

`scorecard.py` reads those lines and nothing else. Adding a check means
emitting one more line; it never means modifying the scorer.

Verdicts are `PASS`, `FAIL`, `REVIEW` (needs a human to close), `NA` (the rule
has no applicable construct here), and `FINDING` (a real defect for which
neither standard has a rule — reported, excluded from the score).

## Two things that are intentional and should not be "fixed"

**The suite builds `-std=gnu11`, not `-std=c11`.** Two of the units under
review do not compile under strict C11: `io_unit.c` fails outright on
`CLOCK_REALTIME`, and `mpc.c` emits an implicit declaration for
`posix_memalign`. Those are recorded as findings against the units
(`DCL31-C`, Power of 10 Rule 10) rather than absorbed into the build flags.

**Some tests are expected to pass.** `MEM-04`, `MEM-07`, `MEM-15`, `MPC-03`,
`MPC-04`, `THM-01` and several static checks pass on the current code. A suite
in which everything fails cannot tell a careless module from an unfinished
one.

## Not in scope

`generate_temps.py` is a test-data generator, excluded by the memory unit's own
design record. This suite is also outside its own scope — it is the
instrument, not the specimen.
