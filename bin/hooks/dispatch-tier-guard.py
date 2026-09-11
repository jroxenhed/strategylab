#!/usr/bin/env python3
"""
dispatch-tier-guard.py — PreToolUse (Agent|Workflow matcher) tier check.

Front-ends subagent-dispatch rule 2 ("set `model` on EVERY dispatch") and rule 3
(effort as a label PREFIX) at the tool-call moment, because prose lost: the rule
stood in global CLAUDE.md, MEMORY.md and the skill, and a 21-agent workflow still
shipped untiered on 2026-08-28 — the built-in workflow-authoring reference loads
with ultracode and says "default to omitting model", and loaded text beat
recalled text. A rule that must fire at composition time cannot live only in
text (same lesson as deferral-count.py, one commit earlier).

WHAT FIRES
  - Agent tool: tool_input.model absent  -> deny. Exception: subagent_type
    "fork" (forks ignore model by design).
  - Workflow tool: every `agent(` call in the script must carry `model:` in its
    options, and a `label:` whose value starts with an effort prefix
    (low|/med|/high|/xhigh|/max|). Script comes from tool_input.script, or is
    read from tool_input.scriptPath. A name-only invocation (saved workflow) is
    allowed through — the script was linted when it was authored.

WHAT DOES NOT FIRE
  - Correct dispatches: zero output, zero friction (the anti-confirm-fatigue
    rule). The guard makes "forgot to choose" impossible; whether the CHOSEN
    tier is right stays a judgment call and stays out of scope.

I/O CONTRACT (matches jack-delete-guard.py)
  stdin  = PreToolUse JSON; reads tool_name + tool_input.
  violation -> stdout = {"hookSpecificOutput":{"hookEventName":"PreToolUse",
              "permissionDecision":"deny","permissionDecisionReason":"<what is
              missing, per call site> + the tiering rubric"}}; exit 0.
  clean  -> print nothing; exit 0.

CRASH CONTRACT
  Any internal error -> "ask" with a confirm-manually reason, never a silent
  allow and never a hard deny on the hook's own bug
  (feedback_guards_fail_loud_not_silent).

SCOPE LIMITS (deliberate)
  - The `agent(` scan is textual with balanced-paren spans, not a JS parse. An
    `agent(` inside a prompt STRING can in principle false-positive; the deny
    text names the offending span so the fix (or the false alarm) is obvious.
    Erring toward firing is the safe direction here.
  - opts built dynamically ({...base, model} or a variable) are invisible to a
    textual scan; spell model/label literally at each call site — that is the
    convention the guard exists to enforce, not a workaround.

stdlib-only. Python 3.9+.
"""

import json
import re
import sys

EFFORT_PREFIX_RE = re.compile(r'^(low|med|high|xhigh|max)\|')
AGENT_CALL_RE = re.compile(r'\bagent\s*\(')

RUBRIC = (
    "tiering rubric — haiku: mechanical reading; sonnet: bulk computation and "
    "scoped single-claim checks; opus: open-ended judgment (the default); "
    "effort rides the label as a prefix (`med|verify:...`). "
    "Full rules: subagent-dispatch skill."
)


def balanced_span(text, start):
    """Return text of the (...) span whose opening paren is at `start`."""
    depth = 0
    in_str = None
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_str:
            if ch == in_str:
                in_str = None
            continue
        if ch in "'\"`":
            in_str = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]  # unterminated: return the tail, caller still lints it


def lint_workflow_script(script):
    """Return a list of violation strings for agent() calls in a script."""
    problems = []
    for n, m in enumerate(AGENT_CALL_RE.finditer(script), start=1):
        span = balanced_span(script, m.end() - 1)
        head = " ".join(span[:90].split())
        if "model:" not in span:
            problems.append(
                "agent() call #{} has no `model:` ({}...)".format(n, head))
        label_m = re.search(r'label:\s*[\'"`]([^\'"`]*)', span)
        if label_m is None:
            problems.append(
                "agent() call #{} has no `label:` — labels carry the effort "
                "prefix ({}...)".format(n, head))
        elif not EFFORT_PREFIX_RE.match(label_m.group(1)):
            problems.append(
                "agent() call #{} label '{}' lacks the effort prefix "
                "low|/med|/high|/xhigh|/max|".format(n, label_m.group(1)))
    return problems


def evaluate(tool_name, tool_input):
    """Return a deny reason, or None when the dispatch is clean."""
    if tool_name == "Agent":
        if tool_input.get("subagent_type") == "fork":
            return None  # forks always inherit; model is ignored by design
        if not tool_input.get("model"):
            return ("Agent dispatch without `model` — subagent-dispatch rule 2: "
                    "set the tier explicitly on every agent. " + RUBRIC)
        return None

    if tool_name == "Workflow":
        script = tool_input.get("script")
        if not script:
            path = tool_input.get("scriptPath")
            if path:
                try:
                    with open(path, encoding="utf-8") as fh:
                        script = fh.read()
                except OSError:
                    return None  # unreadable path fails in the tool itself
        if not script:
            return None  # saved-workflow-by-name: linted at authoring time
        problems = lint_workflow_script(script)
        if problems:
            shown = problems[:6]
            more = len(problems) - len(shown)
            if more > 0:
                shown.append("(+{} more)".format(more))
            return ("Workflow script fails the tier check: " +
                    "; ".join(shown) + ". " + RUBRIC)
        return None

    return None


def emit(decision, reason):
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, separators=(",", ":")))


def main():
    try:
        obj = json.loads(sys.stdin.read())
        tool_name = obj.get("tool_name") or ""
        tool_input = obj.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        reason = evaluate(tool_name, tool_input)
        if reason is not None:
            emit("deny", reason)
        return 0
    except Exception:
        emit("ask", "dispatch-tier-guard errored — confirm the dispatch is "
             "tiered per the subagent-dispatch skill")
        return 0


if __name__ == "__main__":
    sys.exit(main())
