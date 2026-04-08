from fastapi import APIRouter
import yfinance as yf

router = APIRouter()


@router.get("/api/search")
def search_ticker(q: str):
    try:
        results = yf.Search(q, max_results=8)
        quotes = results.quotes if results.quotes else []
        return [
            {
                "symbol": r.get("symbol", ""),
                "name": r.get("longname") or r.get("shortname") or r.get("symbol", ""),
                "type": r.get("quoteType", ""),
            }
            for r in quotes
            if r.get("symbol")
        ]
    except Exception:
        return []
