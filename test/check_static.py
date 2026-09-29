#!/usr/bin/env python3
"""
check_static.py -- structural conformance checks for the three Critter units.

SCOPE: THREE CARDS, ONE PER UNIT
--------------------------------
Each unit is delivered as one runnable program, so each unit is scored on one
card and never merged with another:

    Card A   unit 1, I/O          io_unit.c
    Card B   unit 2, memory       critter_mem.c
    Card C   unit 3, compute      the unit's sources, pooled

sensor_sim.c is NOT scored. It sits behind the sensor.h hardware boundary and
the unit's own README designates it the to-be-replaced part: it is a stand-in
for a driver that does not exist yet, so a finding in it would be discharged by
deleting the file rather than by fixing the I/O unit. It is still linked when
the I/O unit is built and run, because io_unit.c cannot execute without an
implementation of sensor.h -- built, not reviewed.

Unit 3 is pooled rather than split per file. It ships as one binary with a
documented command-line interface, and that is the level at which the team runs
it, so mpc.c, thermal.c, perf.c and main.c are audited together and carry one
verdict per rule. Splitting them would produce four scores for a thing that has
one entry point.

WHY A SEPARATE TOOL FROM THE RUNTIME TESTS
------------------------------------------
Several rules in both standards have no runtime signature at all. NASA P10
Rule 2 is violated when a checking tool *cannot prove* a loop bound, which is a
property of the source text and cannot be observed by executing anything.
Rule 4 is function length, Rule 5 is assertion density, Rule 9 forbids function
pointers whether or not they misbehave. No input makes a missing assertion
happen.

OUTPUT
------
One line per verdict, the same format the C harness and check_dynamic.py emit:

    CT|<module>|<test_id>|<standard>|<rule>|PASS|FAIL|REVIEW|NA|<evidence>

CONSERVATISM, AND WHERE IT IS NOT SAFE
--------------------------------------
For Rule 2 the standard says a bound a tool cannot prove statically is
violated, so a conservative classifier behaves as the rule intends. For
ERR33-C the heuristic cannot be conservative in the safe direction: deciding
whether a stored return value is genuinely checked needs dataflow. Call sites
are therefore classified DISCARDED (a definite violation, no dataflow needed),
ASSIGNED-UNTESTED (a likely violation a human must confirm), or CHECKED, and
every site is printed with its line number. The size of the residue is itself a
measurement.

ERR33_TABLE is transcribed from the table of standard library functions in
ERR33-C, 2016 edition. Note what is deliberately absent: plain printf(),
puts(), qsort(), rewind() and free() are not in that table, so calls to them
are not flagged. Nor are POSIX functions such as nanosleep(), clock_gettime()
and ioctl(). Over-flagging would inflate the finding count and make the
scorecard useless.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _dir(var, *default):
    """Source locations come from the environment, exported by the Makefile, so
    the Makefile and both checkers cannot disagree about where a unit lives."""
    v = os.environ.get(var)
    return os.path.abspath(v) if v else os.path.join(ROOT, *default)


U1 = _dir("U1", "io-subsystem")
U2 = _dir("U2", "memory", "src")
U3 = _dir("U3", "compute-intensive")
U3_INC = (os.path.abspath(os.environ["U3_INC"]) if os.environ.get("U3_INC")
          else os.path.join(U3, "include"))

# The compute unit's README places its sources under src/; the repository has
# them at the top level. Accept either rather than demanding one.
U3_SRC = os.path.join(U3, "src") if os.path.isdir(os.path.join(U3, "src")) \
    else U3

INCLUDE_DIRS = [U1, U2, U3_SRC, U3, U3_INC]

# --- transcribed from ERR33-C's function table, 2016 edition ---------------
ERR33_TABLE = {
    "aligned_alloc", "calloc", "fclose", "fflush", "fgets", "fopen",
    "fprintf", "fputs", "fread", "fscanf", "fseek", "ftell", "fwrite",
    "getenv", "malloc", "realloc", "remove", "rename", "signal",
    "snprintf", "sprintf", "sscanf", "strtod", "strtol", "strtoul",
    "tmpfile", "vfprintf",
}

ALLOCATORS = {"malloc", "calloc", "realloc", "posix_memalign",
              "aligned_alloc", "strdup"}

# Library calls that take a function pointer argument (NASA P10 Rule 9).
FN_PTR_SINKS = {"qsort", "bsearch", "atexit", "at_quick_exit", "signal",
                "thrd_create"}


def emit(module, tid, standard, rule, verdict, evidence):
    print("CT|%s|%s|%s|%s|%s|%s"
          % (module, tid, standard, rule, verdict,
             " ".join(str(evidence).split())))
    sys.stdout.flush()


def base(p):
    return os.path.basename(p)


def as_list(srcs):
    return [srcs] if isinstance(srcs, str) else list(srcs)


# ---------------------------------------------------------------------------
# Source preparation
# ---------------------------------------------------------------------------

def strip_noise(src_text):
    """Blank out comments and string/char literals, preserving line numbers so
    every finding can still be reported against the original line."""
    out = []
    i, n = 0, len(src_text)
    while i < n:
        c = src_text[i]
        if c == "/" and i + 1 < n and src_text[i + 1] == "*":
            j = src_text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " "
                               for ch in src_text[i:j]))
            i = j
        elif c == "/" and i + 1 < n and src_text[i + 1] == "/":
            j = src_text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c in "\"'":
            q, j = c, i + 1
            while j < n and src_text[j] != q:
                j += 2 if src_text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append("".join(ch if ch == "\n" else " "
                               for ch in src_text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def clean_of(path):
    return strip_noise(open(path).read())


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def find_functions(clean):
    """Return [(name, first_line, last_line, body, off0, off1)] by brace
    matching. A function definition is a depth-0 '{' whose preceding
    non-space character is ')', with an identifier before that parenthesis
    group. This does not mistake struct or array initialisers for functions."""
    funcs = []
    depth = 0
    i, n = 0, len(clean)
    while i < n:
        c = clean[i]
        if c == "{":
            if depth == 0:
                k = i - 1
                while k >= 0 and clean[k].isspace():
                    k -= 1
                if k >= 0 and clean[k] == ")":
                    d = 0
                    while k >= 0:
                        if clean[k] == ")":
                            d += 1
                        elif clean[k] == "(":
                            d -= 1
                            if d == 0:
                                break
                        k -= 1
                    m = re.search(r"([A-Za-z_]\w*)\s*$", clean[:k])
                    if m and m.group(1) not in ("if", "for", "while",
                                                "switch", "sizeof", "return"):
                        d2, j = 0, i
                        while j < n:
                            if clean[j] == "{":
                                d2 += 1
                            elif clean[j] == "}":
                                d2 -= 1
                                if d2 == 0:
                                    break
                            j += 1
                        funcs.append((m.group(1), line_of(clean, m.start(1)),
                                      line_of(clean, j), clean[i:j + 1], i, j))
                        depth = 0
                        i = j + 1
                        continue
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return funcs


def const_names(paths):
    """Identifiers expanding to an integer constant, so a loop bound that
    mentions one is statically provable."""
    names = set()
    for p in paths:
        if not os.path.exists(p):
            continue
        txt = clean_of(p)
        for m in re.finditer(r"#\s*define\s+([A-Z_][A-Z0-9_]*)\s+([^\n]+)",
                             txt):
            body = m.group(2).strip()
            # Accept integer-constant suffixes: PROBE_ITERS is 2000000ull,
            # and rejecting the suffix would wrongly call its loop unprovable.
            if re.fullmatch(r"[\s\d\+\-\*/\(\)A-Z_0-9]+[uUlL]*", body) \
               or re.fullmatch(r"\s*\d+[uUlL]*\s*", body):
                names.add(m.group(1))
        for m in re.finditer(r"enum\s*\{([^}]*)\}", txt):
            for part in m.group(1).split(","):
                mm = re.match(r"\s*([A-Za-z_]\w*)", part)
                if mm:
                    names.add(mm.group(1))
    return names


# ---------------------------------------------------------------------------
# Compilation probes
# ---------------------------------------------------------------------------

def compile_probe(src, std, analyzer=False, extra=None):
    incs = [os.path.dirname(src)] + [d for d in INCLUDE_DIRS
                                     if d != os.path.dirname(src)]
    cmd = ["gcc", "-O1", "-Wall", "-Wextra", "-Wpedantic", "-std=" + std]
    if analyzer:
        cmd.insert(1, "-fanalyzer")
    if extra:
        cmd += list(extra)
    cmd += ["-I" + d for d in incs] + ["-c", src, "-o", "/dev/null"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stderr


# GCC quotes identifiers with U+2018/U+2019, not ASCII apostrophes.
Q = "['\u2018\u2019]"


def rule10_pedantic(module, tid, srcs, std="c11", own_flags=None):
    """NASA P10 Rule 10: all code must compile, at the most pedantic setting
    available, with zero warnings, and must pass a strong static analyzer with
    zero warnings. Both halves are checked.

    own_flags, when given, additionally builds with the flags the unit's own
    documentation prescribes, so the evidence can report the contrast between
    the build the team runs and the build the rule asks for."""
    errs = warns = 0
    kinds, first = set(), None
    for s in as_list(srcs):
        _, err = compile_probe(s, std)
        e = len(re.findall(r": error:", err))
        w = len(re.findall(r": warning:", err))
        errs += e
        warns += w
        kinds |= set(re.findall(r"\[-W([a-z0-9-]+)\]", err))
        if first is None:
            m = re.search(r"([^\n]*: (?:error|warning): [^\n]*)", err)
            if m:
                first = base(m.group(1))

    ana = set()
    for s in as_list(srcs):
        _, aerr = compile_probe(s, "gnu11", analyzer=True)
        ana |= set(re.findall(r"\[-Wanalyzer-([a-z-]+)\]", aerr))

    own = ""
    if own_flags:
        oe = ow = 0
        for s in as_list(srcs):
            _, err = compile_probe(s, "gnu11", extra=own_flags)
            oe += len(re.findall(r": error:", err))
            ow += len(re.findall(r": warning:", err))
        own = (" For contrast, the same sources under the flags the unit's own "
               "README prescribes (%s) give %d error(s) and %d warning(s): the "
               "build the team runs is clean, and the build the rule asks for "
               "is not." % (" ".join(own_flags), oe, ow))

    if errs:
        emit(module, tid, "NASA P10", "Rule 10", "FAIL",
             "does not COMPILE under -std=%s -Wall -Wextra -Wpedantic: %d "
             "error(s), %d warning(s). First: %s.%s"
             % (std, errs, warns, first or "?", own))
    elif warns or ana:
        bits = []
        if warns:
            bits.append("%d compiler warning(s) %s" % (warns, sorted(kinds)))
        if ana:
            bits.append("gcc -fanalyzer: %s" % sorted(ana))
        emit(module, tid, "NASA P10", "Rule 10", "FAIL",
             "-std=%s: %s.%s" % (std, "; ".join(bits), own))
    else:
        emit(module, tid, "NASA P10", "Rule 10", "PASS",
             "-std=%s -Wall -Wextra -Wpedantic: zero warnings across %d "
             "file(s); gcc -fanalyzer: zero warnings.%s"
             % (std, len(as_list(srcs)), own))


def dcl31_implicit(module, tid, srcs, std="c11"):
    """DCL31-C. Declare identifiers before using them."""
    imp, und = set(), set()
    for s in as_list(srcs):
        _, err = compile_probe(s, std)
        imp |= set(re.findall(
            r"implicit declaration of function %s(\w+)%s" % (Q, Q), err))
        und |= set(re.findall(r"%s(\w+)%s undeclared" % (Q, Q), err))
    if imp or und:
        emit(module, tid, "SEI CERT C", "DCL31-C", "FAIL",
             "-std=%s: implicit declarations %s; undeclared identifiers %s. "
             "The unit relies on identifiers the selected standard does not "
             "declare; it builds only because the dialect it is built with is "
             "a GNU one" % (std, sorted(imp), sorted(und)))
    else:
        emit(module, tid, "SEI CERT C", "DCL31-C", "PASS",
             "-std=%s: every identifier declared before use across %d file(s)"
             % (std, len(as_list(srcs))))


def exp34_analyzer(module, tid, srcs):
    """EXP34-C. Do not dereference null pointers -- via gcc -fanalyzer."""
    hits = []
    for s in as_list(srcs):
        _, err = compile_probe(s, "gnu11", analyzer=True)
        for m in re.finditer(
                r"([^\n]*?):(\d+):\d+: warning: [^\n]*?\[-Wanalyzer-"
                r"((?:possible-)?null-dereference)\]", err):
            hits.append("%s:%s %s" % (base(m.group(1)), m.group(2),
                                      m.group(3)))
    if hits:
        emit(module, tid, "SEI CERT C", "EXP34-C", "FAIL",
             "gcc -fanalyzer reports %d null-dereference path(s): %s"
             % (len(hits), "; ".join(hits)))
    else:
        emit(module, tid, "SEI CERT C", "EXP34-C", "PASS",
             "gcc -fanalyzer reports no null-dereference path across %d file(s)"
             % len(as_list(srcs)))


def sig31_probe(module, tid, src):
    """SIG31-C. Do not access shared objects in signal handlers.

    The rule permits a handler to touch exactly two things: a lock-free atomic,
    or an object of type volatile sig_atomic_t. This probe redeclares the
    handler-shared object as volatile sig_atomic_t in the same translation
    unit. If the unit's own declaration is compatible the probe compiles; if
    not, the compiler reports conflicting types, which is the violation."""
    clean = clean_of(src)
    handlers = re.findall(r"signal\s*\(\s*\w+\s*,\s*([A-Za-z_]\w*)\s*\)", clean)
    if not handlers:
        emit(module, tid, "SEI CERT C", "SIG31-C", "NA",
             "no signal handler is installed in this module")
        return

    shared = []
    for m in re.finditer(r"^\s*((?:volatile\s+)?[A-Za-z_][\w ]*?)\s+"
                         r"([A-Za-z_]\w*)\s*=", clean, re.M):
        decl, name = m.group(1).strip(), m.group(2)
        for h in handlers:
            body = re.search(r"\b%s\s*\([^)]*\)\s*\{(.*?)\n\}" % re.escape(h),
                             clean, re.S)
            if body and re.search(r"\b%s\b" % re.escape(name), body.group(1)):
                shared.append((name, decl, line_of(clean, m.start())))

    if not shared:
        emit(module, tid, "SEI CERT C", "SIG31-C", "PASS",
             "handler(s) %s touch no file-scope object" % handlers)
        return

    name, decl, ln = shared[0]
    probe = os.path.join("/tmp", "sig31_probe.c")
    with open(probe, "w") as f:
        f.write("#include <signal.h>\n")
        f.write('#include "%s"\n' % os.path.abspath(src))
        f.write("extern volatile sig_atomic_t %s;\n" % name)
    r = subprocess.run(["gcc", "-fsyntax-only", "-std=gnu11"]
                       + ["-I" + d for d in
                          [os.path.dirname(os.path.abspath(src))]
                          + INCLUDE_DIRS] + [probe],
                       capture_output=True, text=True)
    if r.returncode != 0 and re.search(r"conflicting|qualifier", r.stderr):
        emit(module, tid, "SEI CERT C", "SIG31-C", "FAIL",
             "'%s' is declared '%s' at line %d and is read both by handler %s "
             "and by the main loop. Redeclaring it 'volatile sig_atomic_t' is "
             "rejected by the compiler, so it is neither a lock-free atomic "
             "nor a volatile sig_atomic_t: the only two types the rule permits"
             % (name, decl, ln, handlers[0]))
    else:
        emit(module, tid, "SEI CERT C", "SIG31-C", "PASS",
             "'%s' is compatible with volatile sig_atomic_t" % name)


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------

def err33_audit(module, tid, srcs):
    discarded, untested, checked = [], [], []
    for src in as_list(srcs):
        b = base(src)
        lines = clean_of(src).split("\n")
        for i, ln in enumerate(lines, 1):
            for m in re.finditer(r"\b(\w+)\s*\(", ln):
                fn = m.group(1)
                if fn not in ERR33_TABLE:
                    continue
                before = ln[:m.start(1)].rstrip()
                if re.search(r"(=|==|!=|<|>|\(|\breturn\b|,|&&|\|\|)$", before):
                    assign = re.search(r"([A-Za-z_]\w*)\s*=\s*$", before)
                    if assign:
                        var = assign.group(1)
                        window = "\n".join(lines[i - 1:i + 10])
                        if re.search(r"(if|while)\s*\([^)]*\b%s\b" % var,
                                     window) \
                           or re.search(r"\b%s\s*(==|!=|<|>)" % var, window):
                            checked.append((b, i, fn))
                        else:
                            untested.append((b, i, fn, var))
                    else:
                        checked.append((b, i, fn))
                else:
                    discarded.append((b, i, fn))

    total = len(discarded) + len(untested) + len(checked)
    if total == 0:
        emit(module, tid, "SEI CERT C", "ERR33-C", "NA",
             "no call to any function in ERR33-C's table")
    elif discarded or untested:
        d = ", ".join("%s:%d %s" % t for t in discarded[:18])
        u = ", ".join("%s:%d %s->%s" % t for t in untested[:10])
        emit(module, tid, "SEI CERT C", "ERR33-C", "FAIL",
             "%d call(s) to ERR33-C-table functions: %d DISCARDED [%s]; %d "
             "ASSIGNED-UNTESTED [%s]; %d checked. The discarded set is a "
             "definite violation needing no dataflow; the assigned-untested "
             "set needs human confirmation"
             % (total, len(discarded), d or "-", len(untested), u or "-",
                len(checked)))
    else:
        emit(module, tid, "SEI CERT C", "ERR33-C", "PASS",
             "all %d call(s) to ERR33-C-table functions have their result "
             "tested" % total)


def rule1_control_flow(module, tid, srcs):
    bad = []
    for src in as_list(srcs):
        b, clean = base(src), clean_of(src)
        for kw in ("goto", "setjmp", "longjmp"):
            for m in re.finditer(r"\b%s\b" % kw, clean):
                bad.append("%s in %s:%d" % (kw, b, line_of(clean, m.start())))
        for name, a, z, body, _o0, _o1 in find_functions(clean):
            if re.search(r"\b%s\s*\(" % re.escape(name), body[1:]):
                bad.append("direct recursion in %s (%s:%d-%d)"
                           % (name, b, a, z))
    if bad:
        emit(module, tid, "NASA P10", "Rule 1", "FAIL",
             "restricted control flow: " + "; ".join(bad))
    else:
        emit(module, tid, "NASA P10", "Rule 1", "PASS",
             "no goto, no setjmp/longjmp, no direct recursion across %d file(s)"
             % len(as_list(srcs)))


def _paren_end(clean, open_pos):
    j, d = open_pos, 0
    while j < len(clean):
        if clean[j] == "(":
            d += 1
        elif clean[j] == ")":
            d -= 1
            if d == 0:
                return j
        j += 1
    return len(clean)


def rule2_loop_bounds(module, tid, srcs, consts):
    """NASA P10 Rule 2, with the tool's own limits made visible.

    Three outcomes per loop, not two:
      provable    bound is an integer literal or a macro/enum constant.
      review      bound is the induction variable of an enclosing loop, so the
                  loop IS bounded, but proving it needs interval reasoning this
                  tool does not do.
      unprovable  bound is a runtime value. No tool can prove it from source.

    Only the third produces FAIL. Reporting the review class as a violation
    would blame the code for the tool's weakness, and a difficulty metric built
    on that measures the tool rather than the artifact.
    """
    provable, review, unprovable = [], [], []

    for src in as_list(srcs):
        b, clean = base(src), clean_of(src)
        funcs = find_functions(clean)

        def induction(pos):
            for _n, _a, _z, body, o0, o1 in funcs:
                if o0 <= pos <= o1:
                    return set(re.findall(
                        r"for\s*\(\s*(?:[A-Za-z_][\w \*]*?\s+)?([A-Za-z_]\w*)"
                        r"\s*=", body))
            return set()

        for m in re.finditer(r"\b(while|for)\s*\(", clean):
            kind = m.group(1)
            close = _paren_end(clean, m.end() - 1)
            inner = clean[m.end():close]
            ln = line_of(clean, m.start())
            if kind == "while":
                expr = inner.strip()
            else:
                parts = inner.split(";")
                cond = parts[1].strip() if len(parts) >= 2 else ""
                cmp_ = re.search(r"(<=|>=|<|>|!=)\s*(.+)$", cond)
                expr = cmp_.group(2).strip() if cmp_ else cond
            shown = " ".join(inner.split())[:44]
            toks = set(re.findall(r"[A-Za-z_]\w*", expr))
            numeric = bool(expr) and bool(
                re.fullmatch(r"[\d\s\+\-\*/\(\)]+", expr))
            rec = (kind, b, ln, shown)
            if numeric or (toks and toks <= consts):
                provable.append(rec)
            elif toks and toks <= (consts | induction(m.start())):
                review.append(rec)
            else:
                unprovable.append(rec)

    total = len(provable) + len(review) + len(unprovable)
    fmt = lambda xs: "; ".join("%s %s:%d '%s'" % t for t in xs[:6])

    if total == 0:
        emit(module, tid, "NASA P10", "Rule 2", "NA", "no loops")
    elif unprovable:
        emit(module, tid, "NASA P10", "Rule 2", "FAIL",
             "%d of %d loops have a bound no tool can prove from the source "
             "(runtime values): %s. %d provable, %d bounded by an enclosing "
             "loop variable. The rule states that a bound a tool cannot prove "
             "statically counts as violated"
             % (len(unprovable), total, fmt(unprovable), len(provable),
                len(review)))
    elif review:
        emit(module, tid, "NASA P10", "Rule 2", "REVIEW",
             "%d of %d loops bounded by compile-time constants; the remaining "
             "%d are bounded by an enclosing loop's induction variable (%s), "
             "a real bound this tool cannot prove. One reviewer pass, not a "
             "code change" % (len(provable), total, len(review), fmt(review)))
    else:
        emit(module, tid, "NASA P10", "Rule 2", "PASS",
             "all %d loops bounded by compile-time constants" % total)


def rule3_dynamic_alloc(module, tid, srcs):
    sites, in_loop = [], []
    for src in as_list(srcs):
        b, clean = base(src), clean_of(src)
        for m in re.finditer(r"\b(%s)\s*\(" % "|".join(ALLOCATORS), clean):
            sites.append("%s:%d %s" % (b, line_of(clean, m.start()),
                                       m.group(1)))
        for name, _a, _z, body, _o0, _o1 in find_functions(clean):
            for m in re.finditer(r"\b(?:while|for)\s*\(", body):
                j, d = m.end() - 1, 0
                while j < len(body):
                    if body[j] == "{":
                        d += 1
                    elif body[j] == "}":
                        d -= 1
                        if d == 0:
                            break
                    j += 1
                seg = body[m.start():j + 1]
                for mm in re.finditer(r"\b(%s)\s*\(" % "|".join(ALLOCATORS),
                                      seg):
                    in_loop.append("%s in %s (%s)" % (mm.group(1), name, b))

    if not sites:
        emit(module, tid, "NASA P10", "Rule 3", "PASS",
             "no dynamic memory allocation anywhere in the unit")
    elif in_loop:
        emit(module, tid, "NASA P10", "Rule 3", "FAIL",
             "%d dynamic allocation site(s) [%s], %d of them inside a loop "
             "body (%s). An allocation reached repeatedly from a loop is "
             "unambiguously after initialization, so no judgement is needed "
             "to call this a violation"
             % (len(sites), ", ".join(sites), len(in_loop),
                ", ".join(in_loop)))
    else:
        emit(module, tid, "NASA P10", "Rule 3", "REVIEW",
             "%d dynamic allocation site(s) [%s], none inside a loop. Rule 3 "
             "forbids allocation AFTER initialization and permits it during, "
             "so the verdict turns on whether each site is reached only at "
             "startup, which no tool can decide. A reviewer must classify each "
             "site; that judgement is the cost this rule imposes"
             % (len(sites), ", ".join(sites)))


def rule4_function_length(module, tid, srcs, limit=60):
    funcs, over = 0, []
    for src in as_list(srcs):
        b = base(src)
        for name, a, z, _body, _o0, _o1 in find_functions(clean_of(src)):
            funcs += 1
            if z - a + 1 > limit:
                over.append(("%s (%s)" % (name, b), z - a + 1))
    if not funcs:
        emit(module, tid, "NASA P10", "Rule 4", "NA", "no function definitions")
    elif over:
        emit(module, tid, "NASA P10", "Rule 4", "FAIL",
             "%d of %d functions exceed %d lines: %s"
             % (len(over), funcs, limit,
                ", ".join("%s=%d" % t
                          for t in sorted(over, key=lambda x: -x[1]))))
    else:
        emit(module, tid, "NASA P10", "Rule 4", "PASS",
             "all %d functions within %d lines" % (funcs, limit))


def rule5_assertions(module, tid, srcs):
    n_assert, n_func = 0, 0
    for src in as_list(srcs):
        clean = clean_of(src)
        n_assert += len(re.findall(r"\bassert\s*\(", clean))
        n_func += len(find_functions(clean))
    if not n_func:
        emit(module, tid, "NASA P10", "Rule 5", "NA", "no function definitions")
        return
    density = n_assert / float(n_func)
    if density < 2.0:
        emit(module, tid, "NASA P10", "Rule 5", "FAIL",
             "%d assert() call(s) across %d functions = %.2f per function; "
             "Rule 5 requires an average of at least 2. Shortfall %d assertions"
             % (n_assert, n_func, density, 2 * n_func - n_assert))
    else:
        emit(module, tid, "NASA P10", "Rule 5", "PASS",
             "%d assertions across %d functions = %.2f per function"
             % (n_assert, n_func, density))


def rule6_scope(module, tid, srcs):
    """Rule 6: declare all data objects at the smallest possible level of
    scope. A file-scope object WITHOUT 'static' has external linkage and is
    visible to every translation unit, which is never the smallest scope."""
    external, statics = [], 0
    for src in as_list(srcs):
        b, clean = base(src), clean_of(src)
        depth_at, d = [], 0
        for ch in clean:
            depth_at.append(d)
            if ch == "{":
                d += 1
            elif ch == "}":
                d -= 1
        spans = [(a, z) for _n, a, z, _b, _o0, _o1 in find_functions(clean)]
        statics += len(re.findall(r"^\s*static\s+(?!\w+\s*\()", clean, re.M))
        for m in re.finditer(r"^(?!\s*(?:static|extern|typedef|const)\b)"
                             r"\s*([A-Za-z_][\w \*]*?)\s+([A-Za-z_]\w*)\s*"
                             r"(?:=|;)", clean, re.M):
            ln = line_of(clean, m.start())
            if depth_at[m.start()] != 0:          # struct/union member
                continue
            if any(a <= ln <= z for a, z in spans):
                continue
            if re.search(r"\)\s*$", m.group(1)):
                continue
            external.append("'%s' (%s) %s:%d"
                            % (m.group(2), m.group(1).strip(), b, ln))
    if external:
        emit(module, tid, "NASA P10", "Rule 6", "FAIL",
             "%d file-scope object(s) with external linkage: %s. Adding "
             "'static' would restrict each to its translation unit at no cost"
             % (len(external), ", ".join(external)))
    else:
        emit(module, tid, "NASA P10", "Rule 6", "PASS",
             "no file-scope object has external linkage; %d file-scope "
             "object(s) declared static, which is the smallest scope that can "
             "still hold state across calls" % statics)


def rule8_preprocessor(module, tid, srcs):
    total, paste, variadic, detail = 0, [], [], []
    for src in as_list(srcs):
        b, clean = base(src), clean_of(src)
        guard = len(re.findall(r"#\s*ifndef\s+\w+_H\b", clean))
        cond = max(len(re.findall(r"^\s*#\s*(if|ifdef|ifndef|elif|else)\b",
                                  clean, re.M)) - guard, 0)
        total += cond
        if cond:
            detail.append("%s has %d" % (b, cond))
        for m in re.finditer(r"#\s*define\s+\w+\([^)]*\)[^\n]*##", clean):
            paste.append("%s:%d" % (b, line_of(clean, m.start())))
        for m in re.finditer(r"#\s*define\s+\w+\([^)]*\.\.\.", clean):
            variadic.append("%s:%d" % (b, line_of(clean, m.start())))

    problems = []
    if total:
        problems.append("%d conditional-compilation directive(s) outside "
                        "include guards (%s); Rule 8 requires these be kept "
                        "to a minimum" % (total, ", ".join(detail)))
    if paste:
        problems.append("token pasting at %s" % ", ".join(paste))
    if variadic:
        problems.append("variadic macro at %s" % ", ".join(variadic))
    if problems:
        emit(module, tid, "NASA P10", "Rule 8", "FAIL", "; ".join(problems))
    else:
        emit(module, tid, "NASA P10", "Rule 8", "PASS",
             "preprocessor use limited to includes and simple object-like "
             "macros; no token pasting, no variadic macros, no conditional "
             "compilation outside include guards")


def rule9_pointers(module, tid, srcs):
    sinks, multi, declarators = [], [], []
    for src in as_list(srcs):
        b, clean = base(src), clean_of(src)
        for m in re.finditer(r"\b(%s)\s*\(" % "|".join(FN_PTR_SINKS), clean):
            sinks.append("%s:%d %s()" % (b, line_of(clean, m.start()),
                                         m.group(1)))
        for m in re.finditer(r"\(\s*\*\s*[A-Za-z_]\w*\s*\)\s*\(", clean):
            declarators.append("%s:%d" % (b, line_of(clean, m.start())))
        for m in re.finditer(r"[A-Za-z_]\w*\s*\*\s*\*\s*[A-Za-z_]\w*", clean):
            multi.append("%s:%d '%s'" % (b, line_of(clean, m.start()),
                                         " ".join(m.group(0).split())))
    problems = []
    if sinks:
        problems.append("%d call(s) passing a function pointer to a library "
                        "routine: %s" % (len(sinks), "; ".join(sinks)))
    if declarators:
        problems.append("function-pointer declarator(s) at %s"
                        % ", ".join(declarators))
    if multi:
        problems.append("%d declaration(s) with two levels of indirection: "
                        "%s; Rule 9 allows at most one level of dereference"
                        % (len(multi), "; ".join(multi)))
    if problems:
        emit(module, tid, "NASA P10", "Rule 9", "FAIL", "; ".join(problems))
    else:
        emit(module, tid, "NASA P10", "Rule 9", "PASS",
             "no function pointers and no declaration with more than one "
             "level of indirection")


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def main():
    io_c = os.path.join(U1, "io_unit.c")
    mem_c = os.path.join(U2, "critter_mem.c")
    u3 = [os.path.join(U3_SRC, f)
          for f in ("mpc.c", "thermal.c", "perf.c", "main.c")]
    hdr = os.path.join(U3_INC, "critter.h")

    missing = [f for f in [io_c, mem_c, hdr] + u3 if not os.path.exists(f)]
    if missing:
        sys.stderr.write("missing source(s):\n  %s\n"
                         "searched U1=%s U2=%s U3=%s (src=%s) U3_INC=%s\n"
                         % ("\n  ".join(missing), U1, U2, U3, U3_SRC, U3_INC))
        return 2

    # ---- CARD A: unit 1, I/O -------------------------------------------
    # sensor_sim.c is linked to build the unit but is NOT audited: it is
    # behind the sensor.h hardware boundary and designated replaceable.
    m = "unit1/io"
    print("CT_MODULE_BEGIN|%s" % m)
    sig31_probe(m, "IO-01", io_c)
    err33_audit(m, "IO-02", io_c)
    exp34_analyzer(m, "IO-03", io_c)
    dcl31_implicit(m, "IO-04", io_c)
    rule1_control_flow(m, "IO-05", io_c)
    rule2_loop_bounds(m, "IO-06", io_c, const_names([io_c]))
    rule3_dynamic_alloc(m, "IO-07", io_c)
    rule4_function_length(m, "IO-08", io_c)
    rule5_assertions(m, "IO-09", io_c)
    rule6_scope(m, "IO-10", io_c)
    rule8_preprocessor(m, "IO-11", io_c)
    rule9_pointers(m, "IO-12", io_c)
    rule10_pedantic(m, "IO-13", io_c)
    print("CT_MODULE_END|%s" % m)

    # ---- CARD B: unit 2, memory ----------------------------------------
    m = "unit2/memory"
    print("CT_MODULE_BEGIN|%s" % m)
    err33_audit(m, "MEM-09", mem_c)
    rule1_control_flow(m, "MEM-10", mem_c)
    rule2_loop_bounds(m, "MEM-11", mem_c, const_names([mem_c]))
    rule3_dynamic_alloc(m, "MEM-12", mem_c)
    rule4_function_length(m, "MEM-13", mem_c)
    rule5_assertions(m, "MEM-14", mem_c)
    rule6_scope(m, "MEM-15", mem_c)
    rule8_preprocessor(m, "MEM-16", mem_c)
    rule9_pointers(m, "MEM-17", mem_c)
    rule10_pedantic(m, "MEM-18", mem_c)
    print("CT_MODULE_END|%s" % m)

    # ---- CARD C: unit 3, compute ---------------------------------------
    # Pooled across the unit's four sources: it ships as one binary with one
    # entry point, and that is the level at which it is run.
    m = "unit3/compute"
    print("CT_MODULE_BEGIN|%s" % m)
    err33_audit(m, "CMP-07", u3)
    exp34_analyzer(m, "CMP-08", u3)
    dcl31_implicit(m, "CMP-09", u3)
    rule1_control_flow(m, "CMP-10", u3)
    rule2_loop_bounds(m, "CMP-11", u3, const_names(u3 + [hdr]))
    rule3_dynamic_alloc(m, "CMP-12", u3)
    rule4_function_length(m, "CMP-13", u3)
    rule5_assertions(m, "CMP-14", u3)
    rule6_scope(m, "CMP-15", u3)
    rule8_preprocessor(m, "CMP-16", u3)
    rule9_pointers(m, "CMP-17", u3)
    # The unit's README prescribes -O3 -ffast-math and explicitly warns against
    # dropping to bare -std=c11. Report both builds so the conflict is visible.
    rule10_pedantic(m, "CMP-18", u3, own_flags=[
        "-O3", "-ffast-math", "-fno-math-errno"])
    print("CT_MODULE_END|%s" % m)

    return 0


if __name__ == "__main__":
    sys.exit(main())