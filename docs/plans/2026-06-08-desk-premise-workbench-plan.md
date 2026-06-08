# Desk Premise Workbench — Implementation Plan

**Date:** 2026-06-08
**Design spec:** [docs/superpowers/specs/2026-06-08-desk-premise-workbench-design.md](../superpowers/specs/2026-06-08-desk-premise-workbench-design.md)
**Executed via:** the StrategyLab orchestrator cycle (CLAUDE.local.md + orchestrator-playbook.md), not the superpowers writing-plans skill (John's call, 2026-06-08).

## Shape

Three dependent phases, each a TODO F-item. Backend-first so the frontend wires to real endpoints (no mock-data throwaway). The agent-operator (option A) needs no separate phase — it consumes the same endpoints the UI does (agent-native parity); a short runbook ships with Phase 2.

```
F388 (data model + stream registry)  →  F389 (run service + explore/confirm gate)  →  F390 (Desk tab + Premises workbench UI)
        [arch] backend                        [arch] backend                                [arch] frontend
```

Each phase is independently shippable and verifiable. Do not start a phase until its predecessor's gate passes. Within a phase, dispatch backend/frontend impl agents in parallel only when target files don't overlap.

---

## F388 — PremiseSpec data model + stream registry (backend)

**Goal:** the bounded artifact + the extensibility seam, with no UI and no runs yet. Everything downstream compiles from this.

**New files (proposed — confirm at explore step):**
- `backend/research/premise_spec.py` — `PremiseSpec` Pydantic model + validator (vocab-bounded), `spec_hash()`.
- `backend/research/streams/__init__.py` — `Stream` protocol + registry; `payload vocabulary` declaration type.
- `backend/research/streams/form4.py` — first stream, wraps existing `iter_form4_events`; declares its filter/dose vocabulary.
- `backend/research/premise_compile.py` — pure `spec → EventStudyConfig` compiler.
- `backend/research/premise_store.py` — JSON persistence + state-machine (`draft → awaiting_formalization → spec_ready → exploring → explored → awaiting_confirm → confirmed`).

**Tasks:**
1. `PremiseSpec` fields per design §3.1; validator rejects out-of-vocabulary `stream`/`event_filter`/`dose`. `spec_hash` is content hash, stable across field order.
2. `Stream` protocol: `iter_events(start, end, universe)` + declared vocabulary. Registry lookup by id.
3. `form4` stream wraps `iter_form4_events`; vocabulary covers transaction codes + dollar/size predicates already used by `r1_dose`.
4. Compiler maps a spec to `EventStudyConfig` (horizons, entry_lag_days, dedup, floors); dose builder selected from vocabulary.
5. Premise store: load/save (atomic, like `bots.json`), state transitions, version history.

**Verification (Tier B — additive backend, contract surface):**
- Pydantic: valid spec compiles; out-of-vocab spec rejected.
- **F338 anchor:** `form4` stream round-trips real cached data with pre-stated anchors before its output is believed (known-window probe, sane counts).
- Compiler equivalence: reproduce an existing R-1 `EventStudyConfig` from an equivalent spec (assert field-equality).
- Determinism: `spec_hash` stable; same spec → same config.
- State machine: legal transitions only; illegal transition raises.

**Review personas:** `correctness` + `kieran-python-reviewer`; add `data-integrity-guardian` (persisted store).

---

## F389 — Run service + explore/confirm gate (backend)

**Goal:** run a compiled spec, enforce the discipline boundary, expose the endpoints the UI and agent both use.

**Depends on:** F388.

**New/changed files (proposed):**
- `backend/research/premise_run.py` — fast-preview (reduced universe/window) + full-explore (dispatch to worker) async jobs; job state; sentinel-based completion.
- `backend/routes/premises.py` — FastAPI router: premise CRUD, formalize-ingest (validated), trigger-run, run-status, fetch-verdict, graduate-to-confirm. Registered in `shared.py`.
- `docs/desk-agent-operator-runbook.md` — how the attended Claude session pulls `awaiting_formalization` / `awaiting_eval` work (the option-A operator contract).

**Tasks:**
1. Fast preview: reduced universe + shorter window, runs locally, seconds-to-minutes, labelled non-verdict.
2. Full explore: `bin/worker-dispatch.sh` via `bin/worker-probe.sh` (F387) target selection; completion read via `worker-status.sh` sentinel; `≤2020` hard guard asserted.
3. **Explore/confirm gate:** explore runs NOT logged to FDR ledger; `graduate-to-confirm` freezes+hashes spec, runs `power_audit.py` pre-check (fail → block), runs once on the sealed window, **appends to `fdr_ledger.json`**. Confirm idempotent by `spec_hash` (re-run of a frozen hash refused).
4. Endpoints are agent-native: same surface the UI calls; documented in the runbook.

**Verification (Tier C — touches FDR ledger / persisted research state + worker dispatch):**
- Gate: explore never touches the ledger; confirm appends exactly once; underpowered design blocked by power_audit.
- Confirm idempotency by hash (no double-logging).
- Worker path: dispatch + sentinel poll; LOCAL fallback when `worker-probe` reports none.
- Run failure returns premise to `spec_ready` with error note (no silent empty).
- **Inline carve-out:** anything writing the real `fdr_ledger.json` uses a tmp ledger path in tests (engine already supports `fdr_ledger_path`) — never pollute the real ledger.

**Review personas:** `correctness` + `reliability` + `data-integrity-guardian` + `data-migration-expert` (FDR ledger is append-only persistent research state) + `kieran-python-reviewer`.

---

## F390 — Desk tab + Premises workbench UI (frontend)

**Goal:** the plain-English workbench, wired to F389 endpoints.

**Depends on:** F389.

**New/changed files (proposed):**
- `frontend/src/App.tsx` — add `'desk'` to `AppTab`; tab button; mounted-via-display panel (F152 pattern).
- `frontend/src/features/desk/Desk.tsx` — subpane tabs (Premises active; Inbox/Playbooks/Tracking stubbed "later").
- `frontend/src/features/desk/PremiseLibrary.tsx` — master list + status chips.
- `frontend/src/features/desk/PremiseDetail.tsx` — the plain-English loop: idea input (free-text + guided prompts), readback, run controls (fast preview / full explore), verdict panel, graduate gate.
- `frontend/src/api/premises.ts` — typed client for the F389 endpoints.

**Tasks:**
1. Desk tab + subpane scaffold; stubs clearly labelled "later" (no mock pretending to be real).
2. Master-detail per design §3.7 mockup (ui-mockup-v2): plain-English-first, technical spec behind a "show runnable details" fold.
3. Input = free-text AND guided prompts (D7); plain-English readback is the trust anchor (the confirm-it-got-you-right surface).
4. Run+poll reuses the `ValidationRunPanel` pattern; verdict rendered plain-English; graduate-gate is a deliberate, confirmation-gated action.

**Verification (Tier B — frontend, contract via typed client):**
- `npm run build` (not `tsc --noEmit` — verbatimModuleSyntax).
- Render-probe via `bin/verify-batch.sh`: Desk tab mounts, Premises master-detail renders, fold toggles.
- **Live-browser only if** the plain-English loop's interaction is judged first-of-its-kind per live-browser-verification.md "When to run" — default is scripted probe + code reasoning.

**Review personas:** `correctness` + `kieran-typescript-reviewer`; add `agent-native-reviewer` (verify the UI calls the same endpoints an agent would — parity).

---

## Cross-cutting notes
- **No new backtest semantics.** The workbench only builds `EventStudyConfig` inputs; the engine (`event_study.py`) is unchanged. Any temptation to special-case the engine for the workbench is out of scope.
- **F338 gate is mandatory for every new stream** added after `form4` — real-data smoke with pre-stated anchors before belief.
- **Deferred (not this plan):** live poller, paper-tracking, conviction scoring, inbox UI, bot graduation, Qwen/tmux autonomous producers, AI freeform codegen.
- **Expected refinements** (John will surface by testing v1): guided-prompt set, dose-vocabulary friendliness, fast-preview universe definition, verdict iteration-history surfacing. None block v1.
