"""dose_builders.py — Cross-sectional dose construction for F370 explore-0.

Provides two public helpers:

    cross_sectional_zscores(values) -> list[float|None]
        Standardize a list of float|None values across the event population
        (ddof=1; None passthrough; std==0 -> 0.0; <2 finite -> all None).

    composite_dose(rows) -> list[float|None]
        Frozen composite formula (F370 charter):

            COMPOSITE = z(earnings_yoy) + z(revenue_accel)
                      + z(net_margin_infl_pp) - z(dilution_yoy)

        Each z is computed cross-sectionally over the FULL event set passed in.
        An event is None only if ALL four components are None; if some components
        are None they contribute 0.0 after z-scoring (because z(None) -> 0.0 when
        we treat missing as the population mean, which is exactly 0 in z-space).
        This choice is documented here: missing components are treated as neutral
        (zero surprise), NOT excluded. The charter hashes this formula verbatim.

        COMPOSITE FORMULA (frozen, do not alter post-lock):
            composite_i = z_i(earnings_yoy)
                        + z_i(revenue_accel)
                        + z_i(net_margin_infl_pp)
                        - z_i(dilution_yoy)
        where z_i(field) = (field_i - mean(field)) / std(field, ddof=1)
        computed over all events in the input; None fields contribute 0 after z.
        An event is composite=None iff earnings_yoy, revenue_accel,
        net_margin_infl_pp, AND dilution_yoy are all None for that event.

NOTE: std_sue (dose 4) is NOT built here — it requires the F348 trailing-
volatility add, which is gated on explore-0 results (staged execution §3 of
the charter spec).

DESIGN NOTE — composite vs standalone dose-2 field divergence (M-08, intentional):
    The composite formula uses `revenue_accel` (quarter-over-quarter acceleration
    of revenue growth), while the standalone dose-2 score uses `revenue_yoy`
    (year-over-year revenue level growth). These are TWO DIFFERENT surprises, both
    intentional per the F370 charter spec (§ dose design): the composite captures
    whether growth is *accelerating*, while dose-2 captures whether sales grew
    at all. Do NOT "fix" them to match — the divergence is by design.
"""

from __future__ import annotations

import math
from typing import Optional


def cross_sectional_zscores(
    values: list[Optional[float]],
) -> list[Optional[float]]:
    """Standardize across the population (ddof=1); None values pass through.

    Rules:
    - <2 finite values  -> all outputs are None (can't estimate mean/std)
    - std == 0          -> all outputs are 0.0 (degenerate: no spread)
    - None inputs       -> None outputs (never imputed)
    - finite inputs     -> (x - mean) / std
    """
    finite_vals = [v for v in values if v is not None and math.isfinite(v)]
    n = len(finite_vals)

    if n < 2:
        return [None] * len(values)

    mean = sum(finite_vals) / n
    # ddof=1 (sample std)
    variance = sum((v - mean) ** 2 for v in finite_vals) / (n - 1)
    std = math.sqrt(variance)

    if std == 0.0:
        return [0.0 if (v is not None and math.isfinite(v)) else None for v in values]

    result: list[Optional[float]] = []
    for v in values:
        if v is None or not math.isfinite(v):
            result.append(None)
        else:
            result.append((v - mean) / std)
    return result


def composite_dose(rows: list[dict]) -> list[Optional[float]]:
    """Return the frozen F370 composite dose for each event row.

    FROZEN FORMULA (F370 charter, do not alter post-lock):
        composite_i = z(earnings_yoy)_i
                    + z(revenue_accel)_i      ← ACCELERATION (not level)
                    + z(net_margin_infl_pp)_i
                    - z(dilution_yoy)_i

    Note: the composite uses `revenue_accel` (acceleration of growth), while
    the standalone dose-2 in the driver uses `revenue_yoy` (level of growth).
    This is INTENTIONAL (M-08, charter spec §dose design) — two different
    surprises. Do NOT change revenue_accel to revenue_yoy here to "match" dose-2.

    Each z is cross-sectional (ddof=1) over the full rows passed in.
    None components contribute 0.0 after z (treated as population mean = 0
    in z-space; documented choice). An event is composite=None iff ALL four
    source fields are None for that event.

    Args:
        rows: list of event outcome dicts; each row must have a "payload" key
              whose value is a dict containing the F348 surprise fields.

    Returns:
        list of float|None, one per row, in the same order as rows.
    """
    _FIELDS = ["earnings_yoy", "revenue_accel", "net_margin_infl_pp", "dilution_yoy"]
    _SIGNS = [1.0, 1.0, 1.0, -1.0]  # dilution_yoy is subtracted

    # Extract raw per-field values from payload
    raw: dict[str, list[Optional[float]]] = {f: [] for f in _FIELDS}
    for row in rows:
        payload = row.get("payload") or {}
        for f in _FIELDS:
            v = payload.get(f)
            if v is not None:
                try:
                    v = float(v)
                    if not math.isfinite(v):
                        v = None
                except (TypeError, ValueError):
                    v = None
            raw[f].append(v)

    # Compute cross-sectional z-scores for each field
    z_scores: dict[str, list[Optional[float]]] = {
        f: cross_sectional_zscores(raw[f]) for f in _FIELDS
    }

    # Combine: missing components contribute 0 (documented choice above)
    results: list[Optional[float]] = []
    for i in range(len(rows)):
        # An event is None only if ALL four components are None
        all_none = all(raw[f][i] is None for f in _FIELDS)
        if all_none:
            results.append(None)
            continue

        composite = 0.0
        for f, sign in zip(_FIELDS, _SIGNS):
            z = z_scores[f][i]
            if z is None:
                # Missing component -> treat as 0 in z-space (population mean)
                z = 0.0
            composite += sign * z
        results.append(composite)

    return results
