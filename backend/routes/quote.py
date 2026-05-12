import re
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared import _fetch, require_valid_source
from models import normalize_symbol

logger = logging.getLogger(__name__)

# Echo-back sanitizer for invalid batch-quote symbols: keep only allowlist chars
# so a JSON consumer that decodes and forwards the response can't propagate
# null bytes / control chars an attacker embedded in the request payload.
_DISPLAY_CLEAN = re.compile(r"[^A-Z0-9.\-]")

# F98: machine-readable structured detail for all invalid-symbol rejections.
_INVALID_SYMBOL_DETAIL = {
    "error": "invalid_symbol",
    "reason": "must match ^[A-Z0-9][A-Z0-9.-]{0,19}$",
}


class QuoteResult(BaseModel):
    symbol: str
    price: float | None = None
    change_pct: float | None = None
    error: str | None = None


router = APIRouter()


def _fetch_quote(ticker: str, source: str) -> dict:
    """F96: core fetch logic — no HTTP-boundary validation. Caller must have already
    called require_valid_source(source) and normalize_symbol(ticker)."""
    from datetime import date, timedelta

    today = date.today().isoformat()
    start = (date.today() - timedelta(days=10)).isoformat()
    try:
        df = _fetch(ticker, start, today, "1d", source=source)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {ticker}")

        last = df.iloc[-1]
        price = round(float(last["Close"]), 2)

        if len(df) >= 2:
            prev_close = float(df.iloc[-2]["Close"])
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        else:
            change_pct = 0.0

        return {"symbol": ticker.upper(), "price": price, "change_pct": change_pct}
    except HTTPException:
        raise
    except Exception:
        logger.exception("_fetch_quote failed for %s [%s]", ticker, source)
        raise HTTPException(status_code=500, detail="quote fetch failed")


@router.get("/api/quote/{ticker}", responses={400: {"description": "Invalid source"}, 422: {"description": "Invalid ticker symbol"}})
def get_quote(ticker: str, source: str = "yahoo"):
    """Return latest price + daily change % for a single ticker."""
    try:
        ticker = normalize_symbol(ticker)
    except ValueError:
        # F98: structured 422 — no raw input echo, machine-readable error key.
        raise HTTPException(status_code=422, detail=_INVALID_SYMBOL_DETAIL) from None

    # F37/F94: shared allowlist + case-normalize at the route boundary.
    source = require_valid_source(source)

    return _fetch_quote(ticker, source)


@router.post("/api/quotes", response_model=list[QuoteResult], responses={400: {"description": "Invalid source"}})
def get_quotes(symbols: list[str], source: str = "yahoo") -> list[QuoteResult]:
    """Batch quote endpoint — returns quotes for multiple symbols."""
    # F37/F94: same boundary validation as the single-ticker route. Reject up
    # front so the per-symbol HTTPException catch can't quietly turn an unknown
    # source into 20 "no data" rows (provider enumeration vector).
    source = require_valid_source(source)
    results = []
    for sym in symbols[:20]:  # cap at 20 to prevent abuse
        try:
            normalized = normalize_symbol(sym)
        except ValueError:
            # Echo back the post-normalize candidate, truncated to 20 chars (F45)
            # and stripped of anything outside the allowlist so adversarial
            # control chars / null bytes can't ride into a downstream consumer
            # that decodes the JSON response and re-emits it as plain text.
            if isinstance(sym, str):
                display = _DISPLAY_CLEAN.sub("", sym.strip().upper())[:20]
            else:
                display = ""
            results.append({"symbol": display, "price": None, "change_pct": None, "error": "invalid symbol"})
            continue
        try:
            q = _fetch_quote(normalized, source)
            results.append(q)
        except HTTPException as e:
            if e.status_code == 404:
                detail = e.detail if isinstance(e.detail, str) else "no data"
            else:
                detail = "fetch failed"
            results.append({"symbol": normalized, "price": None, "change_pct": None, "error": detail})
    return results
