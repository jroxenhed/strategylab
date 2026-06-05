# REGIME-TEST Verdict — Unit 5, Signal-Driven Research Program (Experiment 1 of 4)

**Date:** 2026-06-05 · **Charter:** `.run/REGIME-TEST/charter.md`, sha256 `d5da66aa…9319e1`, frozen before any outcome data was consulted · **Alpha budget:** 25% program share (α=0.0125 on H1) · **Protocol:** blind charter → implementation (charter-conformance checked) → explore (2015–2020, open) → sealed confirm (2021–2024, explore-blind judge)

## Verdict: H1 REVERSED · 5/6 UNTESTABLE · regime instrument demoted

| # | Hypothesis | Window result |
|---|---|---|
| 1 | H1: RISK_ON > NEUTRAL @63td (primary) | Explore: WEAKENED (direction held, +4.35pt, agreement 0.857, CIs overlap) → **Confirm: REVERSED** (RISK_ON 0.5143 [0.4775–0.5512] vs NEUTRAL 0.5152 [0.4650–0.5654]) |
| 2 | H1: NEUTRAL > RISK_OFF @63td | UNTESTABLE both windows (RISK_OFF: 0 cohorts) |
| 3 | H1: RISK_OFF ≥ STRESS @63td | UNTESTABLE both windows |
| 4 | H2: STRESS lowest @63td | UNTESTABLE both windows |
| 5 | H3: rank ordering @21td | UNTESTABLE both windows |
| 6 | H3: rank ordering @126td | UNTESTABLE both windows |

Cohort coverage: explore RISK_ON 17 / NEUTRAL 4 / STRESS 3 / RISK_OFF 0; confirm RISK_ON 9 / NEUTRAL 5 / STRESS 2 / RISK_OFF 0.

## What this means, plainly

1. **The pond's regime effect did not transfer.** The 24→84% per-year swing that motivated regime-first sequencing was measured on the abandoned washed-out pond with a touch metric. On the liquid universe-v2 with horizon-end cohort-relative metrics, the hypothesized separation **failed to appear out-of-time: the formal verdict is REVERSED** per the charter's rule (point estimate landed marginally inverted — explore's +4.35pt became −0.09pt), with fully overlapping CIs — practically a dead heat, formally a reversal. The plan's transfer-validity caveat (document review, adversarial P1) was correct.
2. **The state design starves its own tails.** RISK_OFF fired 6 days in a decade (daily state counts: RISK_ON=1697, NEUTRAL=485, STRESS=328, RISK_OFF=6 — generation log `.run/REGIME-TEST/generation2.log`; artifact `regime_states.json`, regenerable, charter-sha-pinned), because S4-first evaluation absorbs risk-off days into STRESS — so five of six ledger comparisons were structurally UNTESTABLE. A future regime design must validate state *occupancy* against cohort cadence before pre-registering ordering hypotheses on rare states. Charter was frozen; this is recorded as a design lesson, not retro-fitted.
3. **Per the stall guard (John, 2026-06-05):** Phase 3 proceeds **unconditioned**; regime becomes a *reporting dimension only* (per-state breakdowns still appear in config verdicts as descriptive context; no conditioning, no gating).
4. **Alpha accounting:** H1's 0.0125 share is spent. Remaining program budget covers the three planned configs (momentum, deterioration-short, epistemics ablation) at their pre-registered shares.

## What this does NOT say

- It does not say "regime doesn't matter" — it says *these four states, at quarterly cohort cadence, on this universe, at these horizons* don't predict base rates. Daily-cadence regime effects remain untested (out of scope; noted in plan's Deferred).
- It does not say the direction is genuinely negative: REVERSED here means the *positive* hypothesis failed and the point estimate happened to land −0.09pt — a magnitude indistinguishable from zero. Do not read it as evidence for the opposite ordering, and do not soften it to WEAKENED either (the charter's rule is direction-of-point-estimate, and it was applied as frozen).
- It does not retro-validate the pond observation either; that number stays pond-local.

## Process notes

- One implementation bug caught and fixed before any results were read: tz-aware constituent indices silently zeroed breadth (all-WARMUP artifact); root-caused via reproduction, fixed with RED→GREEN regression tests (`0a91fb8`). The corrected artifact passed face-validity probes (2020-03-20 STRESS, 2022-06-15 STRESS, bull windows RISK_ON) before the test ran.
- Artifacts: `.run/REGIME-TEST/{charter.md, explore-result.json, confirm-result.json, confirm-verdicts.md}` (local); `backend/data/turnaround/regime_states.json` (regenerable, charter-sha-pinned).

**Next per plan:** Phase 3, Unit 6 (momentum config) — unconditioned, with regime as a reporting dimension.
