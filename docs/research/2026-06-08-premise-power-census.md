# Premise Power Census — 2026-06-08

> **Status:** read-only feasibility measurement. No FDR alpha drawn. Findings do not constitute hypothesis tests or research verdicts. Computed on the 14900k worker (`strategylab-worker`); results byte-identical to a local sanity run (cross-host determinism, cf. F357). Calibration anchors A1/A2/A3 PASS on the worker.

## Bottom line (orchestrator synthesis)

**The power ceiling that sank R-1b is NOT universal — it is specific to rare-event premises.** The question the census answered: which premise families have enough clean events, at low enough return dispersion, that the engine could resolve a tradeable (1.0pp) edge?

1. **High-frequency public filings are 3× more testable than insider clusters.** Earnings 8-Ks (item 2.02), quarterly filings (10-Q/10-K), and officer-change 8-Ks (5.02) all clear the 1.0pp floor as a *binary* "beats-market" test (MDE 0.56–0.77pp) — same volatility as insider clusters (~24pp std) but 3× the events. **This is where the engine can actually resolve an edge; the next charter should come from here** (most likely a PEAD/earnings-surprise design that adds a dose score to the 8-K 2.02 / 10-Q stream).
2. **R-2 distress recovery will be underpowered — do not spend the run blind.** Only 447 usable events (the D2 screen is brutally selective), MDE 4.63pp. Same wall R-1b hit. Reconsider before executing the approved R-2 charter; it needs a structurally bigger net or it returns UNTESTABLE.
3. **R-1b's "calmer universe" escape hatch is dead.** The edge lives in small-caps (Q1 <$1.5B: +7.6pp) and vanishes exactly where volatility is low (Q4 >$11.8B: −0.18pp). You cannot have the calm and the signal together. Slicing only ever lowers power anyway — the full-set binary test (n=4,245, MDE 1.006pp) is already R-1b's most powerful view, and it was borderline-significant.

**Honesty flag (do NOT over-read the mean-excess column).** Every filing family shows a near-uniform +1.2 to +1.7pp mean excess — earnings, officer changes, even no-target 8-Ks. That uniformity across unrelated event types is a baseline/selection artifact (size-weighting / survivorship in the matrix universe), **not signal**. Tested directly: fixing a real after-hours-filing look-ahead bug (COR-02) moved the means by <0.1pp, so look-ahead was *not* the cause — the artifact is structural. **The census's POWER numbers (n, std, MDE) are the trustworthy deliverable; the directional mean must not be believed until an F338 probe with a true point-in-time benchmark resolves it.** Any premise promoted from here re-tests on the real `event_study.py` harness (which handles entry timing correctly per F359), never on the census means.

---

**Key insight:** Two MDEs matter, not one.

| MDE | Formula | What it answers |
|---|---|---|
| One-sample | 2.802 × std / √n | Does this family beat the market on average? |
| Dose-gap Q5−Q1 | 2.802 × √(var_q5/n_q5 + var_q1/n_q1) | Does the effect scale with a score? |

**Caveat:** MDE assumes iid events. Cross-sectional correlation on shared entry dates understates the true penalty. MDE is a power-screening heuristic, not a significance verdict.

---

## Ranked Table (headline: 63-trading-day horizon)

| family / slice | n_raw | n_valid | std_63 (pp) | MDE_1samp_63 (pp) | MDE_gap_63 (pp) | testable 1.0pp (1samp/gap) | n needed for 1.0pp | extractor owed |
|---|---|---|---|---|---|---|---|---|
| calibration (R-1b full) | 4245 | 4245 | 23.39 | 1.006 | 3.375 | NO/NO | 4296 | — (anchor) |
| R-1b/Q1 (<$1.5B) | 736 | 736 | 34.57 | 3.570 | — | NO/N/A | 9382 | score TBD |
| R-1b/Q2 ($1.5B-$3.8B) | 759 | 759 | 20.68 | 2.104 | — | NO/N/A | 3360 | score TBD |
| R-1b/Q3 ($3.8B-$11.8B) | 748 | 748 | 19.10 | 1.957 | — | NO/N/A | 2865 | score TBD |
| R-1b/Q4 (>$11.8B) | 751 | 751 | 12.07 | 1.234 | — | NO/N/A | 1145 | score TBD |
| R-1b/no-MC remainder | 1251 | 1251 | — | — | — | — | — | — |
| PEAD 10-Q/10-K | 68875 | 13828 | 23.42 | 0.558 | — | YES/N/A | 4307 | surprise definition |
| 8-K/1.01 | 19685 | 4068 | 30.96 | 1.360 | — | NO/N/A | 7524 | item score TBD |
| 8-K/5.02 | 27225 | 7298 | 23.38 | 0.767 | — | YES/N/A | 4294 | item score TBD |
| 8-K/8.01 | 33903 | 7409 | 27.54 | 0.896 | — | YES/N/A | 5953 | item score TBD |
| 8-K/2.02 | 49939 | 13067 | 23.55 | 0.577 | — | YES/N/A | 4356 | item score TBD |
| 8-K/multi_target | 15517 | 3782 | 24.26 | 1.105 | — | NO/N/A | 4621 | item score TBD |
| 8-K/no_target | 44247 | 9329 | 23.69 | 0.687 | — | YES/N/A | 4405 | item score TBD |
| R-2 D2 distress | 5033 | 447 | 34.90 | 4.626 | — | NO/N/A | 9565 | distress score TBD |

---

## Plain-English Summary Per Family

### Calibration (R-1b full universe — anchor validation)

The R-1b insider-cluster study produced 4245 valid events (floor-passing, 2015-2020 explore split) with a 63-day excess standard deviation of 23.39 percentage points. The one-sample MDE is 1.006pp — meaning this family can detect a mean excess of about 1.01pp at 80% power, well below the 1.0pp tradeable floor. The dose-gap MDE (Q5 vs Q1) is 3.375pp, above the 1.0pp floor — which is exactly R-1b's UNTESTABLE-underpowered verdict for the dose-response question. All calibration anchors A1/A2/A3 pass, validating census mechanics.

### R-1b Sub-Universe (calmer MC quartile buckets)

Of the 2994 valid R-1b events with non-null market cap, bucketed by MC quartile (p25=$1.5B, p50=$3.8B, p75=$11.8B). Each quartile has fewer events (n ≈ 748), which pushes MDE up. The table shows whether any quartile's lower std compensates for the reduced n. Verdict: if no MC bucket drops MDE_gap below 1.0pp at its own n, the 'calmer universe' lever is dead for R-1b. Note: realized trailing vol bucketing skipped — requires price history per event; deferred to worker.

### PEAD / Fundamental Surprise (10-Q and 10-K filings)

Scanned 8132 / 8132 submission files. Found 68875 raw 10-Q/10-K filing events (2015-2020) for universe tickers. After matrix join: 13828 valid events with 63d excess. Std = 23.42pp, MDE_1samp = 0.558pp. No surprise score built yet — this is counts + dispersion only. Extractor owed: surprise definition (estimate-free YoY-accel or actual-vs-trailing).

### 8-K Item-Type Drift

Scanned 8132 / 8132 files. Total raw 8-K events 2015-2020: 190516. Split by item code: 2.02 (earnings results), 5.02 (officer change), 1.01 (material agreement), 8.01 (other), and other/multi. Item types differ markedly in volume and dispersion — see table for per-item MDE.

### R-2 Distress Recovery (D2 predicate)

Applied D2 price gates (Gate A ≥50% crash, Gate B ≤25% above 1yr low, Gate D ≥252 bars, revenue veto OFF) to all 10-Q/10-K filing dates 2015-2020. This is a price-only approximation — no revenue veto. D2-state events: 5033 (from 68875 raw filings). Std = 34.90pp, MDE_1samp = 4.626pp. This answers: will the already-approved R-2 run hit the same underpowered wall R-1b did?

---

*Generated 2026-06-07T23:02:39Z by premise_power_census.py (F369)*
