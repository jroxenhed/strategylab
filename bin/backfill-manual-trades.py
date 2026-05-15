#!/usr/bin/env python3
"""One-shot: backfill broker + bot_id on existing manual journal rows.

Pairs each manual SELL/COVER with the most-recent unconsumed entry on the same
(symbol, direction) and copies that entry's bot_id onto the exit. Sets broker
on any manual row missing it (defaults to "alpaca", the only broker historically
registered for manual flows). Writes a .bak alongside the journal.
"""
import json
import shutil
import sys
from pathlib import Path

JOURNAL = Path("/Users/jroxenhed/Documents/strategylab/backend/data/trade_journal.json")
DEFAULT_BROKER = "alpaca"


def main(dry_run: bool):
    data = json.loads(JOURNAL.read_text())
    trades = data["trades"]
    trades_sorted = sorted(trades, key=lambda t: t.get("timestamp") or "")

    # Per (symbol, direction) stack of {bot_id, qty} entries awaiting an exit.
    stack: dict[tuple[str, str], list[dict]] = {}
    broker_set = 0
    bot_tagged = 0

    for t in trades_sorted:
        side = t.get("side")
        sym = (t.get("symbol") or "").upper()
        if not sym or not side:
            continue
        direction = "long" if side in ("buy", "sell") else "short"
        key = (sym, direction)
        is_entry = side in ("buy", "short")
        is_exit = side in ("sell", "cover")

        if is_entry:
            stack.setdefault(key, []).append({
                "bot_id": t.get("bot_id"),
                "qty": abs(t.get("qty") or 0),
            })
            continue

        if not is_exit:
            continue

        # Backfill broker on any manual row missing it.
        if t.get("source") == "manual" and not t.get("broker"):
            t["broker"] = DEFAULT_BROKER
            broker_set += 1

        # Pop matching entry for pairing.
        entries = stack.get(key) or []
        if not entries:
            continue
        entry = entries.pop()

        # If this exit is manual and untagged, inherit the entry's bot_id.
        if t.get("source") == "manual" and not t.get("bot_id") and entry["bot_id"]:
            t["bot_id"] = entry["bot_id"]
            bot_tagged += 1

    print(f"manual rows broker-backfilled: {broker_set}")
    print(f"manual exits bot_id-backfilled: {bot_tagged}")

    if dry_run:
        print("dry-run; no write")
        return

    backup = JOURNAL.with_suffix(JOURNAL.suffix + ".bak")
    shutil.copy2(JOURNAL, backup)
    print(f"backup: {backup}")
    JOURNAL.write_text(json.dumps(data, indent=2))
    print(f"wrote: {JOURNAL}")


if __name__ == "__main__":
    main(dry_run="--apply" not in sys.argv)
