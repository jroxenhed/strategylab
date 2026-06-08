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

# Derived compact fundamentals cache (F320).
# Versioned path so a schema change just bumps v2 — old derived files are ignored.
DERIVED_CACHE_DIR = CACHE_DIR / "derived" / "v1"

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
# Derived compact fundamentals cache (F320)
# ---------------------------------------------------------------------------
# parse_companyfacts_to_derived() parses the ~1.8–5 MB raw companyfacts JSON
# ONCE and emits a compact per-CIK derived JSON (~KB) containing the five
# quarterly series + shares.  Accessors read from derived; derived is rebuilt
# on miss or when older than the raw facts file (staleness = new facts TTL
# expiry triggered a fresh HTTP fetch).
#
# _load_derived() uses a mtime-keyed in-process dict cache (_derived_cache).
# Two os.stat calls per invocation (~µs) key on (cik, raw_mtime, derived_mtime)
# so a changed raw mtime naturally misses the cache and triggers a rebuild;
# the freshness decision is never cached (COR-01/COR-02).
#
# Point-in-time semantics are UNCHANGED — each series entry carries its
# original `filed` date; `as_of` filtering happens in the callers exactly
# as before.
# ---------------------------------------------------------------------------

_REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]

# All XBRL tags we need to build the derived cache.
_NET_INCOME_TAGS = ["NetIncomeLoss"]
_GROSS_PROFIT_TAGS = ["GrossProfit"]
_OCF_TAGS = ["NetCashProvidedByUsedInOperatingActivities"]
_SHARES_TAG = "CommonStockSharesOutstanding"
# F322: fallbacks for dual-class filers that omit CommonStockSharesOutstanding
_DEI_SHARES_TAG = "EntityCommonStockSharesOutstanding"   # dei namespace; aggregate total
_SHARES_WA_TAG = "WeightedAverageNumberOfSharesOutstandingBasic"  # period-based; last resort

_DERIVED_SCHEMA_VERSION = 1


def parse_companyfacts_to_derived(cik: str) -> dict:
    """Parse raw companyfacts once and return compact derived dict.

    Schema (v1):
    {
      "cik": "0000320193",
      "schema_version": 1,
      "revenue":     [{end, val, filed}, ...],   # oldest-first
      "net_income":  [{end, val, filed}, ...],
      "gross_profit":[{end, val, filed}, ...],
      "ocf":         [{end, val, filed}, ...],
      "shares":      [{end, val, filed, form, src, start, fp}, ...],  # deduped, all levels
    }

    All five series preserve the point-in-time `filed` field so callers can
    apply `filed <= as_of` guards without loss of fidelity.
    Revenue uses the same tag-fallback chain as the old path.
    Each quarterly series merges direct quarterly entries with Q4-derived entries
    (identical logic to the old _get_quarterly_series_for_tag_list path).

    Shares fallback chain (F322):
      1. us-gaap:CommonStockSharesOutstanding  (instantaneous; src='primary')
      2. dei:EntityCommonStockSharesOutstanding (instantaneous aggregate; src='dei';
         values summed across share-class contexts in the same filing — COR-01)
      3. us-gaap:WeightedAverageNumberOfSharesOutstandingBasic (period-based last resort;
         src='wa' for quarterly entries, src='wa_fy' for annual-only — DI-07)
    Each fallback only activates when the previous level returns no entries.
    DI-01: 'wa' and 'wa_fy' are period averages, not point-in-time counts.
    Use get_shares_outstanding_detail() to retrieve (val, src) for the chosen entry.
    """
    padded = _pad_cik(cik)
    facts = fetch_companyfacts(padded)

    def _series_for_tag_list(tags: list[str]) -> list[dict]:
        for tag in tags:
            series = _extract_quarterly_series(facts, tag)
            if series:
                q4_derived = _derive_q4_from_annual(facts, tag)
                if q4_derived:
                    existing_ends = {e["end"] for e in series}
                    for d in q4_derived:
                        if d["end"] not in existing_ends:
                            series.append(d)
                    series.sort(key=lambda x: x["end"])
                return series
        return []

    def _raw_shares_for(namespace: str, tag: str) -> list:
        try:
            return (
                facts.get("facts", {})
                .get(namespace, {})
                .get(tag, {})
                .get("units", {})
                .get("shares", [])
            )
        except (AttributeError, KeyError):
            return []

    # Shares: all entries (not just quarterly) — keep same fields as raw path
    # Fallback chain: primary → dei aggregate → weighted-average basic (F322)
    shares_entries: list[dict] = []

    primary = _raw_shares_for("us-gaap", _SHARES_TAG)
    dei_agg = _raw_shares_for("dei", _DEI_SHARES_TAG)
    wa_basic = _raw_shares_for("us-gaap", _SHARES_WA_TAG)

    def _ingest_shares(raw: list, src: str) -> list[dict]:
        out = []
        for e in raw:
            filed = e.get("filed", "")
            val = e.get("val", None)
            end = e.get("end", "")
            form = e.get("form", "")
            if filed and val is not None:
                out.append({"end": end, "val": float(val), "filed": filed, "form": form, "src": src,
                            "start": e.get("start", ""), "fp": e.get("fp", "")})
        return out

    def _span_days(e: dict) -> int:
        """Return the period length in days for a share entry; large sentinel if no start."""
        start = e.get("start", "")
        end = e.get("end", "")
        if not start or not end:
            return 9999
        try:
            from datetime import datetime as _dt
            return (_dt.strptime(end, "%Y-%m-%d") - _dt.strptime(start, "%Y-%m-%d")).days
        except ValueError:
            return 9999

    def _dedup_wa_entries(entries: list[dict]) -> list[dict]:
        """Deduplicate WA entries: for each end date pick the best single entry.

        Selection criterion (COR-02): prefer shortest span (quarterly over YTD/annual),
        tiebreak by latest filed date, then latest end date.  This is deterministic
        w.r.t. EDGAR list ordering.

        DI-07: when quarterly (fp != 'FY') entries exist for an end date, exclude FY
        entries at that end date (12-month average is a worse proxy than 3-month).
        If only FY entries exist for a given end date, keep the best one tagged 'wa_fy'
        so downstream callers know it is an annual average.
        """
        from collections import defaultdict as _dd
        by_end: dict[str, list[dict]] = _dd(list)
        for e in entries:
            by_end[e["end"]].append(e)

        result = []
        for end, group in by_end.items():
            non_fy = [e for e in group if e.get("fp", "") != "FY"]
            fy_only = not non_fy
            candidates = group if fy_only else non_fy
            # Sort: shortest span first, then latest filed, then latest end
            best = min(
                candidates,
                key=lambda e: (_span_days(e), e.get("filed", "2000-01-01"), e.get("end", "")),
            )
            # Deterministic tiebreak: if multiple share shortest span AND same filed, pick max end
            shortest_span = _span_days(best)
            tied = [e for e in candidates if _span_days(e) == shortest_span and e.get("filed", "") == best.get("filed", "")]
            if len(tied) > 1:
                best = max(tied, key=lambda e: e.get("end", ""))
            entry = dict(best)
            if fy_only:
                entry["src"] = "wa_fy"  # DI-07: tag annual-average fallback
            else:
                entry["src"] = "wa"
            result.append(entry)

        return sorted(result, key=lambda x: x["end"])

    primary_entries = _ingest_shares(primary, "primary")
    if primary_entries:
        shares_entries = primary_entries
    else:
        dei_raw = _ingest_shares(dei_agg, "dei")
        wa_raw = _ingest_shares(wa_basic, "wa")

        # COR-01: DEI — sum across distinct share-class contexts that share the same (end, filed)
        # date.  Multiple entries with identical end+filed come from the same filing and represent
        # separate share classes (e.g. Class A + Class B filed together); sum their values.
        # Entries with the same end but different filed dates are amendments — keep latest-filed only.
        if dei_raw:
            from collections import defaultdict as _dd2
            # Step 1: within each (end, filed) pair, sum vals (handles per-class DEI entries)
            per_ef: dict[tuple, dict] = {}
            for e in dei_raw:
                key = (e["end"], e["filed"])
                if key not in per_ef:
                    per_ef[key] = dict(e)
                else:
                    per_ef[key]["val"] += e["val"]  # sum share classes
            # Step 2: for each end date, keep the latest-filed entry (amendment preference)
            by_end_dei: dict[str, dict] = {}
            for (end, filed), e in per_ef.items():
                if end not in by_end_dei or filed > by_end_dei[end]["filed"]:
                    by_end_dei[end] = e
            dei_entries = sorted(by_end_dei.values(), key=lambda x: x["end"])
        else:
            dei_entries = []

        # COR-02 / DI-07: WA — deterministic dedup preferring shortest span, excluding FY
        # when quarterly entries exist for the same end date.
        wa_entries = _dedup_wa_entries(wa_raw) if wa_raw else []

        if dei_entries or wa_entries:
            # Merge: dei first (instantaneous), then wa/wa_fy to fill gaps; deduplicate by end.
            # Prefer dei (instantaneous) over wa (period-based) when both cover the same end date.
            merged: dict[str, dict] = {}
            for e in wa_entries:
                merged[e["end"]] = e
            for e in dei_entries:
                merged[e["end"]] = e  # dei overwrites wa for same end
            shares_entries = sorted(merged.values(), key=lambda x: x["end"])

    return {
        "cik": padded,
        "schema_version": _DERIVED_SCHEMA_VERSION,
        "revenue": _series_for_tag_list(_REVENUE_TAGS),
        "net_income": _series_for_tag_list(_NET_INCOME_TAGS),
        "gross_profit": _series_for_tag_list(_GROSS_PROFIT_TAGS),
        "ocf": _series_for_tag_list(_OCF_TAGS),
        "shares": shares_entries,
    }


def _derived_path(cik: str) -> Path:
    """Return the derived cache path for a CIK.

    Uses CACHE_DIR at call time so monkeypatch.setattr(edgar, 'CACHE_DIR', tmp_path)
    also redirects the derived cache to tmp_path/derived/v1/ in tests.
    """
    padded = _pad_cik(cik)
    return CACHE_DIR / "derived" / "v1" / f"{padded}.json"


def _raw_facts_path(cik: str) -> Path:
    """Return the raw facts cache path for a CIK (used for staleness check)."""
    padded = _pad_cik(cik)
    return CACHE_DIR / "facts" / f"{padded}.json"


def _derived_is_fresh(cik: str) -> bool:
    """True if derived cache exists and is at least as new as the raw facts file."""
    derived = _derived_path(cik)
    raw = _raw_facts_path(cik)
    if not derived.exists():
        return False
    if not raw.exists():
        # No raw file yet (perhaps CIK was just deleted); treat derived as stale.
        return False
    return derived.stat().st_mtime >= raw.stat().st_mtime


# Mtime-keyed in-process derived cache.
# Key: (cik_padded, raw_mtime_float_or_None, derived_mtime_float_or_None)
# Two os.stat calls per accessor invocation (~µs); a changed mtime naturally
# misses the cache so the freshness decision is never stale (COR-01 / COR-02).
_derived_cache: dict[tuple, dict] = {}
_DERIVED_CACHE_MAX = 1024  # FIFO-bounded: full-universe runs would otherwise hold every CIK + stale-mtime leftovers (~50KB each) for the process lifetime


def _derived_cache_put(key: tuple, data: dict) -> None:
    """Insert into the in-process derived cache, FIFO-evicting past the size cap."""
    while len(_derived_cache) >= _DERIVED_CACHE_MAX:
        _derived_cache.pop(next(iter(_derived_cache)))
    _derived_cache[key] = data


def _derived_mtimes(cik: str) -> tuple[float | None, float | None]:
    """Return (raw_mtime, derived_mtime) for CIK; None when file absent."""
    raw = _raw_facts_path(cik)
    derived = _derived_path(cik)
    raw_mtime: float | None = raw.stat().st_mtime if raw.exists() else None
    derived_mtime: float | None = derived.stat().st_mtime if derived.exists() else None
    return raw_mtime, derived_mtime


def _load_derived(cik: str) -> dict:
    """Load derived compact fundamentals for CIK, building from raw on miss/stale.

    Uses a mtime-keyed in-process cache (not lru_cache) so the freshness
    decision is re-evaluated on every call via two cheap os.stat calls.
    A changed raw or derived mtime naturally misses the cache, preventing
    stale data from being served after a TTL-driven raw refresh (COR-01)
    or after raw file deletion (COR-02).

    Cache is per-process; server restart clears it.
    """
    padded = _pad_cik(cik)
    raw_mtime, derived_mtime = _derived_mtimes(padded)

    # COR-02: raw file absent — treat as orphan derived; re-fetch from network.
    # This replicates the first-load orphan policy from _derived_is_fresh.
    if raw_mtime is None:
        logger.debug("edgar derived: raw absent for %s — fetching from network", padded)
        derived_path = _derived_path(padded)
        data = parse_companyfacts_to_derived(padded)
        derived_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(derived_path, json.dumps(data, separators=(",", ":")), backup_depth=0)
        # Re-read mtimes after write so the new entry is cached under fresh key.
        raw_mtime2, derived_mtime2 = _derived_mtimes(padded)
        key2 = (padded, raw_mtime2, derived_mtime2)
        _derived_cache_put(key2, data)
        return data

    # Freshness: derived must exist and be at least as new as raw.
    is_fresh = derived_mtime is not None and derived_mtime >= raw_mtime

    cache_key = (padded, raw_mtime, derived_mtime)
    if is_fresh and cache_key in _derived_cache:
        logger.debug("edgar derived in-process cache hit: %s", padded)
        return _derived_cache[cache_key]

    derived_path = _derived_path(padded)

    if is_fresh:
        # Derived file is fresh — load from disk (first call for this mtime pair).
        logger.debug("edgar derived cache hit (disk): %s", derived_path)
        try:
            data = json.loads(derived_path.read_text(encoding="utf-8"))
            _derived_cache_put(cache_key, data)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("edgar: corrupt derived cache %s — rebuilding: %s", derived_path, exc)
            derived_path.unlink(missing_ok=True)
            # Fall through to rebuild below.

    # Miss or stale or corrupt — rebuild from raw.
    logger.debug("edgar derived cache miss/stale for %s — building from raw", padded)
    data = parse_companyfacts_to_derived(padded)
    derived_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(derived_path, json.dumps(data, separators=(",", ":")), backup_depth=0)
    # Cache under the post-write mtimes so the next call is a hit.
    raw_mtime2, derived_mtime2 = _derived_mtimes(padded)
    new_key = (padded, raw_mtime2, derived_mtime2)
    _derived_cache_put(new_key, data)
    return data


def _load_derived_cache_clear() -> None:
    """Clear the in-process derived cache (test helper, mirrors lru_cache.cache_clear)."""
    _derived_cache.clear()


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
    """Try tags in order; return first non-empty quarterly series (raw-path fallback).

    This function is kept for internal use by parse_companyfacts_to_derived()
    and for test monkeypatching compatibility.  Production callers (the five
    public accessors) now route through _load_derived() instead.
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
# Public parsed accessors  (F320: route through derived cache)
# ---------------------------------------------------------------------------


def get_quarterly_revenue(cik: str) -> list[dict]:
    """Return list of {end, val, filed} dicts, oldest-first.

    Tries XBRL tags in priority order: Revenues, RevenueFromContractWith..., SalesRevenueNet.
    Returns [] on missing data.
    Reads from derived compact cache (F320); rebuilds from raw on miss/stale.
    """
    return _load_derived(cik).get("revenue", [])


def get_quarterly_net_income(cik: str) -> list[dict]:
    """Same shape as get_quarterly_revenue(). Tag: NetIncomeLoss."""
    return _load_derived(cik).get("net_income", [])


def get_quarterly_gross_profit(cik: str) -> list[dict]:
    """Tag: GrossProfit."""
    return _load_derived(cik).get("gross_profit", [])


def get_quarterly_ocf(cik: str) -> list[dict]:
    """Tag: NetCashProvidedByUsedInOperatingActivities."""
    return _load_derived(cik).get("ocf", [])


def get_shares_outstanding(cik: str, as_of: date) -> float | None:
    """Most-recent shares outstanding filed on or before as_of.

    Returns None if unavailable.
    Point-in-time: only considers entries with filed <= as_of.
    For weighted-average or annual-WA (period-based) fallback entries, also requires
    end <= as_of to avoid using a future-period average as a proxy for a past date.
    Reads from derived compact cache (F320); rebuilds from raw on miss/stale.

    Fallback chain (F322): primary (CommonStockSharesOutstanding) →
    dei:EntityCommonStockSharesOutstanding (instantaneous; summed across share classes
    in the same filing — COR-01) → WeightedAverageNumberOfSharesOutstandingBasic
    (quarterly preferred over annual; last resort — DI-01/DI-07).

    DI-01 / COR-03: when the selected entry has src='wa' or src='wa_fy', the returned
    value is a period-weighted average (not a point-in-time instantaneous count).
    Callers computing P/S or market cap should note that 'wa' may differ from the
    true instantaneous count by 2–5 % on stable firms and up to 10–30 % during rapid
    dilution periods (e.g. PTON 2019–2021).  'wa_fy' (annual average) carries a larger
    bias window (12 months) and should be treated as a rough proxy.
    To inspect the source, call get_shares_outstanding_detail() which returns
    (val, src) instead of just val.
    """
    detail = get_shares_outstanding_detail(cik, as_of)
    if detail is None:
        return None
    return detail[0]


def get_shares_outstanding_detail(cik: str, as_of: date) -> tuple[float, str] | None:
    """Like get_shares_outstanding but returns (val, shares_source) tuple.

    shares_source is one of:
      'primary'  — us-gaap:CommonStockSharesOutstanding (instantaneous, most precise)
      'dei'      — dei:EntityCommonStockSharesOutstanding (instantaneous aggregate;
                   summed across share classes in the same filing)
      'wa'       — WeightedAverageNumberOfSharesOutstandingBasic, quarterly period
                   (~3-month average; last resort; may differ 2–5 % from true PIT count)
      'wa_fy'    — WeightedAverageNumberOfSharesOutstandingBasic, annual period
                   (~12-month average; only used when no quarterly WA entry exists;
                   carries larger potential bias vs true PIT count)
    """
    entries = _load_derived(cik).get("shares", [])
    if not entries:
        return None

    as_of_str = as_of.isoformat()
    # Filter to filed <= as_of; for wa/wa_fy (period-based) also require end <= as_of
    eligible = []
    for e in entries:
        if e.get("val") is None:
            continue
        if e.get("filed", "") > as_of_str:
            continue
        src = e.get("src", "primary")
        if src in ("wa", "wa_fy") and e.get("end", "") > as_of_str:
            continue
        eligible.append(e)

    if not eligible:
        return None

    # Most recent by filed date.
    # COR-06 / PTON 2021 fix: when multiple WA/wa_fy entries share the same filed date,
    # tiebreak by latest end date so we prefer the current-period average over the
    # prior-year comparative figure reported in the same 10-Q (e.g. PTON 2020-11-06
    # filing had both end=2019-09-30 [38M pre-IPO] and end=2020-09-30 [288M], and the
    # old max-by-filed returned whichever Python encountered first).
    # For primary/dei entries the filed-only sort is preserved (instantaneous values are
    # all correct; changing the tiebreaker would silently alter already-correct results).
    wa_srcs = {"wa", "wa_fy"}
    has_wa = any(e.get("src", "primary") in wa_srcs for e in eligible)
    if has_wa:
        best = max(eligible, key=lambda e: (e.get("filed", ""), e.get("end", "")))
    else:
        best = max(eligible, key=lambda e: e.get("filed", ""))
    return float(best["val"]), best.get("src", "primary")


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


# ---------------------------------------------------------------------------
# F314 — raw-facts cache age/size pruning
# ---------------------------------------------------------------------------

# Age cap: raw companyfacts files older than this are eligible for eviction.
# 30 days is safe: fetch_companyfacts TTL is 7 days, so any file the scanner
# actually needs will have been refreshed within the last week.
_RAW_FACTS_MAX_AGE_DAYS = 30

# Soft size cap for the raw facts directory (in bytes).  When total size exceeds
# this the oldest files are evicted until size drops below the cap OR only
# _RAW_FACTS_MIN_KEEP files remain (safety floor — never delete everything).
# Derived cache is untouched.  Default: 12 GB.
_RAW_FACTS_SIZE_CAP_BYTES = 12 * 1024 ** 3
_RAW_FACTS_MIN_KEEP = 50  # never evict below this many files


def prune_edgar_raw_cache(
    max_age_days: int = _RAW_FACTS_MAX_AGE_DAYS,
    size_cap_bytes: int = _RAW_FACTS_SIZE_CAP_BYTES,
    min_keep: int = _RAW_FACTS_MIN_KEEP,
    dry_run: bool = False,
) -> dict:
    """Age-based prune + soft size cap on the raw companyfacts cache.

    NOTE (DI-04): this function should be called AFTER a scan completes, not before.
    If called before, the size-cap phase can evict files that are 8–29 days old
    (within the scan's active population); those CIKs then re-fetch from the SEC
    network mid-scan, adding latency and risk of data_gap on rate-limit.
    The age-phase only removes files ≥ max_age_days old, which are safely outside
    the 7-day refresh TTL, so pre-scan age eviction is safe.  Size-cap eviction is
    the risky phase.

    Never touches the derived cache.

    Algorithm:
      1. Collect all .json files in CACHE_DIR/facts/.
      2. Delete files with mtime older than ``max_age_days``.
      3. If total remaining size still exceeds ``size_cap_bytes``, evict oldest
         files by mtime until size drops below cap OR only ``min_keep`` files remain.

    Returns a summary dict: {deleted_age, deleted_size, bytes_freed, dry_run}.
    The derived cache is deliberately excluded from all eviction.

    Concurrent invocations are benign: double-delete OSError is caught but may
    cause ``bytes_freed`` to be slightly overstated by the losing process.
    """
    facts_dir = CACHE_DIR / "facts"
    if not facts_dir.exists():
        return {"deleted_age": 0, "deleted_size": 0, "bytes_freed": 0, "dry_run": dry_run}

    now = time.time()
    cutoff_age = now - max_age_days * 86400

    # Collect (path, mtime, size) for all raw facts files
    entries: list[tuple[Path, float, int]] = []
    for p in facts_dir.glob("*.json"):
        try:
            st = p.stat()
            entries.append((p, st.st_mtime, st.st_size))
        except OSError:
            pass

    deleted_age = 0
    bytes_freed = 0

    # Step 1: age-based eviction
    survivors: list[tuple[Path, float, int]] = []
    for path, mtime, size in entries:
        if mtime < cutoff_age:
            if not dry_run:
                try:
                    path.unlink()
                    deleted_age += 1
                    bytes_freed += size
                    logger.debug("edgar: prune age-evict %s (%.0f days old)", path.name,
                                 (now - mtime) / 86400)
                except OSError as exc:
                    logger.warning("edgar: prune age-evict failed for %s: %s", path.name, exc)
            else:
                deleted_age += 1
                bytes_freed += size
        else:
            survivors.append((path, mtime, size))

    # Step 2: size cap — evict oldest survivors until below cap
    # DI-03 / COR-04: use a `remaining` counter that tracks actual deletions so the
    # min_keep guard is never corrupted by OSError failures mid-loop.  Never evict
    # below min_keep; warn when size-cap eviction fires (DI-04 indirect re-fetch risk).
    total_size = sum(s for _, _, s in survivors)
    deleted_size = 0
    if total_size > size_cap_bytes:
        remaining = len(survivors)
        if remaining > min_keep:
            logger.warning(
                "edgar: prune size-cap eviction triggered (total=%.1fMB cap=%.1fMB "
                "survivors=%d min_keep=%d); CIKs evicted may re-fetch from SEC during scan",
                total_size / 1024 ** 2, size_cap_bytes / 1024 ** 2, remaining, min_keep,
            )
        # Sort oldest-first
        survivors.sort(key=lambda x: x[1])
        for path, mtime, size in survivors:
            if total_size <= size_cap_bytes or remaining <= min_keep:
                break
            if not dry_run:
                try:
                    path.unlink()
                    deleted_size += 1
                    bytes_freed += size
                    total_size -= size
                    remaining -= 1  # only decrement on successful deletion (COR-04)
                    logger.debug("edgar: prune size-evict %s", path.name)
                except OSError as exc:
                    logger.warning("edgar: prune size-evict failed for %s: %s", path.name, exc)
                    # Do NOT decrement remaining — file was not actually deleted
            else:
                deleted_size += 1
                bytes_freed += size
                total_size -= size
                remaining -= 1

    if deleted_age or deleted_size:
        logger.info(
            "edgar: prune_raw_cache: deleted_age=%d deleted_size=%d bytes_freed=%.1fMB dry_run=%s",
            deleted_age, deleted_size, bytes_freed / 1024 ** 2, dry_run,
        )

    return {
        "deleted_age": deleted_age,
        "deleted_size": deleted_size,
        "bytes_freed": bytes_freed,
        "dry_run": dry_run,
    }
