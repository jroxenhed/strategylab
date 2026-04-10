"""Trade journal logger — appends trades to a JSON file."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
JOURNAL_PATH = DATA_DIR / "trade_journal.json"


def _log_trade(symbol: str, side: str, qty: float, price: float | None,
               source: str, stop_loss_price: float | None = None,
               reason: str | None = None, expected_price: float | None = None,
               direction: str = "long"):
    """Append a trade entry to the journal."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if JOURNAL_PATH.exists():
        journal = json.loads(JOURNAL_PATH.read_text())
    else:
        journal = {"trades": []}
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
        "direction": direction,
    })
    JOURNAL_PATH.write_text(json.dumps(journal, indent=2))
