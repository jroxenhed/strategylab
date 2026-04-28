"""Trade journal logger — appends trades to a JSON file."""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

_default_data = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.environ.get("STRATEGYLAB_DATA_DIR", str(_default_data)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
JOURNAL_PATH = DATA_DIR / "trade_journal.json"


def _load_trades() -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        return json.loads(JOURNAL_PATH.read_text()).get("trades", [])
    except (json.JSONDecodeError, OSError):
        return []


def compute_realized_pnl(symbol: str, direction: str = "long", bot_id: str | None = None,
                         since: str | None = None, *, trades: list[dict] | None = None) -> float:
    """Sum realized P&L for bot-sourced trades of a given (symbol, direction, bot_id).

    Pairs entries with exits in chronological order (LIFO: each exit consumes
    the most recent open entry). When `bot_id` is given, only trades tagged to
    that bot count — so deleting a bot and spinning up a new one on the same
    symbol doesn't inherit the old bot's realized P&L or skew sizing. When
    `since` (ISO timestamp) is given, only trades at or after it count —
    supports the "Reset P&L" affordance without deleting the bot.
    """
    if trades is None:
        trades = _load_trades()

    total = 0.0
    open_entry: dict | None = None  # {"price": float, "qty": float}
    for t in trades:
        if t.get("source") != "bot":
            continue
        if t.get("symbol", "").upper() != symbol.upper():
            continue
        if bot_id is not None and t.get("bot_id") != bot_id:
            continue
        if since is not None and (t.get("timestamp") or "") < since:
            continue
        side = t.get("side")
        price = t.get("price")
        qty = t.get("qty")
        if price is None or not qty:
            continue

        is_entry = side in ("buy", "short")
        row_dir = "long" if side in ("buy", "sell") else "short"
        if row_dir != direction:
            continue

        if is_entry:
            open_entry = {"price": price, "qty": qty}
        elif open_entry is not None:
            pair_qty = min(open_entry["qty"], qty)
            if direction == "short":
                total += (open_entry["price"] - price) * pair_qty
            else:
                total += (price - open_entry["price"]) * pair_qty
            open_entry = None

    return total


def compute_bot_avg_cost_bps(symbol: str, bot_id: str | None = None,
                             since: str | None = None, *, trades: list[dict] | None = None) -> tuple[float | None, int]:
    """Average of per-fill unsigned slippage cost for this bot's trades.

    Reads the journal's cached `slippage_bps` (computed at log time with the
    current helper), so stale values in `BotState.slippage_bps` can't contaminate.
    Returns (avg_bps, fill_count); avg is None when no qualifying rows.
    """
    if trades is None:
        trades = _load_trades()
    vals: list[float] = []
    for t in trades:
        if t.get("source") != "bot":
            continue
        if t.get("symbol", "").upper() != symbol.upper():
            continue
        if bot_id is not None and t.get("bot_id") != bot_id:
            continue
        if since is not None and (t.get("timestamp") or "") < since:
            continue
        s = t.get("slippage_bps")
        if s is None:
            continue
        vals.append(float(s))
    if not vals:
        return None, 0
    return round(sum(vals) / len(vals), 2), len(vals)


def first_bot_entry_time(symbol: str, direction: str = "long", bot_id: str | None = None,
                         since: str | None = None, *, trades: list[dict] | None = None) -> str | None:
    """Return ISO timestamp of the earliest bot entry for (symbol, direction, bot_id).

    Used so an open first position still contributes to the aligned sparkline
    window — equity snapshots are only written on exits.
    """
    if trades is None:
        trades = _load_trades()
    entry_sides = ("buy",) if direction == "long" else ("short",)
    for t in trades:
        if t.get("source") != "bot":
            continue
        if t.get("symbol", "").upper() != symbol.upper():
            continue
        if bot_id is not None and t.get("bot_id") != bot_id:
            continue
        if t.get("side") not in entry_sides:
            continue
        ts = t.get("timestamp")
        if ts:
            if since is not None and ts < since:
                continue
            return ts
    return None


def _log_trade(symbol: str, side: str, qty: float, price: float | None,
               source: str, stop_loss_price: float | None = None,
               reason: str | None = None, expected_price: float | None = None,
               direction: str = "long", bot_id: str | None = None,
               broker: str | None = None):
    """Append a trade entry to the journal.

    `bot_id` is required for bot-sourced trades so that P&L can be scoped to
    the specific bot (see `compute_realized_pnl`). Manual routes pass None.
    """
    if JOURNAL_PATH.exists():
        journal = json.loads(JOURNAL_PATH.read_text())
    else:
        journal = {"trades": []}
    from slippage import slippage_cost_bps  # lazy: slippage imports JOURNAL_PATH from us

    cost_bps: float | None = None
    if price is not None and expected_price is not None and side is not None:
        cost_bps = round(slippage_cost_bps(side, expected_price, price), 2)

    journal["trades"].append({
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "stop_loss_price": stop_loss_price,
        "source": source,
        "reason": reason,
        "expected_price": expected_price,
        "slippage_bps": cost_bps,
        "direction": direction,
        "bot_id": bot_id,
        "broker": broker,
    })
    JOURNAL_PATH.write_text(json.dumps(journal, indent=2))
