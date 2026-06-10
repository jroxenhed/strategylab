---
name: review-wave
description: >
  Severity-tiered persona review wave with adversarial verification (F362).
  Runs Review (one agent per persona, findings to .run/<taskId>/) then adversarial
  Verify (refutation per P0/P1 against disk). Use after a build passes its gate.
---

# review-wave — Structured Multi-Persona Code Review

Wraps `.claude/workflows/review-wave.js`: parallel persona review + adversarial P0/P1 verification.

## IMPORTANT: You MUST invoke via the Workflow tool with a structured JSON args object

**DO NOT pass prose into the `args` field.** The workflow calls `JSON.parse(args)` and will throw
`JSON Parse error: Unexpected identifier` if it receives anything other than a JSON object.

## Invoke via the Workflow tool — exact form

```
Workflow({ name: "review-wave", args: {
  "taskId":   "F123",
  "files":    ["/abs/path/a.py", "/abs/path/b.sh"],
  "intent":   "One paragraph: what changed + what to confirm + scope caveats.",
  "personas": [
    { "key": "correctness", "prompt": "Look for logic errors, edge cases, state bugs, error propagation." },
    { "key": "kieran-python", "prompt": "Python style, idioms, error handling, type annotations." }
  ]
}})
```

All four keys (`taskId`, `files`, `intent`, `personas`) are required — missing any one throws
`review-wave needs args {taskId, files, intent, personas}`.

## Files must be absolute paths

Agents do not inherit a working directory. Always use absolute paths, e.g.:
`/Users/jroxenhed/Documents/strategylab/backend/routes/backtest.py`

## Post-wave telemetry (F302 / F362)

After the wave completes, record it in run-state as a single synthetic row (Option A — wave-level aggregate):

```bash
python3 bin/run-state.py add-agent F123 \
  --role review --persona wave \
  --tokens <tokens_spent from return value> \
  --status ok \
  --headline "4 personas, N confirmed P0/P1, M refuted"
# If any reviewer hit the effort cap, add: --cap-hit
```

The return value includes `tokens_spent` (wave-level aggregate). Per-persona token breakdown
is not available from the workflow engine — this is a known limitation (deferred: expose per-call
accounting in a future review-wave engine enhancement).

## Personas

**Tier B (1-2 personas):** `correctness` + one file-type persona (`kieran-python` for `.py`,
`kieran-typescript` for `.ts/.tsx`).

**Tier C (4-6 personas):** add `adversarial`, `security`, and conditionals
(`reliability`, `project-standards`, `maintainability`) per diff content.

Full tier classification table and persona roster:
`docs/overnight-builder-guide.md` §4.

## Effort cap (F302)

Include in every persona's `prompt` field:

```
EFFORT CAP: ≤25 tool calls / ~3 minutes. If you hit the cap, return partial findings
(all severities so far) + include the string "cap_hit" in your response.
P0/P1 findings are mandatory before the cap; deliver those first.
```

If a reviewer returns `cap_hit`, pass `--cap-hit` when recording with `run-state.py add-agent`.
