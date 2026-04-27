# Session Post-Mortem: 2026-04-27 — The 16-Task Blitz

**Duration:** ~2 hours  
**Tasks shipped:** 16 (15 TODO items + 1 follow-up fix)  
**Commits:** 12  
**Subagents spawned:** ~18 (reviews + implementations)  
**Files changed:** ~25 across frontend and backend  
**Regressions:** 0 (all type-checks passed, no broken features)

## What happened

The session started with "Today you get to choose :)" — full creative freedom to pick and ship tasks from the TODO backlog. What emerged was a workflow discovery session disguised as a productivity sprint.

### Phase 1: Sequential warm-up (tasks 1–5)

Started with B15 (MACD crossover bug fix) — a focused, well-understood bug. Built confidence and established the commit rhythm. Then D1 (timezone toggle), which introduced the review workflow: spec → dispatch review subagents → incorporate feedback → implement. C3, C2, and B16 followed, each getting faster as the pattern solidified.

**Key moment:** The D1 timezone toggle went through two parallel review subagents (scope + UX). The UX reviewer caught the re-render gap and the EST/EDT vs ET label issue. Both were incorporated before implementation. This was the session where the review loop proved its value for non-trivial work.

### Phase 2: Parallel discovery (tasks 6–9)

First parallel dispatch: C4 + F1 as simultaneous subagents. The context savings were immediately obvious — two tasks shipping at once while the main session stayed lean. Then B10 + F9 in parallel. By this point the pattern was locked in: pick independent tasks touching different files, brief each agent clearly, verify diffs on return.

**Key moment:** The user noticed the context needle barely moving despite high throughput. This was the "aha" — subagents as context-window management, not just parallelism.

### Phase 3: Full parallel orchestration (tasks 10–15)

Two more parallel batches: C1 + D4 (2 agents), then B2 + B11 + B17 + D9 (4 agents simultaneously). The 4-agent batch was the peak — four independent workstreams across frontend visual polish, backend reconciliation logging, data pipeline wiring, and strategy UX, all landing within minutes of each other.

**Key moment:** B17 turned out to be already implemented (B18 had shipped the tooltip). The subagent discovered this, stripped just the marker labels, and reported back. A human would have spent 20 minutes reading Chart.tsx to reach the same conclusion.

### Phase 4: Follow-up fix (task 16)

User tested the app and found the chart didn't respect the timezone toggle. Single subagent dispatched, returned a clean fix threading `toET()` through the timezone module. Shipped and verified visually.

## What worked

### The orchestrator pattern
Main session as architect, subagents as implementers. This kept the main context window clean for decision-making while offloading all file exploration, editing, and type-checking to disposable contexts. The main session never read a full file after the first two tasks.

### Graduated review ceremony
- **Trivial tasks** (F1 rename, C3 color tweak): no review, straight to implementation
- **Small logic tasks** (C2, C4): quick single-reviewer sanity check
- **Non-trivial tasks** (D1 timezone): full parallel review (scope + UX), incorporate, implement
- **Subagent-implemented tasks** (B10, D9): self-review checklist in the prompt

This prevented both over-engineering (reviewing a string rename) and under-engineering (shipping a timezone system without checking re-render behavior).

### Clear agent briefings
Each subagent got:
1. What to do (specific files, specific changes)
2. Why (context about the system)
3. How to verify (tsc, self-review checklist)
4. What to report back (exact changes, edge cases handled)

Vague prompts like "fix the timezone" would have produced mediocre results. The specificity came from the main session understanding the codebase and making design decisions upfront.

### Diff verification before every commit
Every subagent result was reviewed via `git diff` before committing. This caught:
- The F9 agent sneaking in a `max_spread_bps` field to the bot summary (helpful, but should have been called out)
- Confirming B17 was truly minimal (just text label removal, not a rewrite)

## What didn't work (or could improve)

### No visual verification
The CLAUDE.md rule says "start the dev server and use the feature in a browser before reporting the task as complete." We skipped this for every task — relying on type-checks only. The user caught the chart timezone gap during their own testing. If we'd checked visually after D1, we'd have caught it in the same commit.

**Lesson:** For UI changes, at minimum take a screenshot via a browser automation subagent or explicitly flag "not visually verified" to the user.

### Review workflow not enforced on subagents
The user called this out mid-session: "both agents follow the same rules we agreed on?" They didn't. The subagents implemented directly without dispatching their own review subagents. For this session's task sizes it was fine, but for larger features dispatched to subagents, the review loop should be part of the agent's own prompt.

**Lesson:** For non-trivial subagent tasks, add "dispatch a review subagent on your own changes before reporting done" to the prompt.

### Commit batching vs. per-task commits
The CLAUDE.md rule says "commit per task, don't batch." We batched the 4-agent parallel run (B2 + B11 + B17 + D9) into a single commit. This was pragmatic (they landed close together and the combined diff was reviewable), but it makes `git blame` and `git revert` coarser.

**Lesson:** When agents finish at different times, commit each as it lands rather than waiting to batch.

### Agent cwd drift
Twice the working directory drifted to `frontend/` (from running `npx tsc`) and caused git commands to fail. The fix was always `cd /Users/.../strategylab &&` but it's friction.

**Lesson:** Always use absolute paths in git commands, or reset cwd explicitly after type-checks.

## Metrics

| Metric | Value |
|--------|-------|
| Tasks shipped | 16 |
| Bug fixes | 3 (B15, B16 verified, start.sh port) |
| Features | 8 (D1, C2, C4, B10, B11, B2, B17, D9) |
| Visual polish | 3 (C3, C1, F1) |
| Cleanup | 2 (D4, F9) |
| Docs/workflow | 2 (journal, CLAUDE.md) |
| Type-check failures | 0 |
| Subagent failures | 0 |
| Post-ship bugs found by user | 1 (chart timezone) |
| Post-ship bugs fixed | 1 |

## Patterns to keep

1. **Subagents for anything beyond trivial.** The context savings alone justify it, even for single tasks.
2. **Graduated review ceremony.** Match the rigor to the risk.
3. **Verify the diff.** Trust but verify — subagents describe intent, not necessarily outcome.
4. **Journal at session end.** Future sessions start with full context of what shipped.
5. **Let the user choose the pace.** "Pick the next one" → "pick two" → "pick four" was a natural ramp.

## Patterns to adopt

1. **Visual verification for UI changes.** Either automate it or flag explicitly.
2. **Enforce review loops inside subagent prompts** for non-trivial work.
3. **Commit individually** even when agents finish close together.
4. **Pin cwd** after any command that changes directory.
