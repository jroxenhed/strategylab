#!/usr/bin/env python3
"""SubagentStop hook — append per-subagent usage telemetry to
.run/current-session-usage.jsonl.

Receives the SubagentStop JSON payload on stdin. Parses the transcript JSONL
at payload.transcript_path to sum token usage from assistant-turn entries.
Appends one JSON line to $CLAUDE_PROJECT_DIR/.run/current-session-usage.jsonl.

Always exits 0 — a crash here must never block a subagent from stopping.
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _sum_transcript(transcript_path: str) -> Optional[tuple[int, int, int]]:
    """Return (input_tokens, output_tokens, tool_use_count) by scanning the
    transcript JSONL line-by-line (constant memory, safe for multi-MB files).

    Returns None when transcript_path is empty/missing/unreadable — None means
    'data not available' and the caller should write null fields. Zeros are only
    returned when the transcript was read successfully and genuinely empty.

    Tool-use counting: count tool_use blocks ONLY from content arrays of
    assistant-turn entries. Top-level entries with type==tool_use are NOT
    counted separately — they appear as blocks inside content arrays of the
    enclosing assistant message in all observed transcript shapes, and counting
    both would double-count.
    """
    if not transcript_path:
        return None
    try:
        p = Path(transcript_path)
        if not p.exists():
            return None
        input_tok = output_tok = tool_uses = 0
        with p.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Assistant turn — extract usage block.
                # Two observed shapes:
                #   {"type": "assistant", "message": {"usage": {...}, "content": [...]}}
                #   {"role": "assistant", "usage": {...}, "content": [...]}
                usage = None
                is_assistant = False
                if entry.get("type") == "assistant":
                    msg = entry.get("message") or {}
                    usage = msg.get("usage")
                    is_assistant = True
                elif entry.get("role") == "assistant":
                    usage = entry.get("usage")
                    is_assistant = True
                if isinstance(usage, dict):
                    input_tok += usage.get("input_tokens", 0) or 0
                    output_tok += usage.get("output_tokens", 0) or 0
                # Count tool_use blocks from content arrays of assistant entries only.
                # Explicit None check: an empty content list [] is falsy but valid —
                # don't fall through to message.content in that case.
                if is_assistant:
                    if entry.get("type") == "assistant":
                        msg = entry.get("message") or {}
                        content = msg.get("content")
                    else:
                        content = entry.get("content")
                    if content is None:
                        content = (entry.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        content = []
                    tool_uses += sum(
                        1 for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    )
    except Exception:
        return None
    return input_tok, output_tok, tool_uses


def main() -> None:
    # Read stdin payload
    try:
        raw_payload = sys.stdin.read()
        payload = json.loads(raw_payload)
    except Exception:
        payload = {}

    agent_id = payload.get("agent_id") or "unknown"
    agent_type = payload.get("agent_type") or "unknown"
    session_id = payload.get("session_id") or "unknown"
    transcript_path = payload.get("transcript_path") or ""

    # Sum tokens from transcript.
    # None result means transcript was unavailable — write null fields so zeros
    # are unambiguous (zeros == transcript read fine but genuinely empty).
    result = _sum_transcript(transcript_path)
    if result is None:
        input_tok = output_tok = tool_uses = None
        total_tokens = None
    else:
        input_tok, output_tok, tool_uses = result
        total_tokens = input_tok + output_tok

    # Build output record
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "tokens": total_tokens,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "tool_uses": tool_uses,
        "duration_ms": None,  # not available from SubagentStop payload
        "timestamp": timestamp,
        "session_id": session_id,
    }

    # Resolve output path from CLAUDE_PROJECT_DIR env var; fall back to script-
    # relative repo root so the hook works even when the env var is absent.
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(
        Path(__file__).resolve().parent.parent.parent
    )
    out_path = Path(project_dir) / ".run" / "current-session-usage.jsonl"

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            # Advisory exclusive lock: serializes concurrent SubagentStop hook
            # processes so their writes don't interleave and corrupt lines.
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(record) + "\n")
            # Lock released automatically on close.
    except Exception:
        pass  # never crash — this is a fire-and-forget logger


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # unconditionally swallow all exceptions
    sys.exit(0)
