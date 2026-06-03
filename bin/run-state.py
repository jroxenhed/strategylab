#!/usr/bin/env python3
"""run-state.py — Read/update/validate externalized orchestrator run-state.

Tier 2 of the lean-context contract (see CLAUDE.local.md → "Subagent output
contract"). Each task's working memory lives in `.run/<task-id>/state.json` so
the orchestrator holds pointers + one small state file instead of accreting
every subagent payload in its context window.

Idempotent by design:
  - `init` never clobbers an existing state.json (use --force to reset).
  - `add-agent` dedups by (role, persona); re-running updates that row in place.
  - `add-target` / `verify` dedup paths.
  - `set` / `decide` are naturally idempotent (last write wins).

Guardrail (non-negotiable — see CLAUDE.local.md): state is updated only from
*verified reality*, never an agent's self-report. `validate` enforces this: any
agent `result` file or `verified_files` entry that does not exist on disk is a
hard error (exit 1), so a lying state.json fails loudly.

Usage:
  bin/run-state.py init F123 --title "short title" --tier B
  bin/run-state.py set F123 phase review
  bin/run-state.py add-target F123 backend/routes/bots.py frontend/src/App.tsx
  bin/run-state.py add-agent F123 --role reviewer --persona correctness \\
                   --result .run/F123/review-correctness.json --status ok \\
                   --headline "3 findings, 1 P1"
  bin/run-state.py add-finding F123 --id C-01 --severity P1
  bin/run-state.py decide F123 --finding C-01 --decision fix
  bin/run-state.py verify F123 backend/routes/bots.py
  bin/run-state.py show F123
  bin/run-state.py validate F123      # exit 1 on drift / schema error
"""
import argparse
import json
import sys
from pathlib import Path

RUN_DIR = Path(".run")
TIERS = {"A", "B", "C"}
PHASES = {"explore", "implement", "verify", "review", "synthesize", "fix", "done"}
STATUSES = {"ok", "blocked", "failed"}
SEVERITIES = {"P0", "P1", "P2"}
DECISIONS = {None, "fix", "defer"}
BUILDS = {None, "pass", "fail"}


def state_path(task: str) -> Path:
    return RUN_DIR / task / "state.json"


def load(task: str) -> dict:
    p = state_path(task)
    if not p.exists():
        sys.exit(f"error: no state file at {p} (run `init {task}` first)")
    return json.loads(p.read_text())


def save(task: str, state: dict) -> None:
    p = state_path(task)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n")


def skeleton(task: str, title: str, tier: str) -> dict:
    return {
        "task": task,
        "title": title or "",
        "tier": tier or "B",
        "phase": "explore",
        "plan": None,
        "targets": [],
        "agents": [],
        "open_findings": [],
        "decisions": None,
        "build": None,
        "verified_files": [],
    }


def cmd_init(a):
    p = state_path(a.task)
    if p.exists() and not a.force:
        # Idempotent: leave existing state untouched.
        print(f"exists: {p} (use --force to reset)")
        return
    save(a.task, skeleton(a.task, a.title, a.tier))
    print(f"wrote: {p}")


def cmd_show(a):
    print(json.dumps(load(a.task), indent=2))


def cmd_get(a):
    state = load(a.task)
    if a.key not in state:
        sys.exit(f"error: unknown key {a.key!r}")
    val = state[a.key]
    print(val if isinstance(val, str) else json.dumps(val))


SETTABLE = {"title", "tier", "phase", "plan", "decisions", "build"}


def cmd_set(a):
    state = load(a.task)
    if a.key not in SETTABLE:
        sys.exit(f"error: {a.key!r} not settable via `set` (settable: {sorted(SETTABLE)})")
    value = None if a.value in ("", "null") else a.value
    state[a.key] = value
    save(a.task, state)
    print(f"{a.key} = {value!r}")


def cmd_add_target(a):
    state = load(a.task)
    for path in a.paths:
        if path not in state["targets"]:
            state["targets"].append(path)
    save(a.task, state)
    print(f"targets: {state['targets']}")


def cmd_add_agent(a):
    state = load(a.task)
    row = {
        "role": a.role,
        "persona": a.persona,
        "result": a.result,
        "status": a.status,
        "headline": a.headline,
    }
    # Dedup by (role, persona) — re-running updates in place.
    for i, existing in enumerate(state["agents"]):
        if (existing.get("role"), existing.get("persona")) == (a.role, a.persona):
            state["agents"][i] = row
            break
    else:
        state["agents"].append(row)
    save(a.task, state)
    print(f"agent {a.role}/{a.persona}: {a.status} — {a.headline}")


def cmd_add_finding(a):
    state = load(a.task)
    for f in state["open_findings"]:
        if f.get("id") == a.id:
            f["severity"] = a.severity
            f["decision"] = a.decision
            break
    else:
        state["open_findings"].append({"id": a.id, "severity": a.severity, "decision": a.decision})
    save(a.task, state)
    print(f"finding {a.id}: {a.severity} (decision={a.decision})")


def cmd_decide(a):
    state = load(a.task)
    for f in state["open_findings"]:
        if f.get("id") == a.finding:
            f["decision"] = a.decision
            save(a.task, state)
            print(f"finding {a.finding}: decision={a.decision}")
            return
    sys.exit(f"error: no finding {a.finding!r}")


def cmd_verify(a):
    state = load(a.task)
    for path in a.files:
        if path not in state["verified_files"]:
            state["verified_files"].append(path)
    save(a.task, state)
    print(f"verified_files: {state['verified_files']}")


def cmd_validate(a):
    state = load(a.task)
    errors = []

    for key in skeleton(a.task, "", "B"):
        if key not in state:
            errors.append(f"missing key: {key}")

    if state.get("tier") not in TIERS:
        errors.append(f"tier {state.get('tier')!r} not in {sorted(TIERS)}")
    if state.get("phase") not in PHASES:
        errors.append(f"phase {state.get('phase')!r} not in {sorted(PHASES)}")
    if state.get("build") not in BUILDS:
        errors.append(f"build {state.get('build')!r} not in {sorted(str(b) for b in BUILDS)}")

    for ag in state.get("agents", []):
        if ag.get("status") not in STATUSES:
            errors.append(f"agent {ag.get('role')}/{ag.get('persona')}: status {ag.get('status')!r} invalid")
        # Drift gate: a claimed result file must actually exist.
        res = ag.get("result")
        if res and not Path(res).exists():
            errors.append(f"DRIFT: agent {ag.get('role')}/{ag.get('persona')} result {res} does not exist on disk")

    for f in state.get("open_findings", []):
        if f.get("severity") not in SEVERITIES:
            errors.append(f"finding {f.get('id')}: severity {f.get('severity')!r} invalid")
        if f.get("decision") not in DECISIONS:
            errors.append(f"finding {f.get('id')}: decision {f.get('decision')!r} invalid")

    # Drift gate: every verified file must actually exist.
    for path in state.get("verified_files", []):
        if not Path(path).exists():
            errors.append(f"DRIFT: verified_files entry {path} does not exist on disk")

    if errors:
        print(f"INVALID ({len(errors)}):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: {a.task} valid (phase={state['phase']}, tier={state['tier']})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("task"); s.add_argument("--title", default=""); s.add_argument("--tier", default="B"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("show"); s.add_argument("task"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("get"); s.add_argument("task"); s.add_argument("key"); s.set_defaults(fn=cmd_get)
    s = sub.add_parser("set"); s.add_argument("task"); s.add_argument("key"); s.add_argument("value"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("add-target"); s.add_argument("task"); s.add_argument("paths", nargs="+"); s.set_defaults(fn=cmd_add_target)
    s = sub.add_parser("add-agent"); s.add_argument("task"); s.add_argument("--role", required=True); s.add_argument("--persona", default=""); s.add_argument("--result", default=None); s.add_argument("--status", required=True, choices=sorted(STATUSES)); s.add_argument("--headline", default=""); s.set_defaults(fn=cmd_add_agent)
    s = sub.add_parser("add-finding"); s.add_argument("task"); s.add_argument("--id", required=True); s.add_argument("--severity", required=True, choices=sorted(SEVERITIES)); s.add_argument("--decision", default=None, choices=["fix", "defer"]); s.set_defaults(fn=cmd_add_finding)
    s = sub.add_parser("decide"); s.add_argument("task"); s.add_argument("--finding", required=True); s.add_argument("--decision", required=True, choices=["fix", "defer"]); s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("verify"); s.add_argument("task"); s.add_argument("files", nargs="+"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("validate"); s.add_argument("task"); s.set_defaults(fn=cmd_validate)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
