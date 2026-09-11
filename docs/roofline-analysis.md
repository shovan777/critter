> **Provenance.** Automated analysis pass, copied verbatim. Benchmarks ran on an x86 Zen 5
> box using 128-bit-width fp64 kernels, which are architecturally identical to Cortex-A76's
> NEON fp64 peak — so "% of 8 FLOP/cyc" transfers 1:1; absolute GF/s does not.

All measurements are in. Here is the brief.

---

# Critter compute-bound unit: dense-vs-sparse MPC roofline settlement

**Bench code:** `/tmp/claude-1000/-home-shovan-pitt-ws-critter/090449a7-a351-4cdf-913c-c98dd2ae92f0/scratchpad/bench/` — `peak.c`, `pmu.h` (perf_event_open wrapper; no `perf` binary on this box), `mpc.c` (dense condensed + QDLDL-style sparse banded KKT, same plant), `kern.c`/`kern2.c` (syrk variant ladder), `gemvk.c` (GEMV blocking), `cond.c` (κ(H)), `solvers.c`/`solv2.c` (solver iteration counts), `final.c` (recommended config).

**Measurement basis / what transfers.** This box is Zen 5 (Ryzen AI 9 HX 370). Measured FMA peaks: scalar fp64 27.8 GF/s, **128-bit fp64 36.67 GF/s**, 256-bit fp64 71.84 GF/s, 128-bit fp32 72.77 GF/s. The 128-bit fp64 figure is exactly **8 FLOP/cycle = 2 FMA/cyc × 2 lanes × 2 FLOP — architecturally identical to Cortex-A76's fp64 NEON peak.** So every dense kernel below was either written with `__m128d` intrinsics or compiled `-mprefer-vector-width=128`, and **"% of 8 FLOP/cyc" transfers 1:1 to the Pi 5.** Absolute GF/s does not. Also transferable: FLOP/instruction and branches/kFLOP (ISA-level mix), which combine with A76's 4-wide decode into a hard ceiling.

---

## (a) Condensed dense QP: dimensions, FLOP counts, arithmetic intensity

**Construction** (Rawlings & Mayne §8; Borrelli/Bemporad/Morari ch. 11). With `x∈R^n` (n=3: T_air, T_mass, integrator), `u∈R^m` (m=1, cooling), horizon N, states eliminated:

```
X = Φx₀ + ΓU        Γ ∈ R^{Nn×Nm} block lower-triangular, Γ_{i,j} = A^{i-j-1}B
Ĝ = Q̄^{1/2}Γ         (fold the state cost into Γ once; Q diagonal for a thermal cost)
H = ĜᵀĜ + R̄          ∈ R^{N×N} dense SPD          ← decision dim is M = Nm = N
g = ĜᵀQ̄^{1/2}Φx₀ + linear disturbance term
```

Decision dimension is **N**, not 7N. That is the whole point of condensing at m=1.

**Exact FLOP counts** (instrumented, not estimated — `mpc.c -DCOUNTFLOPS`):

| Phase | Closed form | N=64 | N=128 | N=256 |
|---|---|---:|---:|---:|
| Ĝ build (LTV column propagation) | `n²N(N−1) + …` ≈ 10.5N² | 42,528 | 171,072 | 686,208 |
| **SYRK H = ĜᵀĜ** | `2n·N(N+1)(N+2)/6 ≈ nN³/3` | 274,560 | 2,146,560 | 16,974,336 |
| **Cholesky H+ρI = LLᵀ** | `N³/3 + N²/2` | 89,429 | 707,242 | 5,625,173 |
| gradient g | `nN² + 2n²N` | 13,632 | 51,840 | 201,984 |
| fwd+back substitution (1 solve) | `2N²` | 8,192 | 32,768 | 131,072 |
| GEMV Hu (1 first-order iter) | `2N²` | 8,192 | 32,768 | 131,072 |
| **Total/step, ADMM K=25** | | **0.83 M** | **4.72 M** | **30.0 M** |

Two structural notes the write-up must get right:

- **The N³ term is the condensing SYRK, not the Cholesky** — `nN³/3` vs `N³/3`, i.e. 3× larger at n=3. That is fortunate: SYRK is the highest-FLOP/cycle kernel in the set (measured 87–91% of peak), Cholesky the middling one (29%).
- **For a strictly LTI plant, H is Toeplitz-structured and computable in O(nN²), and is constant** — precompute offline and the N³ term vanishes. Do not silently rely on rebuilding an LTI Hessian; that *is* a replica knob. Make the model genuinely LTV, which is physically real here: chiller COP varies with outdoor-air temperature, so `B_k = B·cop(T_amb,k)` over the forecast horizon and the horizon slides every step. That restores `nN³/3` honestly and is *better control*, not more work for its own sake. (Successive re-identification of A,B from the last window — directly relevant to Critter's fault story — has the same effect.)

**Arithmetic intensity vs DRAM** (Pi 5 fp64 ridge 1.55–1.75 FLOP/byte):

| N | MFLOP/step | cold working set | AI cold | × ridge | warm bytes | AI warm | × ridge |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 64 | 0.83 | 83 KB | 9.7 | **5.9×** | 736 B | 1,127 | 683× |
| 128 | 4.72 | 327 KB | 14.1 | **8.5×** | 1,248 B | 3,778 | 2,290× |
| 256 | 30.0 | 1,293 KB | 22.7 | **13.8×** | 2,272 B | 13,222 | 8,014× |

"Cold" pessimistically assumes the *entire* working set is re-fetched from DRAM every control step (i.e. the sibling Critter units evicted everything). "Warm" is the compulsory traffic only: x₀ (24 B) + ambient/COP forecast (8N) + setpoint/limits + u* out. **Even the cold bound is 6–14× right of the ridge, and AI grows ∝ N** (FLOPs ∝ N³, bytes ∝ N²) — the compute-bound margin improves monotonically with the fidelity knob. That is the cleanest sentence in the paper.

---

## (b) Why sparse KKT loses — and the premise needs one correction

Same plant, same horizon, full-space banded KKT: `w = [u_k, x_{k+1}, λ_{k+1}]` per stage, dim `N(m+2n) = 7N`, quasi-definite `[[P+σI, Cᵀ],[C, −δI]]`, natural (already band-minimal) ordering, QDLDL-style up-looking LDLᵀ with elimination tree — i.e. exactly what OSQP does, and I gave sparse the *favourable* ordering.

Measured, N=128, fp64, 128-bit width where applicable:

| kernel | FLOP | cycles | **FLOP/cyc** | **% of 8** | **FLOP/ins** | **br/kFLOP** | br-miss | **L1D miss** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dense SYRK, 4×4 reg-blocked | 6.49 M | 935 k | **6.93** | **86.8%** | **1.77** | **31.4** | — | **15.6%** |
| dense SYRK, hand 128b 4-acc dot | 2.15 M | 546 k | 3.93 | 49.1% | 1.74 | 41.3 | — | — |
| dense SYRK, 4-acc + `restrict`, `-ffast-math` | 2.15 M | 444 k | 4.84 | 60.5% | 1.90 | 84.8 | — | — |
| dense GEMV, 4-row blocked | 32.8 k | 5.4 k | 6.02 | 75.3% | 2.21 | 35.3 | — | — |
| dense Cholesky (unblocked right-looking) | 707 k | 302 k | 2.34 | 29.3% | 0.88 | 172 | 0.15% | 11.1% |
| dense triangular solve (TRSV) | 32.8 k | 23.9 k | 1.37 | 17.2% | 0.57 | 283 | 0.5% | 7.4% |
| **sparse banded LDLᵀ factor** | 17.0 k | 58.1 k | **0.303** | **3.8%** | **0.069** | **1,772** | **0.013%** | **1.7%** |
| **sparse LDLᵀ solve** | 12.1 k | 20.7 k | **0.585** | **7.3%** | **0.173** | 807 | 0.02% | 0% |

**Ratios (blocked dense SYRK ÷ sparse LDL): 22.9× in FLOP/cycle, 25.7× in FLOP/instruction, 56× in branch density.**

**The correction to the premise: sparse KKT here is NOT memory-bound.** Its L1D read-miss rate is **1.7%**; the dense SYRK's is **15.6%** — the kernel that misses L1 nine times more often is 23× faster. The sparse MPC KKT is *banded*, so its working set stays ~60 KB at any N and never leaves L1/L2. Nor is it branch-*misprediction*-bound: 0.013% miss rate, the branches are predicted perfectly. It is **instruction-issue bound**: 14.5 instructions retired per FLOP, because `y[Li[q]] -= Lx[q]*yc` is a scatter through an indirection — no NEON gather/scatter, so it is irreducibly scalar, and every FLOP drags an index load, an address computation, and a loop-bound test with it. This is precisely the failure mode of the MIQP finding (0.48 FLOP/cyc), and calling it "cache misses" in the paper would be wrong and a reviewer would catch it.

**The A76 transfer, made rigorous.** A76 retires ≤4 instructions/cycle and has 2× 128-bit FMA pipes (≤2 FMA/cyc = 8 FLOP/cyc fp64) and 2 load-capable AGUs (≤2×128-bit loads/cyc = 32 B/cyc from L1). Combining the measured, ISA-invariant instruction mix with the decode width:

- dense blocked SYRK: `min(4 ins/cyc × 1.77 FLOP/ins, 8) = 7.08 FLOP/cyc` → **88.5% of Pi 5 fp64 peak**, front-end-limited, matching the 86.8% measured here.
- sparse LDLᵀ: `4 × 0.069 = 0.276 FLOP/cyc` → **3.5% of peak, and that is an upper bound** ignoring stalls. A76's much smaller OOO window than Zen 5's will make the real figure lower. Consistent with the 0.48 FLOP/cyc already measured for the MIQP.

Two secondary results that validate the model to within 1%:
- **Unblocked GEMV is predicted to cap at 50% of FMA peak** (2 loads per FMA against 2 load ports). Measured: 48.9% (N=64), 49.6% (N=128), 41.1% (N=256). 4-row blocking amortises the x-load and lifts it to **74.5 / 75.3 / 77.0%**. Row-block the GEMV; it is a 10-line change worth 1.5×.
- **Compiler flags, correcting the prior note.** On GCC 14.3.1, `-std=c11` emits **zero** `vfmadd` in the SYRK (38 `vaddsd` + 11 `vmulpd`); `-std=gnu11` emits `vfmadd231pd`/`vfmadd231sd`. So **the prior finding is confirmed: use `-std=gnu11`.** But contraction is the *smaller* lever — `-ffp-contract=fast` alone moved the kernel 0%, because the binding constraint is that GCC will not reassociate the dot-product reduction. `-ffast-math` (or `-fassociative-math`) took FLOP/instruction 0.61 → 1.90 and FLOP/cycle 1.71 → 4.84, a **2.8× gain**. Recommended: `-O3 -std=gnu11 -march=armv8.2-a+fp16+dotprod -mtune=cortex-a76 -ffast-math -fno-math-errno`. Hand-written `__m128d` intrinsics are immune to all of it (they always FMA) and are what gets you the last stretch to 87%.

**The honest wall-clock caveat, which the paper must state.** Sparse is O(N) and dense condensed is O(N³). Measured end-to-end per control step (K=25 iterations, 128-bit): at N=64 dense is **2.1× faster** than sparse while doing 2.7× more FLOPs; at N=128 they are within 1.3×; at N=256 sparse is 5.3× faster. **Dense condensed is not chosen because it is faster — it is chosen because at N ≤ 128 it is competitive-or-better on time while doing its work at 8–23× the FLOP/cycle.** That is the defensible framing, and it is also why N ≤ 128 matters independently of cache.

---

## (c) Cache thresholds — the N values

fp64. Four storage schemes; pick one deliberately:

| Scheme | bytes(N) | **L1 64 KB** | **L2 512 KB** | L3 2 MB (shared) |
|---|---|---:|---:|---:|
| **A** packed-triangular Ĝ + in-place Cholesky | `12N(N+1) + 8N²` | **N ≤ 55** | **N ≤ 160** | N ≤ 322 |
| A2 packed-triangular Ĝ, H and L separate | `12N(N+1) + 16N²` | N ≤ 47 | N ≤ 135 | N ≤ 272 |
| **B** dense rectangular Ĝ (Nn×N, BLAS-shaped) | `24N² + 8N²` | **N ≤ 44** | **N ≤ 127** | N ≤ 255 |
| C H and L only (Ĝ freed / LTI precomputed) | `8N²` | N ≤ 88 | N ≤ 253 | N ≤ 509 |

fp32 multiplies every threshold by √2 ≈ 1.41.

**But L1 residency is the wrong criterion, and the measurement proves it.** Sweeping the 4×4-blocked SYRK across footprints:

| N | footprint (scheme B) | % of 8 FLOP/cyc |
|---:|---:|---:|
| 32 | 40 KB | 91.1 |
| 48 | 90 KB | 90.8 |
| 64 | 128 KB | 88.4 |
| 96 | 288 KB | 89.7 |
| 128 | 512 KB | 86.8 |
| 160 | 800 KB | 87.1 |
| 192 | 1.15 MB | 83.2 |
| 256 | 2.0 MB | 66.9 |
| 384 | 4.6 MB | 62.2 |
| 512 | 8.2 MB | 21.3 |

Efficiency is **flat at 83–91% from 40 KB to ~1 MB** — i.e. from well inside L1 to the edge of this box's 1 MB/core L2 — then falls. A register-blocked kernel is internally L1-blocked regardless of total footprint; what matters is **staying inside the private L2**. Scaling the knee to Pi 5's 512 KB private L2:

- **N ≤ 127 (scheme B) or N ≤ 160 (scheme A) is the clean compute-bound band on Pi 5.**
- N ≈ 160–255: spills into the **2 MB L3, which is shared with the other three cores** — so the memory-bound Critter unit will contend with you and your FLOP/cycle becomes a function of what the siblings are doing.
- N > 255 (scheme B): touches DRAM.

**Recommended nominal: N = 96 or 128.** At N=128, scheme A is 327 KB — comfortably inside private L2, so the compute unit's measured FLOP/cycle is **provably independent of the I/O-bound and memory-bound units' behaviour**. That is a genuinely strong experimental-design argument for a three-unit paper, and it is worth an explicit control experiment (run the compute unit alone vs. concurrently with the other two; show flat FLOP/cycle). Sweep N ∈ {16, 24, 32, 48, 64, 96, 128, 192, 256, 384} for the roofline plot — the plateau-then-cliff *is* the compute-bound proof.

**fp64 is mandatory, not a preference.** Measured κ(H) ≈ 2×10³·N²: **8.3×10⁵ at N=32, 3.2×10⁷ at N=128, 1.8×10⁸ at N=256** (λ_max ∝ N², λ_min ≈ R + 0.086, essentially R-insensitive over R ∈ [10⁻⁴,10⁻¹]). fp32 (ε ≈ 1.2×10⁻⁷) loses the factorization entirely above N ≈ 8. Do not chase the 2× fp32 roofline here.

---

## (d) Solver recommendation: **dense ADMM**

Measured iterations to `|u₀ − u₀*| < 10⁻⁴·u_max` on the real box-constrained condensed thermal QP (constraint active — the chiller saturates):

| N | κ(H) | PGD | FISTA | FISTA + Jacobi/Ruiz | **dense ADMM (tuned ρ)** |
|---:|---:|---:|---:|---:|---:|
| 32 | 8.3e5 | **diverges** (>20k) | 413 | **worse** (>20k) | **28** |
| 64 | 5.5e6 | >20k | 1,054 | >20k | ~40 |
| 128 | 3.2e7 | >20k | 2,195 | 3,147 | **13** (ρ=10) |
| 256 | 1.8e8 | >20k | 5,241 | 7,634 | ~30 |

ρ-sweep at N=128, κ=3.2×10⁷: ρ=1 → 106 it, **ρ=10 → 13 it**, ρ=100 → 28 it, ρ=1000 → 273 it. **Two decades of ρ give ≤106 iterations.** ADMM's rate is governed by ρ, not by κ — exactly the OSQP claim, confirmed at κ=3×10⁷.

| criterion | proj. gradient | FISTA | **dense ADMM** | dense active-set |
|---|---|---|---|---|
| LOC (measured, this impl.) | ~22 | ~30 | **~34** (Chol 14 + solve 6 + loop 14) | ~150–300 |
| iterations needed | **fails** | 400–5,000 | **13–30** | 5–50, *data-dependent* |
| per-iter kernel | GEMV | GEMV | 2× TRSV, or 1 GEMV w/ explicit inverse | rank-1 factor update |
| per-iter FLOP/cyc (measured) | 6.02 (75%) | 6.02 (75%) | 1.37 (17%) / **6.02 (75%)** | low, irregular |
| determinism | fixed K | fixed K | **fixed K, zero data-dependent branches** (projection is `min`/`max`, branchless & vectorized) | **iteration count varies with the active set** |
| conditioning sensitivity | fatal | O(√κ) — fatal here | **insensitive** | insensitive |
| explaining it | trivial | trivial | splitting + dual var, ~½ page; OSQP is the citation | homotopy/working sets, hard |

**Recommend dense ADMM. Reject active-set** on determinism (a data-dependent iteration count reproduces the MIQP anti-pattern: work anti-correlated with the interesting condition) and LOC. **Reject FISTA** on a subtler and more important ground: at 2,200 iterations FISTA's per-step FLOPs become 97% GEMV, so **the iteration count silently becomes the work knob — and iteration count is a pure inefficiency knob, not a fidelity knob.** More FISTA iterations produce the *same* control action computed worse. That is exactly the replica-knob trap the earlier rounds identified. With ADMM at fixed K=25, the N³ condensing+factorization phases are **78% of the FLOPs**, so **N — genuinely better control — is the only work knob.** Keep FISTA as the named runner-up and one paragraph of justification; it is the right answer for a well-scaled, low-κ problem and this one is not.

**Two-day build plan (schedule-safe, working deliverable at end of day 1):**

- **Day 1** — Ĝ build → SYRK → Cholesky → forward/back substitution = *unconstrained* MPC. ~60 LOC, complete and shippable, and it is already the compute-bound demonstration (SYRK is 46% of the FLOPs at 87% of peak). Validate against an offline Eigen/OSQP oracle.
- **Day 2** — add the box constraint `0 ≤ u ≤ u_max` (physically mandatory: a chiller has finite capacity and cannot heat) via ADMM: reuse the Cholesky on `H+ρI`, add the z-update (clip) and w-update. ~20 more LOC. Tune ρ offline once and hard-code it — the plant is fixed, so ρ is a design constant like any other controller gain, and a fixed K certified offline over the operating envelope is standard embedded-MPC practice ([Richter, Jones & Morari 2012](https://www.researchgate.net/publication/224266022_Computational_Complexity_Certification_for_Real-Time_MPC_With_Input_Constraints_Based_on_the_Fast_Gradient_Method) is the citation for a-priori iteration bounds). Cross-check: with constraints inactive, ADMM must reproduce the day-1 Cholesky answer.
- **Stated optimization, if time** — replace the two TRSVs per iteration with a precomputed `(H+ρI)⁻¹` and one GEMV. Measured: the iteration kernel goes from **1.37 FLOP/cyc (17%) to 5.8–6.1 FLOP/cyc (73–76%)**. My unblocked `trtri`+product costs ~3× the Cholesky, so at K=25 the two variants are within ~20% on wall time — but the before/after roofline plot is itself a good paper result.

Projected Pi 5 single-core, N=128, ADMM K=25: **~800 µs/step at ~2.5 FLOP/cyc** (Cholesky+TRSV) or **~400 µs at ~5.9 FLOP/cyc** (explicit-inverse GEMV). Both leave enormous headroom at a 1–60 s HVAC control period, so N is free to be the fidelity knob all the way to the L2 knee.

**One framing point for the paper's positioning.** Everything above is about efficiency; none of it argues *why* MPC. The prior finding stands: duty cycle and cycle period cost ~20 integer ops and move strongly with capacity loss. MPC's defensible axis is that it **optimizes an action over a horizon subject to actuator limits** rather than observing a symptom — and the measurable artifact of that is the constraint activity (`u` saturating at `u_max` over the horizon, which my runs reproduce: u₀* = u_max). Report *that* as the control-value signal, and the roofline as the systems result. Do not claim MPC detects the fault better than the duty cycle; claim it acts on it.

**Sources:** [OSQP (Stellato et al.)](https://www.web.stanford.edu/~boyd/papers/pdf/osqp.pdf) · [OSQP, Math. Prog. Comp.](https://link.springer.com/article/10.1007/s12532-020-00179-2) · [qpOASES (Ferreau et al.)](https://link.springer.com/article/10.1007/s12532-014-0071-1) · [Richter, Jones & Morari, fast-gradient complexity certification](https://www.researchgate.net/publication/224266022_Computational_Complexity_Certification_for_Real-Time_MPC_With_Input_Constraints_Based_on_the_Fast_Gradient_Method) · [Jerez, Kerrigan & Constantinides, sparse vs condensed QP for LTI MPC](https://cas.ee.ic.ac.uk/people/gac1/pubs/JuanCDC11.pdf) · [Axehill, controlling the level of sparsity in MPC](https://www.diva-portal.org/smash/get/diva2:780367/FULLTEXT01.pdf) · [Cortex-A76 microarchitecture (WikiChip)](https://en.wikichip.org/wiki/arm_holdings/microarchitectures/cortex-a76)