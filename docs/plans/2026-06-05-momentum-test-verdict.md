# MOMENTUM-TEST Verdict — Unit 6, Signal-Driven Research Program (Experiment 2 of 4)

> **CONFORMANCE AMENDMENT (2026-06-05, same evening):** the deterioration run exposed that universe-v2 price/volume floors were not enforced point-in-time in the config/null paths — 9.7% (explore) / 6.7% (confirm) of this experiment's events were sub-$5, and the exhaustive null medians included unfloored names, despite the charter specifying universe-v2. A quick floored recheck on the existing artifacts holds the H1 direction (confirm median excess +3.50pp floored vs +3.42pp unfloored; means deflate as vol-junk exits). Floors are being enforced (with counted exclusions) and **the experiment is being regenerated under conformance; the verdicts below are superseded pending the conformant rerun.** The charter is unchanged — this is conformance repair, not redesign.

**Date:** 2026-06-05 · **Charter:** `.run/MOMENTUM-TEST/charter.md`, sha256 `ffef4c05…c6189f`, frozen blind · **Alpha:** 25% program share → per-comparison α=0.00625 · **Protocol:** blind charter → implementation (161 tests) → explore (2015–2020, open) → sealed confirm (2021–2024, explore-blind judge) · **Config:** M1 — `pct_off_high ≤ 5%` + price > rising 200d SMA, long, universe-v2, cohort-exhaustive nulls

## Verdict: H1 CONFIRMED · H2 (gating) WEAKENED → momentum-as-such NOT confirmed

| Hypothesis | Explore (calibration) | Confirm (the test) |
|---|---|---|
| H1: 63td excess > 0 vs whole-cohort null | hit_v2 0.597, median +2.19pp, 18/24 cohorts | **CONFIRMED**: mean +5.394pp, 99.375% CI [+1.705, +11.895], agreement 0.875 (14/16), n=7,130 |
| H2 (necessary gate): excess survives same-vol-tercile stratification | — | **WEAKENED**: stratified mean +3.144pp, CI [−0.807, +9.687] touches zero (CI bar FAILED); agreement 0.625 ≥ 0.60 (agreement bar PASSED) |
| H3 (descriptive): horizon consistency | monotone +0.75/+2.19/+3.45pp | same-sign positive +1.43/+5.39/+6.64pp @21/63/126td |

Charter rule applied as frozen: *H1-pass + H2-fail = "beta-explained" → no CONFIRMED momentum claim.*

## What this means, plainly

1. **A cohort-relative excess is established; its explanation is not.** Names near 52-week highs in uptrends beat their same-cohort universe peers out-of-time at a strict α, in a 2021–2024 window that contains the 2022 drawdown (descriptive observation; the window was fixed by pre-registration, not chosen for adversity). But when each name is judged against *volatility-matched* peers, the excess can no longer be distinguished from zero at the pre-registered CI bar — note precisely: **H2's agreement bar passed (0.625 ≥ 0.60); only the CI-excludes-zero bar failed.** The honest claim: a positive tilt exists; whether it is momentum or a volatility/beta exposure is unresolved at this bar, and any retest must specifically target the CI bar (tighter stratification or more cohorts), not the direction.
2. **The gate did exactly what it was designed for.** Without H2, this would be the program's first CONFIRMED and a false-confidence milestone — the most documented anomaly family confirming on schedule. H2 was made *necessary* precisely because "momentum config rediscovers beta in a bull tape" was the plan's named High/High risk.
3. **WEAKENED ≠ REVERSED.** The stratified point estimate is positive (+3.14pp) with majority cohort agreement (0.625); it failed a strict bar, it did not invert. The charter's amendment rule applies: any sharpened retest (e.g., finer stratification, beta-regression control, larger window) is a **new pre-registered experiment**, not a reread of this one.
4. **Program accounting:** experiment 2 of 4 spent. Score: REVERSED (regime), WEAKENED (momentum). The program success bar (≥1 CONFIRMED config at pre-registered effect) is not yet met; two experiments remain (deterioration-short, epistemics ablation).

## What this does NOT say

- It does not say momentum is dead — it says *this config's excess is not distinguishable from a vol tilt at α=0.00625 with 16 cohorts*. A beta-controlled momentum design remains an open, pre-registerable question.
- It does not license trading the H1 result: the H2 gate exists because a vol tilt is cheaper to obtain directly and carries different risk.
- Survivorship (R5): universe-v2 is currently-listed-only. Near-high names that later delisted are invisible; the bias direction for *this* config is plausibly smaller than in crash-pond slices but is not zero and is not measured here.

## Process notes

- Two instrument defects were caught and fixed before any conclusion was read: the source-mode null gap (null_n=0, all excess None — U1 design gap; fixed with streaming cohort-exhaustive null aggregates, RED→GREEN, commit `ccf53d90bf08c2b428dceebbd93079b5eadefb57` 2026-06-05, backend/turnaround_validation.py) and a window-purity anchor omission in the predecessor judge's verdict script (caught by the fresh sealed judge's charter audit). The F338 face-validity anchors passed before interpretation: 7,130 events, 16/16 cohorts with null aggregates, 100% excess coverage, window purity verified.
- **Reproducibility caveat (adversarial review MV-03):** the verdict script's bootstrap CIs were computed without a fixed seed — H1/H3 runs produced slightly different lower bounds (+1.673 vs +1.705) around the same point estimate. The CONFIRMED verdict is robust across both draws, but the specific CI digits in this record are not bit-reproducible. The authoritative raw output is `.run/MOMENTUM-TEST/confirm-verdicts.json`; future charters must pin a bootstrap seed (folded into F338's smoke-probe standard).
- One judge agent terminated mid-watch (zombie pattern); the detached run survived it and a fresh sealed judge completed the verdict — crash-indifference by design.
- Artifacts: `.run/MOMENTUM-TEST/{charter.md, confirm-verdicts.md, confirm-verdicts.json}`; result JSONs under `backend/data/turnaround/momentum_M1_{explore,confirm}_result.json` (local, regenerable).

**Next per plan:** Unit 7 (deterioration-short, one run two questions) and Unit 8 (epistemics ablation), then Decision Gate 9 judges the program against its success bar.
