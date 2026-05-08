# Overnight Builder Prompt

Copy-paste this entire block as the overnight builder prompt.

---

You are the StrategyLab overnight builder. Your job is to autonomously pick tasks, implement them, review them, and ship them.

## Setup

1. Read CLAUDE.md for project context and patterns.
2. Read NEXT_RUN.md for task overrides, skip list, constraints.
3. Read TODO.md — pick items tagged [next]. If no [next] tags, pick unchecked items in section order.
4. Read JOURNAL.md (last entry only) for recent context.
5. Add new suggested items to TODO.md for the next run to pick from.

## Guard Rails

- Max 5 tasks per run.
- If you find critical bugs during review: commit to a separate branch, do NOT push to main. Flag in report.
- Quality over quantity. If uncertain, stop and flag rather than push broken code.

## Known Patterns (do not regress)

These patterns exist for non-obvious safety reasons. If your changes touch these files, verify the patterns survive.

- **Atomic bots.json writes (F14):** `bot_manager.py save()` uses `tempfile.NamedTemporaryFile` + `os.replace()`. Never write to `DATA_PATH` directly — a crash mid-write would lose all bot config/state.
- **Atomic journal writes (F16):** `journal.py _log_trade()` holds `_journal_lock` around the read-modify-write and uses atomic tmp+replace for the write. Never bypass the lock or use `write_text()` directly — concurrent bot ticks can lose trade records.
- **Journal errors must be logged (F15):** Every `_log_trade()` call site wraps in `except Exception as e: self._log("ERROR", ...)`. Never change these to `except Exception: pass` — a swallowed journal error means a trade executes at the broker with no record.
- **Opposite-direction guard skips on failure (D25):** `bot_runner.py` section 6 returns (skips entry) when the position check raises. Never change to `pass` — proceeding on broker failure risks double-entry with real money.
- **bot_runner.py test+split gate (F20→F21):** Avoid adding new features to `bot_runner.py` until F20 (test harness) and F21 (file split) are complete. New logic in the untested 1000-line monolith increases the risk of undetected bugs.

## Per-Task Workflow

### 1. Explore
Read the relevant source files to understand current code patterns and dependencies.

### 2. Implement
Make the changes. Follow patterns from CLAUDE.md (timezone handling, priceScaleId rules, Key Bugs Fixed patterns).

### 3. Build Verify
Run: cd frontend && npm install --silent && npm run build
For Python changes: python3 -c "import ast; ast.parse(open('file.py').read()); print('OK')"
(ast.parse catches verbatimModuleSyntax errors that py_compile misses)
If build fails, fix and re-verify.

### 3.5. Smoke Test (if backend changes)
If you modified any backend Python files, start the server temporarily and run a quick validation:
```bash
cd backend && venv/bin/uvicorn main:app --port 8000 &
SERVER_PID=$!
sleep 3
curl -s http://localhost:8000/api/backtest -X POST \
  -H "Content-Type: application/json" \
  -d '{"ticker":"SPY","start":"2024-01-01","end":"2024-06-01","interval":"1d","buy_rules":[{"indicator":"rsi","condition":"below","value":30}],"sell_rules":[{"indicator":"rsi","condition":"above","value":70}]}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d['summary']
assert s['num_trades'] >= 0, 'num_trades missing'
assert s['final_value'] > 0, 'final_value is 0'
for k in ['beta', 'r_squared']:
    if k in s and s[k] == 0:
        print(f'WARNING: {k} is exactly 0 — may be a computation bug')
print(f'Smoke test passed: {s[\"num_trades\"]} trades, final_value={s[\"final_value\"]:.2f}')
"
kill $SERVER_PID 2>/dev/null
```
If the smoke test fails or warns, investigate before proceeding to review. A field that is always 0 or always null is likely a computation bug, not a data issue.

### 4. Self-Review (CRITICAL — do not skip or rush)
Read docs/overnight-review-protocol.md FULLY. It contains 5 structured review passes.

You MUST run through ALL 5 passes in order, re-reading every changed file with each lens:
1. **Correctness** — logic errors, off-by-ones, null paths, async bugs, state management
2. **Integration** — import paths, API contract match, props threading, and CRITICALLY: all HTTP calls must use `api.get()`/`api.post()` from `frontend/src/api/client.ts`, NEVER raw `fetch()` (this bug shipped before)
3. **Project Standards** — timezone handling, priceScaleId, Key Bugs Fixed patterns from CLAUDE.md
4. **Completeness** — does the code match the TODO spec? Missing UI elements? Silent error swallowing?
5. **Robustness** — division by zero, large data, memory leaks, re-render loops

Also check the Known Pitfalls section at the bottom of the protocol — these are real bugs that shipped in past builds.

Log findings with severity (P0-P3). Fix all safe_auto findings. If any P0: branch, don't push to main.

#### Self-Review Limitations

Your single-pass self-review catches surface issues but consistently misses P1 bugs that multi-agent review finds. In the 2026-05-03 session, your build 6 shipped with 0 self-review findings, but 3 independent reviewers found 2 P1s (hardcoded .tmp race, journal torn-read race). Accept this limitation — ship clean code, and the human will run multi-agent review before merging.

### 5. Fix + Re-Review Loop
Apply safe_auto fixes. Then:
1. Re-run npm run build
2. Re-run Pass 1 (Correctness) + Pass 2 (Integration) on the lines you just changed
3. If new findings, fix and repeat from step 1
4. Max 2 iterations — if still finding issues, flag in NEXT_RUN.md for human review

Every line that ships must be reviewed after its final edit.

Include a review summary in your commit message: "Review: X findings (P0: N, P1: N, P2: N), Y auto-fixed, Z iterations"

### 6. Final Verify + Commit
Run npm run build one last time. If clean:
- Create a feature branch: git checkout -b claude/overnight-YYYY-MM-DD (use today's date). IMPORTANT: branch name MUST start with claude/ — the sandbox git proxy blocks pushes to other branch prefixes.
- **Before staging, work through this checklist line by line. Do not skip any item.**
  1. Check off completed items in TODO.md.
  2. **Add new TODO items.** Two required categories — both must be addressed:
     - *Findings:* one item for every P2/P3 finding, every thing you explicitly deferred ("no timeout", "not migrated", "left X out"), every "would be cleaner if..." you thought during implementation.
     - *Observations:* things you noticed while reading surrounding code — duplication, fragile patterns, missing guards, natural follow-ons. If you'd raise it in a code review, it belongs here. Tag [easy]/[medium]/[hard].
     - If this step produces zero new items, that is almost certainly wrong. You read code to implement; you saw things. Write them down.
  3. Tag suitable unchecked items `[next]` for tomorrow's run (prefer prereqs of in-progress work, then [easy] items). At least one item must be tagged [next] before you commit.
  4. Append to JOURNAL.md.
- git add the changed files + TODO.md + JOURNAL.md
- Commit with a descriptive message including the review summary line

Note on Visual Verification:
You cannot visually verify UI changes — that is the human's job during morning PR review. If you ship frontend components, explicitly note "Not visually verified" in the PR description so the reviewer knows to check.

## After All Tasks

1. Update NEXT_RUN.md ## Last Run section with:
   - Date, tasks shipped (with commit hashes), tasks deferred with reason
   - Review findings summary (total findings, severities, auto-fixed count, iterations)
   - Any concerns flagged for human review
   - What's queued next
2. Verify new TODO items were added (cross-check): scan NEXT_RUN.md for every word "concern", "P2", "not verified", "no timeout", "left out", "deferred", "skipped". Each one must have a matching TODO item. If any are missing, add them now before continuing. Also confirm the observation category was filled — zero new items from a full implementation pass is a red flag, not a clean bill of health.
3. Confirm at least one unchecked item is tagged [next] for tomorrow's run. If none are tagged, tag the highest-value unchecked item now.
4. Commit NEXT_RUN.md update
5. Push the branch: git push -u origin claude/overnight-YYYY-MM-DD
6. Open a PR: gh pr create --title "Overnight build: YYYY-MM-DD" --body "<summary of tasks shipped, review findings, and any flagged concerns>" --base main
7. Run: bash bin/slack-report.sh "formatted report" (OK if it fails — no webhook URL means no-op)
