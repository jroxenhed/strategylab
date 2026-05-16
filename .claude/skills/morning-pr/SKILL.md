---
name: morning-pr
description: Review, test, browser-verify (when warranted), and merge the latest overnight-build PR (or a specified PR number). Use when the user says "review the overnight PR", "morning PR", "/morning-pr", or asks to ship the overnight build.
---

# Morning PR review + merge

Wraps the routine: find the overnight draft PR → static checks → optional browser verification → merge → pull main.

## Inputs

- **No arg** — pick the most recent DRAFT PR whose title starts with "Overnight build".
- **Numeric arg** — use that PR number directly.

## Procedure

1. **Locate PR.**
   ```
   gh pr list --limit 10
   ```
   If no arg, pick the latest DRAFT with title `Overnight build YYYY-MM-DD`. Bail with a clear message if none found.

2. **Read PR body.**
   ```
   gh pr view <N>
   ```
   Note: tier (A/B/C), files changed, "Browser:" line, test plan. The body's `Verification` section lists what the overnight builder already ran.

3. **Checkout + diff.**
   ```
   git fetch origin
   git checkout <branch>
   git pull
   git diff main --stat
   ```
   Spot-check the diff against PR claims (file paths match, LOC roughly matches).

4. **Static checks.** Run from repo root:
   ```
   (cd frontend && npm run build) 2>&1 | tail -20
   (cd frontend && npm test -- --run) 2>&1 | tail -10
   ```
   `npm run build` not `tsc --noEmit` (catches verbatimModuleSyntax). If either fails, STOP and report.

5. **Browser verification — judgment call.**

   Verify in browser when ANY of:
   - PR body says `Browser: Not verified` / `please confirm` / `[needs-mcp]`
   - Diff touches `frontend/src/features/**/*.tsx` with user-visible behavior change (rendering conditions, hover/click/timer/effect deps, chart mounts)
   - Tier B/C item (per CLAUDE.md severity rules)
   - **Your own judgment** — if the diff smells UI-risky even when the PR claims static-verified-only, browser-check anyway. Token cost is not a constraint here.

   Skip when: pure refactor with zero behavior change, backend-only, infra-only.

   Protocol (lives in CLAUDE.md → "Live-Browser UI Verification"):
   - `curl -s http://localhost:5173 > /dev/null` — confirm dev server up
   - `mcp__chrome-devtools__list_pages` → `new_page` or `navigate_page` to `http://localhost:5173/`
   - Drive the specific behavior the diff changes — don't just confirm mount. Use a real backtest result if one is already loaded.
   - Assert in code (DOM query, localStorage, `performance.getEntriesByType('resource')`), not eyeballing screenshots.
   - Screenshot to `/Users/jroxenhed/Documents/strategylab/screenshot-*.png` (must be within workspace root). Delete after.
   - Watch for traps: date inputs commit on blur not change; `display: 'none'` keeps tabs mounted; localStorage keys are `strategylab-saved-strategies` (dashed); trade-conditional sub-tabs need ≥2/5 sells.

6. **Docs check.** Overnight builder normally updates `TODO.md` + `JOURNAL.md` in the same commit. Verify:
   ```
   git diff main -- TODO.md JOURNAL.md | head -40
   ```
   Confirm F-IDs match PR body, F-IDs filed for deferred items exist. If missing, fix in a follow-up commit before merge.

7. **Merge.**
   ```
   gh pr ready <N>
   gh pr merge <N> --squash --delete-branch
   git checkout main && git pull
   ```

8. **Report.** One paragraph: PR #, what shipped, build/test status, browser verification (or skip reason + one-line "static-verified only — <reason>"), merge confirmation.

## Stop conditions

- Build or tests fail → report, do NOT merge.
- Browser verification finds a regression → report, do NOT merge. User decides whether to fix-forward or revert.
- PR body claims something the diff contradicts (file count mismatch, missing F-ID in TODO) → report and ask.

## Anti-patterns

- Don't dispatch reviewer subagents — morning calibration is 2-persona max, and the user typically wants ship-or-flag, not another review pass. If the diff genuinely warrants persona review, ask first.
- Don't `cat` full files to skim. Grep + read slice.
- Don't trust line numbers from PR body — grep the anchor.
- Don't skip docs check — overnight builder occasionally forgets the atomic update.
