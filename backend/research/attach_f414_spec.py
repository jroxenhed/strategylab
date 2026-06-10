"""One-off script: Attach F414 spec to existing premise p-495676dd.

Run from backend/:
    venv/bin/python3 research/attach_f414_spec.py

Continues from create_f414_premise.py which created p-495676dd in draft.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from research.premise_store import PremiseStore

NEW_PID = "p-495676dd"

SPEC_DICT = {
    "premise_text": (
        "Discretionary insider selling in small/mid-caps precedes sustained "
        "underperformance vs the market over the following 1-3 months."
    ),
    "stream": "form4",
    "event_filter": {"exclude_10b51": True},
    "dose": "s1_score",
    "dose_params": {},
    "analysis_form": "one_sample",
    "design_mde_pp": 8.0,
    "horizons": [10, 21, 30, 63],
    "entry_lag_days": 1,
    "dedup_same_ticker": True,
    "dedup_window_days": 30,
    "direction": "short",   # F414: short direction — harness computes (entry-exit)/entry
                             # so positive short excess = stock went down = thesis correct.
                             # s1_onesample gate checks mean_excess > 0 for direction="short".
    "floors": {
        "min_price": 5.0,
        "min_avg_volume": 500000,
        "max_market_cap": 10000000000.0,
    },
    "min_peer_count": 8,
    "fdr_q": 0.10,
    "n_boot": 999,
    "plain_summary": (
        "CIRCULARITY CAVEAT: The hypothesis direction (negative excess) was formed by "
        "observing the 2015-2020 data in the F396 census run (premise p-1569aa97). "
        "This explore run on the same window is therefore hypothesis-confirming, not "
        "hypothesis-generating. Advance to confirm requires a FRESH OUT-OF-SAMPLE window "
        "(2021+ data under F393). The p-values and effect sizes are informative as "
        "measurements but carry NO inferential weight as evidence of a novel discovery. "
        "Tests whether discretionary (non-10b5-1) insider S-type disposal filings at "
        "small/mid-cap US equities (MC <= $10B) precede negative universe excess "
        "(underperformance vs the liquid universe median) over 10/21/30/63 trading days. "
        "Analysis form: one-sample mean excess test (not dose-response quintiles). "
        "Primary horizon: 30 trading days. "
        "Pre-stated anchors: 30d mean -11.74pp, p_boot~0.000, MDE_1samp 6.59pp, n=20 "
        "(derived_from p-1569aa97 explore). "
        "direction=short in spec: harness computes short-side returns; positive mean_excess "
        "= insider selling is correct (stock underperformed)."
    ),
}


def main() -> None:
    store = PremiseStore()

    p = store.premises.get(NEW_PID) if isinstance(store.premises, dict) else None
    if p is None:
        # premises is a list
        premises_list = store.premises if isinstance(store.premises, list) else []
        for item in premises_list:
            if item.get("premise_id") == NEW_PID:
                p = item
                break
    if p is None:
        # Try via internal method
        try:
            p = store._get(NEW_PID)
        except KeyError:
            print(f"ERROR: Premise {NEW_PID} not found")
            sys.exit(1)

    print(f"Premise {NEW_PID}: status={p.get('status')}, derived_from={p.get('derived_from')}")

    # Transition to awaiting_formalization
    store.transition(NEW_PID, "awaiting_formalization")
    print(f"Transitioned: draft → awaiting_formalization")

    # Attach spec (does NOT auto-transition; we call transition after)
    store.add_spec(NEW_PID, SPEC_DICT)
    print("Spec attached")

    # Transition to spec_ready
    store.transition(NEW_PID, "spec_ready")
    print("Transitioned: awaiting_formalization → spec_ready")

    # Re-read to confirm
    try:
        p2 = store._get(NEW_PID)
    except Exception:
        p2 = {}
    print(f"Final status: {p2.get('status')}")
    print(f"derived_from: {p2.get('derived_from')}")
    spec = p2.get("spec", {})
    print(f"analysis_form: {spec.get('analysis_form')}")
    print(f"design_mde_pp: {spec.get('design_mde_pp')}")
    print(f"direction: {spec.get('direction')}")
    print()
    print(f"NEW PREMISE ID: {NEW_PID}")


if __name__ == "__main__":
    main()
