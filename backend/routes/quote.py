from fastapi import APIRouter, HTTPException
from shared import _fetch

router = APIRouter()


@router.get("/api/quote/{ticker}")
def get_quote(ticker: str, source: str = "yahoo"):
    """Return latest price + daily change % for a single ticker."""
    from datetime import date, timedelta

    ticker = ticker.strip().upper()
    if not ticker or len(ticker) > 20:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

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
    results = []
    for sym in symbols[:20]:  # cap at 20 to prevent abuse
        sym = sym.strip().upper()
        if not sym or len(sym) > 20:
            results.append({"symbol": sym, "price": None, "change_pct": None})
            continue
        try:
            q = get_quote(sym, source)
            results.append(q)
        except Exception:
            results.append({"symbol": sym, "price": None, "change_pct": None})
    return results
