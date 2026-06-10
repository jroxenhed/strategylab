"""PremiseSpec — research premise data model for the StrategyLab workbench.

A PremiseSpec fully describes a statistical event-study test: which stream to
draw events from, how to filter them, which dose formula to apply, and what
EventStudyConfig parameters to use.  It is the spec object passed through the
research pipeline (formalization → compile → run → store).

spec_hash()
-----------
Hashes STRUCTURAL fields ONLY (the fields that define the statistical test):
    stream, event_filter, dose, dose_params, horizons, entry_lag_days,
    dedup_same_ticker, dedup_window_days, direction, floors, min_peer_count,
    fdr_q, n_boot.

Excluded from the hash:
    - premise_text  : prose provenance; rewording must not mint a new test
    - guided        : scaffolded Q&A input; narrative, not structural
    - plain_summary : AI readback; narrative, not structural
    - spec_hash     : the field being computed; excluded to avoid circularity

Rationale: two identically-structured specs with differently-worded summaries
ARE the same test for FDR multiplicity accounting.  The hash identifies the
test design, not the human description.

Tuples serialize as JSON arrays in the canonical payload; the hash is stable
across Python versions for all primitive types used here.
"""
from __future__ import annotations

import hashlib
import json
import sys
import os
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Ensure backend/ is on sys.path regardless of how this module is imported.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class GuidedAnswers(BaseModel):
    """Optional scaffolded Q&A fields captured during premise formalization."""
    trigger: Optional[str] = None          # "what event triggers this"
    stronger_when: Optional[str] = None    # "when does the edge strengthen"
    hold_length: Optional[str] = None      # "how long to hold"
    direction: Optional[str] = None        # "long or short"


class UniverseFloors(BaseModel):
    """Universe quality filters applied per ticker before EventStudyConfig.

    These map to universe_floors.MIN_PRICE and MIN_AVG_VOLUME but are not
    fields of EventStudyConfig — the run service (F389) applies them via
    universe_floors.passes_floors(df, as_of) when building the universe.
    CompileResult.floors carries them to the run service.

    max_market_cap (F395): if set, events with MC > max_market_cap are excluded
    by the dose builder. Applied inside build_s1_events() (and future builders)
    where MC is already computed. floor_status()/passes_floors() are NOT modified
    — they can't compute MC without a CIK lookup (see brief §1.5 / D2).
    None = no ceiling (default, r1 specs unaffected).
    """
    min_price: float = 5.0          # last close >= 5.0 (universe_floors.MIN_PRICE)
    min_avg_volume: int = 500_000   # trailing 63-td mean share volume (MIN_AVG_VOLUME)
    max_market_cap: Optional[float] = None  # None = no ceiling; e.g. 10_000_000_000 for $10B


# ---------------------------------------------------------------------------
# Structural field set (used by spec_hash)
# ---------------------------------------------------------------------------
# These are the ONLY fields that define the statistical test.  Any change here
# produces a new hash.  Prose fields are excluded (see module docstring).
_STRUCTURAL_FIELDS = frozenset({
    "stream",
    "event_filter",
    "dose",
    "dose_params",
    "horizons",
    "entry_lag_days",
    "dedup_same_ticker",
    "dedup_window_days",
    "direction",
    "floors",
    "min_peer_count",
    "fdr_q",
    "n_boot",
})

# Non-structural fields excluded from the hash.
_NON_STRUCTURAL_FIELDS = frozenset({
    "premise_text",
    "guided",
    "plain_summary",
    "spec_hash",
})

# Registered dose formula ids — single source of truth shared with premise_compile.
# premise_compile.py imports _VALID_DOSES and asserts _VALID_DOSES ⊆ _COST_FN_BY_DOSE
# at module load so adding a dose here without a cost_fn immediately fails loud.
_VALID_DOSES: frozenset[str] = frozenset({"r1_score", "s1_score"})


# ---------------------------------------------------------------------------
# PremiseSpec
# ---------------------------------------------------------------------------

class PremiseSpec(BaseModel):
    """Full spec for one statistical event-study test.

    Validated at creation time:
    - stream must be a registered stream id (ledger enforcement)
    - dose must be a registered dose formula id (ledger enforcement)
    - event_filter keys must be in the stream's filter_vocabulary() (ledger enforcement)
    - dose_params keys must be in the stream's dose_vocabulary() (ledger enforcement)

    Frozen: structural fields cannot be mutated after construction, preventing
    stale spec_hash values (spec_hash is assigned at confirm-freeze, not at
    add_spec time — see F389).
    """
    model_config = ConfigDict(frozen=True)

    # --- Provenance (excluded from spec_hash) ---
    premise_text: str                         # the plain-English idea (required)
    guided: Optional[GuidedAnswers] = None    # optional scaffolded Q&A fields
    plain_summary: Optional[str] = None       # AI's plain-English readback

    # --- Stream selection (vocab-bounded) ---
    stream: str = Field(default="form4", validate_default=True)  # registered stream id

    # --- Event filter (bounded predicates over stream's filter vocabulary) ---
    event_filter: dict[str, Any] = {}         # keys must be in filter_vocabulary()

    # --- Dose config (vocab-bounded) ---
    dose: str = "r1_score"                    # registered dose formula id
    dose_params: dict[str, Any] = {}          # keys must be in dose_vocabulary()

    # --- Study config fields (map to EventStudyConfig) ---
    horizons: tuple[int, ...] = (21, 63, 126)
    entry_lag_days: int = 1
    dedup_same_ticker: bool = True
    dedup_window_days: int = 30               # R-1 charter amendment: 30 calendar days
    direction: Literal["long", "short"] = "long"
    floors: UniverseFloors = UniverseFloors()
    min_peer_count: int = 8                   # R-1 charter: 8 (not engine default 5)
    fdr_q: float = 0.10
    n_boot: int = 999

    # --- Identity (set only at confirm-freeze) ---
    spec_hash: Optional[str] = None           # content hash; None until frozen

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator("horizons")
    @classmethod
    def _validate_horizons(cls, v: tuple) -> tuple:
        if len(v) == 0:
            raise ValueError(
                "horizons must be non-empty. "
                "Provide at least one horizon (e.g. horizons=(21, 63, 126)). "
                "Ledger enforcement: an empty horizons tuple would cause max() to raise "
                "at run time and silently fail the study."
            )
        return v

    @field_validator("stream")
    @classmethod
    def _validate_stream(cls, v: str) -> str:
        from research.streams import _REGISTRY
        if v not in _REGISTRY:
            raise ValueError(
                f"Unknown stream {v!r}. Registered: {sorted(_REGISTRY)}. "
                f"Ledger enforcement: only registered streams accepted."
            )
        return v

    @field_validator("dose")
    @classmethod
    def _validate_dose(cls, v: str) -> str:
        if v not in _VALID_DOSES:
            raise ValueError(
                f"Unknown dose formula {v!r}. Allowed: {sorted(_VALID_DOSES)}. "
                f"Ledger enforcement: only registered dose formulas accepted."
            )
        return v

    @model_validator(mode="after")
    def _validate_event_filter(self) -> "PremiseSpec":
        from research.streams import _REGISTRY
        # Unconditional lookup: field_validator guarantees stream is registered.
        # If registry is empty (test monkeypatch or import-order bug), this raises
        # immediately rather than silently skipping — fail loud, not silent.
        stream = _REGISTRY[self.stream]
        vocab = stream.filter_vocabulary()
        for key in self.event_filter:
            if key not in vocab:
                raise ValueError(
                    f"event_filter key {key!r} not in {self.stream} vocabulary "
                    f"{sorted(vocab)}. Ledger enforcement."
                )
        return self

    @model_validator(mode="after")
    def _validate_dose_params(self) -> "PremiseSpec":
        from research.streams import _REGISTRY
        # Unconditional lookup: field_validator guarantees stream is registered.
        # If registry is empty (test monkeypatch or import-order bug), this raises
        # immediately rather than silently skipping — fail loud, not silent.
        stream = _REGISTRY[self.stream]
        vocab = stream.dose_vocabulary()
        for key in self.dose_params:
            if key not in vocab:
                raise ValueError(
                    f"dose_params key {key!r} not in {self.stream} dose vocabulary "
                    f"{sorted(vocab)}. Ledger enforcement."
                )
        return self


# ---------------------------------------------------------------------------
# spec_hash — hashes structural fields only
# ---------------------------------------------------------------------------

def _normalize_value(v: Any) -> Any:
    """Normalize a value for canonical hashing.

    Rules:
    - Floats that are integer-valued (21.0) → int (21), so 21 and 21.0 hash
      identically.  Applies recursively to list elements.
    - Lists of scalars → sorted, so ["P","S"] and ["S","P"] hash identically
      (filter/dose lists represent unordered sets of predicates).
    - Dicts → recursively normalize values (sort_keys handled by json.dumps).
    - Other types pass through unchanged.
    """
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        normalized = [_normalize_value(e) for e in v]
        # Sort if all elements are scalars (str/int/float/bool) — order-independent
        try:
            return sorted(normalized, key=lambda x: (type(x).__name__, x))
        except TypeError:
            return normalized  # non-sortable heterogeneous list: leave order as-is
    if isinstance(v, dict):
        return {k: _normalize_value(vv) for k, vv in v.items()}
    return v


def spec_hash(spec: PremiseSpec) -> str:
    """Compute a stable 16-char hex hash of spec's structural fields.

    Hashes ONLY the fields that define the statistical test (stream,
    event_filter, dose, dose_params, horizons, entry_lag_days,
    dedup_same_ticker, dedup_window_days, direction, floors, min_peer_count,
    fdr_q, n_boot).

    Excluded: premise_text, guided, plain_summary, spec_hash.
    Rationale: two specs with identical structure but different prose ARE the
    same test for FDR multiplicity accounting.  Rewording must not mint a new
    test id.

    Canonicalization:
    - Integral floats (21.0) and ints (21) hash identically.
    - Lists of scalars are sorted before hashing (order-independent predicates).
    - sort_keys=True on json.dumps for dict key order.

    NOTE: spec_hash is intentionally assigned at confirm-freeze (F389), not at
    add_spec time.  The field is None until the premise reaches the confirmed
    state.

    Tuples serialize as JSON arrays (stable); hash is stable across Python
    versions for all primitive types used here.
    """
    full = spec.model_dump()
    payload = {k: v for k, v in full.items() if k in _STRUCTURAL_FIELDS}
    normalized = {k: _normalize_value(v) for k, v in payload.items()}
    canonical = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
