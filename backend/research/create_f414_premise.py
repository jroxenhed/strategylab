"""One-off script: Create F414 one-sample direction premise.

Duplicates p-1569aa97 (the s1 insider-selling dose-response premise) and
attaches an analysis_form="one_sample" spec.

Run from backend/:
    venv/bin/python3 research/create_f414_premise.py

Writes to backend/data/premises.json atomically via PremiseStore.
Prints the new premise_id for recording in the F414 changelog.

DO NOT re-run after the premise has been created — duplicate_premise is
idempotent per call but will create a second clone if run again.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure backend/ is on sys.path.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from research.premise_store import PremiseStore

PARENT_PREMISE_ID = "p-1569aa97"

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
    "direction": "short",   # F414: short direction. Harness computes (entry-exit)/entry,
                            # so positive values = stock went down = insider selling correct.
                            # s1_onesample gate checks mean_excess > 0 (positive short return).
                            # Parent p-1569aa97 used direction="long" (r1 dose-response doesn't
                            # depend on sign), but for one_sample gate the direction field must
                            # encode the thesis direction (orchestrator correction #2: "negative"
                            # in the long-return frame = "positive" in the short-return frame).
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
        "(derived_from p-1569aa97 explore)."
    ),
}


def main() -> None:
    store = PremiseStore()

    # Check parent exists
    parent = store.premises.get(PARENT_PREMISE_ID)
    if parent is None:
        print(f"ERROR: Parent premise {PARENT_PREMISE_ID!r} not found in premises.json")
        sys.exit(1)
    print(f"Parent premise: {PARENT_PREMISE_ID} (status={parent.get('status')})")

    # 1. Clone the parent to get derived_from set automatically
    new_pid = store.duplicate_premise(PARENT_PREMISE_ID)
    print(f"Created new premise: {new_pid} (derived_from={PARENT_PREMISE_ID})")

    # 2. Transition draft → awaiting_formalization
    store.transition(new_pid, "draft", "awaiting_formalization")
    print(f"Transitioned {new_pid}: draft → awaiting_formalization")

    # 3. Attach spec (transitions to spec_ready internally)
    store.add_spec(new_pid, SPEC_DICT)
    print(f"Spec attached to {new_pid}")

    # 4. Verify
    p = store.premises[new_pid]
    print(f"Final status: {p.get('status')}")
    print(f"derived_from: {p.get('derived_from')}")
    spec = p.get("spec", {})
    print(f"analysis_form in spec: {spec.get('analysis_form')}")
    print(f"design_mde_pp in spec: {spec.get('design_mde_pp')}")
    print(f"direction in spec: {spec.get('direction')}")
    print()
    print(f"NEW PREMISE ID: {new_pid}")
    print("Record this in .run/F414/impl.md")


if __name__ == "__main__":
    main()
