---
title: "feat: Signal-Driven Research Program — from premise-hunting to instrument-led search"
type: feat
status: active
date: 2026-06-05
deepened: 2026-06-05
origin: docs/ideation/2026-06-05-premise-free-pond-eda.md
---

# Signal-Driven Research Program

## Overview

Replace premise-first research (pick a story, test it for a year) with an instrument-led search for tradeable structure: build the measurement platform the evidence says we need, weaponize the single strongest measured fact (regime), then run high-event-rate, price-led signal configs through a generalized harness under pre-registration discipline. Target cadence: **weeks-to-months holding periods, long + short**, free data with a pre-approved paid-data upgrade trigger.

## Problem Frame

Two validation runs and a premise-free EDA (2026-06-05) produced these binding facts:

1. **Regime dominates selection.** Per-year pond hit rates swing 24%→84%; explore→confirm base rates moved 0.524→0.380. "When" carries more variance than any "what" we have measured. *Transfer-validity caveat:* the 24→84% swing is a touch-metric effect measured **on the abandoned pond** (washed-out, crashed-microcap names, where the touch metric the plan disowns is volatility-sensitive). Treat "regime dominates" as **pond-measured** until Unit 5 re-measures it on universe v2 with horizon-end metrics. The regime-first sequencing rationale is therefore a **hypothesis bet, stated as such** — not a transplanted axiom.
2. **Validatability is a design constraint, not an afterthought.** The turnaround screen died *operationally* (1.3 events/yr can never clear its own CI), independent of whether it worked.
3. **Price leads, filings trail** (hypothesis-grade, frame-dependent evidence): filings-as-trigger was structurally late in every autopsy; Form 4 clusters do **not** rescue washed-out names (sealed pre-registered test, F334). Filings remain open as *filters/vetoes* and on *non-crashed* universes.
4. **Metrics can manufacture verdicts.** The +50%-touch/12m metric and touch-vs-hold comparison both injected premise artifacts into conclusions.
5. **Free data lies below ~$5.** Survivorship bound (F335): the penny candidate leans killed-by-bias; anything in delisted-land is unmeasurable without paid data.
6. **The instruments now exist**: atlas *scaffolding* (F333 — touch-metric, pond-calibrated), explore/confirm split + test-ledger protocol (EDA-0605), stratified EDGAR fetcher (F334), survivorship break-even arithmetic (F335). Note: the shipped null atlas is scaffolding only — a Universe-v2, horizon-end-metric **atlas v2 must be regenerated before R2 binds** (it carries zero universe-v2 cells and uses the disowned touch metric); this is an explicit gating prerequisite, not a sub-bullet (see Unit 2 and R2).

The beaten-down-names universe is abandoned as a *selection premise*; its events table is retained as calibration data for the instruments.

## Requirements Trace

**Experiment discipline**

- R1. Every config pre-registers an expected event rate ≥ ~100/yr before any run (operational-kill lesson).
- R4. Explore/confirm time-split + frozen-hypothesis discipline + test-count ledger on every experiment.

**Measurement methodology**

- R2. Every result is judged against a cohort-matched local null (atlas v2), never a global average — and its universe-v2 regeneration gates all config verdicts (the shipped F333 atlas is touch-metric scaffolding and does not satisfy R2).
- R3. Outcome metrics are horizon-end, cohort-relative forward returns at weeks-to-months horizons — no touch-based primary metrics, no cross-exit-strategy comparisons.

**Universe & data**

- R5. Universe floors: min price $5, meaningful liquidity floor; residual survivorship bias documented per slice using the F335 method.
- R7. Paid-data upgrade triggers automatically when its pre-defined condition fires (see Key Technical Decisions) — no new decision round.

**Signal & direction**

- R6. Long and short configs supported end-to-end (direction + borrow cost already in backtester).
- R8. Signals derive from price/volume first; filings enter only as filters/vetoes or on non-crashed universes (controlled ablation arms exempt: Unit 8 deliberately builds a filings-as-trigger arm to MEASURE the boundary R8 asserts).

## Scope Boundaries

- No live trading or bot deployment in this plan — validation only; deployment decisions are downstream of verdicts.
- No intraday signals (cost-dominated at this account scale; daily bars only).
- No ML/parameter-dense detectors (overfit surface; the harmonic-pattern idea stays in IDEAS.md until parameters can be locked on principle).
- The pond (washed-out universe) is not re-mined for new long premises. (Abandonment is confirmed, not assumed: Unit 2's verification reprocesses the run-2 events through the corrected v2 metrics as a zero-new-data counterfactual — if no excess survives, abandonment is evidence-confirmed.)

### Deferred to Separate Tasks

- F331 (parallel price prefetch) and F332 (persist price frames): already-filed Architecture items; this plan depends on F332 and sequences it, but its spec lives in TODO.md.
- Phase B paid-data purchase + integration: separate task triggered by R7's condition.
- Bot daily-cadence support (F188a): needed only when a config graduates to deployment.
- Regime as a standalone **daily** signal: testing whether regime state predicts daily-cadence forward returns would require a daily-cadence harness (the current harness produces quarterly cohorts only). Out of scope — Unit 5 tests only the quarterly-cohort base-rate claim.

## Context & Research

### Relevant Code and Patterns

- `backend/turnaround_validation.py` — `run_validation`'s hardcoded `run_filter` call (~line 511) and universe build (~line 440). Note: `bars_loader` is a locally-defined closure *inside* `run_validation` (~line 461), NOT an injected parameter — there is no existing injection seam; Unit 1 introduces one. Return contract: `list[CandidateResult]`.
- `backend/turnaround.py` — `build_universe()` (F319 junk-suffix hygiene, ETF/SPAC title exclusion) with existing `min_price=1.0` / `min_avg_volume=100k` floors in `_process_symbol()` Stage 1a; `pct_off_high` / `pct_above_low` already computed (momentum config inverts these gates).
- `backend/research/` — reusable primitives: stratified sampler, rate-limited EDGAR fetch + gzip caching, Wilson CI, `_cohort_stats`, `_price_band`, Form 4 buy parsing, break-even phantom arithmetic.
- `backend/data/turnaround/null_atlas.json` — local base-rate lookup (F333), `meta.usage` documents the consultation contract.
- Backtester cost model + `direction` field (short support) per CLAUDE.md.

### Institutional Learnings

- EDA-0605 protocol (charter → explore/confirm → sealed judge → adversarial review) is the template for every experiment unit below.
- Reviewer false positives happen; fixers verify findings against data before applying (INSTR-0605).
- `docs/solutions/` does not exist; institutional memory lives in JOURNAL.md + ideation docs.

### External References

- Ideation evidence review (docs/ideation/2026-06-05-edge-premise-families-ideation.md): momentum/quality and insider cluster buying are the best-documented surviving 2020s anomalies; 8-K text drift documented in microcaps; offering overshoot fade documented at 150–300 events/yr.

## Key Technical Decisions

- **Regime becomes an instrument, not a confound**: a point-in-time, price-only regime classifier is built and validated *before* any new signal config runs, because regime is the largest *pond-measured* effect we possess. The "regime dominates" finding is a touch-metric effect measured on the abandoned pond (see Problem Frame #1); treating it as the strongest fact on universe v2 is a **hypothesis bet, stated as such**, that Unit 5 re-measures with horizon-end metrics before the framing is load-bearing. Rationale: conditioning on regime mechanically raises every config's local-null precision; and if regime state alone predicts forward base rates out-of-time, that is itself the first candidate signal.
- **Outcome engine v2 replaces +50%-touch**: primary metrics are cohort-relative forward returns at 21/63/126 trading-day horizons (median + mean excess vs matched null, hit defined as beating the cohort null median). Touch metrics demoted to diagnostics. Rationale: R3; touch metrics manufactured the run-2 artifact.
- **Pluggable candidate source (new injection pattern)**: configs become callables returning `list[CandidateResult]`; `run_filter` becomes config #0 via a new optional `candidate_source` param on `run_validation` (default `None` → legacy path). Note: `bars_loader` is a local closure, not an existing seam — Unit 1 introduces the injection. Four concrete consumers in-plan justify the abstraction: config #0 (legacy), momentum (Unit 6), deterioration-short (Unit 7), epistemics ablation (Unit 8). Rationale: IDEAS.md already identified this; it is the gate for every config below.
- **Universe v2 is a config, not a rewrite**: reuse `build_universe()` with floors raised to min_price $5 / min_avg_volume 500k and no washed-out gate. Rationale: F319 hygiene is premise-independent; floors implement R5.
- **Paid-data upgrade trigger (pre-approved)**: purchase survivorship-free data when either (a) a config passes explore but its confirm verdict flips under F335 worst-case phantom injection, or (b) >30% of a config's explore events sit below the $5 floor. Rationale: John's "free now, pre-approve upgrade" decision made concrete.
- **Shorts are first-class**: deterioration-short runs as an actual short config (borrow costs modeled), AND its signal is simultaneously read as a long-side exclusion filter — one run, two pre-registered questions. Rationale: John's long+short decision; ideation's inversion insight.

## Open Questions

### Resolved During Planning

- Data budget: free now, pre-approved upgrade trigger (John, 2026-06-05).
- Direction: long + short (John).
- Horizon: weeks-to-months, daily bars (John).
- First config family: momentum/trend — best external evidence + highest event rate + price-led (R8). Acknowledged explicitly: this is a **prior-based bet from external literature** — the program's one deliberate exception to its measured-facts-first discipline — justified by the family's high event rate and price-led nature, with the beta-distinguishing prediction (excess survives beta/size stratification, not just market-excess) pre-registered in Unit 6's charter.
- **Program success bar** (John, 2026-06-05, document-review P0): the program succeeds when **≥1 config reaches CONFIRMED on the confirm window with effect ≥ its pre-registered minimum** (meaningful excess at 63d *after costs*; exact threshold locked in each config's charter against the program alpha budget). Anything less → Decision Gate 9 decides pivot-or-stop with the full evidence ledger in hand; "three WEAKENED verdicts" is a defined non-success, not an ambiguous limbo.
- **Regime-first sequencing affirmed** (John, 2026-06-05, document-review P1 considered and declined): Phase 2 completes before Phase 3 configs run — cleaner conditioning from day one, serialization cost accepted. Stall guard: if Unit 5 returns UNTESTABLE or REVERSED, Phase 3 proceeds *unconditioned* with regime demoted to a reporting dimension — the ordering holds, but a failed instrument cannot block the signal configs indefinitely.

### Deferred to Implementation

- Exact regime classifier features (trend/vol/breadth definitions): depends on what's computable point-in-time from cached daily bars without survivorship distortion; locked during U5 pre-registration, before any outcome data is touched.
- Matched-null sampling design for the new universe (with-replacement vs cohort-exhaustive): depends on universe size after floors; locked in U2's spec.
- Beta-control design (null stratification by beta/size vs market-excess returns): cohort+universe matching neutralizes time/regime, NOT the cross-sectional beta tilt of a momentum selection. Choose between adding beta/size as explicit null-stratification keys or computing market-excess returns; locked in U2's sampling spec before momentum CONFIRMED can be claimed.
- Whether the 8-K latency config (Phase 4) uses EFTS counts or full-text fetch: depends on EFTS API behavior at the required scale; probe during Phase 4 spec.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
                       ┌──────────────────────────────┐
                       │  Universe v2 (liquid, $5+)    │
                       └──────────────┬───────────────┘
                                      │ tickers per as_of
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
   ┌──────────▼─────────┐  ┌─────────▼──────────┐  ┌─────────▼─────────┐
   │ Config A: momentum │  │ Config B: deter.-  │  │ Config C: epist.  │
   │ (long)             │  │ short (short+veto) │  │ ablation (A vs    │
   │                    │  │                    │  │ filing-selected)  │
   └──────────┬─────────┘  └─────────┬──────────┘  └─────────┬─────────┘
              │   CandidateSource contract: (as_of, universe) → [CandidateResult]
              └───────────────────────┼───────────────────────┘
                                      ▼
                       ┌──────────────────────────────┐
                       │ Harness (generalized)         │
                       │  outcome engine v2:           │
                       │  21/63/126d cohort-relative   │
                       │  fwd returns vs matched null  │
                       └──────────────┬───────────────┘
                                      │ judged against
                  ┌───────────────────┼────────────────────┐
                  ▼                   ▼                    ▼
        ┌──────────────────┐ ┌────────────────┐ ┌──────────────────────┐
        │ null_atlas v2     │ │ regime state   │ │ explore/confirm +    │
        │ (local base rates)│ │ (point-in-time)│ │ ledger + sealed judge│
        └──────────────────┘ └────────────────┘ └──────────────────────┘
```

## Implementation Units

### Phase 1 — Platform (the harness stops being premise-shaped)

- [x] **Unit 1: Pluggable candidate-source interface**

**Goal:** Decouple `run_validation` from `run_filter`; any config callable can emit candidates.

**Requirements:** R1, R6, R8

**Dependencies:** None

**Files:**
- Modify: `backend/turnaround_validation.py`
- Modify: `backend/routes/turnaround.py` (config selection on the validate endpoint)
- Test: `backend/tests/test_turnaround_validation.py`

**Approach:**
- `bars_loader` is a local closure inside `run_validation`, not a parameter, so there is no seam to mirror — Unit 1 **introduces** the injection pattern: add an optional `candidate_source` callable parameter to `run_validation`, default `None` → the legacy `run_filter` path (config #0, regression anchor).
- A config declares: name, direction (long/short), pre-registered expected event rate, horizon set. Harness refuses to run a config without an event-rate declaration (R1 enforced in code).
- Enforcement mechanics: the event-rate declaration is a **required field on the config object**; the harness validates it pre-run and refuses (no partial artifacts) if absent — the existing error-path test scenario below covers it.

**Patterns to follow:** `CandidateResult` return contract; the optional-callable-with-`None`-default convention (the injection pattern Unit 1 establishes for all later configs).

**Test scenarios:**
- Happy path: injected dummy source emitting known candidates on 2 cohorts → events table contains exactly those events with correct cohort tags.
- Happy path: default path (no source injected) reproduces a frozen slice of run-2 output (regression: legacy behavior byte-stable).
- Error path: config without event-rate declaration → run refused with explicit error, no partial artifacts written.
- Edge case: source returning zero candidates on a cohort → cohort recorded as empty, run continues, no division-by-zero in stats.
- Integration: short-direction config flows direction through to outcome computation (sign-correct returns).

**Verification:** Legacy regression test green; a toy config runs end-to-end producing a verdict artifact.

- [x] **Unit 2: Outcome engine v2 (cohort-relative forward returns)**

**Goal:** Replace +50%-touch/12m as primary metric with 21/63/126-trading-day cohort-relative forward returns vs a matched null; long+short sign handling.

**Requirements:** R2, R3, R6

**Dependencies:** Unit 1, Unit 3 (universe v2 required before atlas v2)

**Files:**
- Modify: `backend/turnaround_validation.py` (outcome section; extend `ValidationRequest` with `direction` + `borrow_rate_annual`; short-aware `_apply_costs()`)
- Modify: `backend/research/build_null_atlas.py` (atlas v2: new horizons + universe-v2 recalibration; branch on `schema_version`)
- Test: `backend/tests/test_turnaround_validation.py`

**Approach:**
- Per event: forward returns at each horizon; excess = event return − cohort-matched null median at same horizon; "hit" = excess > 0. Touch/days-to-hit retained as diagnostic fields only.
- **Horizons implemented by bar-counting on the already-fetched frame** (count N trading rows forward from entry; survivorship-safe, no external calendar), superseding the calendar-month `_horizon_end_date` for v2 metrics. The `ValidationRequest.horizon_months` constraint (`ge=3, le=24`) must be relaxed/superseded.
- **Events table becomes `schema_version=2` with ADDITIVE fields** (21d/63d/126d excess returns) so existing schema_version=1 consumers keep working; `build_null_atlas.py` branches on `schema_version` (matches the existing schema_version convention).
- **Short cost path (P0 — currently long-only):** `_apply_costs()` is documented "Long direction only" and applies slippage entry-up/exit-down unconditionally. Extend `ValidationRequest` with `direction: Literal['long','short'] = 'long'` and `borrow_rate_annual: float = 0.5`; **invert slippage sign for shorts**; accrue borrow cost over the holding period by reusing the `borrow_cost()` pattern from `backend/routes/backtest.py`. Sign for shorts: net return = (entry − exit)/entry. (Unit 7's short verdicts gate on this work — see its dependency note.)
- Null sample drawn from same cohort + universe via the F334 stratified sampler primitive; sampling design locked in the unit's spec before outcomes are computed, including the **beta-control choice** (beta/size null-stratification keys vs market-excess returns — see Deferred to Implementation) so momentum CONFIRMED is not claimed on un-beta-controlled excess.
- **Atlas v2 is a gating prerequisite, not a sub-step:** regenerated from the new universe (Unit 3) so universe-v2 local nulls exist before any config runs; until atlas v2 exists, R2 is unsatisfiable and no config verdict is valid.

**Test scenarios:**
- Happy path: synthetic events with known prices → exact expected excess returns at all three horizons.
- Edge case: event within 126 trading days of data end → long-horizon cell marked incomplete, not extrapolated.
- Edge case: cohort with n<30 null events → stats flagged insufficient (atlas convention), config run warns.
- Error path: missing price data for an event ticker → event excluded with counted reason, never silently dropped.
- Integration: short-direction event with falling price produces positive excess (sign correctness end-to-end).
- Short cost correctness: a short event's slippage sign is inverted vs long (entry-down/exit-up) AND borrow cost accrues over the holding period (assert accrual scales with hold days at the configured `borrow_rate_annual`).

**Verification:** Run-2 events table reprocessed through v2 produces the audit's apples-to-apples numbers (the EDA's corrected null hold-median, computed against the **legacy 12-month horizon fields kept as diagnostics** — so this reproduction check does not contradict the new 21/63/126-trading-day horizon set) — the engine reproduces the EDA's corrected math. This same reprocessing **also serves as the pond-abandonment counterfactual check**: if corrected cohort-relative metrics still show no excess vs the local null, abandonment is evidence-confirmed (closing the loop the EDA opened); if it shows signal, that is material information.

- [x] **Unit 3: Universe v2 + price infrastructure**

**Goal:** Liquid tradeable universe (min_price $5, min_avg_volume 500k, F319 hygiene, no washed-out gate) + F332 price-frame persistence so iteration is minutes, not 35-minute walls.

**Requirements:** R5

**Dependencies:** None (parallel with Units 1–2); F332 spec in TODO.md

**Files:**
- Modify: `backend/turnaround.py` (`build_universe` parameterization)
- Modify: `backend/turnaround_validation.py` (per F332's TODO spec)
- Test: `backend/tests/test_turnaround.py`

**Approach:**
- Floors as parameters, not constants; survivorship residual documented per F335 method (delisting intensity above $5 measured once, cited in every verdict doc).
- **`FilterParams` defaults change must not silently alter existing consumers.** Raising `min_price` 1.0→5.0 and `min_avg_volume` 100k→500k as the new parameter defaults would change behavior for every current `ScanRequest` caller (`backend/routes/turnaround.py` constructs `FilterParams` via `default_factory`, so the Discovery scan endpoint would silently inherit the new floors). Existing consumers must get **explicit overrides** preserving today's floors — check the routes that construct `FilterParams` and pin them.
- F332 lands here because every subsequent unit re-sweeps the same 2015–2024 daily bars.

**Test scenarios:**
- Happy path: universe build excludes sub-$5 and thin-volume names present in fixtures.
- Edge case: F319 junk suffixes still excluded with the washed-out gate off.
- Integration: second validation run on same span starts at the date loop (price frames served from disk), byte-identical outcomes.

**Verification:** Universe size + composition snapshot committed as fixture; a repeat run's wall-clock drops by an order of magnitude.

### Phase 2 — The regime instrument (the strongest fact, weaponized)

- [x] **Unit 4: Point-in-time regime classifier (price-only)**

**Goal:** Daily regime state 2015–2024 from index/breadth/vol features computable point-in-time from cached daily bars; no look-ahead, no survivorship leak.

**Requirements:** R2, R8

**Dependencies:** Unit 3 (price persistence)

**Files:**
- Create: `backend/research/regime_state.py`
- Create: `backend/data/turnaround/regime_states.json`
- Test: `backend/tests/test_regime_state.py`

**Approach:**
- Feature set locked by pre-registration BEFORE any outcome data is consulted (charter file in `.run/`, same protocol as EDA-0605). Candidate features: index trend (e.g., 200d slope/position), realized vol bands, breadth (% of universe-v2 above its own 200d — survivorship-safe because computed on currently-listed only and used as a *relative* daily feature, with this caveat documented).
- Output: small discrete state space (target 3–5 states; granularity locked at pre-registration). Artifact has schema_version + generation provenance, same conventions as null_atlas.

**Test scenarios:**
- Happy path: known fixture window (e.g., 2020 crash) lands in the expected stress state.
- Edge case: first 200 days of history → states marked warmup, excluded from downstream joins.
- Error path: missing index data for a date → state absent with counted reason, not interpolated.
- Integration: every date in the events table joins to exactly one non-warmup state.

**Verification:** Artifact regenerates deterministically; no feature uses data after its date (spot-audit by shifting input window and confirming states before the shift are unchanged).

- [x] **Unit 5: Pre-registered test — does regime state predict forward base rates?**

**Goal:** The first experiment of the program: regime state at cohort date vs that cohort's forward base rate, out-of-time. If yes, regime is candidate signal #1 *and* a validated conditioning instrument; if no, it remains a descriptive caution.

**Requirements:** R1: N/A — not a candidate-selection config; R4 applies. R2.

**Dependencies:** Units 2, 4

**Files:**
- Create: `backend/research/regime_validation.py`
- Create: `.run/REGIME-TEST/charter.md` (pre-registration; gitignored, verdict goes to docs/)
- Test: `backend/tests/test_regime_state.py`

**Approach:**
- This unit tests claim (a) only: **regime state predicts QUARTERLY cohort base rates** — testable with the existing quarterly harness (the 36+ quarterly cohorts across 2015–2024). It does NOT test claim (b): regime as a standalone **daily** signal — that would require a daily-cadence harness that does not exist (out of scope; see Deferred to Separate Tasks). The R1 event-rate gate does not apply because this is not a candidate-selection config.
- Frozen before looking: state definitions (U4), the ordering hypothesis (which states predict higher forward base rates), explore window (2015–2020) / confirm window (2021–2024), success bar (rank agreement + effect size with CI), ledger.
- Ground truth: pond cohorts (calibration) AND universe-v2 cohorts (the population that matters going forward).

**Test scenarios:**
- Happy path: synthetic data with planted state→outcome relationship recovered at correct effect size.
- Edge case: state with <3 cohort observations in confirm → UNTESTABLE verdict (the regime-2020 lesson — encode it).
- Integration: explore/confirm boundary enforced — confirm-window data physically absent from the explore computation (separate invocation).

**Verification:** Verdict doc in `docs/plans/` (CONFIRMED/WEAKENED/REVERSED/UNTESTABLE per hypothesis) with ledger; sealed-judge protocol followed.

### Phase 3 — First signal configs (pre-registered, regime-conditioned)

- [x] **Unit 6: Momentum config (long)**

**Goal:** The best-documented anomaly family, implemented as config #1 through the generalized harness: near-high + uptrend persistence on universe-v2, weeks-to-months horizons.

**Requirements:** R1 (est. high hundreds of events/yr — pre-registered before running), R2, R3, R4, R8

**Dependencies:** Units 1–4 (Unit 5 verdict informs conditioning but does not block)

**Files:**
- Create: `backend/research/config_momentum.py`
- Test: `backend/tests/test_config_momentum.py`

**Approach:**
- Gates built from already-computed `pct_off_high` / `pct_above_low` inverted (near 52-wk high rather than far below), plus a trend-persistence measure; parameters locked at pre-registration with explicit ledger budget for the (small, enumerated) parameter grid.
- Judged at 21/63/126d vs local null; results reported per regime state.

**Test scenarios:**
- Happy path: fixture name pinned near its high passes gates; washed-out fixture fails.
- Edge case: IPO with <252 trading days → excluded with counted reason.
- Error path: parameter outside the pre-registered grid → refused (ledger enforcement).
- Integration: end-to-end run on a 2-cohort fixture produces events + verdict artifact with regime joins.

**Verification:** Explore/confirm verdict doc with ledger; event rate vs pre-registered estimate reported (R1 honesty check).

- [x] **Unit 7: Deterioration-short config (short + long-veto, one run, two questions)**

**Goal:** Run-1's miss-list signature (price crashed, trailing fundamentals still printing fine) inverted into a short-candidate emitter — the only premise with in-house evidence. Simultaneously read as a long-side exclusion filter.

**Requirements:** R1, R2, R3, R4, R6

**Dependencies:** Units 1–4; Unit 6's null machinery (shares cohorts). Note: Unit 7's **short verdicts gate on Unit 2's short-cost work** (direction-aware slippage sign + borrow-cost accrual) — the validation cost engine is long-only today, so sign-correct short returns do not exist until that Unit 2 path lands.

**Files:**
- Create: `backend/research/config_deterioration.py`
- Test: `backend/tests/test_config_deterioration.py`

**Approach:**
- Both pre-registered questions in one charter: (Q1) short excess returns vs local null at 21/63/126d with borrow costs from the backtester's cost model; (Q2) do *long* configs improve when these names are excluded? (filter lift on Unit 6's events).
- Fundamentals enter only as the *trailing-still-positive* veto component — filings as filter, per R8.

**Test scenarios:**
- Happy path: GPRO-2015-class fixture (crashed price, positive trailing YoY) emitted as short candidate.
- Edge case: borrow-cost model applied — a marginal raw edge that dies after costs reports both numbers.
- Error path: missing fundamentals for a candidate → excluded with counted reason (never imputed).
- Integration: Q2 filter-lift computed against Unit 6's actual event set, same cohorts.

**Verification:** One verdict doc, two pre-registered verdicts, ledger covering both.

- [x] **Unit 8: Epistemics ablation (prices the axiom)**

**Goal:** Head-to-head, same universe, same horizons: price-only selection vs filing-only selection. Converts "price leads, filings trail" from FRAME-DEPENDENT (audit classification) into a measured number, settling the CLAUDE.md axiom's evidence status.

**Requirements:** R2, R3, R4, R8

**Dependencies:** Unit 6 (price-only arm reuses its machinery); EDGAR corpus (exists)

**Files:**
- Create: `backend/research/config_epistemics_ablation.py`
- Test: `backend/tests/test_config_epistemics.py`

**Approach:**
- Two arms emit equal-sized candidate sets per cohort (rank-N from each criterion); identical evaluation. Pre-registered comparison: excess-return distributions + rank correlation of overlap.
- Outcome updates the Research Axiom's wording in CLAUDE.md either way (measured > hand-picked miss-list).

**Test scenarios:**
- Happy path: arms produce disjoint-by-construction fixtures → comparison runs with zero-overlap branch exercised.
- Edge case: cohort where filing arm can't fill rank-N (sparse filings) → arm sizes recorded, comparison weighted accordingly per pre-registered rule.
- Integration: both arms' events tagged and separable in one events table.

**Verification:** Verdict doc + a follow-up edit *proposal* for the CLAUDE.md axiom citing the measurement — but the actual axiom edit is **blocked until the program-level gate (Decision Gate 9)** verifies the program ledger against the pre-registered alpha budget; no single-config result rewrites the axiom on its own.

### Phase 4 — Conditional second wave (decision gate)

- [ ] **Decision Gate 9: Gate review + next config selection**

> Decision Gate 9 is a **decision checkpoint, not an implementation unit** — it produces no behavioral code; it gates whether (and which) second-wave config proceeds.

**Goal:** Synthesize Phase 2–3 verdicts; select (or decline) the next config from: 8-K/EFTS latency drift, insider general-universe (NOT pond-rescue — that died), offering overshoot fade. Apply the paid-data trigger if its condition fired.

**Requirements:** R1, R7

**Dependencies:** Units 4–8

**Files:**
- Create: `docs/plans/` verdict-synthesis doc (named at execution time)

**Approach:** John's decision point, structured: per-config verdict table judged against the **program success bar** (≥1 CONFIRMED config at pre-registered minimum effect after costs — see Resolved During Planning), paid-data trigger evaluation, recommendation. **Multiple-comparisons accounting is pre-registered up front, not created here:** a program alpha budget fixes the planned config count before any config runs, and each config's per-config success bar is tightened against that budget (each charter MUST cite the program budget at pre-registration). Decision Gate 9 **verifies the program ledger totals against the pre-registered budget** — it does not invent a multiple-comparisons correction post-hoc. (Unit 8's CLAUDE.md axiom edit is blocked until this program-level gate.)

**Test scenarios:** Test expectation: none — synthesis/decision document, no behavioral code.

**Verification:** Documented decision; IDEAS.md/TODO.md updated accordingly.

## System-Wide Impact

- **Interaction graph:** `run_validation` callers (routes, ValidationRunPanel polling) see new config-selection surface; legacy run_filter path preserved as config #0 so the Discovery tab keeps working unchanged until UI grows a config picker (out of scope).
- **Error propagation:** configs refusing to run (missing event-rate declaration, out-of-grid parameters) must surface through `GET /validate/status` error field — same channel F313 built.
- **State lifecycle risks:** atlas v2 and regime_states.json join validation results in `backend/data/turnaround/` — all follow schema_version + atomic-write conventions (INSTR-0605 review findings made these binding).
- **API surface parity:** validate endpoints gain optional config param; default unchanged (additive, compiler/test-enforced → Tier B per review conventions).
- **Integration coverage:** the cross-layer scenario unit tests can't prove — a full config run producing a verdict artifact — is exercised per-config on 2-cohort fixtures (cheap, in CI).
- **Unchanged invariants:** events table schema_version=1 consumers (EDA scripts, atlas builder) keep working — v2 outcome fields are additive; +50%-touch fields remain populated as diagnostics.

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Program-level multiple comparisons (many configs, many horizons) | High | High | Pre-registered program alpha budget: planned config count fixed up front, per-config success bars tightened accordingly, each config charter MUST cite the program budget at pre-registration; Unit 8's CLAUDE.md axiom edit blocked until the program-level gate (Decision Gate 9). The Gate verifies ledger totals against the budget — it does not create the correction post-hoc. Horizons pre-registered per config, not scanned; sealed-judge confirm on everything |
| Regime classifier overfits its own history | Med | High | Features locked pre-outcome; discrete small state space; out-of-time confirm window; UNTESTABLE verdict honored (regime-2020 lesson) |
| Breadth feature carries survivorship residue | Med | Med | Documented caveat; relative (not absolute) usage; F335 method quantifies residue above $5 once |
| Momentum config rediscovers beta in a bull tape | Med | High | Cohort-relative excess vs local null is regime/time-adjusted, **NOT beta-adjusted** — cohort+universe matching neutralizes tape/regime but not the cross-sectional beta tilt of a momentum selection. A beta-control design (beta/size null-stratification keys vs market-excess returns, locked in Unit 2's sampling spec) is required before momentum CONFIRMED can be claimed; per-regime reporting separates tape-timing |
| Free-data ceiling invalidates a winner late | Med | Med | R7 trigger pre-approved — the failure mode converts to a purchase, not a stall |
| Borrow-cost model too coarse for short verdicts | Med | Med | B9 (cost model v2) flagged; short verdicts report gross AND net; marginal-after-cost = WEAKENED not CONFIRMED |
| Harness generalization breaks run-2 reproducibility | Low | High | Config #0 regression test pins legacy behavior; run-2 artifact snapshot preserved |

## Phased Delivery

- **Phase 1 (Units 1–3):** platform — no science claims, pure capability. Parallel-friendly.
- **Phase 2 (Units 4–5):** regime instrument + the program's first pre-registered experiment.
- **Phase 3 (Units 6–8):** three configs, shared cohorts/nulls, independent charters.
- **Phase 4 (Decision Gate 9):** synthesis + John's gate (decision checkpoint, not an implementation unit).

## Documentation / Operational Notes

- Every experiment unit produces a verdict doc; JOURNAL.md bullets per unit (handoff contract).
- Unit 8's outcome triggers a CLAUDE.md Research Axioms edit (evidence-status update) — flagged for John either way.
- Program ledger lives with Decision Gate 9's synthesis doc; the pre-registered program alpha budget is fixed before any config runs and each config charter cites it.

## Sources & References

- **Origin documents:** docs/ideation/2026-06-05-premise-free-pond-eda.md, docs/ideation/2026-06-05-edge-premise-families-ideation.md
- Evidence artifacts: backend/data/turnaround/null_atlas.json, validation_result.json (run-2 events), edgar_cache/delisting/bound_result.json
- Related code: backend/turnaround_validation.py, backend/turnaround.py, backend/research/
- Related TODO items: F331, F332 (price infra), B9 (cost model v2), F188a (daily bots, deployment-time)
- Decisions (John, 2026-06-05): free data + pre-approved upgrade trigger; long+short; weeks-to-months horizons
