"""PremiseStore — JSON persistence for research premises.

Mirrors the bot_manager.py / bots.json pattern exactly:
- atomic_write_text with backup_depth=1
- json.dumps(data, indent=2, default=str)
- load at startup, save on every mutation
- warn+skip on corrupt entries (missing premise_id or invalid/unknown status)
- raw dicts in memory (Pydantic validated at ingest only)

Failure modes on load():
  - CORRUPT FILE (top-level JSON parse error): raise ValueError naming the file.
    Silent-empty on whole-file failure would allow the next save() to overwrite
    recoverable data; backup_depth=1 (.bak) is the only disk safety net.
  - UNKNOWN SCHEMA VERSION: log a warning and continue (forward-migration risk).
  - CORRUPT ENTRY (missing premise_id or unknown status): log warning + skip entry.

State machine (7 states):
    draft
      → awaiting_formalization   (user saves raw idea; AI ready to formalize)
    awaiting_formalization
      → spec_ready               (AI wrote validated PremiseSpec + plain_summary)
    spec_ready / explored
      → exploring                (run triggered, job in flight)
    exploring
      → explored                 (run complete; verdict available)
      → spec_ready               (on failure — revert)
    explored
      → awaiting_confirm         (user decides to graduate; power_audit check — F389)
      → spec_ready               (revert if needed)
    awaiting_confirm
      → confirmed                (spec frozen, FDR ledger appended; terminal — F393)
      → explored                 (revert if power_audit fails)

    NOTE: draft → spec_ready is NOT a valid direct transition.
    The path is: draft → awaiting_formalization → spec_ready.

Illegal transitions raise ValueError("Cannot transition {current!r} → {target!r}").

DATA_PATH is a module-level str so tests can monkeypatch it:
    import research.premise_store as ps
    ps.DATA_PATH = str(tmp_path / "p.json")
    store = ps.PremiseStore()
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure backend/ is on sys.path regardless of how this module is imported.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fileutil import atomic_write_text  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data path (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------
DATA_PATH = str(Path(_BACKEND_DIR) / "data" / "premises.json")

# Schema version this code understands
_SUPPORTED_VERSION = 1

# ---------------------------------------------------------------------------
# Disposition set (F397)
# ---------------------------------------------------------------------------
_VALID_DISPOSITIONS: frozenset[str] = frozenset({
    "active",
    "parked_needs_data",
    "parked_sharpen",
    "rejected",
    "promising",
})


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
_TRANSITIONS: dict[str, set[str]] = {
    "draft":                  {"awaiting_formalization"},
    "awaiting_formalization": {"spec_ready", "draft"},           # revert if AI fails
    "spec_ready":             {"exploring", "awaiting_formalization"},  # re-formalize
    "exploring":              {"explored", "spec_ready"},         # spec_ready on failure
    "explored":               {"spec_ready", "awaiting_confirm"},
    "awaiting_confirm":       {"confirmed", "explored"},          # revert if power_audit fails
    "confirmed":              set(),                              # terminal
}

_ALL_STATES: frozenset[str] = frozenset(_TRANSITIONS.keys())


def _now_utc() -> str:
    """ISO 8601 UTC timestamp string."""
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# PremiseStore
# ---------------------------------------------------------------------------

class PremiseStore:
    """JSON-backed store for research premises.

    In-memory representation: dict[premise_id, raw_dict].
    Pydantic validation happens only at add_spec() ingest time.
    """

    def __init__(self) -> None:
        self.premises: dict[str, dict] = {}
        self.load()

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def save(self) -> None:
        """Atomically persist the current store state to DATA_PATH."""
        data = {"version": _SUPPORTED_VERSION, "premises": list(self.premises.values())}
        atomic_write_text(
            DATA_PATH,
            json.dumps(data, indent=2, default=str),
            backup_depth=1,
        )

    def load(self) -> None:
        """Load premises from DATA_PATH.

        Failure modes:
        - File absent: no-op (first run).
        - Top-level JSON parse error: raises ValueError naming the file.
          Silent-empty is dangerous — the next save() would overwrite recoverable
          data.  The .bak file remains intact for manual recovery.
        - Unknown/newer schema version: log warning, continue loading.
        - Missing premise_id: warn + skip entry.
        - Invalid/unknown status field: warn + skip entry.
        """
        if not os.path.exists(DATA_PATH):
            return
        try:
            with open(DATA_PATH, encoding="utf-8") as f:
                raw = f.read()
        except OSError as exc:
            raise ValueError(
                f"premise_store.load: cannot read {DATA_PATH}: {exc}"
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"premise_store.load: {DATA_PATH} is not valid JSON — "
                f"file may be truncated or corrupt. "
                f"Recover from {DATA_PATH}.bak if present. Detail: {exc}"
            ) from exc

        # F12: schema-version guard
        file_version = data.get("version", 1)
        if file_version != _SUPPORTED_VERSION:
            log.warning(
                "premise_store.load: file version %r differs from supported %r — "
                "forward-migration may be required; loading anyway",
                file_version,
                _SUPPORTED_VERSION,
            )

        for p in data.get("premises", []):
            pid = p.get("premise_id")
            if not pid:
                log.warning("premise_store.load: skipping entry without premise_id")
                continue
            # F8: validate status field
            status = p.get("status")
            if status not in _ALL_STATES:
                log.warning(
                    "premise_store.load: premise %r has invalid/unknown status %r, skipping",
                    pid,
                    status,
                )
                continue
            # F397: lazy-default new disposition fields for existing premises
            p.setdefault("disposition", "active")
            p.setdefault("disposition_note", "")
            p.setdefault("derived_from", None)
            self.premises[pid] = p

    # -----------------------------------------------------------------------
    # Mutation API
    # -----------------------------------------------------------------------

    def add_premise(self, premise_text: str) -> str:
        """Create a new premise in 'draft' state.  Returns premise_id."""
        pid = f"p-{uuid.uuid4().hex[:8]}"
        now = _now_utc()
        new_entry = {
            "premise_id": pid,
            "created_at": now,
            "updated_at": now,
            "status": "draft",
            "premise_text": premise_text,
            "spec": None,
            "spec_history": [],
            "run_history": [],
            "error_note": None,
            # F397: disposition metadata
            "disposition": "active",
            "disposition_note": "",
            "derived_from": None,
        }
        # F11: snapshot-before-mutate pattern
        prior = copy.deepcopy(self.premises)
        self.premises[pid] = new_entry
        try:
            self.save()
        except Exception:
            self.premises = prior
            raise
        return pid

    def add_spec(self, premise_id: str, spec_dict: dict) -> None:
        """Validate and attach a PremiseSpec to a premise.

        Validates via PremiseSpec(**spec_dict); raises ValueError on invalid
        spec without mutating the store.

        Refuses to replace a spec on a confirmed (terminal) premise — overwriting
        a confirmed spec after FDR ledger append would corrupt multiplicity
        accounting.

        Does NOT automatically transition state — caller transitions to
        spec_ready after attaching the spec.

        Version accounting (clean append-only audit trail):
          - First add_spec: current spec = v1, spec_history = [] (nothing archived yet).
          - Subsequent add_spec: archives the prior spec as {version: N, note: 'archived'},
            then sets the new spec as the current version N+1.
          - spec_hash is intentionally not set here; it is assigned at confirm-freeze
            (F389), not at add_spec time — the field is None until the premise is
            confirmed.
        """
        # Inline import to avoid circular import at module load
        from research.premise_spec import PremiseSpec  # noqa: E402

        p = self._get(premise_id)

        # F6: refuse on terminal state
        if p.get("status") == "confirmed":
            raise ValueError(
                f"Cannot replace spec on a confirmed premise {premise_id!r}. "
                f"Confirmed is terminal (FDR ledger appended)."
            )

        # Validate via Pydantic — raises ValidationError if invalid; store not mutated
        validated = PremiseSpec(**spec_dict)

        now = _now_utc()

        # F5: correct version accounting — use an explicit spec_version counter stored
        # on the premise dict so the current version is always known without counting
        # history entries (which are only written on replacement, not on first set).
        #   - p["spec_version"] is absent before the first add_spec call.
        #   - After the N-th add_spec call, p["spec_version"] == N.
        #   - On each call we archive the current spec at its known version, then
        #     bump the counter.
        current_version = p.get("spec_version", 0)  # 0 means no spec yet
        next_version = current_version + 1

        # F11: snapshot-before-mutate
        prior_premises = copy.deepcopy(self.premises)

        try:
            # Archive the current spec (if any) before replacing
            if p.get("spec") is not None and current_version > 0:
                p.setdefault("spec_history", []).append({
                    "version": current_version,
                    "spec": p["spec"],
                    "at": p.get("updated_at", now),
                    "note": "archived",
                })

            p["spec"] = validated.model_dump()
            p["spec_version"] = next_version
            p["updated_at"] = now
            self.save()
        except Exception:
            self.premises = prior_premises
            raise

    def transition(self, premise_id: str, new_state: str) -> None:
        """Apply a state transition; raises ValueError if illegal."""
        p = self._get(premise_id)
        current = p["status"]

        if new_state not in _ALL_STATES:
            raise ValueError(
                f"Unknown state {new_state!r}. Valid states: {sorted(_ALL_STATES)}."
            )

        allowed = _TRANSITIONS.get(current, set())
        if new_state not in allowed:
            raise ValueError(
                f"Cannot transition {current!r} → {new_state!r}. "
                f"Legal transitions from {current!r}: {sorted(allowed) if allowed else '(terminal)'}."
            )

        # F11: snapshot-before-mutate
        prior = copy.deepcopy(self.premises)
        p["status"] = new_state
        p["updated_at"] = _now_utc()
        try:
            self.save()
        except Exception:
            self.premises = prior
            raise

    def append_run(self, premise_id: str, run_record: dict) -> None:
        """Append a run result dict to the premise's run_history."""
        p = self._get(premise_id)
        # F11: snapshot-before-mutate
        prior = copy.deepcopy(self.premises)
        p.setdefault("run_history", []).append(run_record)
        p["updated_at"] = _now_utc()
        try:
            self.save()
        except Exception:
            self.premises = prior
            raise

    def set_error_note(self, premise_id: str, note: Optional[str]) -> None:
        """Set (or clear) an error note on a premise."""
        p = self._get(premise_id)
        # F11: snapshot-before-mutate
        prior = copy.deepcopy(self.premises)
        p["error_note"] = note
        p["updated_at"] = _now_utc()
        try:
            self.save()
        except Exception:
            self.premises = prior
            raise

    def set_disposition(self, premise_id: str, disposition: str, note: str = "") -> None:
        """Set disposition + optional note on a premise (F397).

        Allowed in any state (disposition is metadata, not a run state).
        Raises ValueError if disposition not in _VALID_DISPOSITIONS.
        Caps note at 2000 chars.
        """
        if disposition not in _VALID_DISPOSITIONS:
            raise ValueError(
                f"Unknown disposition {disposition!r}. "
                f"Valid values: {sorted(_VALID_DISPOSITIONS)}."
            )
        note = note[:2000]
        p = self._get(premise_id)
        # F11: snapshot-before-mutate
        prior = copy.deepcopy(self.premises)
        p["disposition"] = disposition
        p["disposition_note"] = note
        p["updated_at"] = _now_utc()
        try:
            self.save()
        except Exception:
            self.premises = prior
            raise

    def duplicate_premise(self, premise_id: str) -> str:
        """Clone premise_text + spec into a new draft, setting derived_from (F397).

        The original premise is NOT modified.
        Returns the new premise_id.
        """
        original = self._get(premise_id)
        new_pid = f"p-{uuid.uuid4().hex[:8]}"
        now = _now_utc()
        new_entry = {
            "premise_id": new_pid,
            "created_at": now,
            "updated_at": now,
            "status": "draft",
            "premise_text": original.get("premise_text", ""),
            "spec": copy.deepcopy(original.get("spec")),
            "spec_history": [],
            "run_history": [],
            "error_note": None,
            # F397
            "disposition": "active",
            "disposition_note": "",
            "derived_from": premise_id,
        }
        # H2: set spec_version = 1 on the clone (F416 fix).  The copied spec is
        # the clone's founding version 1.  Setting 0 would cause the first
        # add_spec call to see "no spec yet" (0 means no spec), silently skip
        # archiving the founding copy, and overwrite it without a history record.
        # With spec_version=1, add_spec correctly archives the founding copy at
        # version 1 and writes version 2 on first edit — preserving lineage.
        if original.get("spec") is not None:
            new_entry["spec_version"] = 1
            # R-8: always clear spec_hash from the cloned spec so a derived premise
            # never carries the parent's frozen hash.  This prevents a spurious 409
            # "duplicate structural spec" when the descendant tries to graduate.
            if isinstance(new_entry.get("spec"), dict):
                new_entry["spec"].pop("spec_hash", None)
        # F11: snapshot-before-mutate
        prior = copy.deepcopy(self.premises)
        self.premises[new_pid] = new_entry
        try:
            self.save()
        except Exception:
            self.premises = prior
            raise
        return new_pid

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _get(self, premise_id: str) -> dict:
        """Retrieve a premise dict; raises KeyError if not found."""
        if premise_id not in self.premises:
            raise KeyError(f"Premise not found: {premise_id!r}")
        return self.premises[premise_id]


# ---------------------------------------------------------------------------
# Module-level helper: derive machine outcome from run_history (F397)
# ---------------------------------------------------------------------------

def derive_machine_outcome(premise_dict: dict) -> str:
    """Derive a one-line machine outcome string from a premise dict.

    Based on the latest run_history entry. Defensive against missing keys.

    Returns:
      "—"                              — no runs yet
      "failed: <error_note>"           — run failed or had an error_note
      "UNTESTABLE — N events"          — verdict present, UNTESTABLE decision
      "<explore_decision> · <stat>"   — explored with a verdict
    """
    run_history = premise_dict.get("run_history") or []
    # Filter to actual run entries (have run_type + verdict key)
    run_entries = [
        r for r in run_history
        if isinstance(r, dict) and "run_type" in r and "verdict" in r
    ]
    if not run_entries:
        return "—"

    latest = run_entries[-1]
    verdict = latest.get("verdict")
    error_note = latest.get("error")

    # Run-level failure
    if latest.get("status") in ("failed", "error") or error_note:
        msg = error_note or "unknown error"
        # Truncate to keep the one-liner manageable
        return f"failed: {str(msg)[:80]}"

    # No verdict dict
    if not isinstance(verdict, dict):
        if verdict is None and error_note is None:
            return "—"
        return "—"

    explore_decision = verdict.get("explore_decision") or ""
    n_valid = verdict.get("n_valid_events")

    # UNTESTABLE path
    if explore_decision and "UNTESTABLE" in str(explore_decision).upper():
        n_str = str(n_valid) if n_valid is not None else "?"
        return f"UNTESTABLE — {n_str} events"

    # Failed verdict (no explore_decision but error-like note in verdict)
    verdict_note = verdict.get("note") or ""
    if not explore_decision and verdict_note:
        return f"failed: {str(verdict_note)[:80]}"

    if not explore_decision:
        return "—"

    # Explored with a verdict — add a short stat
    mde = verdict.get("mde_q5q1_pp")
    if isinstance(mde, (int, float)):
        stat = f"MDE {mde:.2f}pp"
    elif n_valid is not None:
        stat = f"{n_valid} events"
    else:
        stat = ""

    if stat:
        return f"{explore_decision} · {stat}"
    return explore_decision
