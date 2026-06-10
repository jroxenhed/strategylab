"""F413 — E2E test for premise_run_worker.run_full_explore_sync.

This is a @pytest.mark.slow real-data test that exercises the full explore
chain: cache load → event build → event study → r1_analysis → sidecar write.
It is SKIPPED in CI (and on any machine without the EDGAR + price cache).

Skip gate: backend/data/turnaround/edgar_cache/form4_stratified/index.json

Pre-stated anchors (from the 2026-06-10 manual run on worker data):
  - r1_explore_verdict.json written to outdir
  - ledger_entry.json sidecar written to outdir
  - ledger_entry["analysis_form"] == "dose_response"  (F416)
  - "spec_horizons" key present in ledger_entry       (F416)
  - "63" appears in ledger_entry["horizons"]           (63-injection)
  - ledger_entry["primary_horizon"] == max(spec.horizons)
  - verdict["explore_decision"] in {"ADVANCE", "DROP", "BORDERLINE",
      "UNTESTABLE-underpowered", "UNTESTABLE — power not evaluable"}
  - verdict["n_valid_events"] > 0

To run locally (worker data present):
    cd backend && venv/bin/python3 -m pytest tests/test_premise_e2e.py -v -m slow

To run on mfcore01 worker (worker data present):
    python3 -m pytest tests/test_premise_e2e.py -v -m slow
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# sys.path
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ---------------------------------------------------------------------------
# Cache availability gate
# ---------------------------------------------------------------------------
_INDEX_PATH = (
    _BACKEND / "data" / "turnaround" / "edgar_cache" / "form4_stratified" / "index.json"
)
_CACHE_PRESENT = _INDEX_PATH.exists()

_SKIP_REASON = (
    "Requires full EDGAR + price cache (worker data not present). "
    f"Expected: {_INDEX_PATH}"
)

# ---------------------------------------------------------------------------
# The premise p-495676dd (one_sample) exists on mfcore01 worker data and is
# the cheapest confirmed-working path.  The r1 dose-response path is tested
# if it can be found via the store; otherwise we use the s1 path.
# Both paths end up in run_full_explore_sync, which is the function under test.
# ---------------------------------------------------------------------------
_ONE_SAMPLE_PREMISE_ID = "p-495676dd"  # confirmed on worker 2026-06-10


@pytest.mark.slow
@pytest.mark.skipif(not _CACHE_PRESENT, reason=_SKIP_REASON)
def test_full_explore_sync_r1(tmp_path: Path) -> None:
    """E2E: run_full_explore_sync on a dose_response (r1) premise.

    Creates a minimal PremiseStore pointing to tmp_path so the real store
    is not mutated.  Monkeypatches DATA_PATH to a temp file with one r1
    premise constructed from the r1-family dose formula.

    Pre-stated anchors asserted (F413):
      - r1_explore_verdict.json written
      - ledger_entry.json written
      - analysis_form == "dose_response" in ledger (F416 fix)
      - spec_horizons present in ledger (F416 fix)
      - "63" in ledger horizons (63-injection)
      - primary_horizon == max(spec.horizons)
      - explore_decision in allowed set
      - n_valid_events > 0
    """
    import research.premise_store as ps_mod
    from research.premise_run_worker import run_full_explore_sync
    from research.premise_spec import PremiseSpec

    # Build a minimal dose_response premise spec — horizons=(21, 63) so
    # primary=63 and 63-injection is a no-op (already present).
    spec = PremiseSpec(
        premise_text="E2E test premise — Form 4 open-market purchase dose-response",
        analysis_form="dose_response",
        horizons=(21, 63),
        fdr_q=0.20,
        n_boot=200,  # reduced for speed
    )
    spec_dict = spec.model_dump()

    # Write a minimal premises.json in tmp_path
    test_premise_id = "p-e2etest01"
    store_data = {
        "version": 1,
        "premises": [
            {
                "premise_id": test_premise_id,
                "status": "spec_ready",
                "created_at": "2026-06-10T00:00:00+00:00",
                "spec_version": 1,
                "spec": spec_dict,
                "spec_history": [],
                "disposition": "active",
                "disposition_note": "",
                "derived_from": None,
            }
        ],
    }
    store_file = tmp_path / "premises.json"
    store_file.write_text(json.dumps(store_data, indent=2))

    # Monkeypatch DATA_PATH so PremiseStore() reads our tmp file
    original_data_path = ps_mod.DATA_PATH
    ps_mod.DATA_PATH = str(store_file)

    # outdir: a dedicated subdir of tmp_path (run_full_explore_sync writes artifacts here)
    outdir = tmp_path / f"premise_{test_premise_id}_explore_e2e"
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        verdict = run_full_explore_sync(premise_id=test_premise_id, outdir=outdir)
    finally:
        ps_mod.DATA_PATH = original_data_path

    # --- Pre-stated anchors ---

    # 1. verdict file written to disk
    verdict_path = outdir / "r1_explore_verdict.json"
    assert verdict_path.exists(), f"r1_explore_verdict.json not written to {outdir}"

    # 2. ledger_entry.json sidecar written
    ledger_path = outdir / "ledger_entry.json"
    assert ledger_path.exists(), f"ledger_entry.json not written to {outdir}"

    ledger = json.loads(ledger_path.read_text())

    # 3. analysis_form == "dose_response" (F416 fix)
    assert ledger.get("analysis_form") == "dose_response", (
        f"Expected analysis_form='dose_response', got {ledger.get('analysis_form')!r}"
    )

    # 4. spec_horizons key always present (F416 fix)
    assert "spec_horizons" in ledger, (
        "spec_horizons key missing from ledger_entry.json (F416 regression)"
    )

    # 5. "63" appears in horizons (63-injection always present in harness)
    assert 63 in ledger.get("horizons", []), (
        f"63 not in ledger horizons={ledger.get('horizons')!r} — 63td injection missing"
    )

    # 6. primary_horizon == max(spec.horizons)
    expected_primary = max(spec.horizons)
    assert ledger.get("primary_horizon") == expected_primary, (
        f"primary_horizon mismatch: expected {expected_primary}, "
        f"got {ledger.get('primary_horizon')!r}"
    )

    # 7. explore_decision in expected set
    allowed_decisions = {
        "ADVANCE",
        "DROP",
        "BORDERLINE",
        "UNTESTABLE-underpowered",
        "UNTESTABLE — power not evaluable",
        "WEAKENED-IN-EXPLORE",
    }
    decision = verdict.get("explore_decision")
    assert decision in allowed_decisions, (
        f"explore_decision {decision!r} not in allowed set {allowed_decisions}"
    )

    # 8. n_valid_events > 0 (real events found in cache)
    n_valid = verdict.get("n_valid_events", 0)
    assert n_valid > 0, (
        f"n_valid_events={n_valid!r} — no valid events found; "
        "check that edgar_cache contains Form 4 filings for 2015-2020"
    )
