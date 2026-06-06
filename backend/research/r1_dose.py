"""R-1 Dose builder — build_r1_events().

Implements the Agent-A contract from the R-1 explore brief.

Given:
  - A stratified Form 4 index (edgar_cache/form4_stratified/index.json)
  - The matching XML files ({cik}_{accession_nodash}.xml in the same dir)
  - An issuer submissions dir (edgar_cache/submissions/{padded_cik}.json)
  - A price-frame loader  (loader_fn: ticker → Optional[pd.DataFrame])
  - An optional shares function (shares_fn: cik, date → float|None)

Produces:
  - A list[EventRecord] where each record carries the R-1 dose payload
  - A meta dict with counts

Charter: docs/plans/2026-06-06-R1-insider-cluster-charter-DRAFT.md (§2/§3).
All constants frozen per charter §2b and §3b; do NOT tune post-hoc.

10b5-1 exclusion: transactions whose footnote text or form remarks contain any
of the machine-identifiable markers are dropped from BOTH D and k.  Markers
(case-insensitive): "10b5-1", "10b5_1", "rule 10b5".

Offline-safety: get_shares_outstanding (edgar._load_derived) will fetch from
the network when the raw companyfacts file is absent (COR-02 path).  We wrap
it here as _shares_outstanding_disk_only, which reads the derived cache
directly and returns None rather than fetching.  The R-1 charter forbids live
EDGAR fetches.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from research.event_study import (  # noqa: E402
    EventRecord,
    _parse_acceptance_dt,
    _filing_date_fallback_dt,
    _to_et,
)
from turnaround_validation import _frame_dates  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen constants (§2b, §3b — do NOT touch without minting a new experiment)
# ---------------------------------------------------------------------------
_W_PRIMARY: int = 21          # trailing dose window in business days (§2b)
_BETA: float = 0.5            # distinct-insider weight (§2b)
_PERTURB_WINDOWS: tuple[int, ...] = (20, 21, 22)    # §3b
_PERTURB_FLOORS: tuple[int, ...] = (0, 40_000, 60_000)  # §3b — 0 is primary
# Payload key names for the 9 perturbation cells
_PERTURB_KEY_MAP: dict[tuple[int, int], str] = {
    (w, f): f"W{w}_F{'0' if f == 0 else ('40k' if f == 40_000 else '60k')}"
    for w in _PERTURB_WINDOWS
    for f in _PERTURB_FLOORS
}

# 10b5-1 marker patterns (case-insensitive, §2b)
_10B51_PATTERNS: list[re.Pattern] = [
    re.compile(r"10b5.?1", re.IGNORECASE),
    re.compile(r"rule\s*10b5", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Offline-safe shares-outstanding reader
# ---------------------------------------------------------------------------

def _shares_outstanding_disk_only(cik: str, as_of: date) -> Optional[float]:
    """Read most-recent CommonStockSharesOutstanding from derived cache only.

    Unlike edgar.get_shares_outstanding, this function NEVER hits the network.
    Returns None when the derived cache file is absent or has no eligible entry.

    The derived cache lives at:
        <edgar_cache>/derived/v1/{padded_cik}.json
    It is built by edgar.parse_companyfacts_to_derived from the raw
    companyfacts JSON (CACHE_DIR/facts/{cik}.json).
    """
    import edgar  # local import — avoids circular at module load
    padded = str(int(cik)).zfill(10)
    derived_path = edgar._derived_path(padded)
    if not derived_path.exists():
        return None
    try:
        data = json.loads(derived_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("shares_outstanding_disk_only: read error %s: %s", derived_path, exc)
        return None

    entries = data.get("shares", [])
    if not entries:
        return None

    as_of_str = as_of.isoformat()
    eligible = [
        e for e in entries
        if e.get("filed", "") <= as_of_str and e.get("val") is not None
    ]
    if not eligible:
        return None
    best = max(eligible, key=lambda e: e["filed"])
    return float(best["val"])


# ---------------------------------------------------------------------------
# 10b5-1 detection helpers
# ---------------------------------------------------------------------------

def _is_10b51_text(text: str) -> bool:
    """Return True if text contains a machine-identifiable 10b5-1 marker."""
    if not text:
        return False
    for pat in _10B51_PATTERNS:
        if pat.search(text):
            return True
    return False


def _txn_is_10b51(txn_el: ET.Element) -> bool:
    """Return True if the transaction element carries a 10b5-1 marker.

    Checks (case-insensitive):
      - Every <footnote> text on the transaction (via <footnotes>/<footnoteId>
        link or direct children) in the parent form's footnote registry.
      - The transaction's own direct text children.
    We scan all text nodes within the transaction subtree for simplicity.
    """
    # Collect all text in this transaction's subtree
    text_parts = []
    for el in txn_el.iter():
        if el.text:
            text_parts.append(el.text)
        if el.tail:
            text_parts.append(el.tail)
    combined = " ".join(text_parts)
    return _is_10b51_text(combined)


def _form_is_10b51(root: ET.Element) -> bool:
    """Return True if the form-level remarks contain a 10b5-1 marker."""
    for remarks_el in root.iter("remarks"):
        text = (remarks_el.text or "")
        if _is_10b51_text(text):
            return True
    for footnote_el in root.iter("footnote"):
        text = (footnote_el.text or "")
        if _is_10b51_text(text):
            return True
    return False


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------

def _pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


def _parse_qualifying_transactions(
    xml_content: str,
) -> tuple[list[dict], Optional[str], bool]:
    """Parse non-derivative code-P acquisition transactions from a Form 4 XML.

    Returns:
        transactions: list of dicts, each:
            {
              "shares": float | None,
              "price": float | None,  # None if missing
              "is_10b51": bool,        # transaction-level 10b5-1 flag
            }
        owner_cik: str | None — the reporting owner's CIK
        form_10b51: bool — True if form-level 10b5-1 marker found

    Only returns transactions with transactionCode==P AND acquiredDisposedCode==A
    (open-market purchase acquisitions).  If the XML is malformed, returns ([], None, False).
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
        if code != "P" or adc != "A":
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
# Dose-window aggregation
# ---------------------------------------------------------------------------

def _busday_window_start(d: date, W: int) -> date:
    """Return the first calendar date of a trailing W-business-day window ending at d.

    The window is [start, d] inclusive — containing exactly W business days
    (Mon-Fri, no holiday calendar per charter frozen mechanics note).
    W=21 → trailing one trading month.
    """
    # numpy.busday_offset with roll='backward' to find the date that is
    # exactly (W-1) business days before d (so [start..d] has W bdays).
    start_np = np.busday_offset(d.isoformat(), -(W - 1), roll="backward")
    return date.fromisoformat(str(start_np))



def _aggregate_dose_window(
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

    Scans ALL index entries for this ticker (not just the triggering filing)
    to build the full window dose.

    Performance kwargs (all optional; pass from build_r1_events for speed):
      ticker_index : dict[ticker_upper → list[filing_record]]  (PERF-03)
      xml_cache    : dict[accession_nodash → (txns, owner_cik, form_10b51)]  (PERF-04)
      subs_cache   : dict[padded_cik → subs_data]  (PERF-02, used for COR-04)

    COR-04: window membership is keyed on each filing's acceptanceDateTime ET date
    (falls back to filed_date when acceptanceDateTime is absent).

    Returns:
        D       — total qualifying purchase dollars (10b5-1 excluded, missing price → $0)
        k       — distinct non-10b5-1 owner CIKs with ≥1 qualifying P purchase
        n_filings_window     — number of qualifying filings in window
        n_10b51_excluded     — count of 10b5-1-excluded transactions
        missing_price_txns   — count of transactions with missing price
    """
    import bisect as _bisect

    window_start = _busday_window_start(event_date, W)

    D: float = 0.0
    owner_has_non10b51: set[str] = set()  # PY-07: set of non-10b5-1 owner CIKs
    n_filings_window: int = 0
    n_10b51_excluded: int = 0
    missing_price_txns: int = 0

    ticker_upper = ticker.upper()

    # PERF-03: use pre-built inverted index if available; fall back to full scan
    if ticker_index is not None:
        filing_records = ticker_index.get(ticker_upper, [])
    else:
        # Legacy fallback: O(n) scan over full index
        filing_records = []
        for entry in index.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("ticker", "").upper() != ticker_upper:
                continue
            if entry.get("status") != "done":
                continue
            entry_cik = entry.get("cik", "")
            if not entry_cik:
                continue
            entry_padded_val = _pad_cik(entry_cik)
            for filing in entry.get("filings", []):
                if filing.get("xml_status") != "ok":
                    continue
                filing_records.append({
                    "entry_padded": entry_padded_val,
                    "entry_cik": entry_cik,
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

        # Fall back to filed_date when acceptanceDateTime is absent
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

        # PERF-04: use cached XML parse result if available
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
            txns, filing_owner_cik, _ = _parse_qualifying_transactions(xml_content)
            if xml_cache is not None:
                xml_cache[accession_nodash] = (txns, filing_owner_cik, None)

        if not txns:
            continue

        n_filings_window += 1

        for txn in txns:
            if txn["is_10b51"]:
                n_10b51_excluded += 1
                continue
            # Non-10b5-1 qualifying purchase
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
# Score computation
# ---------------------------------------------------------------------------

def _compute_score(D: float, k: int, MC: float) -> float:
    """Frozen score formula §2b: log1p(D/MC) * (1 + 0.5*k)."""
    if MC <= 0:
        return 0.0
    return math.log1p(D / MC) * (1.0 + _BETA * k)


def _compute_score_perturb(
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
    """Compute all 9 perturbation scores per §3b.

    PERF-01: one _aggregate_dose_window call per unique W (3 calls max).
    F-floor variants are post-aggregation scalar clamps — no re-aggregation.
    W21_F0 reuses the already-computed (primary_D, primary_k) when provided.

    W21_F0 must equal the primary score (same code path).
    Floor clamp: events with D < F → D clamped to 0 for that variant only (aggregate-level).
    """
    if MC <= 0:
        # MC undefined or zero → all perturb scores are None
        return {v: None for v in _PERTURB_KEY_MAP.values()}

    # PERF-01: aggregate once per unique W; reuse primary W21 result
    w_results: dict[int, tuple[float, int]] = {}
    for W in _PERTURB_WINDOWS:
        if W == _W_PRIMARY and primary_D is not None and primary_k is not None:
            w_results[W] = (primary_D, primary_k)
        else:
            D_w, k_w, _, _, _ = _aggregate_dose_window(
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
# Cached close lookup for MC calculation
# ---------------------------------------------------------------------------

def _get_cached_close(
    ticker: str,
    as_of: date,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    *,
    frame_cache: Optional[dict] = None,
) -> Optional[float]:
    """Return the closing price on the last trading day ≤ as_of.

    Uses loader_fn(ticker) → pd.DataFrame (same contract as event_study).
    Mirrors how event_study handles DatetimeIndex (tz-aware or naive).

    PERF-05: accepts optional frame_cache dict keyed by ticker_upper.
    Caches (df, dates_list) per ticker — avoids repeated loader_fn calls
    for tickers with multiple events. Uses bisect for O(log n) date lookup.

    Returns None if no suitable bar found.
    """
    import bisect as _bisect

    ticker_upper = ticker.upper()

    if frame_cache is not None:
        if ticker_upper not in frame_cache:
            try:
                df = loader_fn(ticker)
            except Exception as exc:
                log.debug("_get_cached_close: loader_fn failed for %s: %s", ticker, exc)
                frame_cache[ticker_upper] = None
            else:
                if df is None or df.empty:
                    frame_cache[ticker_upper] = None
                else:
                    try:
                        dates_list = _frame_dates(df)
                        frame_cache[ticker_upper] = (df, dates_list)
                    except Exception:
                        frame_cache[ticker_upper] = None
        cached = frame_cache.get(ticker_upper)
        if cached is None:
            return None
        df, dates_list = cached
    else:
        try:
            df = loader_fn(ticker)
        except Exception as exc:
            log.debug("_get_cached_close: loader_fn failed for %s: %s", ticker, exc)
            return None
        if df is None or df.empty:
            return None
        try:
            dates_list = _frame_dates(df)
        except Exception:
            return None

    if not dates_list:
        return None

    # PERF-05: bisect to find last date <= as_of (O(log n) vs O(n) linear scan)
    # dates_list is sorted ascending (from _frame_dates)
    pos = _bisect.bisect_right(dates_list, as_of) - 1
    if pos < 0:
        return None

    best_close: Optional[float] = None
    for i in range(pos, -1, -1):
        try:
            row = df.iloc[i]
            close_val = float(row["Close"])
            if not math.isnan(close_val) and close_val > 0:
                best_close = close_val
                break
        except Exception:
            pass

    return best_close


# ---------------------------------------------------------------------------
# Submissions lookup for acceptanceDateTime
# ---------------------------------------------------------------------------

def _get_acceptance_dt_from_subs(
    cik: str,
    accession: str,
    subs_dir: Path,
    *,
    subs_cache: Optional[dict] = None,
) -> Optional[str]:
    """Read acceptanceDateTime for a given (cik, accession) from submissions JSON.

    PERF-02: accepts an optional subs_cache dict keyed by padded_cik.
    Populates the cache on first read; subsequent calls are a dict lookup.

    Returns the raw acceptanceDateTime string if found, else None.
    """
    padded = _pad_cik(cik)

    if subs_cache is not None:
        if padded not in subs_cache:
            subs_path = subs_dir / f"{padded}.json"
            if not subs_path.exists():
                subs_cache[padded] = {}
            else:
                try:
                    subs_cache[padded] = json.loads(subs_path.read_text(encoding="utf-8"))
                except Exception:
                    subs_cache[padded] = {}
        data = subs_cache[padded]
    else:
        subs_path = subs_dir / f"{padded}.json"
        if not subs_path.exists():
            return None
        try:
            data = json.loads(subs_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    filings = data.get("filings", {}).get("recent", {})
    accessions = filings.get("accessionNumber", [])
    acceptance_dts = filings.get("acceptanceDateTime", [])

    for acc, adt in zip(accessions, acceptance_dts):
        if acc == accession:
            return adt if adt else None
    return None


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def build_r1_events(
    start: date,
    end: date,
    *,
    index_path: Path,
    xml_dir: Path,
    subs_dir: Path,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    shares_fn: Optional[Callable[[str, date], Optional[float]]] = None,
) -> tuple[list[EventRecord], dict]:
    """Build R-1 EventRecord list with dose payload.

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

    Returns
    -------
    events : list[EventRecord]
        One EventRecord per (ticker, ET acceptance date) with ≥1 qualifying P
        filing. event_ts = latest qualifying acceptance ts that day.
        payload keys:
          form_type, accession, filing_date, acceptance_fallback,
          score, score_undefined, D, k, MC, n_filings_window,
          n_10b51_excluded, missing_price_txns, score_perturb (9 keys)
    meta : dict
        filings_scanned, filings_qualifying, acceptance_fallbacks,
        n_10b51_excluded_total, missing_price_txns_total,
        score_undefined_total, events_raw, events_returned
    """
    if shares_fn is None:
        shares_fn = _shares_outstanding_disk_only

    # Load index
    try:
        raw_index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("build_r1_events: failed to read index %s: %s", index_path, exc)
        return [], {"error": str(exc)}

    # Strip _meta key if present
    index = {k: v for k, v in raw_index.items() if k != "_meta"}

    # PERF-02: submissions cache (padded_cik → parsed JSON)
    subs_cache: dict = {}

    # PERF-04: XML parse cache (accession_nodash → (txns, owner_cik, form_10b51))
    xml_cache: dict = {}

    # PERF-03: build inverted ticker→filings index once
    # Each entry: {entry_cik, entry_padded, accession, filed, xml_status}
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

    # PERF-05: price frame cache (ticker_upper → (df, dates_list) or None)
    frame_cache: dict = {}

    # Meta counters
    filings_scanned = 0
    filings_qualifying = 0
    acceptance_fallbacks = 0
    n_10b51_excluded_total = 0
    missing_price_txns_total = 0
    score_undefined_total = 0

    # Accumulate per-(ticker, ET date) candidates
    # Key: (ticker_upper, et_date) → list of (event_ts, accession, filed_str, is_fallback, cik)
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

            # PERF-04: parse XML once, cache result
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
                txns_parsed, owner_cik_parsed, form_10b51_parsed = _parse_qualifying_transactions(xml_content)
                xml_cache[accession_nodash] = (txns_parsed, owner_cik_parsed, form_10b51_parsed)

            txns, _owner_cik, form_10b51 = xml_cache[accession_nodash]
            if not txns:
                continue

            # Check whether any non-10b5-1 qualifying transaction exists
            has_non_10b51_qualifying = any(not t["is_10b51"] for t in txns)
            if not has_non_10b51_qualifying:
                # All qualifying transactions are 10b5-1-flagged; skip as triggering filing
                n_10b51_excluded_total += len(txns)
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

    # Now build one EventRecord per (ticker, et_date)
    events: list[EventRecord] = []

    for (ticker, et_date), candidates in day_candidates.items():
        # event_ts = latest qualifying acceptance ts that day
        candidates_sorted = sorted(candidates, key=lambda x: x[0])
        latest = candidates_sorted[-1]
        event_ts, accession, filed_str, is_fallback, cik = latest

        # PERF-01: Aggregate dose window (primary: W=21) — pass caches
        D, k, n_filings_window, n_10b51_exc, missing_price = _aggregate_dose_window(
            ticker, et_date, _W_PRIMARY, xml_dir, index, cik,
            ticker_index=ticker_index,
            xml_cache=xml_cache,
            subs_cache=subs_cache,
        )
        n_10b51_excluded_total += n_10b51_exc
        missing_price_txns_total += missing_price

        # PERF-05: Market cap calculation via frame cache
        shares_outstanding = shares_fn(cik, et_date)
        cached_close = _get_cached_close(ticker, et_date, loader_fn, frame_cache=frame_cache)

        if shares_outstanding is None or cached_close is None:
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
                score = _compute_score(D, k, MC)
                score_undefined = False

        # PERF-01: Perturbation scores — reuse primary (D,k), 3 window calls max
        score_perturb = _compute_score_perturb(
            ticker, et_date, xml_dir, index, cik, MC if MC is not None else 0.0,
            primary_D=D, primary_k=k,
            ticker_index=ticker_index,
            xml_cache=xml_cache,
            subs_cache=subs_cache,
        )
        # If score is None, all perturb scores should be None too
        if score_undefined:
            score_perturb = {v: None for v in _PERTURB_KEY_MAP.values()}

        # Verify W21_F0 == primary score (same code path sanity)
        assert score_perturb.get("W21_F0") == score or (score is None and score_perturb.get("W21_F0") is None), (
            f"W21_F0 mismatch: {score_perturb.get('W21_F0')} vs {score}"
        )

        payload = {
            "form_type": "4",  # event may aggregate 4/4A — use generic form_type
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
        "n_10b51_excluded_total": n_10b51_excluded_total,
        "missing_price_txns_total": missing_price_txns_total,
        "score_undefined_total": score_undefined_total,
        "events_raw": len(day_candidates),
        "events_returned": len(events),
    }
    log.info(
        "build_r1_events: scanned=%d qualifying=%d fallbacks=%d 10b51_excl=%d "
        "missing_price=%d score_undefined=%d events=%d",
        filings_scanned,
        filings_qualifying,
        acceptance_fallbacks,
        n_10b51_excluded_total,
        missing_price_txns_total,
        score_undefined_total,
        len(events),
    )
    return events, meta
