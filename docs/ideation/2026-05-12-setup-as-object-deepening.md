---
date: 2026-05-12
topic: setup-as-first-class-object
parent: 2026-05-12-discovery-page-ideation.md (survivor #9)
mode: repo-grounded
status: deepening — pre-spec
---

# Setup-as-First-Class-Object — Deepening

Six parallel agents took the seed idea from the Discovery ideation and pushed on it from different angles: data-model, identity/versioning, consumer inventory, adversarial, cross-domain prior art, behavioral fingerprint deep-dive. They disagree on important things. That disagreement is the point — this doc captures the live debate before any commitment is made.

The doc is structured to graduate into a spec: §1 frames the central tension, §2-7 are the six takes, §8 synthesizes a recommended v0 wedge that all six positions are compatible with, §9 lists the open questions to resolve before spec.

---

## 1. The Central Tension

The deepest disagreement among the agents is **not about schema** — it's about whether to build the object at all.

- **Adversarial agent** argues: ship a 10-line `setup_hash(rule_json)` helper. Add one nullable `setup_hash` column to journal rows. Done. Three of the six downstream Discovery ideas are unblocked by the hash alone. The full Setup object is engineer-fantasy infrastructure for features that don't exist, on a codebase that has spent F119/F122/F125/F128/F141/F143 tightening contracts — inserting a new primary key under all of it now will produce F-items in the "Key Bugs Fixed" section a year from now.

- **Data-model + consumer agents** argue: the cache + journal payoff alone justifies the object. The Backtester's optimizer (C22) and Monte Carlo routes already pay wall-clock pain that Setup-hash result-caching would solve. The journal's `bot_id`-scoped PnL aggregation is *itself* a workaround for the missing Setup primary key — and produced one of the Key Bugs Fixed entries already.

- **Identity agent** sits between them: agrees with the object, but is more restrictive than the data-model agent about what goes *in* the body — explicitly excludes position_size, allocated_capital, slippage_bps, broker, data_source. Setup is signal-generation identity; everything else is deployment.

The disagreement is real and recommends a graduated path, not a binary "ship or don't." See §8.

---

## 2. Take — Data Model & Schema

**Recommendation: Pointer Setup.**

Setup is a body keyed by `content_hash`, with a separate stable `setup_id` UUID for per-user references. The same canonical body across users dedupes to one definition row — required for the downstream "Wardley clock" / anonymous fingerprint pool.

```python
class Setup:
    setup_id: str            # uuid v7, stable, per-user surrogate
    content_hash: str        # blake2b-128 of canonical-JSON(signal+exits+regime+universe+interval)
    name: str                # mutable, NOT in hash
    description: str         # mutable, NOT in hash
    universe: SetupUniverse  # discriminated union: symbol|basket|screen
    interval: str            # "1d","15m"… IS identity
    signal: SetupSignal      # buy_rules+sell_rules+buy_logic+sell_logic
    exits:  SetupExits       # stop_loss, trailing, max_bars, dynamic_sizing
    regime: SetupRegime      # direction + optional regime gate
    schema_version: int = 1
    parent_setup_id: str | None
    tags: list[str]          # mutable
```

**Excluded from Setup body** (live in `RunContext` / `Deployment`): `start`/`end`, `lookback_days`, `initial_capital`, `position_size`, `source`, `extended_hours`, `broker`, `allocated_capital`, `pnl_epoch`, `slippage_bps`, `commission`, `borrow_rate_annual`. The slippage/cost-model defaults explicitly stay *out* — baking them in destroys cross-time comparability as the slippage model evolves.

**Persistence: SQLite at `data/strategylab.sqlite`.** Tables: `setup_definitions(content_hash PK, body_json, schema_version)`, `setups(setup_id PK, content_hash FK, name, tags, parent, ts)`, `setup_runs(run_id, setup_id, run_context, summary, fingerprint_blob)`, `setup_stats(setup_id PK, last_run_at, run_count, best_sharpe)`. The existing `bots.json` / `journal.json` files reach end-of-life under this — write-through during migration, then authoritative SQLite.

**Forever decisions** (must get right in v0): what fields participate in `content_hash`; whether muted rules count (no — drop before hashing); `SetupUniverse` as a discriminated union (ship the shape even if only `symbol` is populated); `interval` as identity not run-context; *not* putting `direction` on Setup when `regime.enabled` (a regime-gated Setup carries both directions).

**Smallest first PR** (fully reversible, no UX change): add SQLite tables + `to_setup()` / `from_setup()` adapters on `StrategyRequest` and `BotConfig` behind a `SETUPS_ENABLED` flag; add nullable `setup_id` column to journal rows; ship `GET /api/setups/by-hash/{hash}` to verify plumbing. No call site changes. No UI.

---

## 3. Take — Identity, Versioning, Hashing

**Recommendation: UUID v7 + blake2b-128, mutate-while-draft / fork-on-freeze, drop muted rules before hashing, property-based fuzz tests on canonicalization.**

Key calls:

- **RSI<30 vs RSI<31 → different.** Threshold is content. No tolerance-based "close enough" hashing.
- **Rule order swapped → same.** Canonical form sorts the rule list by deterministic 7-tuple `(indicator, condition, param, value, threshold, negated, json.dumps(params))`.
- **Same rules, different position_size → same Setup, different deployment.** Disagrees with the data-model agent here — argues position_size lives outside Setup. The downstream "anonymous similar-backtest leaderboard" only works if two users running the same logic at different sizes pool together.
- **`negated=true` on `RSI>70` vs native `RSI<=70` → different in v0.** Canonicalizing negation requires an inverse table for ~10 conditions including the asymmetric `turns_*` ones; the cost of getting that table wrong is silently merging non-equivalent strategies. Hard pass.
- **Cross-user equivalence → yes.** Same `content_hash` across users by construction. This is load-bearing for the Wardley clock and anonymous tile.

**Mutation rule:** a Setup is frozen the moment a backtest row, bot, or scan result references it. Pre-freeze, edits mutate in place (UI says "draft"). Post-freeze, any edit forks (new UUID, `parent_id` pointer, recompute content_hash). No `version: int` column — `len(ancestor_chain)` is the version.

**The trap to prevent (dbt false-different / Hibernate false-same patterns):** a property-based test (`hypothesis`) that asserts `content_hash(setup) == content_hash(canonicalize(setup))` AND `content_hash(setup) != content_hash(mutate_field(setup, f))` for every field in the canonicalization-input subset. CI fails if you add a field to Setup without adding a row to the fuzz test.

---

## 4. Take — Consumer Inventory

**Highest-leverage consumers, in shipping order after the substrate lands:**

1. **Backtester result cache** (HIGH) — pure infra→infra payoff, but the optimizer (C22, thousands of runs/sweep) and Monte Carlo are *existing* pain points. Vindicates the infra by making something the user already does dramatically faster.
2. **Journal / Performance per-Setup aggregation** (HIGH) — `compute_realized_pnl`'s `bot_id` scoping is already a workaround for the missing Setup join. Replace with `setup_id` scoping. Solves a class of bugs that has *already* shipped (the P&L leak in Key Bugs Fixed).
3. **Strategy Builder Save-as-Setup + version chain** (HIGH) — without this surface, no Setup is ever created. Replaces `savedStrategies.ts` (localStorage) with server persistence.
4. **Anti-Discovery banner ("you tried this before")** (HIGH) — the single feature that *only* exists with Setup. Cheap (one hash check in Builder), maximally legible as "look what this infra enables."
5. **Falconer's Mews** (HIGH) — the consolidated home for Setups; highest *visible* payoff per LOC because it's a list view over the new entity.

**Deliberately ranked out of the first wave:** Phantom Portfolio, Mixtape, Council, Necropsy — high leverage but each requires *additional* infra (paper-bot scheduler, fingerprint k-NN, persona scoring, journal annotations). Save for after the substrate has proved itself. Bot Manager refactor to `setup_id` is HIGH leverage but HIGH risk (live trading code, Key Bugs Fixed scars) — sequence it *after* the read-path consumers have validated the schema.

**Non-obvious future surfaces** unlocked: Setup share-as-URL, Setup diff viewer (git-style version diffs), Setup regression test-suite (pinned Sharpe on fixed data), Setup ancestry tree, fingerprint search bar (paste an equity curve → find nearest Setups).

---

## 5. Take — Adversarial

**Counter-recommendation: ship a hash helper, not the object.**

```python
# backend/setup_hash.py
import hashlib, json
def setup_hash(rule_set: dict) -> str:
    return hashlib.sha256(
        json.dumps(rule_set, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
```

Plus one nullable `setup_hash` column on journal rows, populated at `_log_trade()`. Zero migration risk (nullable, additive). Unblocks "more like this," "you ran this before," Phantom Portfolio joins. **~5% of the cost, ~80% of the value.**

**The "six ideas pay off" itemization:**
- Regime-aware Discovery — `regime` is already on `StrategyRequest`. No Setup needed.
- Anti-Discovery — `SELECT * FROM journal WHERE symbol=? AND setup_hash=?`. 10 lines.
- Phantom Portfolio — needs a stable strategy ID. `setup_hash` again. 10 lines.
- Falconer's Mews — these are *already* saved strategies in the UI. Renaming them is cosmetic.
- Council of Personas — personas score a request payload. Doesn't care if it's persisted.
- Necropsy Drawer — needs a journal annotation, not a Setup row.

**Score: 3 of 6 solved by hash helper alone. 2 are cosmetic. 1 (Wardley) is the furthest off.**

**The fingerprint claim is probably overblown.** What dominates the variance in the behavioral vector is *symbol volatility* (TSLA vs KO drawdown shapes are wildly different irrespective of strategy). k-NN over this vector will recommend "more like this" = "more strategies on similar-vol symbols," which is a sector lookup with extra steps. Until n ≈ 10⁴, faiss/hnswlib pays maintenance cost for zero algorithmic benefit. (The fingerprint agent agrees on the index choice — see §7.)

**Three thresholds that would make Setup-as-object genuinely necessary:**
1. Sharing/marketplace ships (needs stable URL-able IDs + provenance).
2. N saved strategies > ~200 or `bots.json` > ~10 MB (hash-grouping query becomes slow).
3. Multi-user (identity stops being optional).

**None of these are on the roadmap.** Until one is, the hash helper wins.

---

## 6. Take — Cross-Domain Prior Art

Seven principles distilled from dbt, Kubernetes, Terraform, GitHub Actions, Nix, Figma, Spotify, Quantopian, Docker, Looker:

1. **Identity stable, content mutable.** Terraform suffered for years because address (`aws_db_instance.old_name`) *was* identity — renames triggered destroy+create on production databases until the `moved` block shipped in 2021. *Implication:* `setup_id` (UUID) is forever; `name`, `description`, `tags`, even rules are mutable on the stable record.

2. **Spec/status separation is load-bearing.** Kubernetes hard-enforces it; the user owns `spec`, controllers write `status`, they never merge. *Implication:* Setup's `config` (declared) is strictly separate from `observed` (backtest results, fingerprints, regime fit). Never write observed back into config. The diff is the product.

3. **Hash for cache-busting; name for identity.** dbt's `unique_id` is the FQN; the checksum is a cache signal. Conflating them is fatal. *Implication:* `setup_id` is identity; `content_hash` is dedup/cache. Never make the hash the primary key.

4. **Escape hatches accumulate into landmines.** Figma's "detach from component" was meant for edge cases; Salesforce documented 3,000+ detachments in a year on one button. *Implication:* if you allow "clone and diverge" on Setup (inevitable), build lineage observability from day one — surface how many bots run each variant, warn before archiving a parent.

5. **Hash granularity must match invalidation semantics.** Nix learned this the hard way; Docker layer-level hashing is the right granularity, not file-level or image-level. *Implication:* hash the *behavioral spec* (signal + exits + regime + universe + interval). A rename must not invalidate the fingerprint index. A rule change must.

6. **Rule-based vs explicit-list is a type distinction, not a mode.** Spotify separates rule-generated playlists from manual playlists at the type level. *Implication:* a parametric Setup ("EMA crossover with searched periods") is a *different object type* from a fixed-value Setup. The parametric Setup is a generator; the fixed Setup is an instance. Track this from the start — retrofitting is expensive.

7. **The rename problem always arrives late.** Terraform took 5+ years to ship the `moved` block. dbt still has no equivalent. LookML breaks downstream reports silently on field rename. *Implication:* implement a `previous_ids` / `aliases` array on Setup at v1, even if always empty. Record lineage explicitly.

**Cautionary tale that matters most for StrategyLab — Quantopian:** modeled trading algorithms as first-class submitted objects but prioritized IP protection over inspectability. Result: the platform could only evaluate by output metrics, no insight into mechanism. Crowdsourced overfitted noise. Closed October 2020. *Inverse implication for Setup:* the object is only worth it if it carries enough semantic content (rules + cost context + regime + fingerprint) that *why* a Setup performs can be examined. An opaque Setup is the Quantopian mistake at smaller scale.

---

## 7. Take — Behavioral Fingerprint

**Recommendation: 84-dim named vector across 9 blocks. Numpy linear scan, no ANN library. Self-clustering, not k-NN, is the killer use case.**

```
v[0:7]     entry_hour_hist (7 ET buckets, rate-normalized)
v[7:12]    entry_dow_hist
v[12:18]   hold_bars_hist (6 log2 bins)
v[18:24]   hold_calendar_days
v[24:30]   trade_pnl_pct_hist
v[30:35]   drawdown_shape
v[35:38]   exposure (time-in-market, long/short share)
v[38:43]   inter-trade clustering
v[43:48]   regime_overlap
v[48:54]   cost_signature (slippage_bps, p90, commission share, borrow share, modeled-vs-realized, data-present flag)
v[54:60]   exit_reason_mix
v[60:66]   trade_outcome_shape (win-rate, payoff, profit-factor, kelly, skew)
v[66:72]   intra-trade dynamics (MAE, MFE, time-to-peak)
v[72:78]   monthly_consistency
v[78:82]   structural_meta (n_rules, has_regime_filter, has_trailing)
v[82:84]   bar_horizon (interval, lookback)
```

**Normalization: rate-based features primary, p99-clipping + z-score for unbounded scalars. Duration almost invariant by design.**

**Distance metric: cosine, split 70/30 between shape-block (v[0:48]) and outcome-block (v[48:84]).** Prevents cost-signature dominance when a user has 40 RSI variants on the same symbol.

**Index: pure numpy linear scan.** 1k vectors × 84 dims × float32 = 336 KB; `corpus @ query` is ~30 µs. faiss/hnswlib don't pay until ~50k vectors. Single-user app with O(100-1000) backtests/lifetime — linear is correct for years. Same `knn(query) → indices` interface; swap behind it later if needed.

**What the metric will and won't tell you:**
- ✅ Two RSI mean-reversion strategies on different symbols → cluster (the win)
- ✅ EMA-cross and MACD-cross → cluster (correctly identified as "same family")
- ❌ Profitability — Sharpe 1.5 and Sharpe 0.3 cluster if shapes match. By design.
- ❌ Overfit — needs walk-forward signal.
- ❌ Will-work-live — orthogonal.

The honest pitch: **the metric finds strategies that take the same trades at the same times under the same regimes. It's a "what does this do" detector, not a "is this good" detector.**

**The killer use case is self-clustering, not k-NN.** Agglomerative clustering over the user's own corpus, rerun weekly, producing 3-6 named clusters with auto-labels ("morning RSI reversion family, n=12, median Sharpe 0.9", "EOD momentum family, n=18, median Sharpe 1.3"). Exposes obsessions ("18 variants of the same EMA cross"), gaps ("zero overnight-hold strategies"), and dead branches ("this cluster topped out 3 months ago").

**v0 ship — 1 PR, ~1-2 weeks: a "Behavior Radar" widget on the backtest results page.** Compute the 84-vec on backtest completion, store in `data/fingerprints.json` keyed by setup_hash, render a 6-axis radar (collapsed blocks) overlaid against corpus median. Explicitly out of v0: k-NN UI, clustering, sharing, ANN library.

**Failure-mode guards:** `n_trades < 3` → refuse to compute. `n_trades < 10` → confidence=low. No slippage data → downweight cost block to 0.1. Daily-bar backtest → entry_hour gets uniform sentinel + bar_horizon flag distinguishes.

---

## 8. Recommended v0 Wedge (compatible with all six takes)

The adversarial and pro-object camps are *not* incompatible if the sequencing is right. Here is a phased path that **starts with the adversarial recommendation, graduates only on triggered evidence:**

### Phase 1 — Hash Helper Only (1 PR, 2-3 days)
- Add `backend/setup_hash.py` with the 10-line canonicalization helper (apply the identity agent's canonicalization rules — sort rule list, drop muted, normalize float repr, blake2b-128).
- Add nullable `setup_hash: str | None` column to journal rows, populated at `_log_trade()`.
- Property-based fuzz test on canonicalization (the identity agent's tripwire).
- **Ship as much as possible behind this alone.** Specifically: "you tried this before" check in Strategy Builder, Phantom Portfolio's stable strategy ID, journal grouping by setup_hash.

### Phase 2 — Behavioral Fingerprint Radar (1-2 weeks)
- Implement `backend/fingerprint.py` per §7 — the 84-dim vector.
- Store in `data/fingerprints.json` keyed by `setup_hash` (NOT by Setup row — no Setup row yet).
- "Behavior Radar" widget on backtest results page. Single screen. Single user value prop: "what does my strategy actually do?"
- **No k-NN UI yet.** The radar against corpus median is enough at n=1.

### Phase 3 — Decision Point (after phases 1+2 ship and accrue ~1 month of usage)

**Trigger evaluation:** has any of the following happened?
- (a) The hash-helper grouping query has become slow (n > 200 saved strategies or `bots.json` > 10 MB).
- (b) A sharing/URL feature has been requested or planned.
- (c) Multi-user is on the roadmap.
- (d) The journal `bot_id` workaround has caused a second bug in the same class as the original P&L leak.
- (e) The optimizer/Monte Carlo wall-clock pain has become user-visible and result-caching by hash is the cleanest fix.

**If any trigger fires → graduate to Setup-as-object** per the data-model + identity agents' design. The migration is straightforward because phase 1 already populates `setup_hash` everywhere — the new SQLite `setup_definitions` table is a "promote the hash to a real row" backfill.

**If no trigger fires after 1 month → the adversarial agent was right.** The hash helper is sufficient; Setup-as-object remains deferred indefinitely.

### Phase 4 (conditional) — Setup-as-object proper
Only if phase 3 triggered. Ship the data-model agent's smallest-first-PR: SQLite tables + `to_setup()` / `from_setup()` adapters + `GET /api/setups/by-hash/{hash}` + journal `setup_id` column (nullable, additive to existing `setup_hash`). Then sequence consumers per §4's payoff ranking — cache, journal aggregation, Builder Save, Anti-Discovery, Mews.

---

## 9. Open Questions Before Spec

If this graduates to a spec, the following are unresolved and need decisions:

1. **Position size / cost-model fields — in or out of Setup body?** Data-model agent says in (Fat Setup). Identity agent says out (deployment). **Pick before phase 4.** Resolution probably hinges on whether two users with the same logic at different sizes should hash to the same `content_hash` — yes argues out, no argues in.
2. **Slippage policy on Setup vs RunContext.** Currently `decide_modeled_bps` learns from fills; baking the result of that learning into Setup destroys cross-time comparability. Strong default: out. But this needs explicit acknowledgment.
3. **Muted rules: in or out of hash?** Identity agent says drop them. Surfaces a UX edge case: muting a rule changes Setup identity. Pick a stance and write it in a comment next to the hash function.
4. **`direction` field placement when `regime.enabled` is true.** A regime-gated Setup contains both directions; identity agent flags this as a v0 trap.
5. **SQLite or stay on JSON files for one more cycle?** Phase 4 introduces SQLite. Is the rest of the app ready for that infra shift, or should the SQLite migration be a separate, fully-considered move?
6. **Fingerprint vector — what to do for backtests with `n_trades < 10`?** Refuse, or compute-with-low-confidence? Affects every downstream consumer.
7. **Cross-user content_hash pooling — yes from day one, or guarded by feature flag?** Pooling has zero cost in single-user world but is a forward commitment to a multi-user architecture.

---

## 10. Status

This doc is **deepening, pre-spec.** It captures six distinct architectural takes and the synthesis path that reconciles them. The recommended next steps are:

- If proceeding to implementation: open phase 1 (hash helper) as its own TODO item under Section E. It's a 2-3 day move with no migration risk and is reversible.
- If proceeding to a full spec: this doc is the input. Run `ce:plan` or `ce:brainstorm` with §8 (the v0 wedge) as the seed, and resolve the seven open questions in §9 explicitly before writing the spec.
- If pausing: the doc is durable. The phase 1 hash helper is independently shippable whenever — it's the safest move regardless of whether Setup-as-object ever lands.

---

## 11. My Recommendation

Three user-value subagents and two reviewers later, here is what I actually think — with the bits the reviewers caught me on stripped out.

### The single move

**Ship Phase 1 of §8 plus one new Builder banner. In a single PR. Stop there until evidence accumulates.**

Concretely: the hash helper (per the identity agent's canonicalization rules, with the property-based fuzz test), the nullable `thesis_hash` column on journal rows, and a single banner in the Strategy Builder that fires *when the user clicks Save or Run, not while they're typing* — checking whether the hash matches a prior backtest and surfacing one line: *"You ran this on DATE — Sharpe X over N trades. Re-run?"* Dismissable. Per-hash dismiss state stored locally so it doesn't reappear after dismissal.

That's the move. Everything else in this doc — Ghost Mews, Necropsy, behavioral fingerprint, SQLite migration, Setup-as-object proper — is conditional on this landing well.

### Why this specifically

Phase 1 of §8 (hash helper alone, no UI) is the adversarial agent's recommendation. It's correct on engineering risk but ships nothing the user can see, which means the substrate accrues without earning the right to graduate. The banner is the smallest possible user-visible moment that gives Phase 1 a verdict: either "this saved me from a re-run" gets said in JOURNAL.md within a month, or it doesn't, and then we know Phase 1 wasn't pulling its weight and the rest of this doc is dead.

The banner is also a real test of the harder question: does the user actually want the platform to have memory *for them*, or do they want to keep memory in their own surfaces (JOURNAL.md, MEMORY.md, TODO.md) and let the platform stay stateless? Reviewer 1 caught me projecting on this — the user already solved continuity outside the platform. The banner is the cheapest experiment that produces real data on whether platform-side memory is welcome or redundant.

### What I'm probably wrong about

**The "continuity-of-self" claim in earlier drafts was projection.** The user runs JOURNAL.md, MEMORY.md, TODO.md, and a Slack overnight summary already. They've deliberately built their continuity infrastructure *outside* the trading platform. The strongest version of this recommendation accepts that platform-side memory might be redundant with what they already have, and that the banner is a test — not an assertion.

If after a month of Phase 1 the banner has fired a handful of times and the user shrugged each time, the right move is to back out, not graduate to Phase 4. The whole §8 phased plan and §11.x ambition is null and void in that case. I'd want to be told that explicitly.

### The naming sidebar (demoted)

The strategic-framing agent argued for renaming Setup → **Thesis**. I think they're right on substance and reviewer 2 is right that it's not a strategic pillar. Verdict: pick the name *before* the first column lands, because renaming after is expensive, but stop pretending the choice changes architecture. My preference is Thesis for the reasons the strategic agent gave (testable claim about the world, separates spec from evidence cleanly, no collision with broker/folk vocabulary). But this is a small call worth 30 seconds, not a top-level commitment.

### A typical week with Phase 1 live

Four small scenes that show what shipping the recommendation actually looks like from the user's seat. No invented numbers — only behaviors the banner can actually produce from a hash check against the journal.

**Scene 1 — Sunday evening, the small mirror.** The user opens the Strategy Builder, dials in an EMA crossover on QQQ they "have a feeling about." Hits **Run Backtest**. A grey banner appears under the Run button: *"You ran this rule set on AAPL on 2026-03-04 (n=23 trades). New symbol — running as fresh."* The banner doesn't block. It told the user the rule set is recycled even though the symbol is new — which is a useful disambiguation, because their gut said "new idea" and the platform corrected to "old rule, new symbol." They proceed. The whole interaction is ~2 seconds and adds a tiny mirror moment.

**Scene 2 — Tuesday afternoon, the accidental duplicate.** The user has had an idea on the walk home and rebuilds it in the Builder from memory. Clicks **Save as Thesis**, types the name "RSI Reversion Plus." Banner: *"This Thesis is identical to one you saved on 2026-04-12 as 'RSI Reversion v2'. Save as new, replace, or cancel?"* They click cancel, open the existing one, see they're a month behind their own thinking. No second copy. The `theses.json` file stays one entry shorter than it would have. This is the cheapest possible payoff the recommendation produces and it produces it constantly once a user has >10 saved Theses.

**Scene 3 — Wednesday night, the non-event during play.** The user is dial-twiddling — they want to *feel* how RSI threshold sensitivity behaves on a single symbol. They tweak the threshold from 30 → 28 → 32 → 27 → 25, hitting Run between each. The banner does *not* fire on any of these because each is a unique hash. No nagging, no toast, no glow. The platform stays out of the way during exploration. This is the scene that earns the "no mothering" line: ~80% of the user's backtests are play, and Phase 1's behavior in those 80% is *silence*.

**Scene 4 — Saturday morning, the negative case.** The user runs an old idea again deliberately, to see how it performs on fresh data through Q1's chop. Banner fires: *"You ran this on 2026-02-10 (n=18 trades). Re-run?"* They think *"yes, that's the point,"* glance past it, click through. The banner added nothing in this moment — but also didn't slow them down. Cost: half a second of eye-flick. The right metric for the recommendation is not "every banner produces value" — it's "no banner produces friction." Phase 1 wins by being mostly invisible and occasionally useful, not by being constantly clever.

### What counts as the same Thesis?

The banner's behavior in all four scenes turns on one question: when does a rule edit produce a new hash vs the same hash? Per the identity agent's canonicalization rules (§3), the answer is:

| Change | Same hash? | Why |
|---|---|---|
| RSI threshold 30 → 29 | **Different** | Threshold is content — different numbers fire different trades |
| Add a MACD rule | **Different** | Rule list grew |
| Remove a rule | **Different** | Rule list shrank |
| Reorder existing rules | **Same** | Rules sorted by deterministic tuple before hashing |
| Mute a rule | **Same** | Muted rules dropped before hashing (no-op on signal generation) |
| Toggle `negated` on a rule | **Different** | Treated as semantic content in v0 (canonicalizing negation is a tar pit) |
| Rename the Thesis | **Same** | Name / description / tags not in hash |
| Change interval 5m → 15m | **Different** | Interval is identity (a 5m setup ≠ 1d setup) |
| Change stop-loss 2% → 1.5% | **Different** | Exits are identity per data-model agent |
| Change position_size 50% → 100% | **Same** | Size is deployment, not Thesis (open question §9.1 — agents disagreed) |
| Switch yahoo → alpaca data | **Same** | Data source is deployment |
| Switch direction long → short | **Different** | Direction is content |

Two consequences of this design:

**Thresholds are content, not noise.** RSI 30 vs 29 is genuinely a different strategy — different trades fire, different equity curve, different fingerprint. That's what makes the banner *trustworthy*: it never claims identity it can't prove. The cost: Phase 1's banner won't catch "you're one tweak away from a thing you ran yesterday." Catching near-matches requires the **"1 rule away" diff engine** (rejected from Discovery, deferred to its own future brainstorm) — a Phase 2+ feature.

**Hash is not Thesis.** Every Run / backtest produces a `thesis_hash` that goes onto the journal row, automatically. A *named Thesis* in `theses.json` only exists when the user explicitly clicks **Save as Thesis**. So Scene 3's dial-twiddling creates five unique hashes in the journal trail (fine, the journal grows anyway) and zero named Theses (also fine, the user didn't save anything). The hash trail powers the banner; the named list is for the user's curated library.

**Edge case worth flagging up front**: position_size and slippage_bps. The data-model agent argued these go *inside* the hash (a "Fat Setup"); the identity agent argued they go *outside* (signal-generation only). Open question §9.1 — pick before any hash is committed to journal. My vote follows the identity agent (size and slippage are deployment) because it preserves the property that two users running the same logic at different sizes hit the same hash, which is the load-bearing call for any future cross-user feature.

**What these scenes are NOT showing** (and what the reviewers were right to push me away from): the banner saying anything about regime fit, behavioral fingerprint similarity to other Theses, ghost-bot performance, or "you're about to resurrect a strategy that died in chop." All of that is conditional on Phase 2-4 landing. Phase 1's banner has access to one thing only: the hash, and the journal rows that share it. Everything it says is a fact about *what you literally ran before*, not an interpretation. That constraint is a feature — it makes the banner trustworthy in week one because it can only state facts, not opinions.

If three months in the user is still glancing at the banner and occasionally going *"oh right, I did try this,"* the recommendation has earned its keep. If the banner has fired four times and felt redundant each time, back it out.

### What Ghost Mews actually buys you (if the banner lands)

The killer-use-case agent's pitch for Ghost Mews was that every OOS-clean strategy auto-spawns a paper bot. Reviewer 2 correctly pointed out that 47 phantoms is engagement bait on a $10k account — you can only deploy 2-3 real bots, the marginal phantom past #5 is dashboard noise.

The right-sized version: **Ghost Mews caps at ~5 active phantoms,** with auto-expiry on the rest. The killer feature isn't a portfolio; it's *replacing the 14-day shadow-trade discipline you don't actually run* with five forward-tests that run themselves. Five phantoms × 30 days = enough live evidence to promote with conviction. That's a real deployment-quality lift, sized for the actual capital base.

Ghost Mews is not Phase 1. It's the kind of thing that makes sense ~3 months after the banner has demonstrated that platform-side memory has earned its keep.

### What I'm explicitly *not* recommending

- **The full Setup-as-object SQLite migration** as anything but a long-deferred option. §8 phases 3-4 are the right shape, but they should remain genuinely conditional — not the assumed destination.
- **Behavioral fingerprint as a Phase-1 or Phase-2 feature.** The fingerprint agent's 84-dim vector is interesting but the adversarial agent's "dominated by symbol volatility" critique is unresolved. Ship the simpler exact-hash check first; revisit the fingerprint only if exact-hash matching turns out to fire too rarely to be useful.
- **The "corpus with views" framing** as a way to sell the present-state move. Phase 1 is a hash column with one consumer. Calling it a corpus borrows future-state architecture noun to inflate the present move. The corpus might exist at Phase 4, conditional on Phase 1-3 landing. Until then it's a hash with a banner.
- **The Thesis-anti-resurrection magic moment** (TSLA fanfic in the earlier draft) as evidence the recommendation is worth shipping. That scenario combined three features that don't exist yet (fingerprint + Necropsy + regime tagging) and invented match percentages. It's a possible Year-1 outcome conditional on a lot landing — not a Phase-1 promise.

### The honest cost

5-7 days, not 2-3. The hash + journal column is a day. The fuzz test for canonicalization is another day (non-negotiable per the identity agent — false-same is the killer failure). The banner has non-trivial UX choices: when does it fire (only on Save/Run, not on rule-edit, per the "no mothering" line), how is dismiss state persisted, what counts as a "match" — exact hash or canonical-near, what's the empty-state when no prior runs exist. 2-3 days for the banner alone. Save-as-Thesis button + `theses.json` is another day or two.

### Three open questions before this ships

1. **Does the banner fire on rule-edit, on Save, on Run, or on Deploy intent?** This is *the* "no mothering" question. My vote: only on Save and Run buttons being clicked, never during rule-edit. The user is in play during composition; commitment is the click.
2. **Is per-hash dismiss state permanent or session-local?** Permanent risks burying useful warnings; session-local risks nagging. My vote: permanent dismiss with a "show all hidden warnings" toggle in settings.
3. **Does the journal backfill for existing rows?** Probably no — leave legacy rows with NULL `thesis_hash`. Only new trades carry it. Backfilling existing rows would require re-hashing every saved strategy's rule set, which is a one-shot job that's easy to get wrong and not worth the risk.

### Stack-ranked summary

1. **Ship**: hash helper + journal column + Builder banner on Save/Run + Save-as-Thesis button. ~1 week.
2. **Watch**: does the user reference the banner in JOURNAL.md within a month? If yes → graduate. If no → back out, the substrate isn't earning its keep, the rest of this doc is moot.
3. **Conditional on (1) earning its keep**: Ghost Mews (right-sized to ~5 phantoms), then behavioral radar (the fingerprint agent's v0), then the §8 Phase 4 SQLite migration if the volume justifies it.

That's the whole recommendation. The corpus framing, the Thesis rename, the "platform speaking" rhetoric — all conditional on Step 1 producing evidence. Without that evidence, the strategic ambition is the orchestrator's, not the user's.

