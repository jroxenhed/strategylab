"""F356 — Form 4 Dataset Ingest Layer.

Parses SEC Insider Transactions Data Set quarterly ZIPs
(backend/data/turnaround/edgar_cache/form4_datasets/) into EventRecord-compatible
event streams consumable by the existing dose-window aggregation in r1_dose.py.

Public API:
    build_form4_dataset_events(quarters=None, ...)
        → (list[EventRecord], meta_dict)

Design decisions (orchestrator-pinned, 2026-06-07):
  - CIK-primary universe join (tickers drift; CIKs don't)
  - 10b5-1 semantics replicate r1_dose._is_10b51_text EXACTLY (form-level +
    row-level text scan over transaction string columns)
  - ONE owner CIK per filing = lexicographically smallest RPTOWNERCIK (no
    document-order SK in TSV; flagged approximation)
  - Anchor 1 is EXACT: 7,718 P+A transactions in 2018q1
  - Acceptance-DT helpers imported from event_study (no reimplementation)
  - Older-index pages fetched and cached offline in submissions/older_pages/;
    optional via fetch_missing kwarg

Amendment dedup (F356 fix #1 / ADV-01):
  - Dedup is performed GLOBALLY after all quarters are parsed (cross-quarter
    pairs exist). Key = (issuer_cik, owner_cik, period_of_report).
  - When both '4' and '4/A' share a key, keep ONLY the latest acceptanceDateTime.
  - Tie-break on equal acceptanceDateTimes: higher accession number wins.
  - Controlled by dedup_amendments kwarg (default True). Toggle preserves
    comparability with the XML path at the F354 gate.

Row-level 10b5-1 detection limitation:
  - _txn_row_is_10b51 scans SECURITY_TITLE only. TRANS_FORM_TYPE carries short
    codes ('4', '4/A') that never contain 10b5-1 text — removed from _TXN_TEXT_COLS.
  - Per-transaction footnote text is NOT available in the flat TSV (footnotes table
    is keyed by accession only, no row-level join). Row-level detection is therefore
    limited to SECURITY_TITLE; the form-level scan (REMARKS + all footnotes) is the
    primary exclusion gate.
  - ADV-03 deferred: a new charter would be required to change frozen exclusion
    semantics for mixed (some 10b5-1, some discretionary) forms.

Universe note (COR-01):
  - build_universe(raw, params=None) applies structural filters only (ticker
    length, special chars, junk suffixes, ETF/Trust/SPAC by title). No
    price/volume floors are applied at ingest time.
  - The R-1 study universe (_build_universe_tickers in run_r1_explore.py) is
    built from price-cache coverage (2012-2021 span) + non-zero SIC — a
    fundamentally different construction that cannot be unified here without
    adding a date-pinned price-cache dependency to the ingest. COR-01 NOT-APPLIED;
    evidence documented in .run/F356/fix-wave.md.

All constants frozen per the R-1 charter. Do NOT tune post-hoc.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

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
from research.r1_dose import _is_10b51_text  # noqa: E402 — reuse exact regexes

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default paths (repo-relative, resolved from this file's location)
# ---------------------------------------------------------------------------
_DEFAULT_DATASET_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "form4_datasets"
_DEFAULT_SUBMISSIONS_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "submissions"

# SEC EDGAR fair-use header (mirrors fetch_form4_datasets.py)
_UA = "StrategyLab research john@milford.se"
_SUBS_BASE_URL = "https://data.sec.gov/submissions/{name}"
# Pace between network fetches for older-index pages (SEC fair-access: ~0.15s)
_FETCH_PACE_SECS = 0.15
_last_fetch_time: float = 0.0  # single-threaded only; see DI-05 / DEFER note

# Document types to include (charter §2a)
_INCLUDE_DOC_TYPES: frozenset[str] = frozenset({"4", "4/A"})

# Transaction filter constants
_TRANS_CODE_BUY = "P"
_ADC_ACQUIRED = "A"

# TSV string columns scanned for row-level 10b5-1 markers.
# NOTE: TRANS_FORM_TYPE removed (carries only '4'/'4/A' codes, never 10b5-1 text).
# Per-transaction footnote text is not available in the flat TSV — form-level
# scan (REMARKS + all FOOTNOTE_TXT) is the primary exclusion gate (see module docstring).
_TXN_TEXT_COLS = [
    "SECURITY_TITLE",
]


# ---------------------------------------------------------------------------
# Universe loader
# ---------------------------------------------------------------------------

def _load_liquid_universe() -> dict[int, str]:
    """Load the structural universe and return {cik_int: primary_ticker} map.

    Uses build_universe() which applies structural exclusions (ticker length,
    special chars, junk suffixes, ETF/Trust/SPAC by title). No price/volume
    floors are applied — those require run_filter() with a date as_of, which
    is incompatible with historical ingest. Callers that need the floor-checked
    ~4,700-ticker R-1 set should apply their own filter downstream.

    For CIKs with multiple tickers, the FIRST occurrence from build_universe
    is treated as the primary ticker (it consistently returns common stock first).

    Returns a dict keyed by int-normalised CIK (so TSV's '0000036270' → 36270).
    """
    from turnaround import build_universe  # noqa: E402
    from edgar import fetch_universe  # noqa: E402

    raw = fetch_universe()
    pairs = build_universe(raw, params=None)  # list[(ticker, cik_str)]

    cik_to_ticker: dict[int, str] = {}
    for ticker, cik_str in pairs:
        cik_int = int(cik_str)
        if cik_int not in cik_to_ticker:
            cik_to_ticker[cik_int] = ticker
    return cik_to_ticker


# ---------------------------------------------------------------------------
# Filing date parser (TSV uses DD-MMM-YYYY)
# ---------------------------------------------------------------------------

def _parse_filing_date(dd_mmm_yyyy: str) -> Optional[date]:
    """Parse TSV FILING_DATE string 'DD-MMM-YYYY' → date.

    Returns None on any parse failure.
    """
    if not dd_mmm_yyyy or pd.isna(dd_mmm_yyyy):
        return None
    try:
        return datetime.strptime(str(dd_mmm_yyyy).strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Acceptance-DT helpers
# ---------------------------------------------------------------------------

def _pace_fetch() -> None:
    """Sleep if needed to stay within SEC fair-access rate."""
    global _last_fetch_time
    now = time.monotonic()
    elapsed = now - _last_fetch_time
    wait = max(0.0, _FETCH_PACE_SECS - elapsed)
    if wait > 0:
        time.sleep(wait)
    _last_fetch_time = time.monotonic()


def _fetch_older_page(name: str, cache_dir: Path) -> Optional[dict]:
    """Fetch and cache an older submissions page by 'name' (e.g. CIK...-submissions-001.json).

    Cache lives in cache_dir (submissions/older_pages/) — isolated from the
    primary CIK-level submissions cache (DI-03 fix).

    Write is atomic: JSON is parsed FIRST; if the body is non-JSON the bytes
    are never written to disk and None is returned (DI-01 fix).

    Returns the parsed JSON dict, or None on failure.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / name
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("_fetch_older_page: stale/poisoned cache %s: %s", cache_path, exc)
            return None

    url = _SUBS_BASE_URL.format(name=name)
    _pace_fetch()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        # DI-01: parse JSON FIRST; only write on success (atomic tmp → rename)
        parsed = json.loads(raw.decode("utf-8"))
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(cache_path)
        return parsed
    except Exception as exc:
        log.debug("_fetch_older_page: failed to fetch %s: %s", url, exc)
        return None


def _get_acceptance_dt_from_subs(
    padded_cik: str,
    accession_with_dashes: str,
    subs_dir: Path,
    *,
    subs_cache: Optional[dict] = None,
    fetch_missing: bool = True,
) -> tuple[Optional[str], str]:
    """Look up acceptanceDateTime in the submissions JSON (and optionally older pages).

    Returns (adt_str_or_None, source) where source ∈
    {"direct_hit", "older_index_fetch", "no_subs_file", "not_found"}.
    """
    # Load main submissions JSON (cached)
    if subs_cache is not None:
        if padded_cik not in subs_cache:
            subs_path = subs_dir / f"{padded_cik}.json"
            if not subs_path.exists():
                subs_cache[padded_cik] = {}  # COR-04: use {} not None for sentinel
            else:
                try:
                    subs_cache[padded_cik] = json.loads(subs_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.debug("_get_acceptance_dt_from_subs: parse error %s: %s",
                              padded_cik, exc)
                    subs_cache[padded_cik] = {}
        data = subs_cache[padded_cik]
    else:
        subs_path = subs_dir / f"{padded_cik}.json"
        if not subs_path.exists():
            return None, "no_subs_file"
        try:
            data = json.loads(subs_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("_get_acceptance_dt_from_subs: parse error %s: %s", padded_cik, exc)
            return None, "no_subs_file"

    if not data:  # handles both None (legacy) and {} (missing/unreadable)
        return None, "no_subs_file"

    # Search recent filings
    filings_block = data.get("filings", {}).get("recent", {})
    acc_list = filings_block.get("accessionNumber", [])
    adt_list = filings_block.get("acceptanceDateTime", [])
    for acc_s, adt_s in zip(acc_list, adt_list):
        if acc_s == accession_with_dashes:
            if adt_s:
                return adt_s, "direct_hit"
            # Field present but empty — fall through to older pages
            break

    # Try older index pages (Fork A) — cached in dedicated older_pages/ subdir (DI-03)
    if fetch_missing:
        older_files = data.get("filings", {}).get("files", [])
        older_pages_dir = subs_dir / "older_pages"
        for page_rec in older_files:
            page_name = page_rec.get("name", "")
            if not page_name:
                continue
            page_data = _fetch_older_page(page_name, older_pages_dir)
            if page_data is None:
                continue
            p_accs = page_data.get("accessionNumber", [])
            p_adts = page_data.get("acceptanceDateTime", [])
            for acc_s, adt_s in zip(p_accs, p_adts):
                if acc_s == accession_with_dashes:
                    if adt_s:
                        return adt_s, "older_index_fetch"
                    continue  # DI-04: was break; continue to exhaust same-page rows

    return None, "not_found"


def _resolve_event_ts(
    padded_cik: str,
    accession_with_dashes: str,
    filing_date_iso: str,
    subs_dir: Path,
    *,
    subs_cache: Optional[dict] = None,
    fetch_missing: bool = True,
) -> tuple[Optional[datetime], bool, str]:
    """Resolve event_ts for a filing.

    Returns (event_ts_utc_or_None, is_fallback, source_str).
    source_str ∈ {"direct_hit", "older_index_fetch", "filing_date_fallback",
                  "no_timestamp"}.

    Returns (None, True, "no_timestamp") when FILING_DATE is absent/unparseable
    AND no ADT is found — caller MUST check for None and drop the event (DI-02).
    Never emits 1970 epoch sentinels.
    """
    adt_str, source = _get_acceptance_dt_from_subs(
        padded_cik, accession_with_dashes, subs_dir,
        subs_cache=subs_cache,
        fetch_missing=fetch_missing,
    )

    if adt_str:
        event_ts = _parse_acceptance_dt(adt_str)
        if event_ts is not None:
            return event_ts, False, source

    # Fallback: filing_date + 16:01 ET (from event_study._filing_date_fallback_dt)
    fallback_ts = _filing_date_fallback_dt(filing_date_iso)
    if fallback_ts is not None:
        return fallback_ts, True, "filing_date_fallback"

    # DI-02: no timestamp available — drop event, never emit epoch sentinel
    log.warning(
        "_resolve_event_ts: no timestamp for %s / %s — dropping event",
        padded_cik, accession_with_dashes,
    )
    return None, True, "no_timestamp"


# ---------------------------------------------------------------------------
# 10b5-1 TSV-level helpers
# ---------------------------------------------------------------------------

def _txn_row_is_10b51(row: pd.Series) -> bool:
    """Return True if a NONDERIV_TRANS row carries a 10b5-1 marker in its text columns.

    Checks SECURITY_TITLE only (TRANS_FORM_TYPE carries only '4'/'4/A' codes
    and can never match 10b5-1 patterns — see module docstring for structural gap).
    """
    for col in _TXN_TEXT_COLS:
        val = row.get(col)
        if val and not pd.isna(val) and _is_10b51_text(str(val)):
            return True
    return False


def _form_is_10b51_tsv(
    accession: str,
    remarks: Optional[str],
    footnotes_by_acc: dict[str, list[str]],
) -> bool:
    """Return True if the form carries a 10b5-1 marker at form level.

    Scans:
      (a) SUBMISSION.REMARKS
      (b) ALL FOOTNOTE_TXT rows for this accession
    If any text matches, returns True — which excludes EVERY transaction in the form.
    """
    if remarks and not pd.isna(remarks) and _is_10b51_text(str(remarks)):
        return True
    for fnote_text in footnotes_by_acc.get(accession, []):
        if fnote_text and _is_10b51_text(fnote_text):
            return True
    return False


# ---------------------------------------------------------------------------
# ZIP table loader
# ---------------------------------------------------------------------------

def _load_quarter_tables(
    zip_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the four relevant TSV tables from a quarterly ZIP.

    Returns (submission_df, reportingowner_df, nonderiv_df, footnotes_df).

    Uses dtype=str throughout to avoid pandas coercing CIK-like columns.
    Uses sep='\\t' with quoting=csv.QUOTE_NONE to handle embedded quote chars
    in footnote text without mangling.
    """
    import csv

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        def _read(table_name: str) -> pd.DataFrame:
            if table_name not in names:
                log.warning("_load_quarter_tables: %s not in %s", table_name, zip_path.name)
                return pd.DataFrame()
            with zf.open(table_name) as f:
                try:
                    df = pd.read_csv(
                        f,
                        sep="\t",
                        dtype=str,
                        quoting=csv.QUOTE_NONE,
                        on_bad_lines="skip",
                        encoding="utf-8",
                    )
                except Exception as exc:
                    log.warning("_load_quarter_tables: read error %s/%s: %s", zip_path.name, table_name, exc)
                    return pd.DataFrame()
            return df

        sub_df = _read("SUBMISSION.tsv")
        owner_df = _read("REPORTINGOWNER.tsv")
        nd_df = _read("NONDERIV_TRANS.tsv")
        fn_df = _read("FOOTNOTES.tsv")

    return sub_df, owner_df, nd_df, fn_df


# ---------------------------------------------------------------------------
# Core quarter processor
# ---------------------------------------------------------------------------

def _process_quarter(
    zip_path: Path,
    quarter: str,
    cik_to_ticker: dict[int, str],
    *,
    subs_dir: Path,
    subs_cache: Optional[dict] = None,
    fetch_missing: bool = True,
) -> tuple[list[EventRecord], dict]:
    """Parse one quarterly ZIP and return (events, per_quarter_stats).

    Owner CIK per filing = lexicographically smallest RPTOWNERCIK
    (no document-order SK in TSV; flagged approximation — see impl-ingest.md).

    Ticker resolution (ADV-02 fix):
      event ticker = ISSUERTRADINGSYMBOL (upper) when that symbol maps back to
      the SAME CIK in the universe; else fall back to the CIK's primary universe
      ticker and increment n_ticker_fallback. Payload records tsv_symbol and
      universe_symbol whenever they differ.
    """
    sub_df, owner_df, nd_df, fn_df = _load_quarter_tables(zip_path)

    if sub_df.empty:
        log.warning("_process_quarter: empty SUBMISSION.tsv in %s", zip_path.name)
        return [], _empty_qstats()

    # -----------------------------------------------------------------------
    # Filter SUBMISSION to Form 4/4/A only
    # -----------------------------------------------------------------------
    if "DOCUMENT_TYPE" in sub_df.columns:
        sub_df = sub_df[sub_df["DOCUMENT_TYPE"].isin(_INCLUDE_DOC_TYPES)].copy()
    submissions_scanned = len(sub_df)

    # -----------------------------------------------------------------------
    # Build reverse-lookup for ticker resolution (PY-01/ADV-06: O(1) per row)
    # -----------------------------------------------------------------------
    # Map ticker_upper → cik_int for the same-CIK check in ADV-02
    ticker_upper_to_cik: dict[str, int] = {v.upper(): k for k, v in cik_to_ticker.items()}
    # Set of all universe tickers (for O(1) membership test — PY-01/ADV-06)
    universe_ticker_set: frozenset[str] = frozenset(ticker_upper_to_cik.keys())

    # -----------------------------------------------------------------------
    # Universe filter: CIK-primary (tickers drift; CIKs don't)
    # -----------------------------------------------------------------------
    universe_pass_rows = []
    universe_fail = 0
    cik_match = ticker_match = both_match = disagree = 0
    n_ticker_fallback = 0

    for _, row in sub_df.iterrows():
        issuer_cik_raw = row.get("ISSUERCIK", "")
        issuer_sym = row.get("ISSUERTRADINGSYMBOL", "")
        if not issuer_cik_raw or pd.isna(issuer_cik_raw):
            universe_fail += 1
            continue
        try:
            cik_int = int(issuer_cik_raw)
        except (ValueError, TypeError):
            universe_fail += 1
            continue

        matched_ticker = cik_to_ticker.get(cik_int)

        # Count stats — O(1) per row with frozenset (PY-01/ADV-06)
        sym_upper = str(issuer_sym).upper() if issuer_sym and not pd.isna(issuer_sym) else ""
        sym_in_univ = sym_upper in universe_ticker_set if sym_upper else False

        if matched_ticker is not None and sym_in_univ:
            both_match += 1
            if matched_ticker.upper() != sym_upper:
                disagree += 1
        elif matched_ticker is not None:
            cik_match += 1
        elif sym_in_univ:
            ticker_match += 1

        if matched_ticker is None:
            universe_fail += 1
            continue

        # ADV-02: prefer ISSUERTRADINGSYMBOL when it maps to the SAME CIK in universe
        effective_ticker = matched_ticker
        tsv_symbol = None
        universe_symbol = None
        if sym_upper and sym_upper in ticker_upper_to_cik:
            if ticker_upper_to_cik[sym_upper] == cik_int:
                # TSV symbol resolves to the correct CIK — use it directly
                effective_ticker = sym_upper
            else:
                # TSV symbol maps to a DIFFERENT CIK (era-drift / ticker reuse)
                # → fall back to universe's primary ticker, record mismatch
                effective_ticker = matched_ticker
                tsv_symbol = sym_upper
                universe_symbol = matched_ticker.upper()
                n_ticker_fallback += 1
        elif sym_upper and sym_upper not in ticker_upper_to_cik:
            # TSV symbol unknown to the universe map (era drift / secondary listing
            # / rename). TRUST the issuer's own filed symbol: pricing it via the
            # CIK's primary can hit a different instrument entirely (ADV-02:
            # Navient files under NAVI; its map primary JSM is a $25-par note —
            # wrong returns AND wrong market cap for D/MC). An era-correct symbol
            # with no price frame is excluded+counted downstream — honest loss
            # beats silent wrong-instrument pricing.
            if sym_upper != matched_ticker.upper():
                effective_ticker = sym_upper
                tsv_symbol = sym_upper
                universe_symbol = matched_ticker.upper()
                n_ticker_fallback += 1

        universe_pass_rows.append({
            "accession": row["ACCESSION_NUMBER"],
            "filing_date_raw": row.get("FILING_DATE", ""),
            "period_of_report": row.get("PERIOD_OF_REPORT", ""),
            "remarks": row.get("REMARKS", ""),
            "issuer_cik": str(cik_int),
            "padded_cik": str(cik_int).zfill(10),
            "ticker": effective_ticker,
            "doc_type": row.get("DOCUMENT_TYPE", "4"),
            "tsv_symbol": tsv_symbol,
            "universe_symbol": universe_symbol,
        })

    submissions_universe_pass = len(universe_pass_rows)

    # -----------------------------------------------------------------------
    # Build lookup structures (PY-02: vectorized where straightforward)
    # -----------------------------------------------------------------------

    # footnotes: accession → list of footnote texts (vectorized groupby)
    footnotes_by_acc: dict[str, list[str]] = {}
    if not fn_df.empty and "ACCESSION_NUMBER" in fn_df.columns and "FOOTNOTE_TXT" in fn_df.columns:
        fn_clean = fn_df[
            fn_df["ACCESSION_NUMBER"].notna() & fn_df["FOOTNOTE_TXT"].notna()
        ].copy()
        fn_clean["ACCESSION_NUMBER"] = fn_clean["ACCESSION_NUMBER"].astype(str)
        fn_clean["FOOTNOTE_TXT"] = fn_clean["FOOTNOTE_TXT"].astype(str)
        footnotes_by_acc = (
            fn_clean.groupby("ACCESSION_NUMBER")["FOOTNOTE_TXT"]
            .apply(list)
            .to_dict()
        )

    # reporting owner: accession → sorted list of owner CIKs (vectorized groupby)
    owner_by_acc: dict[str, list[str]] = {}
    if not owner_df.empty and "ACCESSION_NUMBER" in owner_df.columns and "RPTOWNERCIK" in owner_df.columns:
        own_clean = owner_df[
            owner_df["ACCESSION_NUMBER"].notna() & owner_df["RPTOWNERCIK"].notna()
        ].copy()
        own_clean["ACCESSION_NUMBER"] = own_clean["ACCESSION_NUMBER"].astype(str)
        own_clean["RPTOWNERCIK"] = own_clean["RPTOWNERCIK"].astype(str)
        owner_by_acc = (
            own_clean.groupby("ACCESSION_NUMBER")["RPTOWNERCIK"]
            .apply(lambda s: sorted(s.tolist()))
            .to_dict()
        )

    # NONDERIV_TRANS: accession → list of P+A transaction dicts
    # Uses itertuples for the inner loop (faster than iterrows; vectorized
    # numeric conversion applied on the filtered slice first)
    nd_pa: dict[str, list[dict]] = defaultdict(list)
    qualifying_txns_total = 0
    if not nd_df.empty:
        required_cols = {"ACCESSION_NUMBER", "TRANS_CODE", "TRANS_ACQUIRED_DISP_CD"}
        if required_cols.issubset(set(nd_df.columns)):
            p_mask = (nd_df["TRANS_CODE"] == _TRANS_CODE_BUY) & (nd_df["TRANS_ACQUIRED_DISP_CD"] == _ADC_ACQUIRED)
            pa_df = nd_df[p_mask].copy()
            qualifying_txns_total = len(pa_df)

            # Vectorized numeric conversion (PY-02)
            # NOTE: avoid underscore-prefix column names — itertuples renames them to _N
            pa_df = pa_df.copy()
            pa_df["SHARES_NUM"] = pd.to_numeric(
                pa_df["TRANS_SHARES"] if "TRANS_SHARES" in pa_df.columns else pd.Series(dtype=str),
                errors="coerce",
            )
            pa_df["PRICE_NUM"] = pd.to_numeric(
                pa_df["TRANS_PRICEPERSHARE"] if "TRANS_PRICEPERSHARE" in pa_df.columns else pd.Series(dtype=str),
                errors="coerce",
            )

            for row_t in pa_df.itertuples(index=False):
                acc = getattr(row_t, "ACCESSION_NUMBER", "")
                if not acc or (isinstance(acc, float) and pd.isna(acc)):
                    continue
                shares_val = getattr(row_t, "SHARES_NUM", float("nan"))
                price_val = getattr(row_t, "PRICE_NUM", float("nan"))
                shares = None if (shares_val != shares_val) else float(shares_val)  # NaN check
                price = None if (price_val != price_val) else float(price_val)
                # Row-level 10b5-1 scan (SECURITY_TITLE only)
                sec_title = getattr(row_t, "SECURITY_TITLE", "")
                txn_10b51 = bool(
                    sec_title and not (isinstance(sec_title, float) and sec_title != sec_title)
                    and _is_10b51_text(str(sec_title))
                )
                nd_pa[str(acc)].append({
                    "shares": shares,
                    "price": price,
                    "txn_10b51": txn_10b51,
                })

    # -----------------------------------------------------------------------
    # Build events
    # -----------------------------------------------------------------------
    events: list[EventRecord] = []
    n_10b51_excluded = 0
    missing_price_txns = 0
    n_multi_owner_forms = 0
    acceptances_direct_hit = 0
    acceptances_fetched = 0
    acceptances_fallback = 0
    n_no_timestamp_dropped = 0
    n_midnight_utc_adt = 0
    amendments = 0

    for sub_rec in universe_pass_rows:
        accession = sub_rec["accession"]
        filing_date_raw = sub_rec["filing_date_raw"]
        period_of_report = sub_rec["period_of_report"]
        remarks = sub_rec["remarks"]
        padded_cik = sub_rec["padded_cik"]
        ticker = sub_rec["ticker"]
        doc_type = sub_rec["doc_type"]

        if doc_type == "4/A":
            amendments += 1

        # Get P+A transactions for this filing
        txns = nd_pa.get(accession, [])
        if not txns:
            continue

        # Form-level 10b5-1 check
        form_10b51 = _form_is_10b51_tsv(accession, remarks, footnotes_by_acc)

        # Filter transactions
        non_10b51_txns = []
        excl_count = 0
        for txn in txns:
            # If form is 10b5-1, ALL transactions excluded
            if form_10b51 or txn["txn_10b51"]:
                excl_count += 1
            else:
                non_10b51_txns.append(txn)
        n_10b51_excluded += excl_count

        if not non_10b51_txns:
            continue

        # Compute D (total dollar value) and count missing-price txns
        D = 0.0
        mp_count = 0
        for txn in non_10b51_txns:
            price = txn["price"]
            shares = txn["shares"]
            if price is None or price <= 0:
                mp_count += 1
                D += 0.0
            else:
                D += (shares or 0.0) * price
        missing_price_txns += mp_count

        # Owner CIK: lexicographically smallest RPTOWNERCIK (flagged approximation)
        owner_ciks = owner_by_acc.get(accession, [])
        if len(owner_ciks) > 1:
            n_multi_owner_forms += 1
        # k = distinct owner CIK count (non-10b5-1 owners)
        k = len(owner_ciks) if owner_ciks else 1
        owner_cik = owner_ciks[0] if owner_ciks else ""

        # Parse filing date to ISO for fallback helper
        filing_date_obj = _parse_filing_date(filing_date_raw)
        filing_date_iso = filing_date_obj.isoformat() if filing_date_obj else ""

        # Resolve acceptanceDateTime
        event_ts, is_fallback, adt_source = _resolve_event_ts(
            padded_cik,
            accession,
            filing_date_iso,
            subs_dir,
            subs_cache=subs_cache,
            fetch_missing=fetch_missing,
        )

        # DI-02: drop events with no resolvable timestamp
        if event_ts is None:
            n_no_timestamp_dropped += 1
            continue

        # ADV-04: flag midnight-UTC ADTs (potential data quality artifact)
        adt_midnight_utc = False
        if not is_fallback and event_ts.hour == 0 and event_ts.minute == 0 and event_ts.second == 0:
            adt_midnight_utc = True
            n_midnight_utc_adt += 1

        # Count acceptance stats
        if adt_source == "direct_hit":
            acceptances_direct_hit += 1
        elif adt_source == "older_index_fetch":
            acceptances_fetched += 1
        elif adt_source != "no_timestamp":
            acceptances_fallback += 1

        payload = {
            "form_type": doc_type,
            "accession": accession,
            "filing_date": filing_date_iso,
            "period_of_report": period_of_report,
            "acceptance_fallback": is_fallback,
            "acceptance_dt_source": adt_source,
            "adt_midnight_utc": adt_midnight_utc,
            "D": D,
            "k": k,
            "n_txns_qualifying": len(non_10b51_txns),
            "n_10b51_excluded": excl_count,
            "missing_price_txns": mp_count,
            "owner_cik": owner_cik,
            "owner_ciks": owner_ciks,
        }
        # Record ticker mismatch metadata if present
        if sub_rec["tsv_symbol"] is not None:
            payload["tsv_symbol"] = sub_rec["tsv_symbol"]
            payload["universe_symbol"] = sub_rec["universe_symbol"]

        ev = EventRecord(
            ticker=ticker,
            event_ts=event_ts,
            payload=payload,
            is_fallback=is_fallback,
        )
        events.append(ev)

    qstats = {
        "submissions_scanned": submissions_scanned,
        "submissions_universe_pass": submissions_universe_pass,
        "submissions_universe_fail": universe_fail,
        "cik_match_only": cik_match,
        "ticker_match_only": ticker_match,
        "both_match": both_match,
        "disagree": disagree,
        "qualifying_txns_raw": qualifying_txns_total,
        "form4_10b51_excluded_txns": n_10b51_excluded,
        "missing_price_txns": missing_price_txns,
        "events_qualifying": len(events),
        "amendments": amendments,
        "n_multi_owner_forms": n_multi_owner_forms,
        "acceptances_direct_hit": acceptances_direct_hit,
        "acceptances_fetched": acceptances_fetched,
        "acceptances_fallback": acceptances_fallback,
        "n_ticker_fallback": n_ticker_fallback,
        "n_no_timestamp_dropped": n_no_timestamp_dropped,
        "n_midnight_utc_adt": n_midnight_utc_adt,
    }
    return events, qstats


def _empty_qstats() -> dict:
    return {
        "submissions_scanned": 0,
        "submissions_universe_pass": 0,
        "submissions_universe_fail": 0,
        "cik_match_only": 0,
        "ticker_match_only": 0,
        "both_match": 0,
        "disagree": 0,
        "qualifying_txns_raw": 0,
        "form4_10b51_excluded_txns": 0,
        "missing_price_txns": 0,
        "events_qualifying": 0,
        "amendments": 0,
        "n_multi_owner_forms": 0,
        "acceptances_direct_hit": 0,
        "acceptances_fetched": 0,
        "acceptances_fallback": 0,
        "n_ticker_fallback": 0,
        "n_no_timestamp_dropped": 0,
        "n_midnight_utc_adt": 0,
        "n_superseded_dropped": 0,
    }


# ---------------------------------------------------------------------------
# Quarter discovery
# ---------------------------------------------------------------------------

def _discover_quarters(dataset_dir: Path) -> list[str]:
    """Return sorted list of quarter codes found in dataset_dir."""
    quarters = []
    for p in dataset_dir.iterdir():
        if p.suffix == ".zip" and p.stem.endswith("_form345"):
            q = p.stem.replace("_form345", "")
            quarters.append(q)
    return sorted(quarters)


# ---------------------------------------------------------------------------
# Amendment dedup (ADV-01 / fix #1)
# ---------------------------------------------------------------------------

def _dedup_amendments(events: list[EventRecord]) -> tuple[list[EventRecord], int]:
    """Global amendment dedup pass (cross-quarter pairs supported).

    Key = (ticker, owner_cik, period_of_report). When both a '4' and a '4/A'
    (or multiple 4/As) share a key, keep ONLY the one with the latest
    acceptanceDateTime. Tie-break: higher accession number string wins.

    Returns (deduplicated_events, n_superseded_dropped, n_dup4_collisions).
    n_dup4_collisions counts ambiguous all-original-'4' key collisions, which
    are KEPT (not merged) and reported for the F354 gate audit.
    """
    from datetime import timezone as _tz

    _EPOCH = datetime(1970, 1, 1, tzinfo=_tz.utc)

    # Group by dedup key
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, ev in enumerate(events):
        owner_cik = ev.payload.get("owner_cik", "")
        period = ev.payload.get("period_of_report", "")
        # Key on issuer CIK, not ticker: ticker is a derived, resolution-dependent
        # value (ADV-02), so an original 4 and its 4/A could resolve differently
        # and dodge dedup. CIK is the stable issuer identity.
        key = (ev.payload.get("issuer_cik", ""), owner_cik, period)
        groups[key].append(i)

    keep_indices: set[int] = set()
    n_superseded = 0
    n_dup4_collisions = 0

    for key, indices in groups.items():
        if len(indices) == 1:
            keep_indices.add(indices[0])
            continue

        # Check if we have a mix of '4' and '4/A'
        has_amendment = any(
            events[i].payload.get("form_type") == "4/A" for i in indices
        )
        if not has_amendment:
            # All plain '4's sharing (issuer, owner, period): ambiguous class —
            # usually accidental double-submissions, occasionally legitimate
            # distinct filings. Merging would silently drop real dollars from D
            # (beyond ADV-01's reviewed supersession scope), so KEEP ALL and
            # count the collision group for the F354 gate audit.
            keep_indices.update(indices)
            n_dup4_collisions += 1
            continue

        # Pick the winner: latest acceptanceDateTime; tie-break = higher accession
        best_idx = indices[0]
        best_ts = events[best_idx].event_ts if not events[best_idx].is_fallback else _EPOCH
        best_acc = events[best_idx].payload.get("accession", "")

        for i in indices[1:]:
            ev = events[i]
            ev_ts = ev.event_ts if not ev.is_fallback else _EPOCH
            ev_acc = ev.payload.get("accession", "")
            if ev_ts > best_ts or (ev_ts == best_ts and ev_acc > best_acc):
                best_idx = i
                best_ts = ev_ts
                best_acc = ev_acc

        keep_indices.add(best_idx)
        n_superseded += len(indices) - 1

    deduplicated = [ev for i, ev in enumerate(events) if i in keep_indices]
    return deduplicated, n_superseded, n_dup4_collisions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_form4_dataset_events(
    quarters: Optional[list[str]] = None,
    *,
    dataset_dir: Path = _DEFAULT_DATASET_DIR,
    submissions_dir: Path = _DEFAULT_SUBMISSIONS_DIR,
    fetch_missing: bool = True,
    dedup_amendments: bool = True,
) -> tuple[list[EventRecord], dict]:
    """Parse SEC Insider Transactions Data Set quarterly tables into event records.

    Parameters
    ----------
    quarters : list[str], optional
        Quarter codes to process (e.g. ["2018q1", "2018q2"]).
        If None, process all quarters found in dataset_dir.
    dataset_dir : Path
        Root directory containing {quarter}_form345.zip files.
    submissions_dir : Path
        Root directory containing {padded_cik}.json submissions files.
    fetch_missing : bool
        If True (default), fetch older EDGAR submissions pages for CIKs
        whose recent filings block doesn't cover the filing's accession.
        Set False for offline/test runs.
    dedup_amendments : bool
        If True (default), apply global amendment dedup: for each
        (ticker, owner_cik, period_of_report) key that has both a '4' and
        a '4/A', keep only the one with the latest acceptanceDateTime.
        Toggle to False for comparability with the XML path (which does not
        dedup). Controlled at the F354 gate.

    Returns
    -------
    events : list[EventRecord]
        One EventRecord per qualifying (ticker, acceptanceDateTime) filing with
        ≥1 non-10b5-1 P+A transaction. Cross-quarter amendment dedup applied
        when dedup_amendments=True.
    meta : dict
        Aggregate stats across all processed quarters, plus per_quarter breakdown.
    """
    dataset_dir = Path(dataset_dir)
    submissions_dir = Path(submissions_dir)

    if quarters is None:
        quarters = _discover_quarters(dataset_dir)

    log.info("build_form4_dataset_events: processing %d quarters, fetch_missing=%s",
             len(quarters), fetch_missing)

    # Load universe once
    cik_to_ticker = _load_liquid_universe()
    log.info("Universe loaded: %d CIKs", len(cik_to_ticker))

    # Shared submissions cache (padded_cik → parsed JSON or {})
    subs_cache: dict = {}

    all_events: list[EventRecord] = []
    per_quarter: dict[str, dict] = {}

    # Aggregate meta counters
    total_submissions_scanned = 0
    total_universe_pass = 0
    total_universe_fail = 0
    total_qualifying_txns = 0
    total_10b51_excluded = 0
    total_missing_price = 0
    total_direct_hit = 0
    total_fetched = 0
    total_fallback = 0
    total_amendments = 0
    total_ticker_fallback = 0
    total_no_timestamp_dropped = 0
    total_midnight_utc_adt = 0

    for quarter in quarters:
        zip_path = dataset_dir / f"{quarter}_form345.zip"
        if not zip_path.exists():
            log.warning("build_form4_dataset_events: ZIP not found: %s", zip_path)
            continue

        log.info("Processing %s ...", quarter)
        events_q, qstats = _process_quarter(
            zip_path, quarter, cik_to_ticker,
            subs_dir=submissions_dir,
            subs_cache=subs_cache,
            fetch_missing=fetch_missing,
        )
        all_events.extend(events_q)
        per_quarter[quarter] = qstats

        total_submissions_scanned += qstats["submissions_scanned"]
        total_universe_pass += qstats["submissions_universe_pass"]
        total_universe_fail += qstats["submissions_universe_fail"]
        total_qualifying_txns += qstats["qualifying_txns_raw"]
        total_10b51_excluded += qstats["form4_10b51_excluded_txns"]
        total_missing_price += qstats["missing_price_txns"]
        total_direct_hit += qstats["acceptances_direct_hit"]
        total_fetched += qstats["acceptances_fetched"]
        total_fallback += qstats["acceptances_fallback"]
        total_amendments += qstats["amendments"]
        total_ticker_fallback += qstats["n_ticker_fallback"]
        total_no_timestamp_dropped += qstats["n_no_timestamp_dropped"]
        total_midnight_utc_adt += qstats["n_midnight_utc_adt"]

        log.info(
            "  %s: sub_scanned=%d, univ_pass=%d, txns_pa=%d, 10b51_excl=%d, events=%d",
            quarter, qstats["submissions_scanned"], qstats["submissions_universe_pass"],
            qstats["qualifying_txns_raw"], qstats["form4_10b51_excluded_txns"],
            qstats["events_qualifying"],
        )

    # Global amendment dedup (cross-quarter pairs require post-collection pass)
    n_superseded_dropped = 0
    n_dup4_collisions = 0
    if dedup_amendments and all_events:
        all_events, n_superseded_dropped, n_dup4_collisions = _dedup_amendments(all_events)
        log.info(
            "Amendment dedup: %d superseded events dropped (%d remaining); "
            "%d ambiguous all-original key collisions KEPT (F354 gate audit)",
            n_superseded_dropped, len(all_events), n_dup4_collisions,
        )

    meta = {
        "quarters_processed": len(per_quarter),
        "submissions_scanned": total_submissions_scanned,
        "submissions_universe_pass": total_universe_pass,
        "submissions_universe_fail": total_universe_fail,
        "form4_qualified_txns": total_qualifying_txns,
        "form4_10b51_excluded_txns": total_10b51_excluded,
        "form4_missing_price_txns": total_missing_price,
        "events_qualifying": len(all_events),
        "events_returned": len(all_events),
        "acceptances_direct_hit": total_direct_hit,
        "acceptances_fetched": total_fetched,
        "acceptances_fallback": total_fallback,
        "amendments_included": total_amendments,
        "n_superseded_dropped": n_superseded_dropped,
        "n_dup4_collisions": n_dup4_collisions,
        "n_ticker_fallback": total_ticker_fallback,
        "n_no_timestamp_dropped": total_no_timestamp_dropped,
        "n_midnight_utc_adt": total_midnight_utc_adt,
        "per_quarter": per_quarter,
    }

    log.info(
        "build_form4_dataset_events: done. quarters=%d events=%d "
        "direct_hit=%d fetched=%d fallback=%d superseded_dropped=%d",
        len(per_quarter), len(all_events),
        total_direct_hit, total_fetched, total_fallback, n_superseded_dropped,
    )
    return all_events, meta
