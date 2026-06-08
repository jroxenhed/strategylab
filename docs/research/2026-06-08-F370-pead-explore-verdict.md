# F370 — PEAD / earnings-surprise explore-0 — VERDICT: NULL (no callable dose)

*Explore-0 feasibility screen, non-ledgered (no confirm alpha spent). Run on strategylab-worker 2026-06-08, seed 20260608, matrix pin "universe medians from matrix build 2026-06-07". Design spec: [docs/plans/2026-06-08-F370-pead-surprise-charter-spec.md]. Driver: backend/research/run_f370_explore.py.*

## Plain English
We tested whether a company's fundamental **surprise vs its own past** — revenue/earnings growth, acceleration, margin shift, dilution, computed estimate-free (no analyst consensus) at the moment it files its 10-Q/10-K — predicts where the stock drifts afterward. **It does not.** No version of the surprise produced a tradeable, statistically real signal. We are spending no confirm budget and building no further instrument on this premise.

## Numbers (primary horizon 63 trading days)
Universe: matrix-pinned 4,678; explore 2015–2020; 61,926 raw 10-Q/10-K events → 16,247 passed the price floor; per-dose n after dose-null filtering below.

| Dose | Q5−Q1 | one-sided p (boot) | empirical MDE | Spearman ρ (n=5) | coverage |
|---|---|---|---|---|---|
| earnings_yoy (seasonal-random-walk SUE proxy) | **−2.02pp** | 0.999 | 2.17pp | −0.40 | 47.9% (n=7,776) |
| revenue_yoy | **+1.50pp** | 0.32 | 2.63pp | +0.30 | 65.2% (n=10,591) |
| composite z(earn)+z(rev_accel)+z(margin)−z(dilution) | **+0.44pp** | 0.49 | 2.32pp | +0.70 | 65.5% (n=10,634) |

**Graduation rule (pre-stated): none qualify.** Every dose's |Q5−Q1| is below its own empirical MDE and none is significant (p ≫ 0.05); earnings-surprise is even sign-negative. The census's ~1.8pp dose-gap MDE prediction held (came in 2.2–2.6pp on post-floor n) — the dose-response is underpowered AND the point estimates are near-zero, so this is a genuine null, not merely "couldn't see it."

## Announcement-to-filing gap lens (John's "measure the gap")
Linked each 10-Q/10-K to the most recent 8-K **item 2.02** earnings announcement filed before it (time-based match within 90 days; 8-K reportDate is the *announcement* date, NOT the fiscal period end — verified 0/N period match, so a period-key join is wrong). Matched **10,883 / 16,247 events (67%)**.
- Announcement→filing drift: **mean +1.41%, median +0.60%.**
- Correlation of that drift with the surprise dose: **≈ 0.04 (earnings), 0.04 (composite) — essentially zero.**

**Interpretation.** There IS a small positive drift between the announcement and the filing, but it does **not scale with the fundamental surprise**. So this is not cleanly "the surprise was already priced before the filing" (the axiom's strong form). The simpler, honest read: the estimate-free own-history surprise is a **weak return predictor in this universe — in neither window.** The small positive drifts (post-filing means +0.40/+1.37/+2.62pp at 21/63/126d; the +1.41% gap) are the **size/survivorship baseline** F371 already quantified, not signal — which is exactly why the dose-response (baseline-cancelling) is null while the raw means look positive.

## Decisions
1. **Close F370 at explore-0. No confirm touch. No std_sue build** — standardized SUE sharpens dose *ordering*; it cannot manufacture a signal that is absent in both the post-filing and announcement-to-filing windows, and earnings-surprise ordering points the wrong way.
2. The F348 surprise layer + this explore harness + the gap lens are validated, reusable instruments for the next premise.
3. Relationship to the axiom ("price leads, filings trail"): consistent in spirit (no post-filing drift to capture) but the gap lens shows the pre-filing drift isn't surprise-linked either — so the cleaner statement is "this estimate-free surprise carries little return information," reinforcing F371.

## Provenance / honesty
- Non-ledgered feasibility screen: `fdr_ledger_path=None` throughout; no FDR alpha spent; no confirm-window data analyzed (explore-split only; `assert max(event_ts) ≤ cutoff+1d` guards against confirm-era enumeration — 1,257 confirm-SPLIT events existed (boundary 2020 filings whose entry fell in 2021+ due to sparse price data) and were correctly excluded from analysis).
- Determinism: dose-response reproduced bit-identically across two worker runs.
- Review: review-wave (11 agents) confirmed 5 findings (incl. the parallel-loader crash the first worker run caught); all fixed. Gap-lens 8-K-2.02 matching was wrong on the first run (period-key join → 0 matches); rediagnosed (announcement-date semantics) and fixed to a time-based match, validated on real data (67% match, sane gaps).
