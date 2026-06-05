"""F334 — Stratified Form 4 refetch + pre-registered insider test.

Stdlib + urllib only; no new deps.
User-Agent mirrors edgar.py convention: StrategyLab/1.0 (contact: john@milford.se)

PHASE 1: Stratified sample (seed=42)
  ~16 events per as_of cohort, proportional is_null mix within cohort.
  ALL 12 signal events included regardless of stratum quota.

PHASE 2: Fetch EDGAR Form 4 XMLs into edgar_cache/form4_stratified/

PHASE 3: Pre-registered insider test (verbatim, no variants):
  ≥2 distinct insider open-market BUY filings (tx code P) in 90d before as_of
  vs events without, on EXPLORE slice only (as_of ≤ 2020-12-31).
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path("/Users/jroxenhed/Documents/strategylab")
EVENTS_PATH = BASE_DIR / "backend/data/turnaround/validation_result.json"
UNIVERSE_PATH = BASE_DIR / "backend/data/turnaround/edgar_cache/universe.json"
CACHE_DIR = BASE_DIR / "backend/data/turnaround/edgar_cache/form4_stratified"
REPORT_DIR = BASE_DIR / ".run/INSTR-0605"
REPORT_PATH = REPORT_DIR / "impl-f334.md"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("f334")

# ---------------------------------------------------------------------------
# EDGAR HTTP — mirror edgar.py conventions (stdlib urllib, ≤5 req/s)
# ---------------------------------------------------------------------------

_USER_AGENT = "StrategyLab/1.0 (contact: john@milford.se)"
_MIN_INTERVAL = 0.20  # 5 req/s ceiling
_last_req_time: float = 0.0
_WALL_CAP_SECS = 75 * 60  # 75 minutes hard cap for fetch phase
_fetch_start_time: float = 0.0


def _edgar_get(url: str, retries: int = 3) -> bytes:
    """Rate-limited GET with exponential backoff on 429/503."""
    global _last_req_time
    # Rate limit
    now = time.monotonic()
    elapsed = now - _last_req_time
    wait = max(0.0, _MIN_INTERVAL - elapsed)
    if wait > 0:
        time.sleep(wait)
    _last_req_time = time.monotonic()

    delays = [2, 8, 30, 60]  # PY-04: 4 entries for up to 3 retries (index 0..2 used)
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                delay = delays[attempt]
                log.warning("HTTP %s for %s — retry %d in %ds", exc.code, url, attempt + 1, delay)
                time.sleep(delay)
                continue
            raise
        except Exception:
            if attempt < retries:
                delay = delays[attempt]
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"_edgar_get exhausted retries for {url}")


def _pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


# ---------------------------------------------------------------------------
# PHASE 1: Load events + build ticker→CIK map
# ---------------------------------------------------------------------------

def load_events() -> list[dict]:
    with open(EVENTS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # DI-09: guard against pre-events artifacts (schema_version=0 → events=[]). These
    # scripts open validation_result.json directly, bypassing the route backfill shim,
    # so a stale pre-events run would silently produce all-zero statistics.
    sv = int(data.get("schema_version", 0))
    if sv == 0:
        raise SystemExit(
            "insider_stratified: pre-events artifact (schema_version=0) — re-run validation first."
        )
    return data["events"]


def build_ticker_cik_map(events: list[dict]) -> dict[str, str]:
    """Build ticker→padded_CIK from universe.json."""
    with open(UNIVERSE_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    ticker_cik: dict[str, str] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str", 0)
        if ticker:
            ticker_cik[ticker] = _pad_cik(cik)
    return ticker_cik


# ---------------------------------------------------------------------------
# PHASE 1: Stratified sampling
# ---------------------------------------------------------------------------

def stratified_sample(
    events: list[dict],
    ticker_cik: dict[str, str],
    target_per_cohort: int = 16,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    """Sample ~target_per_cohort events per as_of cohort.

    Algorithm (seed=42):
    - Group events by as_of cohort.
    - Within each cohort, split into signal (is_null=False) and null (is_null=True).
    - ALL signal events are included regardless of quota.
    - Null events are randomly sampled proportionally to fill up to target.
    - Events whose ticker has no CIK mapping are skipped; counted and reported.

    Returns (sampled_events, stats_dict).
    """
    rng = random.Random(seed)

    # Group by cohort
    cohort_groups: dict[str, list[dict]] = defaultdict(list)
    for evt in events:
        cohort_groups[evt["as_of"]].append(evt)

    sampled: list[dict] = []
    stats = {
        "seed": seed,
        "target_per_cohort": target_per_cohort,
        "cohorts": {},
        "no_cik_tickers": set(),
        "no_cik_events_skipped": 0,
        "total_sampled": 0,
        "signal_events_included": 0,
        "null_events_sampled": 0,
    }

    for cohort_date in sorted(cohort_groups.keys()):
        cohort = cohort_groups[cohort_date]

        # Separate signal vs null
        signal_evts = [e for e in cohort if not e["is_null"]]
        null_evts = [e for e in cohort if e["is_null"]]

        # Filter by CIK availability
        def has_cik(e: dict) -> bool:
            ticker = str(e.get("ticker", "")).upper()
            if ticker not in ticker_cik:
                stats["no_cik_tickers"].add(ticker)
                return False
            return True

        signal_with_cik = [e for e in signal_evts if has_cik(e)]
        null_with_cik = [e for e in null_evts if has_cik(e)]

        signal_skipped = len(signal_evts) - len(signal_with_cik)
        null_skipped = len(null_evts) - len(null_with_cik)
        stats["no_cik_events_skipped"] += signal_skipped + null_skipped

        # Include all signal events
        chosen_signal = signal_with_cik[:]

        # Null quota = target - signal included (but at least 0)
        null_quota = max(0, target_per_cohort - len(chosen_signal))

        # Proportional null sample (without replacement, but cap at available)
        null_sample_n = min(null_quota, len(null_with_cik))
        chosen_null = rng.sample(null_with_cik, null_sample_n) if null_sample_n > 0 else []

        cohort_chosen = chosen_signal + chosen_null

        stats["cohorts"][cohort_date] = {
            "cohort_total": len(cohort),
            "signal_total": len(signal_evts),
            "null_total": len(null_evts),
            "signal_with_cik": len(signal_with_cik),
            "null_with_cik": len(null_with_cik),
            "signal_included": len(chosen_signal),
            "null_sampled": len(chosen_null),
            "total_chosen": len(cohort_chosen),
            "is_explore": cohort_date <= "2020-12-31",
        }
        stats["signal_events_included"] += len(chosen_signal)
        stats["null_events_sampled"] += len(chosen_null)
        sampled.extend(cohort_chosen)

    stats["total_sampled"] = len(sampled)
    stats["no_cik_tickers"] = sorted(stats["no_cik_tickers"])
    return sampled, stats


# ---------------------------------------------------------------------------
# PHASE 2: Fetch Form 4 filings for sampled events
# ---------------------------------------------------------------------------

def _load_index() -> dict:
    index_path = CACHE_DIR / "index.json"
    if index_path.exists():
        try:
            with open(index_path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        # DI-03: self-healing reconciliation — any entry claiming xml_status='ok'
        # for a filing whose XML file is missing on disk gets downgraded to 'missing'
        # so the next run refetches it rather than silently skipping it.
        # Strip _meta key so callers only see event_key→result entries.
        index = {k: v for k, v in raw.items() if k != _INDEX_META_KEY}
        repaired = 0
        for event_key, entry in index.items():
            if not isinstance(entry, dict):
                continue
            for filing in entry.get("filings", []):
                if filing.get("xml_status") != "ok":
                    continue
                cik = entry.get("cik", "")
                accession = filing.get("accession", "")
                if not cik or not accession:
                    continue
                padded = str(int(cik)).zfill(10)
                accession_nodash = accession.replace("-", "")
                cache_path = CACHE_DIR / f"{padded}_{accession_nodash}.xml"
                if not cache_path.exists():
                    filing["xml_status"] = "missing"
                    repaired += 1
        if repaired:
            log.warning("_load_index: downgraded %d index entries to 'missing' (XML absent on disk)", repaired)
        return index
    return {}


_INDEX_META_KEY = "_meta"


def _save_index(index: dict, seed: int = 42, target_per_cohort: int = 16) -> None:
    """Write index dict to disk atomically. DI-04: embeds _meta for identity check."""
    index_path = CACHE_DIR / "index.json"
    # Inject/update _meta without mutating caller's dict
    payload = {_INDEX_META_KEY: {
        "schema_version": 1,
        "seed": seed,
        "target_per_cohort": target_per_cohort,
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }}
    payload.update({k: v for k, v in index.items() if k != _INDEX_META_KEY})
    tmp = index_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())  # DI-05: match fileutil.py fsync pattern for durability
    tmp.replace(index_path)


def _get_submissions(cik: str) -> dict | None:
    """Fetch submissions index for CIK. Mirrors edgar.py fetch_submissions."""
    padded = _pad_cik(cik)
    # Use the shared submissions cache from the parent cache dir
    shared_subs = BASE_DIR / "backend/data/turnaround/edgar_cache/submissions" / f"{padded}.json"
    if shared_subs.exists():
        try:
            with open(shared_subs, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    try:
        raw = _edgar_get(url)
        data = json.loads(raw)
        # Cache for reuse
        shared_subs.parent.mkdir(parents=True, exist_ok=True)
        tmp = shared_subs.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        tmp.replace(shared_subs)
        return data
    except Exception as exc:
        log.warning("submissions fetch failed CIK %s: %s", padded, exc)
        return None


def _get_form4_xml(cik: str, accession: str) -> str:
    """Fetch Form 4 XML; cache under form4_stratified/. Returns '' on failure."""
    padded = _pad_cik(cik)
    accession_nodash = accession.replace("-", "")
    cache_path = CACHE_DIR / f"{padded}_{accession_nodash}.xml"

    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    cik_int = int(padded)
    # Try index.json to find the xml filename
    idx_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}"
        f"/{accession_nodash}/index.json"
    )
    xml_filename = None
    try:
        raw = _edgar_get(idx_url)
        idx_data = json.loads(raw)
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
        log.debug("form4 index fetch failed %s/%s: %s", padded, accession, exc)
        return ""

    if not xml_filename:
        return ""

    xml_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}"
        f"/{accession_nodash}/{xml_filename}"
    )
    try:
        raw = _edgar_get(xml_url)
        xml_content = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("form4 XML fetch failed %s: %s", xml_url, exc)
        return ""

    # Validate before caching — DI-01/PY-05: ParseError → return '' so caller
    # records xml_status='fail'; never cache or count malformed content as ok.
    try:
        ET.fromstring(xml_content)
    except ET.ParseError:
        log.warning("Malformed XML (ParseError) for %s/%s — treating as fail", padded, accession)
        return ""

    # Atomic write
    tmp = cache_path.with_suffix(".xml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml_content)
    tmp.replace(cache_path)
    return xml_content


def fetch_form4_for_event(
    event: dict,
    ticker_cik: dict[str, str],
    index: dict,
) -> dict:
    """Fetch all Form 4 filings for one event. Returns filing summary dict."""
    ticker = str(event["ticker"]).upper()
    as_of_str = event["as_of"]
    event_key = f"{ticker}_{as_of_str}"

    if event_key in index and index[event_key].get("status") == "done":
        return index[event_key]

    cik = ticker_cik.get(ticker)
    if not cik:
        result = {"ticker": ticker, "as_of": as_of_str, "status": "no_cik", "filings": []}
        index[event_key] = result
        return result

    as_of_date = date.fromisoformat(as_of_str)
    window_start = as_of_date - timedelta(days=90)
    window_start_str = window_start.isoformat()
    as_of_str_iso = as_of_str

    subs = _get_submissions(cik)
    if subs is None:
        result = {"ticker": ticker, "as_of": as_of_str, "cik": cik, "status": "subs_fail", "filings": []}
        index[event_key] = result
        return result

    filings_section = subs.get("filings", {}).get("recent", {})
    forms = filings_section.get("form", [])
    accessions = filings_section.get("accessionNumber", [])
    filed_dates = filings_section.get("filingDate", [])

    form4_accessions = []
    for form, accession, filed in zip(forms, accessions, filed_dates):
        if form not in ("4", "4/A"):
            continue
        # PY-01: Both boundaries inclusive — spec says "90 days before as_of"
        # which includes as_of itself (project convention: [as_of−90d, as_of]).
        if not (window_start_str <= filed <= as_of_str_iso):
            continue
        form4_accessions.append({"accession": accession, "filed": filed})

    filings_fetched = []
    for item in form4_accessions:
        accession = item["accession"]
        xml = _get_form4_xml(cik, accession)
        status = "ok" if xml else "fail"
        filings_fetched.append({
            "accession": accession,
            "filed": item["filed"],
            "xml_status": status,
        })

    result = {
        "ticker": ticker,
        "as_of": as_of_str,
        "cik": cik,
        "status": "done",
        "filings": filings_fetched,
        "form4_count": len(filings_fetched),
        "ok_count": sum(1 for f in filings_fetched if f["xml_status"] == "ok"),
    }
    index[event_key] = result
    return result


def run_fetch_phase(
    sampled: list[dict],
    ticker_cik: dict[str, str],
) -> tuple[dict, dict]:
    """Run Phase 2 fetch with 75-minute wall-clock cap. Returns (index, fetch_stats)."""
    global _fetch_start_time
    _fetch_start_time = time.monotonic()

    index = _load_index()
    fetch_stats = {
        "total_events": len(sampled),
        "done": 0,
        "skipped_cached": 0,
        "no_cik": 0,
        "subs_fail": 0,
        "form4_fetched": 0,
        "form4_ok": 0,
        "cap_hit": False,
        "events_processed": 0,
    }

    total = len(sampled)
    for i, event in enumerate(sampled):
        elapsed = time.monotonic() - _fetch_start_time
        if elapsed > _WALL_CAP_SECS:
            log.warning("WALL CAP HIT at %d/%d events (%.1f min)", i, total, elapsed / 60)
            fetch_stats["cap_hit"] = True
            break

        ticker = event["ticker"].upper()
        as_of_str = event["as_of"]
        event_key = f"{ticker}_{as_of_str}"

        if event_key in index and index[event_key].get("status") == "done":
            fetch_stats["skipped_cached"] += 1
            fetch_stats["events_processed"] += 1
            fetch_stats["done"] += 1
            existing = index[event_key]
            fetch_stats["form4_fetched"] += existing.get("form4_count", 0)
            fetch_stats["form4_ok"] += existing.get("ok_count", 0)
            continue

        if i % 50 == 0:
            log.info("Fetching event %d/%d — %s %s (%.1fmin elapsed)", i + 1, total, ticker, as_of_str, elapsed / 60)

        result = fetch_form4_for_event(event, ticker_cik, index)
        fetch_stats["events_processed"] += 1

        status = result.get("status")
        if status == "done":
            fetch_stats["done"] += 1
            fetch_stats["form4_fetched"] += result.get("form4_count", 0)
            fetch_stats["form4_ok"] += result.get("ok_count", 0)
        elif status == "no_cik":
            fetch_stats["no_cik"] += 1
        elif status == "subs_fail":
            fetch_stats["subs_fail"] += 1

        # Persist index every 10 events
        if (i + 1) % 10 == 0:
            _save_index(index)

    _save_index(index)
    return index, fetch_stats


# ---------------------------------------------------------------------------
# PHASE 3: Coverage balance check
# ---------------------------------------------------------------------------

def check_coverage_balance(sampled: list[dict], index: dict) -> dict:
    """Check per-stratum coverage is roughly balanced."""
    explore_cohorts: dict[str, dict] = defaultdict(lambda: {"total": 0, "done": 0})
    confirm_cohorts: dict[str, dict] = defaultdict(lambda: {"total": 0, "done": 0})

    for evt in sampled:
        ticker = evt["ticker"].upper()
        as_of = evt["as_of"]
        key = f"{ticker}_{as_of}"
        entry = index.get(key, {})
        done = entry.get("status") == "done"
        if as_of <= "2020-12-31":
            explore_cohorts[as_of]["total"] += 1
            if done:
                explore_cohorts[as_of]["done"] += 1
        else:
            confirm_cohorts[as_of]["total"] += 1
            if done:
                confirm_cohorts[as_of]["done"] += 1

    def coverage_pct(cohorts: dict) -> list[float]:
        out = []
        for v in cohorts.values():
            t = v["total"]
            if t > 0:
                out.append(v["done"] / t * 100)
        return out

    explore_pcts = coverage_pct(explore_cohorts)
    confirm_pcts = coverage_pct(confirm_cohorts)

    def stats(pcts: list[float]) -> dict:
        if not pcts:
            return {"min": 0, "max": 0, "mean": 0, "n_cohorts": 0}
        return {
            "min": round(min(pcts), 1),
            "max": round(max(pcts), 1),
            "mean": round(sum(pcts) / len(pcts), 1),
            "n_cohorts": len(pcts),
        }

    total_explore = sum(v["total"] for v in explore_cohorts.values())
    done_explore = sum(v["done"] for v in explore_cohorts.values())
    total_confirm = sum(v["total"] for v in confirm_cohorts.values())
    done_confirm = sum(v["done"] for v in confirm_cohorts.values())

    return {
        "explore": stats(explore_pcts),
        "confirm": stats(confirm_pcts),
        "explore_overall_pct": round(done_explore / total_explore * 100, 1) if total_explore > 0 else 0,
        "confirm_overall_pct": round(done_confirm / total_confirm * 100, 1) if total_confirm > 0 else 0,
        "balanced": (
            min(explore_pcts + confirm_pcts) >= 50 if (explore_pcts and confirm_pcts) else False
        ),
    }


# ---------------------------------------------------------------------------
# PHASE 3: Parse Form 4 XMLs — extract distinct insider BUY owners
# ---------------------------------------------------------------------------

def _parse_owner_cik(xml_content: str) -> str | None:
    """Extract reporting owner CIK from Form 4 XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None
    # Look for reportingOwner/reportingOwnerId/rptOwnerCik
    for owner_el in root.iter("reportingOwner"):
        cik_el = owner_el.find(".//rptOwnerCik")
        if cik_el is not None and cik_el.text:
            return cik_el.text.strip()
    return None


def _has_open_market_buy(xml_content: str) -> bool:
    """Return True if the Form 4 has ≥1 nonDerivative transaction with code P and acquiredDisposedCode A."""
    if not xml_content:
        return False
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return False

    for txn in root.iter("nonDerivativeTransaction"):
        code_el = txn.find(".//transactionCode")
        adc_el = txn.find(".//transactionAcquiredDisposedCode/value")
        if code_el is None:
            continue
        code = (code_el.text or "").strip()
        adc = (adc_el.text or "").strip().upper() if adc_el is not None else ""
        if code == "P" and adc == "A":
            return True
    return False


def count_distinct_insider_buys(event: dict, ticker_cik: dict[str, str], index: dict) -> int:
    """Count distinct reporting owner CIKs with open-market BUY filings in 90d window.

    Open-market BUY = nonDerivativeTransaction with transactionCode P AND
    acquiredDisposedCode A (acquired). Excludes derivative-only filings.
    Distinct = distinct reporting owner CIKs.
    """
    ticker = event["ticker"].upper()
    as_of_str = event["as_of"]
    event_key = f"{ticker}_{as_of_str}"

    entry = index.get(event_key, {})
    if entry.get("status") != "done":
        return -1  # data unavailable

    cik = entry.get("cik")
    if not cik:
        return -1

    filings = entry.get("filings", [])
    buyer_owner_ciks: set[str] = set()

    for filing in filings:
        if filing.get("xml_status") != "ok":
            continue
        accession = filing["accession"]
        padded = _pad_cik(cik)
        accession_nodash = accession.replace("-", "")
        cache_path = CACHE_DIR / f"{padded}_{accession_nodash}.xml"
        if not cache_path.exists():
            continue
        xml_content = cache_path.read_text(encoding="utf-8")
        if _has_open_market_buy(xml_content):
            owner_cik = _parse_owner_cik(xml_content)
            if owner_cik:
                buyer_owner_ciks.add(owner_cik)
            else:
                # If we can't get owner CIK, use accession as proxy for distinct
                buyer_owner_ciks.add(f"acc:{accession}")

    return len(buyer_owner_ciks)


# ---------------------------------------------------------------------------
# PHASE 3: Statistical helpers
# ---------------------------------------------------------------------------

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for proportion k/n at ~95% CI."""
    if n == 0:
        return (0.0, 0.0)
    p_hat = k / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    spread = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    lo = max(0.0, centre - spread)
    hi = min(1.0, centre + spread)
    return (round(lo, 4), round(hi, 4))


def median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


# ---------------------------------------------------------------------------
# PHASE 3: The frozen test
# ---------------------------------------------------------------------------

def run_frozen_test(
    sampled: list[dict],
    ticker_cik: dict[str, str],
    index: dict,
) -> dict:
    """Pre-registered frozen test (verbatim spec).

    Events with ≥2 distinct insider open-market BUY filings (transaction code P)
    in the 90 days before as_of vs events without, on the EXPLORE slice only
    (as_of ≤ 2020-12-31): hit rate (Wilson CI), median net_return_pct,
    median horizon_end_return_pct. Per-cohort robustness check.
    """
    # Explore slice
    explore = [e for e in sampled if e["as_of"] <= "2020-12-31"]
    confirm_all = [e for e in sampled if e["as_of"] > "2020-12-31"]

    log.info("Explore slice: %d events, Confirm slice: %d events", len(explore), len(confirm_all))

    def classify_events(events: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """Return (buyers_ge2, buyers_lt2, no_data)."""
        buyers_ge2 = []
        buyers_lt2 = []
        no_data = []
        for evt in events:
            n = count_distinct_insider_buys(evt, ticker_cik, index)
            if n < 0:
                no_data.append(evt)
            elif n >= 2:
                buyers_ge2.append(evt)
            else:
                buyers_lt2.append(evt)
        return buyers_ge2, buyers_lt2, no_data

    explore_ge2, explore_lt2, explore_no_data = classify_events(explore)
    confirm_ge2, confirm_lt2, confirm_no_data = classify_events(confirm_all)

    def compute_group_stats(events: list[dict], label: str) -> dict:
        n = len(events)
        hits = sum(1 for e in events if e.get("hit", False))
        hit_rate = hits / n if n > 0 else 0.0
        ci_lo, ci_hi = wilson_ci(hits, n)
        net_rets = [e["net_return_pct"] for e in events if e.get("net_return_pct") is not None]
        horiz_rets = [e["horizon_end_return_pct"] for e in events if e.get("horizon_end_return_pct") is not None]
        return {
            "label": label,
            "n": n,
            "hits": hits,
            "hit_rate": round(hit_rate * 100, 1),
            "ci_95": [round(ci_lo * 100, 1), round(ci_hi * 100, 1)],
            "median_net_return_pct": round(median(net_rets), 2) if median(net_rets) is not None else None,
            "median_horizon_end_return_pct": round(median(horiz_rets), 2) if median(horiz_rets) is not None else None,
            "underpowered": n < 15,
        }

    explore_ge2_stats = compute_group_stats(explore_ge2, "explore_ge2_buyers")
    explore_lt2_stats = compute_group_stats(explore_lt2, "explore_lt2_buyers")

    # Per-cohort robustness check
    cohort_robustness = []
    explore_cohort_dates = sorted(set(e["as_of"] for e in explore))
    for cohort_date in explore_cohort_dates:
        cohort_evts = [e for e in explore if e["as_of"] == cohort_date]
        # PY-08: pre-compute buyer counts once per event to avoid double disk reads
        buyer_counts = {id(e): count_distinct_insider_buys(e, ticker_cik, index) for e in cohort_evts}
        c_ge2 = [e for e in cohort_evts if buyer_counts[id(e)] >= 2]
        c_lt2 = [e for e in cohort_evts if 0 <= buyer_counts[id(e)] < 2]
        ge2_hits = sum(1 for e in c_ge2 if e.get("hit"))
        lt2_hits = sum(1 for e in c_lt2 if e.get("hit"))
        cohort_robustness.append({
            "as_of": cohort_date,
            "ge2_n": len(c_ge2),
            "ge2_hit_pct": round(ge2_hits / len(c_ge2) * 100, 1) if c_ge2 else None,
            "lt2_n": len(c_lt2),
            "lt2_hit_pct": round(lt2_hits / len(c_lt2) * 100, 1) if c_lt2 else None,
        })

    # Direction agreement pct: cohorts where ge2 hit_rate > lt2 hit_rate
    comparable = [
        c for c in cohort_robustness
        if c["ge2_n"] > 0 and c["lt2_n"] > 0
        and c["ge2_hit_pct"] is not None and c["lt2_hit_pct"] is not None
    ]
    direction_agree = sum(1 for c in comparable if c["ge2_hit_pct"] >= c["lt2_hit_pct"])
    direction_pct = round(direction_agree / len(comparable) * 100, 1) if comparable else None

    # CONFIRM stats (computed but sealed)
    confirm_ge2_stats = compute_group_stats(confirm_ge2, "confirm_ge2_buyers")
    confirm_lt2_stats = compute_group_stats(confirm_lt2, "confirm_lt2_buyers")

    verdict = "underpowered, CI only, no verdict language" if explore_ge2_stats["underpowered"] else "powered"

    return {
        "explore": {
            "slice": "as_of ≤ 2020-12-31",
            "total_events": len(explore),
            "no_data_events": len(explore_no_data),
            "ge2_buyers": explore_ge2_stats,
            "lt2_buyers": explore_lt2_stats,
            "verdict": verdict,
            "cohort_robustness": cohort_robustness,
            "direction_agree_pct": direction_pct,
            "comparable_cohorts": len(comparable),
        },
        "confirm_SEALED": {
            "note": "CONFIRM (read only if explore signals)",
            "slice": "as_of > 2020-12-31",
            "total_events": len(confirm_all),
            "no_data_events": len(confirm_no_data),
            "ge2_buyers": confirm_ge2_stats,
            "lt2_buyers": confirm_lt2_stats,
        },
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def write_report(
    sample_stats: dict,
    fetch_stats: dict,
    coverage_balance: dict,
    test_results: dict,
    elapsed_secs: float,
) -> None:
    """Write impl-f334.md report."""
    lines: list[str] = []

    lines.append("# F334 — Stratified Form 4 Refetch + Pre-Registered Insider Test")
    lines.append(f"\n**Run date:** {date.today().isoformat()}")
    lines.append(f"**Elapsed:** {elapsed_secs / 60:.1f} minutes")
    lines.append(f"**Wall cap hit:** {fetch_stats.get('cap_hit', False)}")
    lines.append("")

    # Sampling table
    lines.append("## Sampling (seed=42)")
    lines.append("")
    lines.append(f"- Total events in dataset: {sum(c['cohort_total'] for c in sample_stats['cohorts'].values())}")
    lines.append(f"- Signal events (is_null=False): {sample_stats['signal_events_included']}")
    lines.append(f"- Null events sampled: {sample_stats['null_events_sampled']}")
    lines.append(f"- Total sampled: {sample_stats['total_sampled']}")
    lines.append(f"- No-CIK tickers skipped: {len(sample_stats['no_cik_tickers'])} tickers, {sample_stats['no_cik_events_skipped']} events")
    if sample_stats['no_cik_tickers']:
        lines.append(f"  - Tickers: {', '.join(sample_stats['no_cik_tickers'])}")
    lines.append("")
    lines.append("### Per-Cohort Sample Table")
    lines.append("")
    lines.append("| as_of | cohort_total | signal_incl | null_sampled | chosen | slice |")
    lines.append("|-------|-------------|-------------|--------------|--------|-------|")
    for cohort_date, c in sorted(sample_stats["cohorts"].items()):
        slice_label = "explore" if c["is_explore"] else "confirm"
        lines.append(
            f"| {cohort_date} | {c['cohort_total']} | {c['signal_included']} "
            f"| {c['null_sampled']} | {c['total_chosen']} | {slice_label} |"
        )
    lines.append("")

    # Fetch stats
    lines.append("## Fetch Statistics")
    lines.append("")
    lines.append(f"- Events processed: {fetch_stats['events_processed']} / {fetch_stats['total_events']}")
    lines.append(f"- Done (submissions + XMLs fetched): {fetch_stats['done']}")
    lines.append(f"- Skipped (already cached): {fetch_stats['skipped_cached']}")
    lines.append(f"- No CIK: {fetch_stats['no_cik']}")
    lines.append(f"- Submissions fetch fail: {fetch_stats['subs_fail']}")
    lines.append(f"- Form 4 filings fetched: {fetch_stats['form4_fetched']}")
    lines.append(f"- Form 4 XMLs OK: {fetch_stats['form4_ok']}")
    lines.append("")

    # Coverage balance
    lines.append("## Coverage Balance Check")
    lines.append("")
    bal = coverage_balance
    lines.append(f"- Explore overall: {bal['explore_overall_pct']}%  (cohort range: {bal['explore']['min']}–{bal['explore']['max']}%, mean {bal['explore']['mean']}%, n={bal['explore']['n_cohorts']} cohorts)")
    lines.append(f"- Confirm overall: {bal['confirm_overall_pct']}%  (cohort range: {bal['confirm']['min']}–{bal['confirm']['max']}%, mean {bal['confirm']['mean']}%, n={bal['confirm']['n_cohorts']} cohorts)")
    balanced_str = "BALANCED (min ≥ 50%)" if bal["balanced"] else "IMBALANCED (some cohorts < 50%)"
    lines.append(f"- Balance assessment: {balanced_str}")
    lines.append("")

    # Test results — EXPLORE
    lines.append("## Pre-Registered Test Results (EXPLORE slice only)")
    lines.append("")
    lines.append("**Test spec (verbatim):** Events with ≥2 distinct insider open-market BUY filings")
    lines.append("(transaction code P) in the 90 days before as_of vs events without, on the EXPLORE")
    lines.append("slice only (as_of ≤ 2020-12-31): hit rate (Wilson CI), median net_return_pct,")
    lines.append("median horizon_end_return_pct. Per-cohort robustness check (per-cohort direction pct).")
    lines.append("")

    exp = test_results["explore"]
    lines.append(f"**Explore slice:** {exp['total_events']} events (no-data excluded: {exp['no_data_events']})")
    lines.append(f"**Verdict:** {exp['verdict']}")
    lines.append("")
    lines.append("### Group Comparison")
    lines.append("")
    lines.append("| Group | n | Hit rate | Wilson 95% CI | Median net_return_pct | Median horizon_end_return_pct |")
    lines.append("|-------|---|----------|---------------|-----------------------|-------------------------------|")

    def group_row(g: dict) -> str:
        ci = g["ci_95"]
        underpow = " **(underpowered)**" if g["underpowered"] else ""
        return (
            f"| {g['label']}{underpow} | {g['n']} | {g['hit_rate']}% "
            f"| [{ci[0]}%, {ci[1]}%] "
            f"| {g['median_net_return_pct']} "
            f"| {g['median_horizon_end_return_pct']} |"
        )

    lines.append(group_row(exp["ge2_buyers"]))
    lines.append(group_row(exp["lt2_buyers"]))
    lines.append("")

    lines.append("### Per-Cohort Robustness")
    lines.append("")
    lines.append(f"Direction agreement (ge2 hit% ≥ lt2 hit%): {exp['direction_agree_pct']}% of {exp['comparable_cohorts']} comparable cohorts")
    lines.append("")
    lines.append("| as_of | ge2_n | ge2_hit% | lt2_n | lt2_hit% |")
    lines.append("|-------|-------|----------|-------|----------|")
    for c in exp["cohort_robustness"]:
        ge2_hp = f"{c['ge2_hit_pct']}%" if c["ge2_hit_pct"] is not None else "—"
        lt2_hp = f"{c['lt2_hit_pct']}%" if c["lt2_hit_pct"] is not None else "—"
        lines.append(f"| {c['as_of']} | {c['ge2_n']} | {ge2_hp} | {c['lt2_n']} | {lt2_hp} |")
    lines.append("")

    # CONFIRM — sealed appendix
    lines.append("---")
    lines.append("")
    lines.append("## CONFIRM (read only if explore signals)")
    lines.append("")
    lines.append("> **SEALED APPENDIX — Read only if explore group shows a signal worth investigating.**")
    lines.append("> Numbers are computed but deliberately separated to avoid contaminating explore interpretation.")
    lines.append("")

    conf = test_results["confirm_SEALED"]
    lines.append(f"**Confirm slice:** {conf['total_events']} events (no-data excluded: {conf['no_data_events']})")
    lines.append("")
    lines.append("| Group | n | Hit rate | Wilson 95% CI | Median net_return_pct | Median horizon_end_return_pct |")
    lines.append("|-------|---|----------|---------------|-----------------------|-------------------------------|")
    lines.append(group_row(conf["ge2_buyers"]))
    lines.append(group_row(conf["lt2_buyers"]))
    lines.append("")

    report_text = "\n".join(lines) + "\n"
    tmp = REPORT_PATH.with_suffix(".md.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(report_text)
    tmp.replace(REPORT_PATH)
    log.info("Report written: %s", REPORT_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    t0 = time.monotonic()

    log.info("=== F334 Phase 1: Load + Sample ===")
    events = load_events()
    ticker_cik = build_ticker_cik_map(events)
    log.info("Loaded %d events, %d ticker→CIK mappings", len(events), len(ticker_cik))

    sampled, sample_stats = stratified_sample(events, ticker_cik, target_per_cohort=16, seed=42)
    log.info(
        "Sampled %d events (%d signal, %d null) from %d cohorts; %d events skipped (no CIK)",
        sample_stats["total_sampled"],
        sample_stats["signal_events_included"],
        sample_stats["null_events_sampled"],
        len(sample_stats["cohorts"]),
        sample_stats["no_cik_events_skipped"],
    )

    log.info("=== F334 Phase 2: Fetch Form 4 XMLs ===")
    index, fetch_stats = run_fetch_phase(sampled, ticker_cik)
    log.info(
        "Fetch complete: %d done, %d cached, %d form4 XMLs OK",
        fetch_stats["done"],
        fetch_stats["skipped_cached"],
        fetch_stats["form4_ok"],
    )

    coverage_balance = check_coverage_balance(sampled, index)
    log.info(
        "Coverage balance: explore=%s%%, confirm=%s%% (balanced=%s)",
        coverage_balance["explore_overall_pct"],
        coverage_balance["confirm_overall_pct"],
        coverage_balance["balanced"],
    )

    if fetch_stats["cap_hit"] and not coverage_balance["balanced"]:
        log.error("STATUS: blocked — cap hit AND coverage imbalanced")
        elapsed = time.monotonic() - t0
        write_report(sample_stats, fetch_stats, coverage_balance, {
            "explore": {"slice": "blocked", "total_events": 0, "no_data_events": 0,
                        "ge2_buyers": {}, "lt2_buyers": {}, "verdict": "BLOCKED",
                        "cohort_robustness": [], "direction_agree_pct": None, "comparable_cohorts": 0},
            "confirm_SEALED": {"note": "BLOCKED", "slice": "", "total_events": 0,
                               "no_data_events": 0, "ge2_buyers": {}, "lt2_buyers": {}},
        }, elapsed)
        print("STATUS: blocked")
        return

    log.info("=== F334 Phase 3: Pre-Registered Frozen Test ===")
    test_results = run_frozen_test(sampled, ticker_cik, index)

    elapsed = time.monotonic() - t0
    write_report(sample_stats, fetch_stats, coverage_balance, test_results, elapsed)

    # Summary to stdout
    exp = test_results["explore"]
    ge2 = exp["ge2_buyers"]
    lt2 = exp["lt2_buyers"]
    print(f"STATUS: ok")
    print(f"Coverage: explore={coverage_balance['explore_overall_pct']}%, confirm={coverage_balance['confirm_overall_pct']}%")
    print(f"Explore groups: ge2_buyers n={ge2['n']}, lt2_buyers n={lt2['n']}, no_data={exp['no_data_events']}")
    print(f"Explore ge2 hit_rate={ge2['hit_rate']}% CI=[{ge2['ci_95'][0]}%,{ge2['ci_95'][1]}%]; lt2 hit_rate={lt2['hit_rate']}% CI=[{lt2['ci_95'][0]}%,{lt2['ci_95'][1]}%]")
    print(f"Verdict: {exp['verdict']}")
    print(f"Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
