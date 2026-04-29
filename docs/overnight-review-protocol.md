# Overnight Builder Review Protocol

Distilled from ce:review's methodology. The overnight builder reads this file and includes it in review agent prompts.

## Output Format

Return JSON:
```json
{
  "reviewer": "correctness",
  "findings": [
    {
      "title": "Short issue title (10 words max)",
      "severity": "P0|P1|P2|P3",
      "file": "relative/path.tsx",
      "line": 42,
      "confidence": 0.85,
      "autofix_class": "safe_auto|gated_auto|manual|advisory",
      "suggested_fix": "Concrete fix or null",
      "pre_existing": false
    }
  ],
  "residual_risks": ["..."],
  "testing_gaps": ["..."]
}
```

## Severity Scale

| Level | Meaning | Action |
|-------|---------|--------|
| P0 | Critical breakage, data loss, exploitable vulnerability | Must fix before push |
| P1 | High-impact defect hit in normal usage | Should fix |
| P2 | Moderate issue (edge case, perf, maintainability trap) | Fix if straightforward |
| P3 | Low-impact, minor | Defer |

## Confidence Gate

- Below 0.60: Do NOT report. Too speculative.
- 0.60-0.69: Only if clearly actionable with evidence.
- 0.70-0.84: Real and important. Report.
- 0.85-1.00: Certain. Report.
- Exception: P0 at 0.50+ survives the gate.

## Autofix Classification

- **safe_auto**: Local, deterministic fix. Examples: missing null check, off-by-one, dead code removal, extract duplicated helper. The fixer applies these automatically.
- **gated_auto**: Fix exists but changes contracts/behavior/permissions. Commit but do NOT push — flag for human review.
- **manual**: Needs design decisions. Flag in report, don't fix.
- **advisory**: Informational only. No code action.

## False-Positive Suppression

Do NOT flag:
- Pre-existing issues unrelated to this diff
- Style nitpicks a linter/formatter would catch
- Code that looks wrong but is intentional (check Key Bugs Fixed in CLAUDE.md)
- Issues already handled elsewhere (check callers, guards, middleware)
- Generic "consider adding" without a concrete failure mode
- Suggestions that restate what the code already does

## Diff Scope Tiers

- **Primary**: Lines added or modified. Main focus. Full confidence.
- **Secondary**: Unchanged code in the same function that interacts with changes. Report if the change creates the bug.
- **Pre-existing**: Issues in unchanged, unrelated code. Mark `pre_existing: true`. Don't count toward verdict.

## Intent Verification

Compare code against the task description from TODO.md. If the code does something the task doesn't describe, or fails to do what the task promises, flag it. Mismatches between intent and implementation are the highest-value findings.

## Review Personas

Dispatch these as parallel agents. Each gets the diff, file list, intent, and this protocol.

### Always-on (every review):

**Correctness** — Logic errors, edge cases, state management bugs, error propagation failures, off-by-ones, null/undefined paths, race conditions.

**Maintainability** — Dead code, coupling between unrelated modules, duplicated logic, naming that obscures intent, premature abstraction, unnecessary indirection.

**Project Standards** — Read CLAUDE.md. Check: timezone handling (toET/toDisplayTime on all timestamps), priceScaleId rules, Key Bugs Fixed patterns (yf.download, create_task not await, run_coroutine_threadsafe, bot_id tagging). Flag violations of any documented pattern.

### Conditional (add when relevant):

**Security** — When diff touches endpoints, user input, external API calls, auth, env vars.

**Reliability** — When diff touches async code, error handling, retries, polling loops, external service calls.

**TypeScript** — When diff touches .ts/.tsx. Type safety, React hooks (dependency arrays, cleanup), unsafe casts, proper use of lightweight-charts API.

**Python** — When diff touches .py. Type hints, async patterns, Pythonic idioms, proper exception handling.

## Synthesis Rules

After all reviewers return:
1. Deduplicate: same file + line ±3 + similar title = merge, keep highest severity/confidence
2. Cross-reviewer agreement: 2+ reviewers flag same issue → boost confidence by 0.10
3. Route: safe_auto → fixer queue. gated_auto/P0 → commit but don't push. advisory → report only.
4. Sort: P0 first → confidence desc → file → line
