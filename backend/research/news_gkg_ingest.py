"""GDELT GKG bulk news ingest (F405) — full-universe news volume + tone via BigQuery.

Replaces the F402 per-ticker DOC API path for full-panel production. The per-ticker
API is empirically non-viable at universe scale (~38 tickers/hour under throttling,
~44h for the liquid universe alone — killed 2026-06-09). This module inverts the
access pattern: instead of asking GDELT about each ticker, it scans GDELT's public
BigQuery copy of the GKG (Global Knowledge Graph — every article processed since
2015-02-19, with NER-extracted organizations and document tone) ONCE, matching all
universe tickers in a single pass.

Cost model (validated 2026-06-09)
---------------------------------
The GKG table is 21.5 TB / 1.8B rows, but billing is per column scanned and the
table is day-partitioned. Scanning only (DATE, V2Organizations, V2Tone) over the
full 2015→2026 history processes ~315 GB — under a third of BigQuery's 1 TB/month
free tier (sandbox: no billing account needed). Dry-runs are free and report exact
bytes; build_panel() prints the dry-run total before executing anything.

Entity matching (THE dominant risk — same as F402)
--------------------------------------------------
V2Organizations entries are "Name,charOffset;Name,charOffset;…". Names are
normalized (lowercase, &/- → space, strip [.,'’], collapse whitespace, strip
leading "the ") and exact-matched against a per-ticker alias table built from
the F400 universe manifest:
  - "full" alias: cleaned company name ("apple inc")
  - "core" aliases: every progressive corporate-suffix-strip stage ("goldman
    sachs group" AND "goldman sachs"), ≥4 chars, plus "'s"-dropped variants
    ("mcdonald")
  - "alt" aliases: tiny curated colloquial/pre-rename list (_ALT_ALIASES:
    facebook→META, google→GOOGL, disney→DIS) — separately excludable
Aliases shared by >1 ticker are dropped at query time.

Empirical corpus behavior (pilot, March 2023):
  - GDELT NER never emits bare "Apple"/"Tesla" as orgs (common-word ambiguity);
    those names only match via the "full" channel ("apple inc": ~97 articles/day).
  - Coined names ("netflix": ~3,078/day, "gamestop") appear bare → "core" channel.
  - A small set of core aliases is pathological: "nasdaq" (~2,524/day) and "visa"
    (~1,384/day) mostly tag index references / immigration news, NOT the company.
Consequences, stamped in metadata:
  1. Capture rate is name-shape dependent → news_volume is only meaningful
     WITHIN-ticker over time (spike vs own baseline), never across tickers.
  2. The panel keeps alias_type channels separate ("full" / "core" / "any" =
     article-deduped union) so downstream work can trade precision vs recall;
     pathological cores stay quarantined in the "core" channel.

Output schema
-------------
parquet columns: ticker (str), date (date), alias_type ("full"|"core"|"alt"|"any"),
                 n_articles (int64), avg_tone (float64)
  - n_articles: COUNT(DISTINCT article) matching the ticker's alias(es) that day
  - avg_tone: mean document tone (V2Tone field 1) over matching rows; in the
    "any" channel an article matched by both aliases weighs twice (negligible,
    documented for honesty)
  - date: article publication date (GKG partition date, UTC) — PIT-safe

Sandbox note: dataset tables expire after 60 days (sandbox default). The parquet
+ sidecar on local disk are the durable artifacts; re-production costs one more
~315 GB pass against a fresh month's quota.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from research.news_ingest import _clean_name_for_gdelt  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROJECT = "strategylab-research"
_DATASET = "gdelt"
_ALIAS_TABLE = f"{_PROJECT}.{_DATASET}.ticker_aliases"
_GKG_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_GKG_START = date(2015, 2, 19)  # GKG 2.0 history start
_DEFAULT_OUTPUT_DIR = _BACKEND_DIR / "data" / "gdelt"
_DEFAULT_MANIFEST = _BACKEND_DIR.parent / ".run" / "F-BATCH-0609" / "universe_manifest.parquet"
_GCLOUD_BIN_DIR = "/opt/homebrew/share/google-cloud-sdk/bin"
_MAX_DOWNLOAD_ROWS = 5_000_000  # per-year safety ceiling; observed ~0.4M/yr

# Corporate suffix tokens stripped (right-to-left) to form the "core" alias.
_SUFFIX_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd",
    "limited", "plc", "holdings", "holding", "group", "sa", "nv", "ag",
    "lp", "llc",
}

# Curated colloquial / pre-rename aliases (alias_type='alt', F407). GDELT
# empirics 2024-03-12: "facebook" 9,157/day vs "meta platforms inc" 61;
# "google" 6,195/day vs "alphabet inc" 59; "disney" 4,071/day vs
# "walt disney company" 78. Kept deliberately tiny and company-level (no
# product brands). GOOGL only (not GOOG) — a shared alias would trip the
# ambiguity guard and kill both. Precision-critical work can exclude 'alt'.
_ALT_ALIASES: dict[str, list[str]] = {
    "META": ["facebook"],
    "GOOGL": ["google"],
    "DIS": ["disney"],
    # manifest writes "JP Morgan" (spaced); GDELT fuses it ("jpmorgan chase co"
    # 730/day on 2024-03-12) — the spaced aliases can never match
    "JPM": ["jpmorgan chase co", "jpmorgan chase", "jpmorgan"],
}


# ---------------------------------------------------------------------------
# Alias table
# ---------------------------------------------------------------------------

def _norm_name(s: str) -> str:
    """Normalize a name for exact matching — MUST mirror the SQL-side
    normalization in _panel_sql() exactly (any divergence silently drops all
    matches for affected names). v2 (F407), grounded in GDELT raw-form
    empirics: '&' and '-' become spaces ('procter & gamble' ↔ GDELT
    'procter gamble', 'coca-cola' ↔ 'coca cola'), [.,'’] stripped, whitespace
    collapsed, leading 'the ' stripped ('the boeing company' class).
    Commas never appear in GKG org names (they delimit the char offset)."""
    s = s.lower()
    s = re.sub(r"[&\-]", " ", s)
    s = re.sub(r"[.,'’]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^the ", "", s)
    return s


def _strip_parentheticals(s: str) -> str:
    """Remove trailing parentheticals from NASDAQ directory names.

    'Goldman Sachs Group, Inc. (The)' → 'Goldman Sachs Group, Inc.' — the
    '(The)' form otherwise survives into the alias and kills it (GDELT never
    emits it). Applied before normalization."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", s).strip() or s


def build_alias_frame(manifest_path: Path) -> pd.DataFrame:
    """Build the ticker → alias table from the F400 universe manifest.

    Returns columns: ticker, alias, alias_type ("full"|"core"|"alt"),
    n_tickers_for_alias. Ambiguous aliases (n_tickers_for_alias > 1) are kept
    in the frame but excluded at query time, so the upload is self-documenting.

    v2 (F407): every progressive suffix-strip stage becomes its own "core"
    alias ('goldman sachs group inc' → 'goldman sachs group' → 'goldman
    sachs' — GDELT emits all three forms); names containing "'s" also emit
    the apostrophe-and-s-dropped variant (GDELT 'mcdonald' 1,782/day vs
    'mcdonalds' 292); curated _ALT_ALIASES add colloquial/pre-rename names
    as "alt". COUNT(DISTINCT GKGRECORDID) in the panel SQL dedups articles
    matched by several aliases of the same channel.
    """
    df = pd.read_parquet(manifest_path, columns=["ticker", "name"])
    rows: list[tuple[str, str, str]] = []
    for r in df.itertuples():
        if not r.name or not str(r.name).strip():
            continue
        raw = _strip_parentheticals(_clean_name_for_gdelt(str(r.name)))
        full = _norm_name(raw)
        if not full:
            continue
        t = r.ticker.upper()
        rows.append((t, full, "full"))
        # "'s" names: GDELT drops the possessive entirely ("McDonald's" →
        # "mcdonald"); _norm_name alone yields "mcdonalds". Emit both shapes.
        variants = [full]
        if "'s" in raw.lower() or "’s" in raw.lower():
            dropped = _norm_name(re.sub(r"['’]s\b", "", raw, flags=re.IGNORECASE))
            if dropped and dropped != full:
                rows.append((t, dropped, "core"))
                variants.append(dropped)
        # progressive suffix-strip stages for EVERY base variant, each stage
        # its own core alias ("mcdonald corporation" must also yield "mcdonald")
        for base in variants:
            toks = base.split(" ")
            while len(toks) > 1 and toks[-1] in _SUFFIX_TOKENS:
                toks = toks[:-1]
                stage = " ".join(toks)
                if len(stage) >= 4:
                    rows.append((t, stage, "core"))
    for ticker, aliases in _ALT_ALIASES.items():
        for alias in aliases:
            rows.append((ticker.upper(), _norm_name(alias), "alt"))
    out = pd.DataFrame(rows, columns=["ticker", "alias", "alias_type"]).drop_duplicates()
    # an alias appearing in several channels for the SAME ticker keeps only the
    # most precise channel (full > core > alt) so channels stay disjoint per alias
    rank = {"full": 0, "core": 1, "alt": 2}
    out["_rank"] = out.alias_type.map(rank)
    out = (out.sort_values("_rank").drop_duplicates(["ticker", "alias"], keep="first")
              .drop(columns="_rank"))
    out["n_tickers_for_alias"] = out.groupby("alias")["ticker"].transform("nunique")
    # curated alt aliases must stay unambiguous — a future manifest addition
    # colliding with one would silently kill BOTH at query time (review C7)
    alt_amb = out[(out.alias_type == "alt") & (out.n_tickers_for_alias > 1)]
    if not alt_amb.empty:
        raise RuntimeError(
            f"_ALT_ALIASES collide with manifest names: {alt_amb.alias.tolist()}")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# bq CLI wrapper (no python BigQuery deps — the CLI handles auth/paging)
# ---------------------------------------------------------------------------

def _bq_bin() -> str:
    found = shutil.which("bq")
    if found:
        return found
    candidate = Path(_GCLOUD_BIN_DIR) / "bq"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("bq CLI not found — install google-cloud-sdk (brew install --cask google-cloud-sdk)")


def _bq(*args: str, stdin: str | None = None, timeout: int = 1800) -> str:
    cmd = [_bq_bin(), f"--project_id={_PROJECT}", *args]
    proc = subprocess.run(
        cmd, input=stdin, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        # bq prints some errors (e.g. "already exists") to stdout, not stderr
        err = (proc.stderr.strip() or proc.stdout.strip())[:500]
        raise RuntimeError(f"bq failed ({' '.join(args[:2])}): {err}")
    return proc.stdout


def _bq_query(sql: str, *extra: str, timeout: int = 1800) -> str:
    return _bq("query", "--use_legacy_sql=false", *extra, stdin=sql, timeout=timeout)


def ensure_dataset() -> None:
    try:
        _bq("mk", "--dataset", f"{_PROJECT}:{_DATASET}")
    except RuntimeError as exc:
        # bq wraps messages at 80 cols — normalize whitespace before matching
        if "already exists" not in re.sub(r"\s+", " ", str(exc).lower()):
            raise


def upload_aliases(aliases: pd.DataFrame, work_dir: Path) -> None:
    csv_path = work_dir / "ticker_aliases.csv"
    aliases.to_csv(csv_path, index=False)
    _bq(
        "load", "--replace", "--source_format=CSV", "--skip_leading_rows=1",
        "--schema=ticker:STRING,alias:STRING,alias_type:STRING,n_tickers_for_alias:INTEGER",
        f"{_PROJECT}:{_DATASET}.ticker_aliases", str(csv_path),
    )
    log.info("uploaded %d alias rows to %s", len(aliases), _ALIAS_TABLE)


# ---------------------------------------------------------------------------
# Panel SQL
# ---------------------------------------------------------------------------

def _panel_sql(start: date, end: date, dest_table: str) -> str:
    """One year-chunk query: scan GKG partitions [start, end), join aliases,
    aggregate per (ticker, day, alias_type) plus an article-deduped "any" group
    via GROUPING SETS — one scan, three channels."""
    return f"""
CREATE OR REPLACE TABLE `{_PROJECT}.{_DATASET}.{dest_table}` AS
WITH arts AS (
  SELECT
    GKGRECORDID,
    CAST(_PARTITIONTIME AS DATE) AS day,
    CAST(SPLIT(V2Tone, ',')[OFFSET(0)] AS FLOAT64) AS tone,
    ARRAY(
      SELECT DISTINCT
        -- normalization chain MUST mirror _norm_name() exactly (v2/F407):
        -- lower → [&-]→space → strip [.,'’] (comma per review C1/DI-1; GDELT
        -- encoding makes embedded commas impossible, but the normalizers must
        -- stay structurally identical) → collapse whitespace → strip leading 'the '
        REGEXP_REPLACE(
          TRIM(REGEXP_REPLACE(
            REGEXP_REPLACE(
              REGEXP_REPLACE(LOWER(REGEXP_EXTRACT(p, r'^(.*),[0-9]+$')), r'[&\\-]', ' '),
              r"[.,'’]", ''),
            r'\\s+', ' ')),
          r'^the ', '')
      FROM UNNEST(SPLIT(V2Organizations, ';')) AS p
      WHERE REGEXP_EXTRACT(p, r'^(.*),[0-9]+$') IS NOT NULL
    ) AS orgs
  FROM `{_GKG_TABLE}`
  WHERE _PARTITIONTIME >= TIMESTAMP('{start.isoformat()}')
    AND _PARTITIONTIME < TIMESTAMP('{end.isoformat()}')
    AND V2Organizations != ''
),
matched AS (
  SELECT m.ticker, a.day, m.alias_type, a.GKGRECORDID, a.tone
  FROM arts a, UNNEST(a.orgs) AS org
  JOIN `{_ALIAS_TABLE}` m ON org = m.alias
  WHERE m.n_tickers_for_alias = 1
)
-- IFNULL must live in an OUTER query: BigQuery resolves GROUP BY names to
-- SELECT aliases first, so `IFNULL(alias_type,'any') AS alias_type` inside the
-- grouped SELECT makes GROUPING SETS group by the IFNULL expression and then
-- NULL it for the (ticker, day) set — the 'any' label silently never appears.
-- (Found by the F338 probe on the first full panel, 2026-06-09.)
SELECT ticker, day, IFNULL(alias_type, 'any') AS alias_type, n_articles, avg_tone
FROM (
  SELECT
    ticker,
    day,
    alias_type,
    COUNT(DISTINCT GKGRECORDID) AS n_articles,
    AVG(tone) AS avg_tone
  FROM matched
  GROUP BY GROUPING SETS ((ticker, day, alias_type), (ticker, day))
)
"""


def _year_chunks(start: date, end: date) -> list[tuple[date, date, str]]:
    """Calendar-year chunks [chunk_start, chunk_end) clamped to [start, end]."""
    chunks = []
    for year in range(start.year, end.year + 1):
        c_start = max(start, date(year, 1, 1))
        c_end = min(end, date(year + 1, 1, 1))
        if c_start < c_end:
            chunks.append((c_start, c_end, f"panel_{year}"))
    return chunks


def dry_run_total_gb(chunks: list[tuple[date, date, str]]) -> float:
    """Free dry-run over all chunks; returns total GB that would be processed."""
    total = 0
    for c_start, c_end, dest in chunks:
        out = _bq_query(_panel_sql(c_start, c_end, dest), "--dry_run")
        m = re.search(r"([0-9]+) bytes", out)
        if not m:
            raise RuntimeError(f"dry-run output unparseable for {dest}: {out[:200]}")
        total += int(m.group(1))
    return total / 1e9


def download_table(table: str) -> pd.DataFrame:
    csv_text = _bq_query(
        f"SELECT * FROM `{_PROJECT}.{_DATASET}.{table}`",
        "--format=csv", f"--max_rows={_MAX_DOWNLOAD_ROWS}",
    )
    # keep_default_na=False: pandas' default NA tokens include 'NA' — a ticker
    # symbol shape (review C3). Only truly empty cells become NaN.
    df = pd.read_csv(io.StringIO(csv_text), keep_default_na=False, na_values=[""],
                     dtype={"ticker": str, "alias_type": str})
    if len(df) >= _MAX_DOWNLOAD_ROWS:
        raise RuntimeError(f"{table} hit the {_MAX_DOWNLOAD_ROWS}-row download ceiling — rows lost")
    return df


# ---------------------------------------------------------------------------
# Panel build
# ---------------------------------------------------------------------------

def build_panel(
    output_dir: Path,
    manifest_path: Path,
    start: date,
    end: date,
    dry_run_only: bool = False,
) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(output_dir / "run.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)

    chunks = _year_chunks(start, end)
    log.info("F405 GKG panel build: %s → %s, %d year-chunks", start, end, len(chunks))

    ensure_dataset()
    aliases = build_alias_frame(manifest_path)
    log.info("alias table: %d rows, %d tickers, %d ambiguous (excluded at query time)",
             len(aliases), aliases.ticker.nunique(), int((aliases.n_tickers_for_alias > 1).sum()))
    upload_aliases(aliases, output_dir)

    total_gb = dry_run_total_gb(chunks)
    log.info("dry-run total: %.1f GB to process (free tier: 1000 GB/month)", total_gb)
    if dry_run_only:
        return None

    frames: list[pd.DataFrame] = []
    for i, (c_start, c_end, dest) in enumerate(chunks, 1):
        log.info("[%d/%d] building %s (%s → %s)…", i, len(chunks), dest, c_start, c_end)
        _bq_query(_panel_sql(c_start, c_end, dest))
        df = download_table(dest)
        log.info("[%d/%d] %s: %d rows, %d tickers", i, len(chunks), dest, len(df),
                 df.ticker.nunique() if len(df) else 0)
        frames.append(df)

    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel.pop("day")).dt.date
    panel = panel[["ticker", "date", "alias_type", "n_articles", "avg_tone"]]
    panel = panel.sort_values(["ticker", "date", "alias_type"]).reset_index(drop=True)

    out_path = output_dir / "news_panel_gkg.parquet"
    panel.to_parquet(out_path, index=False)

    days_present = set(panel.date.unique())
    d0, d1 = min(days_present), max(days_present)
    gap_days = sorted(
        d for d in (d0 + timedelta(days=i) for i in range((d1 - d0).days + 1))
        if d not in days_present
    )

    meta = {
        "source": "gdelt_gkg_bulk_bigquery (gdelt-bq.gdeltv2.gkg_partitioned)",
        "item": "F405",
        "fetch_vintage": datetime.now(timezone.utc).isoformat(),
        "survivorship": (
            "current-listing snapshot (F400 universe manifest) — delisted names "
            "(e.g. SIVB, FRC) are ABSENT from the panel for all history"
        ),
        "coverage_start": str(panel.date.min()),
        "coverage_end": str(panel.date.max()),
        "pit_field": "publication date (GKG partition date, UTC)",
        "n_rows": int(len(panel)),
        "n_tickers": int(panel.ticker.nunique()),
        "mapping_method": (
            "exact normalized org-name match against universe-manifest aliases "
            "(v2/F407: &/- → space, leading-'The' and trailing-parenthetical "
            "stripped, progressive suffix stages, 's-variants); channels: full "
            "(cleaned name), core (suffix-strip stages), alt (curated "
            "colloquial/pre-rename: facebook/google/disney), any "
            "(article-deduped union); aliases shared by >1 ticker dropped"
        ),
        "honesty_note": (
            "Capture rate is name-shape dependent (GDELT NER never emits bare "
            "'Apple'/'Tesla'; coined names like 'netflix' appear bare) — volume is "
            "only meaningful WITHIN-ticker vs its own baseline, never across "
            "tickers. Known pathological core aliases match non-company usage: "
            "'nasdaq' (index references), 'visa' (immigration news) — use the "
            "'full' channel for precision-critical work. 'any'-channel avg_tone "
            "weighs an article once per matching alias (negligible double-count). "
            "Known capture GAPS (probe-discovered 2026-06-09): (1) leading-'The' "
            "names produce dead core aliases ('The Boeing Company' → 'the boeing') "
            "— BA has ZERO rows; (2) aliases are CURRENT names applied to all "
            "history, so renamed companies' pre-rename coverage is missed (META "
            "alias never matches 2018 'facebook' orgs); (3) saturated-coverage "
            "names (NFLX) barely move on investor shocks — volume spike ≠ "
            "investor-news spike for entertainment/consumer megabrands."
        ),
        "bytes_processed_gb": round(total_gb, 1),
        "coverage_gap_days": [str(d) for d in gap_days],
        "coverage_gap_note": (
            "calendar days inside [coverage_start, coverage_end] with zero panel "
            "rows. Known gaps were verified against raw GKG partitions as "
            "upstream corpus outages (probe _VERIFIED_CORPUS_GAPS); any NEW gap "
            "fails probe anchor A5 until verified the same way"
        ),
    }
    meta_path = output_dir / "news_panel_gkg.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("panel written: %s (%d rows, %d tickers) + %s",
             out_path, len(panel), panel.ticker.nunique(), meta_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="F405 GDELT GKG bulk news panel build")
    parser.add_argument("--start", default=str(_GKG_START), help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=str(date.today()), help="End date YYYY-MM-DD (exclusive)")
    parser.add_argument("--manifest", default=str(_DEFAULT_MANIFEST))
    parser.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR))
    parser.add_argument("--dry-run-only", action="store_true",
                        help="Upload aliases + print dry-run GB total, build nothing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build_panel(
        output_dir=Path(args.output_dir),
        manifest_path=Path(args.manifest),
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        dry_run_only=args.dry_run_only,
    )


if __name__ == "__main__":
    main()
