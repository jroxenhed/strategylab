"""backend/research/fundamental_surprise.py — F348: Fundamental-surprise event payloads.

Layer 1: compute_surprise_payload(cik, as_of) — pure, disk-only, point-in-time.
Layer 2: build_pead_surprise_events(...) — harness-ready EventRecord enumeration.

Point-in-time discipline (dominant correctness requirement):
  - NO entry with filed > as_of may influence any output.
  - Prior-period entries are also filtered to filed <= as_of (no later restatement leakage).
  - as_of boundary = filing's ET calendar date; date-granular (no intraday ordering used).

Sign conventions (see spec table):
  revenue_yoy:       >0 = growth (good)
  revenue_accel:     >0 = accelerating (good)
  earnings_yoy:      >0 = earnings growth (good); None when prior_ni <= 0
  net_margin:        level (context)
  net_margin_infl_pp:   >0 = margin expanding (good), percentage-points
  gross_margin_infl_pp: >0 = expanding (good), percentage-points
  dilution_yoy:      >0 = dilution (BAD)
  ocf_accrual_ratio: >1 = cash-backed earnings (good); only when ni_t > 0

All fields default to None (never 0) when inputs are missing or ill-defined.

A1 anchor band (K8): revenue_yoy for AAPL FY2019 expected in [-0.05, 0.20].
The docstring historically stated [0.00, 0.20]; probe_a1 uses [-0.05, 0.20] to
accommodate a quarter with slight YoY revenue dip. The wider band is correct —
Apple's FY2019 revenue was ~-2% YoY — so the docstring is updated to match.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Path setup (mirrors smoke_probe_f349_f350.py pattern)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent

for _p in [str(_BACKEND_DIR), str(_SCRIPT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Imports from existing modules (read-only — do not modify those files)
# ---------------------------------------------------------------------------
import edgar  # noqa: E402  (only for _derived_path — never the network loader)
from research.event_study import EventRecord  # noqa: E402

# _ET_TZ defined locally (M1/M11/K1 — decoupled from power-census module).
# After-hours entry-date offset (_entry_date_for_filing) is NOT applied here:
# F348 emits EventRecord(ticker, event_ts, payload) only; entry-date resolution
# is the event_study harness's responsibility, not this module's.
_ET_TZ = ZoneInfo("America/New_York")

log = logging.getLogger(__name__)

_SUBMISSIONS_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "submissions"

# ---------------------------------------------------------------------------
# Canonical key list — define once, import everywhere (F377)
# ---------------------------------------------------------------------------
#: Ordered tuple of all numeric output keys from compute_surprise_payload.
#: Exported so smoke_probe_f348.py, tests, and any future consumer can import
#: rather than duplicate. Order matches the spec table in the module docstring.
NUMERIC_KEYS: tuple[str, ...] = (
    "revenue_yoy",
    "revenue_accel",
    "earnings_yoy",
    "net_margin",
    "net_margin_infl_pp",
    "gross_margin_infl_pp",
    "dilution_yoy",
    "ocf_accrual_ratio",
)


def _load_derived_disk_only(cik: str) -> dict:
    """Read the F320 derived cache for `cik` from disk ONLY — never the network.

    edgar._load_derived() falls back to fetching companyfacts from SEC on a
    cache-miss (non-deterministic, mutates the shared cache, slow mid-study).
    The F348 disk-only contract requires that a CIK absent from the derived
    cache is treated as "no fundamentals" (counted as n_no_derived downstream),
    NOT silently fetched. Mirrors r1_dose._shares_outstanding_disk_only.

    Returns the parsed derived dict, or {} when the file is absent/unreadable.
    """
    padded = str(int(cik)).zfill(10)
    derived_path = edgar._derived_path(padded)
    if not derived_path.exists():
        return {}
    try:
        return json.loads(derived_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("fundamental_surprise: derived read error %s: %s", derived_path, exc)
        return {}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _filter_pit(entries: list[dict], as_of: date) -> list[dict]:
    """Return only entries with filed <= as_of (point-in-time filter).

    This is the primary look-ahead guard; called once at load time for each
    series before any computation or lookup.

    Args:
        entries: list of {end, filed, val} dicts
        as_of: the point-in-time boundary date (inclusive)

    Returns:
        filtered list, preserving original order
    """
    result = []
    for e in entries:
        try:
            filed = date.fromisoformat(e["filed"])
        except (KeyError, ValueError):
            continue
        if filed <= as_of:
            result.append(e)
    return result


def _latest_entry(entries: list[dict]) -> Optional[dict]:
    """Return the entry with the latest 'end', breaking ties by latest 'filed'.

    Args:
        entries: already point-in-time filtered list of {end, filed, val}

    Returns:
        the current-period entry, or None if entries is empty
    """
    if not entries:
        return None
    return max(entries, key=lambda e: (e.get("end", ""), e.get("filed", "")))


def _find_prior_entry(
    entries: list[dict],
    current_end: str,
    delta_days: int,
    tolerance_days: int,
) -> Optional[dict]:
    """Find the prior-period entry whose 'end' is closest to current_end - delta_days.

    Among entries within ±tolerance_days of the target, picks the one with the
    latest 'filed' (best knowledge as of as_of). This also guards against a
    later restatement of the prior period because all entries in `entries` are
    already filtered to filed <= as_of.

    Args:
        entries: point-in-time filtered entries for a series
        current_end: ISO date string of the current period end
        delta_days: approximate days back (365 for YoY, 91 for QoQ)
        tolerance_days: window half-width (±45 for YoY, ±30 for QoQ)

    Returns:
        best matching entry, or None if no match within tolerance
    """
    try:
        target = date.fromisoformat(current_end) - timedelta(days=delta_days)
    except ValueError:
        return None

    best: Optional[dict] = None
    best_dist = tolerance_days + 1  # one beyond tolerance so any match wins

    for e in entries:
        try:
            e_end = date.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        dist = abs((e_end - target).days)
        if dist > tolerance_days:
            continue
        # Closer absolute distance wins; break ties by latest filed
        if dist < best_dist or (
            dist == best_dist and e.get("filed", "") > (best or {}).get("filed", "")
        ):
            best = e
            best_dist = dist

    return best


def _is_finite(x: float) -> bool:
    return math.isfinite(x)


# ---------------------------------------------------------------------------
# Layer 1 — pure primitive
# ---------------------------------------------------------------------------

def compute_surprise_payload(cik: str, as_of: date) -> dict:
    """Compute point-in-time fundamental surprise deltas for a company at a filing date.

    Loads derived cache for `cik` (disk-only, no network) and applies the
    point-in-time filter (filed <= as_of) before any computation. All numeric
    outputs are None when inputs are missing or ill-defined — never 0-filled.

    Designed for import by F370 (dose-score formula) and F347 (heterogeneity).
    This function computes the deltas only; it makes no premise commitment.

    Point-in-time discipline:
      1. Every series is filtered to filed <= as_of before lookup.
      2. Prior-period entries are also from the filtered set — no later
         restatement of the comparison quarter leaks in.
      3. as_of is date-granular; no intraday ordering used.

    Args:
        cik: EDGAR CIK (padded or unpadded — edgar.py handles padding)
        as_of: the filing's ET calendar date (the look-ahead boundary)

    Returns:
        dict with keys:
            revenue_yoy, revenue_accel, earnings_yoy, net_margin,
            net_margin_infl_pp, gross_margin_infl_pp, dilution_yoy,
            ocf_accrual_ratio  — float or None
            current_end, current_filed  — str (current period dates)
            yoy_end, qoq_end  — str or None (matched prior period ends)
            n_nonnull  — int count of non-None numeric output fields
    """
    # Output key order is deterministic (spec table order) — use module-level NUMERIC_KEYS (F377)
    out: dict = {k: None for k in NUMERIC_KEYS}
    out["current_end"] = None
    out["current_filed"] = None
    out["yoy_end"] = None
    out["qoq_end"] = None
    out["n_nonnull"] = 0

    derived = _load_derived_disk_only(cik)  # disk-only — never hits the network

    # Point-in-time filter for every series (guard 1)
    rev_pit = _filter_pit(derived.get("revenue", []), as_of)
    ni_pit = _filter_pit(derived.get("net_income", []), as_of)
    gp_pit = _filter_pit(derived.get("gross_profit", []), as_of)
    ocf_pit = _filter_pit(derived.get("ocf", []), as_of)
    sh_pit = _filter_pit(derived.get("shares", []), as_of)

    # Current period: latest end with filed <= as_of (already filtered)
    cur_rev = _latest_entry(rev_pit)
    if cur_rev is None:
        # No revenue data at all — cannot determine current period
        return out

    current_end = cur_rev["end"]
    current_filed = cur_rev.get("filed", "")
    out["current_end"] = current_end
    out["current_filed"] = current_filed

    # Retrieve current-period values for all series
    # Each series may have its own "latest" entry — we use current_end to cross-match
    # by finding the entry in each series closest to current_end (within ±15d)
    def _val_at_end(pit_entries: list[dict], target_end: str) -> Optional[float]:
        """Get the value of a series at a specific period end, filed <= as_of.

        Among entries with end == target_end (all already PIT-filtered), returns
        the value of the max-filed entry (C1: latest-filed tie-break matches
        _find_prior_entry so current and prior use the same amendment vintage).
        """
        # Primary: exact end match — pick the latest-filed among matches (C1)
        best_exact: Optional[dict] = None
        for e in pit_entries:
            if e.get("end") == target_end:
                if best_exact is None or e.get("filed", "") > best_exact.get("filed", ""):
                    best_exact = e
        if best_exact is not None:
            v = best_exact.get("val")
            try:
                fv = float(v)  # type: ignore[arg-type]
                if v is not None and _is_finite(fv):
                    return fv
            except (TypeError, ValueError):
                pass  # non-numeric val → treat as missing (DI-03)

        # Fallback: closest within ±15 days (covers quarter-end rounding across forms)
        try:
            t = date.fromisoformat(target_end)
        except ValueError:
            return None
        best_dist = 16
        best_val: Optional[float] = None
        best_filed = ""
        for e in pit_entries:
            try:
                e_end = date.fromisoformat(e["end"])
            except (KeyError, ValueError):
                continue
            dist = abs((e_end - t).days)
            if dist >= best_dist:
                continue
            v = e.get("val")
            try:
                fv = float(v)  # type: ignore[arg-type]
                if v is not None and _is_finite(fv):
                    # Among same-distance entries prefer latest-filed (C1 fallback)
                    if dist < best_dist or e.get("filed", "") > best_filed:
                        best_val = fv
                        best_dist = dist
                        best_filed = e.get("filed", "")
            except (TypeError, ValueError):
                pass  # non-numeric val → skip (DI-03)
        return best_val

    rev_t = _val_at_end(rev_pit, current_end)
    ni_t = _val_at_end(ni_pit, current_end)
    gp_t = _val_at_end(gp_pit, current_end)
    ocf_t = _val_at_end(ocf_pit, current_end)
    sh_t = _val_at_end(sh_pit, current_end)

    # --- YoY prior (closest end to current_end - 365d, ±45d) ---
    yoy_rev = _find_prior_entry(rev_pit, current_end, 365, 45)
    yoy_ni = _find_prior_entry(ni_pit, current_end, 365, 45)
    yoy_gp = _find_prior_entry(gp_pit, current_end, 365, 45)
    yoy_sh = _find_prior_entry(sh_pit, current_end, 365, 45)

    yoy_end = yoy_rev["end"] if yoy_rev else None
    out["yoy_end"] = yoy_end

    # --- QoQ prior for revenue_accel (closest end to current_end - 91d, ±30d) ---
    qoq_rev = _find_prior_entry(rev_pit, current_end, 91, 30)
    qoq_end = qoq_rev["end"] if qoq_rev else None
    out["qoq_end"] = qoq_end

    # Extract scalar YoY values
    rev_prior = float(yoy_rev["val"]) if yoy_rev and yoy_rev.get("val") is not None else None
    ni_prior = float(yoy_ni["val"]) if yoy_ni and yoy_ni.get("val") is not None else None
    gp_prior = float(yoy_gp["val"]) if yoy_gp and yoy_gp.get("val") is not None else None
    sh_prior = float(yoy_sh["val"]) if yoy_sh and yoy_sh.get("val") is not None else None

    # Validate finite
    if rev_prior is not None and not _is_finite(rev_prior):
        rev_prior = None
    if ni_prior is not None and not _is_finite(ni_prior):
        ni_prior = None
    if gp_prior is not None and not _is_finite(gp_prior):
        gp_prior = None
    if sh_prior is not None and not _is_finite(sh_prior):
        sh_prior = None

    # --- Compute deltas ---

    # revenue_yoy = (rev_t - rev_prior) / abs(rev_prior)
    # Guard: rev_prior must be non-None, non-zero, finite
    if rev_t is not None and rev_prior is not None and rev_prior != 0 and _is_finite(rev_t):
        out["revenue_yoy"] = (rev_t - rev_prior) / abs(rev_prior)

    # revenue_accel = revenue_yoy(t) - revenue_yoy(t-1q)
    # Need YoY for the prior quarter too. Prior quarter's YoY:
    #   prior_qoq_rev_t  = rev at qoq_end
    #   prior_qoq_rev_prior = rev YoY-prior of qoq period (qoq_end - 365d, ±45d)
    if out["revenue_yoy"] is not None and qoq_rev is not None:
        qoq_end_str = qoq_rev["end"]
        qoq_rev_val = float(qoq_rev["val"]) if qoq_rev.get("val") is not None else None
        if qoq_rev_val is not None and _is_finite(qoq_rev_val):
            qoq_yoy_prior = _find_prior_entry(rev_pit, qoq_end_str, 365, 45)
            if qoq_yoy_prior is not None and qoq_yoy_prior.get("val") is not None:
                qoq_prior_val = float(qoq_yoy_prior["val"])
                if _is_finite(qoq_prior_val) and qoq_prior_val != 0:
                    prior_yoy = (qoq_rev_val - qoq_prior_val) / abs(qoq_prior_val)
                    out["revenue_accel"] = out["revenue_yoy"] - prior_yoy

    # earnings_yoy = (ni_t - ni_prior) / abs(ni_prior)
    # Guard: when ni_prior <= 0, sign of from-loss change is uninterpretable → None
    if (
        ni_t is not None
        and ni_prior is not None
        and _is_finite(ni_t)
        and ni_prior > 0  # strict: prior must be positive (spec: ni_{t-4q} <= 0 → None)
        and ni_prior != 0
    ):
        out["earnings_yoy"] = (ni_t - ni_prior) / abs(ni_prior)

    # net_margin = ni_t / rev_t  (requires rev_t > 0)
    if ni_t is not None and rev_t is not None and rev_t > 0 and _is_finite(ni_t) and _is_finite(rev_t):
        out["net_margin"] = ni_t / rev_t

    # net_margin_infl_pp = (ni_t/rev_t - ni_prior/rev_prior) * 100
    # K10: rev_prior > 0 (strict positive) — negative rev_prior flips margin sign
    if (
        ni_t is not None and rev_t is not None and rev_t > 0
        and ni_prior is not None and rev_prior is not None and rev_prior > 0
        and _is_finite(ni_t) and _is_finite(rev_t)
        and _is_finite(ni_prior) and _is_finite(rev_prior)
    ):
        margin_t = ni_t / rev_t
        margin_prior = ni_prior / rev_prior
        out["net_margin_infl_pp"] = (margin_t - margin_prior) * 100.0

    # gross_margin_infl_pp = (gp_t/rev_t - gp_prior/rev_prior) * 100
    # K10: rev_prior > 0 (strict positive) — negative rev_prior flips margin sign
    if (
        gp_t is not None and rev_t is not None and rev_t > 0
        and gp_prior is not None and rev_prior is not None and rev_prior > 0
        and _is_finite(gp_t) and _is_finite(rev_t)
        and _is_finite(gp_prior) and _is_finite(rev_prior)
    ):
        gm_t = gp_t / rev_t
        gm_prior = gp_prior / rev_prior
        out["gross_margin_infl_pp"] = (gm_t - gm_prior) * 100.0

    # dilution_yoy = (sh_t - sh_prior) / sh_prior  (>0 = dilution, BAD)
    if (
        sh_t is not None and sh_prior is not None
        and sh_prior != 0 and sh_prior > 0  # shares outstanding must be positive
        and _is_finite(sh_t) and _is_finite(sh_prior)
    ):
        out["dilution_yoy"] = (sh_t - sh_prior) / sh_prior

    # ocf_accrual_ratio = ocf_t / ni_t  (only when ni_t > 0)
    if (
        ocf_t is not None and ni_t is not None
        and ni_t > 0  # spec: only when ni_t > 0
        and _is_finite(ocf_t) and _is_finite(ni_t)
    ):
        out["ocf_accrual_ratio"] = ocf_t / ni_t

    # Count non-null numeric outputs
    out["n_nonnull"] = sum(1 for k in NUMERIC_KEYS if out[k] is not None)

    return out


# ---------------------------------------------------------------------------
# Layer 2 — event enumerator
# ---------------------------------------------------------------------------

def build_pead_surprise_events(
    universe_tickers: list[str],
    span_start: str,
    span_end: str,
    *,
    forms: tuple[str, ...] = ("10-Q", "10-K"),
    submissions_dir: Optional[Path] = None,
) -> tuple[list[EventRecord], dict]:
    """Enumerate 10-Q/10-K filings and return EventRecords with surprise payloads.

    Walks submissions/*.json, filters to the specified forms, reads acceptanceDateTime,
    maps CIK→ticker against the provided universe. Tickers absent from the
    universe are skipped and counted (F364 population-scope discipline).

    as_of for the payload = ET calendar date of acceptanceDateTime.
    This is the look-ahead boundary: information public on that date is used;
    nothing later.

    event_ts = UTC acceptanceDateTime (timezone-aware).

    Population-scope note (F364): every count in meta states the population it
    was measured over. n_filings_seen is the raw total before any filter;
    n_in_universe is the subset mapped to the universe; n_events is the
    EventRecord count produced.

    Args:
        universe_tickers: list of tickers in the liquid universe (from build_liquid_universe)
        span_start: ISO date string YYYY-MM-DD — include filings on or after this date
        span_end: ISO date string YYYY-MM-DD — include filings on or before this date
        forms: tuple of EDGAR form types to include (default 10-Q, 10-K)
        submissions_dir: override the submissions directory (test injection point)

    Returns:
        (events, meta) tuple where:
          events — list[EventRecord] with payload = {filing_form, period_end, ...surprises}
          meta — dict with population-scoped counts and per-field coverage
    """
    subs_dir = submissions_dir or _SUBMISSIONS_DIR
    universe_set = set(universe_tickers)

    # Per-field non-null coverage tracking (measured over n_events) — use module-level NUMERIC_KEYS (F377)
    field_nonnull: dict[str, int] = {k: 0 for k in NUMERIC_KEYS}

    n_filings_seen = 0       # raw 10-Q/10-K count in date range (population: all submissions)
    n_in_universe = 0        # subset whose ticker is in universe_set
    n_no_derived = 0         # in-universe events with no derived cache
    n_skipped_no_ticker = 0  # CIK had no ticker mapping (population: all scanned files)
    n_parse_errors = 0       # population: all submission files in subs_dir
    events: list[EventRecord] = []

    # Single-pass over submissions dir: each file is read exactly once (F378).
    # The previous two-pass structure (first pass builds cik_to_ticker, second
    # pass reads filings) doubled I/O for no gain — both passes consumed the
    # same file. Now ticker extraction and filing enumeration happen together.
    #
    # COR-06 / RM-07 verification: ticker selection is IDENTICAL to the old
    # two-pass code.  Both versions use `tickers[0]` (the first ticker in the
    # submissions JSON, which EDGAR lists as the primary ticker for the issuer).
    # For multi-ticker CIKs the old first-pass also stored `tickers[0]`:
    #   cik_to_ticker[cik] = tickers[0]   ← verbatim from the original code
    # The output is therefore byte-identical for every CIK.  No change needed.
    for fp in sorted(subs_dir.glob("*.json")):
        cik = fp.stem  # zero-padded 10-digit CIK
        try:
            filing_data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            n_parse_errors += 1
            continue

        tickers = filing_data.get("tickers", [])
        ticker = tickers[0] if tickers else None
        if not ticker:
            # Count only once per file (not per-filing)
            n_skipped_no_ticker += 1
            continue

        recent = filing_data.get("filings", {}).get("recent", {})
        form_list = recent.get("form", [])
        accept_dts = recent.get("acceptanceDateTime", [])
        period_ends = recent.get("reportDate", [])  # period end date

        if len(form_list) != len(accept_dts):
            continue

        # Pad period_ends to same length if present
        if len(period_ends) != len(form_list):
            period_ends = [""] * len(form_list)

        for idx, (form, adt) in enumerate(zip(form_list, accept_dts)):
            if form not in forms:
                continue
            if not adt:
                continue

            # Parse the acceptanceDateTime to determine date range membership.
            # K9/DI-02: parse to a tz-aware datetime so event_ts (UTC) and as_of
            # (ET calendar date) are both derived from the same object — they
            # cannot contradict. Handles: "Z", "+HH:MM", fractional seconds with
            # any offset (e.g. "2019-05-01T21:30:45.123-04:00" must NOT lose the
            # offset when fractional seconds are stripped).
            try:
                adt_str: str = adt
                # Normalize trailing Z → +00:00
                if adt_str.endswith("Z"):
                    adt_str = adt_str[:-1] + "+00:00"
                # Strip fractional seconds while preserving the offset:
                # split on "." only if there is a "+" or "-" offset after the dot
                if "." in adt_str:
                    dot_idx = adt_str.index(".")
                    # Find the offset sign after the dot
                    after_dot = adt_str[dot_idx + 1:]
                    for sep in ("+", "-"):
                        if sep in after_dot:
                            offset_part = sep + after_dot.split(sep, 1)[1]
                            adt_str = adt_str[:dot_idx] + offset_part
                            break
                    else:
                        # No offset after fractional part — just strip fractions (assume UTC)
                        adt_str = adt_str[:dot_idx] + "+00:00"
                dt_aware = datetime.fromisoformat(adt_str)
                # Ensure tz-aware (fromisoformat on "+00:00" is already aware)
                if dt_aware.tzinfo is None:
                    dt_aware = dt_aware.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            # Derive both event_ts and as_of from the same tz-aware object (K9)
            dt_et = dt_aware.astimezone(_ET_TZ)
            as_of_date: date = dt_et.date()
            # as_of is the ET calendar date of the filing itself (the look-ahead boundary).
            # After-hours entry-date offset (advancing to next trading day) is the
            # event_study harness's responsibility — not applied here (M1/K1).
            filing_date_str = as_of_date.isoformat()

            # Apply span filter
            if filing_date_str < span_start or filing_date_str > span_end:
                continue

            n_filings_seen += 1

            if ticker not in universe_set:
                continue
            n_in_universe += 1

            period_end_str = period_ends[idx] if idx < len(period_ends) else ""

            # Compute surprise payload (disk-only, point-in-time at as_of_date)
            try:
                surprise = compute_surprise_payload(cik, as_of_date)
                has_derived = surprise.get("current_end") is not None
            except Exception as exc:
                log.debug(
                    "fundamental_surprise: compute_surprise_payload failed cik=%s as_of=%s: %s",
                    cik, as_of_date, exc,
                )
                surprise = {k: None for k in NUMERIC_KEYS}
                surprise.update({"current_end": None, "current_filed": None,
                                  "yoy_end": None, "qoq_end": None, "n_nonnull": 0})
                has_derived = False

            if not has_derived:
                n_no_derived += 1

            payload = {
                "filing_form": form,
                "period_end": period_end_str or surprise.get("current_end"),
                **surprise,
            }

            # Track per-field coverage (population: n_in_universe events)
            for k in NUMERIC_KEYS:
                if payload.get(k) is not None:
                    field_nonnull[k] += 1

            # Build EventRecord
            # event_ts = the UTC acceptanceDateTime (timezone-aware).
            # dt_aware is already tz-aware; convert to UTC for canonical storage (K9).
            event_ts_utc = dt_aware.astimezone(timezone.utc)
            events.append(EventRecord(
                ticker=ticker,
                event_ts=event_ts_utc,
                payload=payload,
                is_fallback=False,
            ))

    n_events = len(events)
    meta = {
        # Population-scoped counts (F364)
        "n_filings_seen": n_filings_seen,        # population: all form-matched filings in date range
        "n_in_universe": n_in_universe,           # population: n_filings_seen subset in universe
        "n_events": n_events,                     # == n_in_universe (events always emitted; payload errors produce all-None but still emit)
        "n_no_derived": n_no_derived,             # population: n_in_universe events missing derived cache
        "n_skipped_no_ticker": n_skipped_no_ticker,  # population: all scanned submission files
        "n_parse_errors": n_parse_errors,         # population: all submission files
        # Per-field non-null coverage (population: n_in_universe events)
        "coverage_population": n_in_universe,
        "field_nonnull": field_nonnull,
        "field_coverage_frac": {
            k: (field_nonnull[k] / n_in_universe) if n_in_universe > 0 else None
            for k in NUMERIC_KEYS
        },
        "forms": list(forms),
        "span_start": span_start,
        "span_end": span_end,
    }
    return events, meta
