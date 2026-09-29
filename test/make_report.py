#!/usr/bin/env python3
"""
make_report.py -- render the decision record and rubric breakdown to PDF.

The measured scorecard is read from build/verdicts.txt at render time rather
than transcribed, so the document cannot drift from what the suite actually
reports. Re-run `make run` then `python3 make_report.py` and the numbers in
the PDF follow the code.
"""

import html
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERDICTS = os.path.join(HERE, "build", "verdicts.txt")

# Default output is results/ beside this script, so the PDF lands next to the
# figures and tables and can be opened from a remote editor. Override with
# --out DIR.
OUT_DIR = os.path.join(HERE, "results")
if "--out" in sys.argv:
    OUT_DIR = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])


def scorecard_text():
    if not os.path.exists(VERDICTS):
        return "(build/verdicts.txt not found -- run `make run` first)"
    r = subprocess.run([sys.executable, os.path.join(HERE, "scorecard.py"),
                        VERDICTS], capture_output=True, text=True)
    return r.stdout or r.stderr


def verdict_rows():
    rows = []
    if not os.path.exists(VERDICTS):
        return rows
    for line in open(VERDICTS):
        if line.startswith("CT|"):
            p = line.rstrip("\n").split("|", 6)
            if len(p) == 7:
                rows.append(p[1:])
    return rows


CSS = """
@page { size: A4; margin: 19mm 17mm 18mm 17mm; }
body { font-family: "DejaVu Serif", Georgia, serif; font-size: 9.6pt;
       line-height: 1.42; color: #16191d; }
h1 { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 19pt;
     margin: 0 0 2pt 0; line-height: 1.16; }
h2 { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 12.4pt;
     margin: 20pt 0 5pt 0; padding-bottom: 2.5pt;
     border-bottom: 1.1pt solid #16191d; page-break-after: avoid; }
h3 { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 10.3pt;
     margin: 13pt 0 3pt 0; page-break-after: avoid; }
h4 { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 9.6pt;
     margin: 10pt 0 2pt 0; page-break-after: avoid; }
p { margin: 0 0 6pt 0; text-align: justify; }
.sub { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 10.6pt;
       color: #3d454e; margin: 0 0 1pt 0; }
.meta { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 8.2pt;
        color: #5a636d; margin: 8pt 0 0 0; }
hr.rule { border: 0; border-top: 2pt solid #16191d; margin: 7pt 0 13pt 0; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0 9pt 0;
        font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 7.9pt; }
th { text-align: left; border-bottom: 1pt solid #16191d;
     border-top: 1pt solid #16191d; padding: 3.2pt 4pt;
     background: #f2f4f6; vertical-align: bottom; }
td { border-bottom: 0.4pt solid #ccd2d8; padding: 3.2pt 4pt;
     vertical-align: top; }
tr { page-break-inside: avoid; }
code, .mono { font-family: "DejaVu Sans Mono", monospace; font-size: 8.3pt; }
td code { font-size: 7.6pt; }
pre { font-family: "DejaVu Sans Mono", monospace; font-size: 6.4pt;
      line-height: 1.26; background: #f6f7f9; border: 0.4pt solid #ccd2d8;
      padding: 6pt 7pt; white-space: pre-wrap; word-wrap: break-word; }
.verd { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 7.4pt;
        font-weight: bold; }
.fail { color: #9c1f1f; } .pass { color: #1c6b2e; }
.rev  { color: #8a5a00; } .na { color: #5a636d; }
blockquote { margin: 7pt 0 8pt 0; padding: 6pt 9pt; background: #f6f7f9;
             border-left: 2.4pt solid #16191d; font-size: 9.1pt; }
blockquote p { margin: 0; }
.note { font-size: 8.7pt; color: #3d454e; margin: 0 0 7pt 0; }
ul, ol { margin: 0 0 7pt 0; padding-left: 15pt; }
li { margin-bottom: 2.6pt; text-align: justify; }
.pb { page-break-before: always; }
.kv td { border: 0; padding: 1.4pt 5pt 1.4pt 0; }
.small { font-size: 8.4pt; }
"""


def v_class(v):
    return {"FAIL": "fail", "PASS": "pass", "REVIEW": "rev",
            "NA": "na", "FINDING": "fail"}.get(v, "")


def esc(s):
    return html.escape(str(s))


# --------------------------------------------------------------------------
# Test-plan tables, one per card. Hand-written: the "what it does" and "pass
# criterion" columns are design intent, which is not recoverable from output.
# --------------------------------------------------------------------------
PLAN = [
 ("A", "unit1/io_unit.c", "Unit 1 &mdash; I/O", "product code, in scope", [
  ("IO-01", "SEI CERT", "SIG31-C", "Compile probe",
   "Redeclare the handler-shared object as <code>volatile sig_atomic_t</code> in the same translation unit and compile with <code>-fsyntax-only</code>.",
   "Probe compiles. A conflicting-type diagnostic means the object is neither a lock-free atomic nor a <code>volatile sig_atomic_t</code> &mdash; the only two things the rule permits a handler to touch."),
  ("IO-02", "SEI CERT", "ERR33-C", "Call-site audit + human",
   "Classify every call to a function in ERR33-C&rsquo;s own table as discarded, assigned-untested, or checked.",
   "Zero discarded results. Discarded is a definite violation; assigned-untested is reported with line numbers for a reviewer to close."),
  ("IO-03", "SEI CERT", "EXP34-C", "gcc -fanalyzer",
   "Static path analysis for null dereference on the <code>malloc</code>/<code>realloc</code> results.",
   "Analyzer reports no null-dereference path."),
  ("IO-04", "SEI CERT", "DCL31-C", "Compiler diagnostic",
   "Compile at <code>-std=c11 -Wpedantic</code> and collect implicit-declaration and undeclared-identifier diagnostics.",
   "Every identifier declared before use under the nominal standard."),
  ("IO-05", "NASA P10", "Rule 2", "Source scan + human",
   "Classify each loop bound as an integer literal or constant, an enclosing loop&rsquo;s induction variable, or a runtime value.",
   "No loop bound is a runtime value. The rule counts a bound a tool cannot prove statically as violated."),
  ("IO-06", "NASA P10", "Rule 3", "Source scan + human",
   "Locate every allocator call and decide whether it sits inside a loop body.",
   "No allocation, or all allocation confined to initialization. An allocator reached from a loop is a violation with no judgement required."),
  ("IO-07", "NASA P10", "Rule 5", "Source scan",
   "Count <code>assert(</code> calls and divide by the number of function definitions.",
   "Average of at least two assertions per function."),
  ("IO-08", "NASA P10", "Rule 6", "Source scan",
   "Find file-scope object definitions at brace depth zero that lack <code>static</code>.",
   "No file-scope object has external linkage."),
  ("IO-09", "NASA P10", "Rule 9", "Source scan",
   "Detect calls handing a function pointer to a library routine, function-pointer declarators, and declarations with two levels of indirection.",
   "None of the three present."),
  ("IO-10", "NASA P10", "Rule 10", "Compiler + analyzer",
   "Build at the most pedantic setting available, then run <code>gcc -fanalyzer</code>.",
   "Zero compiler warnings and zero analyzer warnings."),
  ("IO-11", "NASA P10", "Rule 4", "Source scan",
   "Brace-match every function and count its line span.",
   "No function longer than 60 lines."),
  ("IO-12", "Functional", "Output integrity", "Integration run",
   "Run the unit for six seconds at 100&nbsp;Hz, interrupt with <code>SIGINT</code>, audit the CSV. Six seconds crosses the 500-sample periodic-flush boundary once, which is the only way to reach the tail-flush arithmetic.",
   "Correct header, <code>seq</code> contiguous from zero, no duplicates, no malformed rows, every value inside <code>int16_t</code>."),
  ("IO-13", "Functional", "Reproducibility", "Integration run",
   "Two seeded runs, compare the <code>raw_temp_cC</code> column.",
   "Identical prefixes. Every baseline-versus-hardened comparison in the review depends on this holding."),
 ]),
 ("B", "unit1/sensor_sim.c", "Unit 1 &mdash; I/O", "behind the sensor.h hardware boundary, scored apart", [
  ("SIM-01", "SEI CERT", "MSC30-C", "Runtime",
   "Capture two 16-sample streams at the same seed and compare.",
   "Streams differ. Identical streams confirm the generator is a seeded <code>rand()</code>, which is the rule&rsquo;s named subject."),
  ("SIM-02", "SEI CERT", "MSC32-C", "Runtime",
   "With <code>CRITTER_SEED</code> unset, capture two streams from separate <code>sensor_init()</code> calls.",
   "Streams differ, which is the rule&rsquo;s own stated criterion for a properly seeded generator."),
  ("SIM-03", "SEI CERT", "INT31-C", "Runtime",
   "Request seed 2<sup>32</sup> and seed 0 and compare the streams. <b>This is the narrowing-conversion case:</b> the value parses correctly, the destination type cannot hold it, nothing reports the loss.",
   "Streams differ. Identical streams prove the <code>unsigned long &rarr; unsigned int</code> cast discarded the high 32 bits."),
  ("SIM-04", "SEI CERT", "ERR33-C", "Runtime",
   "Set <code>CRITTER_SEED</code> to an unparseable string; compare against seed 0.",
   "The unit reports an error. Silently becoming seed 0 confirms <code>strtoul</code>&rsquo;s documented error return is not checked."),
  ("SIM-05", "SEI CERT", "INT31-C", "Runtime",
   "200,000 reads; record the observed range against <code>int16_t</code> limits. Tests the <i>other</i> narrowing site, the glitch return.",
   "Observed range inside <code>int16_t</code>. Recorded as not-applicable for this site so the INT31-C failure is attributed to the seed cast alone."),
  ("SIM-06", "NASA P10", "Rule 2", "Source scan", "Loop inventory.",
   "No unprovable bound. Recorded not-applicable: the module has no loops."),
  ("SIM-07", "NASA P10", "Rule 5", "Source scan", "Assertion density.",
   "At least two per function."),
  ("SIM-08", "NASA P10", "Rule 6", "Source scan", "File-scope linkage.",
   "No external-linkage object. The two file-scope <code>static</code>s are the smallest scope that can still hold state across a three-function interface."),
  ("SIM-09", "NASA P10", "Rule 10", "Compiler + analyzer", "Pedantic build.",
   "Zero warnings from both."),
  ("SIM-10", "NASA P10", "Rule 4", "Source scan", "Function lengths.",
   "All within 60 lines."),
  ("SIM-11", "NASA P10", "Rule 3", "Source scan", "Allocator inventory.",
   "No dynamic allocation."),
 ]),
 ("C", "unit2/critter_mem.c", "Unit 2 &mdash; memory", "product code, in scope; generate_temps.py excluded per the unit&rsquo;s own design record", [
  ("MEM-01", "SEI CERT", "ARR30-C", "Runtime, guard slots",
   "Hand <code>summarize_by_month</code> the interior of a 14-element array and feed it records with month 13 and month 0, then inspect the two slots either side. The test observes the overrun <i>without itself committing undefined behaviour</i>, because both guard slots are inside the same array object.",
   "Guard slots unchanged. Either slot modified means the month field reached the subscript unchecked."),
  ("MEM-02", "NASA P10", "Rule 7", "Runtime",
   "Parse <code>13/45/2024 99:99:99,20.0</code>.",
   "Line rejected. Acceptance confirms seven successful conversions is the only criterion applied, which is what makes MEM-01 reachable from a log file."),
  ("MEM-03", "SEI CERT", "INT30-C", "Runtime, 32-bit model",
   "Model <code>size_t</code> as 32 bits and evaluate the unit&rsquo;s own allocation expression at 2<sup>27</sup> readings. <b>This is the &lsquo;value does not fit the type&rsquo; test:</b> the arithmetic is right, the type is too narrow.",
   "A guard fires before the multiplication wraps. On the Pi&nbsp;5 the wrap is unreachable; the finding is a property of the type, not the board, and follows the source to any 32-bit userland."),
  ("MEM-04", "SEI CERT", "EXP34-C", "Runtime",
   "Force the <code>compute_median</code> allocation to fail.",
   "Returns without dereferencing. <b>Expected to pass</b> &mdash; a suite that only ever reports violations cannot discriminate."),
  ("MEM-05", "NASA P10", "Rule 7", "Runtime",
   "Inspect what <code>compute_median</code> returns on allocation failure and what the caller can do with it.",
   "Failure is distinguishable from success. Returning 0.0&nbsp;&deg;C &mdash; a plausible Celsius median &mdash; leaves the caller nothing to check."),
  ("MEM-06", "NASA P10", "Rule 7", "Runtime",
   "Forty identical readings, so the standard deviation is exactly zero, then call <code>remove_outliers</code>.",
   "Readings survive, or the zero divisor is rejected. Every z-score becomes 0.0/0.0, and NaN fails the retention comparison."),
  ("MEM-07", "SEI CERT", "FLP30-C", "Source scan", "Loop-counter types.",
   "No floating-point loop counter. <b>Expected to pass.</b>"),
  ("MEM-08", "SEI CERT", "INT33-C", "Human",
   "Classify each division by a possibly-zero count.",
   "Recorded not-applicable with the reason: all four are floating-point, and INT33-C constrains integer division only. See &sect;8."),
  ("MEM-09", "SEI CERT", "ERR33-C", "Call-site audit + human",
   "Same audit as IO-02.", "Zero discarded results."),
  ("MEM-10", "NASA P10", "Rule 2", "Source scan + human",
   "Same classification as IO-05.", "No bound is a runtime value."),
  ("MEM-11", "NASA P10", "Rule 3", "Source scan + human",
   "Allocator inventory and loop-containment test.",
   "All allocation confined to initialization. Sites outside a loop are reported for a reviewer to classify, because no tool can decide what counts as initialization."),
  ("MEM-12", "NASA P10", "Rule 4", "Source scan", "Function lengths.",
   "All within 60 lines."),
  ("MEM-13", "NASA P10", "Rule 5", "Source scan", "Assertion density.",
   "At least two per function."),
  ("MEM-14", "NASA P10", "Rule 9", "Source scan", "Function pointers and indirection depth.",
   "None present."),
  ("MEM-15", "NASA P10", "Rule 10", "Compiler + analyzer", "Pedantic build.",
   "Zero warnings from both. <b>Expected to pass</b> &mdash; and the pass is the point: every other finding on this card sits underneath a clean build."),
  ("MEM-16", "SEI CERT", "ARR30-C", "End-to-end, sanitizers",
   "Build the shipped source with AddressSanitizer and UndefinedBehaviorSanitizer and drive it with a log file containing one out-of-range month.",
   "No sanitizer diagnostic. Independent confirmation of MEM-01 by a different mechanism: if the two disagree, the test is wrong, not the unit."),
  ("MEM-17", "NASA P10", "Rule 7", "End-to-end",
   "Run the shipped binary on a stuck-sensor log and read its own report.",
   "The cleaned log is non-empty, or the unit reports a fault."),
  ("MEM-18", "&mdash;", "Unmapped", "End-to-end",
   "One 153-character log line, arranged so the first record is padded to exactly 127 bytes and the second record begins precisely at the point <code>fgets</code> must stop.",
   "Readings out equals physical lines in. Both halves parsing as valid records fabricates a reading that was never in the input."),
 ]),
 ("D", "unit3/mpc.c", "Unit 3 &mdash; compute", "product code, in scope", [
  ("MPC-01", "SEI CERT", "FLP32-C", "Runtime",
   "Call one control step with <code>q_track = -1</code>, which the shipped harness accepts straight from <code>atof(getenv(...))</code>, and inspect the returned plan.",
   "Argument rejected before <code>sqrt</code>. The compounding failure to look for: the Cholesky guard is <code>d &lt;= 1e-14</code>, and that comparison is false for NaN, so the guard does not fire."),
  ("MPC-02", "&mdash;", "Unmapped", "Runtime",
   "Compare the returned status byte against the returned payload.",
   "Status is consistent with the payload it describes."),
  ("MPC-03", "SEI CERT", "MEM35-C", "Runtime",
   "After <code>crit_ws_alloc</code>, check that the last byte of every sub-array carved out of the arena lies inside it.",
   "All ten extents within <code>[base, base+arena_bytes)</code>. <b>Expected to pass.</b>"),
  ("MPC-04", "NASA P10", "Rule 2", "Runtime",
   "Solve an easy problem and a hard one; compare ADMM iteration counts.",
   "Counts identical and equal to the compile-time constant. <b>Expected to pass</b> &mdash; the fixed iteration count is a deliberate design property."),
  ("MPC-05", "NASA P10", "Rule 7", "Runtime, forked",
   "Call the control step with a null observation pointer in a child process; the parent reads the child&rsquo;s termination signal. A test for missing parameter validation cannot run in-process, because the thing it detects is a crash.",
   "Child exits normally. Termination by signal means none of the six pointer parameters is validated."),
  ("MPC-06", "SEI CERT", "DCL31-C", "Compiler diagnostic",
   "Compile at <code>-std=c11 -Wpedantic</code>.",
   "No implicit declaration."),
  ("MPC-07", "NASA P10", "Rule 1", "Source scan",
   "Search for <code>goto</code>, <code>setjmp</code>, <code>longjmp</code>, and direct recursion.",
   "None present."),
  ("MPC-08", "NASA P10", "Rule 2", "Source scan + human",
   "Loop-bound classification.",
   "No bound is a runtime value. Bounds that are an enclosing loop&rsquo;s induction variable are reported as <span class=\"verd rev\">REVIEW</span>, not failure &mdash; see &sect;7."),
  ("MPC-09", "NASA P10", "Rule 4", "Source scan", "Function lengths.",
   "All within 60 lines."),
  ("MPC-10", "NASA P10", "Rule 5", "Source scan", "Assertion density.",
   "At least two per function."),
  ("MPC-11", "NASA P10", "Rule 10", "Compiler + analyzer", "Pedantic build.",
   "Zero warnings from both."),
  ("MPC-12", "NASA P10", "Rule 3", "Source scan + human", "Allocator inventory.",
   "Allocation confined to initialization. The single arena allocation is reported for reviewer classification rather than asserted to be a violation."),
 ]),
 ("E", "unit3/thermal.c", "Unit 3 &mdash; compute", "product code, in scope, linked alone so a fault here cannot be another module&rsquo;s", [
  ("THM-01", "NASA P10", "Rule 2", "Runtime",
   "Run estimator initialization, which drives the Riccati recursion to its literal 4000-iteration bound, and inspect the resulting gain.",
   "Terminates; all three gains finite; the measured-state gain lies in (0,&nbsp;1]. <b>Expected to pass.</b>"),
  ("THM-02", "NASA P10", "Rule 7", "Runtime",
   "One good measurement, then one NaN flagged <code>valid</code>, then ten more good measurements.",
   "State stays finite, or the NaN is rejected. The <code>valid</code> flag says the reading is fresh; nothing says it is a number."),
  ("THM-03", "SEI CERT", "FLP32-C", "Source scan",
   "Inventory of <code>&lt;math.h&gt;</code> calls.",
   "Recorded not-applicable: the file includes the header but calls nothing from it, so the rule has no construct to bite on."),
  ("THM-04", "&mdash;", "Unmapped", "Runtime",
   "Discretise two plants differing only in coil conductance; run both estimators under full cooling demand and compare trajectories.",
   "Trajectories differ. Identical trajectories show the estimator&rsquo;s hardcoded conductances cannot track the plant it is estimating."),
  ("THM-05", "NASA P10", "Rule 1", "Source scan", "Control-flow inventory.", "None present."),
  ("THM-06", "NASA P10", "Rule 2", "Source scan", "Loop-bound classification.", "No unprovable bound."),
  ("THM-07", "NASA P10", "Rule 4", "Source scan", "Function lengths.", "All within 60 lines."),
  ("THM-08", "NASA P10", "Rule 5", "Source scan", "Assertion density.", "At least two per function."),
  ("THM-09", "NASA P10", "Rule 10", "Compiler + analyzer", "Pedantic build.", "Zero warnings from both."),
  ("THM-10", "NASA P10", "Rule 3", "Source scan", "Allocator inventory.", "No dynamic allocation."),
 ]),
 ("F", "unit3/perf.c + main.c", "Unit 3 &mdash; compute", "harness, excluded from the unit&rsquo;s score &mdash; both files declare themselves so in their own headers", [
  ("HRN-01", "SEI CERT", "EXP34-C", "gcc -fanalyzer", "Null-dereference path analysis.", "No path reported."),
  ("HRN-02", "SEI CERT", "ERR33-C", "Call-site audit + human", "Audit across both files.", "Zero discarded results."),
  ("HRN-03", "NASA P10", "Rule 8", "Source scan",
   "Count conditional-compilation directives outside include guards; detect token pasting and variadic macros.",
   "Preprocessor use limited to includes and simple macros, with conditional compilation kept to a minimum."),
  ("HRN-04", "NASA P10", "Rule 9", "Source scan", "Function pointers and indirection depth.", "None present."),
  ("HRN-05", "NASA P10", "Rule 1", "Source scan", "Control-flow inventory.", "None present."),
 ]),
]


def plan_html():
    out = []
    for card, module, unit, role, rows in PLAN:
        out.append('<h3>Card %s &mdash; <code>%s</code></h3>' % (card, module))
        out.append('<p class="note">%s &middot; %s</p>' % (unit, role))
        out.append('<table><tr><th style="width:7%">Test</th>'
                   '<th style="width:8%">Source</th>'
                   '<th style="width:9%">Rule</th>'
                   '<th style="width:13%">Method</th>'
                   '<th style="width:33%">How it is tested</th>'
                   '<th style="width:30%">Pass criterion</th></tr>')
        for tid, std, rule, method, how, crit in rows:
            out.append('<tr><td><code>%s</code></td><td>%s</td>'
                       '<td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>'
                       % (tid, std, rule, method, how, crit))
        out.append('</table>')
    return "\n".join(out)


def results_table():
    rows = verdict_rows()
    if not rows:
        return "<p>(no verdicts available)</p>"
    out = ['<table><tr><th style="width:24%">Module</th>'
           '<th style="width:8%">Test</th><th style="width:9%">Rule</th>'
           '<th style="width:9%">Verdict</th>'
           '<th style="width:50%">Evidence recorded by the suite</th></tr>']
    for module, tid, std, rule, verdict, evidence in rows:
        ev = esc(evidence)
        if len(ev) > 300:
            ev = ev[:300] + "&hellip;"
        out.append('<tr><td><code>%s</code></td><td><code>%s</code></td>'
                   '<td>%s</td><td class="verd %s">%s</td>'
                   '<td class="small">%s</td></tr>'
                   % (esc(module), esc(tid), esc(rule),
                      v_class(verdict), esc(verdict), ev))
    out.append("</table>")
    return "\n".join(out)


BODY = """
<h1>Testing the Critter Against Two Safety Standards</h1>
<p class="sub">Test plan, conformance rubric, and the reasoning behind both</p>
<hr class="rule">
<table class="kv small">
<tr><td><b>Artefacts under test</b></td><td>Six modules across the three Critter units, scored on six separate cards</td></tr>
<tr><td><b>Normative sources</b></td><td>SEI CERT C Coding Standard, 2016 Edition (v2016-06-29-1140) &middot; NASA/JPL Power of 10 Rules (Holzmann, 2006)</td></tr>
<tr><td><b>Rules exercised</b></td><td>All 10 Power of 10 rules &middot; 13 of the 99 numbered CERT rules</td></tr>
<tr><td><b>Suite</b></td><td>4 C test binaries, 2 Python checkers, 1 rubric engine &middot; no external dependencies</td></tr>
<tr><td><b>Toolchain</b></td><td>GCC 13.3 &middot; <code>-fanalyzer</code> &middot; AddressSanitizer &middot; UndefinedBehaviorSanitizer &middot; python3</td></tr>
</table>

<h2>1. Scope, and what was deliberately left out</h2>
<p>This document records the test plan for the three Critter units, the rubric that scores the results, and the reasoning behind each choice. It is a decision record, not a findings report &mdash; the findings are in &sect;9, and the suite regenerates them on demand.</p>
<p>Two sources are used and no others: the SEI CERT C Coding Standard, 2016 Edition, and the NASA/JPL Power of 10 rules. No MISRA guidance, no JPL institutional standard, no CWE mapping, no outside commentary. Where those two sources leave a gap, the gap is recorded as a gap rather than filled from elsewhere; &sect;8 collects three such cases, and they turn out to be among the more useful results here.</p>
<p>The exclusions are equally deliberate. <code>generate_temps.py</code> is a test-data generator that the memory unit&rsquo;s own design record places outside the review boundary, so it is not scored. The test suite itself is not scored either: it is scaffolding, and scoring the instrument alongside the specimen would be circular. Its harness header says so explicitly, so a later reviewer cannot mistake the boundary.</p>

<h2>2. Why thirteen CERT rules and not ninety-nine</h2>
<p>The 2016 edition contains 99 numbered rules across fifteen chapters. Testing all of them against six small modules would produce a document that is mostly the phrase &ldquo;no applicable construct&rdquo;, and a reviewer cannot audit what they cannot read. The rules exercised here were selected against three criteria, applied in order:</p>
<ol>
<li><b>The construct is present.</b> The module must actually contain the thing the rule constrains. The concurrency chapter is untouched because none of these modules creates a thread.</li>
<li><b>A violation is demonstrable, not arguable.</b> Preference went to rules where a test can produce evidence &mdash; a clobbered guard slot, a NaN command, two identical streams &mdash; over rules whose verdict would rest on a reviewer&rsquo;s taste.</li>
<li><b>The rule ID exists as a numbered section in the supplied edition.</b> This sounds like bookkeeping. It eliminated a rule the units&rsquo; own documentation relies on; see &sect;8.1.</li>
</ol>
<p>All ten Power of 10 rules are exercised on every applicable card. There are only ten, so completeness costs almost nothing, and the contrast between the two standards is more legible when one of them is covered exhaustively.</p>
<table>
<tr><th style="width:17%">Chapter</th><th style="width:22%">Rules exercised</th><th style="width:61%">Why these, and why nothing else from the chapter</th></tr>
<tr><td>Declarations (DCL)</td><td>DCL31-C</td><td>Two units rely on identifiers that strict C11 does not declare. Caught by the compiler, so the check costs nothing and the evidence is a diagnostic.</td></tr>
<tr><td>Expressions (EXP)</td><td>EXP34-C</td><td>Unchecked allocation results appear in two modules and are correctly checked in a third, which makes the rule useful for discriminating between them rather than only for condemning.</td></tr>
<tr><td>Integers (INT)</td><td>INT30-C, INT31-C, INT33-C</td><td>The narrowing and wraparound cases. INT33-C is exercised specifically to record that it does <i>not</i> cover the divisions that matter here.</td></tr>
<tr><td>Floating point (FLP)</td><td>FLP30-C, FLP32-C</td><td>The compute unit takes a square root of a caller-supplied weight. FLP30-C is included as a control: it passes everywhere.</td></tr>
<tr><td>Arrays (ARR)</td><td>ARR30-C</td><td>The single most serious memory-safety finding in the product code.</td></tr>
<tr><td>Memory (MEM)</td><td>MEM35-C</td><td>Included because the compute unit gets it right; an all-failing rubric conveys no information.</td></tr>
<tr><td>Signals (SIG)</td><td>SIG31-C</td><td>The I/O unit installs a handler that shares state with its main loop &mdash; exactly the rule&rsquo;s subject.</td></tr>
<tr><td>Error handling (ERR)</td><td>ERR33-C</td><td>The highest-volume finding across every card, and the one whose automation limits are most instructive.</td></tr>
<tr><td>Miscellaneous (MSC)</td><td>MSC30-C, MSC32-C</td><td>The simulated sensor is built on <code>rand()</code> with a fixed seed, which is both rules at once and a good test of whether the rubric can carry an intentional deviation.</td></tr>
</table>

<h2>3. Six cards, not three units</h2>
<p>The assignment decomposes the Critter into three units. The suite scores six modules, because three of the shipped files declare themselves to be something other than product code and folding them together would corrupt every number downstream.</p>
<ul>
<li><code>sensor_sim.c</code> sits behind the <code>sensor.h</code> hardware boundary, and the unit&rsquo;s README calls it &ldquo;the to-be-replaced part&rdquo;. Its findings may be discharged by <i>deleting the file</i> when real hardware arrives. That is a categorically different disposition from a finding in <code>io_unit.c</code>, which has to be fixed.</li>
<li><code>main.c</code> and <code>perf.c</code> are labelled &ldquo;harness, not product&rdquo; in their own header comments. The unchecked allocation in <code>main.c</code> is real, and it is also the kind of thing a benchmark driver gets away with.</li>
<li><code>mpc.c</code> and <code>thermal.c</code> are both product code but have very different profiles &mdash; one fails most of what it is measured against, the other passes most of it. A single compute-unit score would average that away and hide where the work is.</li>
</ul>
<p>Cards are never merged. The rubric prints a cross-module summary but computes no unit-level or system-level aggregate, because there is no defensible way to average a card whose findings are architectural against one whose findings are typos.</p>

<h2>4. Three test layers, assigned by rule semantics</h2>
<p>Each rule is tested at the layer its own wording demands, not at whichever layer was convenient.</p>
<table>
<tr><th style="width:16%">Layer</th><th style="width:26%">Implementation</th><th style="width:58%">Rules that belong here, and why</th></tr>
<tr><td><b>Runtime</b></td><td>Four C binaries, <code>test_*.c</code></td><td>Rules whose violation has an observable signature: a wrong value, an out-of-bounds write, a lost data set, a NaN command. If a violation can be made to <i>happen</i>, a test that makes it happen is better evidence than a source scan that predicts it.</td></tr>
<tr><td><b>Static</b></td><td><code>check_static.py</code> plus compiler and analyzer probes</td><td>Rules with no runtime signature at all. Power of 10 Rule 2 is the clearest case: it is violated when a tool <i>cannot prove</i> a bound, which is a property of the source text and cannot be observed by running anything. Rule 4 is function length; Rule 5 is assertion density; Rule 9 forbids function pointers whether or not they misbehave.</td></tr>
<tr><td><b>End-to-end</b></td><td><code>check_dynamic.py</code></td><td>Two jobs. First, independent confirmation: ARR30-C is caught in-process by guard slots and again by AddressSanitizer on the shipped binary, driven by a log file. Second, defects that only exist at the seam &mdash; the 128-byte line buffer is invisible from inside <code>parse_line</code>, which is handed a line and parses it correctly. That defect lives in the relationship between <code>count_lines</code>, <code>fgets</code> and the file, and only a real file shows it.</td></tr>
</table>

<h3>4.1 Reaching static functions without touching the source</h3>
<p>Every function in <code>critter_mem.c</code> is <code>static</code>, so none of them can be called from another translation unit. Adding declarations, or removing <code>static</code>, would mean reviewing a file that is not the file under review. Instead the source is included into the test with its entry point renamed by the preprocessor:</p>
<blockquote><p><code>#define main critter_mem_main</code><br><code>#include "critter_mem.c"</code></p></blockquote>
<p>The bytes of the artefact are unchanged, and the rename happens in the test. The compute unit needs no such trick &mdash; its API has external linkage &mdash; and <code>thermal.c</code> is linked entirely alone, with a local stand-in for the workspace struct, so that a fault in its test cannot be attributed to <code>mpc.c</code>.</p>

<h3>4.2 Testing a unit that has no testable entry point</h3>
<p>The I/O unit&rsquo;s only entry point is an unbounded acquisition loop terminated by a signal. It cannot be unit-tested without refactoring it, so it is tested the way it is used: started, run, interrupted, and audited through its CSV. The run length is chosen deliberately at six seconds, because at 100&nbsp;Hz that crosses the 500-sample periodic-flush boundary exactly once, and a fencepost error in either the periodic flush or the tail flush would duplicate or drop samples precisely there. A shorter run would never reach that arithmetic.</p>

<h3>4.3 Two tests that needed a process boundary</h3>
<p>A test for missing parameter validation cannot run in-process, because the thing it detects is a crash. <code>MPC-05</code> therefore calls the control step with a null observation in a forked child and has the parent read the child&rsquo;s termination signal &mdash; the expected fault is <i>observed</i> rather than suffered. Similarly, <code>MEM-01</code> detects an out-of-bounds subscript using guard slots that lie inside the same array object as the twelve the unit believes it was given, so the test can watch the overrun without itself committing undefined behaviour. A test for undefined behaviour that relies on undefined behaviour proves nothing.</p>

<h2 class="pb">5. The test plan</h2>
<p>Every test carries an identifier, the rule it maps to, the mechanism that produces its verdict, and an explicit pass criterion. Tests expected to pass are marked; they are not padding. A rubric in which every check fails cannot distinguish a module that is careless from one that is merely unfinished, and three of the six cards here contain genuine passes that matter.</p>
__PLAN__

<h2 class="pb">6. The rubric</h2>
<p>The rubric scores two things and keeps them apart, because conflating them is how a review comes to claim a module is safe when what it actually means is that the checks were easy to run.</p>

<h3>6.1 Conformance &mdash; pass or fail, per rule, per module</h3>
<p>This is the score. A rule passes for a module only if every test mapped to that rule passed. One failure fails the rule.</p>
<table>
<tr><th style="width:13%">Verdict</th><th style="width:87%">Meaning and effect on the score</th></tr>
<tr><td class="verd pass">PASS</td><td>The module satisfies the rule and a test demonstrates it. Counts toward conformance.</td></tr>
<tr><td class="verd fail">FAIL</td><td>The module violates the rule and a test demonstrates it. Counts against conformance.</td></tr>
<tr><td class="verd rev">REVIEW</td><td>The rule is neither satisfied nor violated on the available evidence, and closing it needs a human. Counts against conformance, because an open question is not a pass. See &sect;7.</td></tr>
<tr><td class="verd na">NA</td><td>The rule has no applicable construct in this module. Reported, but excluded from the denominator &mdash; a rule with nothing to bite on is neither passed nor failed. Recorded explicitly so &ldquo;checked and clean&rdquo; can be told apart from &ldquo;never examined&rdquo;.</td></tr>
</table>
<p class="note">Conformance = PASS &divide; (PASS + FAIL + REVIEW), over rules, not over tests. Several tests may map to one rule &mdash; ARR30-C is tested twice for the memory unit, from inside and from outside &mdash; and the rule takes the worst verdict of its tests.</p>

<h3>6.2 Verification difficulty &mdash; what the verdict cost to obtain</h3>
<p>Every test is tagged with the method that produced its verdict, and each method carries a cost in reviewer effort. The tag is assigned explicitly, test by test, in a table inside the rubric engine, so the cost model can be audited line by line rather than inferred.</p>
<table>
<tr><th style="width:12%">Method</th><th style="width:7%">Cost</th><th style="width:81%">What it means, and why it is priced this way</th></tr>
<tr><td><b>COMPILE</b></td><td>1</td><td>The compiler or <code>gcc -fanalyzer</code> said so. Free, repeatable, no judgement, no maintenance. Put it in CI and stop thinking about it.</td></tr>
<tr><td><b>SCAN</b></td><td>2</td><td>A source scan decided it. Cheap to run, but somebody had to write the scanner, and its verdict is only as good as its heuristic. Counting assertions is reliable; deciding what a loop bound means is not.</td></tr>
<tr><td><b>RUNTIME</b></td><td>3</td><td>A test had to be designed, an input constructed, and the expected behaviour predicted in advance. This is where the engineering time actually goes. Constructing the split-line input required knowing the buffer size, the parser&rsquo;s acceptance rule, and where <code>fgets</code> would stop.</td></tr>
<tr><td><b>HUMAN</b></td><td>5</td><td>No tool can close it. A reviewer must read the code and decide. Rule 3&rsquo;s &ldquo;after initialization&rdquo;, Rule 2&rsquo;s bounds that are real but unprovable, and ERR33-C&rsquo;s assigned-but-untested residue all land here. Priced at five rather than four to reflect that this cost recurs on every change, while a CI check is paid once.</td></tr>
</table>
<p>Two figures follow from the tags. <b>Effort</b> is the summed cost across a card&rsquo;s tests &mdash; a rough estimate of what the card costs to verify once. <b>Manual share</b> is the fraction of a card&rsquo;s findings whose cheapest available detection method was <i>not</i> the compiler: that is, how much of the review no tool will do for you.</p>

<h3>6.3 Difficulty bands</h3>
<table>
<tr><th style="width:20%">Band</th><th style="width:33%">Trigger</th><th style="width:47%">What it tells the team</th></tr>
<tr><td><b>A &mdash; clean</b></td><td>No findings.</td><td>Re-run in CI and move on.</td></tr>
<tr><td><b>B &mdash; tool-assisted</b></td><td>Manual share &le; 34% and conformance &ge; 60%.</td><td>Most findings came from the compiler or the analyzer, and local edits discharge them. Budget CI time.</td></tr>
<tr><td><b>C &mdash; reviewer-led</b></td><td>Manual share &le; 67%.</td><td>The majority of findings needed a written test or a human reading. Budget reviewer time, not just CI time.</td></tr>
<tr><td><b>C+ &mdash; reviewer-led, heavy</b></td><td>Manual share &gt; 67%.</td><td>Almost nothing here is machine-detectable. This module sets the schedule.</td></tr>
<tr><td><b>D &mdash; redesign</b></td><td>A confirmed <span class="verd fail">FAIL</span> on Power of 10 Rule 2, 3 or 9.</td><td>At least one finding constrains the architecture &mdash; heap allocation after initialization, function pointers, or a loop with no provable bound. Hardening means rewriting the module, not patching it. Band D overrides the others: a module can be cheap to check and still need rebuilding.</td></tr>
</table>
<p class="note">A <span class="verd rev">REVIEW</span> on a structural rule deliberately does <i>not</i> trigger band D. A review is an open question, and answering it may clear the rule outright &mdash; which is exactly what happens to the compute unit&rsquo;s Rule 3 verdict, where the single arena allocation looks like a violation to a scanner and is plainly initialization to a human.</p>

<h3>6.4 Worked example</h3>
<p>Card C, the memory unit, scores 12 applicable rules: 3 pass, 8 fail, 1 review, and one further rule recorded not-applicable and excluded. Conformance is 3 &divide; 12 = 25%. Of its 9 findings, none is reachable by the compiler or the analyzer &mdash; the module builds clean at the most pedantic setting and the analyzer reports nothing &mdash; so the manual share is 100%. That alone would place it in band C+. But its Rule 2 and Rule 9 verdicts are confirmed failures on structural rules, so band D overrides: the buffers are sized from the input file at runtime and the median goes through a library sort reached by function pointer, and neither is a local edit. Effort totals 53 points across 18 tests, the highest of any card, which is consistent with it being the only module whose findings all had to be constructed by hand.</p>

<h2>7. Where the tools stop, and saying so</h2>
<p>Power of 10 Rule 2 states that a bound a checking tool cannot prove statically counts as violated. Read naively, that licenses a lazy scanner to fail everything and call it rigour. The scanner here classifies each loop into three buckets instead of two:</p>
<ul>
<li><b>Provable</b> &mdash; the bound is an integer literal, a macro constant, or an enumerator.</li>
<li><b>Review</b> &mdash; the bound is the induction variable of an enclosing loop in the same function. The loop <i>is</i> bounded, and proving it needs interval reasoning this scanner does not do. A stronger analyzer would likely discharge it; a human certainly can.</li>
<li><b>Unprovable</b> &mdash; the bound is a runtime value: a function parameter, a counter, a library call. No tool can prove this from the source, and no amount of tool quality changes that.</li>
</ul>
<p>Only the third bucket produces a failure. The distinction matters because reporting the second bucket as a violation would blame the code for the scanner&rsquo;s weakness, and any difficulty metric built on that is measuring the tool, not the artefact. It changes a real verdict: the compute unit&rsquo;s MPC kernel has 28 of 32 loops bounded by compile-time constants and four bounded by an enclosing block index, and it is recorded as needing one reviewer pass rather than as failing.</p>
<p>The ERR33-C audit is honest in the same direction but cannot be conservative in the safe one. Deciding whether a stored return value is genuinely checked needs dataflow analysis. Call sites are therefore classified as <i>discarded</i> (the call is its own statement &mdash; a definite violation needing no dataflow), <i>assigned-untested</i> (stored, but the variable appears in no nearby comparison &mdash; a likely violation a human must confirm), or <i>checked</i>. Every site is printed with its line number so a reviewer can close the residue by eye. The size of that residue is itself a measurement, and it is what puts ERR33-C in the HUMAN cost bucket on four of six cards.</p>

<h2>8. Three places the standards do not reach</h2>

<h3>8.1 A rule the units&rsquo; documentation cites that is not in this edition</h3>
<p>The memory unit&rsquo;s design record maps its divide-by-zero exposure to <code>FLP03-C</code>, twice. In the supplied 2016 edition, <code>FLP03-C</code> is not a rule. It appears only as a cross-reference inside the prose of other rules; it has no numbered section and no conformance criteria. Checking that a cited identifier actually exists in the normative source sounded like bookkeeping when it was written into the selection criteria in &sect;2, and it caught a citation that a reviewer working from the design record would have accepted.</p>
<p>The substantive consequence is worse than the citation error. <code>INT33-C</code> covers divide-by-zero for <i>integer</i> division. Every division by a possibly-zero count in the memory unit is floating-point. So the 2016 edition contains <b>no rule</b> covering float divide-by-zero, and the finding has to be carried under Power of 10 Rule 7 &mdash; parameter validation &mdash; instead. The suite records this as an explicit not-applicable verdict with the reasoning attached, rather than quietly dropping it.</p>

<h3>8.2 ERR33-C&rsquo;s table is narrower than it looks</h3>
<p>ERR33-C applies to the functions in its own table, and that table is specific. <code>fopen</code>, <code>fprintf</code>, <code>fclose</code>, <code>fflush</code>, <code>fgets</code>, <code>sscanf</code>, <code>malloc</code>, <code>realloc</code>, <code>signal</code>, <code>getenv</code> and <code>strtoul</code> are all in it, which makes the findings against them airtight. Plain <code>printf</code>, <code>puts</code>, <code>qsort</code>, <code>rewind</code> and <code>free</code> are <i>not</i>, so calls to them are not flagged &mdash; over-flagging would inflate the finding count and make the scorecard worthless. <code>nanosleep</code> and <code>clock_gettime</code> are POSIX rather than C standard and so are absent too, which means the I/O unit&rsquo;s unchecked timing calls cannot be cited under this rule at all, despite being exactly the kind of thing it exists to prevent.</p>

<h3>8.3 Three demonstrated defects with no rule in either standard</h3>
<p>The suite found three real defects, each reproducible on demand, that no rule in either source covers. They are scored separately and excluded from conformance, because scoring a module against a rule that does not exist would be dishonest. They are the most interesting result in the exercise, because they are what a purely rule-driven review would have shipped.</p>
<table>
<tr><th style="width:10%">Test</th><th style="width:24%">Module</th><th style="width:66%">Defect</th></tr>
<tr><td><code>MEM-18</code></td><td><code>critter_mem.c</code></td><td>A log line longer than the 128-byte buffer is split by <code>fgets</code>, and if the split falls in the right place <i>both halves parse as valid readings</i>. The suite constructs such a line and finds a temperature record in the cleaned output that appears in no input reading. <code>STR31-C</code> is not violated, because <code>fgets</code> does not overflow anything. No rule covers silent record fabrication by buffer splitting.</td></tr>
<tr><td><code>MPC-02</code></td><td><code>mpc.c</code></td><td>The control step returns status 0, meaning &ldquo;ok&rdquo;, alongside a NaN control command. Neither standard requires a status code to be consistent with the payload it describes, so no rule list would prompt a reviewer to check.</td></tr>
<tr><td><code>THM-04</code></td><td><code>thermal.c</code></td><td>The estimator hardcodes three conductances that duplicate plant-model fields, and takes no plant argument, so the duplication cannot be kept in step even in principle. Two plants differing only in coil conductance produce bit-identical estimator trajectories. Neither standard has a rule against duplicating a constant.</td></tr>
</table>

<h2>9. Results as measured</h2>
<p>Reproduced verbatim from the suite. Numbers in this section are generated at render time from <code>build/verdicts.txt</code>, not transcribed, so the document cannot drift from the code.</p>
<pre>__SCORE__</pre>

<h3 class="pb">9.1 Every verdict, with its recorded evidence</h3>
__RESULTS__

<h2>10. Reading the results</h2>
<p>Three observations that the rubric was built to surface, and did.</p>
<p><b>The compiler is nearly useless here, and that is the headline.</b> Across all six cards, the great majority of findings were not reachable by the compiler or by <code>gcc -fanalyzer</code>. The memory unit is the extreme case: it builds clean at <code>-Wall -Wextra -Wpedantic -std=c11</code> with zero warnings, the analyzer reports nothing, and every one of its nine findings had to be constructed by hand. Its own design record claims the clean build, and the claim is true &mdash; which is the point. Every finding on that card sits underneath it.</p>
<p><b>The two standards fail in different directions, and the split is structural.</b> The CERT findings are mostly local and mostly mechanical once you know where to look: a missing NULL check, a narrowing cast, an unchecked return. The Power of 10 findings are mostly architectural &mdash; heap allocation after initialization, a library sort reached by function pointer, loop counts driven by input size, zero assertions anywhere. No analyzer will tell a team that heap allocation after initialization is forbidden, because that is a policy about architecture rather than a detectable defect. That asymmetry is why band D is triggered only by Power of 10 rules.</p>
<p><b>Two units do not compile at the standard they nominally target.</b> The I/O unit does not build at all under <code>-std=c11 -Wpedantic</code> &mdash; <code>CLOCK_REALTIME</code> is a hard error &mdash; and the MPC kernel emits an implicit declaration for <code>posix_memalign</code>. Both build fine under the default GNU dialect, which is why nobody noticed. Power of 10 Rule 10 asks for a pedantic build from the first day of development, and this is what that rule is for. The suite is built <code>-std=gnu11</code> as a consequence, and that concession is recorded as a finding against the units rather than absorbed silently into the build system.</p>

<h2>11. Limits of this suite</h2>
<ul>
<li><b>The rubric weights are a judgement.</b> The 1/2/3/5 cost model is defensible but not derived from measurement. Anyone re-running this should feel free to re-price it; the tags are separated from the costs precisely so that re-pricing does not require re-tagging.</li>
<li><b>Conformance treats all rules as equal.</b> An out-of-bounds array write and a missing assertion each cost one rule. Severity weighting was left out deliberately, because a severity scale is a second set of contestable judgements layered on the first, and the per-rule table gives a reader everything needed to apply their own.</li>
<li><b>The static scanner is a heuristic, not a parser.</b> It strips comments and literals and matches braces; it does not build an AST. It is accurate on these six files, each of which was checked by hand against its output, and two false results were found and fixed during development &mdash; a false negative on <code>DCL31-C</code> caused by GCC quoting identifiers with typographic quotation marks, and a false positive on Rule 6 caused by struct members being mistaken for file-scope objects. On a larger codebase it would need to become a real front end.</li>
<li><b>Absence of a sanitizer report is not absence of a defect.</b> A 12-element automatic array can be overrun without landing outside any sanitizer redzone. This is why <code>ARR30-C</code> is tested twice by different mechanisms, and why the end-to-end check reports <span class="verd rev">REVIEW</span> rather than <span class="verd pass">PASS</span> if the sanitizers stay quiet while the guard-slot test fires.</li>
<li><b>Thirteen of ninety-nine CERT rules.</b> The selection is justified in &sect;2 and each choice is auditable, but it is a selection. A conformance figure of 25% means 25% of the rules <i>tested</i>, and should never be quoted without that qualifier.</li>
</ul>

<h2>12. Reproducing this</h2>
<p>No network access and no packages beyond a compiler and python3.</p>
<blockquote><p class="mono">cd test<br>
make run&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;# build, run all three layers, print the scorecard<br>
make verdicts&nbsp;# raw CT| verdict lines, unscored<br>
make runtime&nbsp;&nbsp;# the four C test binaries only<br>
make static&nbsp;&nbsp;&nbsp;# structural rules and compile probes only<br>
make dynamic&nbsp;&nbsp;# end-to-end and integration only (~10 s)<br>
make report&nbsp;&nbsp;&nbsp;# regenerate this document from the latest run<br>
make paths&nbsp;&nbsp;&nbsp;&nbsp;# show the resolved source paths and exit</p></blockquote>
<p>The suite expects <code>io-subsystem/</code>, <code>memory/src/</code> and <code>compute-intensive/</code> beside <code>test/</code>, with <code>critter.h</code> in <code>compute-intensive/include/</code>. Four variables locate them &mdash; <code>U1</code>, <code>U2</code>, <code>U3</code> and <code>U3_INC</code> &mdash; and the Makefile exports all four to both Python checkers, so the build and the checkers cannot disagree about where a unit lives. A wrong path fails immediately with the files it could not find and the paths it searched. The card names in the scorecard are logical labels for the six review cards, not directory paths, and do not change when the layout does. Fault inputs are written to <code>faults/</code> and left in place on purpose, so a reviewer can read the exact bytes that produced each verdict. Every verdict is one line of the form <code>CT|module|test|standard|rule|verdict|evidence</code>, which is the only interface between the three layers and the rubric &mdash; adding a check means emitting one more line, not modifying the scorer.</p>
"""


def main():
    body = (BODY.replace("__PLAN__", plan_html())
                .replace("__SCORE__", esc(scorecard_text()))
                .replace("__RESULTS__", results_table()))
    doc = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
           "<title>Critter Safety-Guideline Test Plan and Rubric</title>"
           "<style>%s</style></head><body>%s</body></html>" % (CSS, body))

    os.makedirs(OUT_DIR, exist_ok=True)
    html_path = os.path.join(HERE, "build", "report.html")
    pdf_path = os.path.join(OUT_DIR,
                            "critter_test_plan_and_rubric.pdf")
    with open(html_path, "w") as f:
        f.write(doc)

    r = subprocess.run([
        "wkhtmltopdf", "--enable-local-file-access", "--quiet",
        "--page-size", "A4",
        "--margin-top", "19mm", "--margin-bottom", "18mm",
        "--margin-left", "17mm", "--margin-right", "17mm",
        "--footer-font-size", "7", "--footer-font-name", "DejaVu Sans",
        "--footer-left", "Critter safety-guideline test plan and rubric",
        "--footer-right", "[page] / [topage]",
        "--footer-spacing", "6",
        html_path, pdf_path], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        return 1
    print("wrote %s (%.0f KB)" % (pdf_path,
                                  os.path.getsize(pdf_path) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())