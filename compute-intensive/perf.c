/* ===========================================================================
 * Measurement harness. Built BEFORE the solver on purpose: it is what
 * catches "the compiler deleted the kernel" and "the working set crept"
 * while there is still time to fix them.
 *
 * Cycles come from perf_event_open where the PMU is available, and from
 * CLOCK_MONOTONIC_RAW scaled by a measured frequency otherwise. On a Pi 5
 * the arm-pmu device-tree node is missing under some 6.12.y kernels, so
 * the fallback is not hypothetical.
 * ======================================================================== */
#define _GNU_SOURCE
#include "critter.h"

#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>

#if defined(__linux__)
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <linux/perf_event.h>
#endif

/* --- monotonic wall clock ---------------------------------------------- */
static uint64_t now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* --- perf_event_open wrapper ------------------------------------------- */
#if defined(__linux__)
static int pe_open(uint32_t type, uint64_t config)
{
    struct perf_event_attr a;
    memset(&a, 0, sizeof a);
    a.type           = type;
    a.size           = sizeof a;
    a.config         = config;
    a.disabled       = 1;
    a.exclude_kernel = 1;   /* perf_event_paranoid=2 allows user-only */
    a.exclude_hv     = 1;
    long fd = syscall(__NR_perf_event_open, &a, 0, -1, -1, 0);
    return (int)fd;
}
#endif

static int   g_fd_cyc = -1, g_fd_ins = -1;
static int   g_probed = 0;

static void perf_probe(void)
{
    if (g_probed) return;
    g_probed = 1;
#if defined(__linux__)
    g_fd_cyc = pe_open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES);
    g_fd_ins = pe_open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS);
#endif
}

void crit_perf_begin(crit_perf_t *pf)
{
    perf_probe();
    memset(pf, 0, sizeof *pf);
#if defined(__linux__)
    if (g_fd_cyc >= 0) { ioctl(g_fd_cyc, PERF_EVENT_IOC_RESET, 0);
                         ioctl(g_fd_cyc, PERF_EVENT_IOC_ENABLE, 0); }
    if (g_fd_ins >= 0) { ioctl(g_fd_ins, PERF_EVENT_IOC_RESET, 0);
                         ioctl(g_fd_ins, PERF_EVENT_IOC_ENABLE, 0); }
#endif
    pf->ns_wall = now_ns();
}

void crit_perf_end(crit_perf_t *pf)
{
    pf->ns_wall = now_ns() - pf->ns_wall;
#if defined(__linux__)
    uint64_t v = 0;
    if (g_fd_cyc >= 0) {
        ioctl(g_fd_cyc, PERF_EVENT_IOC_DISABLE, 0);
        if (read(g_fd_cyc, &v, sizeof v) == (ssize_t)sizeof v) {
            pf->cycles = v; pf->counters_valid |= 1u;
        }
    }
    if (g_fd_ins >= 0) {
        ioctl(g_fd_ins, PERF_EVENT_IOC_DISABLE, 0);
        if (read(g_fd_ins, &v, sizeof v) == (ssize_t)sizeof v) {
            pf->instructions = v; pf->counters_valid |= 2u;
        }
    }
#endif
}

/* --- FNV-1a over the outputs, consumed through a volatile sink ---------
 * Without this the optimiser is entitled to delete the entire kernel,
 * and at -O3 -ffast-math it will.
 */
uint64_t crit_fnv1a(const void *buf, size_t n, uint64_t seed)
{
    const unsigned char *p = (const unsigned char *)buf;
    uint64_t h = seed ? seed : 1469598103934665603ull;
    for (size_t i = 0; i < n; i++) {
        h ^= (uint64_t)p[i];
        h *= 1099511628211ull;
    }
    return h;
}

/* --- peak probe --------------------------------------------------------
 * Cortex-A76 and any 128-bit-constrained x86 both peak at 8 fp64 FLOP/cyc
 * (2 FMA/cycle x 2 lanes x 2 FLOP). This is the denominator of every
 * percentage the paper reports, so it must be right before anything else
 * is measured.
 *
 * Independence is the point: with fewer chains than the pipe latency x
 * throughput product you measure the dependency chain, not the hardware.
 */
/* Explicit 128-bit vectors: GCC unrolls a scalar chain loop into scalars
 * and never re-packs them, which caps the probe at the 2 scalar-FMA/cycle
 * issue limit and understates the ceiling by 2x. vector_size(16) maps to
 * SSE/AVX-128 on x86 and to NEON on aarch64, so one probe serves both.
 *
 * 8 vector chains x 2 lanes = 16 independent fp64 FMA chains, comfortably
 * past the latency x throughput product on either core.
 */
typedef double v2d __attribute__((vector_size(16)));

#define PROBE_CHAINS 8
#define PROBE_ITERS  2000000ull

double crit_peak_probe(double *out_fpc)
{
    static volatile double sink;
    v2d a[PROBE_CHAINS];
    const v2d b = { 1.0000001, 1.0000001 };
    const v2d c = { 1e-9, 1e-9 };
    for (int i = 0; i < PROBE_CHAINS; i++) {
        a[i][0] = 1.0 + (double)i * 1e-3;
        a[i][1] = 1.0 + (double)i * 2e-3;
    }

    crit_perf_t pf;
    crit_perf_begin(&pf);
    for (uint64_t it = 0; it < PROBE_ITERS; it++) {
        a[0] = a[0]*b + c;  a[1] = a[1]*b + c;
        a[2] = a[2]*b + c;  a[3] = a[3]*b + c;
        a[4] = a[4]*b + c;  a[5] = a[5]*b + c;
        a[6] = a[6]*b + c;  a[7] = a[7]*b + c;
    }
    crit_perf_end(&pf);

    double acc = 0.0;
    for (int i = 0; i < PROBE_CHAINS; i++) acc += a[i][0] + a[i][1];
    sink = acc;
    (void)sink;

    double flops = 2.0 * 2.0 * (double)PROBE_CHAINS * (double)PROBE_ITERS;
    double gfs   = flops / (double)pf.ns_wall;                /* GFLOP/s   */
    *out_fpc = (pf.counters_valid & 1u) && pf.cycles
             ? flops / (double)pf.cycles : 0.0;
    return gfs;
}
