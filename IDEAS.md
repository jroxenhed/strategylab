# Ideas

Unsifted thoughts, brainstorms, things that might never ship. Capture surface for the "I wonder if..." stuff that doesn't yet have a concrete what + why + rough how.

**Graduation rule:** when an idea has a clear implementation outline (file paths, rough line count, dependencies), assign it a fresh F-ID and move it to `TODO.md` as a concrete entry. Until then, it lives here. Retired IDs (E1–E4, C30) are not reused.

**Format:** date headers, then bullets. No IDs, no priorities, no difficulty tags. Just the thought.

---

## 2026-06-04

- **Discovery — scan for candidates** (was E1): Scan the universe for good StrategyLab strategy candidates. Needs concrete criteria (Sharpe threshold? volatility range? sector?) before it can become a real task.

- **Discovery — batch backtesting** (was E2): Efficiency-critical batch backtesting to support Discovery scanning workflows. Depends on scan criteria being defined first.

- **Discovery — AI/ML parameter tweaking** (was E3): AI/ML-assisted parameter optimization beyond the current grid search. Fuzzy territory until Discovery has a concrete use case driving it.

- **Discovery — candidate pipeline** (was E4): Full pipeline from candidate discovery through spawning a bot army. Stretch goal that presupposes E1–E3 are concrete and working.

- **CPCV (Combinatorial Purged Cross-Validation)** (was C30): Alternative to walk-forward that generates a *distribution* of OOS paths via combinatorial train/test draws + bar-purging to prevent leakage. Output: Probability of Backtest Overfitting (PBO) score + Deflated Sharpe Ratio (López de Prado, "Advances in Financial Machine Learning", 2018; SSRN 3257497). Stretch goal — purging matters most for ML-style overlapping labels (triple-barrier), less for rule-based crossover signals; embargo (`gap_bars`) already covers the main rule-engine leakage path. Graduate once C28 is in real use and distributional evidence beyond single WFE is actually wanted.
