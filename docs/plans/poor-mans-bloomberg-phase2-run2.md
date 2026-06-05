# Turnaround Screen — Phase-2 Run 2: Final Verdict

**Date:** 2026-06-05 (interactive session, follow-up to [run-1](poor-mans-bloomberg-phase2-run1.md))
**Status: VERDICT = KILL (the thesis, this time — not a proxy). Phases 3–5 permanently gated. F317/F316/F324/F325 closed won't-do.**

## What changed between runs (the rerun protocol, all shipped)

- **F326** sign-change inflection gate (spec-faithful: ≥1 negative YoY quarter before the positive run)
- **F319** universe hygiene v2 (junk suffixes out: 8,909 → 7,055 names)
- **F321** conviction pillar repaired (4 bugs; validation unaffected — conviction skipped by design)
- **F327 + events table** (full per-event outcomes persisted, schema_version=1)
- **COR-02** per-cohort overlap cooldowns (cross-cohort suppression bias removed)
- **COR-01** GP/revenue quarter alignment in gm_delta
- **F313** progress/cancel/timeout + Discovery run panel (51-min run, fully observable)

## The numbers (run-1 → run-2)

| | Run-1 (broken gate) | Run-2 (faithful gate) |
|---|---|---|
| Signal events / hits | 125 / 44 | **12 / 4** |
| Signal hit rate [Wilson 95%] | 35.2% [27.4–43.9] | **33.3% [13.8–60.9]** |
| Null events / hits | 3,034 / 1,384 | 3,068 / 1,392 |
| Null hit rate [Wilson 95%] | 45.6% [43.9–47.4] | **45.4% [43.6–47.1]** |
| Signal mean / median return | +8.8% / +12.9% | **+4.6% / −3.1%** |
| Null mean / median return | — | **+13.2% / +25.2%** |
| Fetch failures | 1,039 | 393 |

The 12 signal events: RRC-2017, WT-2018, INCY-2018, ARLP-2020, INGN-2021, IMMR-2022✓, INVX-2022✓, MCY-2023✓, MYPS-2023, XYZ-2023, BURL-2023✓, CRSR-2023. ENPH-2023 and GPRO-2015 correctly excluded by the sign-change gate (the run-1 autopsy class). CRSR genuinely turned (2022 gaming bust → 2023 positive run) and still lost 49% — thesis failure, not gate failure.

## Why KILL is the verdict

1. **No edge over the null, twice.** Two structurally different configs (broken proxy, faithful spec) both sit below merely-washed-out. Signal median −3.1% vs null +25.2%.
2. **Unvalidatable by construction.** The faithful gate fires ~1.3×/year across 7k names. Separating ~50% from 45% at that event rate needs decades. A screen that cannot ever clear its own falsification gate is dead regardless of its true rate.
3. **F328 miss-horizon read (8 misses, ad-hoc query):** INCY 17.1m / ARLP 19.5m / XYZ 15.0m hit +50% just past the window ("fired early"); RRC 50m / WT 89m (no); INGN/MYPS/CRSR never. A 24m horizon would read 7/12 — but null-24m was not computed and post-hoc horizon stretching is the multiple-comparisons trap. Recorded as forensics, not evidence.
4. Survivorship inflates the null more than the signal (run-1 caveat, still true) — nothing in it rescues the signal.

## What survives the kill

- **The harness** — point-in-time EDGAR + filter + validation engine with per-event outcomes, progress/cancel, null cohorts. This is the asset. Premise families queue in IDEAS.md: deterioration short (run-1 miss-list signature, only family with evidence in hand), null-as-strategy (45% +50%-touch base rate, +13% mean), momentum, drift configs, factor lab. Multiple-comparisons guardrail applies to all.
- **Design notes preserved:** sticky watchlist membership (F317), gate margins/forming tier (F324), dose-response cohorts (F325) — patterns for whatever screen comes next.
- **Harness TODO items remain open:** F320 (derived fundamentals cache), F331 (parallel price prefetch), F332 (persist price frames — reruns become minutes), F314/F315/F322/F329/F330.
- ENPH coda stands: the screen's run-1 pick was right-name-wrong-clock; John traded the real 2026 turn manually. The lesson (price leads, filings trail; in washed-out land trailing fundamentals select for unfinished bad news) is the project's most durable finding.

## Where everything lives

- Run-2 payload: `backend/data/turnaround/validation_result.json` (events table included). Run-1 preserved at `validation_result_run1.json` (+ `.bak` depth 3).
- Watchlist regen (F321 runtime step) intentionally skipped — the screen is dead; the repaired conviction client + recorded-fixture tests remain as harness infra.
- Build/review artifacts: `.run/F-RERUN-0605/` (3 impl lanes + F313 + UI panel, 6 review JSONs, decisions.md, fix changelog).

*One-line state: the harness ran a clean experiment end-to-end in 51 observable minutes and returned an unambiguous kill; the screen is dead, the laboratory works.*
