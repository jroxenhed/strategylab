# StrategyLab Cheat Sheet

Quick reference for daily workflows. Full details in the linked docs.

## Interactive Sessions (daytime)

Start Claude Code in the project directory. It reads CLAUDE.md automatically.

```
cd ~/Documents/strategylab
claude
```

The orchestrator cycle runs automatically — you describe what you want, Claude delegates to subagents, reviews, fixes, and commits. You make decisions, Claude does the work.

**Key commands in Claude Code:**
- `/schedule` — manage the overnight builder
- `ce:review` — run a multi-persona code review (the user says "use ce:review")
- Just describe what you want — Claude follows the CLAUDE.md workflow

## Overnight Builder (autonomous)

Full explainer: [overnight-builder.md](overnight-builder.md)

### Queue work (evening)

Tag items in TODO.md:
```markdown
- [ ] [next] **C11** Monte Carlo simulation — ...
- [ ] [next] **C12** Rolling performance window — ...
- [ ] **B20** Multi-timeframe (not tagged = skipped)
```

Commit and push:
```bash
git add TODO.md && git commit -m "tag [next] items" && git push
```

### Enable / disable

From Claude Code terminal:
```
"enable the overnight builder"
"disable the overnight builder"
```

From web UI: https://claude.ai/code/routines

Manual trigger: click "Run now" on the routines page.

### Steer a run

Edit `NEXT_RUN.md` before the run:
```markdown
## Task Override
- C11           ← only build these

## Skip
- B20           ← skip even if [next]

## Constraints
- Don't touch bot_runner.py
- Backend only
```

### Check results (morning)

```bash
git pull
cat NEXT_RUN.md    # run report at the bottom
```

Also check Slack if you set up the webhook.

### If something went wrong

```bash
git log --oneline -5          # see what was pushed
git revert <hash>             # undo a bad commit
# Add to NEXT_RUN.md ## Skip to prevent retry
```

## Key Files

| File | What it does |
|------|-------------|
| `CLAUDE.md` | Project rules. Claude reads this every session. |
| `TODO.md` | Task backlog. `[next]` = overnight builder picks it up. |
| `JOURNAL.md` | Ship log. What was built and when. |
| `NEXT_RUN.md` | Steer overnight runs + see run reports. |
| `backend/.env` | Secrets (API keys, webhook URLs). Gitignored. |
| `docs/overnight-review-protocol.md` | How the builder reviews code. |
| `bin/slack-report.sh` | Sends Slack notifications. |
| `.claude/settings.json` | Claude Code hooks (auto build check on .ts edits). |

## Secrets in backend/.env

```
ALPACA_API_KEY=...           # Alpaca market data
ALPACA_SECRET_KEY=...
IBKR_HOST=...               # IBKR Gateway (optional)
IBKR_PORT=...
NOTIFY_URL=https://ntfy.sh/your-topic    # Bot trade alerts (phone)
SLACK_WEBHOOK_URL=https://hooks.slack.com/...  # Builder run reports
```

## Links

- Routines: https://claude.ai/code/routines
- Connectors: https://claude.ai/customize/connectors
- Slack webhooks: https://api.slack.com/apps
- ntfy.sh: https://ntfy.sh
