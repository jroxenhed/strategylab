# D19 Bot Card Redesign Post-Mortem

_2026-04-29_

## What we did
Full UI redesign of bot cards: responsive sparklines, columnar stats, compact kebab dropdown, portfolio alignment. ~300 lines changed across 5 files, 106 new tests.

## Process used
1. **Explore** (haiku) → understand current implementation
2. **Plan** (sonnet) → write detailed spec with exact CSS values
3. **Review spec** (opus, 2x parallel) → correctness + maintainability reviews found 8 real issues
4. **Incorporate review findings** → updated spec with fixes (shared ui.ts, single constant, stopPropagation, division-by-zero guard)
5. **Implement** (sonnet) → single agent executed the full spec
6. **Review implementation** (opus) + **Write tests** (sonnet) — parallel
7. **Fix review findings** → 2 bugs fixed (stale detail, menuOpen reset)
8. **Visual verify** → user checked in browser, gave directional feedback
9. **Iterate** → 3 rounds of small CSS refinements based on screenshots

## What worked well
- **Parallel spec reviews** caught real bugs before implementation (stopPropagation, click-outside race, division-by-zero, stale detail state). Much cheaper to fix in spec than in code.
- **Model routing** (haiku/sonnet/opus) kept costs down without sacrificing quality. Haiku for exploration, sonnet for implementation + tests, opus only for reviews.
- **Single implementation agent** with a thorough spec produced clean code on first try — only 1 test fix needed (text matching).
- **Iterative visual refinement** with user screenshots was fast — small CSS-only changes, no re-review needed.
- **Test agent in parallel with review** saved ~2 minutes of wall time.

## What could be improved
- **Over-planned the flex values** — went through 3 iterations (50/50 → auto/1 → 35/65) because the initial "even split" assumption was wrong. Should have started with content-hugging left + flex-1 right from the spec phase.
- **Didn't ask user about column alignment upfront** — the 50/50 split, then auto-content, then 35% fixed were all attempts to solve alignment vs space-efficiency. Should have asked "do you want sparklines to align across cards, or each card to hug its content?" before implementing.
- **Test brittleness on flex values** — tests checking exact `style.flex` strings broke on every layout iteration. These tests are checking implementation details, not behavior. Better to test "sparkline is wider than info column" or "sparkline takes remaining space" semantically.
- **Review scope mismatch** — the second-round correctness review noted "this diff is larger than described" because it saw the full accumulated diff, not just the spacing change. Should scope reviews to the specific change being reviewed.

## Key takeaway
The explore→spec→review→implement→review cycle front-loads thinking and catches bugs cheaply. The visual iteration loop at the end is unavoidable for CSS work — no amount of spec review can replace seeing the actual layout. Budget 2-3 rounds of visual refinement for any layout change.

**Why:** Future UI redesign sessions should follow this pattern but with better upfront alignment on layout constraints.

**How to apply:** For CSS/layout tasks, ask "what should align?" and "what should hug content?" before speccing flex values. Keep tests behavioral, not structural.
