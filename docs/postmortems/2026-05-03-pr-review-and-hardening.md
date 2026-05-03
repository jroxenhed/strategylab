# Session Post-Mortem: 2026-05-03 PR Review + Architectural Hardening

**Shipped:** 6 P1 review fixes (PR #10), 5 safety fixes (PR #11 overnight), 2 P1 hardening fixes (PR #11 review), 14 new TODO items, overnight task limit raised 3→5
**Duration:** Single session
**Pattern:** Review-driven development — multi-persona agents find bugs, then targeted fix agents resolve them

---

## What happened

1. User shared PR #10 (D24: regime filter live bot integration, 530+/154- lines)
2. Launched 4 parallel review agents (correctness, reliability, adversarial, API contract)
3. Synthesized findings → 6 P1 bugs, all fixed and pushed
4. Updated TODO/JOURNAL, tagged next run, raised overnight limit to 5
5. Helicopter-view architectural audit via Explore agent → found 7 structural issues (crash-unsafe writes, silent error swallowing, no bot_runner tests, unbounded growth, polling waste)
6. Added F14-F21 to TODO, prioritized safety fixes for overnight run
7. Overnight build 6 shipped in <15 minutes (5/5 tasks, 0 findings)
8. Reviewed the overnight's output with 3 more review agents → found 2 P1 gaps in the safety fixes themselves
9. Fixed both P1s, verified with another review pass, merged

## What worked

### 1. Multi-persona review caught real bugs the overnight builder missed

The overnight builder's self-review found 0 P1s across its 5 safety fixes. Our 3-agent review pass found 2 P1s that would have shipped:
- Hardcoded `.tmp` path enabling concurrent-save race
- `_load_trades()` unprotected by the new journal lock, enabling torn reads

The overnight builder is good at implementation but its single-pass self-review can't match 3 independent reviewers attacking from different angles. The review-of-the-review pattern (build → overnight ships → human triggers multi-agent review → fix → merge) is the right workflow for safety-critical code.

### 2. Reviewer consensus as confidence signal

The `_load_trades()` lock gap was flagged independently by all 3 reviewers (correctness, reliability, adversarial). When 3/3 reviewers find the same bug through different reasoning paths, confidence is near-certain. When only 1/3 finds something, it warrants investigation but may be a false positive.

### 3. Helicopter-view audit surfaced systemic issues

Reading 150 lines of diff doesn't reveal that `bots.json` has no crash protection, or that the 1000-line bot_runner has zero tests. The Explore agent's full-repo survey found issues that no PR-scoped review would catch. This should be a periodic exercise — every ~20 shipped items, do a full architectural audit.

### 4. Safety-first prioritization

Reordering the overnight run from features (C18, D23) to safety fixes (F14-F16, D24a, D25) was the right call. The three crash-safety fixes took <15 minutes combined but protect against total data loss. Features can wait; crash protection can't.

### 5. Review-then-fix loop is fast

The full cycle — dispatch 3 reviewers in parallel (~2 min), synthesize findings (~1 min), dispatch 2 fix agents in parallel (~30s), verify + re-review (~2 min) — takes about 5 minutes total. The cost of NOT doing this is shipping bugs that get discovered in production with real money at stake.

## What didn't work

### 1. Reviewers reading wrong branch (3 of 7 agents)

The first round's correctness reviewer couldn't access the PR branch (no bash permission), the adversarial reviewer and API contract reviewer both read main instead of the PR branch. Result: most of their findings were false positives about code that the PR had already changed.

**Lesson:** For PR reviews, either (a) check out the PR branch before dispatching, or (b) extract PR files to `/tmp` and point agents there. The retry approach (re-dispatch with `/tmp` paths) worked but wasted a full agent slot.

### 2. Adversarial reviewer found real pre-existing bugs but framed them as PR findings

ADV-003 (opposite-direction guard `pass`), ADV-006 (manual_buy bypasses guards), ADV-007 (stop_bot doesn't cancel OTO orders) are all real bugs — but they're pre-existing, not introduced by PR #10. The adversarial reviewer didn't distinguish "this code is dangerous" from "this PR made the code dangerous."

**Lesson:** Include in adversarial prompts: "Distinguish between bugs INTRODUCED by this PR vs PRE-EXISTING bugs revealed by reading the surrounding code. Both are worth flagging but label them differently."

### 3. Too many [next] tags

After adding new items, 8 TODOs were tagged `[next]` but the limit is 5. The overnight builder had to silently prioritize. Should keep `[next]` tags to exactly the limit count, ordered by priority.

**Lesson:** Maintain `[next]` count = overnight task limit. Excess tags create ambiguity about priority order.

## Session metrics

| Metric | Value |
|--------|-------|
| PRs reviewed | 2 (#10, #11) |
| Review agents dispatched | 7 (4 for PR #10, 3 for PR #11) |
| P1 bugs caught by review | 8 (6 in PR #10, 2 in PR #11) |
| Fix agents dispatched | 6 (2+2 for PR #10, 2 for PR #11) |
| Verification review agents | 2 (1 per PR, post-fix) |
| New TODO items added | 14 (D24c-D25, F14-F21) |
| Overnight tasks shipped | 5 (F14-F16, D24a, D25) |
| False positive rate | ~40% (branch-access issues) |

## Patterns to preserve

### Review-of-overnight workflow
```
1. Overnight builder ships PR
2. Human triggers multi-agent review (correctness + reliability + adversarial)
3. Orchestrator synthesizes, dispatches fix agents for confirmed P1s
4. Quick verification review of fixes
5. Merge
```

### Periodic architectural audit
```
Every ~20 shipped items:
1. Dispatch Explore agent with "very thorough" breadth
2. Focus on: data integrity, error handling patterns, test coverage gaps,
   performance bottlenecks, security surface
3. Add findings to TODO with difficulty tags
4. Prioritize safety fixes over features
```

### Priority ordering for overnight runs
```
1. Safety/crash fixes (protect real money)
2. Test harness for untested critical paths
3. Refactoring that unblocks future work
4. Features
```

## Key decision: F20→F21 gate

The most consequential decision this session was establishing that no more feature work should land in bot_runner.py until it has tests (F20) and is split (F21). Every overnight build that adds to the 1000-line untested monolith makes the eventual split harder and increases the surface area for undetected bugs. The gate is now documented in TODO.md and NEXT_RUN.md.
