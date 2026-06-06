"""F353 — Fetch SEC Insider Transactions Data Sets (bulk quarterly ZIPs).

Replaces the naive ~1M-XML EDGAR fetch (30-55h rate-capped) with the SEC's
pre-parsed quarterly bundles: SUBMISSION / REPORTINGOWNER / NONDERIV_TRANS /
FOOTNOTES tables per quarter, every Form 3/4/5 since 2009.
Data dictionary: https://www.sec.gov/files/insider_transactions_readme.pdf

Scope: 2015q1..2026q1 (45 ZIPs, ~2-4 GB). Filing-event data only — fetching
2025+ FILINGS does not touch the sealed 2025+ PRICE confirm window (the hard
guard protects outcomes, not events).

Progress: logs to stdout AND <out_dir>/fetch.log (John's rule: long-running
tasks must be followable — `tail -f .../form4_datasets/fetch.log`).
Resumable: existing ZIPs that pass a zip-integrity check are skipped.

Usage:
    backend/venv/bin/python backend/research/fetch_form4_datasets.py
"""
from __future__ import annotations

import logging
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "form4_datasets"

_URL = (
    "https://www.sec.gov/files/structureddata/data/"
    "insider-transactions-data-sets/{q}_form345.zip"
)
# SEC fair-use: declared UA, modest pacing (these are static files, not the
# EDGAR API, but stay polite).
_UA = "StrategyLab research john@milford.se"
_PACE_SECONDS = 1.0
_QUARTERS = [f"{y}q{q}" for y in range(2015, 2026) for q in range(1, 5)] + ["2026q1"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _zip_ok(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except Exception:
        return False


def run() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(OUT_DIR / "fetch.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(fh)

    done = skipped = failed = 0
    total_bytes = 0
    t0 = time.monotonic()
    for i, q in enumerate(_QUARTERS, 1):
        dest = OUT_DIR / f"{q}_form345.zip"
        if dest.exists() and _zip_ok(dest):
            skipped += 1
            log.info("[%2d/%d] %s — already on disk, zip OK, skipped", i, len(_QUARTERS), q)
            continue
        url = _URL.format(q=q)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            tmp = dest.with_suffix(".zip.part")
            tmp.write_bytes(data)
            if not _zip_ok(tmp):
                tmp.unlink(missing_ok=True)
                failed += 1
                log.error("[%2d/%d] %s — downloaded but failed zip integrity, discarded", i, len(_QUARTERS), q)
                continue
            tmp.rename(dest)
            done += 1
            total_bytes += len(data)
            log.info(
                "[%2d/%d] %s — %.1f MB (cum %.1f MB, %.0fs elapsed)",
                i, len(_QUARTERS), q, len(data) / 1e6, total_bytes / 1e6,
                time.monotonic() - t0,
            )
        except Exception as exc:
            failed += 1
            log.error("[%2d/%d] %s — FAILED: %s", i, len(_QUARTERS), q, exc)
        time.sleep(_PACE_SECONDS)

    log.info(
        "DONE: %d fetched (%.1f MB), %d skipped, %d failed, %.0fs total",
        done, total_bytes / 1e6, skipped, failed, time.monotonic() - t0,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
