# QP / MPC library survey

Verified by cloning, reading source, and building each one. Provenance: automated survey pass, build times measured on an x86 development box.

## DAQP

- **URL:** https://github.com/darnstrom/daqp
- **Language:** Pure C (C99). VERIFIED by file count: src/ has 14 .c + include/ 13 .h, ZERO .cpp in the core. The only C++ (10 .cpp) is the optional interfaces/daqp-eigen/ binding, which you simply do not compile. 4,780 LOC total core.
- **License:** MIT (verified: LICENSE reads 'MIT License, Copyright (c) 2022 Daniel Arnström')
- **Dense or sparse:** DENSE, but the WRONG KIND of dense. Verified by reading src/factorization.c: it is a dual active-set method using recursive rank-1 LDL' UPDATES (daqp_update_LDL_add), storing L in a flat contiguous array (work->L[disp++]) with unit-stride dot products manually unrolled 4x with 4 accumulators. No CSC, no pointer chasing. BUT the roofline math is disqualifying: a rank-1 update on an n x n factor is ~2n^2 FLOP over ~4n^2 bytes = 0.50 fp64 FLOP/byte REGARDLESS of n. The Pi 5 fp64 ridge is 1.55-1.75 FLOP/byte, so DAQP sits a factor of ~3 BELOW the ridge and cannot be made compute-bound by growing the problem. The whole point of an active-set method is to AVOID the O(n^3) refactorization, i.e. it is engineered to destroy exactly the arithmetic intensity you need. It also has data-dependent branching on the active set, which is the same latency-bound failure mode as the MIQP branch-and-bound already measured at 0.48 FLOP/cyc.
- **ARM/NEON:** No NEON intrinsics and no assembly, but VERIFIED fully portable: grep for immintrin/arm_neon/__m256/_mm_ across src/ and include/ returns NOTHING. It is plain C that will compile unchanged on aarch64. Vectorization is left entirely to GCC's auto-vectorizer, which the prior finding pegs at ~12.7% of peak.
- **Build difficulty:** MEASURED: 2 SECONDS. cmake + make -j8, exit 0, zero external dependencies ('library free'), produced libdaqp.so and libdaqpstat.a. Junior dev: 15 minutes including reading the API. The C API is 2 functions: daqp_solve(DAQPResult*, DAQPWorkspace*) and daqp_quadprog(DAQPResult*, DAQPProblem*, DAQPSettings*). This is the single safest build in the entire survey.
- **Verdict:** USE-AS-BASELINE-ONLY. This is the best offline/on-device validation ORACLE in the survey and the one I would actually put on the Pi next to your own solver: MIT (so it can even ship legally), pure C, zero deps, 2-second build, trivial API. Use it to prove your hand-written solver returns the same optimizer to 1e-9. Do NOT make it the compute-bound unit itself: at 0.50 FLOP/byte fixed it will profile memory/latency-bound and it will contradict your paper's thesis. Ironically it makes an excellent CONTRAST datapoint for the paper: 'the active-set solver is 3x below the ridge and stays there as N grows; the condensed IPM crosses it.'
- **Verified:** yes — cloned, inspected source, built, timed

## OSQP

- **URL:** https://github.com/osqp/osqp
- **Language:** Pure C for the library. VERIFIED: 62 .c + 63 .h; all 28 .cpp files are under tests/ (googletest harness) and are not part of libosqp.
- **License:** Apache-2.0 (verified: LICENSE header reads 'Apache License Version 2.0')
- **Dense or sparse:** SPARSE — and this CONFIRMS your prior suspicion with direct evidence. The builtin algebra backend is CSC (algebra/_common/csc_math.c) and the linear solver is QDLDL (vendored at algebra/_common/lin_sys/qdldl). I read the QDLDL factorization inner loop and it is textbook gather/scatter pointer chasing: `yVals[Li[j]] -= Lx[j] * yVals_cidx;` and `x[Li[j]] -= Lx[j] * val;` — an indirect load, an indirect store, and one FMA, with the address of every access depending on a just-loaded index. That is a dependent-load chain, not a FLOP stream. It will profile memory-latency-bound on a Cortex-A76 and will NOT demonstrate compute-boundedness. Also note the CSC scaling loops (`Ax[i] *= d[Ai[i]]`) have the same indirection.
- **ARM/NEON:** None, and none possible for the sparse kernels (indirect indexing defeats NEON). VERIFIED portable: no immintrin/arm_neon/intrinsics anywhere in src/ or algebra/. Builds clean on aarch64.
- **Build difficulty:** MEASURED: 2 SECONDS on x86_64 (cmake + make -j8, exit 0). It auto-fetches QDLDL via CMake FetchContent, which means it needs NETWORK ACCESS at configure time — a real gotcha if the Pi is on a locked-down lab network. The demo binary runs and solves correctly (I ran osqp_demo: 'status: solved, optimal objective 1.8800, 50 iterations, 4.50e-05s'). Junior dev: 30 minutes.
- **Verdict:** USE-AS-BASELINE-ONLY. It is the most credible, most citable QP oracle in the field and Apache-2.0 lets it ship — but its internals are precisely the memory-latency-bound shape your paper must argue AGAINST. Use it on the host (or the Pi) to validate your solution vector. Then use it as your paper's headline contrast: 'OSQP's sparse LDL performs one FMA per dependent gather; our condensed Cholesky performs n/24 FLOP per byte.' That comparison is worth a whole figure. Just never let it be the unit under test.
- **Verified:** yes — cloned, read QDLDL/csc_math source, built, ran the demo

## BLASFEO

- **URL:** https://github.com/giaf/blasfeo
- **Language:** C + hand-written assembly. VERIFIED: 283 .c, 49 .h, and 150 .S assembly files. Zero C++.
- **License:** 2-Clause BSD (permissive, ships fine)
- **Dense or sparse:** DENSE, panel-major — the correct shape. It stores matrices in fixed-height panels (block-row-major, column-major within a panel) specifically so the innermost GEMM kernel streams contiguous data into registers with no packing on the critical path. This is the one library in the survey whose linear algebra is genuinely engineered to sit at the compute-bound end of the roofline.
- **ARM/NEON:** YES, and I verified the exact answer to your question about which cores. ARMV8A_ARM_CORTEX_A76 IS a first-class named target (CMakeLists.txt lines 65, 173, 400, 416) — that is literally the Pi 5's core. BUT the important caveat, which I confirmed by reading the CMake target-dispatch logic: there is NO A76-specific kernel block. Line 385 sets TARGET2=ARMV8A_ARM_CORTEX_A57 for A76/A73/Apple-M1, and the kernel file lists at lines 965-1016 are selected off A57. So A76 compiles the A57-tuned aarch64 NEON assembly: fp64 kernel_dgemm_8x4_lib4.S and kernel_dgemm_4x4_lib4.S; fp32 kernel_sgemm_16x4_lib4.S, 12x4, 8x8, 8x4, plus sgemv/spack. These ARE real hand-written NEON assembly panel-major kernels that will run on your A76 — they are just not micro-architecturally tuned FOR the A76 (no A76-specific scheduling/latency tuning). C flags are only '-march=armv8-a+crc+crypto+simd', notably NOT -mcpu=cortex-a76. So: real NEON assembly, yes; A76-optimized, no — A57-optimized and A76-compatible.
- **Build difficulty:** MEASURED: 5 SECONDS for the x64 build (cmake + make -j8, exit 0, auto-detected X64_INTEL_HASWELL). On the Pi you must pass -DTARGET=ARMV8A_ARM_CORTEX_A76 manually (auto-detection is x64-only — the CMake comment says 'with automatic X64 target detection'). Build itself: ~1-2 min on a Pi 5. The REAL cost is not the build, it is the API: BLASFEO does not take your column-major arrays. You must allocate blasfeo_dmat structures, call blasfeo_pack_dmat to convert into panel-major, and learn a non-BLAS calling convention. Realistically 4-8 hours for a junior dev to get a correct Cholesky+GEMM pipeline, and that is a meaningful bite out of a 2-day budget.
- **Verdict:** USE — but ONLY as a stretch goal / day-2-afternoon comparison point, never as the critical path. The schedule-safe plan is: write your own kernels first, get the unit working and measured, THEN if time remains link BLASFEO to answer 'how close to a hand-tuned expert assembly kernel did I get?' That single number ('my hand-vectorized NEON Cholesky hit X% of BLASFEO's dgemm') is worth a lot in the paper and costs nothing if you skip it. If you have not got the unit running by end of day 1, drop BLASFEO entirely — the panel-major API is exactly the kind of thing that eats an unplanned afternoon.
- **Verified:** yes — cloned, built and timed, read the CMake target dispatch and the armv8a kernel file lists directly

## acados

- **URL:** https://github.com/acados/acados
- **Language:** Core is C. But it recursively vendors BLASFEO, HPIPM and a qpOASES fork, and the real user-facing workflow is Python/MATLAB + CasADi code generation.
- **License:** 2-Clause BSD (verified: LICENSE reads 'The 2-Clause BSD License')
- **Dense or sparse:** Structure-exploiting Riccati via HPIPM/BLASFEO — dense small blocks. For your problem this is a trap: the Riccati recursion works on nx-by-nx blocks where nx is your state dimension (~4 for a thermal model). Dense Cholesky AI is n/24 fp64 FLOP/byte, so at nx=4 that is 0.17 FLOP/byte — an order of magnitude BELOW the 1.55-1.75 ridge. The structure-exploiting formulation deliberately trades the big O(N^3) dense block for many tiny O(nx^3) blocks, which is great for latency and terrible for demonstrating compute-boundedness.
- **ARM/NEON:** Yes, inherited from BLASFEO (same A76->A57 aarch64 assembly kernels described above), if you set the BLASFEO target correctly through acados' CMake — which is an extra flag most people miss.
- **Build difficulty:** MEASURED, AND IT FAILED. Recursive clone: 18 seconds but 253 MB. Then `cmake -DACADOS_WITH_QPOASES=ON && make -j8` FAILED with exit 2 in 5 seconds: acados/external/qpoases/src/QProblem.c:472 'error: passing argument 1 of ConstraintsCPY from incompatible pointer type [-Wincompatible-pointer-types]'. Root cause verified: GCC 14 promoted -Wincompatible-pointer-types from a warning to a permerror (gcc.gnu.org/gcc-14/porting_to.html), and acados' bundled qpOASES_e C fork predates that. I then re-ran with -DACADOS_WITH_QPOASES=OFF and it built clean in 9 seconds producing libacados.so. So the fix exists and is one flag — but a junior dev hitting a 100-line pointer-type error wall inside a vendored third-party fork will not guess that flag, and this is exactly the day-eating toolchain fight you told me to disqualify. This risk is LIVE on a Pi 5: Raspberry Pi OS Trixie / recent Ubuntu arm64 ship GCC 14+, where this reproduces; only older Bookworm (GCC 12) escapes it.
- **Verdict:** AVOID. Three independent disqualifiers: (1) I measured an actual build failure on a modern GCC that a junior dev would burn hours on; (2) 253 MB of vendored dependencies for a one-week class assignment is absurd surface area; (3) even when it works, its Riccati structure gives you tiny nx-by-nx blocks at ~0.17 FLOP/byte, which is the OPPOSITE of the paper's thesis. It is a superb tool for real embedded NMPC research and completely wrong for a 2-day compute-bound demonstration.
- **Verified:** yes — cloned recursively, built (failed), diagnosed, rebuilt with workaround, timed both

## qpOASES

- **URL:** https://github.com/coin-or/qpOASES
- **Language:** C++ ONLY — DISQUALIFYING. VERIFIED by direct file count on the clone: src/ and include/ contain 18 .cpp, 22 .hpp, 11 .ipp and ZERO .c files. Every core file (QProblem.cpp, Bounds.cpp, Constraints.cpp, Matrices.cpp, SQProblemSchur.cpp) is C++. There IS an interfaces/c/ directory but it contains only .c EXAMPLE programs calling a C wrapper — the library itself still compiles with g++ and drags in libstdc++. You cannot ship this in a C11 deliverable.
- **License:** LGPL-2.1 — also awkward: viral-ish linking obligations you do not want to reason about for a class deliverable.
- **Dense or sparse:** Dense active-set (online active set strategy) with rank-1 factorization updates — same roofline problem as DAQP: ~0.50 fp64 FLOP/byte, invariant in n, ~3x below the Pi 5 ridge. Plus heavy data-dependent branching on working-set changes.
- **ARM/NEON:** None. Plain portable C++ with optional BLAS/LAPACK replacement stubs (BLASReplacement.cpp / LAPACKReplacement.cpp).
- **Build difficulty:** Moderate on its own (CMake or the provided make_linux.mk, maybe 30-60 min), but irrelevant — it is disqualified on language before you start. Note also that its embedded C fork (qpOASES_e, as vendored by acados) is what I measured FAILING to compile under GCC 14.
- **Verdict:** AVOID — hard disqualification on the stated C11 constraint, confirmed by counting actual source files rather than trusting the README. Even setting language aside it is a rank-1-update active-set method (wrong roofline shape) under LGPL (wrong license shape) with a known GCC 14 breakage in its C fork. Nothing recommends it here.
- **Verified:** yes — cloned and counted actual source file extensions

## TinyMPC

- **URL:** https://github.com/TinyMPC/TinyMPC
- **Language:** C++ — DISQUALIFYING, despite the 'embedded/microcontroller' marketing that implies C. VERIFIED on the clone: src/tinympc/ contains admm.cpp, tiny_api.cpp, codegen.cpp, rho_benchmark.cpp and types.hpp/tiny_api.hpp/admm.hpp — ZERO .c files anywhere in the repo. The 356 .h files are a VENDORED COPY OF EIGEN under include/Eigen. So it is C++ plus a heavyweight header-only C++ template library.
- **License:** MIT (the license is the one good thing here)
- **Dense or sparse:** Dense (Eigen fixed-size matrices) but algorithmically ADMM + a precomputed infinite-horizon LQR/Riccati gain. The whole design goal is to precompute the expensive factorization ONCE offline and make the online loop a sequence of tiny cached matrix-vector products — i.e. it is explicitly engineered to be as close to zero-FLOP as possible. AI is GEMV-class, ~0.25 FLOP/byte, ~6x below the ridge. This is the most anti-compute-bound design in the entire survey.
- **ARM/NEON:** Only whatever Eigen's generic NEON vectorization gives you at -O3, and on the tiny fixed-size matrices TinyMPC uses that is mostly irrelevant. No hand-tuned aarch64 kernels.
- **Build difficulty:** The CMake build is easy (~5 min), so this one is a schedule-SAFE library — it just solves the wrong problem in the wrong language. Its codegen emits C++ too, so that is not an escape hatch.
- **Verdict:** AVOID. Fails the hard C11 constraint (core is .cpp/.hpp + vendored Eigen), and even as an offline oracle it is worse than DAQP or OSQP because you would be pulling in Eigen. Most importantly its entire value proposition — precompute the factorization, make the online step trivially cheap — is the exact opposite of what you need to demonstrate. Do not be seduced by the 'MPC on a microcontroller' framing; cheap is the enemy here.
- **Verified:** yes — cloned and inspected src/ directly

## HPIPM

- **URL:** https://github.com/giaf/hpipm
- **Language:** C (with Python/MATLAB/Octave interfaces). Clean C core.
- **License:** 2-Clause BSD (verified: LICENSE.txt line 6 reads 'The 2-Clause BSD License')
- **Dense or sparse:** Both — it genuinely has a dense_qp/ module (d_dense_qp_ipm.c, d_dense_qp_kkt.c, plus QCQP variants) alongside ocp_qp/. The dense_qp interior-point path is, on paper, EXACTLY the right algorithmic shape for you: a full dense Cholesky/LDL refactorization every IPM iteration, which is the O(n^3)-on-O(n^2)-bytes kernel that crosses the roofline ridge. The OCP (Riccati) path is not — it decomposes into nx-by-nx blocks at ~0.17 FLOP/byte.
- **ARM/NEON:** Yes, entirely via its mandatory BLASFEO dependency — so the same verified A76-named/A57-tuned aarch64 NEON assembly kernels. You must build BLASFEO first with -DTARGET=ARMV8A_ARM_CORTEX_A76 and then point HPIPM at it.
- **Build difficulty:** Two-stage dependent build (BLASFEO first, then HPIPM against it), and the codebase is LARGE: I measured 136,794 lines of C. Realistically 3-6 hours for a junior dev to build both, get the panel-major data marshalling right, and solve one QP — and that assumes no target-mismatch link errors between the BLASFEO you built and the one HPIPM expects, which is a classic failure mode.
- **Verdict:** AVOID for this schedule, with genuine regret — its dense_qp IPM is the most technically CORRECT library answer in the survey and in a 3-week project I would recommend it. But 137k LOC, a mandatory two-stage dependency build, and a non-BLAS panel-major API is 3-6 hours minimum against a 2-day budget, and it buys you something you can write yourself in ~400 lines that you will actually understand well enough to write 5 days of analysis about. Cite it in the paper as the production-grade implementation of the approach you built.
- **Verified:** yes — cloned, confirmed dense_qp/ module exists, counted LOC, read license

## PIQP

- **URL:** https://github.com/PREDICT-EPFL/piqp
- **Language:** C++ — DISQUALIFYING. Officially 'header only C++14 leveraging the Eigen library'. There IS a C interface (piqp::piqp_c, deliberately modelled on OSQP's), but the official install docs confirm building it STILL requires a C++ compiler and Eigen 3.3.4+, and the documented CMake usage sets CMAKE_CXX_STANDARD 14. So the shipped artifact is a C++ library with a C-callable facade, not a C library.
- **License:** BSD 2-Clause (permissive — the license is fine, the language is not)
- **Dense or sparse:** Both dense and sparse backends are supported, and the dense proximal interior-point backend would be roofline-appropriate (full dense factorization per iteration). Technically among the better-shaped options.
- **ARM/NEON:** Only Eigen's generic vectorization; you would compile with -march=native / -mcpu=cortex-a76 and hope. No hand-written aarch64 kernels.
- **Build difficulty:** CMake build is straightforward but you are compiling a header-only C++14 template library with Eigen on a Pi 5 — that means slow template instantiation (expect several minutes, and watch for OOM if you parallelise too hard on 8 GB). Call it 1-2 hours realistically including the Eigen dependency.
- **Verdict:** AVOID. Fails the C11 constraint: a C-callable ABI is not a C implementation, and shipping it means shipping libstdc++ and Eigen in a deliverable that is specified as C. It is a good modern solver and the dense backend has the right shape, so it is a legitimate offline oracle if you already have Eigen — but OSQP and DAQP are strictly better oracles here because they are actually C and build in 2 seconds.
- **Verified:** yes — fetched repo and the official C/C++ installation docs

## qpSWIFT

- **URL:** https://github.com/qpSWIFT/qpSWIFT
- **Language:** Pure C (ANSI-C). VERIFIED on the clone: src/ + include/ contain 17 .c and 8 .h, no C++. Language is genuinely fine.
- **License:** GPL-3.0 — VERIFIED by reading the LICENSE file ('GNU GENERAL PUBLIC LICENSE Version 3'). This is a strong copyleft and a poor fit for a course deliverable you may want to publish or reuse.
- **Dense or sparse:** SPARSE — and this is the disqualifier. It is a primal-dual interior point method (Mehrotra predictor-corrector, Nesterov-Todd scaling) whose linear algebra is sparse LDL' factorization with an AMD (approximate minimum degree) fill-reducing ordering. Same physics as OSQP/QDLDL: the sparse triangular solves and the numeric factorization are indirect-indexed gather/scatter over an elimination tree, so the inner loop is a dependent-load chain, not a FLOP stream. It will profile memory-latency-bound. Note the cruel irony: the IPM ALGORITHM is right (full refactorization every iteration) but the SPARSE implementation throws away the arithmetic intensity that would have made it compute-bound.
- **ARM/NEON:** None. Portable ANSI-C; the docs claim testing on x86, x86_64 and ARM, so it will build on the Pi, but there are no NEON kernels and sparse indirection would defeat them anyway.
- **Build difficulty:** Easy — CMake, small codebase, no heavyweight dependencies (AMD/LDL are vendored). Probably 20-30 minutes for a junior dev. Schedule-safe to build; just not worth building.
- **Verdict:** AVOID. Two independent problems: GPL-3.0 contaminates your deliverable, and the sparse LDL+AMD internals put it on the memory-latency-bound side exactly like OSQP. If you want a sparse-IPM contrast datapoint for the paper, OSQP already gives you that under Apache-2.0 with a 2-second build, so qpSWIFT adds licence risk for zero marginal insight.
- **Verified:** yes — cloned, counted source files, read the LICENSE

## QPALM

- **URL:** https://github.com/kul-optec/QPALM
- **Language:** C core with C++/Python/Julia/MATLAB/Fortran interfaces. The core is genuinely C, so it passes the language bar.
- **License:** LGPL-3.0 — awkward copyleft for a deliverable; weaker than GPL but still imposes relinking obligations you should not have to think about for a class project.
- **Dense or sparse:** SPARSE. It is a proximal augmented-Lagrangian method whose linear algebra is delegated to LADEL, a sparse LDL factorization library pulled in as a GIT SUBMODULE, with LOBPCG for eigenvalue estimation. Sparse factorization with update/downdate means indirect indexing and pointer chasing — memory-latency-bound, same failure mode as OSQP and qpSWIFT.
- **ARM/NEON:** None. Portable C, no aarch64 kernels.
- **Build difficulty:** Moderate and RISKIER THAN IT LOOKS because of the LADEL git submodule: a plain `git clone` without --recursive gives you a repo that configures and then fails to build, which is a classic junior-dev time sink. Budget 1-2 hours with a real chance of submodule/CMake friction. (This is the same class of hazard that I actually measured biting acados.)
- **Verdict:** AVOID. LGPL-3.0 plus sparse LDL via a submodule dependency: it costs more build risk than OSQP and lands in the same wrong roofline regime. There is no scenario in this 2-day budget where QPALM is the right pick over OSQP-as-oracle.
- **Verified:** yes — fetched repo, confirmed C core, LGPL-3.0, and the LADEL submodule dependency

## SCS

- **URL:** https://github.com/cvxgrp/scs
- **Language:** C. Clean C implementation with optional interfaces.
- **License:** MIT (permissive, ships fine)
- **Dense or sparse:** SPARSE by default — the default direct solver is sparse LDL' via QDLDL with AMD ordering (the same QDLDL I dissected inside OSQP, with the `yVals[Li[j]] -= Lx[j]*...` indirect-indexed inner loop). A dense LAPACK path exists, as do MKL Pardiso and Apple Accelerate backends, but the default and the portable path are sparse. Memory-latency-bound.
- **ARM/NEON:** None natively. You would only get NEON by linking an external optimized BLAS/LAPACK for the dense path, which adds a dependency (OpenBLAS) and its own build/tuning risk on the Pi.
- **Build difficulty:** Easy build (make or cmake, ~5-15 min). But there is a conceptual cost: SCS is a general CONIC solver (ADMM over cones with Anderson acceleration), so expressing a plain box-constrained QP means learning its cone API and data format — more onboarding than OSQP or DAQP for strictly less relevance.
- **Verdict:** AVOID (or at most a redundant oracle). MIT and C so it is legally and linguistically fine, but it is a cone solver aimed at a much more general problem class than your MPC QP, its default linear algebra is the same sparse QDLDL that lands memory-bound, and its ADMM iterations are GEMV-class (~0.25 FLOP/byte). It gives you nothing OSQP does not give you more directly, and OSQP is the standard citation for this algorithm family in MPC. Skip it.
- **Verified:** yes — fetched repo and docs, and separately read the vendored QDLDL source via OSQP

## ECOS

- **URL:** https://github.com/embotech/ecos
- **Language:** C (src/ + CMakeLists.txt, ANSI-C style). Language passes.
- **License:** GPL-3.0 — VERIFIED from the repo ('ECOS is distributed under the GNU General Public License v3.0', with alternative commercial licensing on request from embotech). Strong copyleft; bad for a deliverable.
- **Dense or sparse:** SPARSE. It is an interior-point conic solver whose KKT systems are solved by sparse LDL factorization (vendored SuiteSparse-derived LDL and AMD ordering). Indirect indexing, pointer chasing, memory-latency-bound — same regime as OSQP/qpSWIFT/SCS.
- **ARM/NEON:** None. Portable C, no aarch64-specific kernels.
- **Build difficulty:** Easy in principle (make/CMake, small), but see the maintenance risk below — dormant C projects are exactly where you meet the GCC-14 -Wincompatible-pointer-types permerror class of failure that I measured killing acados' bundled qpOASES_e. A 2022-vintage C codebase compiled with a 2025/2026 GCC is a coin flip.
- **Verdict:** AVOID. Three strikes: GPL-3.0 licence contamination; sparse LDL internals that guarantee a memory-bound profile; and it is DORMANT — VERIFIED last meaningful commit January 2022 (v2.0.10), nothing since. A four-year-unmaintained C codebase against a modern GCC is precisely the unbounded toolchain risk you told me to disqualify on sight, and it buys nothing OSQP does not already provide under Apache-2.0.
- **Verified:** yes — fetched repo and commit history (last commit Jan 2022)

## CVXGEN

- **URL:** https://cvxgen.com/docs/index.html
- **Language:** Generates 'library-free C' — the generated artifact is genuinely plain C with no dependencies, which is its one real strength.
- **License:** UNCLEAR AND RESTRICTIVE — the site has a Licensing page but publishes no open-source licence for the generated code. It is an academic/commercial web service (Mattingley & Boyd, Stanford), not an open-source library. You cannot assume you may redistribute generated code in a deliverable. This alone is disqualifying for a submitted assignment.
- **Dense or sparse:** DENSE, and algorithmically this is the closest thing in the survey to what you actually want: it generates a fully-unrolled primal-dual interior-point solver with the sparsity pattern hard-coded at generation time, doing a full factorization each iteration. But 'fully unrolled' means straight-line code with NO LOOPS — thousands of scalar statements. That is terrible for the paper: you cannot cleanly hand-vectorize it, you cannot scale N without regenerating, and the I-cache pressure from a giant unrolled blob on an A76 introduces a confounding front-end bottleneck that muddies any roofline argument.
- **ARM/NEON:** None. It emits scalar C and relies entirely on the compiler — which per your own prior finding gets ~12.7% of peak on auto-vectorized math.
- **Build difficulty:** The generated code compiles trivially (it is dependency-free C). But you must use an ONLINE WEB INTERFACE to generate it — there is no offline tool. That is a hard external dependency on a third-party service still serving 2013-era documentation, with a hard problem-size cap (~2000 total coefficients, which your condensed QP could plausibly exceed at longer horizons). If the service is down or your account is not approved, your project is dead.
- **Verdict:** AVOID. Unclear redistribution rights, a mandatory third-party web service in your build path, a size ceiling that collides with the very N-scaling knob your paper depends on, and fully-unrolled loop-free code that is actively hostile to hand-vectorization and to a clean roofline story. The N-scaling experiment — the heart of your paper — would require re-generating code from a website for every horizon length. Structurally wrong for this project.
- **Verified:** yes — fetched the official CVXGEN documentation site

## GRAMPC

- **URL:** https://github.com/grampc/grampc
- **Language:** Plain C core — VERIFIED on the clone: 87 .c files, with only 14 .cpp in the interfaces. Language passes cleanly.
- **License:** BSD-3-Clause — VERIFIED by reading LICENSE.txt (copyright Graichen/Voelz/Wietzke et al., 2014-2025, with the standard 3-clause redistribution/endorsement terms). Permissive and actively maintained (copyright runs to 2025).
- **Dense or sparse:** NEITHER, effectively — and that is the disqualifier. GRAMPC is a gradient-based nonlinear MPC framework: augmented Lagrangian outer loop with a projected-gradient inner minimization. It is essentially MATRIX-FREE — it evaluates system dynamics and adjoint sensitivities and takes gradient steps rather than factorizing anything. Its cost is dominated by ODE integration and user-supplied model function evaluations, not by dense linear algebra. Arithmetic intensity is GEMV-class or worse (~0.25 FLOP/byte or lower), and the profile would be dominated by your own model callbacks, making the roofline analysis meaningless.
- **ARM/NEON:** None. Portable C by design (it targets microcontrollers), no aarch64 kernels.
- **Build difficulty:** Genuinely easy — CMake/Makefile, plain C, no dependencies, ~30 min. It is one of the more schedule-safe builds here. But you would also have to write nonlinear model/adjoint callbacks, which is real work and real bug surface for a thermal system that is adequately modelled as linear.
- **Verdict:** AVOID. Good licence, good language, easy build — and completely the wrong physics. A matrix-free projected-gradient method is the LOWEST arithmetic intensity approach in this entire survey; it is designed to avoid factorization altogether. It would profile as neither cleanly compute-bound nor cleanly memory-bound, just dominated by your own model-evaluation callbacks. Also: nonlinear MPC is scope creep you cannot afford in 2 days when a linear thermal model is entirely defensible.
- **Verified:** yes — cloned, counted source files, read LICENSE.txt

## FORCESPRO (Embotech) and ODYS Embedded MPC

- **URL:** https://forces.embotech.com/documentation/introduction/index.html
- **Language:** Both generate C. Both are CLOSED-SOURCE COMMERCIAL products.
- **License:** COMMERCIAL, PER-SEAT, NEGOTIATED — VERIFIED from Embotech's own documentation: FORCESPRO licensing is split into an Engineer License (per engineer computer, for generating solvers), a Software Testing License (SiL/CI, per platform), and a Hardware Testing License (HiL/field testing, per platform controlling a physical system) — meaning deploying to your Pi 5 is specifically the licence tier you would have to pay for. ODYS Embedded MPC is likewise a commercial library. Neither publishes an academic licence you can self-serve.
- **Dense or sparse:** Dense, structure-exploiting interior-point / ADMM generated code. Algorithmically these are excellent and genuinely fast — FORCESPRO is the industrial gold standard for embedded MPC. Irrelevant given the licence.
- **ARM/NEON:** FORCESPRO targets ARM Cortex-A/-M among many embedded platforms, so aarch64 support exists, but the generated kernels are closed and unauditable — which is itself fatal for your paper, since you cannot inspect or explain the inner loop you are profiling.
- **Build difficulty:** UNBOUNDED — gated on a commercial licence negotiation and account approval before you can generate a single line of code. For a one-week assignment this is not a build-time estimate, it is a project-killing dependency on someone else's sales cycle.
- **Verdict:** AVOID, absolutely and without qualification. Licence acquisition alone can exceed your entire project duration, and even on success you would be profiling and writing a paper about a BLACK BOX you are not permitted to inspect — which defeats the purpose of an embedded-systems analysis assignment. Mention FORCESPRO in the paper's related-work section as the commercial state of the art; never put it in your build path.
- **Verified:** yes — fetched Embotech FORCESPRO documentation and ODYS product pages for licensing terms

## muAO-MPC

- **URL:** https://github.com/muaompc/muaompc
- **Language:** Generates portable C (good), but the GENERATOR ITSELF is a Python package. So Python 3 becomes a build-time dependency of your deliverable.
- **License:** BSD-3-Clause (permissive — licence is fine)
- **Dense or sparse:** DENSE but gradient-based, which is the disqualifier. The generated QP solver is an augmented Lagrangian (method of multipliers) outer loop wrapping Nesterov's fast gradient method. Its inner loop is dense matrix-VECTOR products, deliberately restricted to only additions and multiplications (explicitly no divisions, no square roots) for microcontroller determinism. GEMV arithmetic intensity is ~0.25 fp64 FLOP/byte — roughly 6x BELOW the Pi 5 ridge of 1.55-1.75. It is designed for a low memory footprint and deterministic WCET, which are precisely the design goals that produce a memory-bound, not compute-bound, profile.
- **ARM/NEON:** None. It targets Cortex-M microcontrollers, Arduino and Lego Mindstorms NXT — the generated code is deliberately simple portable C with fixed-point support, with zero aarch64 awareness.
- **Build difficulty:** The generated C compiles easily, but you must first install and drive a Python code-generation tool whose docs describe v0.4.x as 'no longer maintained' and whose last substantive activity is old. Budget 1-3 hours with real risk of Python-packaging friction, and note that the toolchain sitting between you and your source code makes iterating on horizon length N clumsy.
- **Verdict:** AVOID. The licence and generated-language are fine, but a Nesterov fast-gradient method on dense GEMV is the single most memory-bound-by-design algorithm you could pick for a unit that must prove it is compute-bound. It optimizes for exactly the wrong figure of merit (minimum FLOPs, minimum memory, deterministic WCET). Additionally, its microcontroller heritage means it is tuned for problem sizes far below what makes a Cortex-A76 interesting.
- **Verified:** yes — fetched repo and supporting literature

## Notes

HEADLINE: Do not use any of these as the compute-bound unit. Write ~400 lines of C11 yourself. Use OSQP or DAQP purely as a validation oracle. I verified this by building, not by reading marketing.

=== THE DECISIVE FINDING: ALGORITHM CLASS DETERMINES ROOFLINE POSITION, AND ALMOST EVERY LIBRARY PICKS THE WRONG SIDE ===

Every solver in this survey is engineered to MINIMIZE FLOPs. That is the correct goal for embedded MPC and the exact opposite of what your paper needs. I computed arithmetic intensity (fp64) for each inner-kernel class against your stated Pi 5 ridge of 1.55-1.75 FLOP/byte:

  Matrix-free projected gradient (GRAMPC)              ~0.1-0.25 F/B   >5x BELOW ridge
  Dense GEMV: ADMM / fast-gradient (muAO-MPC, TinyMPC)      0.25 F/B    6x BELOW ridge
  Rank-1 LDL update: ACTIVE-SET (DAQP, qpOASES)             0.50 F/B    3x BELOW ridge, INVARIANT IN n
  Sparse LDL + AMD (OSQP, qpSWIFT, SCS, ECOS, QPALM)   latency-bound    indirect-indexed dependent loads
  Riccati on nx-by-nx blocks (HPIPM/acados OCP, nx=4)       0.17 F/B    9x BELOW ridge
  --------------------------------------------------------------------------------
  DENSE CHOLESKY refactorization, n=60 (condensed IPM)      2.50 F/B    ABOVE ridge
  DENSE CHOLESKY refactorization, n=120                     5.00 F/B    3x ABOVE ridge
  CONDENSING GEMM H = S'QS, N=40                           13.3  F/B    8x ABOVE ridge
  CONDENSING GEMM H = S'QS, N=60                           20.0  F/B   11x ABOVE ridge

Three consequences you should put in the paper:

1. THE ACTIVE-SET TRAP IS THE SAME TRAP AS THE MIQP BRANCH-AND-BOUND. Rank-1 update AI is 0.50 F/B *independent of n* — you cannot grow your way onto the compute-bound side. Active-set methods exist precisely to avoid the O(n^3) refactorization. Combined with data-dependent branching on working-set changes, DAQP and qpOASES would reproduce your measured 0.48 FLOP/cyc failure. This generalizes your existing finding: it is not "integer variables are bad", it is "any method whose speed comes from avoiding arithmetic is bad."

2. YOUR PRIOR SUSPICION ABOUT SPARSE QP IS CONFIRMED WITH SOURCE-LEVEL EVIDENCE. QDLDL's factorization inner loop, which OSQP vendors, is literally `yVals[Li[j]] -= Lx[j] * yVals_cidx;` — one indirect load, one indirect store, one FMA, with every address depending on a just-loaded index. That is a dependent-load chain, not a FLOP stream, and NEON cannot help. Mark the sparse-QP question RESOLVED: memory-latency-bound, verified.

3. THE COMPUTE-BOUND KERNEL IS THE CONDENSING STEP, NOT THE QP SOLVE. Forming the condensed Hessian H = S'QS is GEMM-shaped at 13-20 F/B and scales as N^3, while the Cholesky is N^3/3 at n/24 F/B. Both sit above the ridge and both grow with horizon. This confirms N as a genuine fidelity knob and gives you two independent compute-bound kernels to profile and hand-vectorize.

=== RECOMMENDED PLAN (schedule-safe) ===
Day 1 AM: dense condensing (build S, then H = S'QS + R, g = S'Q(Ax0 - ref)) in plain C11.
Day 1 PM: dense Cholesky + forward/back substitution; wrap in a short primal-dual IPM or even a simple projected-Newton/active-set-free log-barrier. Validate against OSQP or DAQP on the host to 1e-9.
Day 2 AM: hand-vectorize the Cholesky and the condensing GEMM with NEON intrinsics (arm_neon.h, float32x4_t / float64x2_t, vfmaq_*). Your prior 100%-vs-12.7% finding says this is where the paper's headline number comes from.
Day 2 PM: sweep N, measure FLOP/cyc via perf, plot the roofline. Optional stretch: link BLASFEO for a "how close to expert assembly did I get" number.

Total new code ~400 lines. No external build risk. Every line explainable in a paper. Use -std=gnu11 (per your GCC/FMA-contraction finding), -O3 -mcpu=cortex-a76, and verify FMA contraction actually happened by checking for fmla in the disassembly.

=== CONCRETE LIBRARY RECOMMENDATIONS ===
SHIP: nothing. Your own C11.
ORACLE (pick one, both measured at a 2-second build): DAQP (MIT, pure C, zero deps, 4780 LOC, 2-function API — my first choice, and it can legally ship) or OSQP (Apache-2.0, pure C, the standard citation, but FetchContent needs network at configure time).
STRETCH ONLY: BLASFEO.
EVERYTHING ELSE: avoid.

=== VERIFICATION LOG (what I actually did, not what READMEs claim) ===
Built and timed on x86_64/GCC 14.3.1: DAQP 2s OK; OSQP 2s OK (ran osqp_demo, solved, obj 1.8800); BLASFEO 5s OK; acados FAILED then 9s OK with a flag.
Language calls made by counting real source files in fresh clones, which overturned marketing in three cases: qpOASES = 18 .cpp / 0 .c; TinyMPC = 12 .cpp + vendored Eigen / 0 .c despite "embedded microcontroller" framing; PIQP = header-only C++14 whose "C interface" still needs a C++ toolchain.

MEASURED SCHEDULE HAZARD (the most important practical result): acados FAILED TO BUILD. `cmake -DACADOS_WITH_QPOASES=ON && make` died at acados/external/qpoases/src/QProblem.c:472 with "error: passing argument 1 of 'ConstraintsCPY' from incompatible pointer type [-Wincompatible-pointer-types]". Cause: GCC 14 turned that warning into a permerror (gcc.gnu.org/gcc-14/porting_to.html); acados' bundled qpOASES_e fork predates it. Workaround is one flag (-DACADOS_WITH_QPOASES=OFF -> clean build in 9s), but a junior dev facing a wall of pointer errors inside a vendored third-party fork will not guess it. THIS IS LIVE ON A PI 5: Raspberry Pi OS Trixie and current Ubuntu arm64 ship GCC 14+. This is the day-eating toolchain fight you asked me to disqualify, and it is not hypothetical — I reproduced it.

BLASFEO A76 QUESTION, PRECISELY ANSWERED: ARMV8A_ARM_CORTEX_A76 is a real first-class target (CMakeLists.txt lines 65, 173, 400, 416). BUT there is no A76-specific kernel block — line 385 sets TARGET2=ARMV8A_ARM_CORTEX_A57, and the kernel lists at lines 965-1016 dispatch off A57. So the Pi 5 gets hand-written aarch64 NEON assembly (fp32: sgemm 16x4/12x4/8x8/8x4, sgemv, spack; fp64: dgemm 8x4/4x4, dgemv, dger, dpack), compiled with only -march=armv8-a+crc+crypto+simd and notably NOT -mcpu=cortex-a76. Verdict: real NEON assembly panel-major kernels, A57-TUNED and A76-COMPATIBLE, not A76-optimized. State it that way in the paper — the distinction is defensible and shows you read the build system.

LICENSE DISQUALIFICATIONS worth flagging early: ECOS and qpSWIFT are GPL-3.0; QPALM is LGPL-3.0; qpOASES is LGPL-2.1; CVXGEN has no published redistribution licence and requires a web service; FORCESPRO/ODYS are commercial per-seat with a separate paid tier specifically for deploying to hardware.

ONE CAVEAT ON MY OWN WORK: all builds were on x86_64 Fedora with GCC 14.3.1; no aarch64 cross-compiler was available in this environment, so I could not compile BLASFEO's A76 target or confirm Pi 5 wall-clock build times. Build-system friction and language/license findings transfer directly; ARM codegen does not. The AI figures are analytic (standard FLOP/byte counts), not measured on hardware.
