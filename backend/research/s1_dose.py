"""S-1 Dose builder — build_s1_events().

Sell-side mirror of r1_dose.py. Implements the insider-SELL dose for the
StrategyLab research workbench. Given the same inputs as build_r1_events(),
produces EventRecords with s1_score based on discretionary (non-10b5-1)
insider SELLING activity.

Design: import shared helpers from r1_dose (10b5-1 detection, shares-outstanding,
score formula, cached-close, submissions lookup). Only the transaction-filter
(S+D instead of P+A) and the main loop are new here. This ensures 10b5-1 logic
stays in one place — if r1_dose's helpers are updated, s1_dose automatically
picks up the change.

IMPORTANT — xml_cache isolation (E1):
build_s1_events() has its own xml_cache dict (accession_nodash → sell txns).
It MUST NOT share this cache with build_r1_events — the two functions populate
it with structurally identical but semantically opposite transaction sets
(P+A vs S+D). Always create a fresh dict for each builder call.

Score formula (identical to r1):
    s1_score = log1p(D / MC) * (1 + _BETA * k)
where D = total qualifying SALE dollars in the trailing window,
      k = distinct discretionary-selling owner CIKs in the window,
      MC = shares_outstanding * close_price at event date.

Charter: mirrors R-1 charter §2b/§3b for sell side.
All constants frozen — do NOT tune post-hoc.
"""
from __future__ import annotations

import json
import logging
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ---------------------------------------------------------------------------
# Import shared helpers from r1_dose (E2 — reuse, don't copy)
# NOTE: If r1_dose is refactored, update these imports.
# ---------------------------------------------------------------------------
from research.r1_dose import (  # noqa: E402
    _is_10b51_text,        # 10b5-1 text marker detection
    _txn_is_10b51,         # per-transaction 10b5-1 flag
    _form_is_10b51,        # form-level 10b5-1 flag
    _shares_outstanding_disk_only,  # offline-safe shares lookup
    _busday_window_start,  # trailing W-bday window start
    _compute_score,        # frozen score formula: log1p(D/MC)*(1+beta*k)
    _get_cached_close,     # price frame close lookup
    _get_acceptance_dt_from_subs,   # submissions acceptanceDateTime lookup
    _pad_cik,              # zero-pad CIK to 10 digits
    _PERTURB_WINDOWS,      # (20, 21, 22) — §3b frozen
    _PERTURB_FLOORS,       # (0, 40_000, 60_000) — §3b frozen
    _PERTURB_KEY_MAP,      # W×F → payload key name
    _W_PRIMARY,            # 21 — §2b frozen
    _BETA,                 # 0.5 — §2b frozen
)

from research.event_study import (  # noqa: E402
    EventRecord,
    _parse_acceptance_dt,
    _filing_date_fallback_dt,
    _to_et,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# S/D transaction parser — the only new parsing logic vs r1_dose
# ---------------------------------------------------------------------------

def _parse_qualifying_sell_transactions(
    xml_content: str,
) -> tuple[list[dict], Optional[str], bool]:
    """Parse non-derivative code-S disposal transactions from a Form 4 XML.

    Sell-side mirror of r1_dose._parse_qualifying_transactions.
    The ONLY change: filters code=="S" and adc=="D" instead of "P" and "A".

    Returns:
        transactions: list of dicts, each:
            {
              "shares": float | None,
              "price": float | None,  # None if missing
              "is_10b51": bool,        # transaction-level 10b5-1 flag
            }
        owner_cik: str | None — the reporting owner's CIK
        form_10b51: bool — True if form-level 10b5-1 marker found

    Only returns transactions with transactionCode==S AND acquiredDisposedCode==D
    (open-market sale disposals). If the XML is malformed, returns ([], None, False).
    """
    if not xml_content:
        return [], None, False
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return [], None, False

    # Reporting owner CIK
    owner_cik: Optional[str] = None
    for owner_el in root.iter("reportingOwner"):
        cik_el = owner_el.find(".//rptOwnerCik")
        if cik_el is not None and cik_el.text:
            owner_cik = cik_el.text.strip()
            break

    form_10b51 = _form_is_10b51(root)

    transactions: list[dict] = []
    for txn in root.iter("nonDerivativeTransaction"):
        code_el = txn.find(".//transactionCode")
        adc_el = txn.find(".//transactionAcquiredDisposedCode/value")
        if code_el is None:
            continue
        code = (code_el.text or "").strip()
        adc = (adc_el.text or "").strip().upper() if adc_el is not None else ""
        # S-1: filter code-S disposal transactions (S/D instead of P/A)
        if code != "S" or adc != "D":
            continue

        # Transaction-level 10b5-1 flag
        txn_10b51 = _txn_is_10b51(txn) or form_10b51

        shares: Optional[float] = None
        price: Optional[float] = None

        shares_el = txn.find(".//transactionShares/value")
        if shares_el is not None and shares_el.text:
            try:
                shares = float(shares_el.text.strip())
            except ValueError:
                pass

        price_el = txn.find(".//transactionPricePerShare/value")
        if price_el is not None and price_el.text:
            try:
                price = float(price_el.text.strip())
            except ValueError:
                pass

        transactions.append({
            "shares": shares,
            "price": price,
            "is_10b51": txn_10b51,
        })

    return transactions, owner_cik, form_10b51


# ---------------------------------------------------------------------------
# Sell dose-window aggregation
# ---------------------------------------------------------------------------

def _aggregate_sell_dose_window(
    ticker: str,
    event_date: date,
    W: int,
    xml_dir: Path,
    index: dict,
    cik: str,
    *,
    ticker_index: Optional[dict] = None,
    xml_cache: Optional[dict] = None,
    subs_cache: Optional[dict] = None,
) -> tuple[float, int, int, int, int]:
    """Aggregate D and k over the trailing W-bday window ending at event_date.

    Sell-side mirror of r1_dose._aggregate_dose_window — uses
    _parse_qualifying_sell_transactions (S+D) instead of P+A.

    The xml_cache here is the S-1 builder's own cache; it MUST NOT be the same
    dict as r1_dose's xml_cache (different transaction sets — see E1 in module doc).

    Returns:
        D       — total qualifying sale dollars (10b5-1 excluded, missing price → $0)
        k       — distinct non-10b5-1 owner CIKs with ≥1 qualifying S sale
        n_filings_window     — number of qualifying filings in window
        n_10b51_excluded     — count of 10b5-1-excluded transactions
        missing_price_txns   — count of transactions with missing price
    """
    window_start = _busday_window_start(event_date, W)

    D: float = 0.0
    owner_has_non10b51: set[str] = set()
    n_filings_window: int = 0
    n_10b51_excluded: int = 0
    missing_price_txns: int = 0

    ticker_upper = ticker.upper()

    if ticker_index is not None:
        filing_records = ticker_index.get(ticker_upper, [])
    else:
        filing_records = []
        for entry in index.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("ticker", "").upper() != ticker_upper:
                continue
            if entry.get("status") != "done":
                continue
            entry_cik_val = entry.get("cik", "")
            if not entry_cik_val:
                continue
            entry_padded_val = _pad_cik(entry_cik_val)
            for filing in entry.get("filings", []):
                if filing.get("xml_status") != "ok":
                    continue
                filing_records.append({
                    "entry_padded": entry_padded_val,
                    "entry_cik": entry_cik_val,
                    **filing,
                })

    for filing_rec in filing_records:
        accession = filing_rec.get("accession", "")
        filed_str = filing_rec.get("filed", "")
        entry_cik_rec = filing_rec.get("entry_cik", cik)
        entry_padded_rec = filing_rec.get("entry_padded", _pad_cik(cik))

        # COR-04: resolve acceptanceDateTime ET date for window membership
        acceptance_et_date: Optional[date] = None
        if subs_cache is not None:
            ep = _pad_cik(entry_cik_rec)
            subs_data = subs_cache.get(ep)
            if subs_data:
                filings_block = subs_data.get("filings", {}).get("recent", {})
                accessions_list = filings_block.get("accessionNumber", [])
                adt_list = filings_block.get("acceptanceDateTime", [])
                for acc_s, adt_s in zip(accessions_list, adt_list):
                    if acc_s == accession and adt_s:
                        adt_parsed = _parse_acceptance_dt(adt_s)
                        if adt_parsed is not None:
                            acceptance_et_date = _to_et(adt_parsed).date()
                        break

        if acceptance_et_date is None:
            if not filed_str:
                continue
            try:
                acceptance_et_date = date.fromisoformat(filed_str)
            except ValueError:
                continue

        if not (window_start <= acceptance_et_date <= event_date):
            continue

        accession_nodash = accession.replace("-", "")

        # PERF-04: use cached XML parse result if available (S-1's own cache)
        if xml_cache is not None and accession_nodash in xml_cache:
            txns, filing_owner_cik, _ = xml_cache[accession_nodash]
        else:
            xml_path = xml_dir / f"{entry_padded_rec}_{accession_nodash}.xml"
            if not xml_path.exists():
                continue
            try:
                xml_content = xml_path.read_text(encoding="utf-8")
            except Exception:
                continue
            txns, filing_owner_cik, _ = _parse_qualifying_sell_transactions(xml_content)
            if xml_cache is not None:
                xml_cache[accession_nodash] = (txns, filing_owner_cik, None)

        if not txns:
            continue

        n_filings_window += 1

        for txn in txns:
            if txn["is_10b51"]:
                n_10b51_excluded += 1
                continue
            # Non-10b5-1 qualifying sale
            price = txn["price"]
            shares = txn["shares"]
            if price is None or price <= 0:
                missing_price_txns += 1
                dollar_value = 0.0
            else:
                dollar_value = (shares or 0.0) * price
            D += dollar_value

            if filing_owner_cik:
                owner_has_non10b51.add(filing_owner_cik)

    k = len(owner_has_non10b51)
    return D, k, n_filings_window, n_10b51_excluded, missing_price_txns


# ---------------------------------------------------------------------------
# Sell score perturbation
# ---------------------------------------------------------------------------

def _compute_sell_score_perturb(
    ticker: str,
    event_date: date,
    xml_dir: Path,
    index: dict,
    cik: str,
    MC: float,
    *,
    primary_D: Optional[float] = None,
    primary_k: Optional[int] = None,
    ticker_index: Optional[dict] = None,
    xml_cache: Optional[dict] = None,
    subs_cache: Optional[dict] = None,
) -> dict[str, Optional[float]]:
    """Compute all 9 perturbation scores per §3b for the sell side.

    Mirrors r1_dose._compute_score_perturb but delegates to
    _aggregate_sell_dose_window. Reuses primary (D,k) for W=21 when provided.
    """
    if MC <= 0:
        return {v: None for v in _PERTURB_KEY_MAP.values()}

    w_results: dict[int, tuple[float, int]] = {}
    for W in _PERTURB_WINDOWS:
        if W == _W_PRIMARY and primary_D is not None and primary_k is not None:
            w_results[W] = (primary_D, primary_k)
        else:
            D_w, k_w, _, _, _ = _aggregate_sell_dose_window(
                ticker, event_date, W, xml_dir, index, cik,
                ticker_index=ticker_index,
                xml_cache=xml_cache,
                subs_cache=subs_cache,
            )
            w_results[W] = (D_w, k_w)

    perturb: dict[str, Optional[float]] = {}
    for (W, F), key in _PERTURB_KEY_MAP.items():
        D, k = w_results[W]
        D_eff = 0.0 if (F > 0 and D < F) else D
        perturb[key] = _compute_score(D_eff, k, MC)

    return perturb


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def build_s1_events(
    start: date,
    end: date,
    *,
    index_path: Path,
    xml_dir: Path,
    subs_dir: Path,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    shares_fn: Optional[Callable[[str, date], Optional[float]]] = None,
    max_market_cap: Optional[float] = None,  # F395 floor: None = no ceiling
) -> tuple[list[EventRecord], dict]:
    """Build S-1 EventRecord list with sell-dose payload.

    Interchangeable signature with build_r1_events (plus max_market_cap).

    Parameters
    ----------
    start, end
        Inclusive date range filter on acceptanceDateTime ET date.
    index_path
        Path to edgar_cache/form4_stratified/index.json.
    xml_dir
        Directory containing {padded_cik}_{accession_nodash}.xml files.
    subs_dir
        Directory containing {padded_cik}.json submissions files.
    loader_fn
        ticker → Optional[pd.DataFrame]; the harness price-frame loader.
    shares_fn
        (cik: str, as_of: date) → Optional[float]; defaults to
        _shares_outstanding_disk_only (disk-only, offline-safe).
    max_market_cap
        If set, events with MC > max_market_cap are excluded entirely
        (not appended to results). Use for small/mid-cap universe ceiling.
        None = no ceiling (default, keeps r1 specs unaffected).
        IMPORTANT: must be explicitly passed at both call sites (preview +
        worker). Silently ignored if not passed. See brief §4 / D2.

    Returns
    -------
    events : list[EventRecord]
        One EventRecord per (ticker, ET acceptance date) with ≥1 qualifying S
        filing. event_ts = latest qualifying acceptance ts that day.
        Payload keys (mirror of build_r1_events):
          form_type, accession, filing_date, acceptance_fallback,
          score, score_undefined, D, k, MC, n_filings_window,
          n_10b51_excluded, missing_price_txns, score_perturb (9 keys)
    meta : dict
        filings_scanned, filings_qualifying, acceptance_fallbacks,
        n_10b51_excluded_total, missing_price_txns_total,
        score_undefined_total, events_raw, events_returned,
        n_cap_ceiling_excluded
    """
    if shares_fn is None:
        shares_fn = _shares_outstanding_disk_only

    # Load index
    try:
        raw_index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("build_s1_events: failed to read index %s: %s", index_path, exc)
        return [], {"error": str(exc)}

    # Strip _meta key if present
    index = {k: v for k, v in raw_index.items() if k != "_meta"}

    # PERF-02: submissions cache
    subs_cache: dict = {}

    # PERF-04: S-1's OWN xml_cache — separate from r1's (E1: cache isolation)
    # Entries: accession_nodash → (sell_txns, owner_cik, form_10b51)
    # These are S+D transactions ONLY, never mixed with r1's P+A cache.
    xml_cache: dict = {}

    # PERF-03: inverted ticker→filings index
    ticker_index: dict[str, list[dict]] = {}
    for entry in index.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "done":
            continue
        t = entry.get("ticker", "").upper()
        entry_cik_val = entry.get("cik", "")
        if not t or not entry_cik_val:
            continue
        entry_padded_val = _pad_cik(entry_cik_val)
        for filing in entry.get("filings", []):
            if filing.get("xml_status") != "ok":
                continue
            rec = dict(filing)
            rec["entry_cik"] = entry_cik_val
            rec["entry_padded"] = entry_padded_val
            ticker_index.setdefault(t, []).append(rec)

    # PERF-05: price frame cache
    frame_cache: dict = {}

    # Meta counters
    filings_scanned = 0
    filings_qualifying = 0
    acceptance_fallbacks = 0
    n_10b51_excluded_total = 0      # window-scoped: 10b5-1 txns excluded inside triggering-event dose windows
    n_10b51_sales_seen_total = 0    # parse-time: ALL qualifying S/D txns flagged is_10b51==True (any filing, pre-gate)
    missing_price_txns_total = 0
    score_undefined_total = 0
    n_cap_ceiling_excluded = 0
    n_excluded_unknown_mc = 0   # S3-FIX: events with uncomputable MC excluded when cap set

    # Accumulate per-(ticker, ET date) candidates
    day_candidates: dict[tuple[str, date], list[tuple]] = defaultdict(list)

    for event_key, entry in index.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "done":
            continue
        ticker = entry.get("ticker", "").upper()
        cik = entry.get("cik", "")
        if not ticker or not cik:
            continue

        for filing in entry.get("filings", []):
            if filing.get("xml_status") != "ok":
                continue
            accession = filing.get("accession", "")
            filed_str = filing.get("filed", "")

            filings_scanned += 1

            # PERF-04: parse XML once into S-1's own cache (S+D only)
            padded = _pad_cik(cik)
            accession_nodash = accession.replace("-", "")
            if accession_nodash not in xml_cache:
                xml_path = xml_dir / f"{padded}_{accession_nodash}.xml"
                if not xml_path.exists():
                    xml_cache[accession_nodash] = ([], None, False)
                    continue
                try:
                    xml_content = xml_path.read_text(encoding="utf-8")
                except Exception:
                    xml_cache[accession_nodash] = ([], None, False)
                    continue
                txns_parsed, owner_cik_parsed, form_10b51_parsed = _parse_qualifying_sell_transactions(xml_content)
                xml_cache[accession_nodash] = (txns_parsed, owner_cik_parsed, form_10b51_parsed)

            txns, _owner_cik, form_10b51 = xml_cache[accession_nodash]
            if not txns:
                continue

            # Parse-time 10b5-1 tally: count ALL qualifying S/D txns flagged is_10b51,
            # BEFORE the triggering-gate decision (has_non_10b51_qualifying below).
            # This gives a complete picture of planned-sale detections across every filing,
            # not just those that become triggering events.
            n_10b51_sales_seen_total += sum(1 for t in txns if t["is_10b51"])

            # Check whether any non-10b5-1 qualifying sell transaction exists
            has_non_10b51_qualifying = any(not t["is_10b51"] for t in txns)
            if not has_non_10b51_qualifying:
                # All qualifying transactions are 10b5-1-flagged; skip as triggering filing.
                # S1-FIX: do NOT increment n_10b51_excluded_total here — the per-transaction
                # count is tallied in _aggregate_sell_dose_window (the truth source). Counting
                # here as well was a double-count (one reviewer COR-06, one DI-09).
                continue

            filings_qualifying += 1

            # PERF-02: resolve acceptanceDateTime via subs_cache
            adt_str = _get_acceptance_dt_from_subs(cik, accession, subs_dir, subs_cache=subs_cache)
            is_fallback = False
            event_ts = _parse_acceptance_dt(adt_str) if adt_str else None
            if event_ts is None:
                event_ts = _filing_date_fallback_dt(filed_str)
                if event_ts is None:
                    continue
                is_fallback = True
                acceptance_fallbacks += 1

            et_date = _to_et(event_ts).date()

            # Date range filter
            if et_date < start or et_date > end:
                continue

            day_candidates[(ticker, et_date)].append(
                (event_ts, accession, filed_str, is_fallback, cik)
            )

    # Build one EventRecord per (ticker, et_date)
    events: list[EventRecord] = []

    for (ticker, et_date), candidates in day_candidates.items():
        candidates_sorted = sorted(candidates, key=lambda x: x[0])
        latest = candidates_sorted[-1]
        event_ts, accession, filed_str, is_fallback, cik = latest

        # Aggregate sell dose window (primary: W=21)
        D, k, n_filings_window, n_10b51_exc, missing_price = _aggregate_sell_dose_window(
            ticker, et_date, _W_PRIMARY, xml_dir, index, cik,
            ticker_index=ticker_index,
            xml_cache=xml_cache,
            subs_cache=subs_cache,
        )
        n_10b51_excluded_total += n_10b51_exc
        missing_price_txns_total += missing_price

        # Market cap calculation
        shares_outstanding = shares_fn(cik, et_date)
        cached_close = _get_cached_close(ticker, et_date, loader_fn, frame_cache=frame_cache)

        if shares_outstanding is None or cached_close is None:
            # S3-FIX: when max_market_cap is set, MC-None events cannot be verified within
            # the cap — exclude them rather than letting unknown-MC events leak past the ceiling.
            if max_market_cap is not None:
                n_excluded_unknown_mc += 1
                continue
            score = None
            MC = None
            score_undefined = True
            score_undefined_total += 1
        else:
            MC = shares_outstanding * cached_close
            if MC <= 0:
                score = None
                score_undefined = True
                score_undefined_total += 1
            else:
                # max_market_cap floor (F395): skip events above the ceiling
                if max_market_cap is not None and MC > max_market_cap:
                    n_cap_ceiling_excluded += 1
                    continue  # don't append — no signal above cap ceiling
                score = _compute_score(D, k, MC)
                score_undefined = False

        # Perturbation scores — reuse primary (D,k), 3 window calls max
        score_perturb = _compute_sell_score_perturb(
            ticker, et_date, xml_dir, index, cik, MC if MC is not None else 0.0,
            primary_D=D, primary_k=k,
            ticker_index=ticker_index,
            xml_cache=xml_cache,
            subs_cache=subs_cache,
        )
        if score_undefined:
            score_perturb = {v: None for v in _PERTURB_KEY_MAP.values()}

        # S4-FIX: use math.isclose for numeric comparison (float == is fragile),
        # and explicitly require both-None or both-defined (None==None silently
        # passes the old assert even when the perturb path is broken).
        _w21 = score_perturb.get("W21_F0")
        if score is None and _w21 is None:
            pass  # both undefined — ok
        elif score is None or _w21 is None:
            raise AssertionError(
                f"W21_F0 mismatch: one is None but not the other: "
                f"W21_F0={_w21!r} vs score={score!r}"
            )
        else:
            import math as _math
            assert _math.isclose(_w21, score, rel_tol=1e-9, abs_tol=1e-12), (
                f"W21_F0 mismatch: {_w21} vs {score}"
            )

        payload = {
            "form_type": "4",
            "accession": accession,
            "filing_date": filed_str,
            "acceptance_fallback": is_fallback,
            "score": score,
            "score_undefined": score_undefined,
            "D": D,
            "k": k,
            "MC": MC,
            "n_filings_window": n_filings_window,
            "n_10b51_excluded": n_10b51_exc,
            "missing_price_txns": missing_price,
            "score_perturb": score_perturb,
        }

        ev = EventRecord(
            ticker=ticker,
            event_ts=event_ts,
            payload=payload,
            is_fallback=is_fallback,
        )
        events.append(ev)

    meta = {
        "filings_scanned": filings_scanned,
        "filings_qualifying": filings_qualifying,
        "acceptance_fallbacks": acceptance_fallbacks,
        "n_10b51_excluded_total": n_10b51_excluded_total,        # window-scoped: txns excluded inside triggering-event dose windows
        "n_10b51_sales_seen_total": n_10b51_sales_seen_total,    # parse-time: ALL 10b5-1 S/D txns seen across every filing (pre-gate)
        "missing_price_txns_total": missing_price_txns_total,
        "score_undefined_total": score_undefined_total,
        "events_raw": len(day_candidates),
        "events_returned": len(events),
        "n_cap_ceiling_excluded": n_cap_ceiling_excluded,
        "n_excluded_unknown_mc": n_excluded_unknown_mc,  # S3-FIX
    }
    log.info(
        "build_s1_events: scanned=%d qualifying=%d fallbacks=%d "
        "10b51_seen=%d 10b51_excl_window=%d "
        "missing_price=%d score_undefined=%d cap_excl=%d unknown_mc_excl=%d events=%d",
        filings_scanned,
        filings_qualifying,
        acceptance_fallbacks,
        n_10b51_sales_seen_total,
        n_10b51_excluded_total,
        missing_price_txns_total,
        score_undefined_total,
        n_cap_ceiling_excluded,
        n_excluded_unknown_mc,
        len(events),
    )
    return events, meta
