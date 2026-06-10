"""Premise compiler: PremiseSpec → CompileResult (EventStudyConfig + metadata).

compile_spec() is a PURE function — no I/O, no random state.
Same spec + same arguments → same CompileResult guaranteed by the mapping table.

Field mapping (PremiseSpec → EventStudyConfig):
  horizons          → horizons         (direct)
  entry_lag_days    → entry_lag_days   (direct)
  dedup_same_ticker → dedup_same_ticker (direct)
  dedup_window_days → dedup_window_days (direct, R-1 default=30)
  fdr_q             → fdr_q            (direct)
  n_boot            → n_boot           (direct)
  min_peer_count    → min_peer_count   (direct)
  (implicit)        → explore_cutoff   ALWAYS _EXPLORE_CUTOFF = date(2020,12,31)
  (implicit)        → allow_post_2020_explore  ALWAYS False
  (implicit)        → use_non_overlapping      ALWAYS False
  (implicit)        → block_size_override      ALWAYS None
  study_name (arg)  → study_name
  output_dir (arg)  → output_dir
  fdr_ledger_path (arg) → fdr_ledger_path

explore_cutoff and allow_post_2020_explore are ALWAYS the safe defaults —
they are never spec-driven (anti-p-hacking: the cutoff is a program-level
invariant, not a per-premise choice).

cost_fn selection:
  dose="r1_score" → lambda ev, price: 0.04  (R-1 charter §7: 2 bps/leg × 2 legs)
  dose="s1_score" → lambda ev, price: 0.04  (S-1 mirrors R-1 charter §7)

floors (min_price, min_avg_volume) are NOT EventStudyConfig fields.  They are
returned in CompileResult.floors so the F389 run service can apply them via
universe_floors.passes_floors(df, as_of) when building the universe.

dose_builder:
  CompileResult.dose_builder is the dose string (e.g. "r1_score", "s1_score").
  The F389 run service reads this field and dispatches to the matching builder
  (build_r1_events from r1_dose.py, or build_s1_events from s1_dose.py) via
  _DOSE_BUILDERS in _run_preview_sync.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

# Ensure backend/ is on sys.path regardless of how this module is imported.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from research.event_study import EventStudyConfig, EventRecord, _EXPLORE_CUTOFF  # noqa: E402
from research.premise_spec import PremiseSpec, UniverseFloors, _VALID_DOSES  # noqa: E402


# ---------------------------------------------------------------------------
# CompileResult
# ---------------------------------------------------------------------------

@dataclass
class CompileResult:
    """Output of compile_spec().

    config       : ready-to-use EventStudyConfig (pure, no I/O)
    floors       : UniverseFloors for the run service to apply per ticker
    dose_builder : dose string ("r1_score" or "s1_score") — F389 dispatches to matching builder
    stream_id    : registered stream id (e.g. "form4")
    """
    config: EventStudyConfig
    floors: UniverseFloors
    dose_builder: str   # "r1_score" | "s1_score" — F389 dispatches to matching builder
    stream_id: str      # registered stream to use


# ---------------------------------------------------------------------------
# cost_fn registry (dose → callable)
# ---------------------------------------------------------------------------

def _cost_fn_r1_score(event: EventRecord, price: float) -> float:
    """R-1 charter §7: 2 bps/leg × 2 legs = 0.04 (4 bps round-trip)."""
    return 0.04


def _cost_fn_s1_score(event: EventRecord, price: float) -> float:
    """S-1: 2 bps/leg × 2 legs = 0.04 (mirror r1 charter §7)."""
    return 0.04


_COST_FN_BY_DOSE = {
    "r1_score": _cost_fn_r1_score,
    "s1_score": _cost_fn_s1_score,
}

# Single-source-of-truth guard: every dose that premise_spec allows MUST have a
# cost_fn entry here.  Adding a dose to _VALID_DOSES without a matching cost_fn
# will raise immediately at module import rather than silently passing cost_fn=None
# to EventStudyConfig at run time.
assert _VALID_DOSES.issubset(_COST_FN_BY_DOSE), (
    f"Doses declared in premise_spec._VALID_DOSES but missing from "
    f"premise_compile._COST_FN_BY_DOSE: {_VALID_DOSES - set(_COST_FN_BY_DOSE)}"
)


# ---------------------------------------------------------------------------
# compile_spec — pure function
# ---------------------------------------------------------------------------

def compile_spec(
    spec: PremiseSpec,
    study_name: str,
    output_dir: Optional[Path] = None,
    fdr_ledger_path: Optional[Path] = None,
) -> CompileResult:
    """Pure function: PremiseSpec → CompileResult (EventStudyConfig + metadata).

    No I/O.  No random state.  Same spec → same result.

    explore_cutoff and allow_post_2020_explore are ALWAYS the safe defaults
    and are never taken from the spec (anti-p-hacking invariant).
    """
    cost_fn = _COST_FN_BY_DOSE.get(spec.dose)

    config = EventStudyConfig(
        study_name=study_name,
        horizons=spec.horizons,
        explore_cutoff=_EXPLORE_CUTOFF,          # ALWAYS date(2020, 12, 31)
        entry_lag_days=spec.entry_lag_days,
        use_non_overlapping=False,                # not exposed in v1
        cost_fn=cost_fn,
        n_boot=spec.n_boot,
        fdr_q=spec.fdr_q,
        output_dir=output_dir,
        dedup_same_ticker=spec.dedup_same_ticker,
        dedup_window_days=spec.dedup_window_days,
        block_size_override=None,                 # not a spec concern
        allow_post_2020_explore=False,            # ALWAYS False (anti-p-hacking)
        fdr_ledger_path=fdr_ledger_path,
        min_peer_count=spec.min_peer_count,
    )

    return CompileResult(
        config=config,
        floors=spec.floors,
        # F389: run service reads dose_builder and dispatches to the matching
        # builder (build_r1_events or build_s1_events) via _DOSE_BUILDERS.
        dose_builder=spec.dose,
        stream_id=spec.stream,
    )
