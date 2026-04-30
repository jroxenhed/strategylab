# Overnight Builder — Status (2026-04-30)

## Current State: Operational

The overnight builder is fully working end-to-end. It picks tasks from TODO.md, implements them, self-reviews, pushes to a `claude/` branch, and opens a PR on GitHub.

**First successful delivery:** PR #4 — C11 (Monte Carlo simulation) + C12 (Rolling performance window). Merged same day.

## Setup Checklist (for reference)

1. **Claude GitHub App** — must be installed on your GitHub account at https://github.com/apps/claude with Contents: Read & write permission. Without this, the sandbox git proxy can't authenticate pushes (403).
2. **Routine permissions** — "Allow unrestricted git push" toggle ON in the routine's Permissions tab at https://claude.ai/code/routines. Without this, only `claude/` prefixed branches are allowed.
3. **Branch naming** — routine prompt uses `claude/overnight-YYYY-MM-DD`. The `claude/` prefix is required by the sandbox git proxy's default branch restrictions.

## Routine

- **ID:** `trig_01VAJyHdiq4TKiCiBbp1wCu3`
- **Schedule:** 00:00 UTC / 02:00 CEST daily
- **Model:** Sonnet 4.6
- **Manage:** https://claude.ai/code/routines

## Daily Workflow

1. Tag TODO items with `[next]` (max 3 per run)
2. Builder runs at 02:00 CEST
3. Morning: check GitHub for PR from `claude/overnight-*` branch
4. Review diff, merge if good
5. Pull locally, test, fix any issues

## Known Issues Found in First Delivery

- **`fetch()` vs `api` client** — the builder used raw `fetch('/api/...')` instead of the project's axios `api` client. Raw fetch hits the Vite dev server (port 5173), not the backend (8000). No proxy is configured. Silently fails. Fixed manually post-merge. The review protocol now flags this pattern.
- **Monte Carlo percentile bug** — all final value percentiles show the same number. Tagged as C13 `[next]` for the builder to fix.

## Steering

Edit `NEXT_RUN.md` to override task selection, add constraints, or skip items for the next run.

## Resolved Issues

- ~~Push 403 from sandbox git proxy~~ — Root cause: Claude GitHub App not installed. Installed at https://github.com/apps/claude with write access.
- ~~Branch prefix restriction~~ — Default sandbox only allows `claude/` prefixed branches. Toggle or use the prefix.
- ~~`gh pr create` also 403'd~~ — Same root cause (missing GitHub App). Both `git push` and `gh` work now.
