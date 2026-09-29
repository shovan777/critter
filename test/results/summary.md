# Per-module summary

| Module | Unit | Rules | Pass | Conf. | Findings | Compiler-detectable | Manual share | Effort | Band |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `critter_mem.c` | Unit 2 - memory | 12 | 3 | 25% | 9 | 0 | 100% | 53 | D - redesign |
| `mpc.c` | Unit 3 - compute | 10 | 1 | 10% | 9 | 2 | 78% | 30 | C+ - reviewer-led, heavy |
| `thermal.c` | Unit 3 - compute | 7 | 5 | 71% | 2 | 0 | 100% | 19 | C+ - reviewer-led, heavy |
| `sensor_sim.c` | Unit 1 - I/O | 9 | 4 | 44% | 5 | 0 | 100% | 26 | C+ - reviewer-led, heavy |
| `io_unit.c` | Unit 1 - I/O | 11 | 0 | 0% | 11 | 4 | 64% | 27 | D - redesign |
| `perf.c+main.c` | Unit 3 - compute | 5 | 1 | 20% | 4 | 1 | 75% | 12 | D - redesign |

**Across all cards:** 54 rule verdicts scored, 14 pass (26%). Of 40 findings, 7 (18%) were reachable by the compiler or `gcc -fanalyzer`; the remaining 33 needed a written test or a human. 3 demonstrated defects have no rule in either standard.

> Conformance is a percentage of the rules **tested** (all 10 Power of 10 rules, 13 of the 99 CERT rules), not of either standard as a whole.
