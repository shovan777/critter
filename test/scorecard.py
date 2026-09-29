#!/usr/bin/env python3
"""
scorecard.py -- the rubric.

Reads CT| verdict lines (stdin, or files named on the command line) and prints
one scorecard per module, then a cross-module summary.

WHAT THE RUBRIC SCORES
----------------------
Two things, kept apart on purpose, because conflating them is how a review
ends up claiming a module is safe when what it means is that the checks were
easy to run.

  1. CONFORMANCE -- pass or fail, per rule, per module. A rule PASSES for a
     module only if every test mapped to it passed. One FAIL fails the rule.
     This is the score.

  2. VERIFICATION DIFFICULTY -- what it cost to reach each verdict. Every test
     is tagged with the method that produced it, and each method carries a
     cost in reviewer effort:

       COMPILE  1  the compiler or gcc -fanalyzer said so. Free, repeatable,
                   no judgement. Put it in CI and forget it.
       SCAN     2  a source scan decided it. Cheap to run, but somebody had
                   to write the scanner, and its verdict is only as good as
                   its heuristic.
       RUNTIME  3  a test had to be designed, an input constructed, and the
                   expected behaviour predicted. This is where the real
                   engineering time goes.
       HUMAN    5  no tool can close it. A reviewer must read the code and
                   decide. Rule 3's "after initialization", Rule 2's bounds
                   that are real but unprovable, and ERR33-C's
                   assigned-but-untested residue all land here.

RULE AGGREGATION
----------------
Several tests may map to one rule -- ARR30-C is tested twice for unit 2, from
inside with guard slots and from outside with AddressSanitizer. The rule takes
the worst verdict of its tests, with FAIL worse than REVIEW worse than PASS.
NA rules are reported but excluded from the denominator: a rule with nothing
to bite on is neither passed nor failed.

UNMAPPED FINDINGS
-----------------
Rows with standard "(neither)" are real defects the tests demonstrate for
which no rule exists in either standard. They are excluded from the
conformance score -- scoring a module against a rule that does not exist would
be dishonest -- and reported in their own column, because "how much did the
rule lists miss" is the most useful number here.
"""

import collections
import sys

# --- test_id -> detection method ------------------------------------------
# Explicit rather than inferred, so the cost model is auditable line by line.
METHOD = {
    # ---- Unit 1, I/O (io_unit.c; sensor_sim.c linked, not audited) -------
    "IO-01": "COMPILE",   # conflicting-redeclaration probe
    "IO-02": "HUMAN",     # ERR33 audit leaves an assigned-untested residue
    "IO-03": "COMPILE",   # gcc -fanalyzer
    "IO-04": "COMPILE",   # implicit-declaration diagnostic
    "IO-05": "SCAN", "IO-06": "HUMAN",   # Rule 2: bound real but unprovable
    "IO-07": "HUMAN",     # Rule 3: "after initialization" is architectural
    "IO-08": "SCAN", "IO-09": "SCAN", "IO-10": "SCAN",
    "IO-11": "SCAN", "IO-12": "SCAN", "IO-13": "COMPILE",
    "IO-14": "RUNTIME",   # signal-driven integration run
    "IO-15": "RUNTIME",
    # ---- Unit 2, memory (critter_mem.c) ----------------------------------
    "MEM-01": "RUNTIME", "MEM-02": "RUNTIME", "MEM-03": "RUNTIME",
    "MEM-04": "RUNTIME", "MEM-05": "RUNTIME", "MEM-06": "RUNTIME",
    "MEM-07": "SCAN", "MEM-08": "HUMAN",
    "MEM-09": "HUMAN", "MEM-10": "SCAN", "MEM-11": "HUMAN",
    "MEM-12": "HUMAN", "MEM-13": "SCAN", "MEM-14": "SCAN",
    "MEM-15": "SCAN", "MEM-16": "SCAN", "MEM-17": "SCAN",
    "MEM-18": "COMPILE",
    "MEM-19": "RUNTIME", "MEM-20": "RUNTIME", "MEM-21": "RUNTIME",
    # ---- Unit 3, compute (whole unit, driven through bin/critter) ---------
    "CMP-01": "RUNTIME", "CMP-02": "RUNTIME", "CMP-03": "RUNTIME",
    "CMP-04": "RUNTIME", "CMP-05": "RUNTIME", "CMP-06": "RUNTIME",
    "CMP-07": "HUMAN", "CMP-08": "COMPILE", "CMP-09": "COMPILE",
    "CMP-10": "SCAN", "CMP-11": "HUMAN", "CMP-12": "HUMAN",
    "CMP-13": "SCAN", "CMP-14": "SCAN", "CMP-15": "SCAN",
    "CMP-16": "SCAN", "CMP-17": "SCAN", "CMP-18": "COMPILE",
}
COST = {"COMPILE": 1, "SCAN": 2, "RUNTIME": 3, "HUMAN": 5}

# Findings that cannot be discharged by a local edit: they constrain the
# module's architecture, so hardening means redesign.
STRUCTURAL = {("NASA P10", "Rule 3"), ("NASA P10", "Rule 9"),
              ("NASA P10", "Rule 2")}

RANK = {"PASS": 0, "REVIEW": 1, "FAIL": 2}
RANK_NAME = {0: "PASS", 1: "REVIEW", 2: "FAIL"}

# One card per unit. Each unit ships as one runnable program, so each is
# scored on its own card and never merged with another: there is no defensible
# way to average a unit whose findings are architectural against one whose
# findings are local.
#
# sensor_sim.c is absent by design. It sits behind the sensor.h hardware
# boundary and its own README designates it the to-be-replaced part, so it is
# linked to make unit 1 runnable but is not audited or scored.
UNIT_OF = [
    ("unit1/io",      "Unit 1 - I/O",     "product"),
    ("unit2/memory",  "Unit 2 - memory",  "product"),
    ("unit3/compute", "Unit 3 - compute", "product, whole unit"),
]


def card_key(module):
    base = module.split(" [")[0]
    for prefix, unit, role in UNIT_OF:
        if base.startswith(prefix):
            return prefix, unit, role
    return base, "?", "?"


def parse(streams):
    rows = []
    for stream in streams:
        for line in stream:
            line = line.rstrip("\n")
            if not line.startswith("CT|"):
                continue
            parts = line.split("|", 6)
            if len(parts) < 7:
                continue
            _, module, tid, std, rule, verdict, evidence = parts
            rows.append(dict(module=module, tid=tid, std=std, rule=rule,
                             verdict=verdict, evidence=evidence))
    return rows


def band(conformance, manual_share, structural, has_findings):
    """Verification-difficulty band. Deliberately not a single number: a
    module can be easy to check and still fail everything."""
    if structural:
        return "D - redesign", ("at least one finding constrains the "
                                "architecture (heap after init, function "
                                "pointers, or unbounded loops), so hardening "
                                "is a rewrite of this module, not a patch")
    if not has_findings:
        return "A - clean", "no findings; re-run in CI and move on"
    if manual_share <= 0.34 and conformance >= 0.6:
        return "B - tool-assisted", ("most findings came from the compiler or "
                                     "the analyzer; local edits discharge them")
    if manual_share <= 0.67:
        return "C - reviewer-led", ("the majority of findings needed a written "
                                    "test or a human reading; budget reviewer "
                                    "time, not just CI time")
    return "C+ - reviewer-led, heavy", ("almost nothing here is machine-"
                                        "detectable; this module sets the "
                                        "schedule")


def main():
    streams = [open(a) for a in sys.argv[1:]] or [sys.stdin]
    rows = parse(streams)
    if not rows:
        sys.stderr.write("no CT| verdict lines found\n")
        return 2

    cards = collections.OrderedDict()
    for r in rows:
        key, unit, role = card_key(r["module"])
        c = cards.setdefault(key, dict(unit=unit, role=role, rows=[]))
        c["rows"].append(r)

    W = 100
    print("=" * W)
    print("CRITTER SAFETY-GUIDELINE SCORECARD")
    print("Sources: SEI CERT C Coding Standard, 2016 Edition; "
          "NASA/JPL Power of 10 Rules")
    print("=" * W)

    totals = dict(pass_=0, fail=0, review=0, na=0, unmapped=0, effort=0)
    summary = []

    for key, c in cards.items():
        rule_rows = [r for r in c["rows"]
                     if r["std"] in ("SEI CERT C", "NASA P10")]
        unmapped = [r for r in c["rows"] if r["std"] == "(neither)"]
        functional = [r for r in c["rows"] if r["std"] == "Functional"]

        by_rule = collections.OrderedDict()
        for r in rule_rows:
            k = (r["std"], r["rule"])
            by_rule.setdefault(k, []).append(r)

        print("")
        print("-" * W)
        print("MODULE  %s        [%s / %s]" % (key, c["unit"], c["role"]))
        print("-" * W)
        print("  %-11s %-9s %-8s %-8s  %s"
              % ("STANDARD", "RULE", "VERDICT", "METHOD", "TESTS"))

        n_pass = n_fail = n_review = n_na = 0
        manual_findings = auto_findings = 0
        structural = False
        effort = 0

        for (std, rule), rs in by_rule.items():
            verdicts = [r["verdict"] for r in rs]
            if all(v == "NA" for v in verdicts):
                v = "NA"
            else:
                worst = max(RANK.get(x, 0) for x in verdicts if x != "NA")
                v = RANK_NAME[worst]
            methods = [METHOD.get(r["tid"], "SCAN") for r in rs]
            effort += sum(COST[m] for m in methods)
            tag = "/".join(sorted(set(methods)))

            if v == "PASS":
                n_pass += 1
            elif v == "FAIL":
                n_fail += 1
            elif v == "REVIEW":
                n_review += 1
            else:
                n_na += 1

            if v in ("FAIL", "REVIEW"):
                worst_m = max(methods, key=lambda m: COST[m])
                if worst_m in ("COMPILE",):
                    auto_findings += 1
                else:
                    manual_findings += 1
                # Only a confirmed FAIL on a structural rule forces the
                # redesign band. A REVIEW is an open question, and answering
                # it may well clear the rule.
                if v == "FAIL" and (std, rule) in STRUCTURAL:
                    structural = True

            print("  %-11s %-9s %-8s %-8s  %s"
                  % (std, rule, v, tag, ", ".join(r["tid"] for r in rs)))

        for r in functional:
            print("  %-11s %-9s %-8s %-8s  %s"
                  % (r["std"], r["rule"][:9], r["verdict"],
                     METHOD.get(r["tid"], "RUNTIME"), r["tid"]))
        for r in unmapped:
            print("  %-11s %-9s %-8s %-8s  %s"
                  % ("(neither)", "UNMAPPED", "FINDING",
                     METHOD.get(r["tid"], "RUNTIME"), r["tid"]))

        scored = n_pass + n_fail + n_review
        conf = (n_pass / float(scored)) if scored else 1.0
        findings = n_fail + n_review
        manual_share = (manual_findings / float(findings)) if findings else 0.0
        b, why = band(conf, manual_share, structural, findings > 0)

        print("")
        print("  rules scored %d   pass %d   fail %d   review %d   "
              "not-applicable %d" % (scored, n_pass, n_fail, n_review, n_na))
        print("  CONFORMANCE  %.0f%% of applicable rules pass"
              % (100.0 * conf))
        print("  FINDINGS     %d total: %d compiler-detectable, %d needing a "
              "written test or a human (%.0f%%)"
              % (findings, auto_findings, manual_findings,
                 100.0 * manual_share))
        if unmapped:
            print("  UNMAPPED     %d demonstrated defect(s) with no rule in "
                  "either standard" % len(unmapped))
        print("  EFFORT       %d method-cost points across %d tests"
              % (effort, len(c["rows"])))
        print("  BAND         %s" % b)
        print("               %s" % why)

        totals["pass_"] += n_pass
        totals["fail"] += n_fail
        totals["review"] += n_review
        totals["na"] += n_na
        totals["unmapped"] += len(unmapped)
        totals["effort"] += effort
        summary.append((key, c["unit"], c["role"], scored, n_pass, conf,
                        findings, manual_share, len(unmapped), effort, b))

    print("")
    print("=" * W)
    print("CROSS-MODULE SUMMARY   (cards are never merged; each is scored "
          "against its own role)")
    print("=" * W)
    print("%-22s %-17s %5s %5s %6s %7s %7s %6s  %s"
          % ("MODULE", "UNIT", "RULES", "PASS", "CONF", "FINDS",
             "MANUAL", "EFFORT", "BAND"))
    for (k, u, role, scored, np_, conf, f, ms, um, eff, b) in summary:
        print("%-22s %-17s %5d %5d %5.0f%% %7d %6.0f%% %6d  %s"
              % (k[:22], u, scored, np_, 100 * conf, f, 100 * ms, eff, b))

    scored_all = totals["pass_"] + totals["fail"] + totals["review"]
    print("")
    print("ACROSS ALL CARDS: %d rule verdicts scored, %d pass (%.0f%%), "
          "%d fail, %d review, %d not applicable."
          % (scored_all, totals["pass_"],
             100.0 * totals["pass_"] / scored_all if scored_all else 0,
             totals["fail"], totals["review"], totals["na"]))
    print("%d demonstrated defects have no rule in either standard."
          % totals["unmapped"])
    print("Total verification effort: %d method-cost points."
          % totals["effort"])
    return 0


if __name__ == "__main__":
    sys.exit(main())