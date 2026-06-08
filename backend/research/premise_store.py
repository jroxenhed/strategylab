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

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _get(self, premise_id: str) -> dict:
        """Retrieve a premise dict; raises KeyError if not found."""
        if premise_id not in self.premises:
            raise KeyError(f"Premise not found: {premise_id!r}")
        return self.premises[premise_id]
