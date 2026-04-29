# Overnight Builder — Next Steps (2026-04-30)

## Where we left off

The overnight builder **works** — it explored, implemented, build-verified, and committed C11, C12, and D21 in a single autonomous run. The only blocker is **git push fails with 403** from the remote sandbox's git proxy. The OAuth token doesn't have write scope.

## What needs fixing

1. **Push credentials for remote routines.** Filed with Anthropic (see below). Until fixed, the builder can implement and commit but can't push.

2. **Workaround: `gh pr create` instead of `git push`.** The GitHub CLI might use a different auth path. Update the routine prompt to create a PR instead of pushing to main. You'd merge the PR manually (or auto-merge). This is actually safer anyway — you review the PR before it hits main.

3. **Second run (1:33) also stranded.** Same 403. Those commits are lost when the sandbox expires.

## To resume

1. Check if Anthropic responded to the support request
2. Try updating the routine to use `gh pr create` instead of `git push origin main`
3. If that works, enable the nightly schedule
4. If not, wait for Anthropic to fix push credentials

## What the builder successfully did (first run)

- Read CLAUDE.md, NEXT_RUN.md, TODO.md, JOURNAL.md
- Picked C11, C12, D21 (all [next] tagged)
- Implemented C11: Monte Carlo backend endpoint + MonteCarloResults.tsx frontend
- Implemented C12: Rolling performance endpoint + RollingChart.tsx
- Implemented D21: Auto-pause on drawdown in bot_runner
- Build-verified all three (npm run build passed)
- Self-reviewed using overnight-review-protocol.md
- Committed all changes (6 commits locally)
- Failed to push (403 from git proxy)

## Routine ID

`trig_01VAJyHdiq4TKiCiBbp1wCu3`
https://claude.ai/code/routines
