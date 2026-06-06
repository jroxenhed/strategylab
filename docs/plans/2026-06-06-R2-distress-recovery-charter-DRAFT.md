# R-2: Distress Recovery (long) — Pre-Registered Charter — DRAFT v2

**STATUS: DRAFT v2 — AWAITING JOHN'S APPROVAL. NOTHING RUNS UNTIL APPROVED.**
**To be FROZEN ON WRITE (sha-pinned at approval, same convention as REGIME-TEST / MOMENTUM-TEST / DETERIORATION-TEST / EPISTEMICS-ABLATION).**
**Date drafted:** 2026-06-06. **Author:** outcome-blind charter agent (no results data consulted — see §0).
**Experiment ID:** R-2. **Engine:** event-clock harness (`backend/research/event_study.py`, `run_event_study`, schema v2).

This is a pre-registered **hypothesis test**. No claim of "edge", "alpha", or "tradeable" structure is made anywhere except in negation. The premise is a deliberate **inversion** of the frozen D2 deterioration-short screen (`backend/research/config_deterioration.py`), re-cast as a **long** recovery hypothesis. The single criterion (the D2 state) is fixed; there is no grid and nothing to tune.

> **v2 revision note (pre-outcome, legal under §6 amendment rule).** John reviewed the v1 DRAFT and flagged a *dose-response* critique of the lab's binary screens generally ("what if the drawdown is 49% not 50%? is the effect proportional to how distressed?"). For R-2 this critique is **deliberately deferred, not adopted**: the D2 state is kept **frozen exactly as-is** because R-2's whole purpose is to test the *specific historical phenomenon* the deterioration-short screen already defined (a continuous-distress variant would be a *different* premise, not this one). v2 makes two additions only: (1) a **pre-registered perturbation band** (§3b) that re-runs the primary under small nudges to the D2 thresholds and requires *sign stability only* — institutionalizing John's "what if 49% not 50%?" as a robustness check without changing the frozen screen; and (2) a **registered FUTURE variant R-2b** (§1) — "depth of distress as a continuous dose" — *explicitly deferred to its own charter* so it cannot be confused with this charter's scope. The 2021–2024 contamination ban is **untouched**. No outcome data was read in making this revision; pre-outcome amendment is legal under §6.

---

## 0. Honesty preconditions (binding)

- All claims are stated as hypotheses with directional predictions and pre-set bars. Every number below is fixed here from convention, from the harness's documented behaviour, or from event-rate arithmetic — none is tuned.
- **Outcome-blindness of this charter (absolute).** This document was authored without reading ANY results, verdict, reanalysis, audit, gate, null-atlas, power-audit, outcome-table, FDR-ledger, JOURNAL, postmortem, or `.run/` result artifact. Allowed inputs were exactly: the harness source, the D2 selection source (`config_deterioration.py` — a selection definition containing no outcomes), and the prior EPISTEMICS-ABLATION charter as a format template (results rows are angle-bracket placeholders, not outcomes).
- **The single criterion is the frozen D2 state, used as-is.** The screen is NOT re-tuned for the long direction. D2 = Gates A+B+D of `config_deterioration.py`: `pct_off_high ≥ 50` (crashed ≥ 50% off the trailing-252td high) AND `pct_above_low ≤ 25` (within 25% of the trailing-252td low) AND ≥ 252 td of history. The revenue veto (Gate C / D1) is deliberately **OFF** — this premise conditions on *continued timely filing*, not on fundamentals (§1, §2).
- **Bootstrap reproducibility (inherited from the program's MV-03 rule — binding):** the harness moving-block bootstrap (`_block_bootstrap_pvalue`, primary) and the Newey-West HAC t-test (`_nw_ttest_pvalue`, cross-check) both run under a **fixed seed. SEED = 20260606** (this charter's draft date). Every bootstrap draw in explore and confirm uses this seed via `np.random.default_rng(20260606)`; the verdict record is bit-reproducible.
- **Real-data smoke probe (F338, MANDATORY before any interpretation):** before any explore/confirm number is read, the implementation must run the event stream on real cached data over a known small slice and check the §6 face-validity anchors. Reading the artifact before interpreting it is part of the gate. Green synthetic suites are NOT sufficient (proven 3× on 2026-06-05).
- No threshold below is tunable. The only pre-registered freedoms are the §3 occupancy/coverage branches (decided and frozen now) and the fixed N implied by event-rate arithmetic (§2). There is no variant grid: the screen is exactly the D2 state.

---

## 1. THE QUESTION + THE POST-HOC CONTAMINATION CONSTRAINT (the reason 2021–2024 is off-limits)

**The premise under test (the inversion):** the lab previously defined the D2 cohort — stocks crashed ≥ 50% off their high, sitting near their 1-year low, above the liquidity floor, *still filing on time* — as **SHORT** candidates (the deterioration-short experiment). R-2 inverts that: the new hypothesis is that **such names, conditioned on the arrival of a continued on-time quarterly filing, RECOVER** — their forward cohort-relative excess is **positive at medium horizons** when held **long**. Intuition: a deeply-distressed name that keeps filing its 10-Q/10-K on schedule is signalling it is *not* dying quietly (no going-concern filing gap, no delisting silence); the on-time filing is the public confirmation that the worst-case (the company going dark) did not happen, and the crash may have overshot.

**THE QUESTION (locked):** *Among D2-state names, does the arrival of an on-time quarterly filing mark a positive forward long excess at medium horizons?* The answer is read as the seeded moving-block-bootstrap CI on the mean **63-trading-day cohort-relative excess** of the events, on the **fresh confirm window** (§4 H1).

### THE CONTAMINATION CONSTRAINT (why 2021–2024 can NEVER be this charter's confirm window)

**Plain statement of the problem.** A confirmation test is only honest if the hypothesis was written down *before* anyone looked at the data the test will be graded on. This particular hypothesis — "invert the deteriorating-short cohort into a long recovery bet" — was **generated POST-HOC from already-unsealed 2021–2024 data.** The deterioration-short experiment ran on 2021–2024, its confirm window was opened and read, and the recovery idea was born *from looking at how those same names behaved in that same window.* That means 2021–2024 is, **for this hypothesis, contaminated by construction**: the data already shaped the question. Re-using it as "confirmation" would be grading an exam using the answer key the question was copied from — it would manufacture a positive result with zero real out-of-sample content.

**The binding rule (frozen, both directions):**
> **2021–2024 may NEVER be cited as evidence for or against the R-2 distress-recovery premise — in either direction.** It is not confirm. It is not even admissible explore. Any number computed on 2021–2024 for this premise is contaminated and is reported, if at all, only as an explicitly-labelled "contaminated, non-evidential" diagnostic that draws no alpha and updates no axiom.

**The two clean windows this charter is allowed to use:**
- **(a) PRIMARY CONFIRM = forward window, entry_date ≥ 2025-01-01.** The price cache extends to 2026-06-05 (verified: a liquid name has 357 trading rows in 2025+). The harness *hard-guards 2025+ out of every explore run* (`_EXPLORE_HARD_CEILING = 2020-12-31`; explore cannot reach past it unless `allow_post_2020_explore` is set — which this charter forbids). That hard guard is exactly what makes 2025+ **virgin**: no prior experiment in this program has been allowed to peek at it. R-2 claims 2025+ as its genuinely-fresh confirmation. **Graded ONCE**, when the accrual threshold (§3) is met.
- **(b) EXPLORE / REFINEMENT = 2015–2020 ONLY.** Hypothesis-generating, open. This is pre-2021 data the deterioration experiment's *confirm* read never touched (its confirm was 2021–2024; its explore was 2015–2020 but for the SHORT framing, not this LONG inversion). 2015–2020 is the legitimate sandbox for checking the mechanism is wired correctly and reporting an explore-window direction, with **no alpha drawn** there.

**Mechanically:** the harness partitions on `entry_date <= explore_cutoff → "explore"` else `"confirm"`. With `explore_cutoff = 2020-12-31` (the hard ceiling, NOT overridden), every 2021+ event lands in the harness's `"confirm"` bucket. **The grading agent (§8) MUST further filter that bucket to `entry_date ≥ 2025-01-01` before reading any confirm number, and MUST discard every 2021-01-01…2024-12-31 event as contaminated.** The 2021–2024 events are emitted into the events table only so survivorship counting is complete; they are tagged and excluded from all evidential reads. This filtering rule is frozen here so it cannot be relaxed post-hoc to borrow 2021–2024 power.

### NOTE — John's dose-response critique, and why R-2 keeps the D2 state BINARY (the deferred R-2b variant)

John's standing critique of the lab's binary screens is the **dose-response** one: a hard 50%-off-high cutoff is arbitrary at its edge (why not 49%? why not 55%?), and a richer design would treat *depth of distress* as a continuous dose and ask whether forward recovery rises smoothly with it. **R-2 deliberately does NOT adopt that here**, for a specific and frozen reason:

> **R-2's purpose is to test the *specific historical phenomenon* the deterioration-short screen already defined — the D2 *state* exactly as `config_deterioration.py` froze it.** Re-casting distress as a continuous dose would change *what is being tested*: it would no longer be "does the D2 cohort (as historically defined) recover?" but "is recovery monotone in distress depth?" — a different question with a different cohort, a different null, and its own multiplicity. Folding that into R-2 would silently mutate the premise mid-flight and forfeit the clean inversion of an already-frozen screen. So the binary D2 state is **kept frozen as-is on purpose** (it is a feature, not an oversight), and the dose-response idea is **registered as a separate future experiment**:

> **R-2b (REGISTERED FUTURE VARIANT — DEFERRED, not in scope here): "depth-of-distress as continuous dose."** A future charter that replaces the binary D2 gates with a *continuous distress score* (e.g. a monotone function of `pct_off_high` and `pct_above_low`, frozen with its own constants and hashed) and tests **dose-response monotonicity** of forward long excess across distress-depth quintiles — analogous to R-1's dose-response design. **R-2b is explicitly NOT this charter**: it would carry its own ID, its own pre-registration, its own alpha draw, its own contamination analysis (its score is *also* post-hoc relative to 2021–2024 and would inherit the same 2025+-only confirm discipline), and its own sha pin. It is named here **solely so that R-2's binary scope is unambiguous and the dose-response idea is not quietly smuggled into R-2's verdict.** Nothing in R-2b is decided, run, or graded by this charter. (The §3b perturbation band below is the *only* nod to John's edge-sensitivity concern that R-2 itself carries — and it is a sign-stability robustness check on the frozen binary screen, NOT a continuous-dose redesign.)

---

## 2. EVENT DEFINITION (state → event, in event-time) + UNIVERSE + N (R1)

The D2 screen is a **state** (a name *is* crashed-and-near-low on a given day). The harness needs discrete **events**. R-2 converts the state into events with a single frozen rule:

> **EVENT = the arrival (acceptance) of an on-time periodic quarterly filing (form ∈ {`10-Q`, `10-K`}) by a name that is in the D2 state as of the trading day immediately before the filing's acceptance.** Entry = the next trading day's OPEN after the filing became public (`acceptanceDateTime` → ET → next-trading-open, the harness's `entry_lag_days = 1` Fork-A semantics). Each such filing-while-distressed is one event.

**Why this is the right event (and not the bare state).** The premise is explicitly *"conditioned on continued timely filing."* The economically-meaningful instant is the moment the distressed company publicly confirms it is still filing on schedule — that is the on-time 10-Q/10-K acceptance. Using the filing *arrival* as the event (a) makes the event a point-in-time public timestamp the harness already parses (`acceptanceDateTime`, with `filingDate`+16:01 ET fallback), (b) honours the Research Axiom's "filings as **confirmation/filter**, never as a blind trigger" — here the filing *confirms the survival the price already crashed on*, it does not initiate a fresh fundamental bet, and (c) gives a clean entry timestamp (next open after acceptance) with no look-ahead.

**Frozen event parameters (no grid):**
- **D2-state test:** evaluated by `config_deterioration._compute_price_gates(df, as_of)` with `as_of` = the last trading day **strictly before** the filing's ET acceptance date (point-in-time: the state is decided from information available *before* the filing prints; the filing is the event, not an input to the state). The state passes iff `gate_a` (`pct_off_high ≥ 50.0`) AND `gate_b` (`pct_above_low ≤ 25.0`) AND `gate_d` (≥ 252 td). **The revenue veto (Gate C) is OFF** — this is the D2 variant, not D1. (Definitions verbatim from `config_deterioration.py`; this charter reuses them, it does not redefine them.)
- **On-time test:** the filing is counted only if its `acceptanceDateTime` is parseable (or its `filingDate` fallback is). No separate "lateness" computation is introduced — the cohort is "names that ARE still filing," and the presence of a parseable 10-Q/10-K acceptance in the cache IS the on-time-filing evidence. (A name that stopped filing simply produces no event; a going-concern/NT 10-Q gap produces no qualifying 10-Q/10-K event. This is the filing-as-filter discipline, not a tuned lateness threshold.)
- **Forms:** `{10-Q, 10-K}` only. `10-Q/A`, `10-K/A`, `NT 10-Q`, and all non-periodic forms are excluded (amendments and notifications are not the clean "still filing on schedule" signal).
- **Entry:** `entry_lag_days = 1` (next trading day's OPEN after the ET acceptance date; the harness default Fork-A). `same_day_entry` is therefore False — no info-laden-open contamination (`event_study.py` ADV-09).
- **Direction:** **long** (the inversion). Forward returns computed long-sign (`_forward_return_terminal(..., direction="long")`).

**Universe:** the **same floored universe** the D2 screen and the harness null both use — `universe_floors.floor_status(df, as_of) == "ok"` enforced point-in-time, identically, on the event picks AND on the harness's exhaustive-null / universe-median path. Floor tokens (`ok` / `below_floor` / `corrupt_frame`) are the counted exclusion reasons. The universe-median peer set per entry_date is computed by `_compute_universe_median` over all floor-passing names alive on that entry_date, pick excluded (Fork B).

**Cohorts / null:** the harness's per-entry_date universe median is the cohort-relative baseline. Excess = pick forward return − same-entry_date floor-passing universe median forward return, at the matched horizon (`fwd_excess_pct`), long-sign. This neutralises market/tape/regime by same-date peer matching (it is NOT beta/vol-adjusted — `event_study.py` COR-04 note; acknowledged).

**De-clustering (R-2 setting, frozen — §6 below restates):** `dedup_same_ticker = True`, `dedup_window_days = 95`. A single distressed name files at most ~4 periodic reports/yr (one per fiscal quarter, ≈ every 63 td ≈ 91 calendar days). A 95-day window collapses any same-ticker double-count (e.g. a 10-K and an overlapping 10-Q/A pair, or a re-acceptance) to ONE event per fiscal quarter, so one quarter's "still filing" signal is one degree of freedom, not N. The raw pre-dedup count is always reported alongside (`survivorship.events_declustered`).

### Fixed N / event-rate declaration (R1)

R-2 does **not** rank-truncate to a fixed N-per-cohort (unlike the EPISTEMICS arms). The event set is **every qualifying D2-state on-time filing** — the cohort is defined by the state+filing conjunction, and its size is whatever the universe produces. The R1 obligation is therefore an **event-rate floor**, not an N:
- **Declared `expected_events_per_year` ≈ 30** across the full universe (order-of-magnitude operational declaration; the D2 state is rare — deeply-crashed-and-near-low names are a small slice of any universe, and only those still filing 10-Q/10-K qualify). This is a conservative floor for the explore window; the post-run R1 honesty check reports realised vs declared per window.
- **The fresh confirm window (2025+) will be SMALL at first.** ≈ 30 events/yr × ~1.4 yr of 2025+ data with completed 63td horizons → an order of ~30–45 confirm events when first gradeable. The §3 minimum-event gate governs whether that is enough to grade or whether R-2 **waits**.

---

## 3. CLOCK + MDE GATE + OCCUPANCY/ACCRUAL PRE-CHECK (all frozen)

**Clock.** Explore runs first, on **2015–2020 only** (`explore_cutoff = 2020-12-31`, `allow_post_2020_explore = False` — the hard guard is NOT overridden). The confirm is **graded once**, on `entry_date ≥ 2025-01-01`, and only when the accrual gate below is met. 2021–2024 events are emitted-but-excluded (§1).

**MDE SELF-REPORT (MANDATORY before any confirm read — Research Axiom).** The harness self-reports the minimum detectable effect on the **explore** stream (`compute_study_stats` → `mde_by_horizon` on excess, and `mde_raw_by_horizon` on raw absolute return, ADV-07, at 80% power). This MUST be read and recorded BEFORE the confirm window is opened.
- **ABORT-IF-UNDERPOWERED bar (frozen):** if the explore-window **63td excess MDE exceeds 4.0 percentage points**, R-2 is declared **UNDERPOWERED — NOT GRADED**, and the confirm window is NOT opened.
- **Reasoning for the 4.0 ppt bar (the smallest economically-meaningful edge for a ~$10k account after costs).** John runs ~$10k; per-trade costs are first-order. The cost model is 2 bps/leg slippage × 2 legs = **4 bps round-trip ≈ 0.04 ppt** of drag (commission-free, long-only — §7). A medium-horizon (63td ≈ 3-month) recovery trade that does not clear costs by a wide multiple is not worth the capital risk in a distressed name. An edge below ~4 ppt over 63td (≈ 16 ppt annualised gross) is, after the round-trip drag and the elevated single-name tail risk of buying crashed stocks, not a margin a ~$10k discretionary account can harvest reliably; if the *experiment itself* cannot resolve an effect smaller than 4 ppt (MDE > 4 ppt), then a true edge in the economically-interesting 0–4 ppt band would be invisible to this test, so grading it would be theatre. 4.0 ppt is thus both the floor of economic interest AND the resolution the test must beat to be worth grading. (This bar is on the **explore** MDE, computed before any confirm contact, so it cannot be chosen to suit a confirm number.)

**MINIMUM-EVENT / ACCRUAL GATE (frozen — the "wait rather than grade" rule).** The 2025+ window accrues over real calendar time. The confirm is graded **only** when BOTH:
1. **At least N_min = 30 confirm events** (entry_date ≥ 2025-01-01) have a **completed 63td horizon** (the primary horizon's forward window fully elapsed in the cache — not a terminal-exit stub); AND
2. **At least 4 distinct calendar quarters** in 2025+ each contribute ≥ 1 such event (so the confirm is not one quarter's idiosyncrasy — the per-sub-era breakdown of §4 has ≥ 4 non-empty cells).

Until BOTH hold, R-2 is **PENDING — INSUFFICIENT FRESH EVENTS**; the charter **waits** and re-checks on a later cache refresh. It does NOT grade a thin window and it does NOT relax the threshold. (N_min = 30 is the conventional small-sample floor below which the moving-block bootstrap on cohort-relative excess is too coarse to exclude zero meaningfully; ~30 events across ≥ 4 quarters is the minimum at which the §4 era-agreement gate is even defined.)

### 3b. PRE-REGISTERED PERTURBATION BAND (John's "what if 49% not 50%?", institutionalized — frozen)

The D2 state is kept **frozen as-is** (§0, §1 NOTE) — the dose-response redesign is deferred to R-2b. But John's *edge-sensitivity* point still applies to a binary screen: a verdict that hinges on whether the drawdown cut is 50% vs 49% is fragile. So the charter **mandates** re-running the §4 primary (the H1 63td mean-excess sign on the fresh 2025+ confirm) under a small, frozen band of nudges to the D2 thresholds, and requires **sign stability only** — *not* significance at every setting. This is a **frozen robustness check, NOT a search and NOT a re-tune of the screen**: its only output is a per-perturbation sign of the mean 63td excess and the binary verdict-capping flag below. No perturbation result is ever selected as "the" answer; the frozen-D2 result of §2 remains the headline. (This band is the *only* edge-sensitivity nod R-2 carries; the continuous-dose treatment is R-2b's, not this charter's.)

**The frozen perturbation set (decided now, no additions post-hoc):**

| Perturbation | Settings re-run | What it answers |
|---|---|---|
| **Drawdown gate (Gate A) 50% → ±5pp** | `pct_off_high` threshold ∈ {**45, 50 (frozen primary), 55**} % | John's "what if 49% not 50%?" — does nudging the crash-depth cut flip the recovery sign? |
| **Low-proximity gate (Gate B) ±5pp** | `pct_above_low` threshold ∈ {**20, 25 (frozen primary), 30**} % | Does where we draw "near the 1-year low" flip the sign? |

For **each** of the {Gate-A ∈ {45,50,55}} × {Gate-B ∈ {20,25,30}} = 9 settings, the events are re-derived (the as_of point-in-time D2 test re-evaluated at the perturbed thresholds), the same §3 accrual/occupancy discipline applies (a perturbed setting that falls below accrual is reported as *thin, sign-only*), and the mean 63td excess sign on the 2025+ confirm is recorded. Gate D (≥252 td history) and the revenue-veto-OFF choice are **not** perturbed (they are structural, not edge-thresholds). The band is run on **both** explore (2015–2020, reported) and, if R-2 reaches grading, the 2025+ confirm (part of the single sealed touch).

**SIGN-STABILITY REQUIREMENT (frozen, verdict-capping):**
- The requirement is **sign stability of the mean 63td excess across the *entire* band** — every setting in the 3×3 grid that meets accrual must show the **same sign** as the frozen-D2 primary. Statistical significance is **not** required at every band setting (perturbed cohorts shrink and small-n bootstraps are coarse); only the **direction** must hold.
- **A sign flip anywhere in the (accrual-meeting) band → the verdict is CAPPED at WEAKENED / NOT-CONFIRMED**, regardless of how significant the headline (frozen-D2) p-value is. A recovery finding that depends on whether the crash cut is 45% vs 55%, or the low-proximity cut is 20% vs 30%, is fragile by John's own standard and is not promotable to CONFIRMED.
- Settings that **fail accrual** (too few perturbed events) are reported as `thin/undetermined` and do **not** by themselves trigger the cap (absence of a sign is not a flip); the cap fires only on an actual sign flip among accrual-meeting settings. The full 3×3 band table (sign / `thin` per cell) is **reported in the verdict** so the cap is auditable. This is a pre-registered branch, not a post-hoc carve-out.

---

## 4. OUTCOME SPEC + HYPOTHESES (frozen before any outcome data)

- **Engine:** event-clock harness v2 (`run_event_study`, `schema_version = 2`). Open-anchored forward returns (Fork A), same-date floor-passing universe median excess (Fork B), terminal-exit delisting handling symmetric on both sides (ADV-03), survivorship counted never dropped (ADV-02).
- **Horizons:** **21 / 63 / 126 trading days** (`V2_HORIZONS_TRADING_DAYS`), bar-counted forward; incomplete cell = `None`, never extrapolated. **63td is the single PRIMARY horizon** ("medium horizon" per the premise); 21td and 126td are pre-registered descriptive consistency reads (not scanned for significance).
- **Excess (long sign):** `excess = pick fwd_return − same-entry_date universe-median fwd_return` at the matched horizon (`fwd_excess_pct`). Positive = the distressed-but-still-filing name beat its same-date floored peers.
- **Costs:** `slippage_bps = 2.0` per leg (long sign), `per_share_rate = 0.0`, `min_per_order = 0.0`, **no borrow** (long-only). Figures reported NET (§7).

**Statistical machinery (frozen):**
- **PRIMARY test = moving-block bootstrap** (`_block_bootstrap_pvalue`, Künsch fixed-length blocks), two-sided H0: mean excess = 0, `n_boot = 999`, `SEED = 20260606`. Block size = harness density estimate (`_block_size_for_horizon`, ≈ horizon / median inter-event-gap), capped at n//2; **if the cap binds (`block_size_capped = True`), the verdict MUST flag it** — a capped bootstrap under-corrects autocorrelation and an iid-labelled-as-block result has reduced inferential value (ADV-10). Given the small confirm n, the small-n iid floor is a live risk and is reported.
- **CROSS-CHECK = Newey-West HAC t-test** (`_nw_ttest_pvalue`, lag from `_compute_nw_lag(entry_dates, forward_days=h)`). If `|p_bootstrap − p_nw| > 0.10` the harness warns and the verdict records the disagreement (event-density / block-size sanity).

**OCCUPANCY PRE-CHECK (encoded before any excess claim).** Before any excess is read in a window, verify the window has a non-empty, completed-horizon event set on **≥ 3 distinct quarterly sub-eras** (explore: the 2015-16 / 2017-18 / 2019-20 default eras; confirm: ≥ 4 distinct 2025+ calendar quarters per §3). A window failing this is **UNTESTABLE** for that hypothesis.

**H1 — PRIMARY (distress-recovery long excess is positive at 63td, on the fresh 2025+ confirm).**
- Prediction: the **mean 63td cohort-relative excess** of R-2 events is **positive** (`> 0`).
- **CONFIRMED bar (ALL required, on the 2025+ confirm window only):**
  1. accrual gate met (§3: ≥ 30 completed-63td confirm events across ≥ 4 quarters) AND occupancy pre-check passes;
  2. explore MDE gate passed (§3: explore 63td excess MDE ≤ 4.0 ppt) — recorded before confirm contact;
  3. confirm-window 63td mean-excess **moving-block-bootstrap CI excludes zero on the positive side** (lower bound > 0) at the per-comparison α (§5);
  4. **per-quarter direction agreement ≥ 0.60** on the confirm window (fraction of 2025+ quarters whose median 63td excess > 0 is ≥ 0.60);
  5. **perturbation sign-stability holds (§3b)** — the confirm mean 63td excess sign is stable across the entire accrual-meeting 3×3 D2-threshold band. A sign flip caps the verdict at WEAKENED / NOT-CONFIRMED (§3b).
- **VERDICT DEFINITIONS:**
  - **CONFIRMED** — all five hold (including §3b perturbation sign-stability).
  - **WEAKENED / NOT-CONFIRMED** — CI touches/crosses zero, or quarter-agreement < 0.60, **or a §3b perturbation-band sign flip occurred** (the cap fires even if the frozen-D2 headline cleared), while the point estimate is non-negative. The recovery premise is not established as a positive long excess; it remains an inverted-intuition, not a measured edge.
  - **REFUTED-AS-STATED** — sign-inverted: mean 63td excess **< 0 with the bootstrap CI excluding zero on the negative side**. The distressed-but-still-filing names *underperformed* their peers — the short framing (D2 as short) is the better-supported reading and the recovery inversion is wrong.
  - **UNTESTABLE / PENDING** — accrual gate unmet (PENDING, wait per §3), or occupancy < the §3/§4 minimum (UNTESTABLE), or explore MDE > 4.0 ppt (UNDERPOWERED — NOT GRADED).

**H2 — ERA-ROBUSTNESS (the sign is not one-quarter luck).**
- The §4 occupancy + the H1.4 per-quarter agreement gate are the H2 mechanism: H1 cannot be CONFIRMED on a single quarter's spike. The confirm verdict **reports the per-2025+-quarter mean-excess breakdown** (`confirm_era_breakdown`, quarters as sub-eras) so the reader sees whether the positive excess is broad or concentrated. (Alpha is drawn on H1; H2 is the within-H1 agreement gate, not a separate draw.)

**H3 — DESCRIPTIVE horizon consistency (NOT gating, NOT alpha-bearing).** Report 21td and 126td mean excess + bootstrap p alongside 63td, as a consistency contrast. Descriptive only.

---

## 5. LEDGER (every comparison counted) + ALPHA BUDGET

**Enumerated comparisons:**
| # | Comparison | Window | Alpha-bearing? |
|---|---|---|---|
| 1 | **H1 — 63td mean excess > 0** (moving-block bootstrap CI excludes zero, positive side) + per-quarter agreement ≥ 0.60 | **2025+ confirm** | **YES — PRIMARY (the only alpha draw)** |
| 2 | H1 at 21td / 126td | 2025+ confirm | No — descriptive horizon consistency |
| 3 | NW HAC cross-check of H1 | 2025+ confirm | No — robustness cross-check (not a second test) |
| 4 | Explore-window H1 (2015–2020) | 2015–2020 explore | No — hypothesis-generating, open |
| 5 | Per-2025+-quarter excess breakdown (H2 mechanism) | 2025+ confirm | No — within-H1 agreement, no separate draw |
| 6 | GROSS-of-cost figure alongside NET | both | No — NET is judged; one decision rule |
| 7 | **CONTAMINATED 2021–2024 diagnostic** (if computed at all) | 2021–2024 | **No — non-evidential by construction (§1); draws no alpha, updates no axiom** |
| 8 | **Perturbation band** (3×3 D2-threshold nudges, §3b) | both | No — sign-stability robustness re-run of H1; draws no alpha (its only effect is the WEAKENED cap on a sign flip) |

**Count: 1 primary alpha-bearing comparison (H1, 63td, confirm).** A single pre-registered hypothesis on a single pre-registered horizon on a single fresh window.

**FDR ledger registration.** The harness registers every horizon's bootstrap p into the append-only **cross-run BH-FDR ledger** (`FDRLedger`, `_append_fdr_ledger` → `backend/data/turnaround/fdr_ledger.json`) at **`fdr_q = 0.10`**, family = **this study's per-horizon hypotheses `{excess_21d, excess_63d, excess_126d}`** within the R-2 study run, AND the cross-run ledger preserves R-2's multiplicity context (study hash, n_boot, block sizes, q, horizons) alongside the prior program experiments so optional-stopping/parameter-shopping across reruns is auditable. The **alpha-bearing decision** for H1 is the §5 per-comparison α below; the FDR ledger is the program-level multiplicity audit, not a substitute for it.

**Program alpha (R-2's draw).** R-2 is a **new experiment** outside the original 4-experiment program budget (REGIME/MOMENTUM/DETERIORATION/EPISTEMICS each spent 0.0125 of the program's 0.05). R-2 draws a **fresh, self-contained α = 0.0125** for its single primary comparison (H1). With one primary comparison, **per-comparison bar α = 0.0125** (two-sided ≈ 98.75% CI; the bootstrap CI on the 63td mean excess must exclude zero at this level). No Bonferroni split is needed (one primary). Decision Gate (program-level) records R-2's draw against the running program ledger; this charter invents no post-hoc correction.

---

## 6. DE-CLUSTERING, SURVIVORSHIP, WINDOWS, F338 ANCHORS, AMENDMENT

**De-clustering (R-2 setting, frozen):** `dedup_same_ticker = True`, `dedup_window_days = 95` (one periodic filing per fiscal quarter → one event; same-ticker filings within 95 calendar days collapse to the chronologically-first). Raw pre-dedup count reported (`events_declustered`). Rationale in §2.

**Survivorship (ADV-02/03, R5 — direction noted):** the universe is currently-listed-only (R5 / F335 bound), but the harness counts missing-from-cache events (`events_no_price_data`) rather than dropping them, and at long horizons a pick or peer whose series ends inside the horizon contributes its **terminal** close symmetrically on both sides of the excess (ADV-03). **Direction of residual bias for a LONG distress-recovery premise (acknowledged):** survivor-only truncation removes names that delisted — and a crashed name that delists is the *worst* outcome of the distress cohort. Their absence biases the measured recovery excess **upward** (the cohort looks healthier than the true population that included the dead). This is the **adverse** direction for H1 — it could manufacture a false-positive recovery. Therefore: (a) the verdict cites the F335 delisting-intensity-above-$5 figure for the D2 cohort; (b) the `survivorship_warning` (no-price-data fraction > 10%) MUST be checked and, if it fires, the verdict is marked **SUSPECT** regardless of the bootstrap result; (c) per R7, if a passing confirm verdict flips under F335 worst-case phantom-delisting injection, the paid-data trigger fires. This survivorship direction is the single biggest threat to H1 and is named here so it cannot be quietly omitted at verdict time.

**Windows:** explore **2015–2020** (open); confirm **entry_date ≥ 2025-01-01** (fresh, graded once per §3); **2021–2024 excluded as contaminated** (§1).

**F338 FACE-VALIDITY ANCHORS (checked on the real-data smoke probe BEFORE any interpretation):**
1. **D2-state sanity:** spot-check 2–3 events — each was genuinely ≥ 50% off its trailing-252td high AND ≤ 25% above its trailing-252td low at the as_of (last trading day before acceptance), with ≥ 252 td of history. The `pct_off_high`/`pct_above_low` carried on the payload match a hand recomputation.
2. **Filing-event sanity:** each event's `form` ∈ {10-Q, 10-K}, its `acceptanceDateTime` (or filingDate fallback) parses, and `entry_date` is the next trading OPEN strictly after the ET acceptance date (no same-day entry; `same_day_entry = False`).
3. **Point-in-time leak check:** the D2 state's `as_of` is **strictly before** the filing's acceptance date for 2–3 names (the state did not peek at the filing it is conditioned on; no forward bar informed the state).
4. **Window-partition anchor:** confirm the events table tags 2015–2020 as explore, 2021–2024 as the **excluded-contaminated** band (present for survivorship counting, flagged non-evidential), and 2025+ as the gradeable confirm; the grading filter (`entry_date ≥ 2025-01-01`) is exercised on the probe.
5. **Excess sign anchor:** a known same-cohort recoverer shows positive 63td cohort-relative excess; a known continued-faller negative — the long-sign excess convention is wired correctly.
6. **Floor anchor:** a known sub-$5 or split-corrupt name at a probe as_of is excluded from BOTH the pick set and the universe median (counted `below_floor`/`corrupt_frame`), confirming identical floor enforcement.
7. **Accrual anchor:** the count of confirm events with a **completed** (non-terminal) 63td horizon is printed and checked against the §3 N_min = 30 / ≥ 4-quarters gate — so the wait-vs-grade decision is made on a verified number, not an assumption.

**SEED = 20260606** (all bootstrap and NW draws, explore and confirm).

**Amendment rule:** this charter is **FROZEN ON WRITE** (sha-pinned at approval). Any change after outcome contact — to the event definition (the D2-state test, the on-time-filing form set, the entry lag), the windows (especially the 2025+ confirm floor or the 2021–2024 exclusion), N_min / accrual gate, the MDE abort bar (4.0 ppt), the horizons, the de-clustering window, the cost/outcome spec, the bootstrap seed, the hypotheses, **the §3b perturbation band (its members or the sign-stability cap)**, or the alpha allocation — constitutes a **NEW experiment** with its own charter and its own alpha draw; it does not amend this one. Pre-outcome implementation-mechanics fixes (a bug caught before any result is read) are permitted and logged, provided no outcome data has been consulted. The §3 accrual/occupancy/MDE branches, the §3b perturbation band, and the §1 contamination-exclusion rule are **pre-registered branches, NOT amendments**. **The deferred R-2b continuous-dose variant (§1 NOTE) is explicitly OUT OF SCOPE** — it is a separate future experiment with its own ID/charter/alpha/sha, never an amendment of R-2. **This v2 revision itself is a legal pre-outcome amendment** of the v1 DRAFT (no result was ever read for R-2; v1 was never run): it keeps the D2 state frozen and only *adds* the §3b band, the §1 R-2b note, and the matching verdict/grading wiring.

---

## 7. COST MODEL + 8. BLIND CONFIRM GRADING + ABORT CRITERIA + VERDICT DEFINITIONS

**§7 Cost model (frozen):** `slippage_bps = 2.0` per leg (unsigned modeled drag, applied directionally per `backend/slippage.py`), `per_share_rate = 0.0`, `min_per_order = 0.0` (commission-free, matches Alpaca US equities), **no borrow leg** (long-only — there is no short side in R-2). Round-trip drag ≈ 4 bps ≈ 0.04 ppt. All figures judged **NET** of this; GROSS reported alongside descriptively. The 4.0 ppt MDE abort bar (§3) is derived from this cost model + the ~$10k account context.

**§8 Blind confirm grading by a FRESH agent (binding protocol).** The agent that opens and grades the 2025+ confirm window MUST be a **fresh agent that has not read the explore-window outcomes** (explore-blind), exactly as the prior four program experiments were judged. Its mandate, in order:
1. Read THIS frozen charter (verify its sha matches the approved pin) — and nothing else from prior R-2 result artifacts.
2. Confirm the §3 **accrual gate** is met on real cached data (≥ 30 completed-63td confirm events across ≥ 4 distinct 2025+ quarters). If not → record **PENDING — INSUFFICIENT FRESH EVENTS**, grade nothing, stop.
3. Confirm the explore **MDE gate** was recorded and passed (explore 63td excess MDE ≤ 4.0 ppt) BEFORE any confirm contact. If MDE > 4.0 ppt → **UNDERPOWERED — NOT GRADED**, stop.
4. Apply the §1 **contamination filter** (`entry_date ≥ 2025-01-01`; discard 2021–2024) before reading any confirm number.
5. Run the F338 face-validity anchors (§6) and read the artifact before interpreting it.
6. Evaluate H1 against its five-part CONFIRMED bar (§4) at α = 0.0125 (§5), run the §3b perturbation band on the confirm window and apply the sign-stability cap, check the survivorship `SUSPECT` flag (§6), and emit a verdict doc + the per-quarter breakdown + the 3×3 perturbation-band sign table.

**ABORT / STAND-DOWN criteria (any one halts grading, frozen):**
- Explore 63td excess **MDE > 4.0 ppt** → UNDERPOWERED — NOT GRADED (test cannot resolve an economically-meaningful edge).
- **Accrual gate unmet** (< 30 completed-63td confirm events, or < 4 contributing quarters) → PENDING — wait, re-check on a later cache refresh; do NOT grade thin.
- **Occupancy** < 3 populated sub-eras in the relevant window → UNTESTABLE for that window.
- **Survivorship warning fires** (no-price-data fraction > 10%) → verdict marked SUSPECT regardless of the bootstrap result; a CONFIRMED that does not survive F335 worst-case phantom-delisting injection triggers the R7 paid-data path and is NOT promoted.
- **Bootstrap block-size cap binds** (`block_size_capped = True`) at 63td → flagged in the verdict as reduced inferential value (iid-labelled-as-block); a CONFIRMED resting on a capped bootstrap is reported with that caveat foregrounded.
- **Perturbation-band sign flip** (§3b) among accrual-meeting D2-threshold settings → caps the verdict at WEAKENED / NOT-CONFIRMED regardless of the frozen-D2 headline p-value.

**VERDICT DEFINITIONS (restated, single source):**
- **CONFIRMED** — accrual + occupancy + MDE gates pass; 2025+ 63td mean-excess bootstrap CI lower bound > 0 at α = 0.0125; per-quarter agreement ≥ 0.60; **§3b perturbation sign-stability holds**; not SUSPECT. The distress-recovery long premise is a **measured** positive medium-horizon excess on virgin 2025+ data, stable to small D2-threshold nudges.
- **WEAKENED / NOT-CONFIRMED** — gates pass but CI touches/crosses zero, or quarter-agreement < 0.60, **or a §3b perturbation-band sign flip occurred**, point estimate non-negative. Inversion not established as a measured (or edge-stable) edge.
- **REFUTED-AS-STATED** — 63td mean excess < 0 with CI excluding zero on the negative side. The distressed-but-still-filing names underperformed peers; the original short framing is the better-supported reading.
- **PENDING** — accrual gate unmet; wait.
- **UNTESTABLE** — occupancy insufficient.
- **UNDERPOWERED — NOT GRADED** — explore MDE > 4.0 ppt.
- **SUSPECT** — any verdict above co-tagged when the survivorship warning fires; not promoted without the F335 stress survival.

No verdict row is selected until the confirm number is read; the definitions are frozen here so the row cannot be chosen to suit the number.

---

## PLAIN-ENGLISH SUMMARY (for a non-expert; every term defined inline)

**What we are testing.** Earlier, the lab found a group of stocks it called "deteriorating": stocks that had **crashed at least 50% from their highest price in the past year**, were **sitting within 25% of their lowest price in that year**, were still **liquid enough to trade** (above a price/volume floor), and were **still filing their required quarterly reports to the SEC on time** (a 10-Q is the quarterly report; a 10-K is the annual one). The lab originally bet these would keep falling — it treated them as **short** candidates (a short = betting the price goes *down*). The new idea here is the opposite: maybe a beaten-down company that *keeps filing its reports on schedule* is quietly signalling "we're still alive and operating," and the price overshot on the way down — so it might **recover**. So R-2 buys them (**long** = betting the price goes *up*) and asks: over the next ~3 months, do these stocks beat comparable stocks?

**What counts as a "trade" (an event).** The moment a beaten-down company *files one of those on-time quarterly reports* is the event. We buy at the next morning's opening price after that report becomes public. We then measure how the stock does versus its peers over 21, 63, and 126 trading days (roughly 1, 3, and 6 months). The headline number is the **63-day (≈3-month)** result — "medium horizon."

**How we judge it.** For each trade we compute **excess return** = how much the stock beat (or trailed) the *typical* tradeable stock on the same day. We add up the excesses and ask, with a statistical resampling method (a "bootstrap" — it shuffles the data thousands of times to see if the average could just be luck), whether the average beat is **reliably above zero**, not a fluke. We also require the result to be **positive in most calendar quarters**, not driven by one lucky stretch.

**The wiggle test (why a 50% cutoff doesn't make-or-break the answer).** John pointed out that a hard line — "crashed at least *50%*" — is arbitrary at the edge: why not 49%, why not 55%? So before we trust a result, we are *required* to re-run the whole thing with the crash cut nudged to **45% and 55%**, and the "near the low" cut nudged to **20% and 30%** — nine combinations in all — and check that the answer **keeps the same direction** every time (we don't demand it stay statistically strong at every nudge, just that it doesn't *flip* from "they recover" to "they keep falling"). If a one-step nudge flips the sign, the finding is fragile and we downgrade it to "not confirmed" no matter how good the headline number looked. (This is a robustness check on the *existing* yes/no screen. A deeper redesign — treating "how distressed" as a sliding *dose* and asking whether recovery grows smoothly with distress depth — is John's bigger idea, and we've written it down as a **separate future experiment, R-2b**, kept out of this one on purpose so the two don't get tangled. R-2 tests the specific historical group exactly as it was originally defined; R-2b would test the dose idea, later, on its own terms.)

**Why 2021–2024 is forbidden — the most important rule here.** A fair test of an idea has to be judged on data the idea *had never seen* when it was dreamed up. This particular idea — "flip the short bet into a long bet" — was **invented by looking at how these stocks behaved in 2021–2024.** That data already shaped the idea. Grading the idea on the *same* 2021–2024 data would be like writing exam questions by peeking at the answer key and then congratulating yourself for getting them right — it proves nothing. So **2021–2024 is banned as evidence for this idea, in either direction**: we may not use it to support the idea *or* to attack it. Instead:
- The **real test** uses **2025 onward** — data the whole research program was deliberately *locked out of* until now (the software literally refuses to let earlier experiments peek at 2025+), which keeps it genuinely "unseen" and fair. We grade it **once**, and only after enough 2025+ trades have had time to play out (at least **30 trades** spread over at least **4 different quarters**, each with its full 3-month window finished). If there aren't enough yet, we **wait** — we do not grade a thin, unreliable sample.
- We're allowed to **practice and sanity-check** on **2015–2020** (older data, never used to grade this idea), but nothing there counts as proof.

**The safety check before we even grade.** Tiny edges aren't worth it for a ~$10,000 account, because trading costs (about 0.04% per round trip here) and the risk of buying crashed stocks eat small gains. So before grading, we check whether the test can even *detect* an edge bigger than **4 percentage points** over 3 months. If the test is too weak to see an edge that size, we declare it **underpowered and don't grade it** — grading a test that can't resolve a meaningful effect would be theatre.

**The honest catch we're flagging up front.** Crashed companies sometimes go bust and vanish from the data. Our stock list only contains companies that survived, so the *worst* outcomes (the ones that died) are missing — which could make the recovery idea look *better* than it really is. We name this risk explicitly, watch a "too many missing names" warning, and if a positive result doesn't survive a worst-case "what if the dead names were here" stress test, we don't trust it.

**The possible verdicts.** **CONFIRMED** (distressed-but-still-filing stocks really did recover, beating peers reliably over 3 months on fresh 2025+ data); **WEAKENED / NOT-CONFIRMED** (no reliable beat — the idea isn't established); **REFUTED-AS-STATED** (they actually *under*performed — the original short bet was the right read); or **PENDING / UNTESTABLE / UNDERPOWERED** (not enough fresh 2025+ data yet, too few quarters, or the test can't resolve a meaningful edge — so we wait or stand down rather than force an answer).

---

*End of charter. DRAFT — AWAITING JOHN'S APPROVAL.*

---

**sha256 fingerprint.** The v1 self-referential body-hash footer is retired in v2. The **full-file sha256** of this revised v2 DRAFT is recorded in the revision author's return (computed with `shasum -a 256` over the entire file) and is what John re-pins as the frozen-on-write hash on approval. Recompute to verify: `shasum -a 256 docs/plans/2026-06-06-R2-distress-recovery-charter-DRAFT.md`.*
