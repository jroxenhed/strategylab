# F340 — Statistical Power Report
**Design power audit via synthetic edge injection (Newey-West corrected)**
Generated: 2026-06-06 | 500 reps × 8 designs × 6 uplift levels | 1500 tickers, 2015–2020

---

## What changed after the autocorrelation fix (COR-01)

**Old method:** one-sample iid t-test on the per-date excess series.
**New method:** Newey-West HAC t-test; lag L = max(0, ceil(63 / median_gap_trading_days) − 1) per design.

| Design | Median gap (trading days) | NW lag | Old MDE @80% | New MDE @80% | Change |
|--------|:---:|:---:|:---:|:---:|:---:|
| QUARTERLY-4 matched | ~63 | **0** | ~3.0 ppt | ~3.0 ppt | no change ✓ |
| QUARTERLY-4 fixed-40 | ~63 | **0** | ~3.0 ppt | ~3.0 ppt | no change ✓ |
| MONTHLY matched | ~21 | 2 | ~1.8 ppt | ~1.8 ppt | minimal |
| MONTHLY fixed-40 | ~21 | 2 | ~1.8 ppt | ~1.8 ppt | minimal |
| EVENT-TIME-100 matched | ~2 | 30 | ~1.9 ppt | ~1.9 ppt | minimal |
| EVENT-TIME-100 fixed-40 | ~2 | 30 | **~0.8 ppt** | **~0.8 ppt** | unchanged |
| EVENT-TIME-400 matched | ~0.7 | 91 | ~2.5 ppt | ~2.4 ppt | −0.1 ppt |
| EVENT-TIME-400 fixed-40 | ~0.7 | 91 | **~0.8 ppt** | **~0.8 ppt** | unchanged |

**Key finding:** The NW correction changes the headline MDE by ≤0.1 ppt for all designs.
The old E=0 FPRs were already within the correct binomial 99% CI [0.026, 0.076]; the correction
did not materially inflate the dense-design placebo rates. The power ranking and conclusions
are unchanged.

**Why the quarterly MDE is robust:** quarterly windows (63-day forward horizon, ~63-day gap)
do not overlap — L=0 so NW degenerates to the standard t-test. This design was the most
trustworthy anchor in the original report, and it remains so.

---

## Power Table (NW-corrected)
**Cell = fraction of 500 simulated experiments that correctly detected a synthetic edge.**
(% detected; 80% is the standard bar for a "powered" study.)

| Design | E=0 (placebo) | E=1% | E=2% | E=3% | E=5% | E=10% |
|--------|:---:|:---:|:---:|:---:|:---:|:---:|
| **QUARTERLY-4 matched** | 6.2% | 11.6% | 42.4% | 81.4% | 99.8% | 100% |
| **MONTHLY matched** | 6.6% | 34.6% | 92.0% | 100% | 100% | 100% |
| **EVENT-TIME-100 matched** | 6.0% | 33.0% | 86.6% | 100% | 100% | 100% |
| **EVENT-TIME-400 matched** | 6.8% | 25.6% | 68.4% | 95.6% | 100% | 100% |
| **QUARTERLY-4 fixed-40** | 7.4% | 10.4% | 44.6% | 80.8% | 99.6% | 100% |
| **MONTHLY fixed-40** | 7.6% | 34.0% | 91.4% | 99.8% | 100% | 100% |
| **EVENT-TIME-100 fixed-40** | 6.6% | **99.2%** | 100% | 100% | 100% | 100% |
| **EVENT-TIME-400 fixed-40** | 6.2% | **100%** | 100% | 100% | 100% | 100% |

**Designs explained:**
- "matched" condition = picks are scaled down proportionally so all designs have comparable total pick counts (~1500–2800). Isolates the calendar density effect.
- "fixed-40" condition = all designs use 40 picks per decision point. Shows combined effect of density + sample size.

---

## Minimum Detectable Edge at 80% Power (NW-corrected)

The **minimum detectable edge (MDE)** is the smallest real advantage a signal would need to have before this experiment design could reliably find it (detected in ≥80% of runs).

| Design | Decision points | Picks/point | **MDE @ 80%** |
|--------|:-:|:-:|:---:|
| QUARTERLY-4 matched | 23 | 40 | **~3.0 ppt** |
| MONTHLY matched | 69 | 40 | **~1.8 ppt** |
| EVENT-TIME-100 matched | 600 | 4 | **~1.9 ppt** |
| EVENT-TIME-400 matched | 1447 | 1 | **~2.4 ppt** |
| QUARTERLY-4 fixed-40 | 23 | 40 | **~3.0 ppt** |
| MONTHLY fixed-40 | 69 | 40 | **~1.8 ppt** |
| EVENT-TIME-100 fixed-40 | 600 | 40 | **~0.8 ppt** |
| EVENT-TIME-400 fixed-40 | 1447 | 40 | **~0.8 ppt** |

---

## F338 Smoke Gate Anchors

All four pre-stated anchors PASS (NW-corrected run):

| Anchor | Check | Result |
|--------|-------|--------|
| 1 | E=0 placebo rate within binomial 99% CI [0.026, 0.076] for every design | **PASS** (6.2%–7.6%, all within CI) |
| 2 | E=10 detection ≥95% for every design | **PASS** (all 100%) |
| 3 | Power non-decreasing in E within each design | **PASS** |
| 4 | Denser designs have equal-or-higher power at every E (fixed-40 condition, ±3ppt) | **PASS** |

**Anchor 1 updated:** The prior run used a hand-widened 6 ppt tolerance at E=0 (COR-04).
The new check uses the exact binomial 99% Clopper-Pearson interval: with n=500 reps and
true FPR=0.05, valid observed rates fall in [0.026, 0.076]. All designs pass this principled gate.

**Anchor 4 note:** The matched condition is excluded from Anchor 4. Monthly (69 dates × 40 picks) can outperform Event-Time-100 (600 dates × 4 picks) because power scales as sqrt(n_dates × picks_per_date): Monthly gives sqrt(69×40)=52.5 vs. sqrt(600×4)=49.0. Cutting picks from 40 to 4 requires 100× more dates to compensate, not 8×. This is a genuine finding, not a bug — the comparison isolates the clock effect only when picks are held fixed.

---

## Plain-English Closing Section
*(For a non-expert reader — every term defined inline)*

### The question
The StrategyLab signal program tested trading rules on a quarterly schedule: four fixed dates per year (February, May, August, November 15), picking 25–50 stocks on each date and measuring whether they beat the market over the next 3 months. After years of testing, the conclusion was "no edge found." We ran a simulation to ask: *if there had been a real edge, would this design have been able to find it?*

### How we measured it
We took actual US stock price data from 2015–2020 (a 6-year "explore era" — data from 2021 onwards is kept sealed to avoid self-fulfilling research bias). We built a table of forward 3-month returns for ~1500 stocks across every trading day. We then ran 500 fake experiments per design, each time artificially adding a known advantage (the "edge," measured in percentage points of extra return per pick) to randomly chosen stocks. After each experiment we ran a statistical test and asked: did it detect the advantage? The fraction of experiments where it said "yes" is the **power** (0–100%): 80% power is the standard scientific bar meaning "powered to detect."

**A note on the statistical test:** Adjacent forward-return windows in dense designs overlap heavily (two adjacent picks one day apart both measure most of the same 63-day price move). The standard t-test assumes all observations are independent; using it on overlapping windows understates uncertainty. This run uses a **Newey-West autocorrelation correction** (standard in financial economics) that accounts for this overlap. For the quarterly design there is no overlap (windows are ~63 days apart, exactly the window length), so the correction has no effect there. For the densest designs it substantially widens the standard error. The headline MDEs changed by ≤0.1 ppt versus the uncorrected run — the punchline is robust.

### The headline finding
**The old quarterly design needed a real edge of about 3 percentage points (3 ppt) before it could reliably detect it.** The momentum signal the lab measured in the confirm era produced roughly **1.9 ppt** of excess return. Since 1.9 is below 3.0, the quarterly design would have missed that result in most runs — it simply did not have enough statistical power. The program found "no edge" not because there was no edge, but because the experiment was under-powered for the size of edge that exists.

To put that concretely: if you ran the quarterly design 100 times on a world where a 1.9 ppt advantage was real, it would correctly report "edge found" only about **42 times** and incorrectly say "no edge" the other 58 times. That is not a reliable test.

### Why denser designs are much better
The fix is straightforward: look more often. Statistical tests become more reliable when they accumulate more independent measurements.

- **Monthly design (same 40 picks, 3× more dates):** MDE drops to ~1.8 ppt — just barely above the 1.9 ppt momentum result. Already on the cusp.
- **Event-time-100 with 40 picks (25× more dates, 40 picks each):** MDE drops to ~0.8 ppt. A 1.9 ppt real edge would be detected in essentially every run (100% at E=2).

The combined effect of more decision points *and* more picks per point is dramatic: the fixed-40 event-time designs can detect edges as small as 0.8 ppt — nearly 4× more sensitive than quarterly. **These numbers survive the autocorrelation correction because the denser designs accumulate enough observations that even the wider NW standard error still sees through a real 1.9 ppt signal.**

### The matched-condition surprise
The task asked us to compare designs with comparable total pick counts (so "more dates with fewer picks each" vs "fewer dates with more picks each"). Here the picture is more nuanced: **simply having more dates does not help if you cut picks proportionally.** The test's sensitivity scales as sqrt(n_dates × picks_per_date). Monthly (69 dates × 40 picks = score 52.5) outperforms Event-Time-100 (600 dates × 4 picks = score 49.0) because you need 100× more dates to compensate for 10× fewer picks — slicing picks to gain calendar density is a bad trade.

**Practical implication:** the right upgrade is not "look more often with less focus" but "look much more often with the same rigor per observation." Event-time-100 with 40 picks per date is the design that would have definitively resolved the program's central question.

### The punchline (survives the autocorrelation fix)
> The old quarterly design could only reliably notice an advantage bigger than **3.0 percentage points**; the momentum result of 1.9 ppt sits below that bar — meaning the design was structurally incapable of confirming it in the program. A monthly design would have been borderline; an event-time design with 40 picks per observation (used by most professional factor researchers) would have detected a 1.9 ppt edge with near certainty.
>
> The autocorrelation correction (Newey-West) does not change any of these numbers materially. The quarterly MDE is unchanged (non-overlapping windows, no correction needed). The dense-design MDEs shift by ≤0.1 ppt. The conclusion stands.

---

## Methodology Notes
- **Data:** 1500 US equity tickers, daily closes, explore era 2015-01-01 to 2020-12-31 (confirm era 2021+ never touched). Loaded from local disk cache; no network calls. Tickers with median explore-era price below $5 excluded (~30% of candidates). Returns clipped at [-99%, +500%] per date-column to prevent extreme outlier contamination of the mean.
- **Forward return horizon:** 63 trading days (~3 months), matching the program's measurement window.
- **Null calibration:** excess is defined as mean-pick-return minus universe-mean-return of NON-PICK tickers (COR-02 fix). Excluding picks from the baseline removes a small downward bias (~4% for n_picks=40, n_valid≈1000) and matches the program's "every OTHER qualifying stock" definition.
- **Statistical test:** one-sample Newey-West HAC t-test on per-decision-point excess returns (sorted chronologically); H₀: mean excess = 0; p < 0.05. Lag per design: quarterly L=0 (standard t-test), monthly L=2, EVENT-TIME-100 L=30, EVENT-TIME-400 L=91. Bartlett kernel weights.
- **Anchor 1 tolerance:** exact binomial 99% Clopper-Pearson interval [0.026, 0.076] for n=500 reps, replacing the prior hand-widened ±6 ppt tolerance.
- **Runs:** 500 Monte Carlo reps per cell; total 24,000 experiments. Runtime ~4 minutes.
