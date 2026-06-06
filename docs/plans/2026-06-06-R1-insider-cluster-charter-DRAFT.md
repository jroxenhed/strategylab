# R-1 Insider Cluster-Buying Charter — DRAFT v2 — AWAITING JOHN'S APPROVAL

> **STATUS: DRAFT v2 — AWAITING JOHN'S APPROVAL. NOTHING RUNS UNTIL JOHN APPROVES.**
> This document is **outcome-blind**: it was authored (v1) and revised (v2) without reading any results, outcome tables, verdicts, gates, ledgers, or the program journal. It is to be hash-fingerprinted (sha256 at bottom of this author's return) and, once approved, **FROZEN ON WRITE** — sha-pinned at implementation, same convention as REGIME-TEST / MOMENTUM-TEST / DETERIORATION-TEST / EPISTEMICS-ABLATION.
> **Date:** 2026-06-06. **Experiment ID:** R-1. **Family:** insider-cluster-buying (Form 4). **Harness:** `backend/research/event_study.py` (F342 event-clock harness). **Instrument config:** `backend/research/insider_stratified.py` (Form 4 cluster primitives; config-only, never yet run to a result).
>
> **v2 revision note (pre-outcome, legal under §10).** John reviewed the v1 DRAFT and flagged that its rules were *too binary* — "what if they bought within 6 days instead of 5? what if the sum is $49k? is the dollar figure proportional to market cap?" v2 replaces the binary cluster **gate** with **one frozen continuous score** (a dose), recasts the headline hypothesis as **dose-response monotonicity** (more insider buying → more forward excess, like dose of a drug), and institutionalizes John's "$49k" question as a **pre-registered perturbation band** (the verdict must be sign-stable to small wiggles in the frozen constants). No outcome data was read in making this revision; pre-outcome amendment is legal under §10. The continuous score's constants are **hashed exactly like a threshold** — fuzziness in the *concept* of a cluster does NOT license any post-hoc tuning of the *numbers*; every constant is frozen here with a one-line rationale.

---

## 0. PLAIN-ENGLISH SUMMARY (non-expert reader; every term defined inline)

**What we are testing.** When a company's own *insiders* (its officers and directors) buy their own company's stock, they have to publicly disclose each purchase in a filing called a *Form 4*, filed within 2 business days. We measure how *much* insider buying is happening at a company at a given moment — a single **dose number** that goes *up* when more dollars are bought, when more *different* insiders are buying, and *down* when the company is huge (so a $100k buy at a small company counts for more than at a giant). Then we ask the **dose-response** question: do the companies with the *biggest* insider-buying dose go *up by more than their peers* over the following weeks than the companies with the *smallest* dose? "Dose-response" is the drug-trial idea — if a little of a thing helps a little and a lot helps a lot, in a smooth ladder, that ladder is itself strong evidence the thing is real (much harder to fake than a single yes/no cutoff).

**Why a dose instead of a yes/no rule (John's point).** v1 used a hard switch: "≥ 2 insiders, within 5 business days, ≥ $50,000 — else not a cluster." John rightly asked: *what if they bought within 6 days? what if the total was $49,000? shouldn't a big dollar figure count for less at a giant company?* A hard switch makes the whole finding hostage to arbitrary edges. A **continuous dose** sidesteps all three: 6 days vs 5 just shifts a number slightly, $49k vs $50k is a hair of difference not a cliff, and dividing by company size makes the dollars *proportional to market cap* automatically. We still freeze the dose's formula exactly (so we can't tune it after seeing results), but we no longer pretend the world has a clean on/off line in it.

**The dose (one frozen formula).** For each company on each day, the dose is:

> **score = log( 1 + [ total open-market insider purchase dollars over the trailing 21 business days ] ÷ [ the company's market value that day ] ) × ( 1 + 0.5 × [ number of *different* insiders who bought in that window ] )**

In words: the **first part** is how big the buying was *relative to the company's size* (a $1M buy at a $50M company is a much bigger dose than at a $50B company — this is John's "proportional to market cap"). The `log(1+…)` just keeps one freakishly large buy from dominating everything. The **second part** multiplies the dose up when *more separate people* are buying (two insiders buying is more convincing than one buying twice). Every constant in this formula (the 21-day window, the 0.5 weight on insider count, the log shape) is **frozen here** with a one-line reason, and the whole formula is *hashed like a password* — once approved we cannot nudge any number to chase a result.

**The clock.** *Event-time*: an event is "a fresh insider open-market purchase prints for this company," and we enter the trade at the **next trading day's opening price** after the latest Form 4 in the dose window became public — never on the filing day itself, so we never pretend to trade on information before it was public.

**The comparison + the headline question.** For each event we compute the stock's forward return and subtract the *same-day median return of all other tradeable stocks* (the "universe median" — a fair "what did an average stock do over the same stretch" benchmark). The leftover is the *excess return*. We then **sort every event into five buckets by its dose** (lowest fifth, …, highest fifth — "quintiles"), *separately within each calendar year* so we're not fooled by good-vs-bad eras. The **headline win** is a *dose-response ladder*: the **top-dose fifth must beat the bottom-dose fifth** at 63 trading days (~3 months) with statistical confidence, AND the five buckets must **trend the right way** (bigger dose → bigger excess, tested as a rank-correlation). A smooth ladder is the finding; a single lucky bucket is not.

**The wiggle test (John's "$49k", institutionalized).** Because the dose has frozen constants, we are *required* to re-run the headline comparison with the buying-window nudged to **20 or 22 business days** (±1) and the implicit dollar/size floor nudged **±20%**, and check that the answer *keeps the same sign*. We do NOT require it to stay statistically significant at every wiggle — only that the direction doesn't flip. If nudging a frozen knob by a hair flips the sign, the finding is fragile and the verdict is **capped at WEAKENED** no matter how good the headline number looked. This is a *frozen robustness check*, decided now — it is the opposite of a search for the best setting.

**The power check (so we don't "confirm" something the test was too weak to see).** Before the sealed confirm run, the harness reports the *smallest edge it could reliably detect* (the "MDE"). If that's bigger than the smallest edge worth trading on a ~$10k account after costs, we **abort rather than run an underpowered confirm**.

**What the verdicts mean.** CONFIRMED = the dose-response ladder held up out-of-sample (top fifth beat bottom fifth, the trend was monotone). WEAKENED = it showed in explore but the confirm didn't clear the bar, *or* the wiggle test flipped a sign. REVERSED = the dose ladder ran *backwards* (more insider buying → *worse* returns) with confidence. UNTESTABLE = too few events or too weak a test. Whatever the result, it updates the Form-4 corollary of the program's "price leads, filings trail" axiom *only* at the program's Decision Gate, never by this experiment alone.

*Jargon glossary (footer):* Form 4 = SEC filing of an insider's own trade; insider = officer/director/≥10% owner; **dose / score** = the one frozen continuous number measuring how much insider buying is happening relative to company size; **dose-response** = bigger dose → bigger effect in a smooth ladder (the drug-trial logic); **quintile** = one of five equal buckets sorted by the dose; market cap = shares outstanding × share price (the company's market value); excess return = stock return minus same-day universe median; explore = open analysis window (2015–2020); confirm = sealed out-of-sample window (2021–2024); MDE = minimum detectable effect (smallest true edge the test can catch at 80% power); **perturbation / wiggle band** = the pre-registered set of small nudges to the frozen constants under which the sign must stay stable; FDR = false-discovery-rate control across the family of tests; de-clustering = collapsing repeated same-stock events so they count once; block bootstrap = a resampling test that respects time-overlap in the returns; Spearman = a rank-correlation that asks only whether the buckets *order* correctly, ignoring exact spacing.

---

## 1. HONESTY PRECONDITIONS (binding)

- Every claim below is a **hypothesis with a directional prediction and a pre-set pass/fail bar**. No number is tuned to data; all are fixed here from convention, the harness defaults, or stated economic/cost reasoning.
- **One frozen score, no grids, no variants.** This charter freezes **exactly one continuous dose formula** (§2), **one primary horizon** (63td), and **one primary test** (the dose-response Q5−Q1 comparison + monotonicity trend, §4). There is no parameter sweep and no "best of N" selection over score variants. The **perturbation band (§3b)** is a *mandated robustness re-run of the single frozen primary*, not a search — its only output is a binary sign-stability flag, never a re-chosen winner.
- **Fuzziness licenses ZERO post-hoc tuning (binding statement).** Replacing a binary gate with a continuous score does **not** loosen the freeze — it *tightens* it. The score's every constant (window length W, the insider-count weight, the log shape, the market-cap denominator construction) is fixed here in §2 and **hashed into the config fingerprint exactly like a numeric threshold**. The intuition that "a cluster is fuzzy at the edges" is precisely *why* we must pin the formula and pre-commit the wiggle band: the fuzziness lives in the *concept*, the *numbers* are frozen. No constant may be changed after any outcome number is read except as a NEW experiment (§10).
- **Blindness contract.** This charter was written without consulting any explore or confirm outcome. The implementation may be smoke-tested for mechanics (§7 anchors) but **no outcome number may be read before the explore window is opened**, and **the confirm window is sealed**: it is judged by an agent that has not seen the explore results (§8).
- **The excess is cohort/universe-matched, not beta/vol-adjusted.** The harness measures `excess = pick forward return − same-entry-date universe median forward return` (`event_study.py` Fork B). This neutralises the broad tape on each entry date but is not a factor model; the verdict states this limitation rather than hiding it.
- **De-clustering is mandatory and pre-registered (§6).** A single insider event expressed as a burst of Form 4s, or one company throwing repeated buying in a short span, is **one** economic signal — counting them as N inflates the sample and breaks the bootstrap's independence assumption. The harness's same-ticker collapse is ON with a frozen window (§6).
- **Reproducibility.** All bootstrap draws use the harness's seeded RNG, **SEED = 20260606** (the experiment date), passed to `run_event_study(...)`. The verdict record is bit-reproducible.

---

## 2. EXACT EVENT DEFINITION + FROZEN CONTINUOUS SCORE (no grid)

**Source events.** Form 4 / 4/A filings from the **cached stratified Form 4 sample** only (`backend/data/turnaround/edgar_cache/form4_stratified/`, built by `insider_stratified.py`, seed=42). **No live EDGAR fetch is performed by this experiment** — it reads only what is already on disk. Each candidate filing is parsed via the existing primitives in `insider_stratified.py`: `_has_open_market_buy()` (transaction code **P** with `acquiredDisposedCode == "A"`) and `_parse_owner_cik()` (distinct reporting-owner CIK). Per-transaction dollar value = `transactionShares × transactionPricePerShare` parsed from the Form 4 XML; if price-per-share is absent on a qualifying P transaction, that transaction contributes **$0** to the dollar total (conservative — never imputed upward).

### 2a. What counts as an EVENT (when the clock starts)

An **event** = the public arrival of a fresh **open-market code-P purchase** Form 4 on a company that did not already have a same-ticker event inside the de-dup window (§6). Concretely: scanning the cached sample in time order, each company-day on which a new qualifying P-purchase Form 4 is *accepted* opens one candidate event, timestamped at that filing's `acceptanceDateTime` (the `filingDate + 16:01 ET` fallback is used only when acceptanceDateTime is absent; fallback count recorded). The §6 same-ticker de-dup (window = **21 calendar days**, §6) then collapses bursts so one wave of buying = one degree of freedom.

The event is **not gated on a minimum cluster** — *every* qualifying P-purchase arrival is an event. The "how much of a cluster is this" question is answered **entirely by the continuous score** (§2b), which every event carries on its `EventRecord.payload` (the harness forwards payload verbatim into the outcome table, so the dose travels with the event into the analysis). This is the core v2 change: there is no binary "is it a cluster" cut; there is a continuous dose, and the hypothesis is about the *gradient* of forward excess across that dose (§4).

### 2b. THE FROZEN CONTINUOUS SCORE (the dose — hashed like a threshold)

For an event on ticker T whose latest qualifying Form 4 was accepted on date `d`, define a **trailing dose window** of **W = 21 business days** ending at `d` (inclusive). Let, over all qualifying open-market **code-P** purchases on T whose Form 4 acceptance falls in that window:

- `D` = **total purchase dollars** = Σ (`transactionShares × transactionPricePerShare`) across those P transactions (missing price ⇒ that transaction = $0).
- `k` = **count of distinct reporting-owner CIKs** that contributed ≥ 1 qualifying P purchase in the window (the "how many *different* insiders" number, `_parse_owner_cik`).
- `MC(T, d)` = **point-in-time market cap** = `edgar.get_shares_outstanding(cik(T), as_of=d)` (most-recent `CommonStockSharesOutstanding` filed on or before `d`, point-in-time) **×** the **cached daily close of T on the last trading day ≤ `d`** (the same price cache the harness/universe uses). If shares-outstanding is unavailable (`None`) or the cached close is missing, the market cap is undefined and the event is **counted as `score_undefined` and excluded from the dose-response analysis** (never imputed — ADV-02 style, recorded in meta).

Then the **FROZEN SCORE** is:

> **score = log1p( D / MC(T, d) ) · ( 1 + β · k )**, with **β = 0.5**.

Equivalently `score = math.log1p(D / MC) * (1 + 0.5 * k)`. The **dollar-intensity ratio** `D / MC` is John's "proportional to market cap" made literal; `log1p` tames a single outlier purchase; the `(1 + 0.5·k)` factor is the **distinct-insider multiplier** (a frozen multiplier, per the revision spec — chosen over an additive term so that, with zero dollars, the score is zero regardless of headcount: dose requires *dollars*, and headcount only *amplifies* a real-dollar dose).

**Every constant FROZEN here with one-line rationale (each hashed into the config fingerprint):**

| Constant | Frozen value | One-line rationale (stated, not tuned) |
|---|---|---|
| **Trailing dose window W** | **21 business days** | One trading month — long enough to gather a genuine multi-insider wave and absorb the ≤2-bday Form-4 filing lag, short enough that the buys plausibly share one information event. (Replaces v1's hard "5-business-day cluster window"; the dose now degrades smoothly across the window edge rather than gating on it — directly answers "what if 6 days not 5?".) |
| **Dollar-intensity ratio** | **D / MC** (dollars ÷ point-in-time market cap) | Makes the dose *proportional to company size* (John's question): identical dollars are a larger dose at a smaller company. MC = point-in-time shares (`get_shares_outstanding`) × cached close — both already on disk, no live fetch. |
| **Shape transform** | **log1p(·)** (= ln(1+x)) | Diminishing returns: prevents one gigantic buy (or a tiny-float micro-cap) from swamping the ranking; `log1p` is finite at 0 (a zero-dollar window scores 0). |
| **Distinct-insider weight β** | **0.5** (multiplicative: `1 + 0.5·k`) | More *separate* insiders is more convincing than one insider buying repeatedly; 0.5 makes each additional distinct insider add 50% of the base dollar-dose — a modest, fixed amplification, not a tuned optimum. Multiplicative so dose=0 when dollars=0 regardless of k. |
| **Distinct-insider count k** | **distinct reporting-owner CIKs with a P-buy in W** | One-insider-one-vote (title-agnostic), same primitive as v1's insider count (`_parse_owner_cik`); officer/director title is *recorded* descriptively but carries no weight (avoids a tunable knob). |
| **10b5-1 exclusion** | **Exclude when machine-identifiable** | Pre-scheduled 10b5-1 trades carry no timing signal; a transaction flagged `rule10b5-1` (or footnote-identified) is dropped from *both* `D` and `k`. Non-identifiable = retained (we do not guess); the fraction excluded is recorded per window. |

**Why no binary gate survives.** v1's three binary cuts (≥2 insiders, ≤5 days, ≥$50k) are *all* dissolved into the score: insider count → the `k` multiplier, the 5-day window → the smooth 21-bday trailing window, the $50k floor → the continuous `D/MC` ratio (no floor at all in the primary; a soft floor is only *perturbed* in §3b as a robustness check, never as a gate). **The hypothesis is no longer "do clusters beat peers" but "does forward excess rise monotonically with the dose" (§4).** The score is the single frozen instrument; its formula and every constant are part of the config hash, so post-hoc tuning is structurally impossible without minting a new experiment (§10).

**Entry semantics (harness default, frozen).** `entry_lag_days = 1`: entry is the **Open of the first trading day after the event's ET date** (`event_study.py` `_entry_date_from_event_ts`, Fork A / Option 1). No same-day entry (the harness's ADV-09 contamination case).

**Direction:** **long** for all events (§7 cost model).

**Universe floors:** the harness's standard floors (`research.universe_floors.floor_status`, evaluated point-in-time at the **event date**, ADV-01 — never the entry bar). Below-floor / corrupt-frame events are **counted, never silently dropped** (ADV-02). The universe median benchmark is computed over all floor-passing symbols alive on the same entry date, the pick excluded (Fork B).

---

## 3. CLOCK + MDE STATEMENT + PERTURBATION BAND (frozen)

### 3a. Clock + MDE (mandatory power gate)

**Clock.** Event-time, `entry_lag_days = 1` (next-trading-open after acceptanceDateTime → ET → next trading day), exactly as `event_study.py` documents as its default. acceptanceDateTime is the primary timestamp; the `filingDate + 16:01 ET` fallback is used only when acceptanceDateTime is absent, and the count of fallback timestamps is recorded (`acceptance_dt_fallbacks` in meta).

**MDE self-report is MANDATORY and runs BEFORE confirm (binding).**
- The harness self-reports the **MDE — minimum detectable effect** = the smallest true mean excess detectable at **80% power, two-sided α = 0.05** (`outcome_table.minimum_detectable_effect`, `z_α=1.96`, `z_power=0.842`), per horizon on both the cohort-relative **excess** and the **raw absolute** return (ADV-07), printed by `print_study_report`.
- **For the dose-response primary**, the MDE that gates the experiment is computed on the **Q5−Q1 difference distribution** at 63td on the explore stream — i.e. the smallest top-minus-bottom-quintile mean-excess gap the test could resolve at 80% power, using the pooled per-quintile n and σ. (The single-sample whole-stream MDE is also reported, but the *gate* is on the Q5−Q1 contrast because that is the headline.) Procedure: run the harness on the **explore event stream first**, compute the per-year quintile assignment from the frozen score (§4), read the explore Q5−Q1 63td MDE. **The confirm window is NOT touched until this is read and checked against the abort rule.**

**MDE ABORT RULE (frozen, decided now — cannot be relaxed post-hoc).**
- **Smallest economically meaningful edge for a ~$10k account after costs.** Per repo cost conventions the modeled cost is **2 bps/leg slippage + $0 commission** (§7), ~**4 bps round-trip ≈ 0.04 percentage points (pp)** of drag per trade. We set the **smallest economically meaningful 63td Q5−Q1 excess gap = 1.0 percentage point** (≈ 25× the round-trip drag; a sub-1pp three-month top-minus-bottom-dose gap is below what a ~$10k retail account can reliably harvest after costs and sizing friction). Fixed here on cost reasoning, not on any observed effect size.
- **The rule:** if the explore-window **63td Q5−Q1 MDE > 1.0 pp**, the design is **underpowered to detect even the smallest dose-response gap worth trading**, and the experiment **ABORTS before the confirm run**. Verdict = **UNTESTABLE — underpowered (Q5−Q1 MDE = ⟨value⟩ pp > 1.0 pp floor)**; confirm stays sealed; the Form-4 corollary's evidence status is left unchanged.
- If `Q5−Q1 MDE ≤ 1.0 pp` at 63td on explore, the confirm proceeds under §4/§8.
- The MDE (Q5−Q1, whole-stream excess, and raw) at all three horizons is **reported regardless**, alongside `n_explore_valid` and the per-quintile event counts.

### 3b. PRE-REGISTERED PERTURBATION BAND (John's "$49k", institutionalized — frozen)

The charter **mandates** re-running the §4 primary comparison (the Q5−Q1 63td excess gap and the monotonicity trend) under a small, frozen band of perturbations to the score's constants, and requires **sign stability** — *not* significance at every setting. This is a **frozen robustness check, NOT a search**: its only output is a per-perturbation sign of the Q5−Q1 gap (and of the Spearman trend), and the binary verdict-capping flag below. No perturbation result is ever selected as "the" answer; the frozen-constant result of §2b remains the headline.

**The frozen perturbation set (decided now, no additions post-hoc):**

| Perturbation | Settings re-run | What it answers |
|---|---|---|
| **Window W ± 1 business day** | W ∈ {**20, 21, 22**} business days | John's "what if 6 days not 5?" — does a one-bday nudge of the dose window flip the dose-response sign? |
| **Score-floor ± 20%** | apply a soft minimum-dollar floor `F` to `D` before the ratio, at **F ∈ {0 (frozen primary), $40k, $60k}** (i.e. a $50k reference floor perturbed ±20%; events with `D < F` get `D` clamped to 0 for that perturbation only) | John's "what if the sum is $49k?" — does where we put the dollar floor flip the sign? (The frozen primary has **no** floor, F=0; the ±20% band is the robustness probe around the v1 $50k notion.) |

For **each** perturbation setting the harness re-derives per-year quintiles from the perturbed score and recomputes the Q5−Q1 63td mean-excess gap and the Spearman quintile-trend sign. The band is run on **both** explore (reported) and, if the experiment reaches it, confirm (the confirm band is part of the single sealed touch).

**SIGN-STABILITY REQUIREMENT (frozen, verdict-capping):**
- The requirement is **sign stability of the Q5−Q1 gap AND the monotonic trend across the *entire* band** — every setting in {W∈{20,21,22}} × {F∈{0,40k,60k}} must show the **same sign** as the frozen-constant primary (positive gap, positive trend). Statistical significance is **not** required at every band setting (small per-bucket n makes that too strict); only the **direction** must hold.
- **A sign flip anywhere in the band → the verdict is CAPPED at WEAKENED**, regardless of how significant the headline (frozen-constant) p-value is. A finding that depends on whether the window is 20 vs 22 days, or the floor is $40k vs $60k, is fragile by John's own standard and is not promotable to CONFIRMED.
- The full band table (sign of Q5−Q1 gap + Spearman sign per setting) is **reported in the verdict** so the cap is auditable. This is a pre-registered branch, not a post-hoc carve-out.

---

## 4. HYPOTHESES (H1 dose-response / H1b trend / H2 horizon) + TEST (frozen before any outcome data)

**Horizons.** Primary = **63 trading days (~3 months)**. Secondaries (descriptive, pre-registered, NOT separately alpha-scanned) = **21 td** and **126 td** (`V2_HORIZONS_TRADING_DAYS = (21, 63, 126)`).

**Outcome metric.** Per-event cohort-relative **excess** = `pick Open-anchored forward return − same-entry-date universe-median forward return` at the matched horizon (`event_study.py` `_forward_return_terminal` + `_compute_universe_median`, ADV-03), NET of the §7 cost model.

**Quintile construction (frozen).** Within **each calendar year** of the window (explore: 2015…2020; confirm: 2021…2024), rank that year's valid events by the §2b frozen score and split into **5 equal-count quintiles** (Q1 = lowest dose … Q5 = highest dose). **Within-year** stratification removes era-mix confounds (a year that was simply good for everything cannot masquerade as a dose effect). Quintile assignment is on the de-duplicated event stream (§6). Events with `score_undefined` (missing MC) are excluded from quintiling and counted. The per-year-per-quintile mean 63td excess is the analysis unit; the window-level quintile mean pools across years within the window.

**Primary test machinery.** Harness **moving-block bootstrap** (Künsch MBB, `_block_bootstrap_pvalue`), `n_boot = 999`, SEED 20260606, block size density-derived per horizon (`_block_size_for_horizon`), capped at `n//2`. **Cross-check** = Newey–West HAC t-test (`_nw_ttest_pvalue`). For the Q5−Q1 contrast the bootstrap resamples the **difference** of the two quintiles' per-event excesses (block-resampled to respect overlap). The bootstrap and NW p-values must **agree within 0.10** at the primary horizon; a larger gap is flagged as an event-density / block-size caveat.

### H1 — PRIMARY: DOSE-RESPONSE (top-dose quintile beats bottom-dose quintile at 63td).
- **Prediction:** the **top-quintile (Q5) mean 63td excess > the bottom-quintile (Q1) mean 63td excess** — i.e. the Q5−Q1 difference is **> 0**. (More insider-buying dose → more forward excess.)
- **CONFIRMED bar (ALL required, on the sealed confirm window 2021–2024):**
  1. **Power gate passed** (§3a: explore 63td Q5−Q1 MDE ≤ 1.0 pp — else UNTESTABLE, no confirm).
  2. **Occupancy:** confirm window has **≥ 3 of the 4 confirm years** (2021/2022/2023/2024) populated with ≥ 1 valid event in **both** Q1 and Q5, AND `n_confirm_valid ≥ 25` total valid 63td events (a higher floor than v1's 15 because the design now needs both tails populated for the contrast — 25 keeps ≥ ~5 per quintile; below 25 → UNTESTABLE-confirm).
  3. **Point estimate:** confirm 63td **Q5−Q1 mean excess > 0** (correct direction).
  4. **CI excludes zero:** the confirm 63td seeded block-bootstrap test on the **Q5−Q1 difference** rejects H0 at the per-comparison α (§5) with the difference on the **positive** side (bootstrap CI lower bound on Q5−Q1 > 0).
  5. **Perturbation sign-stability holds (§3b):** the confirm Q5−Q1 gap sign is **stable across the entire perturbation band** (W∈{20,21,22} × F∈{0,40k,60k}). A flip caps the verdict at WEAKENED (§3b, §9).
- **Explore precondition for *reaching* confirm:** confirm is touched ONLY if the **explore** window shows a **positive Q5−Q1 63td gap** AND explore perturbation sign-stability (§3b) holds AND **H1b (below) trends the right way in explore**. Otherwise the experiment stops at explore with verdict **WEAKENED-IN-EXPLORE / not advanced to confirm**; confirm stays sealed.

### H1b — DOSE-RESPONSE MONOTONICITY (the whole ladder trends, not just the endpoints).
- **Prediction:** across the five quintile means (Q1…Q5) of 63td excess, **excess rises with dose** — a monotone-increasing ladder.
- **Frozen test:** **one-sided Spearman rank correlation** between the **quintile index (1…5)** and the **per-quintile mean 63td excess**, ρ_s, tested one-sided (Hₐ: ρ_s > 0) at the §5 α. (Spearman, not Pearson, because we care only that the buckets *order* correctly, not their exact spacing — robust to the dose's nonlinear scale.) A complementary **per-year** Spearman is reported (the within-year ρ_s for each year), and the **fraction of years with ρ_s > 0** is recorded as a consistency read.
- **H1b bar:** on the confirm window, ρ_s > 0 and **one-sided-rejected** at the §5 α. H1b is **alpha-bearing** (it is the dose-response claim) and is part of the FDR family (§5). H1 (Q5−Q1 endpoints) and H1b (full-ladder trend) are **both** required for CONFIRMED — endpoints could separate while the middle is noise; the Spearman insists the *gradient* is real.

### H2 — TRADEABLE-THRESHOLD CANDIDATE (subordinate, the Radar-desk product).
- This is the **clearly-subordinate** secondary: even though the *finding* is dose-response, the *tradeable product* is a top-quintile-only long basket (you cannot trade a correlation, you trade the high-dose names). **Prediction:** the **Q5 (top-dose) absolute mean 63td excess > 0** on the confirm window (not just relative to Q1).
- **H2 bar:** confirm Q5 mean 63td excess > 0 with the seeded block-bootstrap rejecting H0 on the positive side at the §5 α. H2 **HOLDS** iff so. H2 is **subordinate**: it does NOT gate H1/H1b, and it is alpha-bearing only for the "top-quintile is a tradeable Radar candidate" wording. If H1/H1b CONFIRM but H2 fails (the gradient is real but the top bucket alone doesn't clear zero net), the verdict is CONFIRMED-as-dose-response with a note that *the tradeable top-quintile product is not yet established*.

### H3 — HORIZON CONSISTENCY (descriptive, not gating, not alpha-bearing).
- Report the Q5−Q1 gap and Spearman trend at 21td and 126td alongside 63td. Descriptive consistency only; not separately alpha-scanned.

**UNTESTABLE rule (encoded up front):** explore 63td Q5−Q1 MDE > 1.0 pp → UNTESTABLE-underpowered (no confirm). Confirm occupancy < 3 of 4 years with both tails populated OR `n_confirm_valid < 25` → UNTESTABLE-confirm. Survivorship warning (`events_no_price_data` fraction > 0.10, ADV-02) → verdict flagged **SUSPECT**, not promoted to CONFIRMED until resolved.

---

## 5. FDR LEDGER ENTRY + ALPHA BUDGET (frozen)

**FDR ledger.** This experiment's hypotheses register in the cross-run append-only ledger at **`backend/data/turnaround/fdr_ledger.json`** (`event_study._append_fdr_ledger`, ADV-05). The harness applies the **Benjamini–Hochberg step-up** (`FDRLedger`) across this study's alpha-bearing hypotheses at **q = 0.10** (frozen). The ledger records, per run: study name, config hash (which **now includes every frozen score constant** of §2b — W, β, the log shape, the MC construction — so the dose formula is auditably pinned), `q`, `n_boot`, horizons, per-test block sizes + bootstrap p-values + valid n, the per-quintile counts, the perturbation-band sign table, and the BH rejection set.

- **Family (frozen):** the BH family for THIS study = **{ Q5−Q1_63d (H1), Spearman_trend_63d (H1b), Q5_abs_63d (H2) }** — the three alpha-bearing hypotheses on the **primary 63td horizon**, controlled together at **q = 0.10**. The 21d/126d quintile reads and per-year Spearmans are **co-registered descriptively** (H3 / consistency) but are **not** separate BH family members (they are not scanned for a "best horizon"). The dose itself is a single frozen instrument, not a family of score variants — the perturbation band is a robustness re-run of the same hypotheses, NOT additional family members (it changes no verdict except via the sign-stability cap).
- **Per-comparison α for the CI-excludes-zero bars:** H1, H1b, and H2 are each judged at the BH-controlled level — each must be **BH-rejected at q = 0.10** within the 3-member family with the estimate on the predicted side. CONFIRMED requires **H1 AND H1b** both BH-rejected (H2 subordinate, §4).
- **Cross-experiment note:** R-1 is its own family in the ledger (distinct `study_name` + `study_config_hash`); it draws no other experiment's budget. The program Decision Gate reconciles ledger totals. This charter invents no post-hoc correction.

---

## 6. DE-CLUSTERING / PSEUDO-REPLICATION RULE (frozen)

**Setting (frozen):** harness same-ticker de-clustering **ON** — `dedup_same_ticker = True`, **`dedup_window_days = 21`** (raised from v1's 7 to match the **21-business-day dose window W**: within the dose-aggregation window a company is, by construction, one buying wave and must be one degree of freedom — otherwise overlapping trailing windows on the same ticker would double-count the same dose). 21 calendar days is the conservative collapse aligned to the 21-bday dose window.

**What it does (`_dedup_events`):** same-ticker `EventRecord`s whose **event_ts ET dates** fall within 21 calendar days collapse to the **first (chronologically earliest)** event — the one carrying the dose computed over its trailing window. One buying wave → one degree of freedom.

**Reporting (binding):** the **raw pre-dedup event count** and **number de-clustered** are recorded in `meta["survivorship"]` (`events_total`, `events_after_dedup`, `events_declustered`) and reported. The bootstrap, quintiling, and all hypotheses are computed on the **de-duplicated** stream only.

**Non-overlapping cross-check (descriptive, not gating):** `use_non_overlapping = False` for the primary (the block bootstrap corrects return overlap); the harness's greedy non-overlapping filter count at 63td is reported descriptively (`meta["non_overlapping"]`).

---

## 7. COST MODEL + SURVIVORSHIP (frozen)

**Cost model (repo conventions, frozen):** **long-only**; modeled **slippage = 2.0 bps per leg** (repo default); **per-share commission = $0.0**, **min-per-order = $0.0** (commission-free, matches Alpaca US equities); **no borrow** (long). Excess figures reported **NET**. Both Q5 and Q1 (and every quintile) are long baskets under the identical cost treatment and the universe-median benchmark is itself a long basket under the same tape, so the cost drag is small and roughly **common across the Q5−Q1 difference** (it nearly cancels in the contrast); a ~0.04pp round-trip drag cannot move a 1pp+ MDE-clearing Q5−Q1 gap across zero.

**Survivorship (R5 / ADV-02 / ADV-03, noted):** events whose ticker is missing from the price cache are **counted** (`events_no_price_data`), never silently dropped; a no-price fraction > 0.10 raises the survivorship warning and the verdict is flagged **SUSPECT**. At long horizons a pick or peer whose price series ends inside the horizon contributes its **terminal close** as exit, symmetrically on both sides of the excess (ADV-03). Per-horizon terminal-exit counts reported. Events with **undefined market cap** (no point-in-time shares or no cached close) are counted (`score_undefined`) and excluded from quintiling — reported alongside.

**F338 FACE-VALIDITY ANCHORS (real-data smoke probe BEFORE any interpretation — mandatory):**
1. **Score sanity:** at a known cohort, spot-check 2–3 events — recompute the dose by hand: trailing-21-bday P-purchase dollars `D`, distinct-insider count `k`, point-in-time market cap `MC = get_shares_outstanding(cik, d) × cached close ≤ d`, and verify `score == log1p(D/MC)·(1+0.5·k)` to floating tolerance. A high-dollar / multi-insider / small-cap name scores **higher** than a token single-insider buy at a mega-cap.
2. **Quintile sanity:** within one explore year, the 5 quintiles are equal-count (±1), Q5's median dose > Q1's, and the within-year split is on that year's events only (no cross-year leakage).
3. **Entry sanity:** entry_date is the first trading day strictly after the event ET date; entry_price is the Open (Close-fallback events counted/flagged).
4. **Point-in-time / floor + MC:** floor status decided at the **event date** (ADV-01); `get_shares_outstanding` uses only `filed ≤ d` entries (no look-ahead shares); a known sub-$5 or split-corrupt name is excluded and counted; a known `score_undefined` (missing shares) event is excluded from quintiling and counted.
5. **Excess sign:** a known same-cohort big winner shows positive 63td excess, a laggard negative.
6. **De-dup:** a known same-ticker double-arrival within 21 days collapses to one event (`events_declustered` ≥ 1 where expected).
7. **MDE + perturbation printed:** `print_study_report` emits a finite 63td **Q5−Q1** MDE on the explore stream (sane, not NaN), `n_explore_valid` is the count feeding the bootstrap, and the **perturbation-band sign table** (W∈{20,21,22} × F∈{0,40k,60k}) is emitted with a sign per cell.
8. **Survivorship line:** the `events_total / no_price_data / below_floor / score_undefined / entered` line is populated and the no-price fraction is below 0.10 (or SUSPECT fires).

Reading the artifact (meta.json + events.ndjson, including the per-event `score` on the payload) and checking these anchors is **part of the gate** (F338).

---

## 8. BLIND-GRADING PROTOCOL (frozen)

**Two-window, explore-blind-confirm protocol** (same as the four prior experiments):
- **Explore (2015–2020, OPEN):** `explore_cutoff = 2020-12-31` (hard ceiling — explore may NEVER reach 2025+; `allow_post_2020_explore` is **NOT** set). Explore supplies the within-year quintiles, the **mandatory Q5−Q1 MDE power read** (§3a), the **explore perturbation band** (§3b), and the **explore dose-response precondition** (§4). It is the only window an agent may inspect while deciding whether to advance.
- **Confirm (2021–2024, SEALED):** entry_date > 2020-12-31 and ≤ 2024-12-31. The confirm verdict is computed and graded by an **agent that has NOT seen the explore results** — it receives this frozen charter (sha-verified), the §4 bars, the §3b band, the §6 mapping, and the confirm-window artifacts only. The confirm number (Q5−Q1, Spearman, Q5-abs, and the confirm perturbation band) is read **once** (single sealed touch). The §4 bars are applied verbatim.
- **Per-year breakdown in the confirm verdict (binding):** the confirm verdict MUST include the per-confirm-year (2021/2022/2023/2024) per-quintile mean 63td excess + the per-year Spearman signs + the **full perturbation-band sign table**, so the dose-response and the §3b sign-stability cap are auditable from the verdict alone.

---

## 9. ABORT CRITERIA + VERDICT DEFINITIONS (frozen)

**Abort / UNTESTABLE (no confirm spent):**
- **Underpowered:** explore 63td **Q5−Q1 MDE > 1.0 pp** → ABORT before confirm; **UNTESTABLE — underpowered** (§3a). Confirm sealed.
- **Explore dose-response absent:** explore Q5−Q1 63td gap ≤ 0, OR explore Spearman trend not positive, OR explore perturbation sign flip → stop at explore, **WEAKENED-IN-EXPLORE**, confirm not advanced (§4). Confirm sealed.
- **Confirm occupancy:** confirm < 3 of 4 years with both Q1 and Q5 populated OR `n_confirm_valid < 25` → **UNTESTABLE — confirm underpowered**.
- **Survivorship:** `events_no_price_data` fraction > 0.10 → flagged **SUSPECT**; not promotable to CONFIRMED until resolved.

**Verdict definitions (read on the confirm window under §4/§8):**

| Verdict | Requires |
|---|---|
| **CONFIRMED** | Power gate passed; occupancy met; **H1** (confirm 63td Q5−Q1 > 0, BH-rejected at q=0.10 positive side) **AND H1b** (confirm 63td Spearman ρ_s > 0, one-sided BH-rejected) both hold; **AND §3b perturbation sign-stability holds across the whole band**. (If H2 also holds — Q5 absolute 63td excess > 0, BH-rejected — the verdict additionally notes "top-quintile is a candidate tradeable Radar product"; if H2 fails, verdict is CONFIRMED-as-dose-response with "tradeable top-quintile not yet established".) |
| **WEAKENED** | Explore cleared the dose-response precondition and advanced to confirm, but the confirm **fails at least one** non-inversion bar — Q5−Q1 CI touches zero (not BH-rejected), OR Spearman not rejected, OR n too small after dedup, **OR a perturbation-band sign flip occurred** (§3b cap: a flip caps at WEAKENED even if the frozen-constant headline cleared). The dose-response appeared in explore but did not survive sealed confirmation / was not sign-stable. (Includes **WEAKENED-IN-EXPLORE** when the experiment stops before confirm per §4.) |
| **REVERSED** | Confirm 63td **Q5−Q1 mean excess < 0** (and/or Spearman ρ_s < 0) with the seeded block-bootstrap **BH-rejecting H0 at q=0.10 on the negative side** — higher insider-buying dose predicted **worse** forward excess out-of-sample. The dose-response runs backwards; the Form-4 cluster signal is contradicted as stated. |
| **UNTESTABLE** | Any abort condition above (underpowered Q5−Q1 MDE, confirm occupancy, or — for the strong claim — survivorship SUSPECT unresolved). The Form-4 corollary's evidence status is left unchanged. |

**Axiom consequence (sequencing).** This experiment tests the **Form-4 corollary** of the "price leads, filings trail" axiom (the named upstream exception). The outcome produces a verdict doc + an evidence-status **proposal**; **the actual `CLAUDE.md` edit is applied only at the program's Decision Gate**, after it reconciles the FDR ledger totals against the program alpha/q budget. No single experiment rewrites the axiom on its own.

---

## 10. AMENDMENT RULE (frozen on write)

This charter is **FROZEN ON WRITE** (sha-pinned at implementation). Any change to **the §2b frozen score formula or any of its constants** (window W, the distinct-insider weight β, the log shape, the market-cap denominator construction, the 10b5-1 rule), the §2a event definition, the entry semantics, the §4 quintile construction / horizons / H1 / H1b / H2 bars, the §3b perturbation band (its members or the sign-stability cap), the §3a MDE abort threshold, the §5 FDR family or q, the §6 de-dup setting, the cost model, the bootstrap seed, or the verdict mapping **after outcome contact** (after any explore or confirm number is consulted) constitutes a **NEW experiment** with its own charter and its own ledger draw — it does not amend this one. **The continuous score is hashed exactly like a numeric threshold; its fuzziness as a *concept* licenses no tuning of its *numbers*.** Pre-outcome implementation-mechanics fixes (a bug caught on the §7 smoke probe before any result is read) are permitted and logged, provided no outcome data has been consulted. The UNTESTABLE / WEAKENED-IN-EXPLORE / SUSPECT branches and the §3b perturbation band are **pre-registered branches decided here**, not amendments. **This v2 revision itself is a legal pre-outcome amendment** of the v1 DRAFT (no result was ever read for R-1; v1 was never run).

---

*Authored outcome-blind 2026-06-06 (v1); revised outcome-blind 2026-06-06 (v2, dose-response). No results, verdicts, gates, ledgers, reanalyses, or program-journal files were read in drafting or revising this charter. Awaiting John's approval; nothing runs until approved.*
