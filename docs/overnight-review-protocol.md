# Overnight Builder Review Protocol

Single-pass self-review protocol for the overnight builder. No subagents available — this protocol compensates by being structured and exhaustive. Read this file before reviewing your own changes.

## How to Use

After implementing a feature and before committing:
1. Run through each review pass below **in order**
2. For each pass, re-read every changed file with that lens
3. Log findings using the output format
4. Fix anything classified as `safe_auto`
5. If any P0 findings: commit to a separate branch, do NOT push to main

The passes are ordered from highest-value (catches real bugs) to lowest (catches style issues). Don't skip passes — the `fetch()` vs `api` bug that shipped in the first overnight build would have been caught by Pass 3 (Project Standards).

## Output Format

After all passes, write a summary in this format (as a comment in the commit message or in NEXT_RUN.md):

```
Review: X findings (P0: N, P1: N, P2: N), Y auto-fixed
```

For P1+ findings, include one-line descriptions. This gives the human reviewer a quick signal of review quality.

## Pass 1: Correctness (re-read every changed file)

Ask these questions for every function/block you wrote:

- **Off-by-one**: Are loop bounds correct? Is `>=` vs `>` right? Array index 0 vs 1?
- **Null/undefined paths**: What happens if the data is empty, null, or missing a field? Does `?.` or a guard exist where needed?
- **State management**: Does React state update correctly? Are `useState` initial values right? Do `useEffect` dependency arrays include all referenced variables?
- **Edge cases**: What if there are 0 trades? 1 trade? 10,000 trades? Does the code handle the boundaries the UI gates don't cover?
- **Logic inversion**: Is the conditional checking what you think? `&&` vs `||`, `>` vs `<`, `true` vs `false`?
- **Async correctness**: Are promises awaited? Can race conditions occur between concurrent calls? Is loading state reset in `finally`?
- **Type mismatches**: Are numbers being compared to strings? Is a value that could be `undefined` being used without a check?

## Pass 2: Integration (how your code connects to existing code)

- **Import paths**: Do all imports resolve? Are you importing from the right module?
- **API contract**: Does the frontend send exactly what the backend expects? Field names, types, nesting?
- **API client**: All frontend HTTP calls MUST use `import { api } from '../../api/client'` (axios, baseURL `http://localhost:8000`). NEVER use raw `fetch('/api/...')` — it hits the Vite dev server in development and silently fails. This is the #1 pitfall from past builds.
- **Response handling**: Does the frontend handle the actual response shape the backend returns? Check field names match between Pydantic model and TypeScript type.
- **Props threading**: Are all required props passed to components? Does the parent have the data the child expects?
- **Type definitions**: If you added a new type to `shared/types/index.ts`, did you export it? If the backend returns new fields, did you add them to the TypeScript type?

## Pass 3: Project Standards (re-read CLAUDE.md, check each rule)

Go through this checklist against every changed file:

- [ ] **Timezone**: All unix timestamps displayed to the user pass through `toET()` or `toDisplayTime()`. Daily date strings pass through unchanged.
- [ ] **priceScaleId**: Any new chart series has an explicit `priceScaleId` (v5 creates independent scales without one).
- [ ] **yf.download()**: Never used — always `yf.Ticker(symbol).history()` via `_fetch()`.
- [ ] **Fire-and-forget**: Non-critical async side-effects use `asyncio.create_task()`, never `await` in polling loops.
- [ ] **Sync callbacks**: Callbacks from non-asyncio threads use `asyncio.run_coroutine_threadsafe(coro, self._loop)`, not `ensure_future`.
- [ ] **Bot ID tagging**: All `_log_trade()` calls include `bot_id`. All `compute_realized_pnl()` calls filter by `bot_id`.
- [ ] **Chart teardown**: Refs nulled before `chart.remove()`. `syncWidths` reads refs dynamically. Try/catch around sibling chart operations.
- [ ] **Build command**: Verified with `npm run build` (runs `tsc -b`), NOT `tsc --noEmit`.

## Pass 4: Completeness (does the code do what the TODO says?)

- Re-read the TODO item description. Does the implementation match the spec?
- Are there UI elements described in the TODO that you didn't build?
- Are there backend fields that the frontend ignores or vice versa?
- Does the feature gate correctly (e.g., "visible when ≥3 trades" — is that check present)?
- Is loading state shown while async work happens?
- Is error state handled (not just swallowed silently)?

## Pass 5: Robustness

- **Division by zero**: Any arithmetic with user-derived denominators (trade count, price, percentage)?
- **Large data**: Will this work with 1,000 trades? 10,000? Does it create unnecessary copies of large arrays?
- **Memory**: Are there event listeners or subscriptions that need cleanup? `useEffect` return functions?
- **Re-render safety**: Will this cause infinite re-renders? (e.g., creating new objects/arrays in render that trigger `useEffect`)
- **SVG/chart rendering**: Does the chart handle empty data gracefully? Zero-height? Missing labels?

## Severity Scale

| Level | Meaning | Action |
|-------|---------|--------|
| P0 | Critical breakage, data loss, security hole | Must fix. Commit to separate branch if uncertain. |
| P1 | Bug hit in normal usage | Fix before push |
| P2 | Edge case, perf issue, maintainability trap | Fix if straightforward, otherwise note in PR |
| P3 | Minor, cosmetic | Defer |

## Fix-Review Iteration Loop

Fixes can introduce new bugs. After applying safe_auto fixes:

1. Re-run `npm run build`. If it fails, fix and rebuild.
2. Re-run **Pass 1 (Correctness)** and **Pass 2 (Integration)** on the lines you just changed. Only the changed lines — don't re-review the entire file.
3. If new findings emerge, fix them and repeat from step 1.
4. **Max 2 iterations.** If you're still finding issues after 2 fix-review cycles, stop — flag the remaining findings in NEXT_RUN.md for human review rather than risk an infinite loop of cascading fixes.

The goal: every line of code that ships has been reviewed at least once after its final edit.

## Autofix Rules

- **safe_auto**: Fix immediately. Local, deterministic, no behavior change for correct inputs. Examples: missing null check, wrong import path, missing type export.
- **gated_auto**: Fix exists but changes API contract or user-visible behavior. Flag in commit message.
- **manual**: Needs design decision. Note in NEXT_RUN.md for human review.

## Known Pitfalls (accumulated from past builds)

These are real bugs that shipped. Check for them explicitly:

1. **Raw `fetch()` instead of `api` client** — P1, silent failure in dev. Use `api.get()` / `api.post()` from `frontend/src/api/client.ts`.
2. **Silent error swallowing** — `if (res.ok) setResult(data)` with no else means the user sees nothing on failure. At minimum, reset loading state in `finally`. Prefer showing an error message.
3. **Identical percentile values** — When computing percentiles across simulations, verify the shuffle actually produces different sequences. If all percentiles are identical, the randomization or aggregation has a bug.

As new pitfalls are discovered, add them here so future builds check for them.
