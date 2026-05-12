---
date: 2026-05-12
topic: discovery-page
focus: out-of-the-box ideas for what Discovery could become
mode: repo-grounded
---

# Ideation: Discovery Page (StrategyLab)

The Discovery page today (`frontend/src/features/discovery/Discovery.tsx`) is two stacked components — SignalScanner (batch-test one strategy across a watchlist) and PerformanceComparison (single-symbol equity curve). No regime awareness, no journal mining, no curation, no serendipity, no daily ritual. The goal of this round is to imagine what Discovery *could* be, not to tighten what it is.

48 raw candidates were generated across 6 frames (pain, inversion, assumption-break, leverage, cross-domain, constraint-flip), critiqued, and clustered. The 10 below survived.

## Grounding Context (compressed)

- **Capital reality**: ~$10k account. Avoided losses compound faster than discovered winners. Per-trade costs are first-order.
- **Infra already shipped**: `/api/backtest/quick/batch` (DoS-hardened to 500 symbols, F91/F102), per-bot tagged journal, signal_engine rule grammar, slippage empirical endpoint, bot_runner polling loop, IBKR/Alpaca/Yahoo data, multi-pane chart.
- **Roadmap home**: TODO.md Section E — E1 (candidate-scan criteria TBD), E2 (batch efficiency), E3 (AI/ML assist), E4 (candidates → bot army). Officially "mostly untouched."
- **Prior adversarial gotchas (do not re-discover)**: (1) automated sweeps always find *something* — walk-forward/OOS gate non-negotiable. (2) Bot-correlation = hidden portfolio risk — correlation gate before deploy. (3) "Why does this edge exist?" is human-only — never auto-deploy.
- **Market gap**: $0-40/mo tier between Finviz/TradingView (filter-only) and Trade Ideas/MarketSurge ($127+, premium AI scan). No competitor combines backtesting + discovery + (eventual) social in this band.
- **Patterns worth stealing**: pantry-match (Supercook), star-velocity ranking (GitHub Trending), behavioral DNA clustering (Letterboxd Nanocrowd), regime tags (2025-2026 academic), Strava-segment passive leaderboards, Spotify Monday-drop ritual.

## Ranked Ideas

### 1. Regime-Aware Discovery (the systemic move)
**Description:** Build a tiny `RegimeClassifier` service that tags every cached bar (SPY + VIX + ADX → trend / chop / vol-spike / vol-contraction, plus per-symbol micro-regime). Inject at `shared.py:_fetch()` so every consumer inherits regime tags for free. On Discovery, this surfaces three ways: a persistent **Weather Banner** at the top ("Current regime: trending-up, low-vol — VIX 14, breadth 67%, 7-day strip"), a **glyph on every candidate card** showing which regime it thrives in (with mismatch visually loud — wrong-weather strategies fade), and a **No-Click Switchboard** that detects regime shifts overnight and prompts "Regime flipped to chop. 4 of your live bots are out-of-regime; 2 mean-reversion candidates fit better. Rotate?"
**Rationale:** The most expensive blind spot for a $10k retail trader. Academic literature (Harvey 2025, Jia 2025) confirms factor performance is regime-conditional and detection is tractable. Zero retail platforms ship this. Highest leverage of any idea here — once the regime decoration exists, it powers Discovery + Trading + the Daily Mixtape + the Necropsy Drawer + the Anti-Discovery page simultaneously.
**Downsides:** Regime classification is itself a modeling problem — must be honest about confidence (don't bucket boldly when ADX is ambiguous). Risk of users over-trusting a label that's noisier than it looks. Mitigation: surface confidence + display in shaded bands rather than crisp categories.
**Confidence:** 90%
**Complexity:** Medium
**Status:** Unexplored

### 2. The Anti-Discovery Page (What NOT To Trade)
**Description:** Invert the default. The top half of Discovery leads with a **"Do Not Trade Today"** list, mined from: (a) symbols where your historical rules have negative expectancy *given current regime*, (b) symbols where your last 3 attempts lost, (c) symbols where empirical slippage > modeled (per `/api/slippage/{symbol}`), (d) symbols where your live bots already have correlation > 0.7 to your existing book. The win-list is secondary, below the fold. Optional **Tilt Lock** addendum: after 3 consecutive losses OR within 2 hours of a stop-out, the entire candidate grid is veiled behind a one-click "cooling off" panel that nudges journal review instead.
**Rationale:** For a $10k account, avoided losses compound faster than discovered winners. Every other retail platform optimizes for *more trades shown = more engagement*. Inverting that aligns with the user's actual goal (sleep quality, capital preservation). The Tilt Lock addendum solves the revenge-trade pathology that destroys retail accounts.
**Downsides:** Could feel paternal — the "no mothering" line is real for this user. Mitigation: framing is informational, not coercive; the unlock is always one click; no notifications about it. Risk that the negative-expectancy heuristic is itself overfit — guard with a "based on n=X trades" caveat on every flagged symbol.
**Confidence:** 85%
**Complexity:** Low (mines existing journal + slippage data; no new infra)
**Status:** Unexplored

### 3. Phantom Portfolio You Already Own
**Description:** Every strategy that clears the OOS gate (during any backtest, anywhere in the app) **auto-spawns a ghost paper bot** that begins accumulating real journal entries from that moment forward. The user does nothing to opt in. Discovery shows your **phantom P&L curve** — "if you'd deployed every signal that backtested clean, here's where you'd actually be right now" — alongside individual ghost bots ranked by live realized PnL. A "promote to real bot" button retroactively converts any ghost into a tracked allocation, *keeping its full live trade history*.
**Rationale:** Inverts the deploy decision entirely — bots exist by default; the user *demotes* duds rather than approving winners. Live shadow results replace forward-test waiting (the 14-day shadow-trade discipline from prior adversarial review, made painless). TODO E4 ("candidates → bot army") gestures at this; this is the load-bearing concrete version. Wildly novel — no competitor has this.
**Downsides:** Resource cost — N ghost bots × poll cadence. Mitigation: ghosts poll on a longer cadence (15-min bars instead of 1-min), share one `bot_runner` worker, and auto-expire after 90 days unless promoted. Risk of overwhelming the journal with ghost rows — separate `phantom=true` flag so they never leak into real PnL aggregations.
**Confidence:** 75%
**Complexity:** Medium-High (touches `bot_runner` + journal schema)
**Status:** Unexplored

### 4. Falconer's Mews — Rotate Your Existing Birds
**Description:** Reframe Discovery from *acquisition* to *stewardship*. The default surface is your **mews** — a row of saved strategies as cards, each showing current condition (rested / overhunted / molting from recent losses), preferred quarry (sector or regime where it historically thrives), and a one-line rotation suggestion ("Fly the gyrfalcon today — high-VIX chop, your gyrfalcon's preferred weather"). New-strategy acquisition is a secondary tab, not the primary surface.
**Rationale:** Matches the actual situation of a $10k single-user account: you don't need 50 strategies, you need to know which of your 6 to deploy *today*. Every other retail platform treats Discovery as a strategy-acquisition funnel; this is the only framing here that respects that most value lives in disciplined rotation, not endless search. The "no serendipity" pain point is solved by *rotation as serendipity*.
**Rationale (bonus):** Pairs naturally with Idea #1 (regime tags drive the rotation suggestion) and Idea #6 (a council member can argue the rotation case).
**Downsides:** Assumes the user already has a meaningful saved-strategy library — fails for true cold-start (Idea #10 covers cold-start separately). Risk that rotation suggestions become noise if the algorithm is naive — keep the suggestion plain-text and explainable, not a black-box "trust me."
**Confidence:** 85%
**Complexity:** Low-Medium
**Status:** Unexplored

### 5. Council of Personas
**Description:** Each Discovery candidate is presented as a **dialogue** between four named personas, each rendering its own templated take: **Quant** ("Sharpe 1.4, n=47, t-stat 2.1, Kelly 0.18"), **Tape Reader** ("entry candles look like exhaustion, not continuation — chart-pattern flag"), **Macro Bear** ("worked in QE regimes, current regime is QT — historical analog: 2022-Q2"), **Trend Follower** ("aligned with 50-day, breadth confirms — ship it"). Personas can disagree publicly; the disagreement itself is the signal — if all four converge, conviction is high; if Quant says yes but Macro Bear says no, the candidate carries a "split-vote" tag. No LLM needed for v1 — each persona is a small templated scoring function over existing data.
**Rationale:** A single ranked list pretends objectivity, but every ranking encodes a worldview. Surfacing disagreement makes the user calibrate *their own framework* rather than blindly trusting a Sharpe number. Genuinely fun, sticky, and educational — month-3 users build mental models of *why* trades work. Pairs with regime tags (Macro Bear consumes them) and behavioral DNA (Tape Reader consumes it).
**Downsides:** Risk of cute-but-shallow — if the four voices reduce to "four ways of saying the same Sharpe," the framing is theater. Mitigation: each persona must have at least one input the others can't see (Quant: stats; Tape Reader: chart-pattern features; Macro Bear: regime + macro deltas; Trend Follower: trend + breadth). Personas should sometimes be wrong in known ways — that's the lesson.
**Confidence:** 70%
**Complexity:** Medium
**Status:** Unexplored

### 6. Your Journal Is the Alpha
**Description:** Treat the user's own per-bot tagged journal as the most predictive dataset in the app. Discovery surfaces patterns mined from it: *"Your 3pm entries lose 60% of the time. Your shorts only work when RSI < 25 on entry. You over-trade after a win streak (+38% trade count, -22% expectancy). Your best month had 7 trades; your worst had 31."* Each finding is a one-line card with the n that backs it. Optional: a **"Frustration Replay"** button on any losing trade — rewinds the bar, overlays VIX + sector breadth + earnings calendar + gap context, and assigns a one-line cause-of-loss tag ("entered into a gap-down day").
**Rationale:** The journal is the only dataset where the user has structural informational edge over institutions. Market-data backtesting is commoditized; backtesting the user against themselves is uncopyable. Solves the 11pm "I lost again on the same setup, I don't know why" pain at the source. Also: doubles as a behavior-shaping nudge — once you've seen "you over-trade after winners," you can't unsee it.
**Downsides:** Requires a non-trivial trade history to be meaningful (n > ~30 trades minimum). Until then, the surface looks empty. Mitigation: graceful degradation — show "patterns will appear after 30 trades; you have N" rather than a blank panel.
**Confidence:** 80%
**Complexity:** Low (the journal already has every column needed)
**Status:** Unexplored

### 7. Necropsy Drawer
**Description:** A morgue of strategies and bots that **died** — backtests with great in-sample Sharpe that fell apart out-of-sample, paper bots that bled out, live bots auto-demoted by edge decay. Each cadaver has a tagged **cause of death** ("died of regime shift Q3 2024", "killed by slippage on 2-bps assumption", "succumbed to overfitting — n=4 trades", "edge half-life expired"). Browse the drawer *before* designing a new strategy. Cards show the dead strategy's equity curve in red as a permanent memento mori.
**Rationale:** Survivorship bias is the #1 self-inflicted wound on a backtester. Every retail platform shows winners and hides corpses. Surfacing the dead is a vaccine against repeating the same overfit mistake. The cause-of-death taxonomy doubles as a teaching artifact — over a year, the user builds intuition for *how* strategies fail, not just *that* they fail. Genuinely novel in retail.
**Downsides:** Could feel morbid or discouraging if dominant on the surface — keep it as an opt-in drawer/tab, not the landing view. Cause-of-death classification must be honest about ambiguity (some deaths are "unknown" — don't fake a label).
**Confidence:** 75%
**Complexity:** Low-Medium (extends journal schema with `archived_at` + `cause_of_death`)
**Status:** Unexplored

### 8. One-Trade-Today (Default-to-Recommendation)
**Description:** Discovery's default state is a **single full-bleed card**: *"AAPL long, entry 187.40, stop 185.20, target 192. Confidence: 4 of 6 layers (OOS ✓, MC ✓, cross-symbol ✓, paper ✓, micro-live ✗, edge-thesis ✗). Tap to expand."* That's it. Everything else — alternates, rejected candidates, rule details, regime context, persona dialogue — lives behind a single "show me the rest" affordance. The platform pre-decides based on your behavioral DNA + regime + cost context, and asks you to *veto*, not browse.
**Rationale:** Reveals that browsing-style Discovery is a cognitive tax. The platform already knows enough to have an opinion. Default-to-recommendation, not default-to-empty-table. Decision latency drops from minutes to seconds. The salvageable insight even if the radical single-card form is wrong: Discovery should *pre-pick*, not *present a buffet*.
**Downsides:** High-trust UI — wrong pick on a Monday morning destroys credibility for weeks. Confidence-layer badges are non-negotiable to hedge this. Risk of homogenizing user behavior — every user trades the same trade. Mitigation: pre-pick is *seeded by the user's own behavioral DNA*, so two users with different journals see different cards.
**Confidence:** 65%
**Complexity:** Medium (wraps existing infra; the work is the pre-pick scoring function)
**Status:** Unexplored

### 9. Setup-as-First-Class-Object + Behavioral DNA Substrate (infra)
**Description:** Promote the implicit tuple `(rule + symbol + regime + size + cost-context + horizon)` into a real persisted entity called a **Setup**, with its own ID, hash, schema, and history. Every backtest, scan row, bot, journal entry, and shared artifact references a Setup ID instead of carrying loose copies of its parts. On top of Setup, compute a **behavioral fingerprint vector** (entry-timing histogram, hold-duration distribution, drawdown shape, exposure %, trade clustering, regime overlap) for every backtest as it finishes. Index fingerprints in a tiny in-process ANN (faiss/hnswlib) keyed on the vector.
**Rationale:** This is the substrate that makes *six* of the other ideas here 10× cheaper. "More like this," "is this crowded across my own bots," "you ran this before," "similar to last month's winners," anonymous percentile tiles, behavioral-cluster-based recommendations, the Wardley maturity clock — all collapse to a single k-NN call. Without Setup-as-object, every Discovery feature reinvents joins. With it, they share a primary key. The "leverage" frame's strongest move.
**Downsides:** Pure infra — no user-visible payoff in the first PR. Easy to over-scope: the Setup schema is a forever decision (changing it later is migration pain). Mitigation: ship the minimum schema (rule + symbol + regime), let downstream features pull the needed fields up over time.
**Confidence:** 80%
**Complexity:** Medium-High (touches StrategyRequest, BotConfig, journal — needs honest migration plan)
**Status:** Unexplored

### 10. Monday Mixtape (the Daily/Weekly Ritual)
**Description:** Discovery is not (only) a page — it's a **Monday-morning artifact** delivered via Slack/email/PDF/permalink. Format: *"Last week your live bots made $X (Y trades). Three setups matched your behavioral DNA this weekend: A, B, C — each with regime fit, expected slippage, and correlation to your existing book. One bot is showing degraded edge (`bot_42`) — consider muting. Your assumption to test this week: shorts work better when VIX > 22. Open in app: <permalink>."* Generated overnight Sunday by a cron + the overnight builder's existing summarization machinery. The web page mirrors the latest digest as a permanent surface. Pairs with cold-start onboarding (a fresh user gets a personalized weekly drop as soon as they have 30+ trades).
**Rationale:** A page demands the user *come to it*; a digest meets them where they live (inbox, Slack, coffee table on Sunday). Forces a weekly cadence that matches actual trading rhythm — counters the always-on dashboard pathology that quietly bleeds retail capital. Once the digest pipeline exists, every future Discovery feature gets a free distribution channel. Direct steal from Spotify's Monday Discover Weekly ritual mechanic.
**Downsides:** Risk of becoming spam — must be skippable, archivable, and limited to one channel by default. The first 4 weeks of digests will be thin while the personalization signal accumulates — frame as "your library is loading" not as "here's nothing."
**Confidence:** 80%
**Complexity:** Low-Medium (reuses overnight-builder + `bin/slack-report.sh` + cron + Pydantic JSON serialization)
**Status:** Unexplored

## Rejection Summary

| #  | Idea | Reason Rejected |
|----|------|-----------------|
| 1  | Frustration Replay Button | Absorbed into #6 (Journal-as-Alpha) — same data path, narrower framing alone |
| 2  | Pantry Mode | Strong but adjacent to #10 (Mixtape) and #4 (Mews); covered by ingredient-aware suggestions in those |
| 5  | Honest Tombstone | Absorbed into #7 (Necropsy Drawer) — same survivorship-bias fix, weaker as a sort flip alone |
| 6  | 90-Second First Strategy | Important but a cold-start onboarding flow, not a Discovery vision — belongs in its own UX track |
| 8  | Doppelgänger Wall | Bold (b5) but requires multi-user data to land; defer until user base exists |
| 9  | Sleeping-Hours Strategy Foundry | Absorbed into #3 (Phantom Portfolio) — same overnight-compute payoff, weaker as shortlist alone |
| 11 | Symbols-Pick-Themselves Watchlist | Useful infra but a feature of Watchlist, not Discovery; route through that page |
| 12 | Reverse-Engineered Rule Proposer | Bold (b5) but rule-induction search is a research project, not a Discovery feature; revisit when E3 (AI/ML assist) is real |
| 14 | Permanent Live-Since Backtest Ribbon | Absorbed into #3 (Phantom Portfolio) — the ribbon is the ghost's live curve |
| 15 | Auto-Demote Decaying Bots | Bot-system feature, not a Discovery surface; should ship in Trading instead |
| 20 | Monday Briefing | Merged into #10 (Mixtape) |
| 21 | Strategies As Living Policies | Strong idea but a model-design question, deserves its own brainstorm (interacts with overfitting in non-obvious ways) |
| 22 | Setups, Not Symbols | Absorbed into #9 (Setup-as-First-Class-Object) — same insight, infrastructure framing dominates |
| 23 | Forward-Shadow Mode | Absorbed into #3 (Phantom Portfolio) — phantom *is* shadow trading made automatic and painless |
| 24 | Sleep-Quality North-Star | Real insight but soft — better as a small dashboard widget than a Discovery reframe |
| 27 | Regime Service Decorating Backtests | Absorbed into #1 (Regime-Aware Discovery) — same infra, framed as the surface that pays it off |
| 28 | Discovery as Daily Artifact | Merged into #10 (Mixtape) |
| 29 | One-Rule-Away Diff Engine | Strong but deserves its own brainstorm — broader than Discovery; powers improvement loops everywhere |
| 30 | Persisted Triage Stream | Strong leverage but premature without #9 (Setup) shipped first; revisit after Setup lands |
| 31 | Bot-Correlation Matrix | Pure infra; the surfaces in #2 (Anti-Discovery) and #8 (One-Trade) consume it implicitly — ship as needed, not as a feature |
| 32 | Strategy Maturity Wardley Clock | Brilliant but requires anonymous similar-fingerprint counts (i.e. multi-user); defer until user base exists |
| 33 | Stargazing Almanac | Beautiful framing but its insight (strategies are seasonal) is fully captured by regime tags in #1 |
| 34 | Lectionary | Same shape as #8 (One-Trade-Today) with curatorial framing; lost the comparison on actionability |
| 36 | Sommelier's Flight | A comparator-UI feature, sub-component of any of the survivors; doesn't stand alone |
| 37 | Bird Migration Map | Lovely visualization but a Watchlist/Scanner enhancement, not a Discovery reframe |
| 38 | Rosetta Stone | A UI primitive (plain-English column) that should land in every card across the app, not its own idea |
| 40 | Geocache | Requires multi-user or strong past-self surface; defer |
| 41 | Single Red Button | Merged into #8 (One-Trade-Today) as the more pragmatic form |
| 42 | Every Backtest Costs $1 | Reveals a real need for a pre-filter, but the design lives inside #1 (regime) + #9 (Setup) scoring |
| 43 | Discovery for 0 Humans, Only Agents | Important meta-architecture (agent-native contract) but cross-cutting, not Discovery-specific — flag for its own brainstorm |
| 44 | Sunday-Only Ritual | Merged into #10 (Mixtape) — same Monday-drop rhythm, less restrictive |
| 45 | 100 Years of Synthetic Regimes | The robustness-score insight is preserved in #1; the synthetic-regime generator itself is a research project |
| 46 | 12-Year-Old Mode | A UI primitive (plain-English summary), should land across the app, not its own idea |
| 47 | System Distrusts Every Rule | The 6-layer confidence-funnel badges are absorbed into #8 (One-Trade-Today) and #3 (Phantom) — they're the gating mechanism, not a feature |
| 48 | 200-Page Report | The deep-dive-pane salvage idea is already implied in #5 (Personas) and #6 (Journal) expanded views |

## Notes on the Set

- **Three structural moves dominate**: regime-awareness (#1), inverting the default surface (#2 anti-discovery, #4 falconer's mews, #8 one-trade), and inverting the deploy decision (#3 phantom portfolio). Each could be done alone, but #1 multiplies the value of the others.
- **One infra idea (#9)** is honest about being unsexy — but it's the substrate. Worth flagging that ranking it alongside user-visible features is a category error; it should ship *under* whichever surface goes first.
- **Two ideas (#5 Personas, #7 Necropsy) earn their place on novelty alone** — they don't fix a quantifiable pain, but they reshape how users *think* about Discovery, and a $10k retail trader's edge often lives in better thinking, not better data.
- **Notable rejections worth a future brainstorm in their own right**: #29 One-Rule-Away (cross-cutting improvement engine), #21 Living Policies (model-design question), #43 Agent-Native Contract (architecture pattern).
- **The MVP wedge** that touches the most surface for the least code: ship #1 (regime decoration + banner) + #6 (journal-mined patterns) + #10 (Monday Mixtape that consumes both). All three reuse infra that already exists; all three become permanent surfaces other features plug into.
