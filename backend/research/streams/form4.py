"""Form 4 event stream for the StrategyLab research workbench.

Implements the Stream protocol using iter_form4_events (bare filing events:
form_type / accession / filing_date).  Dose-enriched events (score, D, k, MC)
are wired in F389 via build_r1_events — that path requires XML cache paths not
available at spec/compile time.

OVERRIDE 2 (orchestrator): FORM4_FILTER_VOCAB and FORM4_DOSE_VOCAB contain
ONLY keys that are actually implemented and produce a real test.  No stub /
unimplemented keys.  Extending vocabulary is a deliberate reviewed change.

F389 TODO: wire build_r1_events for dose="r1_score" at run time.
"""
from __future__ import annotations

import sys
import os
from datetime import date
from typing import Iterator, Optional

# Ensure backend/ is on sys.path regardless of how this module is imported.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from research.event_study import EventRecord, iter_form4_events  # noqa: E402

# ---------------------------------------------------------------------------
# Vocabulary constants
# ---------------------------------------------------------------------------

# Filter vocabulary: keys that event_filter may reference.
# ONLY keys that are implemented in iter_events (or documented F389 TODO).
# Per OVERRIDE 2: no declared-but-unimplemented stubs.
#
# Currently implemented at iter_events layer (F388):
#   form_types — filters on form_type in {"4","4/A"}
#
# Declared but applied at dose time (F389 TODO, not event-level):
#   transaction_codes — transactionCode == "P" etc. (r1_dose concern)
#   min_dollar_total   — total D threshold (r1_dose concern)
#   exclude_10b51      — 10b5-1 exclusion policy (r1_dose concern)
#
# All four keys are VALIDATED at spec-creation time so a spec referencing
# an undeclared key fails at validation, never at run time.  Their
# event-level application is a documented F389 TODO.
FORM4_FILTER_VOCAB: frozenset[str] = frozenset({
    # Form type filter (4 vs 4/A) — implemented in iter_events (F388)
    "form_types",        # list[str], e.g. ["4", "4/A"] — default both

    # --- F389 TODO: dose-time concerns — declared for validation, not yet applied at event level ---
    # Transaction code filter (r1_dose hardcodes "P"; declared for future premises)
    "transaction_codes",  # list[str], e.g. ["P"] — open-market purchases

    # Dollar threshold for total D in dose window
    "min_dollar_total",   # float >= 0 (r1_dose _PERTURB_FLOORS philosophy)

    # 10b5-1 exclusion policy (r1_dose excludes by default)
    "exclude_10b51",      # bool, default True
})

# Dose vocabulary: keys that dose_params may reference.
# Drawn strictly from r1_dose.py constants (_W_PRIMARY, _BETA).
FORM4_DOSE_VOCAB: frozenset[str] = frozenset({
    # Dose window length in business days (r1_dose._W_PRIMARY = 21)
    "window_bdays",   # int, default 21

    # Distinct-insider weight beta (r1_dose._BETA = 0.5)
    # Formula: log1p(D/MC) * (1 + beta * k)
    "beta",           # float, default 0.5
})

# Valid form types accepted by iter_form4_events
_VALID_FORM_TYPES: frozenset[str] = frozenset({"4", "4/A"})


# ---------------------------------------------------------------------------
# Stream implementation
# ---------------------------------------------------------------------------

class Form4Stream:
    """Stream backed by iter_form4_events (bare Form 4 / 4A filings).

    iter_events applies the form_types filter at the event layer (F388).
    Dose-related filter keys (transaction_codes, min_dollar_total,
    exclude_10b51) are declared in FORM4_FILTER_VOCAB and validated at
    spec-creation time, but their application is deferred to the F389
    run service where build_r1_events has access to XML cache paths.
    """

    @property
    def stream_id(self) -> str:
        return "form4"

    def iter_events(
        self,
        start: date,
        end: date,
        universe: Optional[list[str]] = None,
        *,
        event_filter: Optional[dict] = None,
    ) -> Iterator[EventRecord]:
        """Yield EventRecord objects for Form 4 / 4A filings in [start, end].

        Parameters
        ----------
        start, end : inclusive date range.
        universe   : list of ticker symbols to restrict to; None = all CIKs.
        event_filter : validated predicates from PremiseSpec.event_filter.
            Implemented here: form_types.
            F389 TODO: transaction_codes, min_dollar_total, exclude_10b51
            are dose-time concerns and are NOT applied at this layer.
        """
        ef = event_filter or {}

        # Resolve form_types filter (F388-implemented)
        allowed_form_types: frozenset[str] = _VALID_FORM_TYPES
        if "form_types" in ef:
            requested = frozenset(ef["form_types"])
            unknown = requested - _VALID_FORM_TYPES
            if unknown:
                raise ValueError(
                    f"form_types contains unknown values {sorted(unknown)}. "
                    f"Allowed: {sorted(_VALID_FORM_TYPES)}."
                )
            allowed_form_types = requested

        # F389 TODO: transaction_codes, min_dollar_total, exclude_10b51
        # are recorded in CompileResult.dose_builder="r1_score" for F389
        # to apply via build_r1_events.  They are not filtered here.

        for event in iter_form4_events(
            start=start,
            end=end,
            ticker_list=universe,  # None → all CIKs
        ):
            # Apply form_types filter
            if event.payload.get("form_type") not in allowed_form_types:
                continue
            yield event

    def filter_vocabulary(self) -> frozenset[str]:
        """Declared keys that event_filter may reference."""
        return FORM4_FILTER_VOCAB

    def dose_vocabulary(self) -> frozenset[str]:
        """Declared keys that dose_params may reference."""
        return FORM4_DOSE_VOCAB


# ---------------------------------------------------------------------------
# Auto-registration at module load (required by streams/__init__.py D2)
# ---------------------------------------------------------------------------
from research.streams import register  # noqa: E402
register(Form4Stream())
