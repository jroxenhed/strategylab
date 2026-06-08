# Premise Power Census — 2026-06-08

> **Status:** read-only feasibility measurement. No FDR alpha drawn. Findings do not constitute hypothesis tests or research verdicts. Computed on the 14900k worker (`strategylab-worker`) via `bin/worker-dispatch.sh`; results determinism-checked byte-identical to local (cf. F357). Calibration anchors A1/A2/A3 PASS.

## Bottom line (orchestrator synthesis)

**Power ceiling is rare-event-specific, not universal.** Which premise families have enough clean events at low enough dispersion to resolve a tradeable (1.0pp) edge?

1. **High-frequency public filings are ~3× more testable than insider clusters** — same ~24pp dispersion, ~3× the events → binary-test MDE 0.56–0.90pp, under the 1.0pp floor. PEAD (10-Q/10-K), 8-K 2.02 (earnings), 8-K 5.02 (officer change), 8-K 8.01. **The next charter should come from here** (add a dose score to the filing stream — F348/F370).
2. **R-2 distress recovery will return UNTESTABLE** — only 447 D2 events, MDE 4.63pp. The census saved the blind run (F372).
3. **R-1b's "calmer universe" fix is dead** — edge lives in small-caps (+7.6pp), gone in large-caps (−0.18pp); slicing only lowers power.

**The +1.5pp uniform "edge" is mostly a baseline artifact — now quantified (F371 placebo control).** For each family, a seeded control set of NON-event dates for the SAME tickers estimates the size/survivorship baseline; `net = event − control` is the event-specific component:

| family | event mean | artifact (control) | net (event-specific) | binary MDE |
|---|---|---|---|---|
| R-1b insider | +2.38 | +0.93 | **+1.46** | 1.01 |
| PEAD 10-Q/10-K | +1.50 | +0.94 | **+0.56** | 0.56 |
| 8-K 8.01 | +1.49 | +0.43 | **+1.05** | 0.90 |
| 8-K 5.02 officer | +1.70 | +0.75 | **+0.96** | 0.77 |
| 8-K 2.02 earnings | +1.54 | +1.11 | **+0.43** | 0.58 |
| 8-K no_target (control) | +1.05 | +1.08 | **−0.02** | — |
| R-2 distress | +4.44 | +0.02 | **+4.42** | 4.63 |

~0.9–1.1pp of the apparent filing edge is baseline artifact; the event-specific net is smaller (0.4–1.05pp) but mostly positive — and these families have the power to resolve it. **Negative-control sanity check:** the `no_target` 8-K bucket (filings with no recognized item) nets **−0.02pp** — a population with no real event content shows no event-specific effect, validating the placebo method. Still: the directional net is a *screening* read on a new instrument, not a verdict — any promoted premise re-tests on the real `event_study.py` harness (whose dose-response Q5−Q1 test cancels the common baseline automatically), never on these means.

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
| 8-K/2.02 | 49939 | 13067 | 23.55 | 0.577 | — | YES/N/A | 4356 | item score TBD |
| 8-K/5.02 | 27225 | 7298 | 23.38 | 0.767 | — | YES/N/A | 4294 | item score TBD |
| 8-K/8.01 | 33903 | 7409 | 27.54 | 0.896 | — | YES/N/A | 5953 | item score TBD |
| 8-K/1.01 | 19685 | 4068 | 30.96 | 1.360 | — | NO/N/A | 7524 | item score TBD |
| 8-K/multi_target | 15517 | 3782 | 24.26 | 1.105 | — | NO/N/A | 4621 | item score TBD |
| 8-K/no_target | 44247 | 9329 | 23.69 | 0.687 | — | YES/N/A | 4405 | item score TBD |
| R-2 D2 distress | 5033 | 447 | 34.90 | 4.626 | — | NO/N/A | 9565 | distress score TBD |

---

## Placebo-Control Benchmark (F371)

> **What this is:** For each family's valid events, a random control set of NON-event observations was drawn from the SAME tickers (K=3 per event, at least 63 trading days away from any real event for that ticker). The control's mean excess approximates the baseline/selection artifact. **net_excess = event_mean − control_mean** isolates the event-specific component. Seed=20260608 (deterministic).

| family / bucket | event_n | event_mean (pp) | control_n | control_mean (pp) | net_excess (pp) | interpretation |
|---|---|---|---|---|---|---|
| calibration (R-1b) | 3847 | 2.38 | 11490 | 0.93 | 1.46 | artifact ≈ +0.93pp; event-specific ≈ +1.46pp |
| R-1b sub-universe | — | — | — | — | — | no per-bucket placebo (uses stored excess) |
| PEAD 10-Q/10-K | 13828 | 1.50 | 41168 | 0.94 | 0.56 | artifact ≈ +0.94pp; event-specific ≈ +0.56pp |
| 8-K/2.02 | 13067 | 1.54 | 38843 | 1.11 | 0.43 | artifact ≈ +1.11pp; event-specific ≈ +0.43pp |
| 8-K/5.02 | 7298 | 1.70 | 21835 | 0.75 | 0.96 | artifact ≈ +0.75pp; event-specific ≈ +0.96pp |
| 8-K/8.01 | 7409 | 1.49 | 22011 | 0.43 | 1.05 | artifact ≈ +0.43pp; event-specific ≈ +1.05pp |
| 8-K/1.01 | 4068 | 1.61 | 12148 | 1.12 | 0.49 | artifact ≈ +1.12pp; event-specific ≈ +0.49pp |
| 8-K/multi_target | 3782 | 1.72 | 11242 | 0.86 | 0.86 | artifact ≈ +0.86pp; event-specific ≈ +0.86pp |
| 8-K/no_target | 9329 | 1.05 | 27838 | 1.08 | -0.02 | artifact ≈ +1.08pp; event-specific ≈ -0.02pp |
| R-2 D2 distress | 447 | 4.44 | 1338 | 0.02 | 4.42 | artifact ≈ +0.02pp; event-specific ≈ +4.42pp |

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

*Generated 2026-06-08T00:00:27Z by premise_power_census.py (F369)*
