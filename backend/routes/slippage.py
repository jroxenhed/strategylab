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
    return {
        "modeled_bps":    round(r.modeled_bps, 2),
        "measured_bps":   None if r.measured_bps  is None else round(r.measured_bps,  2),
        "fill_bias_bps":  None if r.fill_bias_bps is None else round(r.fill_bias_bps, 2),
        "fill_count":     r.fill_count,
        "source":         r.source,
    }
