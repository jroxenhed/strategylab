#!/usr/bin/env python3
"""premise_p1569aa97_census.py — F396 power/testability census for the
discretionary-insider-SELL premise (p-1569aa97, s1_score).

Standalone one-off: reads the full-universe explore's events.ndjson directly
and computes one-sample + dose-gap MDEs at the premise's 30td primary horizon
(plus the 63td secondary lens). Deliberately does NOT use
premise_power_census.py — that instrument is 63td-hardcoded throughout (33+
references) and its calibration anchors are R-1b-specific (n=4245); see
.run/F396/plan.md §3 for the decision record.

MDE convention (matches premise_power_census.py): two-sided alpha=0.05 at 80%
power → multiplier z_{0.975} + z_{0.80} = 1.95996 + 0.84162 = 2.80158.

F338 anchors (pre-stated in .run/F396/plan.md §5, grounded in the explore's
verdict + run.log): B1 n_valid=20 exact, B2 n_score_def=18 exact,
B3 mean_30=−11.738±0.05, B4 std_30=10.519±0.05, B5 MDE_1samp_30=6.591±0.05,
B6 MDE_gap_30≈17.5±1.0. B1–B5 failure aborts before any output is written.

Usage:
    backend/venv/bin/python3 backend/research/premise_p1569aa97_census.py \
        --out .run/F396
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from statistics import mean, stdev

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STUDY = (_REPO_ROOT / "backend" / "data" / "turnaround" / "event_studies"
          / "premise_p-1569aa97_explore_1781050404")
_MDE_MULT = 2.80158  # z_0.975 + z_0.80, two-sided alpha=.05, 80% power
_FLOOR_PP = 1.0      # charter MDE abort threshold (R-1 convention, gap form)


def _mde_1samp(sd: float, n: int) -> float:
    return _MDE_MULT * sd / math.sqrt(n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    rows = [json.loads(l) for l in
            (_STUDY / "events.ndjson").read_text().splitlines() if l.strip()]
    valid = [r for r in rows
             if r["floor_status"] == "ok" and r["split"] == "explore"]
    # one-sample form needs no score; dose form needs a defined score
    score_def = [r for r in valid if r["payload"]["score"] is not None]
    ex30 = [r["fwd_excess_pct"]["30"] for r in valid]
    ex63 = [r["fwd_excess_pct"]["63"] for r in valid]
    if any(v is None for v in ex30) or any(v is None for v in ex63):
        log("ABORT: null 30td/63td excess inside floor_ok set — input broken")
        return 1

    mean_30, std_30 = mean(ex30), stdev(ex30)
    mde_1samp_30 = _mde_1samp(std_30, len(valid))
    mean_63, std_63 = mean(ex63), stdev(ex63)
    mde_1samp_63 = _mde_1samp(std_63, len(valid))

    # dose-gap: floor-division quintiles (n//5 per arm) on the score-defined
    # subset — fairer power estimate than the explore's boundary cuts (Q5 n=1)
    sd_sorted = sorted(score_def, key=lambda r: r["payload"]["score"])
    arm = len(sd_sorted) // 5
    q1 = [r["fwd_excess_pct"]["30"] for r in sd_sorted[:arm]]
    q5 = [r["fwd_excess_pct"]["30"] for r in sd_sorted[-arm:]]
    mde_gap_30 = _MDE_MULT * math.sqrt(stdev(q5) ** 2 / arm
                                       + stdev(q1) ** 2 / arm)

    n_needed_1pp = math.ceil((_MDE_MULT * std_30 / _FLOOR_PP) ** 2)
    n_needed_obs = math.ceil((_MDE_MULT * std_30 / abs(mean_30)) ** 2)

    anchors = [
        ("B1 n_valid == 20",            len(valid) == 20),
        ("B2 n_score_def == 18",        len(score_def) == 18),
        ("B3 mean_30 −11.738±0.05",     abs(mean_30 - -11.738) <= 0.05),
        ("B4 std_30 10.519±0.05",       abs(std_30 - 10.519) <= 0.05),
        ("B5 MDE_1samp_30 6.591±0.05",  abs(mde_1samp_30 - 6.591) <= 0.05),
        ("B6 MDE_gap_30 17.5±1.0",      abs(mde_gap_30 - 17.5) <= 1.0),
    ]
    log("F338 anchor checks:")
    for name, ok in anchors:
        log(f"  {'PASS' if ok else 'FAIL'}  {name}")
    hard_fail = any(not ok for (name, ok) in anchors[:5])
    if hard_fail:
        log("ABORT: B1-B5 anchor failure — input handling broken; no census "
            "output written (F338 gate).")
        (out / "census_run.log").write_text("\n".join(lines) + "\n")
        return 1

    log("")
    log(f"n_valid={len(valid)} n_score_def={len(score_def)} (arm={arm}/quintile)")
    log(f"30td: mean={mean_30:+.3f}pp std={std_30:.3f}pp "
        f"MDE_1samp={mde_1samp_30:.3f}pp MDE_gap={mde_gap_30:.3f}pp")
    log(f"63td: mean={mean_63:+.3f}pp std={std_63:.3f}pp "
        f"MDE_1samp={mde_1samp_63:.3f}pp")
    log(f"n needed (one-sample, 80% power): {n_needed_1pp} events for "
        f"{_FLOOR_PP}pp floor; {n_needed_obs} events for the observed "
        f"{abs(mean_30):.1f}pp effect size")

    log("")
    log("PLAIN-ENGLISH VERDICT —")
    log(f"  Dose-response form (more selling -> worse): smallest detectable "
        f"top-vs-bottom gap is {mde_gap_30:.1f}pp with {arm} events per arm. "
        f"UNTESTABLE at this universe size.")
    log(f"  One-sample direction form (any discretionary sell -> excess "
        f"move): smallest detectable effect is {mde_1samp_30:.1f}pp at n="
        f"{len(valid)}. The observed effect (−{abs(mean_30):.1f}pp) is "
        f"{abs(mean_30)/mde_1samp_30:.1f}x the MDE — DETECTABLE for effects "
        f"of the observed size, though far above the {_FLOOR_PP}pp charter "
        f"floor used for small-effect dose gaps.")

    census = {
        "premise_id": "p-1569aa97",
        "study": _STUDY.name,
        "primary_horizon_td": 30,
        "n_valid": len(valid),
        "n_score_defined": len(score_def),
        "mean_excess_30_pp": round(mean_30, 4),
        "std_excess_30_pp": round(std_30, 4),
        "mde_1samp_30_pp": round(mde_1samp_30, 4),
        "mde_gap_30_pp": round(mde_gap_30, 4),
        "arm_size": arm,
        "mean_excess_63_pp": round(mean_63, 4),
        "std_excess_63_pp": round(std_63, 4),
        "mde_1samp_63_pp": round(mde_1samp_63, 4),
        "n_needed_1samp_floor_1pp": n_needed_1pp,
        "n_needed_1samp_observed_effect": n_needed_obs,
        "mde_multiplier": _MDE_MULT,
        "anchors_pass": [name for name, ok in anchors if ok],
        "anchors_fail": [name for name, ok in anchors if not ok],
    }
    (out / "census.json").write_text(json.dumps(census, indent=2) + "\n")
    (out / "census_run.log").write_text("\n".join(lines) + "\n")
    log(f"\nWrote {out / 'census.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
