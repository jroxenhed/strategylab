# The Desk — Premise Workbench (v1 design)

**Date:** 2026-06-08
**Status:** Design approved (brainstorm), pending implementation plan
**Author:** John + Claude (brainstorming session)

## 1. Goal

Build the first stage of **"The Desk"** (the locked product surface for the research program): a **premise test workbench**. A non-expert authors a trading premise in plain English; an AI translates it into a real, runnable event-study test; it backtests on cached data; the results come back in plain English; the user iterates. Surviving premises graduate — under the program's pre-registration discipline — into confirmed playbooks.

This is the front-end of the **explore mill** that `docs/research/PROGRAM.md` already predicts ("once the explore mill exists, charter candidates come from mill survivors across all families").

### What this is NOT (deferred to later rungs)
- **Not** the live event inbox / paper-tracking / conviction scoring. Those are later rungs of the graduation ladder (`IDEAS.md` "The Desk") and depend on a first confirmed playbook.
- **Not** a new backtest engine. It builds inputs for the existing `backend/research/event_study.py` (F342) harness; it adds no new backtest semantics.
- **Not** AI codegen. The AI fills a bounded schema; it never writes arbitrary dose-builder code (deferred "advanced mode").

## 2. Decisions locked in brainstorming

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | First stage = **premise workbench**, not the live inbox | The inbox is "worthless until a charter confirms" (IDEAS.md); the workbench is what *produces* charters and is charter-independent. |
| D2 | Workbench is **playbook-agnostic** | User wanted >1 playbook; the right generalization is a generic premise layer, not hardcoding R-1/R-2. (R-2 specifically is a poor seed: no scorer, F369 says untestable, F372 shelve-decision pending.) |
| D3 | AI role = **bounded charter co-author** | Expressive enough for real premises, but output is a vocab-bounded spec — can't smuggle in arbitrary freedom. Fits the frozen-grid/FDR discipline. |
| D4 | AI invocation = **agent-as-operator** (option A) | Max subscription has no $0 programmatic path (API bills per token; `claude -p` now bills). The app publishes queue-shaped work; an attended Claude Code session pulls it. $0, full quality, agent-native, no automation hack. |
| D5 | Qwen (B) and tmux-bridge (C) kept **open for the future** | Both consume the *same queue* behind the same validated store boundary — no redesign needed to add autonomous producers later. |
| D6 | Human surface is **plain-English, both directions** | User's "plain language always" rule. Loose plain-English input + a plain-English readback of "what the AI will test." Technical spec (stream/filter/dose) lives behind a fold, always vocab-validated. |
| D7 | Input = **free-text AND guided prompts** | Scaffolding for when phrasing won't come; free text when it will. |
| D8 | "Start simple, refine through use" | User will generate new interface/procedure ideas by testing. v1 is the smallest honest version of the loop. |

**D5 addendum (2026-08-14): local-model seat dry-run VALIDATED.** Qwen3.8-27B (Q4_K_M on the PC 4090, llama-server, driven from the Mac) formalized a plain-English premise into a PremiseSpec that passed the real store-boundary validator first try (spec_hash `1c553fc030876603`), including deliberate non-default mapping (horizons → `[63, 126]` for "3-6 months"). Guardrails required before a production seat, learned from the trial:
- **Pin charter-fixed fields in the prompt** (`dedup_window_days`, `min_peer_count`, `fdr_q`, `n_boot`) or hard-set them post-hoc — the model silently retuned `dedup_window_days` 30→21 from premise prose. Legal per validator, wrong per charter.
- **Token budget ≥ 4k or thinking disabled** (`chat_template_kwargs: {"enable_thinking": false}`) — reasoning otherwise consumes the whole budget and content comes back empty.
- **Retry-once policy**: `API Error: Content block not found` through the LiteLLM proxy is retryable-harness (litellm must be pinned ==1.96.2), never model failure.
- **Provenance stamped mechanically from config, never model self-report** — the model's identity beliefs are prompt-determined and correction-resistant.
Stack details + verdicts: session memory `reference_local_qwen_stack.md`; mailbox record in `~/three-body/specs/mailbox/` (2026-08-14).

## 3. Architecture

### 3.1 PremiseSpec (the core artifact)
The single bounded object the AI produces and the user reviews. Compiles directly into `EventStudyConfig`. Frozen + content-hashed when graduating to confirm.

Fields (all validated against registered vocabulary by a Pydantic model at the store boundary):
- `premise_text` — the plain-English idea (provenance)
- `guided` — optional plain-language answers (trigger / stronger-when / hold-length / direction)
- `stream` — a registered event stream id (`form4` first)
- `event_filter` — bounded predicates over the stream's declared payload vocabulary
- `dose` — a frozen score formula from a vocabulary (parameterized, not arbitrary)
- `horizons`, `entry_lag_days`, `dedup_window_days`, `direction`, universe `floors`
- `plain_summary` — the AI's plain-English readback of the above (the trust anchor)
- `spec_hash` — content hash, set only at confirm-freeze

Peer / era / regime lenses are **automatic** (already wired in the engine via F349/F350) — not part of the authored spec.

**The bound is structural and producer-independent:** whatever produces a PremiseSpec (Claude session, Qwen, manual form) is validated at ingest. Invalid → rejected/retried. This is what makes D3 a guarantee rather than a hope.

### 3.2 Stream registry (the extensibility seam)
Makes "add streams as needed (Ratings/News/...)" clean. A `Stream` is a small interface:
- `iter_events(start, end, universe) -> Iterator[EventRecord]` — emits the canonical schema
- a declared **payload vocabulary** — the fields the AI may filter/dose on

`form4` wraps the existing `iter_form4_events`. New streams (8-K item, ratings, news, `fundamental_surprise`) register by implementing this one interface; nothing else in the workbench changes. **F338 discipline applies:** any new stream needs a real-data smoke probe with pre-stated anchors before its output is believed.

### 3.3 Persistence (queue-shaped)
A premise store (JSON, same pattern as `bots.json`). Each premise carries spec versions + run history + verdicts. Status is a **queue-shaped state machine** so any producer/consumer can pull work:
`draft → awaiting_formalization → spec_ready → exploring → explored → (iterate ↺) → awaiting_confirm → confirmed`
The `awaiting_*` states are the seam that lets the agent (D4), Qwen (D5), or a human pull the same work.

### 3.4 AI invocation
No AI caller in the v1 backend. The workbench exposes premise CRUD + the run engine as **endpoints/tools usable by both the UI and an agent** (repo agent-native parity rule). Flow: user drops a premise → state `awaiting_formalization` → attended Claude Code session reads it, writes a validated PremiseSpec + `plain_summary`, sets `spec_ready` → user reviews readback → triggers run → on completion, state `awaiting_eval` → session writes the plain-English verdict. Qwen/tmux consume the same states later.

### 3.5 Run execution
Backtests are **async** (full-scale explore runs can take ~1h+). Two tiers:
- **Fast preview** — reduced universe / shorter window, seconds-to-minutes, labelled "preview, not a verdict."
- **Full explore** — full universe ≤2020, **dispatched to the worker** via `bin/worker-dispatch.sh`, with `bin/worker-probe.sh` (F387) choosing the reachable box and local fallback when none is. Completion read via the `worker-status.sh` sentinel.

Frontend run+poll reuses the existing `ValidationRunPanel` pattern in Discovery.

### 3.6 The explore/confirm gate (the discipline boundary)
- **Explore runs:** free, unlimited, `≤2020-12-31` hard-guarded (engine `_EXPLORE_HARD_CEILING`), **not** logged to the FDR ledger. Hypothesis generation.
- **Graduate to confirm:** a deliberate, weighty UI action — freeze + hash the spec, run `power_audit.py` as a pre-check (don't burn a confirm on an underpowered design), run **once** on the sealed confirm window, **auto-append to the BH-FDR ledger** (`fdr_ledger.json`) so multiplicity is paid. The workbench makes the program's anti-p-hacking machinery automatic instead of hand-policed.

### 3.7 Frontend
New **"Desk" tab** in `App.tsx` (alongside Chart / Live Trading / Discovery). Subpane tabs: **Premises** (the v1 build), with Inbox / Playbooks / Tracking stubbed as "later." Premises pane is **master-detail**: premise library left; selected premise's full lifecycle right (idea → plain-English readback → run controls → plain-English verdict → graduate gate). Existing Discovery research panels migrate under the Desk later (not v1).

## 4. Component boundaries (isolation)
- **`PremiseSpec` model + validator** — one file; owns the bound. Consumers: store, compiler, UI. Testable in isolation (valid/invalid specs).
- **Stream registry + `form4` stream** — one module; owns event emission + vocabulary. Independent of specs and runs.
- **Spec → `EventStudyConfig` compiler** — pure function; spec in, config in. Deterministic, unit-testable.
- **Premise store** — JSON persistence + state machine. No knowledge of AI or UI.
- **Run service** — wraps fast-preview/full-dispatch, owns job state; no knowledge of spec internals beyond the compiler.
- **Desk frontend** — calls the same endpoints an agent would.

## 5. Error handling
- **Invalid AI spec** → store validator rejects; producer retries (the bound).
- **Worker unreachable** → `worker-probe` reports LOCAL; full explore falls back to local or is blocked per `WORKER_REQUIRE` rules.
- **Run failure / timeout** → premise returns to `spec_ready` with an error note; verdict panel shows the failure, never a silent empty.
- **Confirm-gate misuse** → power_audit fail blocks the confirm run (can't graduate an underpowered design); confirm is idempotent per spec_hash (re-running a frozen hash is refused, not silently re-logged).

## 6. Testing
- Pydantic validation: valid specs compile; out-of-vocabulary specs rejected.
- Stream registry: `form4` round-trips real cached data with pre-stated F338 anchors before its output is believed.
- Compiler: spec → `EventStudyConfig` equivalence on a known charter (e.g. reproduce an existing R-1 config from an equivalent spec).
- Determinism: explore run byte-identical across runs (cf. F357/F380).
- State machine: queue transitions, confirm idempotency by hash.

## 7. Out of scope (deferred)
Live event poller, paper-tracking, conviction scoring, inbox UI, bot graduation (later Desk rungs); Qwen (B) and tmux (C) autonomous producers; AI freeform codegen; new paid data streams.

## 8. Open questions / expected refinements
The user will generate interface/procedure ideas by testing v1. Likely early refinements: the guided-prompt set, the dose vocabulary's friendliness, the fast-preview universe definition, and how verdict iteration history is surfaced. None block v1.
