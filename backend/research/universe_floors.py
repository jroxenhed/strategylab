"""backend/research/universe_floors.py — UNIVERSE_V2 floor conformance helper.

ONE shared point-in-time floor check, used by BOTH:
  - candidate emission in the research configs (config_momentum, config_deterioration), AND
  - the harness's source-mode exhaustive null aggregates
    (turnaround_validation._compute_cohort_null_aggregates).

WHY a standalone module: turnaround_validation must not import the research
configs (the configs already import CandidateSourceConfig from the harness — a
back-import would be circular). This module has no project imports, so both the
harness and the configs can depend on it without a cycle.

WHY both paths: the signal cohort and its exhaustive null cohort MUST see the
SAME universe. If only candidate emission enforced the floors, the null
aggregates would still average in sub-$5 / illiquid / split-corrupt names and
the per-cohort excess (signal − null median) would be biased.

CONFORMANCE, not tuning: both charters (MOMENTUM-TEST, DETERIORATION-TEST)
pre-registered universe-v2 floors (min_price 5.0, min_avg_volume 500_000). The
floors live canonically in turnaround.UNIVERSE_V2; mirrored here as the module
constants so this helper has no project import. They are kept in sync by the
self-check at the bottom of this module (raises at import if they drift).

Floor definition (charter — FROZEN):
  min_price       — last close on-or-before as_of must be >= 5.0.
  min_avg_volume  — trailing 63-trading-day mean SHARE volume (rows <= as_of)
                    must be >= 500_000. (Charter says SHARE volume, NOT dollar
                    volume — the mean of the raw Volume column.)

Frame-sanity guard (data hygiene, NOT outcome filtering):
  corrupt_frame   — any single-bar close-over-close ratio > 10x anywhere in the
                    trailing 252-trading-day window (rows <= as_of) is the
                    split-corruption signature (GXXM's $51M entry). Reads only
                    PRE-as_of bars, so it cannot condition on outcomes.

All evaluation is strictly from bars with index date <= as_of (no look-ahead).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Floor constants — mirror turnaround.UNIVERSE_V2 (kept in sync by _check below)
# ---------------------------------------------------------------------------

MIN_PRICE: float = 5.0
MIN_AVG_VOLUME: int = 500_000

# Trailing window (trading rows) for the average-volume floor.
_VOLUME_WINDOW = 63

# Trailing window (trading rows) scanned for the split-corruption guard.
_CORRUPT_WINDOW = 252

# Bar-over-bar close ratio above which a frame is treated as split-corrupt.
_CORRUPT_RATIO = 10.0

# Status tokens (also the counted exclusion reasons used by callers).
OK = "ok"
BELOW_FLOOR = "below_floor"
CORRUPT_FRAME = "corrupt_frame"


def _df_up_to(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Slice df to rows with index date <= as_of (point-in-time, no look-ahead).

    Mirrors turnaround._df_up_to / config _df_up_to (tz-aware index stripping).
    """
    if df is None or df.empty:
        return df
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is not None:
            df = df.copy()
            df.index = idx = idx.tz_localize(None)
        mask = idx.normalize() <= pd.Timestamp(as_of)
    elif hasattr(idx[0], "date"):
        mask = pd.to_datetime(idx).normalize() <= pd.Timestamp(as_of)
    else:
        mask = idx <= pd.Timestamp(as_of)
    return df[mask]


def precompute_df_up_to(df: pd.DataFrame) -> tuple[pd.DataFrame, "pd.Index"]:
    """Precompute the per-frame invariants of `_df_up_to` for hot loops.

    Returns (df_out, norm_idx) where `df_out` is the (tz-stripped if needed)
    frame and `norm_idx` is the normalized comparable index — exactly what
    `_df_up_to` derives per call.  F357 matrix builder: `_df_up_to`'s per-call
    `df.copy()` + tz-strip on tz-aware frames dominated build cost at
    2,608 calls/symbol.  Pass the result as `floor_status(..., pre=...)`;
    slicing semantics are identical by construction (same mask expression).
    """
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is not None:
            df = df.copy()
            df.index = idx = idx.tz_localize(None)
        return (df, idx.normalize())
    if len(idx) and hasattr(idx[0], "date"):
        return (df, pd.to_datetime(idx).normalize())
    return (df, idx)


def _get_close(df: pd.DataFrame) -> Optional[pd.Series]:
    for col in ("Close", "close", "Adj Close", "adj close"):
        if col in df.columns:
            return df[col]
    return None


def _get_volume(df: pd.DataFrame) -> Optional[pd.Series]:
    for col in ("Volume", "volume"):
        if col in df.columns:
            return df[col]
    return None


def floor_status(
    df: Optional[pd.DataFrame],
    as_of: date,
    pre: Optional[tuple[pd.DataFrame, "pd.Index"]] = None,
) -> str:
    """Return the UNIVERSE_V2 floor status for `df` evaluated at `as_of`.

    One of:
      "ok"            — passes the price + average-volume floors and the
                        split-corruption guard.
      "below_floor"   — last close < MIN_PRICE OR trailing-63td mean volume
                        < MIN_AVG_VOLUME (also returned for empty / no-data
                        frames so such names are excluded, never imputed).
      "corrupt_frame" — a > _CORRUPT_RATIO single-bar close ratio anywhere in the
                        trailing _CORRUPT_WINDOW rows (split-corruption signature).

    The corrupt-frame guard is checked FIRST: a split-corrupt frame's price/volume
    cannot be trusted, so it is reported as corrupt_frame rather than below_floor.

    All evaluation reads only rows with index date <= as_of.

    `pre` (optional): result of `precompute_df_up_to(df)` for the same df —
    hot loops pass it to skip `_df_up_to`'s per-call copy/tz-strip.
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return BELOW_FLOOR

    if pre is not None:
        df_out, norm_idx = pre
        sliced = df_out[norm_idx <= pd.Timestamp(as_of)]
    else:
        sliced = _df_up_to(df, as_of)
    if sliced is None or sliced.empty:
        return BELOW_FLOOR

    close = _get_close(sliced)
    if close is None or len(close) == 0:
        return BELOW_FLOOR

    # --- Frame-sanity guard (data hygiene) --------------------------------
    # Scan the trailing _CORRUPT_WINDOW closes for a >10x bar-over-bar ratio.
    corrupt_window = close.iloc[-_CORRUPT_WINDOW:]
    if len(corrupt_window) >= 2:
        prev = corrupt_window.shift(1)
        # Guard against zero/NaN denominators; replace any inf produced by a
        # zero prior close with NaN so it does not falsely flag the frame.
        ratio = (corrupt_window / prev).abs()
        ratio = ratio.replace([float("inf"), float("-inf")], pd.NA).dropna()
        if len(ratio) and float(ratio.max()) > _CORRUPT_RATIO:
            return CORRUPT_FRAME

    # --- min_price floor ---------------------------------------------------
    last_close = float(close.iloc[-1])
    if last_close < MIN_PRICE:
        return BELOW_FLOOR

    # --- min_avg_volume floor (trailing 63td mean share volume) ------------
    volume = _get_volume(sliced)
    if volume is None or len(volume) == 0:
        # No volume data → cannot clear the liquidity floor → exclude.
        return BELOW_FLOOR
    vol_window = volume.iloc[-_VOLUME_WINDOW:]
    vol_window = vol_window.dropna()
    if len(vol_window) == 0:
        return BELOW_FLOOR
    avg_volume = float(vol_window.mean())
    if avg_volume < MIN_AVG_VOLUME:
        return BELOW_FLOOR

    return OK


def passes_floors(df: Optional[pd.DataFrame], as_of: date) -> bool:
    """Convenience boolean wrapper — True iff floor_status(...) == "ok"."""
    return floor_status(df, as_of) == OK


# ---------------------------------------------------------------------------
# Drift guard: fail loudly at import if our mirrored floors diverge from the
# canonical turnaround.UNIVERSE_V2 (conformance, not tuning — they must match).
# Best-effort: if turnaround is unimportable (e.g. isolated unit context), skip.
# ---------------------------------------------------------------------------

def _check_in_sync_with_universe_v2() -> None:
    try:
        from turnaround import UNIVERSE_V2  # type: ignore
    except Exception:
        return
    canon_price = UNIVERSE_V2.get("min_price")
    canon_vol = UNIVERSE_V2.get("min_avg_volume")
    if canon_price != MIN_PRICE or canon_vol != MIN_AVG_VOLUME:
        raise RuntimeError(
            "universe_floors constants drifted from turnaround.UNIVERSE_V2: "
            f"mirror=(min_price={MIN_PRICE}, min_avg_volume={MIN_AVG_VOLUME}) "
            f"canonical=(min_price={canon_price}, min_avg_volume={canon_vol})"
        )


_check_in_sync_with_universe_v2()
