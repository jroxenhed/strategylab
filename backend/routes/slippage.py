"""GET /api/slippage/{symbol} — empirical slippage from trade journal."""
import json
from fastapi import APIRouter
from journal import JOURNAL_PATH

router = APIRouter()

_WORSE_IF_FILL_IS = {
    "buy": "above",
    "cover": "above",
    "sell": "below",
    "short": "below",
}


def _signed_slippage_pct(side: str, price: float, expected: float) -> float | None:
    direction = _WORSE_IF_FILL_IS.get(side)
    if direction is None or expected is None or expected == 0:
        return None
    raw_pct = (price - expected) / expected * 100
    return raw_pct if direction == "above" else -raw_pct


@router.get("/api/slippage/{symbol}")
def get_empirical_slippage(symbol: str):
    if not JOURNAL_PATH.exists():
        return {"empirical_pct": None, "fill_count": 0}
    try:
        trades = json.loads(JOURNAL_PATH.read_text()).get("trades", [])
    except (json.JSONDecodeError, OSError):
        return {"empirical_pct": None, "fill_count": 0}

    values: list[float] = []
    sym_u = symbol.upper()
    for t in trades:
        if t.get("symbol", "").upper() != sym_u:
            continue
        price = t.get("price")
        expected = t.get("expected_price")
        side = t.get("side")
        if price is None or expected is None:
            continue
        slip = _signed_slippage_pct(side, price, expected)
        if slip is None:
            continue
        values.append(slip)

    if not values:
        return {"empirical_pct": None, "fill_count": 0}
    return {
        "empirical_pct": round(sum(values) / len(values), 4),
        "fill_count": len(values),
    }
