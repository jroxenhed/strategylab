# Turnaround Screen — Phase-2 Run 1: Full Write-up & Handoff

**Date:** 2026-06-04 → 06-05 (overnight interactive session)
**Spec:** [poor-mans-bloomberg-spec.md](poor-mans-bloomberg-spec.md) · **Shipped as:** F311 + F312 (commit `85d532f`, follow-ups through `bc68022`)
**Status: VERDICT = KILL (as implemented). Phases 3–5 stay gated. Rerun protocol queued — see §8.**

This doc is the complete pickup point for a fresh session. Read this + TODO.md Up Next and you have everything.

---

## 1. What was built (one screen)

| Module | What | Key invariant |
|---|---|---|
| `backend/edgar.py` | SEC EDGAR client: rate-limited `_get` chokepoint, retry/backoff, atomic on-disk TTL cache, XBRL quarterly extraction (4-tag revenue fallback, Q4-from-annual), Form 4 XML parsing, EFTS buyback search | all HTTP through `_get`; tests monkeypatch it |
| `backend/turnaround.py` | Point-in-time filter engine. 4 pillars: washed-out price / fundamental inflection / P/S valuation / conviction flags. Cheap-first funnel | **every function takes `as_of`**; `filed <= as_of` everywhere; injectable `bars_loader` |
| `backend/turnaround_validation.py` | Quarterly as-of historical scans through the same filter code path; entry/exit with slippage+commission; Wilson CIs; hit rate vs null; miss list; per-ticker overlap cooldown | one network fetch per symbol (memoized full-span loader); conviction skipped by design (additive-only) |
| `backend/routes/turnaround.py` | `POST/GET /api/turnaround/scan[/status]`, `/watchlist`, `/validate[/status|/result]` — async via BackgroundTasks + to_thread, asyncio.Lock guards, persisted JSON | results at `backend/data/turnaround/*.json` |

Build shape: explore+spec (sonnet, orchestrator addendum corrected 4 brief flaws) → 3 parallel impl lanes → 5-persona Tier-C review (52 findings, 8 P0, 38 fixed by 2 parallel fixers) → 878 backend tests green. Run artifacts: `.run/F311-turnaround/` (plan.md, decisions.md, review JSONs, verify.md).

## 2. The run

- **Universe:** 8,909 names (SEC company_tickers.json minus dot/dash/long tickers and ETF/Trust/Acquisition-Corp titles). Known residual junk → F319.
- **Config:** locked FilterParams defaults (spec §4, never tuned), 36 quarterly as-of dates (15 Feb/May/Aug/Nov, 2015–2023), +50%-touch hit within 12m, 2bps slippage, commission-free, `max_universe=10000`.
- **Mechanics:** 105.6 min total. ~60–75 min was the date-#1 price wall (~8.9k sequential yahoo fetches into the in-process memoized loader). 1,039 fetch failures (12%, surfaced not hidden). 3,156 overlapping events suppressed (per-ticker cooldown). 2,064 unique tickers qualified washed-out at some date.
- **Cache built (persists, makes reruns minutes-scale):** `backend/data/turnaround/edgar_cache/` — ~2,000 companyfacts files, **3.9GB** (gitignored). Prices were NOT persisted (in-process memo only).
- **Result payload:** `backend/data/turnaround/validation_result.json` or `GET /api/turnaround/validate/result`.

## 3. The verdict

| | Signal (all gates) | Null (washed-out only) |
|---|---|---|
| Events | 125 | 3,034 |
| Hits (+50% touch ≤12m) | 44 | 1,384 |
| **Hit rate [Wilson 95%]** | **35.2%** [27.4–43.9] | **45.6%** [43.9–47.4] |
| Net return mean / median / p25 / p75 | +8.8% / +12.9% / −28.6% / +51.7% | not reported (gap → F327) |

**The full-screen picks hit LESS often than merely-washed-out names.** Signal CI sits entirely at/below the null. Mean +8.8% is 2015–23 beta, not edge. Spec §8 gate verdict: no UI, no catalysts, no bots for this config.

## 4. The autopsy (why it failed — the interesting part)

Miss-list top 10 by the screen's own conviction: GPRO 2015 (−54%), WTTR 2018 (−25%), SND 2019 (−38%), INGN 2022 (−27%), RNG 2023 (+13%), LGND 2019 (−14%), CRSR 2023 (−49%), BOOM 2023 (−52%), PUMP 2019 (−42%), ENPH 2023 (−36%).

**Not one is a recovering trough. Every one is a decelerating former highflier whose price crashed before its filings.** The implemented inflection gate ("revenue YoY ≥ 0 for ≥2 consecutive quarters") admits names whose revenue was *always* positive but rolling over — the exact opposite of the thesis. The market wasn't wrong about these names; it was early. **Lesson: in washed-out land, price leads and filings trail — conditioning on good trailing fundamentals after a crash selects for names where the bad news hasn't finished arriving.**

**Root cause is a spec-conformance bug, not a thesis refutation:** spec §4 says revenue "has **turned** positive" — *turned* implies a prior negative stretch. The implementation never required the sign change. GPRO-2015 passes the code and fails the spec's sentence. Fix = F326. **Epistemics, stated plainly: we killed a proxy, not the thesis. The thesis gets its first real trial at the rerun.**

### Caveats cutting both ways
- Null is junk-contaminated (warrants/shells, F319) and structurally more volatile; touch-based hit metric favors volatility → gap size unreliable.
- Survivorship inflates both rates, the null more. True null < 45.6%.
- Net of all caveats: nothing suggests the signal *beats* the null — the only question the gate asks.

## 5. Bug ledger (found live, all by positive controls — none by review or tests)

1. **tz-aware index TypeError** — `_fetch()` frames carry ET tz; all synthetic fixtures were naive; `run_filter`'s per-symbol try/except swallowed the crash → both early live runs returned silent zeros that looked legitimate. Found by PTON@2022-09-15 positive control. FIXED (`_df_up_to` strips tz once + regression tests).
2. **Conviction pillar = 4-bug silent dud (F321, open, [next])** — EFTS `ciks` as bare int (silent 0 hits); missing `dateRange=custom` (HTTP 500); parser reads `accession_no`/`form_type` vs real `adsh`/`root_forms`; Form 4 index URL `{accession}-index.json` (404) vs real `index.json` + `directory.item[]`. Found by AAPL positive control (36 real EFTS hits exist; 587 Form 4s in window). Fix + REQUIRED recorded-fixture positive-control tests.
3. **Dual-class shares P/S gap (F322, open)** — `get_shares_outstanding` returns None for PTON/NKE/EL (class A/B filers); 3 of 7 live-tested names. Sum across share-class contexts.

**Session lesson, proven three times: graceful degradation without positive controls manufactures silence that looks like data.** Any screen that can return "no candidates" needs a known-positive control, every time.

## 6. Live ticker case studies (the seven-name tour)

| Name | Verdict | Why it matters |
|---|---|---|
| NKE | washed-out ✓, inflection fail by GM −0.53pp | the near-miss archetype → F324 margins/forming-tier |
| EL | inflecting ✓ (3q rev, 3q NI, GM+), hate gone | first pillar-2 pass; margin-repair visible in XBRL |
| INTC | +516% off low, P/S 6.1 — the chase | "catalysts are the move" encoded correctly |
| TGT | cheap ✓ (P/S 0.37) but +51% off low | valuation alone can't gate |
| MRNA | P/S 0.7 "cheap" + revenue −80% | the value-trap separation working |
| AMC | bounced +106% off low + melting | correct double reject |
| PTON | reject today; 2022 = null-candidate that bounced +59% | null semantics demonstrated point-in-time |

**ENPH coda (key anecdote):** the screen's #10-conviction pick (2023-11, −36% in its window) is **John's best long trade of 2026** — he manually waited for the real turn, exactly what Stage-2 + the sign-change gate are meant to codify. "Right name, wrong clock" ≠ "wrong name" → F328 miss-horizon diagnostic; **ENPH 2023→2026 point-in-time timeline is the agreed case-study vignette once F326 lands.**

## 7. Design review outcomes (gates discussion with John)

Keep binary membership (validation sets + auditability; a score is just a cutoff plus overfit surface). Add zero-knob transparency instead:
- **F324** per-gate signed margins + "forming" near-miss tier (fail exactly one sub-check) + inflection stage labels (early/mid/confirmed — revenue→margins→NI sequence).
- **F325** dose-response validation cohorts (all-gates vs miss-one vs null) — monotonicity is a harsher falsification than signal-vs-null.
- **F317 design note:** sticky watchlist membership — Gate 1 requires below-200dMA while Stage-2 triggers on reclaiming it; without stickiness the system structurally can never fire.

## 8. THE RERUN PROTOCOL (the actual next step, in order)

1. **F319** universe hygiene v2 (clean the null: warrant/unit/right suffixes, Q-shells, foreign OTC; consider company_tickers_exchange.json). A PASS only counts post-fix; a kill still counts.
2. **F321** conviction plumbing (4 fixes + AAPL recorded-fixture positive controls). Regenerate live watchlist after.
3. **F326** sign-change inflection gate (≥1 negative YoY quarter before the positive run). Spec conformance — be transparent it was surfaced by results.
4. **Fold in:** persist event-level outcomes table (every (ticker, as_of) event: pillar margins, is_null, forward returns) — turns F325/F327/F328 + the deterioration-short check into queries instead of runs. Also F327 (null return distribution in payload).
5. **Re-run** full validation on warm caches (~minutes-tens-of-minutes, not 105) — `POST /api/turnaround/validate {"params": {}, "start_year": 2015, "end_year": 2023, "max_universe": 10000}` — and report old vs new side by side.
6. **Then** F328 ENPH vignette + dose-response read → decide whether the thesis lives (F317 ungates) or dies (pivot to the premise families).

Supporting items queued: F313 (progress/cancel — felt pain, [next]), F320 (derived fundamentals cache — kills the ~180-parses-per-ticker tax; largely solves F314), F315/F322/F323 small. All in TODO.md with anchors.

## 9. Ideas banked (IDEAS.md, not F-items yet)

- **Harness-as-platform premise families:** momentum config (market is right), **deterioration short screen** (the GPRO miss-list signature inverted — the only idea with evidence already in hand; needs max-adverse-excursion + hard-to-borrow modeling, B9), drift configs (PEAD/insider/buyback — market is slow), null-as-strategy (the 45.6% bounce pond), factor lab (each pillar alone). **Guardrail: multiple-comparisons discipline in the harness** — N configs tested raises each one's bar.
- Harmonic-pattern config through the same harness (from the PTON Discord chart) — parameter-dense, which is exactly what the null test exposes.

## 10. Where everything lives

- Verdict payload: `backend/data/turnaround/validation_result.json` (+ `GET /api/turnaround/validate/result`)
- EDGAR cache (3.9GB, warm): `backend/data/turnaround/edgar_cache/` — gitignored
- Build artifacts: `.run/F311-turnaround/` (brief+addendum, decisions.md, 5 review JSONs, fixer changelogs)
- Explainer for non-experts: `docs/explainers/turnaround-screen.md`
- JOURNAL 2026-06-05 has the condensed version; this doc is the full one.

*One-line state: Phase 1+2 shipped and verified; first full experiment returned a clean, well-understood kill of the implemented proxy; the corrected thesis is one warm-cache rerun away from its real trial; the harness outgrew the screen.*
