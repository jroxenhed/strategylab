# R-1 Insider Cluster-Buying Charter — DRAFT — AWAITING JOHN'S APPROVAL

> **STATUS: DRAFT — AWAITING JOHN'S APPROVAL. NOTHING RUNS UNTIL JOHN APPROVES.**
> This document is **outcome-blind**: it was authored without reading any results, outcome tables, verdicts, gates, ledgers, or the program journal. It is to be hash-fingerprinted (sha256 at bottom of this author's return) and, once approved, **FROZEN ON WRITE** — sha-pinned at implementation, same convention as REGIME-TEST / MOMENTUM-TEST / DETERIORATION-TEST / EPISTEMICS-ABLATION.
> **Date:** 2026-06-06. **Experiment ID:** R-1. **Family:** insider-cluster-buying (Form 4). **Harness:** `backend/research/event_study.py` (F342 event-clock harness). **Instrument config:** `backend/research/insider_stratified.py` (Form 4 cluster definitions; config-only, never yet run to a result).

---

## 0. PLAIN-ENGLISH SUMMARY (non-expert reader; every term defined inline)

**What we are testing.** When several different *corporate insiders* (a company's own officers and directors) buy their own company's stock at roughly the same time — a "cluster buy" — does the stock tend to go *up by more than its peers* over the following weeks? Insiders must publicly disclose every personal trade in a filing called a *Form 4*, filed within 2 business days of the trade. So a burst of Form 4 *buy* filings on one company is a public, datable event. We line up all such cluster-buy events and measure how the stock did afterward versus a matched basket of other stocks.

**Why this one.** The program's standing belief (the "Research Axiom") is *"price leads, filings trail"* — most filings are a slow record of things informed money already acted on, so using them as a *trigger* means trading behind the crowd. **Form 4 is the named exception**: insiders act *before* the price moves, and the filing lands within 2 days, so a Form 4 is the one public filing that is *upstream* of price. This experiment tests whether that exception is real and measurable.

**The events.** Cluster-buy detections from a pre-built, cached sample of Form 4 filings (the "stratified sample" — a stored, seed-fixed selection of companies and dates, already fetched from the SEC; *no new data is fetched by this experiment*). A *cluster* = **2 or more distinct insiders**, each filing an *open-market purchase* (SEC transaction code **P**, acquired), on the same company **within a 5-business-day window**, with the cluster's **total disclosed dollar value ≥ $50,000**.

**The clock.** *Event-time*: we enter the trade at the **next trading day's opening price** after the filing became public (the harness default), never on the filing day itself. This avoids pretending we could have traded on information before it was public.

**The comparison.** For each event we compute the stock's forward return and subtract the *same-day median return of all other floor-passing stocks* (the "universe median" — a fair benchmark of "what an average stock did over the same window"). The leftover is the *excess return*. We ask: is the average excess **positive**?

**The bar (what counts as a win).** Primary horizon = **63 trading days** (~3 months). The average excess must be **positive**, its statistical confidence interval must **exclude zero on the upside**, the result must be **directionally consistent across the explore sub-eras**, and it must survive on a **single sealed confirm window (2021–2024)** that no one looked at while choosing the rule. We also pre-commit a **power check**: before the confirm run, the harness reports the *minimum effect it could reliably detect* (the "MDE"). If that minimum is larger than the smallest edge worth trading on a ~$10k account after costs, **we abort rather than run an underpowered confirm** — i.e. we refuse to "confirm" something the test was never strong enough to see.

**What the verdict means.** CONFIRMED = the cluster-buy edge held up out-of-sample. WEAKENED = it showed in explore but the confirm didn't clear the bar. REVERSED = clusters *underperformed* peers (sign flipped with confidence). UNTESTABLE = too few events or too weak a test to say anything. Whatever the result, it updates the Form-4 corollary of the axiom *only* at the program's Decision Gate, never by this experiment alone.

*Jargon glossary (footer):* Form 4 = SEC filing of an insider's own trade; insider = officer/director/≥10% owner; cluster = ≥2 distinct insiders buying within a window; transaction code P = open-market purchase; excess return = stock return minus same-day universe median; explore = open analysis window (2015–2020); confirm = sealed out-of-sample window (2021–2024); MDE = minimum detectable effect (smallest true edge the test can catch at 80% power); FDR = false-discovery-rate control across the family of tests; de-clustering = collapsing repeated same-stock events so they count once; block bootstrap = a resampling test that respects time-overlap in the returns.

---

## 1. HONESTY PRECONDITIONS (binding)

- Every claim below is a **hypothesis with a directional prediction and a pre-set pass/fail bar**. No number is tuned to data; all are fixed here from convention, the harness defaults, or stated economic reasoning.
- **No grids. No variants.** This charter freezes **exactly one** cluster definition and **one** primary horizon. There is no parameter sweep, no "best of N" selection. The cluster parameters (§2) are picked on principled grounds and FROZEN.
- **Blindness contract.** This charter was written without consulting any explore or confirm outcome. The implementation may be smoke-tested for mechanics (§7 anchors) but **no outcome number may be read before the explore window is opened**, and **the confirm window is sealed**: it is judged by an agent that has not seen the explore results (§8).
- **The excess is cohort/universe-matched, not beta/vol-adjusted.** The harness measures `excess = pick forward return − same-entry-date universe median forward return` (`event_study.py` Fork B). This neutralises the broad tape on each entry date but is not a factor model; the verdict states this limitation rather than hiding it.
- **De-clustering is mandatory and pre-registered (§6).** A single insider event expressed as a burst of Form 4s, or one company throwing repeated clusters in a short span, is **one** economic signal — counting them as N inflates the sample and breaks the bootstrap's independence assumption. The harness's same-ticker collapse is ON with a frozen window (§6).
- **Reproducibility.** All bootstrap draws use the harness's seeded RNG, **SEED = 20260606** (the experiment date), passed to `run_event_study(...)`. The verdict record is bit-reproducible.

---

## 2. EXACT EVENT DEFINITION (frozen — no grid)

**Source events.** Form 4 / 4/A filings from the **cached stratified Form 4 sample** only (`backend/data/turnaround/edgar_cache/form4_stratified/`, built by `insider_stratified.py`, seed=42). **No live EDGAR fetch is performed by this experiment** — it reads only what is already on disk. Each candidate filing is parsed via the existing primitives in `insider_stratified.py`: `_has_open_market_buy()` (transaction code **P** with `acquiredDisposedCode == "A"`) and `_parse_owner_cik()` (distinct reporting-owner CIK).

**Cluster detection (FROZEN parameters — each chosen on stated grounds, none tuned):**

| Parameter | Frozen value | Rationale (stated, not tuned) |
|---|---|---|
| **Min # distinct insiders** | **2** | Matches the pre-registered insider test already frozen in `insider_stratified.py` (`≥2 distinct insider open-market buys`). "Cluster" means *more than one person*; 2 is the minimal non-trivial cluster and keeps event counts viable on the cached sample. |
| **Cluster window** | **5 business days** | A cluster is "near-simultaneous" buying. 5 business days = one trading week — short enough that the insiders are plausibly reacting to the same information, long enough to absorb the ≤2-business-day Form 4 filing lag so two insiders trading the same day but filing on different days still group. |
| **Min cluster total $ value** | **$50,000** | A dollar floor removes token/de-minimis grants and rounding-lot purchases that carry no conviction signal. $50k aggregate across the cluster is a modest but non-trivial commitment; it is a *floor*, not a tuned optimum. Value taken from each P-transaction's `transactionShares × transactionPricePerShare` parsed from the Form 4 XML; if price-per-share is absent on a transaction, that transaction contributes **$0** to the cluster total (conservative — never imputed upward). |
| **Officer/director weighting** | **None (one-insider-one-vote)** | Each distinct reporting-owner CIK counts once toward the "≥2 distinct insiders" test regardless of title. Weighting officers above directors would be a tunable knob; we deliberately avoid it. Title is **recorded** per insider for descriptive reporting only. |
| **10b5-1 plan exclusion** | **Exclude when identifiable** | 10b5-1 plan trades are pre-scheduled and carry no timing signal. If a Form 4's footnote/`<footnote>` text or the transaction's `rule10b5-1` flag identifies a transaction as executed under a 10b5-1 plan, that transaction is **excluded** from the cluster (it does not count toward the insider count or the $ total). Plans are frequently *not* machine-identifiable on older Form 4s; the **fraction of cluster transactions flagged 10b5-1** is recorded per window so the reader knows how much exclusion actually bit. Non-identifiable = retained (we do not guess). |

**Event timestamp + de-duplication of the cluster itself.** A cluster's event timestamp = the **acceptanceDateTime of the LAST qualifying Form 4 in the cluster** (the moment the full cluster became public). One cluster → one `EventRecord`. The same-ticker harness de-clustering (§6) then collapses any residual same-ticker clusters that still fall within the de-dup window, so a company that clusters twice in one week still contributes one degree of freedom.

**Entry semantics (harness default, frozen).** `entry_lag_days = 1`: entry is the **Open of the first trading day after the event's ET date** (`event_study.py` `_entry_date_from_event_ts`, Fork A / Option 1). No same-day entry (`entry_lag_days = 0` is NOT used — it would expose the entry Open to pre-market reaction, the harness's ADV-09 contamination case).

**Direction:** **long** for all events (§7 cost model).

**Universe floors:** the harness's standard floors (`research.universe_floors.floor_status`, evaluated point-in-time at the **event date**, ADV-01 — never the entry bar). Below-floor / corrupt-frame events are **counted, never silently dropped** (ADV-02). The universe median benchmark is computed over all floor-passing symbols alive on the same entry date, the pick excluded (Fork B).

---

## 3. CLOCK + MDE STATEMENT (mandatory power gate — frozen)

**Clock.** Event-time, `entry_lag_days = 1` (next-trading-open after acceptanceDateTime → ET → next trading day), exactly as `event_study.py` documents as its default. acceptanceDateTime is the primary timestamp; the `filingDate + 16:01 ET` fallback is used only when acceptanceDateTime is absent, and the count of fallback timestamps is recorded (`acceptance_dt_fallbacks` in meta).

**MDE self-report is MANDATORY and runs BEFORE confirm (binding).**
- The harness already self-reports the **MDE — minimum detectable effect** = the smallest true mean excess detectable at **80% power, two-sided α = 0.05** for a one-sample test, computed as `(z_α + z_power)·σ/√n` (`outcome_table.minimum_detectable_effect`, `z_α=1.96`, `z_power=0.842`). It is reported per horizon on BOTH the cohort-relative **excess** and the **raw absolute** return (ADV-07), and printed by `print_study_report`.
- **Procedure (frozen sequencing):** run the harness on the **explore event stream first**. Read `meta["per_horizon"][63]["mde_ppt"]` (the MDE on the 63td excess, the primary horizon). **The confirm window is NOT touched until this explore MDE is read and checked against the abort rule below.**

**MDE ABORT RULE (frozen, decided now — cannot be relaxed post-hoc).**
- **Smallest economically meaningful edge for a ~$10k account after costs.** Reasoning: per repo cost conventions the modeled cost is **2 bps/leg slippage + $0 commission** (§7), i.e. ~**4 bps round-trip ≈ 0.04 percentage points (pp)** of drag per trade. A signal whose *excess* edge is below a small multiple of that drag is not worth trading at this account size — the edge would be swamped by execution noise and the realised after-cost excess would be indistinguishable from zero. We set the **smallest economically meaningful 63td excess edge = 1.0 percentage point** (≈ 25× the round-trip cost drag; a sub-1pp three-month excess over peers is below what a ~$10k retail account can reliably harvest after costs and position-sizing friction). This threshold is **fixed here on cost reasoning, not on any observed effect size.**
- **The rule:** if the explore-window **63td excess MDE > 1.0 pp**, the design is **underpowered to detect even the smallest edge worth trading**, and the experiment **ABORTS before the confirm run**. The verdict is recorded as **UNTESTABLE — underpowered (MDE = ⟨value⟩ pp > 1.0 pp floor)**; no confirm is run, the Form-4 corollary's evidence status is left unchanged, and the program's Decision Gate is told the test could not be powered on the cached sample. **An underpowered confirm is never run to manufacture a verdict.**
- If `MDE ≤ 1.0 pp` at 63td on explore, the confirm proceeds under §4/§8.
- The MDE (excess and raw) at all three horizons is **reported in the verdict regardless**, alongside the realised explore event count `n_explore_valid`.

---

## 4. HYPOTHESES (H1/H2) + TEST (frozen before any outcome data)

**Horizons.** Primary = **63 trading days (~3 months)**. Secondaries (descriptive, pre-registered, NOT separately alpha-scanned) = **21 td (~1 month)** and **126 td (~6 months)** — the harness's `V2_HORIZONS_TRADING_DAYS = (21, 63, 126)`.

**Outcome metric.** Per-event cohort-relative **excess** = `pick Open-anchored forward return − same-entry-date universe-median forward return` at the matched horizon (`event_study.py` `_forward_return_terminal` + `_compute_universe_median`, symmetric terminal-exit delisting handling, ADV-03). NET of the §7 cost model.

**Primary test.** Harness **moving-block bootstrap** (Künsch MBB, `_block_bootstrap_pvalue`, two-sided H0: mean excess = 0), `n_boot = 999`, SEED 20260606, block size **density-derived per horizon** (`_block_size_for_horizon`: `round(horizon / median trading-day gap between events)`), capped at `n//2` inside the bootstrap. **Cross-check** = Newey–West HAC t-test (`_nw_ttest_pvalue` with `_compute_nw_lag(forward_days=h)`). The charter requires the bootstrap and NW p-values to **agree within 0.10** at the primary horizon; a larger gap is flagged in the verdict as an event-density / block-size caveat (the harness already logs this).

**H1 — PRIMARY: insider cluster buys earn positive forward excess at 63td.**
- **Prediction:** mean 63td cohort-relative excess of cluster-buy events is **> 0**.
- **CONFIRMED bar (ALL required, on the sealed confirm window 2021–2024):**
  1. **Power gate passed** (§3: explore 63td excess MDE ≤ 1.0 pp — otherwise UNTESTABLE, no confirm).
  2. **Occupancy:** confirm window has **≥ 3 of the 3 confirm sub-eras populated** with ≥ 1 valid event each, AND `n_confirm_valid ≥ 15` total valid 63td events (below 15 → UNTESTABLE for confirm; 15 is the harness's own `underpowered` threshold used in `insider_stratified.run_frozen_test`).
  3. **Point estimate:** confirm 63td **mean excess > 0** (correct direction).
  4. **CI excludes zero:** the confirm 63td seeded block-bootstrap test **rejects H0 at the per-comparison α** (§5) with the mean on the **positive** side (equivalently, the bootstrap CI lower bound > 0).
  5. **Cohort/era agreement:** **per-confirm-sub-era sign agreement ≥ 0.60** — at least 2 of the 3 confirm sub-eras (`2021`, `2022`, `2023-24`, the harness's `_DEFAULT_CONFIRM_ERAS`) show a **positive** mean 63td excess (`era_consistency` / `confirm_era_breakdown` in meta).
- **Explore precondition for *reaching* confirm (era-consistency gate, §5):** confirm is touched ONLY if the **explore** window already shows **per-explore-sub-era sign agreement: ≥ 2 of the 3 explore sub-eras** (`2015-16`, `2017-18`, `2019-20`) positive at 63td, AND the explore 63td mean excess > 0. If explore sub-eras disagree (sign flips across sub-eras) or explore mean ≤ 0, the experiment stops at explore with verdict **WEAKENED-IN-EXPLORE / not advanced to confirm** — the confirm window stays sealed and unspent.

**H2 — STRONG-FORM CONSISTENCY (the edge is not a one-horizon artefact).**
- **Prediction:** the positive excess is **directionally consistent across horizons** — the 21td and 126td confirm mean excesses are **also ≥ 0** (same sign as the 63td primary), so the result is a coherent forward-drift, not a single-horizon spike.
- **H2 bar:** on the confirm window, **both** the 21td and 126td mean excesses are **≥ 0**. H2 **HOLDS** iff both secondary horizons are non-negative; H2 **FAILS** iff either secondary is negative while 63td is positive (the edge is horizon-fragile — H1 may still stand in relative terms, but the strong "clusters predict sustained outperformance" wording is not supported). H2 is alpha-bearing for the strong-form wording only (§5); it does NOT gate H1.

**UNTESTABLE rule (encoded up front):** explore 63td MDE > 1.0 pp → UNTESTABLE-underpowered (no confirm). Confirm occupancy < 3 sub-eras populated OR `n_confirm_valid < 15` → UNTESTABLE-confirm. Survivorship warning (`events_no_price_data` fraction > 0.10, ADV-02) → verdict flagged **SUSPECT** and the result is reported but not promoted to CONFIRMED until the survivorship caveat is resolved.

---

## 5. FDR LEDGER ENTRY + ALPHA BUDGET (frozen)

**FDR ledger.** This experiment's hypotheses register in the cross-run append-only ledger at **`backend/data/turnaround/fdr_ledger.json`** (`event_study._append_fdr_ledger`, ADV-05). The harness applies the **Benjamini–Hochberg step-up** procedure (`FDRLedger`, `event_study.py`) across the per-horizon excess hypotheses **within this study run** at **q = 0.10** (the harness default `fdr_q`, frozen here). The ledger records, per run: study name, config hash, `q`, `n_boot`, horizons, per-horizon block sizes + bootstrap p-values + valid n, and the BH rejection set — so optional-stopping or parameter-shopping across reruns is auditable.

- **Family (frozen):** the family for BH control is **{excess_21d, excess_63d, excess_126d}** — the three horizon hypotheses of THIS single insider-cluster study, controlled together at **q = 0.10**. The **primary verdict is read on `excess_63d`**; the 21d/126d entries are co-registered (they feed H2 and the BH family) but are not separately scanned for a "best horizon".
- **Per-comparison α for the CI-excludes-zero bar (H1 step 4):** the H1 primary is judged at the BH-controlled level — `excess_63d` must be **BH-rejected at q = 0.10** within the family AND its bootstrap mean on the positive side. No additional Bonferroni split is introduced beyond the single primary horizon; the secondaries are not alpha-scanned (they are consistency/H2 reads).
- **Cross-experiment note:** this R-1 charter is its own family in the ledger (distinct `study_name` + `study_config_hash`). It does not draw from another experiment's budget; the program Decision Gate reconciles the ledger totals. This charter invents no post-hoc correction.

---

## 6. DE-CLUSTERING / PSEUDO-REPLICATION RULE (frozen)

**Setting (frozen):** harness same-ticker de-clustering **ON** — `dedup_same_ticker = True`, **`dedup_window_days = 7`** (`event_study.EventStudyConfig` defaults, adopted verbatim and frozen here).

**What it does (`_dedup_events`):** same-ticker `EventRecord`s whose **event_ts ET dates** fall within 7 calendar days collapse to the **first (chronologically earliest)** event of each cluster — so one economic signal contributes **one** degree of freedom, not N. This sits *downstream* of the §2 cluster construction (a single cluster is already one `EventRecord`); the 7-day harness collapse catches the residual case of the **same company throwing a second cluster within a week**, which is plausibly the same wave of insider conviction.

**Reporting (binding):** the **raw pre-dedup event count** and the **number de-clustered** are recorded in `meta["survivorship"]` (`events_total`, `events_after_dedup`, `events_declustered`) and reported in the verdict. The bootstrap and all hypotheses are computed on the **de-duplicated** stream only.

**Non-overlapping cross-check (descriptive, not gating):** `use_non_overlapping = False` for the primary (the block bootstrap already corrects return overlap), but the harness's greedy non-overlapping filter count at the primary horizon is reported descriptively (`meta["non_overlapping"]`) so the reader can see how much temporal overlap the 63td events carry.

---

## 7. COST MODEL + SURVIVORSHIP (frozen)

**Cost model (repo conventions, frozen):** **long-only**; modeled **slippage = 2.0 bps per leg** (the repo default, applied via the harness `cost_fn`/`StrategyRequest` convention); **per-share commission = $0.0**, **min-per-order = $0.0** (commission-free, matches Alpaca US equities); **no borrow** leg (long). Excess figures are reported **NET** of this cost. Because every event shares the identical cost treatment and the universe-median benchmark is itself a long basket under the same tape, the cost drag is small and roughly common across pick and benchmark; the verdict reports NET and notes that the 63td excess is robust to the exact slippage level (a ~0.04pp round-trip drag cannot move a 1pp+ MDE-clearing edge across the zero line).

**Survivorship (R5 / ADV-02 / ADV-03, noted):** events whose ticker is missing from the price cache are **counted** (`events_no_price_data`), never silently dropped; a no-price fraction > 0.10 raises the harness survivorship warning and the verdict is flagged **SUSPECT**. At long horizons, a pick or peer whose price series ends inside the horizon contributes its **terminal (last) close** as the exit, symmetrically on both sides of the excess (ADV-03), so long-horizon medians are not inflated by quietly dropping delisted names. The per-horizon terminal-exit (attrition) counts are reported.

**F338 FACE-VALIDITY ANCHORS (real-data smoke probe BEFORE any interpretation — mandatory):**
1. **Cluster sanity:** at a known cohort, spot-check 2–3 detected clusters — each has ≥ 2 distinct reporting-owner CIKs, each contributing a code-P (acquired) open-market buy, cluster $ total ≥ $50k, all qualifying Form 4s within the 5-business-day window.
2. **Entry sanity:** entry_date is the first trading day strictly after the event ET date; entry_price is the Open (not Close) for the spot-checked events (Close-fallback events are counted and flagged).
3. **Point-in-time / floor:** floor status is decided at the **event date**, not the entry bar (ADV-01); a known sub-$5 or split-corrupt name at the probe cohort is excluded and counted.
4. **Excess sign:** a known same-cohort big winner shows positive 63td excess, a laggard negative — the long-sign excess convention is wired correctly.
5. **De-dup:** a synthetic/known same-ticker double-cluster within 7 days collapses to one event (`events_declustered` ≥ 1 where expected).
6. **MDE printed:** `print_study_report` emits a finite 63td excess MDE on the explore stream and it is sane (not NaN, not absurd), and `n_explore_valid` is the count actually feeding the bootstrap.
7. **Survivorship line:** the `events_total / no_price_data / below_floor / entered` survivorship line is populated and the no-price fraction is below the 0.10 warning threshold (or the SUSPECT flag fires).

Reading the artifact (meta.json + events.ndjson) and checking these anchors is **part of the gate** — green synthetic suites are not sufficient (F338).

---

## 8. BLIND-GRADING PROTOCOL (frozen)

**Two-window, explore-blind-confirm protocol** (same as the four prior experiments):
- **Explore (2015–2020, OPEN):** the harness `explore_cutoff = 2020-12-31` (and the hard ceiling — explore may NEVER reach 2025+ cache data, `event_study` raises unless `allow_post_2020_explore`, which we do **NOT** set). The explore window is hypothesis-confirming for the **era-consistency precondition** (§4) and supplies the **mandatory MDE power read** (§3). It is the only window an agent may inspect while deciding whether to advance.
- **Confirm (2021–2024, SEALED):** entry_date > 2020-12-31 and ≤ 2024-12-31 (2025+ excluded by the hard ceiling). The confirm verdict is computed and graded by an **agent that has NOT seen the explore results** — it receives this frozen charter, the §4 bars, the §6 mapping, and the confirm-window artifacts only. The confirm number is read **once** (single sealed touch). The §4 H1 bars are applied verbatim; no bar is chosen after the number is seen.
- **Per-sub-era breakdown in the confirm verdict (binding):** the confirm verdict MUST include the per-confirm-sub-era (`2021` / `2022` / `2023-24`) mean 63td excess + sign-agreement breakdown (`confirm_era_breakdown` in meta), so the era-consistency bar (H1 step 5) is auditable from the verdict alone.

---

## 9. ABORT CRITERIA + VERDICT DEFINITIONS (frozen)

**Abort / UNTESTABLE (no confirm spent):**
- **Underpowered:** explore 63td excess **MDE > 1.0 pp** → ABORT before confirm; verdict **UNTESTABLE — underpowered** (§3). Confirm stays sealed.
- **Explore era-inconsistent:** explore sub-eras disagree in sign at 63td OR explore mean ≤ 0 → stop at explore, verdict **WEAKENED-IN-EXPLORE**, confirm not advanced (§4). Confirm stays sealed.
- **Confirm occupancy:** confirm < 3 sub-eras populated OR `n_confirm_valid < 15` → **UNTESTABLE — confirm underpowered**.
- **Survivorship:** `events_no_price_data` fraction > 0.10 → verdict flagged **SUSPECT**; not promotable to CONFIRMED until resolved.

**Verdict definitions (read on the confirm window under §4/§8):**

| Verdict | Requires |
|---|---|
| **CONFIRMED** | All five H1 CONFIRMED-bar conditions met (§4): power gate passed, occupancy met, confirm 63td mean excess > 0, `excess_63d` BH-rejected at q=0.10 with mean on the positive side, **and** ≥ 2 of 3 confirm sub-eras positive. (If H2 also HOLDS — 21td & 126td confirm excesses both ≥ 0 — the verdict additionally notes "strong-form consistent across horizons".) |
| **WEAKENED** | Explore cleared the era-consistency precondition and advanced to confirm, but the confirm 63td result **fails at least one** H1 bar that is *not* a sign-inversion — e.g. CI touches zero (not BH-rejected), or confirm sub-era agreement < 0.60, or point estimate positive but n too small after dedup. The edge appeared in explore but did not survive sealed confirmation. (Includes **WEAKENED-IN-EXPLORE** when the experiment stops before confirm per §4.) |
| **REVERSED** | Confirm 63td **mean excess < 0** with the seeded block-bootstrap **BH-rejecting H0 at q=0.10 on the negative side** (CI excludes zero the *wrong* way) — insider clusters **underperformed** their peers out-of-sample. The Form-4 cluster signal is contradicted as stated. |
| **UNTESTABLE** | Any abort condition above (underpowered MDE, confirm occupancy, or — for the strong claim — survivorship SUSPECT unresolved). The Form-4 corollary's evidence status is left unchanged; the program Decision Gate is told the test could not be exercised on the cached sample. |

**Axiom consequence (sequencing).** This experiment tests the **Form-4 corollary** of the "price leads, filings trail" axiom (the named upstream exception). The outcome produces a verdict doc + an evidence-status **proposal** for the Form-4 corollary; **the actual `CLAUDE.md` edit is applied only at the program's Decision Gate**, after the gate reconciles the FDR ledger totals against the program alpha/q budget. No single experiment rewrites the axiom on its own.

---

## 10. AMENDMENT RULE (frozen on write)

This charter is **FROZEN ON WRITE** (sha-pinned at implementation). Any change to the §2 cluster definition (min insiders, window, $ floor, weighting, 10b5-1 rule), the entry semantics, horizons, the §3 MDE abort threshold, the H1/H2 bars, the §5 FDR family or q, the §6 de-dup setting, the cost model, the bootstrap seed, the verdict mapping, or the windows **after outcome contact** (after any explore or confirm number is consulted) constitutes a **NEW experiment** with its own charter and its own ledger draw — it does not amend this one. Pre-outcome implementation-mechanics fixes (a bug caught on the §7 smoke probe before any result is read) are permitted and logged, provided no outcome data has been consulted. The UNTESTABLE / WEAKENED-IN-EXPLORE / SUSPECT branches are **pre-registered branches decided here**, not amendments.

---

*Authored outcome-blind, 2026-06-06. No results, verdicts, gates, ledgers, reanalyses, or program-journal files were read in drafting this charter. Awaiting John's approval; nothing runs until approved.*
