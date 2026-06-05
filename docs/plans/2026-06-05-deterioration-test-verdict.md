# DETERIORATION-TEST Verdict — Unit 7, Signal-Driven Research Program (Experiment 3 of 4)

**Date:** 2026-06-05 · **Charter:** `.run/DETERIORATION-TEST/charter.md`, sha256 `716c16b2…d00c`, frozen blind · **Alpha:** 25% share, per-comparison α≈0.004167, seeded bootstrap (20260605) · **Protocol:** blind charter → implementation (15 tests + F338 real-cohort smoke probe pre-launch) → explore (2015–2020, open) → sealed explore-blind confirm (2021–2024) · **Run of record: D2 (price-only)** — the fundamentals-veto leg was declared UNVIABLE by the charter's own frozen fallback (no-fundamentals fraction 65–72% ≫ the 40% threshold); the veto leg is **UNTESTED**, not refuted.

## Verdict: H1 WEAKENED · H2 WEAKENED · Q2 WEAKENED — short premise not confirmed; point estimates run against it

| Hypothesis | Confirm window (2021–2024, 570 events, 16 cohorts, floors enforced) |
|---|---|
| H1: 63td NET-of-borrow short excess > 0 | **WEAKENED**: −7.89pp (negative = names fell *less* than cohort peers), seeded CI [−17.35, +2.27], cohort agreement 0.25. Not REVERSED only because the CI crosses zero. Borrow treatment: gross sign-inverted per charter §4, NET deduction 1.766pp (10%/yr × 63td + slippage); calendar-proxy borrow variant does not change the verdict. |
| H2 (vol-tercile gate) | **WEAKENED**: stratified NET −4.81pp, CI [−12.47, +3.39], 570/570 events placed in terciles |
| Q2 (drag-segment: does excluding these names raise the null median?) | **WEAKENED**: −6.32pp, CI [−17.51, +1.76]; drag in only 3/16 cohorts — deterioration names *rebounded above* the null in most cohorts |

R1 accounting: 142.5 realized events/yr vs 105 declared — **no shortfall** in confirm (explore ran at ~64/yr below declaration; recorded). Floors held (min entry $4.97 = one event of as_of-selection vs next-bar-entry drift; none below $4.50).

## What this means, plainly

1. **The in-house premise is gone.** Run-1's miss-list signature — crashed price, fundamentals still printing — was the program's only candidate with internal evidence. On a clean, investable universe ($5/500k floors, point-in-time), shorting that signature *loses*: the names mildly outperform their cohorts at 21–126td. The old signature's apparent decay lived in the sub-$5/illiquid segment the floors exclude — exactly the segment where survivorship-corrupted data (and unborrowable names) make any measurement, and any trade, unreliable.
2. **The conservative bias makes this stronger, not weaker.** Survivorship removes delisted would-be short winners, biasing *against* H1 (charter §6). H1 failed anyway — with the bias in its favor it still couldn't reach positive territory.
3. **Symmetric echo of the pond's lesson.** The original turnaround thesis died trying to *buy* this class of names; the inverted thesis now fails trying to *short* their clean-universe cousins. The washed-out segment offers neither a long nor a short story that survives honest measurement at these horizons — consistent with the EDA's "the null is structure, not opportunity" reading.
4. **The veto leg remains an open question by design.** D1 (with the fundamentals veto) was never tested — EDGAR facts coverage on crashed small names is 28–35%, below the frozen viability bar. Testing it requires the facts-coverage problem solved first (same family as the stratified-Form-4 prereq in IDEAS.md). No verdict is claimed on it.

## What this does NOT say

- It does not validate *buying* crashed names: WEAKENED-negative point estimates with CIs crossing zero are not a long signal; the EDA's nonstationarity finding applies in full.
- It does not refute deterioration-shorting below the floors — it says that segment is unmeasurable with free data (and likely untradeable: borrow, liquidity) — a statement about instruments, not markets.
- It does not test the fundamentals-veto variant (UNTESTED per the frozen fallback).

## Process notes

- F338 enforced end-to-end for the first time: real-cohort smoke probe before launch (passed); face-validity anchors caught the original D1 artifact's corruption (split-damaged frames, sub-$1 entries, mean −13,768pp) BEFORE interpretation; the universe-floor enforcement gap it exposed was fixed program-wide (commit `9e6795f…`, shared `floor_status()` helper, applied symmetrically to candidates and nulls) and triggered the momentum conformance rerun.
- The charter's pre-frozen fallback rule (>40% no-fundamentals → D2 price-only) fired exactly as designed — no mid-experiment judgment call was needed.
- Artifacts: `.run/DETERIORATION-TEST/{charter.md, confirm-verdicts.md, confirm-verdicts.json, compute_verdicts.py}`; result JSONs `backend/data/turnaround/deterioration_D2_{explore,confirm}_result.json` (local, regenerable).

**Program state after experiment 3:** REVERSED (regime) · WEAKENED (momentum, conformant rerun complete 2026-06-05: H1 +1.91pp CI includes zero, H2 both bars failed) · WEAKENED-against (deterioration-short). Remaining: U8 epistemics ablation, then Decision Gate 9 against the program success bar (≥1 CONFIRMED config).
