# Phase 0 — Free-Data Foundation (Desk Discovery Mode prerequisite)

**Status:** APPROVED by John 2026-06-09 (brainstorm → build, autonomous orchestrator run).
**Parent:** F399. **Components:** F400–F403. **Downstream:** F404 (Phase 1 discovery scan — separate spec, gated on this).

## Why this exists

The Desk is getting a second mode (Phase 1, F404): instead of John writing a premise, a **discovery scan** mines data for data points that *lead the forward return* and auto-mints the survivors as premise cards into the existing F397 flow — a premise *generator* feeding the premise *tester*. That scan is only as good as the data panel it can sweep. Today the research universe is the ~4,700-ticker **liquid SEC-filer** set (UNIVERSE_V2: SIC-bearing + `min_price ≥ $5` + `min_avg_volume ≥ 500k` shares) — a carve inherited from the insider/turnaround studies that **structurally excludes exactly where F369 found the strongest effects** (small-caps, Q1<$1.5B → +7.6pp). Phase 0 widens the data foundation so Phase 1 can look where signal actually lives.

Phase 0 is **free-data only**. Sharadar (the only survivorship-free price source, ~$50/mo) was **rejected 2026-06-09**: its license requires deleting all data *and anything derived from it* within 30 days of cancellation — incompatible with a research program whose entire value is reproducible, audit-trailed, sealed-window findings (a confirmed premise would be contraband). Survivorship-free prices remain a deferred, ongoing-cost option, not a Phase 0 blocker.

## Standing constraints (ALL components)

1. **Survivorship is permanent and stamped.** Free sources (yfinance) serve only *currently-listed* tickers — every panel is survivors-only. Widening adds live small-caps/ETFs, NOT dead companies; the two are orthogonal. Every artifact's metadata sidecar carries `survivorship: "survivors-only"`, and Phase 1 stamps it on every discovered premise. An F338 anchor in each price-bearing component *proves the caveat is real* (a known-delisted name is absent).
2. **F338 gate — no output is believed until smoke-probed on real data against PRE-STATED anchors.** Each component below lists its anchors (known-window probes, sane distributions, populated fields). Green synthetic tests are NOT sufficient (F338). Reading the artifact before interpreting it is part of the gate.
3. **Point-in-time integrity.** Every record carries the date the info was *knowable* (publication / acceptance / dissemination date), not just the period it describes. Discovery's lead-lag math dies on look-ahead. (Same discipline as Form-4 acceptanceDateTime.)
4. **Scan-ready output.** Each source emits a tidy, date-indexed, ticker-keyed **parquet panel** joinable to the returns matrix on `(ticker, date)`, plus a JSON metadata sidecar (`source, fetch_vintage, survivorship, coverage_start, coverage_end, pit_field, n_rows, n_tickers`).
5. **Determinism where the source permits** (F357). FINRA biweekly files and GDELT history are append-only → re-ingest must be byte-identical. yfinance retroactively re-adjusts prices/ratings → record a `fetch_vintage` and treat vintage drift as expected (document per-component).
6. **Concurrency rule (Key Bugs Fixed):** price fetches use `yf.Ticker(symbol).history()` via the existing `_fetch`/`PriceFrameCache` path — NEVER `yf.download()` (shared-global-state corruption).
7. **Module shape:** follow `backend/research/form4_ingest.py` — standalone, no FastAPI imports, runnable with `python3`, a `build_*`/`fetch_*` entry point, a `probe_*` smoke script. Build + probe LOCAL on a small real sample; dispatch the full-scale fetch to the worker.

## Component F400 — Widen the price universe

**What:** Build the master currently-listed US-equity universe from the free **NASDAQ Trader symbol directory** (`nasdaqtraded.txt` — NASDAQ + NYSE + AMEX listed common stock + ETFs), then fetch daily OHLCV 2015→2024 into the price cache for all of them via the existing `PriceFrameCache` path.

**Membership becomes a knob, not a gate.** Drop the SEC-filer (SIC) requirement and the `$5 / 500k` floor as *membership* filters. Preserve them as per-date **labels** (`is_sec_filer`, `passes_universe_v2_floor` computed via `universe_floors.py`) so Phase 1 can filter on liquidity/filer-status as scan knobs rather than inheriting the carve.

**Output:** extended price cache + a `universe_manifest.parquet` (`ticker, name, exchange, is_etf, first_date, last_date, n_bars, is_sec_filer`) + a per-(ticker,date) floor-status sidecar reusing `universe_floors.py`. Metadata sidecar per constraint 4.

**F338 anchors (pre-state, then check on the real probe sample):**
1. **Size band:** `nasdaqtraded.txt` yields >7,000 raw symbols; after dropping test issues and de-duping, the tradable common-stock + ETF count lands in a sane band (~6,000–8,000). State the exact number observed.
2. **Known presence:** AAPL, MSFT, SPY (ETF), and a known small-cap all resolve and fetch non-empty split-adjusted frames.
3. **Widen actually widened:** a name *previously excluded* by UNIVERSE_V2 (currently-listed but sub-$5 or non-SIC) now appears in the manifest. Names the proof case.
4. **Survivorship is real (caveat anchor):** a known 2015–2024 *delisted* name (e.g. a bankruptcy) is **absent** — documents the limit, does not claim to fix it.
5. **Bar sanity:** fetched frames have monotonic unique dates, no all-NaN columns, and a known split is reflected (split-adjusted).

**Scale note:** full fetch is thousands of tickers × 10y daily — hours, yfinance-rate-limited → worker, background, `run.log` progress. Build+probe local on ~30 tickers.

## Component F401 — Analyst up/downgrades (yfinance rating actions)

**What:** Per-ticker fetch of yfinance `.upgrades_downgrades` → tidy event panel `(ticker, date, firm, action, from_grade, to_grade, grade_delta)` where `action ∈ {up, down, init, reiterate, unknown}` and `grade_delta` is a signed numeric mapping of the grade change. PIT field = action `date`.

**Output:** event-level parquet + a per-(ticker,date) aggregation panel (`net_upgrades_21d`, `net_upgrades_63d`, last-action recency) joinable to the matrix. Metadata records `fetch_vintage` (ratings can shift).

**F338 anchors:**
1. A heavily-covered name (AAPL) returns many actions spanning years; firm names are plausible (major banks present).
2. `action` maps cleanly — `unknown` bucket < 5% of rows; if higher, the grade-normalization map is incomplete (blocker).
3. **Known-window probe:** one pre-identified real upgrade/downgrade (named in the probe) lands on the correct date with correct direction.
4. Coverage: fraction of the (widened) universe with ≥1 action is in a sane band; state it.
5. PIT sanity: `max(date) ≤ today`; no future-dated actions.

## Component F402 — News volume + tone (GDELT)

**What:** Fetch GDELT (DOC 2.0 / GKG) per-company **daily news volume + average tone**, timestamped. Resolve ticker → company entity (document the mapping and its risk). Rate-limited → backoff + on-disk cache.

**Output:** daily panel `(ticker, date, news_volume, avg_tone)` aligned to trading dates. PIT field = publication `date`. **Honesty note in metadata:** this is aggregate volume/tone, NOT article content; entity-mapping false matches are the dominant risk.

**F338 anchors:**
1. **Known-window probe:** a pre-identified news-spike day for a specific ticker (clean M&A or crash event) shows a clear volume spike on the right date.
2. Tone sign sanity: a known bad-news day has negative `avg_tone`; a known good-news day positive.
3. Volume non-degenerate (not all-zero/constant); large-caps carry higher average volume than small-caps.
4. **Entity-mapping precision:** spot-check N tickers → correct company; false-match rate below a stated threshold (this anchor guards the #1 risk).
5. PIT sanity: timestamps are publication dates, none future-dated.

## Component F403 — Short interest (FINRA)

**What:** Download FINRA biweekly short-interest files (free, ~2018→present), parse to `(ticker, settlement_date, dissemination_date, short_interest_shares, avg_daily_volume, days_to_cover)`. **PIT field = dissemination_date** (data is published ~8–10 business days after settlement; discovery must key off when it was *knowable*, not the settlement date).

**Output:** biweekly event panel + a daily forward-filled alignment carrying a `staleness_days` flag. Metadata: `coverage_start ≈ 2018` (shorter panel than the others — state it).

**F338 anchors:**
1. **Known-window probe:** GME short interest ≈ 61.8M shares on 2021-01-15 reproduces (the data audit's own anchor).
2. Coverage begins ~2018; pre-2018 absent (documents the short panel).
3. `days_to_cover = short_interest / avg_daily_volume` is positive with a plausible distribution.
4. Dissemination lag: `dissemination_date − settlement_date` ≈ 8–10 business days, consistent.
5. Ticker coverage band sane; state it.

## Output integration (Phase 1 handoff)

All four panels live under `backend/data/<source>/` sharing the `(ticker, date)` join key + a metadata sidecar (constraint 4). A thin `backend/research/feature_panel.py` loader (stubbed in Phase 0, owned by Phase 1) documents the canonical join onto the returns matrix and the survivorship/PIT stamps that ride along. Phase 0 ships the stub + schema doc only; the scan consumes it in F404.

## Worker / run plan

- **Build + F338-probe each component LOCAL** on a small real sample (the F338 gate is the orchestrator's inline judgment call — real data + novel assertions = inline carve-out).
- **Full-scale fetches → worker** (`bin/worker-dispatch.sh <outdir> <logname> <script…>`, target mfcore01 per the 2026-06-09 probe), background, progress to `<outdir>/run.log`. Determinism-verify per F357 where the source permits.
- The widened cache produced here also advances **F385** (stage worker data) — the fetch *creates* the broad universe cache on the worker.

## Out of scope (→ F404, Phase 1)

The discovery scan itself: lead-lag (X,k) candidate enumeration, composite screen score (linear IC + rank IC + hit-rate + sub-period stability, equal-weight, weights pre-stated), top-survivor backtest, **deflated-Sharpe attempt tax** (Bailey–López de Prado, counts candidates not metrics), **two-window** discovery/confirm split on the existing WFA/OOS machine, auto-mint into F397 premise cards, **liquidity-as-a-knob**. See the Phase 1 companion note.
