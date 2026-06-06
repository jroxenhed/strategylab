# F339 — Outcome Table Analysis Report

All analyses are **hypothesis-generating only, not confirmatory**.

## Anchor Gate

| # | Anchor | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1a | Momentum confirm mean excess 63d | ≈ +1.9 pts (±0.3) | +1.91% (cohort-level) | PASS |
| 1b | Momentum explore mean excess 63d | ≈ +5.4 pts (±0.5) | +1.31% (cohort-level) | FAIL — see investigation |
| 2 | Epistemics filing confirm excess 63d | ≈ +0.14 pts (±0.2) | +0.14% (cohort-level) | PASS |
| 3 | Deterioration D2 confirm excess positive | > 0 | +6.13% (cohort-level) | PASS |
| 4a | Table row count | > 1000 | 9,527 rows | PASS |
| 4b | Missing 63d for cohorts ≤2023 | < 15% | 0.0% | PASS |

### Anchor 1b Investigation

The stored artifact momentum_M1_explore_result.json is the conformant re-run (2,888 events). The "+5.4 pts" figure in the task spec matches the non-conformant CONFIRM run (H1 CONFIRMED: +5.394pp, n=7,130) per the verdict doc. The postmortem explicitly states "the headline advantage dropped from +5.4 points to +1.9 points" after conformant re-run. The explore artifact correctly yields +1.31% cohort-level; the labeling discrepancy is in the task spec, not the data.

## Analysis A — Effect CIs + MDE

Per (experiment, arm, horizon), bootstrapped (10k resamples, seed=42) over cohort-level means.

Key results at 63d:
- momentum_M1 confirm: mean +1.91%, CI [−0.29, +4.23], MDE 3.33% — CI spans 0
- momentum_M1 confirm 126d: mean +4.78%, CI [+2.42, +7.13] — CI fully positive
- deterioration_D2 confirm: mean +6.13%, CI [−0.96, +12.83], MDE 10.07% — underpowered
- deterioration_D2 confirm 126d: mean +11.21%, CI [+3.66, +19.12] — CI fully positive
- epistemics_price explore: mean +3.62%, CI [+0.86, +6.92] — CI fully positive (explore only)
- epistemics_filing confirm: mean +0.14%, CI [−5.42, +5.32] — consistent with null
- deterioration_D1 explore: extreme outlier (penny-stock collapses), median −1.42%, mean meaningless

## Analysis B — Momentum Decay Curve (500 sampled picks)

| Horizon | Mean absolute return | N |
|---------|---------------------|---|
| 5d | −0.29% | 429 |
| 10d | −0.16% | 429 |
| 21d | −0.94% | 500 |
| 42d | −0.03% | 426 |
| 63d | +1.50% | 500 |
| 84d | −0.05% | 426 |
| 105d | +0.78% | 423 |
| 126d | +3.14% | 500 |

Intermediate horizons (5/10/42/84/105d) are absolute returns, no excess. Pattern: flat/negative short-term, positive 63d+. Consistent with medium-term trend effect, not short-term continuation.

## Analysis C — D2 Confirm as-Long (corrected: cohort-level bootstrap)

| Horizon | N cohorts | N picks | Mean excess | 95% CI |
|---------|-----------|---------|-------------|--------|
| 21d | 16 | 570 | +1.79% | [−2.30, +5.67] |
| 63d | 16 | 570 | +6.13% | [−0.96, +12.83] |
| 126d | 16 | 570 | +11.21% | [+3.66, +19.12] |

**Correction note**: prior run bootstrapped 570 individual picks (too narrow — picks within a cohort share market conditions and are not exchangeable). Corrected run bootstraps 16 cohort means. The 126d CI widens from [+3.01, +10.60] to [+3.66, +19.12] but remains fully above zero. The 63d CI is now [−0.96, +12.83] (spans zero; prior [−1.70, +4.14] was spuriously narrow). The mean at 126d rises from +6.95% (pick-mean) to +11.21% (mean-of-cohort-means), matching Analysis A's cohort-level anchor.

The 126d excess CI is fully above zero. Strongest positive reading in this re-analysis. Not pre-registered as a long strategy — requires a new charter before being treated as evidence.

## Analysis D — Top-1 Concentration

composite_score field present in all artifacts. Top-1 vs all-picks mean excess_63:
- momentum_M1 confirm: +2.30% vs +1.29% (top-1 better)
- deterioration_D2 confirm: +13.75% vs +1.24% (top-1 much better)
- epistemics_price confirm: −26.38% vs −3.33% (top-1 much worse)
- epistemics_price explore: +14.97% vs +3.45%

Mixed results; extremely noisy (1 observation per cohort for top-1). Hypothesis-generating only.

## Plain-English Summary

We built a 9,527-row table from four completed experiments (momentum near 52-week highs, deterioration shorts, deterioration-reversal longs, price-vs-filing epistemics). Each row is one quarterly pick with returns at 1, 3, and 6 months, plus how much better/worse the pick did versus the average stock in the same period ("excess").

Four of five pre-stated checks passed. The one failure is a labeling issue in the task spec ("+5.4 pts for explore" actually described the older uncleaned confirm run); the clean data is correct.

Most effects are too small to distinguish from chance given 16–24 quarterly windows. The MDE (minimum detectable effect = the smallest real advantage our test design could notice) was typically 3–12% — large relative to the effects we saw. Three CI findings fully exclude zero as hypothesis-generating signals: (1) momentum at 126d confirm (+4.78%), (2) D2 reversal as-long at 126d (+11.21%), and (3) epistemics price in the explore window (+3.62%). None of these are confirmed; all require pre-registration before interpretation as evidence.

The strongest new hypothesis from this re-analysis: beaten-down stocks that kept filing on time ("D2 survivors") show +11.21% excess at 6 months with CI entirely above zero (+3.66% to +19.12%) — a potential "distress recovery as long" strategy worth pre-registering. (Prior run showed +6.95% with a CI only valid at pick-level, not cohort-level; the corrected cohort-level CI is wider but still fully positive.)
