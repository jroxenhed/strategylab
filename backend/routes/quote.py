import re

from fastapi import APIRouter, HTTPException
from shared import _fetch, get_available_providers
from models import normalize_symbol

# Echo-back sanitizer for invalid batch-quote symbols: keep only allowlist chars
# so a JSON consumer that decodes and forwards the response can't propagate
# null bytes / control chars an attacker embedded in the request payload.
_DISPLAY_CLEAN = re.compile(r"[^A-Z0-9.\-]")

router = APIRouter()


@router.get("/api/quote/{ticker}")
def get_quote(ticker: str, source: str = "yahoo"):
    """Return latest price + daily change % for a single ticker."""
    from datetime import date, timedelta

    try:
        ticker = normalize_symbol(ticker)
    except ValueError:
        # `from None` suppresses Python's implicit __context__ chaining so the
        # original ValueError doesn't ride into structured log sinks.
        raise HTTPException(status_code=400, detail="Invalid ticker symbol") from None

    # F37: validate source at the route boundary so unknown providers can't be
    # silently swallowed by callers that catch broad Exception (e.g. get_quotes),
    # which would leak provider-registration state via timing/error differentials.
    if source not in get_available_providers():
        raise HTTPException(status_code=400, detail="Invalid source")

    today = date.today().isoformat()
    # Fetch last 5 trading days of daily data — enough to get prev close
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/quotes")
def get_quotes(symbols: list[str], source: str = "yahoo"):
    """Batch quote endpoint — returns quotes for multiple symbols."""
    # F37: same boundary validation as the single-ticker route. Reject up front
    # so the per-symbol HTTPException catch can't quietly turn an unknown source
    # into 20 "no data" rows (provider enumeration vector). Note: get_quote()
    # below also validates source — that inner check is dead code under this
    # call path, kept as defense-in-depth so a future direct caller of get_quote
    # can't bypass it.
    if source not in get_available_providers():
        raise HTTPException(status_code=400, detail="Invalid source")
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
            q = get_quote(normalized, source)
            results.append(q)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail) if e.detail else None
            results.append({"symbol": normalized, "price": None, "change_pct": None, "error": detail or "no data"})
    return results
