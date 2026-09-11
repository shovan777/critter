# Critter — project guide for Claude

## What this is

ECE2160 (embedded systems, Univ. of Pittsburgh) **one-week class assignment**. Not a research
project, not a paper, not a novelty project.

Budget: **2 days to build**, **5 days to analyse and write a technical paper**.

Critter monitors machine-room / server-room temperature for HVAC insight. It has three units,
each meant to exhibit a *different program behavior*:

| Unit | Behavior | Owner |
|---|---|---|
| 1. I/O unit | I/O-bound — reads temperature at a high rate | another team |
| 2. Memory unit | Memory-bound — outlier removal, summarization | another team |
| 3. **Compute unit** | **Compute-bound — predicts HVAC behavior** | **us** |

We build **Unit 3 only**.

## Hard constraints

- **Hardware: Raspberry Pi 5.** Quad Cortex-A76 @ 2.4 GHz, ARMv8.2-A, NEON 128-bit (no SVE), Linux.
- **Language: C11. Not C++.** A C++ library may be used *offline* as a validation oracle, never
  shipped in the deliverable.
- **No on-device training.** Offline/host training is fine. On-device is inference, fitting, tuning.
- **Simplicity and schedule safety beat sophistication.** Two days. Anything that risks a day of
  build-system fighting is a bad recommendation regardless of technical merit.

## Model policy — use the cheapest model that can do the job

Token spend has been a real problem on this project (51 subagents, ~5.3M subagent tokens across
four workflows, a large fraction of it discarded when scope narrowed). Default to cheaper models.

**Agent tool:** pass `model: "haiku" | "sonnet" | "opus"`.
**Workflow `agent()`:** pass `{model: "...", effort: "low"|"medium"|"high"}`.

| Task | Model | Effort |
|---|---|---|
| Reading files, grepping, listing, extracting fields, reformatting | **haiku** | low |
| Running builds/tests and reporting output, mechanical edits | **haiku** | low |
| Web search and fetch, gathering citations, summarizing a source | **haiku** or **sonnet** | low |
| Straightforward code: kernels, harness, CSV loaders, plotting | **sonnet** | medium |
| Benchmarking, profiling, numerical debugging | **sonnet** | medium |
| Architecture decisions, adversarial review, correctness critique | **opus** | high |
| Anything where being wrong costs a day of the 7 | **opus** | high |

Do not use opus for retrieval. Do not use haiku for numerical-correctness judgement.

## Workflow policy

**Default: don't.** For a one-week assignment, a direct answer or 1–3 agents is almost always right.

- Never launch a workflow >6 agents without asking first, and say what it will cost.
- Never launch a broad-survey or "generate N options" workflow unless explicitly asked to ideate.
- Before any fan-out, check the current scope. Most waste here came from running a broad pass
  *after* the decision had already narrowed.
- Do the cheap scouting inline first (list files, grep, one search). Fan out only over what's left.

## Where things stand

**Direction: MPC** (model-predictive control) as the compute-bound unit. Chosen by the user.

Findings that are settled — do not re-derive:

- **Pi 5 roofline (measured):** fp32 peak 16 FLOP/cyc/core; fp64 peak 8. DRAM ~12.4 GB/s at one
  thread, and it *falls* to 7.7 at four. Single-core ridge ≈ **3.1–3.5 fp32 FLOP/byte**, 1.55–1.75 fp64.
- **Being compute-bound here is easy; being memory-bound is hard.** One room's history is L1-resident
  for hours. So the graded claim must be **FLOP/cycle against the 16/8 ISA ceiling**, not cache misses.
- **Dispatch ceiling:** `FLOP/cycle ≤ 16·F / max(F + Z, L)` where F = FMA vector ops, Z = zero-FLOP
  vector ops, L = 128-bit loads. Predicted a measured kernel to 0.1 percentage points.
- **Integer/branch-and-bound MPC is a trap.** A MIQP staging solver measured 0.48 FLOP/cyc (1.5% of
  peak), latency-bound, and its node count collapsed 120,000× under the fault it was meant to detect.
- **Sparse QP solvers are the memory-bound side.** OSQP/ECOS factor a sparse KKT matrix — pointer
  chasing. Dense condensed QP is the compute-bound side.
- **Small dense Cholesky is weak:** ~21% of the fp64 ceiling at n≈99, and blocking doesn't fix it.
  The switched-RC RK2 simulator, lane-parallel, measured **60% of peak-FMA** — better.
- **DAQP** (github.com/darnstrom/daqp) — pure C, MIT, zero dependencies, dense dual active-set.
  Best library candidate. OSQP is C/Apache-2.0 but sparse. HiGHS is C++ — oracle only.

Two rules that are load-bearing and easy to get wrong:

- **Compile with `-std=gnu11`, never bare `-std=c11`** — the latter silently disables FMA
  contraction in GCC. Flat 2× loss, no warning. Full set: `-O3 -mcpu=cortex-a76 -std=gnu11
  -ffp-contract=fast`. Verify with `objdump -d | grep fmla` and confirm `v0.4s`, not `s0`.
- **Cooling enters the thermal model as a CONDUCTANCE**, `s·UA_coil·(Ta − T_supply)` — never as
  constant power `s·Q_cool`. The constant-power form has identical eigenvalues in both compressor
  phases (verified: τ ratio 0.964 both healthy and faulted), so on/off time constants cannot differ
  and any comparison built on them is degenerate.

## Working notes

Prior analysis lives in the session scratchpad, not the repo. The 42-candidate catalogue is
published at https://claude.ai/code/artifact/4601e36b-c608-4d98-b9cd-582f91a58b3f — it is
background, superseded by the MPC decision.

## Style

- Answer the question asked. Don't broaden scope unprompted.
- Surface the substance, not just a summary of it — if analysis produced descriptions, show them.
- Report measured numbers as measured and estimates as estimates; say which machine a number came
  from when it wasn't a Pi 5.
- Prefer a direct answer over a workflow. Prefer 200 lines of C we understand over a library we
  spend a day integrating.
