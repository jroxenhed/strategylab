"""Phase 0 full-scale ingest driver (F400-F403).

One ``--source`` per dispatch so jobs run independently (universe price fetch is
the multi-hour long pole; short_interest is minutes). Thin wrapper over the
already-reviewed build_* functions in the ingest modules — adds uniform
followable logging (stdout + --log-file, per "always have a way to follow
progress"). Intended to run on the worker via bin/worker-dispatch.sh.

Examples (worker):
  python3 backend/research/run_phase0_ingest.py --source short_interest \
      --out-root backend/data --log-file backend/data/short_interest/run.log
  python3 backend/research/run_phase0_ingest.py --source universe \
      --out-root backend/data --log-file backend/data/universe/run.log
  python3 backend/research/run_phase0_ingest.py --source ratings \
      --out-root backend/data --log-file backend/data/ratings/run.log
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent      # backend/research
_BACKEND = _HERE.parent                       # backend
sys.path.insert(0, str(_HERE))


def _setup_log(log_file: str | None) -> logging.Logger:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("phase0")


def _manifest_tickers(stocks_only: bool, log: logging.Logger) -> list[str]:
    from universe_ingest import fetch_nasdaq_trader_manifest, build_universe_manifest
    raw = fetch_nasdaq_trader_manifest()
    man = build_universe_manifest(raw) if "is_etf" not in getattr(raw, "columns", []) else raw
    col = "ticker" if "ticker" in man.columns else man.columns[0]
    if stocks_only and "is_etf" in man.columns:
        man = man[man["is_etf"] == False]  # noqa: E712 — ETFs have no analyst ratings
    tickers = sorted(man[col].dropna().astype(str).str.strip().unique().tolist())
    log.info("manifest tickers: %d (stocks_only=%s)", len(tickers), stocks_only)
    return tickers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    choices=["universe", "ratings", "short_interest", "news"])
    ap.add_argument("--out-root", default=str(_BACKEND / "data"))
    ap.add_argument("--log-file")
    a = ap.parse_args()
    log = _setup_log(a.log_file)
    out = Path(a.out_root)
    log.info("=== Phase0 ingest START source=%s out_root=%s ===", a.source, out)

    if a.source == "universe":
        from universe_ingest import run_full_universe_build, _DEFAULT_PRICE_CACHE_DIR
        run_full_universe_build(
            output_dir=out / "universe",
            cache_dir=_DEFAULT_PRICE_CACHE_DIR,
            log_file=a.log_file,
        )

    elif a.source == "short_interest":
        from short_interest_ingest import (
            biweekly_settlement_dates, build_short_interest_panel, _COVERAGE_START,
        )
        end = date(2024, 12, 31)
        sdates = biweekly_settlement_dates(_COVERAGE_START, end)
        log.info("short_interest: %d settlement dates %s .. %s",
                 len(sdates), sdates[0], sdates[-1])
        build_short_interest_panel(
            sdates,
            date_range=(_COVERAGE_START, end),
            out_dir=out / "short_interest",
        )

    elif a.source == "ratings":
        from ratings_ingest import build_ratings_panels
        tickers = _manifest_tickers(stocks_only=True, log=log)  # ETFs have no ratings
        build_ratings_panels(tickers, output_dir=out / "ratings")

    elif a.source == "news":
        import pandas as pd
        from news_ingest import build_news_panel
        # Bounded to the liquid universe (in_liquid_universe_v1) — full 12k
        # coverage needs the GKG-bulk migration (F405). GDELT-sourced, so this
        # runs without contending with the yfinance jobs (universe/ratings).
        man_path = out / "universe" / "universe_manifest.parquet"
        if man_path.exists():
            man = pd.read_parquet(man_path)
            col = "ticker" if "ticker" in man.columns else man.columns[0]
            sub = (man[man["in_liquid_universe_v1"] == True]  # noqa: E712
                   if "in_liquid_universe_v1" in man.columns else man)
            tickers = sorted(sub[col].dropna().astype(str).str.strip().unique().tolist())
        else:
            tickers = _manifest_tickers(stocks_only=True, log=log)
        log.info("news: %d tickers (liquid-universe subset)", len(tickers))
        build_news_panel(tickers, date(2015, 1, 1), date(2024, 12, 31),
                         output_dir=out / "news")

    log.info("=== Phase0 ingest DONE source=%s ===", a.source)


if __name__ == "__main__":
    main()
