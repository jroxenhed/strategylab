# Ideas

Unsifted thoughts, brainstorms, things that might never ship. Capture surface for the "I wonder if..." stuff that doesn't yet have a concrete what + why + rough how.

**Graduation rule:** when an idea has a clear implementation outline (file paths, rough line count, dependencies), assign it a fresh F-ID and move it to `TODO.md` as a concrete entry. Until then, it lives here. Retired IDs (E1–E4, C30) are not reused.

**Format:** date headers, then bullets. No IDs, no priorities, no difficulty tags. Just the thought.

---

## 2026-06-04

- **Discovery — scan for candidates** (was E1): Scan the universe for good StrategyLab strategy candidates. Needs concrete criteria (Sharpe threshold? volatility range? sector?) before it can become a real task.

- **Discovery — batch backtesting** (was E2): Efficiency-critical batch backtesting to support Discovery scanning workflows. Depends on scan criteria being defined first.

- **Discovery — AI/ML parameter tweaking** (was E3): AI/ML-assisted parameter optimization beyond the current grid search. Fuzzy territory until Discovery has a concrete use case driving it.

- **Discovery — candidate pipeline** (was E4): Full pipeline from candidate discovery through spawning a bot army. Stretch goal that presupposes E1–E3 are concrete and working.

- **CPCV (Combinatorial Purged Cross-Validation)** (was C30): Alternative to walk-forward that generates a *distribution* of OOS paths via combinatorial train/test draws + bar-purging to prevent leakage. Output: Probability of Backtest Overfitting (PBO) score + Deflated Sharpe Ratio (López de Prado, "Advances in Financial Machine Learning", 2018; SSRN 3257497). Stretch goal — purging matters most for ML-style overlapping labels (triple-barrier), less for rule-based crossover signals; embargo (`gap_bars`) already covers the main rule-engine leakage path. Graduate once C28 is in real use and distributional evidence beyond single WFE is actually wanted.

## Harmonic-pattern config through the validation harness (2026-06-05, from PTON Discord chart)
Run an XABCD harmonic detector (swing-point extraction + ratio matching with tolerance bands) as a second candidate-emitting config through the F312 validation harness — same null, same cost model, same miss list — so harmonic hit rates can sit next to the turnaround filter's on one table. Key tension to design around: harmonic detection is parameter-dense (swing tolerance, ratio windows, lookback) = overfit surface, which is exactly what hit-rate-vs-null exposes. Structural prerequisite: none — run_validation is already config-agnostic in shape; needs a pluggable candidate-source interface instead of the hardcoded run_filter call. Graduates to an F-item once the detector's parameter set is locked on principle.

## The harness as a platform — premise-family configs (2026-06-05, post-verdict riff)
The F312 validation harness + EDGAR point-in-time corpus is config-agnostic; the turnaround screen was config #1. Candidate premise families, each a different stance on "is the market right": (a) **momentum config** — near-3y-high + accelerating revenue + expanding margins (inverse of washed-out; INTC-2026 as prototype hit); (b) **deterioration short screen** — the GPRO/ENPH miss-list signature (price crashed, trailing fundamentals still positive) inverted into a short-candidate emitter, run with direction=short + borrow costs; (c) **drift configs** — PEAD / post-buyback-8-K / insider-cluster-buying standalone (market is slow, not wrong; needs F321); (d) **null-as-strategy** — the 45.6% washed-out bounce base rate studied as harvestable structure vs lottery vol (needs F327 distribution); (e) **factor lab** — each pillar alone through the harness, periodic table of which ingredients carry information. PREREQ: persist event-level outcomes (every (ticker, as_of) event with pillar margins + forward returns) — currently only aggregates+miss list survive the run. GUARDRAIL: multiple-config discipline in the harness itself — N configs tested raises the null-beating bar for each (multiple-comparisons correction), or the in-sample trap returns wearing a new mask. Each graduates to an F-item individually once its parameter set is locked on principle.

## Edge-premise ideation pass (2026-06-05, post-kill)
Full 48-idea → 5-survivor ideation with external evidence review: [docs/ideation/2026-06-05-edge-premise-families-ideation.md](docs/ideation/2026-06-05-edge-premise-families-ideation.md). Survivors: Insider Intelligence Stack (best evidence, 75%), 8-K taxonomy + latency drift, Pond 2.0 (null as product, queries on run-2 events table), epistemics-first program (filing-vs-price ablation), offering overshoot fade. Recommended order + per-idea confidence in the doc. Supersedes nothing — extends the harness-as-platform entry above with evidence and priority.

## Stratified Form 4 refetch (2026-06-05, from premise-free EDA coverage gate)
The edgar_cache/form4 store covers 0.40% of pond tickers and is ~97× biased toward old signal candidates — it was fetched by the dead premise, so NO insider question is answerable from it (selection masquerading as signal). Prereq for both the Insider Stack brainstorm and the EDA's pre-registered insider test: fetch Form 4 for a stratified random sample of pond events (strata: is_null × as_of cohort), sized for the one frozen test (≥2 distinct open-market P-code buyers in 90d pre-as_of). Details: [docs/ideation/2026-06-05-premise-free-pond-eda.md](docs/ideation/2026-06-05-premise-free-pond-eda.md). Graduates to an F-item when the Insider Stack session starts.

## Delisting-complete pond universe (2026-06-05, from premise-free EDA)
The one out-of-time-confirmed EDA candidate (penny-entry <$2: Δ+31.5pt hit rate, 10/11 confirm cohorts) sits exactly where the currently-listed-only universe bias is worst — bankrupt/delisted sub-$2 names are invisible, inflating touch rates by construction. Any penny-slice claim (and honestly the whole pond's base rates) needs a delisting-inclusive universe source (e.g., EDGAR submissions of deregistered filers, or a survivorship-free price vendor) before validation. This is the single highest-leverage data-quality upgrade the EDA identified. Details in the same doc.

## The Radar desk — event-driven trading system alongside the bots (2026-06-06, John + Claude design conversation)

The end-product shape for the research program: **an event desk with receipts**, living as a Discovery panel — not an autopilot. Full reasoning in the 2026-06-06 session; condensed:

- **One event schema, two directions.** The F342 harness consumes `(ticker, timestamp, type, payload)` events. Point the same schema forward: EDGAR RSS every 10 min (Form 4, 8-K), yfinance rating actions, FINRA short interest, SEC FTDs — all audited free streams (F341/F346). Live logic and tested logic are the same code; the backtest is the live system pointed at the past. This kills the classic retail failure of live ≠ tested.
- **Playbook cards, not signals.** Each confirmed charter (R-1 insider clusters, R-2 distress recovery, …) becomes a playbook: trigger, entry (next open), horizon, exits, vetoes, plus its evidence — n, excess, CI, per-era consistency, the grading test's MDE. Unconfirmed premises never get cards.
- **Inbox, human decides.** Discovery panel listing today's matched events in plain English with playbook stats; one click → paper order / live order / pass-and-log. At ~$10k with 1–2 positions, choosing among concurrent setups is capital allocation — a human call by design.
- **Track every ping regardless of action (the compounding part).** Mechanical paper-tracking of all surfaced setups builds (a) an ever-growing forward out-of-sample record (answers the multi-period-confirmation desire — time generates fresh windows, this harvests them), (b) a measured verdict on John's discretionary overlay vs mechanical execution (same excess machinery, pointed at the trader), (c) continuous earn/lose status for every playbook instead of one historical grade.
- **Graduation ladder:** signal → paper tier (automatic) → inbox tier (John clicks) → bot tier (only for playbooks proven forward + mechanically executable). The existing bot system is the optional last rung.

**Honest dependency:** worthless until at least one charter confirms — building the inbox before any playbook exists is cart-before-horse. **Charter-independent pieces that can graduate to F-items as soon as F342 closes:** (1) live EDGAR poller emitting harness-schema events (the schema bridge), (2) the paper-tracking forward tier (works for *candidate* playbooks pre-confirmation too — accrues forward evidence while charters wait). The inbox UI graduates only after a first confirmed playbook exists.
