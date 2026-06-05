# EPISTEMICS-TEST Verdict — Unit 8, Signal-Driven Research Program (Experiment 4 of 4)

**Date:** 2026-06-05 · **Charter:** `.run/EPISTEMICS-TEST/charter.md`, sha256 `015b0464…149d`, frozen blind · **Alpha:** final 25% share, per-comparison α≈0.004167, seeded bootstrap (20260605) · **Protocol:** blind charter → implementation (21 tests + F338 real-cohort smoke probe) → explore (2015–2020, open) → sealed explore-blind confirm (2021–2024) · **Question:** the measured form of the Research Axiom *"price leads, filings trail"* — does generic price-derived selection beat generic filing-derived selection, same universe, same horizons?

## Verdict: H1 WEAKENED · H1′ WEAKENED · H2 HOLDS — neither arm carries selection power; axiom NOT measured-confirmed

| Hypothesis | Confirm window (2021–2024, 16 cohorts; price arm 587 / filing arm 398 events; coverage VIABLE 0.755–0.786 → full read) |
|---|---|
| H1 (primary): price-arm − filing-arm paired 63td excess > 0 | **WEAKENED**: Δ = −3.00pp, seeded 99.583% CI [−11.45, +8.18], cohort agreement 0.3125 (5/16). Point estimate leans *negative* but is not sign-inverted at the bar. |
| H1′ (co-primary, disjoint-only members) | **WEAKENED**: Δ = −2.93pp, CI [−12.23, +8.96], agreement 0.375 |
| H2 (strong form): filing-arm own excess ≤ 0 | **HOLDS**: +0.14pp, CI [−8.01, +7.13] — filings-as-trigger show no positive selection information |

Arms: top-50/cohort by 126td return rank (price) vs point-in-time trailing revenue-YoY rank (filing), both long, universe-v2 floors, cohort-exhaustive nulls. Filing arm underfilled (~25/cohort confirm) per recorded coverage; the charter's tiered rule judged coverage VIABLE.

## What this means, plainly

1. **The axiom's measured form failed to confirm — in an instructive way.** "Price leads, filings trail" predicted the price arm should win. It didn't: *neither* arm distinguishes itself from the cohort null, and the head-to-head leans (insignificantly) toward filings. Per the charter's pre-stated consequence table: **the fired consequence row, verbatim: "H1 FAILS (CI touches zero / agreement<0.60), not sign-inverted → NOT MEASURED-CONFIRMED; status left FRAME-DEPENDENT"**, and the precise measured statement is: *generic single-factor selection — by trailing return or by trailing revenue growth — carries no detectable 63td cohort-relative information on the liquid universe, 2021–2024.*
2. **H2 is the half that held.** The filings-as-trigger half of the axiom (filings carry nothing) is consistent with the data (+0.14pp ≈ 0). What failed is the implied converse — that *price* carries something generic selection can harvest. (Cross-experiment synthesis is reserved for the Decision Gate 9 record.)
3. **The latency mechanism is untouched.** The axiom's causal story (information diffusion order; filings are the slowest record) was never tested here — only its selection-edge corollary. Form 4 / event-latency designs (8-K drift) remain open questions with their own pre-registerable tests.
4. **Program alpha fully spent.** Four experiments, four pre-registered verdicts, zero CONFIRMED. Decision Gate 9 now applies the program success bar.

## What this does NOT say

- It does not refute "price leads" as a *latency* claim about event-time information flow — it refutes the idea that a generic trailing-price rank is a harvestable expression of it at these horizons.
- It does not test interaction/multi-factor selection, event-driven designs, or shorter horizons.
- Filing-arm coverage selection (names with parseable XBRL skew larger/healthier) biases the filing arm's quality upward — making the filing arm's null result, if anything, generous to filings.

## Process notes

- F338 enforced throughout: real-cohort smoke probe pre-launch (passed: both arms emitted, ranks verified sorted, overlap counted); 6/6 anchors on both confirm artifacts before judging.
- One agent's detached-launch claim did not survive its exit (no process, no log) — caught by the watcher's disk-truth check; relaunched by the orchestrator; "launch claims get verified like code claims" added to the day's process ledger.
- Regime descriptive breakdown covers 12/16 confirm cohorts: the other 4 as_of dates (2021-02-15 Presidents’ Day; 2021-05-15, 2021-08-15, 2022-05-15 weekends) are non-trading days and the exact-date regime join has no state for them — descriptive only, no verdict impact. (Future nicety: join to last trading day ≤ as_of.)
- Artifacts: `.run/EPISTEMICS-TEST/{charter.md, confirm-verdicts.md, confirm-verdicts.json, compute_verdicts.py}`; result JSONs `backend/data/turnaround/epistemics_{price,filing}_{explore,confirm}_result.json` (local, regenerable).

**Program state after experiment 4 of 4:** REVERSED (regime) · WEAKENED (momentum, conformant) · WEAKENED-against (deterioration-short) · WEAKENED/HOLDS (epistemics: no arm wins; filings-null confirmed). **Zero CONFIRMED → the program success bar is not met. Decision Gate 9 is open: pivot-or-stop with the full ledger.**
