#!/usr/bin/env python3
"""
make_results.py -- produce every result artefact from one run, as files.

WHY THIS EXISTS
---------------
The suite is normally driven over SSH on the Raspberry Pi, where there is no
display. Nothing here opens a window, prints a figure, or waits for a GUI:
matplotlib is forced onto the Agg backend before it is imported, every figure
is written to disk, and the script ends by printing the absolute path of each
file so they can be clicked open in a remote editor.

WHAT IT PRODUCES, all under results/

  scorecard.txt      the full rubric output, verbatim
  verdicts.csv       every verdict, one row per test -- machine readable
  verdicts.md        the same as a markdown table, for pasting into the paper
  summary.csv        per-module metrics: conformance, findings, effort, band
  summary.md         the same as a markdown table
  results.json       everything above, structured, for any further analysis
  FIGURES.md         what each figure shows, and the caveat that goes with it
  fig1..fig8         the plots, as PNG (200 dpi) and PDF (vector, for LaTeX)
  critter_test_plan_and_rubric.pdf   the decision record and rubric

The rubric constants are imported from scorecard.py rather than restated, so
the figures and the printed scorecard can never disagree about a verdict, a
method cost, or a band threshold.

If matplotlib is absent the tables, JSON and PDF are still produced and the
script says what to install. Nothing else depends on it.
"""

import collections
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
BUILD = os.path.join(HERE, "build")
VERDICTS = os.path.join(BUILD, "verdicts.txt")

sys.path.insert(0, HERE)
import scorecard as SC          # noqa: E402  single source of truth

# --- headless plotting ----------------------------------------------------
HAVE_MPL = True
try:
    import matplotlib
    matplotlib.use("Agg")       # MUST precede pyplot; no display on the Pi
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
except Exception as exc:        # noqa: BLE001
    HAVE_MPL = False
    MPL_ERR = str(exc)

INK = "#16191d"
GREY = "#8a939c"
COLOUR = {
    "PASS":   "#2f7d46",
    "FAIL":   "#a4262c",
    "REVIEW": "#b07d00",
    "NA":     "#9aa4ad",
}
METHOD_COLOUR = {
    "COMPILE": "#2f6f9f",
    "SCAN":    "#4f9d69",
    "RUNTIME": "#d9832b",
    "HUMAN":   "#9c3d6b",
}
WRITTEN = []


def note(path):
    WRITTEN.append(os.path.abspath(path))
    return path


def style():
    if not HAVE_MPL:
        return
    plt.rcParams.update({
        "figure.dpi": 110,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "font.size": 9,
        "axes.titlesize": 10.5,
        "axes.labelsize": 9,
        "axes.edgecolor": INK,
        "axes.labelcolor": INK,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "legend.frameon": False,
        "grid.color": "#dfe3e7",
        "grid.linewidth": 0.6,
    })


def save(fig, stem, caption):
    """Every figure goes out twice: PNG to look at over SSH, PDF for LaTeX."""
    png = os.path.join(RESULTS, stem + ".png")
    pdf = os.path.join(RESULTS, stem + ".pdf")
    fig.savefig(png)
    fig.savefig(pdf)
    plt.close(fig)
    note(png)
    note(pdf)
    CAPTIONS.append((stem, caption))


CAPTIONS = []
CAVEAT = [""]


# ==========================================================================
# Aggregation -- same logic the printed scorecard uses, via scorecard.py
# ==========================================================================

def aggregate(rows):
    cards = collections.OrderedDict()
    for r in rows:
        key, unit, role = SC.card_key(r["module"])
        cards.setdefault(key, dict(module=key, unit=unit, role=role,
                                   rows=[]))["rows"].append(r)

    out = []
    for key, c in cards.items():
        rule_rows = [r for r in c["rows"]
                     if r["std"] in ("SEI CERT C", "NASA P10")]
        unmapped = [r for r in c["rows"] if r["std"] == "(neither)"]
        functional = [r for r in c["rows"] if r["std"] == "Functional"]

        by_rule = collections.OrderedDict()
        for r in rule_rows:
            by_rule.setdefault((r["std"], r["rule"]), []).append(r)

        rules, counts = [], collections.Counter()
        auto = manual = 0
        structural = False
        effort = 0

        for (std, rule), rs in by_rule.items():
            verdicts = [r["verdict"] for r in rs]
            if all(v == "NA" for v in verdicts):
                v = "NA"
            else:
                v = SC.RANK_NAME[max(SC.RANK.get(x, 0)
                                     for x in verdicts if x != "NA")]
            methods = [SC.METHOD.get(r["tid"], "SCAN") for r in rs]
            effort += sum(SC.COST[m] for m in methods)
            worst_m = max(methods, key=lambda m: SC.COST[m])
            counts[v] += 1
            if v in ("FAIL", "REVIEW"):
                if worst_m == "COMPILE":
                    auto += 1
                else:
                    manual += 1
                if v == "FAIL" and (std, rule) in SC.STRUCTURAL:
                    structural = True
            rules.append(dict(standard=std, rule=rule, verdict=v,
                              method=worst_m, methods=sorted(set(methods)),
                              tests=[r["tid"] for r in rs]))

        scored = counts["PASS"] + counts["FAIL"] + counts["REVIEW"]
        conf = counts["PASS"] / float(scored) if scored else 1.0
        findings = counts["FAIL"] + counts["REVIEW"]
        manual_share = manual / float(findings) if findings else 0.0
        b, why = SC.band(conf, manual_share, structural, findings > 0)

        out.append(dict(
            module=key, unit=c["unit"], role=c["role"], rules=rules,
            scored=scored, n_pass=counts["PASS"], n_fail=counts["FAIL"],
            n_review=counts["REVIEW"], n_na=counts["NA"],
            conformance=conf, findings=findings, auto=auto, manual=manual,
            manual_share=manual_share, effort=effort,
            n_tests=len(c["rows"]), band=b, band_why=why,
            unmapped=[dict(test=r["tid"], evidence=r["evidence"])
                      for r in unmapped],
            functional=[dict(test=r["tid"], rule=r["rule"],
                             verdict=r["verdict"]) for r in functional],
        ))
    return out


def caveat(cards):
    """Rendered from the data, so the qualifier can never go stale when the
    rule set or the card set changes."""
    cert = len({r["rule"] for c in cards for r in c["rules"]
                if r["standard"] == "SEI CERT C"})
    p10 = len({r["rule"] for c in cards for r in c["rules"]
               if r["standard"] == "NASA P10"})
    return ("Conformance is a percentage of the rules tested (%d of the 10 "
            "Power of 10 rules, %d of the 99 CERT rules), not of either "
            "standard as a whole." % (p10, cert))


def short(module):
    """Compact axis label. One card per unit, so the unit name is enough."""
    return module.split("/", 1)[-1]


# ==========================================================================
# Tables
# ==========================================================================

def write_tables(rows, cards):
    p = note(os.path.join(RESULTS, "verdicts.csv"))
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["module", "unit", "role", "test_id", "standard", "rule",
                    "verdict", "method", "method_cost", "evidence"])
        role_of = {c["module"]: (c["unit"], c["role"]) for c in cards}
        for r in rows:
            key, unit, role = SC.card_key(r["module"])
            unit, role = role_of.get(key, (unit, role))
            m = SC.METHOD.get(r["tid"], "SCAN")
            w.writerow([key, unit, role, r["tid"], r["std"], r["rule"],
                        r["verdict"], m, SC.COST[m], r["evidence"]])

    p = note(os.path.join(RESULTS, "verdicts.md"))
    with open(p, "w") as f:
        f.write("# Every verdict\n\n")
        f.write("| Module | Test | Standard | Rule | Verdict | Method |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in rows:
            key, _, _ = SC.card_key(r["module"])
            m = SC.METHOD.get(r["tid"], "SCAN")
            f.write("| `%s` | `%s` | %s | %s | **%s** | %s |\n"
                    % (key, r["tid"], r["std"], r["rule"], r["verdict"], m))

    hdr = ["module", "unit", "role", "rules_scored", "pass", "fail",
           "review", "not_applicable", "conformance_pct", "findings",
           "compiler_detectable", "needs_test_or_human", "manual_share_pct",
           "effort_points", "tests", "unmapped_findings", "band"]
    p = note(os.path.join(RESULTS, "summary.csv"))
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for c in cards:
            w.writerow([c["module"], c["unit"], c["role"], c["scored"],
                        c["n_pass"], c["n_fail"], c["n_review"], c["n_na"],
                        round(100 * c["conformance"], 1), c["findings"],
                        c["auto"], c["manual"],
                        round(100 * c["manual_share"], 1), c["effort"],
                        c["n_tests"], len(c["unmapped"]), c["band"]])

    p = note(os.path.join(RESULTS, "summary.md"))
    with open(p, "w") as f:
        f.write("# Per-module summary\n\n")
        f.write("| Module | Unit | Rules | Pass | Conf. | Findings | "
                "Compiler-detectable | Manual share | Effort | Band |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---|\n")
        for c in cards:
            f.write("| `%s` | %s | %d | %d | %.0f%% | %d | %d | %.0f%% | %d "
                    "| %s |\n"
                    % (short(c["module"]), c["unit"], c["scored"],
                       c["n_pass"], 100 * c["conformance"], c["findings"],
                       c["auto"], 100 * c["manual_share"], c["effort"],
                       c["band"]))
        tot_scored = sum(c["scored"] for c in cards)
        tot_pass = sum(c["n_pass"] for c in cards)
        tot_find = sum(c["findings"] for c in cards)
        tot_auto = sum(c["auto"] for c in cards)
        f.write("\n**Across all cards:** %d rule verdicts scored, %d pass "
                "(%.0f%%). Of %d findings, %d (%.0f%%) were reachable by the "
                "compiler or `gcc -fanalyzer`; the remaining %d needed a "
                "written test or a human. %d demonstrated defects have no "
                "rule in either standard.\n"
                % (tot_scored, tot_pass, 100.0 * tot_pass / tot_scored,
                   tot_find, tot_auto, 100.0 * tot_auto / tot_find,
                   tot_find - tot_auto,
                   sum(len(c["unmapped"]) for c in cards)))
        f.write("\n> " + caveat(cards) + "\n")


def write_json(cards, rows):
    tot_find = sum(c["findings"] for c in cards) or 1
    payload = dict(
        generated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sources=["SEI CERT C Coding Standard, 2016 Edition",
                 "NASA/JPL Power of 10 Rules"],
        method_costs=SC.COST,
        totals=dict(
            rule_verdicts=sum(c["scored"] for c in cards),
            passed=sum(c["n_pass"] for c in cards),
            failed=sum(c["n_fail"] for c in cards),
            review=sum(c["n_review"] for c in cards),
            not_applicable=sum(c["n_na"] for c in cards),
            findings=sum(c["findings"] for c in cards),
            compiler_detectable=sum(c["auto"] for c in cards),
            automation_rate_pct=round(
                100.0 * sum(c["auto"] for c in cards) / tot_find, 1),
            unmapped=sum(len(c["unmapped"]) for c in cards),
            effort_points=sum(c["effort"] for c in cards),
            tests=len(rows),
        ),
        caveat=(caveat(cards) + " CERT rules were selected where a construct "
                "was present and a violation demonstrable, which biases that "
                "subset toward failure; the CERT and Power of 10 pass rates "
                "are therefore not directly comparable."),
        cards=cards,
    )
    p = note(os.path.join(RESULTS, "results.json"))
    with open(p, "w") as f:
        json.dump(payload, f, indent=2)


# ==========================================================================
# Figures
# ==========================================================================

def fig_conformance(cards):
    order = sorted(cards, key=lambda c: c["conformance"])
    labels = [short(c["module"]) for c in order]
    y = range(len(order))
    fig, ax = plt.subplots(figsize=(7.4, 3.3))

    left = [0.0] * len(order)
    for v in ("PASS", "FAIL", "REVIEW", "NA"):
        key = {"PASS": "n_pass", "FAIL": "n_fail",
               "REVIEW": "n_review", "NA": "n_na"}[v]
        vals = [c[key] for c in order]
        ax.barh(list(y), vals, left=left, color=COLOUR[v], height=0.62,
                label=v.title() if v != "NA" else "Not applicable")
        left = [a + b for a, b in zip(left, vals)]

    for i, c in enumerate(order):
        ax.text(left[i] + 0.25, i, "%.0f%%" % (100 * c["conformance"]),
                va="center", ha="left", fontsize=8, color=INK,
                fontweight="bold")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.set_xlabel("rule verdicts")
    ax.set_xlim(0, max(left) + 2.4)
    ax.set_title("Rule verdicts per module, with conformance\n"
                 "Conformance = pass / (pass + fail + review); "
                 "not-applicable rules are excluded", loc="left")
    ax.legend(ncol=4, loc="lower right", bbox_to_anchor=(1.0, -0.30))
    ax.xaxis.grid(True); ax.set_axisbelow(True)
    save(fig, "fig1_conformance_by_module",
         "Rule verdicts per module. Conformance excludes not-applicable "
         "rules, which are recorded so that 'checked and clean' can be told "
         "apart from 'never examined'.")


def fig_detection(cards):
    order = sorted(cards, key=lambda c: -c["findings"])
    labels = [short(c["module"]) for c in order]
    x = range(len(order))
    buckets = ("COMPILE", "SCAN", "RUNTIME", "HUMAN")
    tally = {b: [] for b in buckets}
    for c in order:
        cnt = collections.Counter(r["method"] for r in c["rules"]
                                  if r["verdict"] in ("FAIL", "REVIEW"))
        for b in buckets:
            tally[b].append(cnt.get(b, 0))

    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    bottom = [0] * len(order)
    for b in buckets:
        ax.bar(list(x), tally[b], bottom=bottom, color=METHOD_COLOUR[b],
               width=0.6, label="%s (cost %d)" % (b.title(), SC.COST[b]))
        bottom = [p + q for p, q in zip(bottom, tally[b])]

    total_auto = sum(tally["COMPILE"])
    total = sum(bottom)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=18, ha="right")
    ax.set_ylabel("findings")
    ax.set_title("How each finding was detected\n"
                 "Only %d of %d findings (%.0f%%) were reachable by the "
                 "compiler or gcc -fanalyzer"
                 % (total_auto, total, 100.0 * total_auto / total),
                 loc="left")
    ax.legend(ncol=2)
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    save(fig, "fig2_detection_method",
         "Findings by cheapest available detection method. This is the "
         "automation asymmetry: the overwhelming majority of findings needed "
         "a written test or a reviewer. The memory unit is the extreme "
         "case: it compiles warning-free at the most pedantic setting and the "
         "analyzer reports nothing, yet every one of its findings is real.")


def fig_standards(cards):
    agg = {"SEI CERT C": collections.Counter(),
           "NASA P10": collections.Counter()}
    for c in cards:
        for r in c["rules"]:
            agg[r["standard"]][r["verdict"]] += 1

    stds = ["SEI CERT C", "NASA P10"]
    verds = ["PASS", "FAIL", "REVIEW", "NA"]
    w = 0.19
    fig, ax = plt.subplots(figsize=(7.4, 3.3))
    for i, v in enumerate(verds):
        vals = [agg[s][v] for s in stds]
        pos = [j + (i - 1.5) * w for j in range(len(stds))]
        bars = ax.bar(pos, vals, width=w, color=COLOUR[v],
                      label=v.title() if v != "NA" else "Not applicable")
        for b, val in zip(bars, vals):
            if val:
                ax.text(b.get_x() + b.get_width() / 2, val + 0.35, str(val),
                        ha="center", fontsize=7.5, color=INK)

    rate = []
    for s in stds:
        sc = agg[s]["PASS"] + agg[s]["FAIL"] + agg[s]["REVIEW"]
        rate.append(100.0 * agg[s]["PASS"] / sc if sc else 0)
    ax.set_xticks(range(len(stds)))
    ax.set_xticklabels(["%s\n%.0f%% of tested rules pass" % (s, r)
                        for s, r in zip(stds, rate)])
    ax.set_ylabel("rule verdicts, all cards pooled")
    ax.set_title("Verdicts by standard\n"
                 "NOT a like-for-like comparison -- see caveat below",
                 loc="left")
    ax.legend(ncol=4)
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    fig.text(0.0, -0.10,
             "Caveat: all 10 Power of 10 rules were exercised, including ones "
             "that pass trivially, whereas the 13 CERT rules were selected\n"
             "where a construct was present and a violation demonstrable. "
             "That biases the CERT column toward failure. What is comparable "
             "is the\ncharacter of the findings: CERT findings are local and "
             "edit-sized; Power of 10 findings are architectural.",
             fontsize=7.4, color="#3d454e", ha="left", va="top")
    save(fig, "fig3_verdicts_by_standard",
         "Verdicts by standard, pooled across cards. The pass rates are not "
         "directly comparable because of how the CERT subset was chosen; the "
         "useful contrast is that CERT findings are local while Power of 10 "
         "findings constrain the architecture.")


def fig_effort(cards):
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    band_colour = {}
    palette = ["#a4262c", "#b07d00", "#2f6f9f", "#2f7d46", "#9c3d6b"]
    for c in sorted(cards, key=lambda c: c["band"]):
        band_colour.setdefault(c["band"], palette[len(band_colour)
                                                  % len(palette)])
    for c in cards:
        ax.scatter(c["effort"], 100 * c["conformance"],
                   s=60 + 34 * c["findings"], alpha=0.82,
                   color=band_colour[c["band"]],
                   edgecolor=INK, linewidth=0.7, zorder=3)
        ax.annotate(short(c["module"]),
                    (c["effort"], 100 * c["conformance"]),
                    textcoords="offset points", xytext=(9, 8),
                    fontsize=8, color=INK)
    ax.set_xlabel("verification effort (summed method cost)")
    ax.set_ylabel("conformance, % of tested rules passing")
    ax.set_xlim(0, max(c["effort"] for c in cards) * 1.28)
    ax.set_ylim(-8, 100)
    ax.set_title("Effort against conformance\n"
                 "Marker area grows with finding count; colour is the "
                 "difficulty band", loc="left")
    ax.grid(True); ax.set_axisbelow(True)
    ax.legend(handles=[Patch(facecolor=v, edgecolor=INK, label=k)
                       for k, v in band_colour.items()],
              loc="upper right")
    save(fig, "fig4_effort_vs_conformance",
         "Effort against conformance. The two dimensions are deliberately "
         "separate: a module can be cheap to check and still need "
         "rebuilding, which is why band D is triggered by structural rules "
         "rather than by cost.")


def fig_matrix(cards):
    rule_keys, seen = [], set()
    for std in ("SEI CERT C", "NASA P10"):
        pool = set()
        for c in cards:
            for r in c["rules"]:
                if r["standard"] == std:
                    pool.add(r["rule"])
        def sort_key(name):
            return (0, int(name.split()[-1])) if name.startswith("Rule ") \
                else (0, name)
        for rule in sorted(pool, key=sort_key):
            if (std, rule) not in seen:
                seen.add((std, rule))
                rule_keys.append((std, rule))

    order = sorted(cards, key=lambda c: -c["conformance"])
    grid = []
    for c in order:
        lookup = {(r["standard"], r["rule"]): r["verdict"] for r in c["rules"]}
        grid.append([lookup.get(k) for k in rule_keys])

    fig, ax = plt.subplots(figsize=(9.0, 3.3))
    for i, row in enumerate(grid):
        for j, v in enumerate(row):
            if v is None:
                ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor="#f1f3f5",
                                           edgecolor="white", linewidth=1.1))
            else:
                ax.add_patch(plt.Rectangle((j, i), 1, 1,
                                           facecolor=COLOUR[v],
                                           edgecolor="white", linewidth=1.1))
                mark = {"PASS": "P", "FAIL": "F", "REVIEW": "R",
                        "NA": "-"}[v]
                ax.text(j + 0.5, i + 0.5, mark, ha="center", va="center",
                        fontsize=7.5, color="white", fontweight="bold")
    ax.set_xlim(0, len(rule_keys)); ax.set_ylim(0, len(order))
    ax.invert_yaxis()
    ax.set_xticks([j + 0.5 for j in range(len(rule_keys))])
    ax.set_xticklabels([r for _, r in rule_keys], rotation=52, ha="right",
                       fontsize=7.5)
    ax.set_yticks([i + 0.5 for i in range(len(order))])
    ax.set_yticklabels([short(c["module"]) for c in order])
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    n_cert = sum(1 for s, _ in rule_keys if s == "SEI CERT C")
    ax.axvline(n_cert, color=INK, linewidth=1.4)
    ax.text(n_cert / 2.0, -0.32, "SEI CERT C", ha="center", fontsize=8.5,
            fontweight="bold")
    ax.text(n_cert + (len(rule_keys) - n_cert) / 2.0, -0.32, "NASA P10",
            ha="center", fontsize=8.5, fontweight="bold")
    ax.set_title("Rule-by-module verdict matrix\n"
                 "P pass   F fail   R needs review   grey: rule not "
                 "exercised on that card", loc="left", pad=34)
    ax.legend(handles=[Patch(facecolor=COLOUR[v], label=lab) for v, lab in
                       (("PASS", "Pass"), ("FAIL", "Fail"),
                        ("REVIEW", "Review"), ("NA", "Not applicable"))],
              ncol=4, loc="lower center", bbox_to_anchor=(0.5, -0.44))
    save(fig, "fig5_rule_module_matrix",
         "Rule-by-module verdict matrix. Grey cells are rules not exercised "
         "on that card, which is a scope decision rather than a result. Note "
         "Power of 10 Rule 5 (assertion density): it fails on every card "
         "where it was checked -- there is not one assert() in the codebase.")


def fig_rule_failures(cards):
    tally = collections.Counter()
    checked = collections.Counter()
    for c in cards:
        for r in c["rules"]:
            key = "%s %s" % ("CERT" if r["standard"] == "SEI CERT C"
                             else "P10", r["rule"])
            if r["verdict"] != "NA":
                checked[key] += 1
            if r["verdict"] in ("FAIL", "REVIEW"):
                tally[key] += 1
    items = [k for k in checked if tally[k]]
    items.sort(key=lambda k: (-tally[k], k))
    if not items:
        return
    fig, ax = plt.subplots(figsize=(7.4, max(3.0, 0.30 * len(items) + 1.3)))
    y = range(len(items))
    ax.barh(list(y), [checked[k] for k in items], color="#e3e7ea",
            height=0.66, label="cards where the rule was checked")
    ax.barh(list(y), [tally[k] for k in items], color=COLOUR["FAIL"],
            height=0.66, label="cards where it failed or needs review")
    for i, k in enumerate(items):
        ax.text(checked[k] + 0.1, i, "%d/%d" % (tally[k], checked[k]),
                va="center", fontsize=7.5, color=INK)
    ax.set_yticks(list(y)); ax.set_yticklabels(items, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("number of cards")
    ax.set_xlim(0, max(checked.values()) + 1.1)
    ax.set_title("Which rules fail, and on how many cards\n"
                 "Ordered by breadth of failure", loc="left")
    ax.legend(loc="lower right")
    ax.xaxis.grid(True); ax.set_axisbelow(True)
    save(fig, "fig6_rule_failure_breadth",
         "Rules ordered by how many cards they fail on. The systemic ones -- "
         "Rule 5, Rule 7, ERR33-C -- are cheap to detect and expensive to "
         "remediate, because the fix is per-function domain work rather than "
         "a mechanical edit.")


def fig_verification_gap(cards):
    """The figure to put in the paper if only one fits.

    Left: where each module's findings came from, with conformance and band.
    Right: the same findings pooled, with the automated share bracketed, and
    the rule-invisible defects shown detached -- they are not a slice of the
    findings, they are findings the rule system never had a category for.
    """
    buckets = ("COMPILE", "SCAN", "RUNTIME", "HUMAN")
    order = sorted(cards, key=lambda c: -c["findings"])

    fig = plt.figure(figsize=(9.6, 4.9))
    gs = fig.add_gridspec(1, 2, width_ratios=[3.05, 1.0], wspace=0.30)
    ax = fig.add_subplot(gs[0, 0])
    bx = fig.add_subplot(gs[0, 1])

    # ---- left: per module -------------------------------------------------
    tally = {b: [] for b in buckets}
    for c in order:
        cnt = collections.Counter(r["method"] for r in c["rules"]
                                  if r["verdict"] in ("FAIL", "REVIEW"))
        for b in buckets:
            tally[b].append(cnt.get(b, 0))

    y = list(range(len(order)))
    left = [0.0] * len(order)
    for b in buckets:
        ax.barh(y, tally[b], left=left, color=METHOD_COLOUR[b], height=0.60,
                edgecolor="white", linewidth=0.7,
                label="%s (cost %d)" % (b.title(), SC.COST[b]))
        left = [a + v for a, v in zip(left, tally[b])]

    for i, c in enumerate(order):
        if tally["COMPILE"][i]:
            ax.text(tally["COMPILE"][i] / 2.0, i, str(tally["COMPILE"][i]),
                    ha="center", va="center", fontsize=7.6, color="white",
                    fontweight="bold")
        ax.text(left[i] + 0.28, i,
                "%s  \u00b7  %.0f%% conf." % (c["band"].split(" - ")[0],
                                           100 * c["conformance"]),
                va="center", fontsize=7.6,
                color=INK if c["band"].startswith("D") else "#4a545e",
                fontweight="bold" if c["band"].startswith("D") else "normal")

    ax.set_yticks(y)
    ax.set_yticklabels([short(c["module"]) for c in order])
    ax.invert_yaxis()
    ax.set_xlabel("findings (rules failing or needing review)")
    ax.set_xlim(0, max(left) + 4.6)
    ax.set_title("Where each module's findings came from", loc="left",
                 fontsize=10.5)
    ax.legend(ncol=2, loc="lower right")
    ax.xaxis.grid(True); ax.set_axisbelow(True)

    # ---- right: pooled ----------------------------------------------------
    pooled = {b: sum(tally[b]) for b in buckets}
    total = sum(pooled.values())
    auto = pooled["COMPILE"]
    unmapped = sum(len(c["unmapped"]) for c in cards)

    bottom = 0.0
    for b in buckets:
        bx.bar([0], [pooled[b]], bottom=bottom, width=0.42,
               color=METHOD_COLOUR[b], edgecolor="white", linewidth=0.8)
        if pooled[b]:
            bx.text(0, bottom + pooled[b] / 2.0, str(pooled[b]),
                    ha="center", va="center", fontsize=8.2, color="white",
                    fontweight="bold")
        bottom += pooled[b]

    # bracket the machine-detectable share against the rest
    bx.plot([0.26, 0.33, 0.33, 0.26], [0.1, 0.1, auto - 0.1, auto - 0.1],
            color=INK, linewidth=0.9)
    bx.text(0.38, auto / 2.0,
            "%d\nby compiler\nor analyzer" % auto,
            va="center", fontsize=8, color=INK, fontweight="bold")
    bx.plot([0.26, 0.33, 0.33, 0.26],
            [auto + 0.1, auto + 0.1, total - 0.1, total - 0.1],
            color=INK, linewidth=0.9)
    bx.text(0.38, (auto + total) / 2.0,
            "%d\nneeded a written\ntest or a human" % (total - auto),
            va="center", fontsize=8, color=INK)

    # the rule-invisible defects, detached: not a slice of the findings
    gap = total * 0.13
    bx.bar([0], [unmapped], bottom=total + gap, width=0.42,
           color="white", edgecolor=INK, linewidth=1.1, hatch="////")
    bx.text(0, total + gap + unmapped / 2.0, str(unmapped), ha="center",
            va="center", fontsize=8.2, color=INK, fontweight="bold")
    bx.text(0.38, total + gap + unmapped / 2.0,
            "%d defects with\nno rule in either\nstandard" % unmapped,
            va="center", fontsize=8, color=INK, style="italic")

    bx.set_xlim(-0.30, 1.25)
    bx.set_ylim(0, total + gap + unmapped + 1.6)
    bx.set_xticks([])
    bx.set_ylabel("findings, all cards pooled")
    bx.set_title("Pooled", loc="left", fontsize=10.5)
    bx.yaxis.grid(True); bx.set_axisbelow(True)
    for sp in ("top", "right", "bottom"):
        bx.spines[sp].set_visible(False)

    fig.text(0.012, 1.10,
             "Only %d of %d findings (%.0f%%) were reachable by the "
             "compiler or gcc -fanalyzer"
             % (auto, total, 100.0 * auto / total),
             fontsize=12.2, fontweight="bold", ha="left", va="bottom")
    n_cert = len({r["rule"] for c in cards for r in c["rules"]
                  if r["standard"] == "SEI CERT C"})
    n_p10 = len({r["rule"] for c in cards for r in c["rules"]
                 if r["standard"] == "NASA P10"})
    fig.text(0.012, 1.045,
             "%d units, %d rule verdicts, two standards. Conformance is a "
             "percentage of the rules tested \u2014 %d of the 10 Power of 10 "
             "rules and %d of the 99 CERT rules."
             % (len(cards), sum(c["scored"] for c in cards), n_p10, n_cert),
             fontsize=8.4, color="#3d454e", ha="left", va="bottom")
    fig.subplots_adjust(top=0.90)

    save(fig, "fig9_verification_gap",
         "THE HEADLINE FIGURE. Findings by cheapest available detection "
         "method, per module and pooled. The hatched block is detached "
         "deliberately: those three defects are not a slice of the findings, "
         "they are defects the rule system had no category for, so a "
         "rule-driven review would have shipped them. Note critter_mem.c: it "
         "compiles warning-free at the most pedantic setting and gcc "
         "-fanalyzer reports nothing, yet it contributes zero to the "
         "machine-detectable column and carries the heaviest verification "
         "cost of any card.")


def fig_monthly():
    """The memory unit's actual product: twelve rows of summarised data."""
    # The real sample first. build/critter_summary.csv is whatever the last
    # fault-injection run produced -- a handful of months, including a
    # fabricated record -- and plotting that as "the unit's output" would be
    # actively misleading. It is used only as a last resort, and the
    # populated-month check below rejects it.
    u2 = os.environ.get("U2", "")
    for cand in (os.path.join(u2, "critter_summary.csv") if u2 else "",
                 os.path.join(os.path.dirname(HERE), "memory", "src",
                              "critter_summary.csv"),
                 os.path.join(BUILD, "critter_summary.csv")):
        if cand and os.path.exists(cand):
            path = cand
            break
    else:
        return

    months, mean, lo, hi, cnt = [], [], [], [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                if int(row["reading_count"]) == 0:
                    continue
                months.append(row["month_name"][:3])
                mean.append(float(row["mean_temp_c"]))
                lo.append(float(row["min_temp_c"]))
                hi.append(float(row["max_temp_c"]))
                cnt.append(int(row["reading_count"]))
            except (KeyError, ValueError):
                return
    if len(months) < 6:
        sys.stderr.write(
            "skipping fig7: %s has only %d populated month(s), so it is "
            "fault-injection output rather than a real run.\n"
            "To get this figure, run the memory unit on the shipped sample "
            "first:\n    cd %s && ./critter_mem sample_temps.txt\n"
            % (path, len(months), u2 or "../memory/src"))
        return

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(7.4, 4.6), sharex=True,
                                  gridspec_kw=dict(height_ratios=[2.5, 1]))
    x = range(len(months))
    ax.fill_between(list(x), lo, hi, color="#cfe0ec", label="min to max")
    ax.plot(list(x), mean, color="#2f6f9f", marker="o", markersize=4,
            linewidth=1.7, label="mean")
    ax.set_ylabel("temperature, degC")
    ax.set_title("Unit 2 output: monthly summary after outlier removal\n"
                 "Read from %s -- %d readings across %d months"
                 % (os.path.basename(path), sum(cnt), len(months)),
                 loc="left")
    ax.legend(ncol=2); ax.grid(True); ax.set_axisbelow(True)

    ax2.bar(list(x), cnt, color="#9aa4ad", width=0.62)
    ax2.set_ylabel("readings")
    ax2.set_xticks(list(x)); ax2.set_xticklabels(months)
    ax2.set_ylim(0, max(cnt) * 1.52)
    ax2.grid(True); ax2.set_axisbelow(True)
    ax2.text(0.005, 0.97, "February's lower count is not a bucketing bug: "
                          "uniformly random timestamps land in it less often.",
             transform=ax2.transAxes, fontsize=7.4, color="#3d454e",
             va="top")
    save(fig, "fig7_unit2_monthly_summary",
         "The memory unit's summarised output: a million readings reduced to "
         "twelve rows. Included because a conformance review should show what "
         "the module produces when it works, not only how it fails.")


def fig_io_trace():
    path = os.path.join(BUILD, "io_integration.csv")
    if not os.path.exists(path):
        return
    seq, temp = [], []
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                seq.append(int(row["seq"]))
                temp.append(int(row["raw_temp_cC"]) / 100.0)
            except (KeyError, ValueError):
                continue
    if len(seq) < 20:
        return

    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    ax.plot(seq, temp, color="#d9832b", linewidth=0.85)
    ax.set_xlabel("sample sequence number")
    ax.set_ylabel("temperature, degC")
    ax.set_title("Unit 1 integration run: %d samples at 100 Hz, terminated "
                 "by SIGINT\nseq contiguous 0..%d, no duplicates, no "
                 "malformed rows (test IO-12)"
                 % (len(seq), seq[-1]), loc="left")
    ax.grid(True); ax.set_axisbelow(True)
    for b in range(500, seq[-1] + 1, 500):
        ax.axvline(b, color=GREY, linewidth=0.9, linestyle="--")
        ax.text(b, ax.get_ylim()[1], " flush boundary", fontsize=7,
                color="#3d454e", va="top")
    save(fig, "fig8_unit1_acquisition_trace",
         "The I/O unit's acquisition trace from the integration run. The "
         "dashed line marks the 500-sample periodic-flush boundary, which is "
         "why the run is six seconds long: a fencepost error in either the "
         "periodic flush or the tail flush would duplicate or drop samples "
         "exactly there. The spikes are the simulated sensor's deliberate "
         "glitch readings, which are what unit 2 exists to remove.")


def write_captions():
    p = note(os.path.join(RESULTS, "FIGURES.md"))
    with open(p, "w") as f:
        f.write("# Figures\n\nEach figure is written twice: `.png` to view "
                "over SSH, `.pdf` (vector) to include in the paper.\n\n")
        for stem, cap in CAPTIONS:
            f.write("## `%s`\n\n%s\n\n" % (stem, cap))
        f.write("---\n\n**Caveat that belongs with any conformance figure "
                "quoted from these plots:** " + CAVEAT[0] + " The "
                "manual-share figure is also unstable at small denominators, "
                "so absolute finding counts are the safer quantity to "
                "cite.\n")


# ==========================================================================

def main():
    if not os.path.exists(VERDICTS):
        sys.stderr.write(
            "no verdicts found at %s\nRun `make run` first.\n" % VERDICTS)
        return 2

    os.makedirs(RESULTS, exist_ok=True)
    with open(VERDICTS) as f:
        rows = SC.parse([f])
    if not rows:
        sys.stderr.write("%s contains no CT| verdict lines\n" % VERDICTS)
        return 2

    cards = aggregate(rows)

    # the rubric, verbatim
    r = subprocess.run([sys.executable, os.path.join(HERE, "scorecard.py"),
                        VERDICTS], capture_output=True, text=True)
    p = note(os.path.join(RESULTS, "scorecard.txt"))
    with open(p, "w") as f:
        f.write(r.stdout or r.stderr)

    write_tables(rows, cards)
    write_json(cards, rows)

    CAVEAT[0] = caveat(cards)
    if HAVE_MPL:
        style()
        fig_conformance(cards)
        fig_detection(cards)
        fig_standards(cards)
        fig_effort(cards)
        fig_matrix(cards)
        fig_rule_failures(cards)
        fig_verification_gap(cards)
        fig_monthly()
        fig_io_trace()
        write_captions()
    else:
        sys.stderr.write(
            "\nmatplotlib not available (%s) -- tables, JSON and the PDF were\n"
            "still written. To get the figures:\n"
            "    sudo apt install python3-matplotlib\n\n" % MPL_ERR)

    # the decision record, into results/ alongside everything else
    rep = os.path.join(HERE, "make_report.py")
    if os.path.exists(rep):
        rr = subprocess.run([sys.executable, rep, "--out", RESULTS],
                            capture_output=True, text=True)
        pdf = os.path.join(RESULTS, "critter_test_plan_and_rubric.pdf")
        if os.path.exists(pdf):
            note(pdf)
        elif rr.returncode != 0:
            sys.stderr.write("PDF generation skipped: %s\n"
                             % rr.stderr.strip()[:200])

    t = dict(scored=sum(c["scored"] for c in cards),
             passed=sum(c["n_pass"] for c in cards),
             finds=sum(c["findings"] for c in cards),
             auto=sum(c["auto"] for c in cards),
             unmapped=sum(len(c["unmapped"]) for c in cards),
             effort=sum(c["effort"] for c in cards))
    print("%d rule verdicts scored, %d pass (%.0f%%). %d findings, %d "
          "compiler-detectable (%.0f%%). %d unmapped defects. %d effort "
          "points."
          % (t["scored"], t["passed"], 100.0 * t["passed"] / t["scored"],
             t["finds"], t["auto"], 100.0 * t["auto"] / t["finds"],
             t["unmapped"], t["effort"]))
    print("\nWrote %d files to %s\n" % (len(WRITTEN), RESULTS))
    for w in WRITTEN:
        print("  " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())