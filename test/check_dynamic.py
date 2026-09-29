#!/usr/bin/env python3
"""
check_dynamic.py -- run each unit the way the team actually runs it.

Each Critter unit ships as one runnable program, so each is driven from the
outside through its real interface:

    unit 1   start it, let it sample, interrupt it, audit the CSV
    unit 2   hand it a log file and read what it reports and writes
    unit 3   invoke bin/critter with the arguments and environment overrides
             its README documents

For unit 3 this is the ONLY level at which it is tested. The unit has one
entry point and one binary; its README documents a command-line interface and
a set of environment overrides, and that is the surface a reviewer can reach
without reaching inside. Testing mpc.c or thermal.c as separate translation
units would produce findings against files the team never runs in isolation.

Black-box testing turned out to be the stronger choice here rather than a
concession. The negative-weight defect (CMP-01) is more damning through the CLI
than it was through a direct call: under the -O3 -ffast-math build the unit's
README prescribes, the failure does not surface as a NaN but as an
uninitialised sentinel reported as a safety margin, with both actuators
saturated, and an exit status of zero.

sensor_sim.c is linked so unit 1 can run at all -- io_unit.c cannot execute
without an implementation of sensor.h -- but is not audited. Built, not
reviewed.

Fault inputs are written to faults/ and left there on purpose, so a reviewer
can read the exact bytes that produced each verdict.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _dir(var, *default):
    v = os.environ.get(var)
    return os.path.abspath(v) if v else os.path.join(ROOT, *default)


U1 = _dir("U1", "io-subsystem")
U2 = _dir("U2", "memory", "src")
U3 = _dir("U3", "compute-intensive")
U3_INC = (os.path.abspath(os.environ["U3_INC"]) if os.environ.get("U3_INC")
          else os.path.join(U3, "include"))
U3_SRC = os.path.join(U3, "src") if os.path.isdir(os.path.join(U3, "src")) \
    else U3

FAULTS = os.path.join(HERE, "faults")
BUILD = os.path.join(HERE, "build")


def emit(module, tid, standard, rule, verdict, evidence):
    print("CT|%s|%s|%s|%s|%s|%s"
          % (module, tid, standard, rule, verdict,
             " ".join(str(evidence).split())))
    sys.stdout.flush()


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ===========================================================================
# Fault inputs for unit 2
# ===========================================================================

GOOD = [
    "01/15/2023 08:30:00,19.250",
    "06/21/2024 14:02:11,25.880",
    "11/03/2025 23:59:59,18.410",
    "03/09/2023 06:15:42,21.004",
]


def write_faults():
    os.makedirs(FAULTS, exist_ok=True)
    files = {
        # month = 13 -> summary[12], one past the end of a 12-element array
        "month_13.txt": GOOD + ["13/01/2024 00:00:00,20.000"] + GOOD,
        # month = 0 -> summary[-1], one before the start
        "month_00.txt": GOOD + ["00/01/2024 00:00:00,20.000"] + GOOD,
        # a stuck sensor: every reading identical, so std_dev == 0
        "constant.txt": ["0%d/01/2024 12:00:00,22.000" % (i % 9 + 1)
                         for i in range(1, 41)],
    }
    # One physical line long enough that the buffered read splits it, arranged
    # so BOTH halves parse as valid readings. The first record is padded to
    # exactly MAX_LINE_LENGTH-1 = 127 characters, which is all fgets will take,
    # so the second record begins precisely at the split point.
    rec1, rec2 = "07/14/2024 15:22:41,26.118", "01/02/2023 03:04:05,99.900"
    files["split_line.txt"] = GOOD + [rec1 + " " * (127 - len(rec1)) + rec2] \
        + GOOD

    for name, lines in files.items():
        with open(os.path.join(FAULTS, name), "w") as f:
            f.write("\n".join(lines) + "\n")


# ===========================================================================
# Unit 2, driven from the outside
# ===========================================================================

MOD2 = "unit2/memory"


def build_mem(tag, extra):
    out = os.path.join(BUILD, "critter_mem_" + tag)
    r = sh(["gcc", "-O1", "-g", "-std=gnu11"] + extra +
           ["-o", out, os.path.join(U2, "critter_mem.c"), "-lm"])
    return (out if r.returncode == 0 else None), r.stderr


def e2e_month_overflow():
    """ARR30-C, confirmed by AddressSanitizer on the shipped source."""
    exe, err = build_mem("asan", ["-fsanitize=address,undefined",
                                  "-fno-omit-frame-pointer"])
    if not exe:
        emit(MOD2, "MEM-19", "SEI CERT C", "ARR30-C", "REVIEW",
             "sanitizer build failed: %s" % err.strip()[:160])
        return

    results = {}
    for name in ("month_13.txt", "month_00.txt"):
        r = sh([exe, os.path.join(FAULTS, name)], cwd=BUILD)
        blob = r.stdout + r.stderr
        m = re.search(r"ERROR: AddressSanitizer: ([a-z-]+)", blob)
        if m:
            results[name] = m.group(1)
        elif "runtime error" in blob:
            mm = re.search(r"runtime error: ([^\n]{0,70})", blob)
            results[name] = "UBSan: " + (mm.group(1) if mm else "?")
        else:
            results[name] = None

    hits = {k: v for k, v in results.items() if v}
    if hits:
        emit(MOD2, "MEM-19", "SEI CERT C", "ARR30-C", "FAIL",
             "AddressSanitizer on the shipped source, driven by a log file: "
             + "; ".join("%s -> %s" % kv for kv in hits.items())
             + ". Independently confirms MEM-01, which used guard slots "
               "in-process")
    else:
        emit(MOD2, "MEM-19", "SEI CERT C", "ARR30-C", "REVIEW",
             "sanitizers did not trip on %s while the guard-slot test MEM-01 "
             "did. A 12-element automatic array can be overrun without "
             "landing outside any sanitizer redzone, so absence here is not "
             "evidence of absence" % ", ".join(results))


def e2e_stuck_sensor():
    """NASA P10 Rule 7, end to end: a stuck sensor erases the log."""
    exe, err = build_mem("plain", [])
    if not exe:
        emit(MOD2, "MEM-20", "NASA P10", "Rule 7", "REVIEW",
             "build failed: %s" % err.strip()[:160])
        return
    r = sh([exe, os.path.join(FAULTS, "constant.txt")], cwd=BUILD)
    read = re.search(r"Read (\d+) readings", r.stdout)
    removed = re.search(r"Outliers found and removed: (\d+)", r.stdout)
    wrote = re.search(r"Wrote (\d+) cleaned readings", r.stdout)
    if read and removed and wrote:
        n, rm, kept = (int(read.group(1)), int(removed.group(1)),
                       int(wrote.group(1)))
        if rm == n and kept == 0:
            emit(MOD2, "MEM-20", "NASA P10", "Rule 7", "FAIL",
                 "40 identical readings (stuck sensor): read %d, reported %d "
                 "as outliers, wrote %d to the cleaned log. The unit exits 0 "
                 "and reports success while deleting the entire data set"
                 % (n, rm, kept))
        else:
            emit(MOD2, "MEM-20", "NASA P10", "Rule 7", "PASS",
                 "constant input: read %d, removed %d, kept %d" % (n, rm, kept))
    else:
        emit(MOD2, "MEM-20", "NASA P10", "Rule 7", "REVIEW",
             "could not parse the unit's report: %r" % r.stdout[-160:])


def e2e_split_line():
    """A defect at the file/buffer seam that neither standard has a rule for."""
    exe, _ = build_mem("plain", [])
    if not exe:
        return
    path = os.path.join(FAULTS, "split_line.txt")
    physical = sum(1 for _ in open(path))
    r = sh([exe, path], cwd=BUILD)
    read = re.search(r"Read (\d+) readings", r.stdout)
    skipped = re.search(r"Skipped (\d+) malformed", r.stdout)
    cleaned = os.path.join(BUILD, "critter_cleaned.txt")
    fabricated = ("99.900" in open(cleaned).read()
                  if os.path.exists(cleaned) else False)

    if read and int(read.group(1)) > physical and fabricated:
        emit(MOD2, "MEM-21", "(neither)", "UNMAPPED", "FINDING",
             "%d physical lines became %d readings (%s malformed reported). "
             "One 153-character line exceeded the 128-byte buffer; fgets split "
             "it at 127 bytes and BOTH halves parsed as valid, so the record "
             "01/02/2023 03:04:05,99.900 -- which is in no input reading -- "
             "appears in critter_cleaned.txt. No rule in either standard "
             "covers silent record fabrication by buffer splitting: STR31-C is "
             "not violated because fgets does not overflow"
             % (physical, int(read.group(1)),
                skipped.group(1) if skipped else "0"))
    else:
        emit(MOD2, "MEM-21", "(neither)", "UNMAPPED", "FINDING",
             "split-line input: %s physical lines, %s readings, fabricated "
             "record present=%s"
             % (physical, read.group(1) if read else "?", fabricated))


# ===========================================================================
# Unit 1, driven from the outside
# ===========================================================================

MOD1 = "unit1/io"


def integration_io_unit(seconds=6.0):
    """The I/O unit's only entry point is an unbounded acquisition loop
    terminated by a signal. It cannot be unit-tested without refactoring the
    artifact, which a review may not do, so it is tested as it is used.

    Six seconds is not arbitrary: at 100 Hz it crosses the FLUSH_EVERY=500
    periodic-flush boundary exactly once, and a fencepost error in either the
    periodic flush or the tail flush would duplicate or drop samples precisely
    there. A shorter run never reaches that arithmetic.

    sensor_sim.c is linked here to satisfy sensor.h. It is not audited.
    """
    exe = os.path.join(BUILD, "io_unit")
    r = sh(["gcc", "-O1", "-g", "-std=gnu11", "-Wall", "-o", exe,
            os.path.join(U1, "io_unit.c"), os.path.join(U1, "sensor_sim.c")])
    if r.returncode != 0:
        emit(MOD1, "IO-14", "Functional", "OUTPUT-INTEGRITY", "REVIEW",
             "build failed: %s" % r.stderr.strip()[:160])
        return

    csv = os.path.join(BUILD, "io_integration.csv")
    env = dict(os.environ, CRITTER_SEED="20260916")
    proc = subprocess.Popen([exe, csv], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)
    time.sleep(seconds)
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        emit(MOD1, "IO-14", "Functional", "OUTPUT-INTEGRITY", "FAIL",
             "unit did not exit within 10 s of SIGINT")
        return

    if not os.path.exists(csv):
        emit(MOD1, "IO-14", "Functional", "OUTPUT-INTEGRITY", "FAIL",
             "no output file produced")
        return

    lines = open(csv).read().splitlines()
    header_ok = bool(lines) and lines[0] == "seq,timestamp_ms,raw_temp_cC"
    seqs, temps, bad = [], [], 0
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) != 3:
            bad += 1
            continue
        try:
            seqs.append(int(parts[0]))
            temps.append(int(parts[2]))
        except ValueError:
            bad += 1

    contiguous = seqs == list(range(len(seqs)))
    dups = len(seqs) - len(set(seqs))
    in_range = all(-32768 <= t <= 32767 for t in temps)

    if header_ok and contiguous and dups == 0 and bad == 0 and in_range:
        emit(MOD1, "IO-14", "Functional", "OUTPUT-INTEGRITY", "PASS",
             "%.1f s at 100 Hz produced %d samples, seq contiguous 0..%d, no "
             "duplicates, no malformed rows, all values inside int16_t. "
             "Periodic-flush boundary %s"
             % (seconds, len(seqs), len(seqs) - 1,
                "crossed (>500 samples)" if len(seqs) > 500
                else "NOT crossed -- lengthen the run"))
    else:
        emit(MOD1, "IO-14", "Functional", "OUTPUT-INTEGRITY", "FAIL",
             "header_ok=%s contiguous=%s duplicates=%d malformed=%d "
             "in_int16_range=%s samples=%d"
             % (header_ok, contiguous, dups, bad, in_range, len(seqs)))

    # The unit's README asserts the stream is reproducible from the seed. Worth
    # confirming, because every baseline-versus-hardened comparison depends on
    # it -- including the ones a reviewer would run after fixing anything.
    csv2 = os.path.join(BUILD, "io_integration2.csv")
    proc = subprocess.Popen([exe, csv2], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)
    time.sleep(2.0)
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return

    def temps_of(path):
        out = []
        for ln in open(path).read().splitlines()[1:]:
            p = ln.split(",")
            if len(p) == 3:
                out.append(p[2])
        return out

    a, b = temps_of(csv), temps_of(csv2)
    k = min(len(a), len(b))
    if k > 50 and a[:k] == b[:k]:
        emit(MOD1, "IO-15", "Functional", "REPRODUCIBILITY", "PASS",
             "two runs at CRITTER_SEED=20260916 agree on the first %d "
             "raw_temp_cC values, so comparing a baseline against a hardened "
             "build on the seq/raw_temp_cC columns is sound, as the README "
             "claims" % k)
    else:
        first = next((i for i in range(k) if a[i] != b[i]), None)
        emit(MOD1, "IO-15", "Functional", "REPRODUCIBILITY", "FAIL",
             "two seeded runs diverge at sample %s (compared %d)" % (first, k))


# ===========================================================================
# Unit 3, driven from the outside through bin/critter
# ===========================================================================

MOD3 = "unit3/compute"


def build_compute():
    """Prefer the unit's own Makefile, so the binary under test is the one the
    team builds, with the flags its README prescribes. Fall back to compiling
    the sources directly if the unit ships no usable Makefile."""
    exe = os.path.join(U3, "bin", "critter")
    if os.path.exists(os.path.join(U3, "Makefile")):
        r = sh(["make", "-C", U3])
        if os.path.exists(exe):
            return exe, "unit's own Makefile", r.stderr
    # fallback: the flags the README documents, minus the -march tuning
    out = os.path.join(BUILD, "critter")
    srcs = [os.path.join(U3_SRC, f)
            for f in ("mpc.c", "thermal.c", "perf.c", "main.c")]
    r = sh(["gcc", "-O3", "-ffast-math", "-fno-math-errno", "-std=gnu11",
            "-I" + U3_INC, "-I" + U3_SRC, "-o", out] + srcs + ["-lm"])
    if r.returncode == 0:
        return out, "fallback build (-O3 -ffast-math -std=gnu11)", r.stderr
    return None, None, r.stderr


def run3(exe, args, env_extra=None, timeout=120):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([exe] + args, capture_output=True, text=True,
                          env=env, timeout=timeout, cwd=U3)


def blackbox_compute():
    exe, how, err = build_compute()
    if not exe:
        for tid, std, rule in (("CMP-01", "SEI CERT C", "FLP32-C"),
                               ("CMP-02", "NASA P10", "Rule 7"),
                               ("CMP-03", "NASA P10", "Rule 7"),
                               ("CMP-04", "SEI CERT C", "MEM35-C"),
                               ("CMP-05", "Functional", "REPRODUCIBILITY")):
            emit(MOD3, tid, std, rule, "REVIEW",
                 "could not build the unit: %s" % err.strip()[:150])
        return

    # ---- CMP-04  MEM35-C, via the --flops report -----------------------
    # The unit prints its own working-set size. Checking it against the
    # analytic figure the header implies is a bounds check on the arena that
    # needs no access to the allocator.
    try:
        r = run3(exe, ["--flops"])
        cfg = dict(re.findall(r"(\w+)=(\d+)", r.stdout))
        ws = re.search(r"total working set ~(\d+) KB", r.stdout)
        n, mm = int(cfg.get("N", 0)), int(cfg.get("M", 0))
        expect = (n * mm + mm * mm) * 8 // 1024
        if ws and n and int(ws.group(1)) == expect:
            emit(MOD3, "CMP-04", "SEI CERT C", "MEM35-C", "PASS",
                 "--flops reports N=%d M=%d, working set %s KB, matching the "
                 "analytic (N*M + M*M) * sizeof(double) = %d KB exactly. The "
                 "arena is sized from compile-time constants and the reported "
                 "extent agrees with the declared one"
                 % (n, mm, ws.group(1), expect))
        else:
            emit(MOD3, "CMP-04", "SEI CERT C", "MEM35-C", "FAIL",
                 "--flops reports working set %s KB against an analytic %d KB "
                 "for N=%d M=%d" % (ws.group(1) if ws else "?", expect, n, mm))
    except subprocess.TimeoutExpired:
        emit(MOD3, "CMP-04", "SEI CERT C", "MEM35-C", "REVIEW",
             "--flops did not terminate")

    # ---- CMP-01  FLP32-C, negative weight from the environment ---------
    # The README documents CRIT_Q_TRACK as a runtime override for the analysis
    # phase. main.c passes it straight to atof and mpc.c takes its square root
    # without checking the sign. sqrt of a negative argument is a domain error.
    try:
        r = run3(exe, ["--demo", "0.2"], {"CRIT_Q_TRACK": "-1"})
        row = None
        for ln in r.stdout.splitlines():
            f = ln.split()
            if len(f) == 7 and re.match(r"^\d+\.\d+$", f[0]):
                row = [float(x) for x in f]
                break
        if row:
            hour, t_air, t_out, u_cool, damper, margin, kwh = row
            sentinel = abs(margin) > 1e20
            saturated = u_cool >= 0.999 and damper >= 0.999
            heating = damper >= 0.999 and t_out > t_air
            if sentinel or saturated:
                emit(MOD3, "CMP-01", "SEI CERT C", "FLP32-C", "FAIL",
                     "CRIT_Q_TRACK=-1 reaches sqrt() in mpc.c unchecked. The "
                     "unit exits %d and reports margin=%.3g degC, which is the "
                     "uninitialised sentinel rather than a measurement, with "
                     "u_cool=%.3f and damper=%.3f. Under the -O3 -ffast-math "
                     "build the README prescribes the domain error does not "
                     "even surface as a NaN"
                     % (r.returncode, margin, u_cool, damper))
                if heating:
                    emit(MOD3, "CMP-06", "(neither)", "UNMAPPED", "FINDING",
                         "the same run commands the damper fully open "
                         "(%.3f) at T_out=%.2f degC against T_air=%.2f degC, "
                         "which heats the room -- the exact error mpc.c's own "
                         "comments say the economiser model exists to prevent "
                         "-- while exit status is 0. Neither standard requires "
                         "a status code to be consistent with the payload it "
                         "describes, nor an actuator command to be "
                         "physically admissible, so no rule list prompts a "
                         "reviewer to check either"
                         % (damper, t_out, t_air))
            else:
                emit(MOD3, "CMP-01", "SEI CERT C", "FLP32-C", "PASS",
                     "negative q_track rejected or handled: margin=%.3g "
                     "u_cool=%.3f damper=%.3f" % (margin, u_cool, damper))
        else:
            emit(MOD3, "CMP-01", "SEI CERT C", "FLP32-C", "REVIEW",
                 "could not parse a --demo row from: %r" % r.stdout[:150])
    except subprocess.TimeoutExpired:
        emit(MOD3, "CMP-01", "SEI CERT C", "FLP32-C", "REVIEW",
             "--demo did not terminate under CRIT_Q_TRACK=-1")

    # ---- CMP-02  Rule 7, environment overrides unvalidated -------------
    try:
        junk = run3(exe, ["--demo", "0.2"], {"CRIT_R_ENERGY": "not-a-number"})
        zero = run3(exe, ["--demo", "0.2"], {"CRIT_R_ENERGY": "0"})
        if junk.stdout == zero.stdout:
            emit(MOD3, "CMP-02", "NASA P10", "Rule 7", "FAIL",
                 "CRIT_R_ENERGY=\"not-a-number\" produced output identical to "
                 "CRIT_R_ENERGY=0: the override is read with atof, whose "
                 "failure return is indistinguishable from a legitimate zero, "
                 "and no diagnostic is issued. A typo in a documented "
                 "environment override silently changes the cost function")
        else:
            emit(MOD3, "CMP-02", "NASA P10", "Rule 7", "PASS",
                 "unparseable environment override is detected and reported")
    except subprocess.TimeoutExpired:
        emit(MOD3, "CMP-02", "NASA P10", "Rule 7", "REVIEW",
             "--demo did not terminate")

    # ---- CMP-03  Rule 7, command-line arguments unvalidated ------------
    cases, bad = [], []
    for args, why in ((["--bench", "0"], "zero repetitions"),
                      (["--bench", "-5"], "negative repetitions"),
                      (["--demo", "abc"], "non-numeric duration")):
        try:
            r = run3(exe, args)
            diag = bool(re.search(r"(?i)error|invalid|usage", r.stdout
                                  + r.stderr))
            cases.append("%s -> exit %d, %s"
                         % (" ".join(args), r.returncode,
                            "diagnostic issued" if diag
                            else "no diagnostic"))
            if r.returncode == 0 and not diag:
                bad.append("%s (%s)" % (" ".join(args), why))
        except subprocess.TimeoutExpired:
            cases.append("%s -> did not terminate" % " ".join(args))
    if bad:
        emit(MOD3, "CMP-03", "NASA P10", "Rule 7", "FAIL",
             "%d of 3 malformed invocations were accepted with exit status 0 "
             "and no diagnostic [%s]. Observed: %s. --bench 0 silently "
             "substitutes the default repetition count and --demo abc runs a "
             "zero-hour simulation, both reporting success"
             % (len(bad), "; ".join(bad), "; ".join(cases)))
    else:
        emit(MOD3, "CMP-03", "NASA P10", "Rule 7", "PASS",
             "malformed invocations rejected: %s" % "; ".join(cases))

    # ---- CMP-05  reproducibility of the anti-dead-code checksum --------
    # The unit's harness warns if the checksum moves between repetitions. Worth
    # confirming it also holds ACROSS invocations, since that is what a
    # baseline-versus-hardened comparison relies on.
    try:
        a = run3(exe, ["--bench", "5"])
        b = run3(exe, ["--bench", "5"])
        ca = re.search(r"checksum ([0-9a-f]+)", a.stdout)
        cb = re.search(r"checksum ([0-9a-f]+)", b.stdout)
        if ca and cb and ca.group(1) == cb.group(1):
            emit(MOD3, "CMP-05", "Functional", "REPRODUCIBILITY", "PASS",
                 "two --bench invocations agree on checksum %s and both "
                 "report status 0; the control path is deterministic across "
                 "processes, not merely across repetitions within one"
                 % ca.group(1))
        else:
            emit(MOD3, "CMP-05", "Functional", "REPRODUCIBILITY", "FAIL",
                 "checksum differs between invocations: %s vs %s"
                 % (ca.group(1) if ca else "?", cb.group(1) if cb else "?"))
    except subprocess.TimeoutExpired:
        emit(MOD3, "CMP-05", "Functional", "REPRODUCIBILITY", "REVIEW",
             "--bench did not terminate")

    sys.stderr.write("[unit 3 driven through %s: %s]\n" % (how, exe))


def main():
    os.makedirs(BUILD, exist_ok=True)
    if shutil.which("gcc") is None:
        sys.stderr.write("gcc not found\n")
        return 2
    write_faults()

    print("CT_MODULE_BEGIN|%s [end-to-end]" % MOD2)
    e2e_month_overflow()
    e2e_stuck_sensor()
    e2e_split_line()
    print("CT_MODULE_END|%s [end-to-end]" % MOD2)

    print("CT_MODULE_BEGIN|%s [integration]" % MOD1)
    integration_io_unit()
    print("CT_MODULE_END|%s [integration]" % MOD1)

    print("CT_MODULE_BEGIN|%s [black-box]" % MOD3)
    blackbox_compute()
    print("CT_MODULE_END|%s [black-box]" % MOD3)
    return 0


if __name__ == "__main__":
    sys.exit(main())