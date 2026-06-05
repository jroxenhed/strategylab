"""F335 Phase A: survivorship-bias bound from free EDGAR data.

Estimates per-cohort-year delisting / deregistration INTENSITY from EDGAR
quarterly form.idx index files (Form 25 / 25-NSE = exchange delisting,
Form 15-12B/12G/15D = deregistration), then bounds how far the pond's
hit-rates *could* move if invisible (delisted-within-horizon) names were
restored as phantom misses.

The pond universe (validation_result.json) contains only currently-listed
names, so delisted candidates are invisible. The washed-out / penny filter
selects crashed names; a delisted-within-horizon name that WOULD have been
selected is a MISSING MISS. We cannot observe these, so we BOUND them.

Honesty constraints (baked in):
  * Not every delisting is a crash. M&A acquisitions file Form 25; going-
    private deals file Form 15. We do NOT pretend to know the distress split.
    We bracket it: LOW=30% / MID=50% / HIGH=80% of delistings are distress.
  * We cannot know how many distress-delisted names were sub-$2-equivalent
    turnaround candidates. We bound by asking: how many phantom misses would
    be NEEDED to erase the effect, and is that number plausible vs measured
    delisting counts.

Sources / convention:
  * form.idx quarterly index: www.sec.gov/Archives/edgar/full-index/YYYY/QTR#/form.idx
  * User-Agent mirrors backend/edgar.py; <=5 req/s; raw responses cached so
    reruns are free.

stdlib + urllib only.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent  # backend/
VALIDATION_PATH = ROOT / "data" / "turnaround" / "validation_result.json"
CACHE_DIR = ROOT / "data" / "turnaround" / "edgar_cache" / "delisting"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "StrategyLab/1.0 (contact: john@milford.se)"  # mirror edgar.py
MIN_INTERVAL = 0.22  # ~4.5 req/s, comfortably <=5

COHORT_YEARS = list(range(2015, 2024))  # 2015..2023 inclusive
QUARTERS = [1, 2, 3, 4]

# Exchange-delisting forms (issuer 25 + exchange 25-NSE). Exclude /A amendments.
DELIST_FORMS = {"25", "25-NSE"}
# Deregistration (Form 15) — going dark / deregister. Exclude foreign 15F* and /A.
# PY-02: empirically verified against all cached form.idx files (2015-2023):
# actual tokens are '15-12B', '15-12G', '15-15D' — '15D' does NOT appear in EDGAR idx.
# Counts: 15-12B=2072, 15-12G=2647, 15-15D=4099 across 9 years × 4 quarters.
DEREG_FORMS = {"15-12B", "15-12G", "15-15D"}

# Distress-split scenarios: fraction of delistings/deregistrations that are
# genuine crashes (the rest are M&A / going-private / clean exits).
SCENARIOS = {"LOW": 0.30, "MID": 0.50, "HIGH": 0.80}

# --------------------------------------------------------------------------
# Rate-limited cached fetch
# --------------------------------------------------------------------------

_last_req = [0.0]


def _throttle() -> None:
    now = time.monotonic()
    wait = MIN_INTERVAL - (now - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    _last_req[0] = time.monotonic()


def fetch_form_idx(year: int, qtr: int) -> str:
    """Fetch one quarterly form.idx, caching the raw (gzipped) bytes.

    DI-06/PY-07: retry with exponential backoff on 429/5xx; write to tmp.gz
    then os.replace() to avoid a partial-write corrupt cache on kill/network drop.
    Returns decoded text. Reruns hit cache (free)."""
    cache_path = CACHE_DIR / f"form_{year}_QTR{qtr}.idx.gz"
    if cache_path.exists():
        with gzip.open(cache_path, "rb") as fh:
            return fh.read().decode("latin-1")

    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/form.idx"
    delays = [2, 8, 30]
    last_exc: Exception | None = None
    for attempt in range(4):  # up to 3 retries
        _throttle()
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            break  # success
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                delay = delays[attempt]
                print(f"  HTTP {exc.code} fetching {url} — retry {attempt+1} in {delay}s")
                time.sleep(delay)
                last_exc = exc
                continue
            raise
        except Exception as exc:
            if attempt < 3:
                delay = delays[attempt]
                time.sleep(delay)
                last_exc = exc
                continue
            raise
    else:
        raise RuntimeError(f"fetch_form_idx exhausted retries for {url}") from last_exc

    # Validate decoded length before caching (guards partial network reads)
    decoded = raw.decode("latin-1")
    if len(decoded) < 1000:
        raise RuntimeError(f"fetch_form_idx: suspiciously short response ({len(decoded)} bytes) for {url}")

    # DI-06: atomic tmp+rename so a kill mid-write doesn't leave corrupt .idx.gz
    tmp_path = cache_path.with_suffix(".gz.tmp")
    with gzip.open(tmp_path, "wb") as fh:
        fh.write(raw)
    os.replace(str(tmp_path), str(cache_path))
    return decoded


def count_forms(text: str) -> Counter:
    """Count form-type tokens of interest in a form.idx body."""
    c: Counter = Counter()
    started = False
    for line in text.splitlines():
        if line.startswith("---"):
            started = True
            continue
        if not started:
            continue
        ft = line[:12].strip()
        if ft in DELIST_FORMS or ft in DEREG_FORMS:
            c[ft] += 1
    return c


# --------------------------------------------------------------------------
# Step 1: measure per-year EDGAR delisting / dereg counts
# --------------------------------------------------------------------------


def measure_delistings() -> dict:
    """Return {year: {'delist': n25, 'dereg': n15, 'total': , 'detail': Counter}}."""
    out: dict = {}
    for year in COHORT_YEARS:
        agg: Counter = Counter()
        for qtr in QUARTERS:
            txt = fetch_form_idx(year, qtr)
            agg.update(count_forms(txt))
        delist = sum(agg[f] for f in DELIST_FORMS)
        dereg = sum(agg[f] for f in DEREG_FORMS)
        out[year] = {
            "delist": delist,
            "dereg": dereg,
            "total": delist + dereg,
            "detail": dict(agg),
        }
        print(f"  {year}: 25/25-NSE={delist:5d}  Form15={dereg:4d}  total={delist+dereg:5d}")
    return out


# --------------------------------------------------------------------------
# Step 2: pond slices from validation_result.json
# --------------------------------------------------------------------------


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def load_pond() -> dict:
    # PY-11/DI-07: use context manager + explicit utf-8 encoding
    with open(VALIDATION_PATH, encoding="utf-8") as f:
        d = json.load(f)
    ev = d["events"]
    for e in ev:
        e["year"] = int(e["as_of"][:4])
    pond = {"events": ev, "raw": d}

    # Pond-wide observed base rates (currently-listed only).
    n_all = len(ev)
    h_all = sum(1 for e in ev if e["hit"])
    pond["all"] = {"n": n_all, "hits": h_all}

    # Penny slice: entry_price < $2.
    penny = [e for e in ev if e.get("entry_price") is not None and e["entry_price"] < 2.0]
    pond["penny_all"] = {
        "n": len(penny),
        "hits": sum(1 for e in penny if e["hit"]),
        "events": penny,
    }

    # Per-year all-slice and penny-slice counts (for proportional phantom alloc).
    by_year_all = defaultdict(lambda: [0, 0])
    by_year_penny = defaultdict(lambda: [0, 0])
    for e in ev:
        by_year_all[e["year"]][0] += 1
        if e["hit"]:
            by_year_all[e["year"]][1] += 1
    for e in penny:
        by_year_penny[e["year"]][0] += 1
        if e["hit"]:
            by_year_penny[e["year"]][1] += 1
    pond["by_year_all"] = dict(by_year_all)
    pond["by_year_penny"] = dict(by_year_penny)
    return pond


# --------------------------------------------------------------------------
# Step 3: break-even phantom arithmetic
# --------------------------------------------------------------------------


def breakeven_phantoms(hits: int, n: int, target_rate: float) -> float:
    """Phantom MISSES x to drag hits/(n+x) down to target_rate.

    hits/(n+x) = target  ->  x = hits/target - n.
    """
    if target_rate <= 0:
        return float("inf")
    return hits / target_rate - n


def slice_rate_with_phantoms(hits: int, n: int, phantoms: float) -> float:
    return hits / (n + phantoms) if (n + phantoms) > 0 else 0.0


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> dict:
    t0 = time.monotonic()  # DI-10: use monotonic clock; wall clock can go backward
    print("== F335 Phase A: survivorship bound ==")
    print("[1/3] Measuring EDGAR delisting/dereg counts per cohort year ...")
    delist = measure_delistings()

    print("[2/3] Loading pond slices ...")
    pond = load_pond()

    # ---- Slice definitions ----------------------------------------------
    # Brief-authoritative penny CONFIRM slice (out-of-time confirm cohorts
    # 2021-2023): 89 / 128, confirm base 0.380. Pull observed confirm-cohort
    # penny numbers from data and reconcile.
    confirm_years = [2021, 2022, 2023]
    penny_confirm = [
        e for e in pond["penny_all"]["events"] if e["year"] in confirm_years
    ]
    pc_n = len(penny_confirm)
    pc_h = sum(1 for e in penny_confirm if e["hit"])
    print(f"      penny confirm (2021-23) observed: hits={pc_h} n={pc_n} "
          f"rate={pc_h/pc_n:.3f}" if pc_n else "      penny confirm empty")

    # Brief states n=128, hits=89, base=0.380. Use brief figures as the
    # authoritative effect-under-test; report observed alongside for honesty.
    PENNY_HITS, PENNY_N = 89, 128
    PENNY_RATE = PENNY_HITS / PENNY_N
    CONFIRM_BASE = 0.380

    # Pond-wide observed base rate.
    pw = pond["all"]
    pw_rate = pw["hits"] / pw["n"]

    # ---- Break-even -----------------------------------------------------
    be_penny = breakeven_phantoms(PENNY_HITS, PENNY_N, CONFIRM_BASE)
    print("[3/3] Break-even & scenario arithmetic ...")
    print(f"      penny break-even phantom misses x: 89/(128+x)=0.380 -> "
          f"x={be_penny:.1f}")

    # ---- Scenario: how many distress sub-$2 phantoms does EDGAR support? --
    # Cohort-years that feed the confirm horizon. A 2021-2023 entry with a
    # 12-month horizon resolves across 2021-2024; delistings in entry-year are
    # the conservative measurable proxy. We sum EDGAR delistings over confirm
    # cohort entry-years 2021-2023.
    confirm_delist_total = sum(
        delist[y]["delist"] + delist[y]["dereg"] for y in confirm_years
    )
    confirm_delist_25 = sum(delist[y]["delist"] for y in confirm_years)

    # The pond's selectable universe is tiny relative to ALL EDGAR delistings:
    # only a fraction of delisted names would have passed the turnaround
    # screen AND been sub-$2 AND been in-horizon. We bound the *upper* phantom
    # supply two ways:
    #   (i) distress-only: scenario_frac * total delistings (universe-wide).
    #   (ii) pond-scaled: scale by the share of EDGAR-listed names the pond
    #        actually screens, then by the penny share, to get a realistic
    #        (not just upper) phantom count.
    # Penny share of pond selections (how selective the penny slice is):
    penny_share = pond["penny_all"]["n"] / pond["all"]["n"]

    # Pond coverage: distinct tickers the pond screened vs ~ universe size.
    # PY-06: derive unique_tickers from data; never silently fall back to a magic number.
    if "unique_tickers" in pond["raw"]:
        pond_unique = pond["raw"]["unique_tickers"]
        unique_tickers_source = "validation_result.json:unique_tickers"
    else:
        pond_unique = len({e["ticker"] for e in pond["events"]})
        print(f"  WARNING: unique_tickers absent from validation_result.json; "
              f"computed {pond_unique} from pond events")
        unique_tickers_source = "computed_from_pond_events"
    POND_COVERAGE = pond_unique / 8000.0  # crude denominator, stated as assumption

    scenarios_out = {}
    for name, frac in SCENARIOS.items():
        distress_delist = confirm_delist_total * frac
        # Upper bound (i): every distress delisting is a phantom miss in penny
        #   slice — wildly generous, ignores penny/screen selectivity.
        phantom_upper = distress_delist
        # Realistic (ii): distress delistings that the pond WOULD have
        #   screened (coverage) AND that are penny-equivalent (penny_share).
        phantom_realistic = distress_delist * POND_COVERAGE * penny_share
        # Resulting penny confirm rate at each phantom level.
        rate_upper = slice_rate_with_phantoms(PENNY_HITS, PENNY_N, phantom_upper)
        rate_real = slice_rate_with_phantoms(PENNY_HITS, PENNY_N, phantom_realistic)
        # Pond-wide rate if phantoms added proportional to delisting intensity
        # (here: add distress delistings to the whole pond, coverage-scaled).
        pond_phantom = distress_delist * POND_COVERAGE
        pw_rate_adj = slice_rate_with_phantoms(pw["hits"], pw["n"], pond_phantom)
        scenarios_out[name] = {
            "frac": frac,
            "distress_delist": distress_delist,
            "phantom_upper": phantom_upper,
            "phantom_realistic": phantom_realistic,
            "penny_rate_upper": rate_upper,
            "penny_rate_realistic": rate_real,
            "pond_phantom": pond_phantom,
            "pond_rate_adj": pw_rate_adj,
        }
        print(f"      {name:4s} (frac={frac}): distress_delist={distress_delist:.0f} "
              f"phantom_real={phantom_realistic:.1f} penny_rate_real={rate_real:.3f} "
              f"penny_rate_upper={rate_upper:.3f}")

    result = {
        "delist": delist,
        "pond": {
            "all": pond["all"],
            "penny_all": {k: pond["penny_all"][k] for k in ("n", "hits")},
            "penny_confirm_observed": {"n": pc_n, "hits": pc_h},
            "by_year_all": pond["by_year_all"],
            "by_year_penny": pond["by_year_penny"],
            "unique_tickers": pond_unique,
            "unique_tickers_source": unique_tickers_source,  # PY-06: audit trail
            "pond_coverage_assumption": POND_COVERAGE,
            "penny_share": penny_share,
        },
        "effect_under_test": {
            "penny_hits": PENNY_HITS,
            "penny_n": PENNY_N,
            "penny_rate": PENNY_RATE,
            "confirm_base": CONFIRM_BASE,
            "delta_pt": (PENNY_RATE - CONFIRM_BASE) * 100,
        },
        "breakeven_penny_phantoms": be_penny,
        "confirm_cohort_delist_total_25_and_15": confirm_delist_total,
        "confirm_cohort_delist_25only": confirm_delist_25,
        "scenarios": scenarios_out,
        "pond_wide_observed_rate": pw_rate,
        "elapsed_secs": time.monotonic() - t0,
    }

    # DI-02: atomic tmp+rename — never truncate the live file mid-write
    out_path = CACHE_DIR / "bound_result.json"
    tmp_out = out_path.with_suffix(".json.tmp")
    tmp_out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    os.replace(str(tmp_out), str(out_path))
    print(f"\nWrote {out_path}")
    print(f"elapsed {result['elapsed_secs']:.0f}s")
    return result


if __name__ == "__main__":
    main()
