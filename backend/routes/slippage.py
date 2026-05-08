"""GET /api/slippage/{symbol} — modeled + measured slippage diagnostics.

All sign/unit convention lives in backend/slippage.py. This route only shapes
the response for the frontend.
"""
from fastapi import APIRouter

from slippage import decide_modeled_bps

router = APIRouter()


@router.get("/api/slippage/{symbol}")
def get_slippage(symbol: str):
    r = decide_modeled_bps(symbol)

    # Optionally include live spread from broker (Alpaca/IBKR).
    # Returns None values when broker is unavailable (e.g., market closed,
    # no broker configured, or Yahoo data source).
    live_spread_bps = None
    half_spread_bps = None
    try:
        from broker import get_trading_provider, AlpacaTradingProvider, IBKRTradingProvider
        provider = None
        try:
            p = get_trading_provider("ibkr")
            if isinstance(p, IBKRTradingProvider):
                provider = p
        except Exception:
            pass
        if provider is None:
            p = get_trading_provider()
            if isinstance(p, AlpacaTradingProvider):
                raise ValueError("IEX-only quotes, not NBBO")
            provider = p
        bid, ask = provider.get_latest_quote(symbol.upper())
        if bid > 0 and ask > bid:
            mid = (bid + ask) / 2.0
            spread_bps = (ask - bid) / mid * 1e4
            live_spread_bps = round(spread_bps, 2)
            half_spread_bps = round(spread_bps / 2.0, 2)
    except Exception:
        pass

    return {
        "modeled_bps":      round(r.modeled_bps, 2),
        "measured_bps":     None if r.measured_bps  is None else round(r.measured_bps,  2),
        "fill_bias_bps":    None if r.fill_bias_bps is None else round(r.fill_bias_bps, 2),
        "fill_count":       r.fill_count,
        "source":           r.source,
        "live_spread_bps":  live_spread_bps,
        "half_spread_bps":  half_spread_bps,
    }
