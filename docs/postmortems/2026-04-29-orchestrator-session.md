# Session Post-Mortem: 2026-04-29 Orchestrator Pattern

**Shipped:** B4, A8 (fix + timezone), E5, plus 4 new TODO items (B20, C9, C10, D20)
**Duration:** Single session, 70/92 total shipped
**Pattern:** Main session as pure orchestrator, all implementation/review via subagents

---

## What worked

### 1. Parallel implementation agents
B4 had backend + frontend types dispatched simultaneously (independent work). E5 had backend endpoint + frontend rewrite in parallel. Zero merge conflicts. This cut wall-clock time roughly in half for each feature.

### 2. ce:review with multiple persona agents
The B4 review dispatched 7 persona agents (correctness, testing, maintainability, project-standards, performance, kieran-python, kieran-typescript). Cross-reviewer consensus surfaced 5 real bugs that would have shipped:
- **P1 negation bypass** — 5 reviewers flagged it independently. Would have caused visualized rule signals to show the exact opposite of what the rule does.
- **P2 rule_index collision** — 3 reviewers. Buy and sell rules sharing index 0 → same color, React key collision.
- **P2 muted guard missing** — 3 reviewers. Signals for rules that had zero effect on the backtest.
- **P2 timezone reactivity** (A8 review) — equity chart not re-rendering on ET↔Local toggle.
- **P1 batch crash** (E5 review) — per-ticker errors propagating 500 for entire batch.

Without the review loop, all 5 bugs ship. The review cost (~2 min wall-clock per dispatch) paid for itself many times over.

### 3. Review → fix → commit cycle
After synthesis, a single sonnet fix agent applied all safe_auto findings in one pass. Clean separation: reviewers are read-only, fixer is write-only, orchestrator verifies and commits. No ambiguity about who does what.

### 4. Haiku for exploration
The initial B4 exploration (mapping marker/rule code paths) and E5 exploration (SignalScanner structure, saved strategy format, AddBotBar props) both used haiku agents. Fast, cheap, thorough enough to write implementation specs. Neither exploration wasted orchestrator context.

### 5. Progressive user feedback
The user's prompts shaped the session into its best form:
- "subagent leaning" → set the tone
- "delegate, delegate, delegate" → caught me when I started debugging A8 manually
- "imagine you are me" → reframed from "do the work" to "direct the work"
- "from now on: subagent workflow for everything" → made it permanent

Each correction made the session more effective. By E5, the pattern was fully automatic: explore (haiku) → implement (parallel sonnet) → review (parallel sonnet) → fix (sonnet) → verify + commit (orchestrator).

## What didn't work

### 1. Manual A8 investigation in main session
I spent ~8 turns manually testing the A8 downsample bug via curl + the dev server before the user reminded me to delegate. The investigation consumed main-session context for no gain — the eventual fix was found by a sonnet agent reading the actual code, not by my API probing.

**Lesson:** Even "quick debugging" should be delegated. The urge to "just check one thing" is the most common orchestrator trap.

### 2. First E5 review agent failure
The correctness review agent for E5 couldn't access Bash (permission issue) and returned nothing useful. Had to re-dispatch with explicit instructions to read files directly instead of running git diff.

**Lesson:** When dispatching review agents, prefer "read these specific files" over "run git diff" — file reads don't need shell permissions.

### 3. Agent working directory drift
The frontend implementation agent for E5 changed its working directory to `frontend/`, which meant the main session's subsequent `grep` and `tsc` commands failed with "no such file or directory" until I noticed. Cost ~3 turns of confusion.

**Lesson:** Always use absolute paths in verification commands. Don't assume agents maintained the working directory.

### 4. Reading full diff into orchestrator context
For the B4 review, I read the entire 31KB diff into the main session to pass to reviewers. This was wasteful — the reviewers read the files themselves. I should have just passed the file list and let them read.

**Lesson:** Pass file paths and intent to reviewers, not the diff content. Let them gather their own evidence.

## Session metrics

| Metric | Value |
|--------|-------|
| Features shipped | 3 (B4, A8, E5) |
| Bugs caught by review | 5 (3 P1, 2 P2) |
| Subagents dispatched | ~25 |
| Agent types used | haiku (exploration), sonnet (implementation, review, fix), opus (orchestrator only) |
| Commits | 5 |
| Main-session code edits | 3 (TODO.md lines, TS unused var, memory file) |

## Pattern to preserve

```
1. Pick task from TODO
2. Explore (haiku) → understand current code, map dependencies
3. Spec (orchestrator) → write tight brief from exploration results
4. Implement (parallel sonnet) → backend + frontend simultaneously
5. Verify (orchestrator) → grep key changes, run tsc, spot-check
6. Review (parallel sonnet via ce:review) → 4-7 persona agents
7. Synthesize (orchestrator) → merge findings, decide fix vs defer
8. Fix (sonnet) → apply all safe_auto findings in one pass
9. Verify + commit (orchestrator) → check fixes, update TODO/JOURNAL, push
10. Repeat
```

The orchestrator's job is judgment (what to build, what to fix, what to defer) and verification (did the agents do what I asked). Everything else is delegated.

## What the user's instructions got right

The CLAUDE.md already had "subagent-first workflow" and "model routing for subagents" before this session. But the user's real-time feedback pushed the pattern further than the docs specified:

- CLAUDE.md said "prefer subagents for anything beyond trivial (<10 line) fixes" — the user pushed to "subagents for everything"
- CLAUDE.md said "review loop for non-trivial work" — the user pushed to ce:review with structured persona agents
- CLAUDE.md didn't specify how to handle review findings — the session established the synthesize → fix agent → verify cycle

These refinements should flow back into CLAUDE.md and memory files so future sessions start where this one ended.
