# Premise Power Census — Spec (2026-06-08)

**Status:** DRAFT for John's review. Not a charter (draws no FDR alpha). A feasibility-measurement pass that decides which premise families earn a real explore run.

## Why this exists

R-1b's lesson: the binding constraint is **statistical power**, not idea quality. A real-looking +3.28pp insider-cluster signal graded **UNTESTABLE-underpowered** because the engine's headline MDE (3.40pp) sat above the 1.0pp tradeable floor. The harness's MDE is the naive one-sample t-test form (matches `outcome_table.minimum_detectable_effect`):

```
MDE = 2.802 × std(excess) ÷ √(n_valid)        # z_alpha 1.96 + z_power 0.842
```

**Two MDEs, not one — and the gap between them is the point.** Computed from the R-1b artifact directly:

| test | n | MDE_63 | what it answers |
|---|---|---|---|
| one-sample mean excess | 4,245 | **1.006pp** | "do these beat the market on average" |
| dose-response Q5−Q1 gap | 591+596 | **3.40pp** | "does the effect scale with dose" (program headline per methodology 6b) |

The dose-gap MDE is ~3× the one-sample MDE because a difference of two means carries a √2 variance penalty *and* each quintile holds only n/5 events. **A premise can be testable as a binary signal yet underpowered as dose-response** — that is exactly R-1b's situation, and it is the most decision-relevant thing the census reports.

Two power levers feed both: **n** (volume) and **std(excess)** (dispersion / "calmness"). The program has steered on n alone. R-1b's real floor-filtered std is **23.39pp** at 63td (NOT the 66pp an earlier draft of this spec back-derived — that conflated the gap MDE with the one-sample MDE; corrected here). **Power = volume × calmness.**

**Caveat the census must print:** the naive MDE assumes iid events; R-1b's events are cross-sectionally correlated (shared entry dates → common market moves), so the naive one-sample MDE *understates* the true penalty. The harness's real significance gate is the block-bootstrap / NW p-value, not the MDE. The census MDE is therefore a **power-screening heuristic** (good for killing hopeless families and ranking), not a significance verdict. The dose-gap largely cancels the common factor, so its MDE is the more trustworthy of the two for ranking dose-response premises.

Before spending another explore run or charter, measure both levers + both MDEs for several families and rank by *predicted MDE vs the 1.0pp floor*. Families that provably can't reach a useful MDE die on paper for the price of a row count.

## What it is NOT

- **Not a hypothesis test.** It computes n and std only. No directional effect is estimated, so **the FDR ledger is NOT drawn** (`ledger_path=None`, per methodology corollary).
- **Not an instrument whose *signal* is believed.** It measures counts and dispersion, which don't require a family's full event extractor or its surprise/score logic. We learn testability *before* paying to build the extractor.

## Output (the one deliverable)

A single ranked table written to `docs/research/2026-06-08-premise-power-census.md` (+ machine-readable `.run/<id>/census.json`). One row per family (and per sub-slice where relevant):

| family / slice | n_raw | n_valid (est) | std_63 (pp) | MDE_1samp_63 (pp) | MDE_gap_63 (pp) | testable to 1.0pp (1samp / gap)? | n needed for 1.0pp | extractor still owed |
|---|---|---|---|---|---|---|---|---|

- **MDE_1samp** = `2.802 × std_63 ÷ √n_valid` (binary "beats market" test — every family has it).
- **MDE_gap** = quintile Q5−Q1 dose-response MDE, reported ONLY for families that carry a dose score at census time (R-1b: the frozen insider score; others: blank until their score exists). Computed as `2.802 × √(var_q5/n_q5 + var_q1/n_q1)`.
- **n needed for 1.0pp** = `(2.802 × std_63 / 1.0)²` for the one-sample test (lower bound on the requirement).

Plus a plain-English paragraph per family: what events, how many, how volatile, both detectable floors, and the verdict (*pursue-binary / pursue-dose / pursue-on-sub-universe / shelve-underpowered*).

## Method (shared mechanics)

- **Universe medians** from `backend/data/universe_matrix.parquet` (the canonical 2026-06-07 build): per `entry_date`, median of `fwd_return_pct` **over floor-passing symbols only** (replicate `event_study.py`'s `_floor_status == ok` filter before the median — confirmed in the explore brief; the median is NOT over all symbols). Excess = `fwd_return_pct − floor_median(date)`.
- **Per family:** assemble an event set `(entry_date, symbol)`, left-join to the matrix, drop no-frame rows, compute `n_valid` and `std(excess)` at 21/63/126 td (headline 63td).
- **Funnel haircut.** `n_raw` is the count straight from the corpus. `n_valid (est)` applies a *labeled* survival assumption; the real funnel is family-specific and only the explore run measures it exactly. Default haircut = R-1b's measured 2,964 quintile-valid / 38,425 raw = **7.7%** (or 4,245 floor-ok / 38,425 = 11%), flagged as approximate; where a family's dedup/floor logic is cheap to apply exactly, apply it and say so.
- **Both MDEs** per the Output section: one-sample always; dose-gap only where a score exists.

## Built-in F338 calibration anchor (gates the whole census)

Before ANY family number is believed, the census must **reproduce the known R-1b result** when fed the R-1b event set on the full universe. Anchors verified directly against the artifact (`…/r1b_…/events.ndjson` + `r1_explore_verdict.json`), 63td, events with `split=="explore"` AND `floor_status=="ok"` AND non-null `fwd_excess_pct["63"]`:
- **Anchor A1:** n_valid == **4,245** (exact).
- **Anchor A2:** std_excess_63 within ±2% of **23.39pp**; mean_excess_63 within ±0.05pp of **+2.31pp**.
- **Anchor A3:** one-sample MDE_63 within ±0.02pp of **1.006pp**; dose-gap MDE_63 within ±0.1pp of **3.40pp** (using the per-quintile n_q5=591 / n_q1=596 split).

If A1–A3 fail, the census mechanics are broken — STOP, do not report family numbers. (This is the census's own real-data smoke probe; it costs nothing because the R-1b artifact already exists.)

## The four families

### 1. R-1b on calmer sub-universes (cheapest — reuses validated data)
- **Input:** `…/r1b_insider_clusters_explore_2015_2020/events.ndjson` (4,245 valid events; carries `payload.MC` and `fwd_excess_pct`). **Caveat measured:** only **2,994 / 4,245** valid events have non-null `payload.MC` — bucketing is on that subset, and the no-MC remainder is reported separately (never silently dropped).
- **Method:** bucket the MC-carrying valid events by point-in-time market cap **and** by realized trailing vol (vol is the more direct calmness axis here); recompute n, std_63, both MDEs per bucket. MC quartiles from the data: p25 $1.5B, p50 $3.8B, p75 $11.8B.
- **Question answered:** is the real fix *calmness* rather than a new premise? **Live caveat:** the valid events are NOT small-cap (median $3.8B) and full-set std is a moderate 23.39pp — so the calmness lever may not exist for R-1b. The census gives a clean yes/no: if no slice drops MDE_gap below 1.0pp at its own n, "calmer universe" is dead for R-1b and we say so.
- **Honesty note:** slicing reduces n, which *raises* MDE; the bet is that std falls faster than √n. The table shows whether it does. Slicing post-hoc on a known signal is explicitly a *feasibility* read, not a confirm look — no alpha drawn, and any resulting R-1c re-tests on a fresh pre-registered design.

### 2. PEAD / fundamental surprise (non-insider, best prior)
- **Input:** `submissions/` corpus for 10-Q/10-K acceptance dates 2015–2020 mapped to universe tickers → `n_raw` event volume + post-filing return dispersion. The surprise *definition* (estimate-free YoY-accel / actual-vs-trailing) is NOT built here — census needs only filing counts + dispersion.
- **Question answered:** does the largest classic-anomaly stream have the events-÷-dispersion to reach 1.0pp? If yes, it justifies building the F348 surprise payload + F338 probe.

### 3. 8-K item-type drift (likely largest stream)
- **Input:** `submissions/` corpus 8-K filings 2015–2020, split by item code (2.02 results / 5.02 officer change / 1.01 agreement / 8.01 other). Per-item `n_raw` + dispersion.
- **Question answered:** which 8-K item type, if any, has both the volume and the calmness to be testable. Per-item rows because item types differ wildly in both levers.

### 4. R-2 distress recovery (predict before running)
- **Input:** R-2 charter event definition + `delisting/` universe; count qualifying events and their dispersion.
- **Question answered:** will the already-approved R-2 run hit the same underpowered wall R-1b did? Cheap insurance — if predicted MDE ≫ 1.0pp, we know before spending the run.

## Deliberately out of scope

- Crowding-fade (FINRA short interest) and analyst-rating drift — not cached as corpora; would need fetching. Add to a later census round if the on-disk four don't yield a survivor.
- Any directional effect estimate, any score formula, any charter text. Survivors graduate to their own brainstorm → charter → explore cycle.

## Done = 

The ranked table exists, A1–A3 pass, and each family has a one-line verdict. John reads it and picks which survivor(s) get a real explore run.
