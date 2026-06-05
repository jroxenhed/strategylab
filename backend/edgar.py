"""SEC EDGAR HTTP client with on-disk JSON cache.

All I/O is synchronous (called from thread pool). No FastAPI imports.
Rate limit: ≤10 req/s per EDGAR policy, enforced via token-bucket sleep.
User-Agent: required per EDGAR policy.

D5: All EDGAR HTTP flows through _get(). Tests monkeypatch edgar._get.
D4: Form 4 buy/sell requires fetching the Form 4 XML documents.
D9: CIK zero-padding to 10 digits at this boundary.
"""

import json
import logging
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from fileutil import atomic_write_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------

CACHE_DIR = Path(__file__).resolve().parent / "data" / "turnaround" / "edgar_cache"

_UNIVERSE_TTL_DAYS = 7
_FACTS_TTL_DAYS = 7
_SUBMISSIONS_TTL_DAYS = 1
_EFTS_TTL_DAYS = 1
_FORM4_TTL_DAYS = 7  # Form 4 XML documents rarely change after filing

# ---------------------------------------------------------------------------
# Rate limiter (≤10 req/s)
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_last_req_time: float = 0.0

_USER_AGENT = "StrategyLab/1.0 (contact: john@milford.se)"

# ---------------------------------------------------------------------------
# Module-level HTTP client (REL-10: connection reuse)
# ---------------------------------------------------------------------------

_http_client = httpx.Client(
    timeout=30.0,
    follow_redirects=True,
    headers={"User-Agent": _USER_AGENT},
)

# ---------------------------------------------------------------------------
# Core HTTP chokepoint (D5)
# ---------------------------------------------------------------------------


def _get(url: str, params: dict | None = None) -> httpx.Response:
    """Single rate-limited, User-Agent-tagged GET chokepoint.

    All EDGAR HTTP flows through here. Tests monkeypatch edgar._get.
    Rate limit: ≤10 req/s (token-bucket: compute wait + update under lock,
    sleep OUTSIDE the lock — REL-04).
    Retries on 429 + 5xx + httpx.TransportError with backoff 1s/4s (REL-03).
    Raises httpx.HTTPStatusError on non-2xx after retries exhausted.
    """
    global _last_req_time

    # Compute wait and claim the next slot atomically, then sleep outside lock.
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_req_time
        wait = max(0.0, 0.1 - elapsed)
        _last_req_time = time.monotonic() + wait  # reserve the slot

    if wait > 0:
        time.sleep(wait)

    _retry_delays = (1, 4)
    last_exc: Exception | None = None
    for attempt in range(len(_retry_delays) + 1):
        try:
            response = _http_client.get(url, params=params)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < len(_retry_delays):
                delay = _retry_delays[attempt]
                logger.warning(
                    "edgar: HTTP %s for %s — retry %d in %ds",
                    response.status_code, url, attempt + 1, delay,
                )
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < len(_retry_delays):
                delay = _retry_delays[attempt]
                logger.warning(
                    "edgar: TransportError for %s — retry %d in %ds: %s",
                    url, attempt + 1, delay, exc,
                )
                time.sleep(delay)
                continue
            raise

    # Should not reach here, but satisfy type checker
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("_get: unexpected exit from retry loop")


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_valid(cache_path: Path, ttl_days: int) -> bool:
    """Return True if cache file exists and is younger than ttl_days."""
    if not cache_path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
    return age < timedelta(days=ttl_days)


def _read_cache(cache_path: Path) -> dict | list:
    """Read and parse a JSON cache file.

    On JSONDecodeError or OSError, deletes the corrupt file and raises so
    the caller falls through to a fresh HTTP fetch (DI-04/REL-08).
    """
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("edgar: corrupt cache %s — deleting: %s", cache_path, exc)
        cache_path.unlink(missing_ok=True)
        raise


def _write_cache(cache_path: Path, data: dict | list) -> None:
    """Write JSON to cache atomically (DI-03: no non-atomic fallback)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, separators=(",", ":"))
    atomic_write_text(cache_path, content, backup_depth=0)


def _write_cache_text(cache_path: Path, content: str) -> None:
    """Write text (e.g. XML) to cache atomically (DI-02/REL-05)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(cache_path, content, backup_depth=0)


def _edgar_get(url: str, cache_path: Path, ttl_days: int) -> dict:
    """Fetch URL with on-disk JSON cache. Returns parsed dict.

    Checks cache first; on miss (or corrupt file) calls _get(), caches result.
    Raises httpx.HTTPStatusError on non-2xx (from _get).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _cache_valid(cache_path, ttl_days):
        logger.debug("edgar cache hit: %s", cache_path)
        try:
            return _read_cache(cache_path)
        except (json.JSONDecodeError, OSError):
            # Corrupt cache deleted by _read_cache; fall through to re-fetch.
            pass

    logger.debug("edgar cache miss: %s — fetching %s", cache_path, url)
    resp = _get(url)
    data = resp.json()
    _write_cache(cache_path, data)
    return data


# ---------------------------------------------------------------------------
# CIK helpers
# ---------------------------------------------------------------------------


def _pad_cik(cik: str | int) -> str:
    """Zero-pad CIK to 10 digits (D9: done at the edgar.py boundary)."""
    return str(int(cik)).zfill(10)


def _cik_int(cik: str) -> int:
    """Strip leading zeros and return integer CIK (needed for archive URLs)."""
    return int(cik)


# ---------------------------------------------------------------------------
# Raw data fetchers
# ---------------------------------------------------------------------------


def fetch_universe() -> dict[str, dict]:
    """Return {TICKER: {cik_str, title}} for all SEC-registered companies.

    Cache: CACHE_DIR/universe.json, TTL 7 days.
    The cik_str values are zero-padded to 10 digits (D9).
    """
    cache_path = CACHE_DIR / "universe.json"
    raw = _edgar_get(
        "https://www.sec.gov/files/company_tickers.json",
        cache_path,
        _UNIVERSE_TTL_DAYS,
    )
    # Raw format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}
    # Normalize: {TICKER: {cik_str: "0000320193", title: "..."}}
    result: dict[str, dict] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        if not ticker:
            continue
        cik_raw = entry.get("cik_str", 0)
        result[ticker] = {
            "cik_str": _pad_cik(cik_raw),
            "title": entry.get("title", ""),
        }
    return result


def fetch_companyfacts(cik: str) -> dict:
    """Return raw XBRL companyfacts JSON for 10-digit zero-padded CIK.

    Cache: CACHE_DIR/facts/{cik}.json, TTL 7 days.
    """
    padded = _pad_cik(cik)
    cache_path = CACHE_DIR / "facts" / f"{padded}.json"
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json"
    return _edgar_get(url, cache_path, _FACTS_TTL_DAYS)


def fetch_submissions(cik: str) -> dict:
    """Return submissions JSON (filing list) for CIK.

    Cache: CACHE_DIR/submissions/{cik}.json, TTL 1 day.
    """
    padded = _pad_cik(cik)
    cache_path = CACHE_DIR / "submissions" / f"{padded}.json"
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    return _edgar_get(url, cache_path, _SUBMISSIONS_TTL_DAYS)


def search_buyback_8k(cik: str, months_back: int = 12, as_of: date | None = None) -> list[dict]:
    """Query EFTS for 8-K filings mentioning 'repurchase' or 'buyback' for this CIK.

    Returns list of {accessionNo, filedAt, formType} dicts.
    Cache: CACHE_DIR/efts/{cik}_{months_back}_{as_of}.json, TTL 1 day.
    Window: [as_of - months_back×30.44d, as_of]. as_of=None → today.

    EFTS query params verified via live curl during implementation:
    q, forms, ciks, startdt, enddt.
    """
    resolved_as_of = as_of if as_of is not None else date.today()
    padded = _pad_cik(cik)
    # Cache key includes as_of so historical and live entries don't collide.
    cache_key = f"{padded}_{months_back}_{resolved_as_of.isoformat()}"
    cache_path = CACHE_DIR / "efts" / f"{cache_key}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "efts").mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_path, _EFTS_TTL_DAYS):
        try:
            return _read_cache(cache_path)
        except (json.JSONDecodeError, OSError):
            pass  # corrupt cache deleted; fall through to re-fetch

    end_dt = resolved_as_of
    start_dt = end_dt - timedelta(days=int(months_back * 30.44))
    params = {
        "q": '"repurchase" OR "buyback"',
        "forms": "8-K",
        "dateRange": "custom",           # Fix 2: required alongside startdt/enddt+forms (else HTTP 500)
        "ciks": padded,                  # Fix 1: must be zero-padded 10-digit string (e.g. 0000320193)
        "startdt": start_dt.isoformat(),
        "enddt": end_dt.isoformat(),
    }
    url = "https://efts.sec.gov/LATEST/search-index"
    try:
        resp = _get(url, params=params)
        raw = resp.json()
    except Exception as exc:
        logger.warning("edgar: EFTS query failed for CIK %s: %s", padded, exc)
        return []

    hits = raw.get("hits", {}).get("hits", [])
    results = []
    for hit in hits:
        src = hit.get("_source", {})
        # Fix 3: real EFTS fields are adsh/root_forms/file_date (not accession_no/form_type)
        root_forms = src.get("root_forms", [])
        results.append({
            "accessionNo": src.get("adsh", ""),
            "filedAt": src.get("file_date", ""),
            "formType": root_forms[0] if root_forms else "",
        })

    _write_cache(cache_path, results)
    return results


# ---------------------------------------------------------------------------
# Form 4 XML fetching and parsing (D4)
# ---------------------------------------------------------------------------


def fetch_form4_xml(cik: str, accession: str) -> str:
    """Fetch Form 4 XML document for a given CIK and accession number.

    Locates the .xml doc via the filing index JSON, then fetches its content.
    Cache: CACHE_DIR/form4/{cik}_{accession_nodash}.xml, TTL 7 days (atomic write).
    Returns empty string on failure.
    """
    padded = _pad_cik(cik)
    accession_nodash = accession.replace("-", "")
    cache_path = CACHE_DIR / "form4" / f"{padded}_{accession_nodash}.xml"

    # TTL-aware cache check (DI-02/REL-05: atomic write + TTL).
    if _cache_valid(cache_path, _FORM4_TTL_DAYS):
        return cache_path.read_text(encoding="utf-8")

    (CACHE_DIR / "form4").mkdir(parents=True, exist_ok=True)

    # Locate the XML document via the filing index.
    # Fix 4: real listing URL is index.json (not {accession}-index.json which 404s);
    # response has directory.item[] with 'name' fields (not documents[].document).
    cik_int = _cik_int(padded)
    idx_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}"
        f"/{accession_nodash}/index.json"
    )
    xml_filename = None
    try:
        resp = _get(idx_url)
        idx_data = resp.json()
        # Two-pass: prefer form4.xml directly; fall back to any .xml in directory.item[].
        items = idx_data.get("directory", {}).get("item", [])
        typed_match: str | None = None
        fallback_match: str | None = None
        for item in items:
            fname = item.get("name", "")
            if fname == "form4.xml" and typed_match is None:
                typed_match = fname
            if fname.endswith(".xml") and fallback_match is None:
                fallback_match = fname
        xml_filename = typed_match or fallback_match
    except Exception as exc:
        logger.debug("edgar: form4 index fetch failed for %s/%s: %s", padded, accession, exc)
        return ""

    if not xml_filename:
        logger.debug("edgar: no XML doc found in form4 index for %s/%s", padded, accession)
        return ""

    xml_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}"
        f"/{accession_nodash}/{xml_filename}"
    )
    try:
        resp = _get(xml_url)
        xml_content = resp.text
    except Exception as exc:
        logger.debug("edgar: form4 XML fetch failed %s: %s", xml_url, exc)
        return ""

    # Validate XML before caching to avoid storing garbage.
    try:
        ET.fromstring(xml_content)
    except ET.ParseError as exc:
        logger.debug("edgar: form4 XML invalid, not caching %s: %s", xml_url, exc)
        return xml_content  # return but don't cache invalid XML

    # Atomic cache write (DI-02/REL-05).
    _write_cache_text(cache_path, xml_content)
    return xml_content


def _parse_form4_transactions(xml_content: str) -> list[dict]:
    """Parse Form 4 XML and return list of {transactionCode, shares, price} dicts.

    transactionCode: 'P' = purchase (buy), 'S' = sale (sell).
    Returns [] on parse failure.
    """
    if not xml_content:
        return []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        logger.debug("edgar: form4 XML parse error: %s", exc)
        return []

    transactions = []
    # nonDerivativeTransaction elements
    for txn in root.iter("nonDerivativeTransaction"):
        code_el = txn.find(".//transactionCode")
        shares_el = txn.find(".//transactionShares/value")
        price_el = txn.find(".//transactionPricePerShare/value")
        if code_el is None:
            continue
        code = (code_el.text or "").strip()
        try:
            shares = float((shares_el.text or "0").strip()) if shares_el is not None else 0.0
        except ValueError:
            shares = 0.0
        try:
            price = float((price_el.text or "0").strip()) if price_el is not None else 0.0
        except ValueError:
            price = 0.0
        transactions.append({"transactionCode": code, "shares": shares, "price": price})

    # derivativeTransaction elements (options/warrants) — skip for net buy/sell count
    return transactions


# ---------------------------------------------------------------------------
# Quarterly series helpers
# ---------------------------------------------------------------------------

_REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]


def _extract_quarterly_series(facts: dict, tag: str) -> list[dict]:
    """Extract quarterly XBRL series for a us-gaap tag from companyfacts.

    Filters to form IN (10-Q, 10-K) and fp != 'FY' (quarterly, not annual).
    Deduplicates by (end, filed) — multiple amendments re-file the same period.
    Returns list of {end, val, filed} dicts, oldest-first.
    Returns [] if tag not found or empty.
    """
    try:
        entries = (
            facts.get("facts", {})
            .get("us-gaap", {})
            .get(tag, {})
            .get("units", {})
            .get("USD", [])
        )
    except (AttributeError, KeyError):
        return []

    if not entries:
        return []

    seen: set[tuple[str, str]] = set()
    result = []
    for e in entries:
        form = e.get("form", "")
        fp = e.get("fp", "")
        end = e.get("end", "")
        filed = e.get("filed", "")
        val = e.get("val", None)

        if form not in ("10-Q", "10-K"):
            continue
        if fp == "FY":
            continue
        if val is None:
            continue

        key = (end, filed)
        if key in seen:
            continue
        seen.add(key)

        result.append({"end": end, "val": float(val), "filed": filed})

    result.sort(key=lambda x: x["end"])
    return result


def _derive_q4_from_annual(facts: dict, tag: str) -> list[dict]:
    """Derive Q4 figures from FY annual - (Q1+Q2+Q3) where quarterly Q4 is missing.

    Returns list of {end, val, filed} dicts for derived Q4 entries only.
    Q4 end date is approximated as the FY end date.
    filed is set to max(FY filed, latest Q component filed) so downstream
    point-in-time guards (filed <= as_of) work correctly.

    COR-01: Q1-Q3 entries are deduplicated by (fy, fp), keeping the latest-filed
    entry per fp before summing.
    """
    # Collect annual FY entries
    try:
        entries = (
            facts.get("facts", {})
            .get("us-gaap", {})
            .get(tag, {})
            .get("units", {})
            .get("USD", [])
        )
    except (AttributeError, KeyError):
        return []

    # FY entries keyed by fy — keep latest filed per fy
    annual: dict[str, dict] = {}
    for e in entries:
        if e.get("form") not in ("10-K", "10-Q"):
            continue
        if e.get("fp") == "FY":
            fy = str(e.get("fy", ""))
            end = e.get("end", "")
            filed = e.get("filed", "")
            val = e.get("val", None)
            if fy and end and val is not None:
                # Keep the entry with the latest filed date
                if fy not in annual or filed > annual[fy]["filed"]:
                    annual[fy] = {"end": end, "filed": filed, "val": float(val)}

    # Quarterly entries grouped by fy — dedupe by (fy, fp), keep latest filed (COR-01)
    by_fy: dict[str, dict[str, dict]] = {}  # fy -> fp -> best_entry
    for e in entries:
        fp = e.get("fp", "")
        if fp in ("Q1", "Q2", "Q3") and e.get("form") in ("10-Q", "10-K"):
            fy = str(e.get("fy", ""))
            filed = e.get("filed", "")
            if fy:
                if fy not in by_fy:
                    by_fy[fy] = {}
                # Keep only the latest-filed entry per (fy, fp)
                if fp not in by_fy[fy] or filed > by_fy[fy][fp].get("filed", ""):
                    by_fy[fy][fp] = e

    derived = []
    for fy, fy_entry in annual.items():
        fp_map = by_fy.get(fy, {})
        q_entries = list(fp_map.values())
        if len(q_entries) < 3:
            continue
        q_sum = sum(float(e.get("val", 0)) for e in q_entries)
        q4_val = fy_entry["val"] - q_sum
        # filed = max of FY filed and latest Q component filed
        max_q_filed = max(e.get("filed", "") for e in q_entries)
        derived_filed = max(fy_entry["filed"], max_q_filed)
        derived.append({
            "end": fy_entry["end"],
            "val": q4_val,
            "filed": derived_filed,
        })

    return derived


def _get_quarterly_series_for_tag_list(cik: str, tags: list[str]) -> list[dict]:
    """Try tags in order; return first non-empty quarterly series.

    Merges direct quarterly entries with Q4-derived entries.
    """
    facts = fetch_companyfacts(cik)

    for tag in tags:
        series = _extract_quarterly_series(facts, tag)
        if series:
            # Attempt to fill Q4 gaps via annual derivation
            q4_derived = _derive_q4_from_annual(facts, tag)
            if q4_derived:
                # Merge, dedup by end date (prefer direct filings over derived)
                existing_ends = {e["end"] for e in series}
                for d in q4_derived:
                    if d["end"] not in existing_ends:
                        series.append(d)
                series.sort(key=lambda x: x["end"])
            return series

    return []


# ---------------------------------------------------------------------------
# Public parsed accessors
# ---------------------------------------------------------------------------


def get_quarterly_revenue(cik: str) -> list[dict]:
    """Return list of {end, val, filed} dicts, oldest-first.

    Tries XBRL tags in priority order: Revenues, RevenueFromContractWith..., SalesRevenueNet.
    Returns [] on missing data.
    """
    return _get_quarterly_series_for_tag_list(cik, _REVENUE_TAGS)


def get_quarterly_net_income(cik: str) -> list[dict]:
    """Same shape as get_quarterly_revenue(). Tag: NetIncomeLoss."""
    return _get_quarterly_series_for_tag_list(cik, ["NetIncomeLoss"])


def get_quarterly_gross_profit(cik: str) -> list[dict]:
    """Tag: GrossProfit."""
    return _get_quarterly_series_for_tag_list(cik, ["GrossProfit"])


def get_quarterly_ocf(cik: str) -> list[dict]:
    """Tag: NetCashProvidedByUsedInOperatingActivities."""
    return _get_quarterly_series_for_tag_list(
        cik, ["NetCashProvidedByUsedInOperatingActivities"]
    )


def get_shares_outstanding(cik: str, as_of: date) -> float | None:
    """Most-recent CommonStockSharesOutstanding filed on or before as_of.

    Returns None if unavailable.
    Point-in-time: only considers entries with filed <= as_of.
    """
    facts = fetch_companyfacts(cik)
    try:
        entries = (
            facts.get("facts", {})
            .get("us-gaap", {})
            .get("CommonStockSharesOutstanding", {})
            .get("units", {})
            .get("shares", [])
        )
    except (AttributeError, KeyError):
        return None

    if not entries:
        return None

    as_of_str = as_of.isoformat()
    # Filter to filed <= as_of
    eligible = [
        e for e in entries
        if e.get("filed", "") <= as_of_str and e.get("val") is not None
    ]
    if not eligible:
        return None

    # Most recent by filed date
    best = max(eligible, key=lambda e: e["filed"])
    return float(best["val"])


def get_form4_net_buys(cik: str, months_back: int = 6, as_of: date | None = None) -> float:
    """Count net insider buy transactions (Form 4) in a point-in-time window.

    Window: [as_of - months_back×30.44d, as_of]. as_of=None → today (live scan).
    Returns signed net dollar value (float) of P - S transactions.
    Returns 0 on missing data.

    Process (D4):
    1. List Form 4 accessions in window from submissions (cap: 20 most recent).
    2. Fetch each filing's XML via fetch_form4_xml().
    3. Parse transactionCode P (buy) vs S (sell).
    4. Return net signed dollar value (shares × price summed).
    """
    resolved_as_of = as_of if as_of is not None else date.today()

    try:
        subs = fetch_submissions(cik)
    except Exception as exc:
        logger.warning("edgar: get_form4_net_buys fetch_submissions failed for %s: %s", cik, exc)
        return 0

    # Extract Form 4 filings from submissions
    filings = subs.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    filed_dates = filings.get("filingDate", [])

    cutoff = resolved_as_of - timedelta(days=int(months_back * 30.44))
    cutoff_str = cutoff.isoformat()
    as_of_str = resolved_as_of.isoformat()

    form4_accessions = []
    for form, accession, filed in zip(forms, accessions, filed_dates):
        if form not in ("4", "4/A"):
            continue
        if filed < cutoff_str:
            continue
        if filed > as_of_str:
            continue  # point-in-time: exclude filings after as_of
        form4_accessions.append(accession)
        if len(form4_accessions) >= 20:
            break

    if not form4_accessions:
        return 0

    net_dollars = 0.0
    for accession in form4_accessions:
        try:
            xml_content = fetch_form4_xml(cik, accession)
        except Exception as exc:
            logger.debug("edgar: form4 XML fetch failed for %s/%s: %s", cik, accession, exc)
            continue
        transactions = _parse_form4_transactions(xml_content)
        for txn in transactions:
            code = txn.get("transactionCode", "")
            dollar_value = txn.get("shares", 0.0) * txn.get("price", 0.0)
            if code == "P":
                net_dollars += dollar_value
            elif code == "S":
                net_dollars -= dollar_value

    return net_dollars


def has_buyback_authorization(cik: str, months_back: int = 12, as_of: date | None = None) -> bool:
    """True if a buyback/NCIB 8-K exists in the point-in-time window.

    as_of=None → today (live scan unchanged).
    """
    try:
        filings = search_buyback_8k(cik, months_back, as_of=as_of)
        return len(filings) > 0
    except Exception as exc:
        logger.warning("edgar: has_buyback_authorization failed for %s: %s", cik, exc)
        return False
