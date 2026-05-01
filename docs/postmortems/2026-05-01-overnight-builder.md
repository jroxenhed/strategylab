# Overnight Builder — Full Postmortem

From broken push to autonomous feature delivery in one session. Documents the entire process: failures, debugging, resolution, and first successful deliveries.

## Timeline

### Day 1 — 2026-04-29 (setup)

- Created scheduled routine `trig_01VAJyHdiq4TKiCiBbp1wCu3` via Claude Code `/schedule`
- Routine runs at 00:00 UTC (02:00 CEST) daily, Sonnet 4.6
- Prompt: read CLAUDE.md/TODO.md/JOURNAL.md, pick `[next]` items, implement, review, commit, push
- Tagged C11 (Monte Carlo), C12 (Rolling Window), D21 (Auto-pause) as `[next]`

### Day 1 — 2026-04-30 00:43 CEST (first run)

**Result: Features built, push failed.**

The builder successfully implemented all three features in one run — 6 commits, build-verified, self-reviewed. Then `git push origin main` returned 403 from the sandbox git proxy.

### Day 1 — 2026-04-30 01:33 CEST (second run)

**Result: Same 403.** Confirmed not transient.

### Day 1 — 2026-04-30 02:01 CEST (scheduled run)

**Result: Failed with API stream idle timeout.** This run had the old prompt (before we started debugging).

### Day 1 — 2026-04-30 ~20:45 CEST (debugging session begins)

Interactive session to diagnose and fix the push issue. This is where the real work happened.

#### Attempt 1: `gh pr create` instead of `git push`
- **Hypothesis:** `gh` might use different auth than the git proxy
- **Result:** Same 403. Both route through the same proxy at `127.0.0.1:<port>`
- **Learning:** All git operations go through the sandbox proxy regardless of tool

#### Attempt 2: `claude/` branch prefix
- **Hypothesis:** Anthropic support bot said branches must be prefixed with `claude/`
- Updated routine prompt to use `claude/overnight-YYYY-MM-DD` branches
- Triggered fresh run at 21:08 CEST
- **Result:** Still 403. The prefix restriction wasn't the issue.

#### Attempt 3: "Allow unrestricted git push" toggle
- Found the toggle in routine edit UI → Permissions tab
- Enabled it for `jroxenhed/strategylab`
- **Result:** Still 403 on the running session (old sessions don't pick up new permissions)
- **Learning:** Permission changes are session-scoped — need a fresh run

#### Attempt 4: Fresh run with toggle enabled
- Triggered new manual run
- **Result:** Still 403. Toggle didn't help either.
- Agent tried multiple fallbacks: `git push`, `git push -u origin HEAD`, retries with backoff, GitHub MCP file push (timed out)

#### Root cause found: Claude GitHub App not installed
- Agent diagnosed: `remote: Permission to jroxenhed/strategylab.git denied to jroxenhed`
- This is a GitHub-level rejection, not a proxy config issue
- The Claude GitHub App was never installed on the GitHub account
- Reads worked because the repo is public — writes require the app with Contents: Read & write permission

#### Resolution
- Installed Claude GitHub App at https://github.com/apps/claude
- Told the stranded session to retry
- **Push succeeded.** Branch `claude/dreamy-albattani-cDc4K` pushed with 2 commits
- PR #4 created: C11 (Monte Carlo) + C12 (Rolling Window), +477 lines, 9 files
- D21 was deferred to next run (builder only got to 2 of 3 tasks)
- **PR #4 merged same evening.**

### Day 1 — Post-merge fixes

- **Bug: Monte Carlo silent failure.** The overnight builder used raw `fetch('/api/...')` instead of the project's `api` axios client. Requests hit the Vite dev server (port 5173), not the backend (8000). No proxy configured. Failed silently — no error, no loading state, just nothing. Fixed to use `api.post()`.
- **Bug: Monte Carlo percentiles all identical.** Discovered but deferred to overnight builder as C13.

### Day 1 — Hardening

- Rewrote review protocol from parallel-agent-based (requires Agent tool, unavailable in remote routines) to 5-pass structured self-review checklist
- Added fix-review iteration loop (max 2 cycles) so fixes get re-reviewed
- Added Known Pitfalls section to review protocol (grows over time as bugs ship)
- Updated routine prompt to enforce all 5 passes and log review summary in commits
- Added 8 new TODO items (C14-C19, D22-D23) for future overnight runs
- Cleaned up 9 stale worktree branches + merged PR branch

### Day 2 — 2026-05-01 02:04 CEST (manual run)

**Result: Full success.** Three features delivered as PR #5.

- **C13** — Fixed Monte Carlo percentile bug. Identified that shuffling trade order doesn't change the sum, so all final values are identical by construction. Replaced `final_value` with `min_equity` (minimum equity touched during each simulation path). Also found a second bug: `setMcLoading(true)` in the `finally` block (should be `false`).
- **C14** — New `TradeHoldDurationHistogram.tsx`. SVG histogram of hold times, colored by win/loss dominance, summary stats (median, avg-win, avg-loss hold).
- **D21** — `BotConfig.drawdown_threshold_pct`. After each position closes, `_tick()` checks peak-to-trough PnL vs allocated capital. Pauses bot with reason + fires `notify_error` via `create_task`.

The builder correctly identified file dependencies (C13 and C14 both touch Results.tsx, so sequential; C14 and D21 independent, so parallel).

### Day 2 — 2026-05-01 02:09 CEST (scheduled run — the race)

The scheduled run fired 9 minutes late (intentional jitter — Anthropic adds up to 10% of period to prevent thundering herd). This created a simultaneous race between the manual run and the scheduled run.

**Result: Completed, but gracefully deferred.** The scheduled run independently implemented all three features, discovered the manual run had already pushed to the same branch, compared both implementations in detail, and chose not to push. Its comparison was valuable — noted that its C13 approach (percentage-based compounding) was more mathematically correct for the stated spec, while the manual run's min_equity approach was more practically useful.

### Day 2 — Post-merge verification

- All three features working correctly in browser
- **Bug found: MAX DD field not saving on existing bots.** `drawdown_threshold_pct` missing from `UpdateBotRequest` in `backend/routes/bots.py`. Same silent-drop bug documented in CLAUDE.md Key Bugs Fixed. Pydantic `extra="ignore"` swallows unknown fields. Fixed and added as Known Pitfall #4 in review protocol.

## What worked

1. **The builder's implementation quality is high.** Both runs independently identified the Monte Carlo root cause correctly (shuffle preserves sum), designed appropriate fixes, and built working UI components. The code is clean and follows project patterns.
2. **Task selection and dependency analysis.** The builder correctly identified `[next]` items, sequenced them by file dependency, and parallelized where safe.
3. **The handoff contract works.** TODO.md, JOURNAL.md, and NEXT_RUN.md were updated atomically with code changes.
4. **PR workflow is safer than direct push.** Review before merge catches issues like the UpdateBotRequest bug.
5. **The race condition resolved itself.** Two simultaneous runs didn't corrupt anything — the second gracefully deferred and provided a useful implementation comparison.
6. **Cost: $0 extra.** All runs covered by Max subscription (15 included daily runs).

## What failed

1. **Push auth was a multi-hour debugging session.** Root cause was simple (install the GitHub App) but not documented anywhere. Wasted 4 runs before finding it.
2. **Review protocol didn't catch the `fetch()` vs `api` bug.** The builder wrote raw `fetch()` instead of using the project's axios client. Silent failure in dev. The review was self-review without structured passes at that point.
3. **Review protocol didn't catch the `UpdateBotRequest` bug.** Even with the improved 5-pass review, the builder added a new field to `BotConfig` without adding it to `UpdateBotRequest`. This is the second time this exact bug has shipped (documented in Key Bugs Fixed).
4. **Scheduled run timing jitter was unexpected.** Up to 10% of period (15 min cap) added as intentional jitter. Not a real problem, just surprised us.

## Lessons / changes made

| Lesson | Action taken |
|--------|-------------|
| Claude GitHub App required for push | Documented in setup checklist |
| Branch must use `claude/` prefix | Hardcoded in routine prompt |
| "Allow unrestricted git push" toggle must be ON | Documented in setup checklist |
| Old sessions don't get new permissions | Documented — always trigger fresh run |
| Self-review misses integration bugs | Rewrote protocol as 5-pass structured checklist |
| Fixes can introduce new bugs | Added fix-review iteration loop (max 2) |
| `fetch()` vs `api` client | Known Pitfall #1 in review protocol |
| `UpdateBotRequest` field sync | Known Pitfall #4 in review protocol |
| Scheduled run jitter is normal | Documented — ~9 min offset is consistent |

## Setup checklist (for anyone replicating)

1. Install Claude GitHub App: https://github.com/apps/claude (Contents: Read & write)
2. Create routine at https://claude.ai/code/routines
3. Enable "Allow unrestricted git push" in Permissions tab
4. Use `claude/` prefixed branch names in prompt
5. Ensure `CLAUDE.md`, `TODO.md`, `JOURNAL.md`, `NEXT_RUN.md` exist in repo
6. Write a review protocol doc the builder can read during self-review
7. Tag TODO items with `[next]` to queue for the builder

## Stats

| Metric | Value |
|--------|-------|
| Total runs to first successful push | 5 (3 failed push, 1 failed timeout, 1 success) |
| Features delivered (PR #4 + PR #5) | 5 (C11, C12, C13, C14, D21) |
| Lines added | ~830 |
| Files changed | ~21 |
| Bugs shipped by builder | 3 (fetch vs api, identical percentiles, UpdateBotRequest) |
| Bugs caught by builder's self-review | 1 (setMcLoading in finally block) |
| Extra API cost | $0 |
| Time from first run to operational pipeline | ~25 hours |
