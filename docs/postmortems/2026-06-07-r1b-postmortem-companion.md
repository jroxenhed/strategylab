# R-1b Postmortem — the Premortem's Companion, Written the Same Day

*Written 2026-06-07, hours after [the premortem](2026-06-07-r1b-premortem-mission-critical-practices.md) and hours after the run it was bracing for. Read the premortem first; this document grades it against reality. Plain English; the premortem's glossary covers both documents.*

---

## What happened

R-1b ran at full scale on the worker: 38,425 candidate insider-buying events (2015–2020) → 19,374 after de-clustering → 2,964 fully valid for the dose-response test. The parallel harness processed all of it in a ~35-second CPU spike (screenshotted for posterity).

**The verdict: UNTESTABLE — underpowered.** And the texture of that verdict is the whole story. The evidence was the premise's strongest ever: the heaviest-insider-buying bucket beat the market by **+3.28pp** over 63 trading days (and its own industry peers by +3.76pp), the dose ladder ordered correctly, all three hypotheses survived the multiple-testing correction, and the result was sign-stable under all nine pre-registered perturbations. Every robustness check we owned, passed.

And the §3a power gate — written before any outcome existed — still said no: the instrument can only reliably *see* gaps of 3.40pp or larger, and the charter demands sensitivity down to 1.0pp (the smallest edge worth trading at this account scale). An instrument that can't see small edges doesn't get to crown a big-looking one; the sealed confirm window stays sealed, and no alpha was spent. Getting the required sensitivity needs roughly **12× more valid events** than 2015–2020 contains.

The anchor probe on the artifact: **15 PASS / 0 FAIL / 0 NOT-RUN** — the first perfect full-scale anchor sweep in program history. The "no" stands on the program's most-validated instrument.

## The premortem scorecard

Hours before the run, we wrote eight stories of how R-1b could fool us. Here is how each fared on contact:

| # | Premortem failure story | What reality said, same day |
|---|---|---|
| 1 | Survivorship — universe has no dead companies | **Still the one on the wall** — but quantified tighter than feared: at full scale only 8.4% of events lacked price data (under the 10% suspicion threshold; the calibrate's scary 20% was early-sample bias). The structural caveat stands: any positive reading is "edge among survivors" until F318. |
| 2 | Timestamps lied | Did not occur — the morning's 19/19 census held; entry timing is conservative by construction. |
| 3 | We peeked without noticing | Did not occur — and is now structurally moot for this run: the verdict forbids unsealing anything. |
| 4 | One bad join poisons everything | Did not occur at scale; every join/drop/collision counter populated and sane (1,725 amendments superseded, 1,509 collisions kept+counted, 5,054-class ticker fallbacks counted). |
| 5 | Too weak a test, and we believed a fluke | **Happened — in the GOOD direction.** The power gate fired exactly as designed, refusing to bless a 3.3pp signal an underpowered instrument couldn't certify. This was the premortem's most-defended item, and the defense is what produced the verdict. |
| 6 | One-era / one-regime artifact | Couldn't reach judgment (no confirm), but the lenses reported honestly: positive in NEUTRAL and STRESS, near-flat in RISK_ON — exactly the kind of structure a future R-1c design must reckon with. |
| 7 | Costs ate it | Untested at playbook level (no playbook exists — nothing confirmed). The 4bps model was applied throughout. |
| 8 | Shared blind spot in instrument + tests | The night's recurring villain, caught repeatedly *before* the run: the fabricated `issuer_cik` fixture field, the Saturday-demanding validator, the NaN comparator. Each caught by a different layer. None survived to touch the result. |

## The catches ledger (one night, one table)

| What was wrong | What caught it |
|---|---|
| "0.6% suspicious timestamps" was measured on the wrong population (true: 0.0016%) | Pre-stated anchor A1 failing loudly |
| Blind author invented a plausible anchor list | Informed-orchestrator verification of blind output |
| Test fixtures fabricated a field real data lacked (`int('')` crash) | First real-data contact (F338) |
| Strict-mode validator demanded medians for a Saturday | The strict gate itself, failing loudly on the wrong thing |
| Parallel-vs-serial "FAIL" that was NaN≠NaN | Reading the diff before believing the verdict |
| Driver lambda couldn't cross process boundaries | Gate calibrate (probe proved the engine, not the plane) |
| 95% of events scoreless (shares cache was R-1's 312-company sample) | Pre-launch coverage re-check |
| Worker missing regime states + the real FDR ledger | One WARNING line in a live-tailed run.log |
| STRESS regime labeled "evidential" against charter text | Gate read of the final report |

Nine catches, nine *different* tripwires. No single defense caught more than two. That's what defense-in-depth means in practice.

## What changed the same night (compounding, not just fixing)

- **F364** — review findings citing population statistics must state the population measured (from catch #1) — now in the playbook and the review-wave prompts.
- **F365/F366** — harness and ingest parallelized, both bit-exactness-proven; *parallelize-by-default* recorded as a standing directive (John: "fix the parallel track, don't discard it").
- **F367** — STRESS cells now always print non-evidential, per charter.
- **F368** — pre-flight data-artifact manifest (hard-fails listing everything missing at once), worker sync script, and a **negative fetch cache**: dead-ticker fetch failures now persist with a TTL, so the ~1,400-strong 404 ceremony runs once ever (0.2ms warm vs up to 2.1s cold, measured). The 8-minute runup is gone for every future run.
- The FDR ledger grew 3 → 5 entries, cross-host, with a verified clean-extension install — the program now has a working pattern for remote runs that draw on shared multiplicity accounting.

## Epilogue: the thesis got its proof

The premortem's Part 3 argued that stochastic builders inside verification machinery yield deterministic systems. Tonight the machinery processed five-plus genuinely random mistakes, consumed them all, and emitted a bit-reproducible artifact (same seed + same frozen vintage → same number to the last digit, across architectures and execution modes).

But the sharper proof is the verdict itself. A system optimized for *feeling* productive would have shipped the +3.3pp — it survived FDR, it survived perturbation, it survived the peer lens, and everyone in the room wanted it to be real. The system we actually built said: *the instrument that found this cannot certify it; come back with a bigger net.* The most expensive failure mode in trading research is the flattering false positive, and the night's final act was the machine declining one.

The premise stays open. The next question — R-1c with a longer period, relaxed floors with stated caveats, or run R-2 first — belongs to John's gate, with the survivorship caveat (item #1, still on the wall) weighing on all three options.

*Months of scaffolding, thirty-five seconds of physics, and an honest "not yet." Working as designed.*
