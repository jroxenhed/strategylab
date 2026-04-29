# The Overnight Builder

An autonomous Claude Code agent that picks tasks from TODO.md, implements them, reviews them, and ships them — while you sleep.

## How It Works

```
                    ┌─────────────────────────────────┐
                    │        You (daytime)             │
                    │                                  │
                    │  1. Tag TODO items with [next]   │
                    │  2. Edit NEXT_RUN.md to steer    │
                    │  3. Push                         │
                    └──────────────┬──────────────────┘
                                   │
                          02:00 CEST trigger
                                   │
                    ┌──────────────▼──────────────────┐
                    │     Overnight Builder Agent       │
                    │     (Claude Code, remote)         │
                    │                                  │
                    │  For each [next] task (max 5):   │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │ 1. Explore+Spec (haiku)  │    │
                    │  │ 2. Implement (sonnet)    │    │
                    │  │ 3. npm run build         │    │
                    │  │ 4. Review (3-4 agents)   │    │
                    │  │ 5. Synthesize findings   │    │
                    │  │ 6. Fix (one fixer agent) │    │
                    │  │ 7. Verify + commit + push│    │
                    │  └──────────────────────────┘    │
                    │                                  │
                    │  Update NEXT_RUN.md with report   │
                    │  Slack notification (if wired)    │
                    └──────────────┬──────────────────┘
                                   │
                          You wake up
                                   │
                    ┌──────────────▼──────────────────┐
                    │        You (morning)             │
                    │                                  │
                    │  git pull → see what shipped     │
                    │  Check NEXT_RUN.md for report    │
                    │  Review any flagged branches     │
                    └─────────────────────────────────┘
```

## Components

### 1. The Scheduled Routine

A Claude Code remote agent that runs on a cron schedule (daily at 00:00 UTC / 02:00 CEST). Managed at:

https://claude.ai/code/routines

The agent runs in Anthropic's cloud with a fresh git checkout of the repo. It has access to Bash, Read, Write, Edit, and the Agent tool for dispatching subagents.

### 2. NEXT_RUN.md (Steering File)

This is your steering wheel. Edit it before the run to control what happens:

```markdown
## Task Override
- C11        ← only build these, ignore [next] tags
- C12

## Skip
- B20        ← skip even if tagged [next]

## Constraints
- Don't touch bot_runner.py (bots are live)
- Backend only — no frontend changes this run

## Notes for the builder
- Focus on backend performance this run
```

The builder reads this before picking tasks. If you don't edit it, it defaults to picking `[next]` items from TODO.md.

### 3. The Review Protocol

Lives at `docs/overnight-review-protocol.md`. Distilled from ce:review (the interactive session's 8-persona review system). The overnight builder uses it to prompt 3-4 parallel review agents:

**Always-on reviewers:**
- **Correctness** — logic errors, edge cases, state bugs
- **Maintainability** — dead code, duplication, naming, coupling
- **Project Standards** — CLAUDE.md compliance, Key Bugs Fixed patterns

**Conditional reviewers** (added when the diff warrants it):
- Security, Reliability, TypeScript, Python

Each reviewer returns structured JSON with severity (P0-P3), confidence (0.0-1.0), and autofix classification. Findings below 0.60 confidence are suppressed. The builder synthesizes, deduplicates, and routes:

| Classification | Action |
|---------------|--------|
| `safe_auto` | Fixer agent applies automatically |
| `gated_auto` | Committed to a branch, NOT pushed to main |
| `P0` | Committed to a branch, NOT pushed to main |
| `advisory` | Noted in report, no code action |

### 4. Guard Rails

- **Max 5 tasks per run** — prevents runaway sessions
- **P0 gate** — anything with critical findings gets committed but NOT pushed to main. You review it manually.
- **Build verification** — `npm run build` runs before and after fixes. If the build breaks, the task is abandoned and flagged.

### 5. Notifications

**Primary:** The builder updates `NEXT_RUN.md ## Last Run` with a full report and commits it. You see it on `git pull`.

**Slack (optional):** Set `SLACK_WEBHOOK_URL` in `backend/.env`. The builder calls `bin/slack-report.sh` to post a summary. Create a webhook at https://api.slack.com/messaging/webhooks.

**ntfy.sh:** The existing bot notification system (D20) is separate — it's for real-time trade alerts, not builder reports.

## Daily Workflow

### Evening (before bed)
1. Review TODO.md — tag items you want built with `[next]`
2. Optionally edit NEXT_RUN.md with constraints or notes
3. Push

### Morning (wake up)
1. `git pull`
2. Check NEXT_RUN.md for the run report
3. If any tasks were deferred or flagged:
   - Check the branch the builder created
   - Review the P0 findings
   - Fix or merge as appropriate
4. Tag new `[next]` items for tonight's run

### During the day (interactive sessions)
Work normally. The orchestrator cycle in CLAUDE.md is the same workflow — explore, implement, review, fix, commit. The overnight builder just automates it.

You can always override the builder's work: revert a commit, edit NEXT_RUN.md to skip a task, or disable the routine entirely at https://claude.ai/code/routines.

## How It Relates to Interactive Sessions

| Aspect | Interactive Session | Overnight Builder |
|--------|-------------------|-------------------|
| Who starts it | You | Cron schedule |
| Model | Opus (orchestrator) + sonnet (agents) | Sonnet throughout |
| Review depth | 8 personas via ce:review | 3-4 personas via review protocol |
| Parallelism | Full pipeline (review A + implement B) | Sequential per task |
| Notifications | You're watching | NEXT_RUN.md + Slack |
| Risk tolerance | Higher (you're there to catch issues) | Lower (P0 = don't push) |
| Best for | Complex features, design decisions | Straightforward [next] items |

The overnight builder handles the "known work" — tasks with clear scope that don't need design decisions. Save the complex, ambiguous, or high-risk work for interactive sessions.

## Troubleshooting

**Builder didn't run:** Check https://claude.ai/code/routines — is it enabled? Check the cron expression.

**Builder ran but shipped nothing:** Check NEXT_RUN.md report. Common causes: no `[next]` items, all items had P0 findings, build failures.

**Builder pushed something broken:** Revert the commit (`git revert <hash>`), add the task to NEXT_RUN.md skip list, and fix it in an interactive session.

**Want to run it now:** Use `/schedule` and select "Run now", or trigger it from https://claude.ai/code/routines.

## Files

| File | Purpose |
|------|---------|
| `NEXT_RUN.md` | Steering file — task overrides, skip list, constraints, run report |
| `docs/overnight-review-protocol.md` | Review methodology for the builder's review agents |
| `bin/slack-report.sh` | Sends formatted message to Slack webhook |
| `CLAUDE.md` | Project context and orchestrator workflow (builder reads this) |
| `TODO.md` | Task backlog — `[next]` tags queue work for the builder |
| `JOURNAL.md` | Ship log — builder appends entries here |

## Private Repo Gotcha

Claude Code routines can't clone private repos via OAuth alone. The repo must be **public** for the remote agent to access it. Your secrets (API keys, webhook URLs) are safe — they're in `backend/.env` which is gitignored.

If Anthropic adds private repo support for routines in the future, switch back to private.

## Slack Setup

1. Go to https://api.slack.com/apps → Create New App → From scratch
2. Name: `StrategyLab Builder`, pick your workspace
3. Incoming Webhooks → Activate → Add New Webhook to Workspace
4. Pick your channel (e.g., `#strategylab-builds`)
5. Copy the webhook URL
6. Add to `.env`:
   ```
   echo 'SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/URL/HERE' >> backend/.env
   ```
7. Test: `bash bin/slack-report.sh "test notification"`

The builder calls `bin/slack-report.sh` after each run. If `SLACK_WEBHOOK_URL` isn't set, it silently skips — no error.
