# Session Summary — 2026-05-02

3.5 hours, $18, 25% context used. Opus 4.6 with high effort.

## What We Did

### Regime Filter — Full V2 Plan
Designed a comprehensive implementation plan for regime-gated direction switching: a single bot that trades both long and short, flipping based on a higher-timeframe signal (e.g., daily 200 SMA).

**The journey:**
1. Started with the user's ideas doc (`docs/ideas/new-todo-items.md`) — two proposed features: A13 (multi-TF indicator overlay) and B21 (regime filter)
2. Explored the full codebase via subagents: direction system, data fetching, backtest loop, bot runner, strategy builder UI
3. Designed V1 (sit-flat gate) — regime gates entries on/off
4. Sent through 3 parallel review agents (feasibility, adversarial, scope guardian)
5. Reviews caught critical issues: always-positioned default is dangerous, A13 visualization is essential for strategy design, Alpaca position netting makes two-bot workaround unviable
6. User pushed the scope: "v2 is what we actually need" — a single bot that switches between long and short
7. Redesigned for full V2 with dual rule sets (independent strategies per direction, e.g., MA-based long + RSI/Stochastic short)
8. Second round of 3 parallel reviews caught more issues: `is_short` refactor staging, journal PnL direction filter, inter-stage schema safety, live vs backtest slippage divergence
9. Final plan: 6 incremental stages (A13a → A13b → B21 → B22 → B23 → D24), each delivering standalone value

**Key design decisions:**
- Three-state regime: long / short / flat (not binary). Short only when dual rules provided.
- Default `on_flip: close_only` (go flat on flip), NOT `close_and_reverse`. Users opt into always-positioned.
- `min_bars: 3` default — consecutive-bar smoothing prevents whipsaw.
- Anti-lookahead contract: T-1 HTF close for T's intraday bars. Non-negotiable.
- Same-symbol guard: regime bot gets exclusive symbol access.

### Overnight Build Verification
- A13a shipped overnight (data foundation: `fetch_higher_tf()`, `align_htf_to_ltf()`, alignment tests)
- C15 (streak analysis), C16 (Kelly sizing), D22 (CSV export) also shipped
- Two overnight runs fired simultaneously (33-min stagger!) — split the work cleanly, zero conflicts
- Merged PR, verified build + tests, all 6 alignment tests pass

### Summary Tab Layout Fix
- Removed `maxHeight: 600` cap on Summary tab content
- Added responsive CSS grid for Cost Breakdown, Win/Loss Streaks, Kelly Sizing — 3 columns on wide screens
- Quick fix, big visual improvement

### Overnight Builder Autonomy Upgrade
- Builder now suggests new TODO items from gaps found during implementation
- Tags `[next]` for the next run automatically
- Added Slack notification with rich format (shipped IDs, review findings, branch, next tagged, build status)
- Fully autonomous loop: pick → build → ship → reflect → suggest → tag → notify → repeat

## Stats
- **79/101 shipped** (was 75 at session start)
- **6 new TODO items** added: A13a, A13b, B21, B22, B23, D24
- **Plan file:** `docs/superpowers/plans/2026-05-01-regime-filter.md`
- **3 plan iterations** with 6 total review agents dispatched
- **~25 subagents** used across the session (exploration, planning, review)
- **0 code exploration in main session** — pure orchestrator

## Workflow Observations

### What worked
- **Parallel review agents are high-ROI.** Three reviewers (feasibility + adversarial + scope) in one dispatch. The adversarial reviewer alone changed the plan scope twice — caught the always-positioned default danger and the Alpaca netting implication.
- **Subagent delegation kept the main context lean.** 3.5 hours of heavy planning work at 25% context. All exploration, planning, and review happened in subagents.
- **User-driven scope decisions.** The AI scoped conservatively (V1 sit-flat). The user pushed to V2 twice — first adding A13 ("how else would I visualize it?"), then full direction switching ("it's all one trade as far as Alpaca is concerned"). Domain knowledge > AI planning.

### Lessons learned
- **Explore broker constraints first** when planning features that touch execution. Both Alpaca netting and the position flip timing were in existing docs but weren't surfaced until the user flagged them.
- **Design for the full vision, deliver incrementally.** "Think small, expand later" led to three plan rewrites. "Think big, decompose into chunks" would have been two.

### The meta
- 4 weeks from zero (no prior AI coding experience) to a fully autonomous dev pipeline
- Overnight builder ships features while sleeping
- 3k token startup context per session (was 10k+)
- Lean CLAUDE.md (173 lines) + memory files + disciplined workflow > plugins and tooling
