# F346 Analyst-Action & News Event Stream Audit
**Date:** 2026-06-06  
**Purpose:** Go/no-go evaluation of free event streams for point-in-time backtesting.  
*"Point-in-time safe" means: the data you'd see on day T is exactly what you'd have seen on day T, not retroactively revised.*

---

## AUDIT 1 — yfinance Analyst Upgrades/Downgrades

**Verdict: CONDITIONAL GO** — rich data, good depth, timestamps are datetime (seconds), determinism probe PASSED. Caveats on timezone and coverage below.

### Access Method
`yf.Ticker(symbol).upgrades_downgrades` — returns a pandas DataFrame, index is `datetime64[s]` (no timezone label).

### Data by Ticker

| Ticker | Rows | Earliest | Latest | Span (yrs) | Future dates | Notes |
|--------|------|----------|--------|------------|--------------|-------|
| AAPL   | 968  | 2012-09-12 11:51 | 2026-06-05 13:15 | ~13.7 | 0 | |
| NVDA   | 986  | 2016-06-02 07:56 | 2026-06-05 15:32 | ~10   | 0 | |
| GPRO   | 132  | 2012-04-30 16:21 | 2024-08-07 14:45 | 12.3  | 0 | No coverage after Aug 2024 (analyst exodus on decline) |
| ENPH   | 436  | 2012-08-08 10:06 | 2026-05-20 10:58 | ~13.8 | 0 | |
| GME    | 106  | 2012-03-21 18:06 | 2025-06-11 12:54 | ~13.2 | 0 | Small-cap; sparse post-meme era |
| BBBY   | 11   | 2025-03-10 19:07 | 2026-04-28 13:56 | ~1.1  | 0 | Delisted/OTC remnant ticker; almost no data |

### Columns
`Firm`, `ToGrade`, `FromGrade`, `Action`, `priceTargetAction`, `currentPriceTarget`, `priorPriceTarget`

**Action codes:**
- `init` — new coverage initiated (no prior grade)
- `up` — upgrade (grade improved)
- `down` — downgrade (grade worsened)
- `main` / `reit` — reiteration (same grade, possibly new price target)

### Anchor Checks
- **(a) Mega-caps >50 actions spanning ≥4 years:** AAPL 968/13.7yr ✓, NVDA 986/10yr ✓ — **PASS**
- **(b) No future-dated actions:** All tickers: 0 future rows — **PASS**
- **(c) Determinism probe (PIT safety):** AAPL fetched twice ~5 min apart; 20/20 oldest rows identical (same firm, grade, action, second-precision timestamp) — **PASS**

### Timestamps
Index is `datetime64[s]`, no timezone label. Empirically, times cluster in UTC 10:00–17:00 (i.e., US Eastern pre-market through mid-session — most actions arrive pre-open or at the open). This means **timestamps are UTC**, not ET. A 10:30 UTC timestamp is ~6:30 AM ET, safely before 9:30 ET open.

**For next-open entry logic:** subtract time from index, check if < 13:30 UTC (9:30 ET); if so, the action is pre-open and the next regular open is same day. If ≥ 13:30 UTC it is intraday/after-hours and the next trade date is day+1 open. The second-precision timestamps support this cleanly.

### PIT Safety Assessment
- **Determinism:** PASS — Yahoo does not appear to rewrite historical upgrade/downgrade records between calls (at least over a 5-minute window; longer-horizon rewriting cannot be ruled out entirely but is unlikely for timestamped third-party actions).
- **Caveat:** Yahoo does not publish a formal immutability guarantee. This data is scraped from Yahoo Finance's analyst page. If a brokerage firm retracts or restates a call, Yahoo may update retroactively without notice. For production signals this is a known risk; for research/backtesting it is acceptable because restatements are rare.
- **Coverage holes:** Very small caps and delisted tickers have sparse or near-zero history (BBBY had only 11 rows with an odd date range suggesting a replacement ticker). Sub-$100M names will be hit-or-miss.

---

## AUDIT 2 — GDELT News Volume

**Verdict: CONDITIONAL GO for volume/spike detection; NO-GO for high-frequency or ticker-specific queries.** History confirmed to 2021; 2017 depth unverifiable due to rate limits.

### Access Method
GDELT 2.0 DOC API: `https://api.gdeltproject.org/api/v2/doc/doc?query=<term>&mode=timelinevol&format=json&startdatetime=YYYYMMDDHHMMSS&enddatetime=YYYYMMDDHHMMSS`

No API key required. Returns JSON with `timeline[0].data[]` — each element has `date` (ISO 8601 UTC string) and `value` (normalized article volume, 0–1 scale).

### GameStop Anchor (Jan 2021 spike vs Dec 2020 baseline)

| Period | Data points | Average value | Peak value | Peak date |
|--------|-------------|---------------|------------|-----------|
| Dec 2020 | 31 | 0.0094 | 0.0273 | — |
| Jan 2021 | 31 | 0.1472 | 1.2940 | 2021-01-29 |
| Feb 2021 | 28 | 0.0870 | ~0.30 | — |

**Jan-peak / Dec-average ratio: 138x** — **ANCHOR PASS** (expected: dramatic spike, got 138x)

The spike buildup is also correctly ordered: Jan 25 = 0.097, Jan 26 = 0.152, Jan 27 = 0.401, Jan 28 = 0.950, Jan 29 = 1.294. Reflects real GME media timeline accurately.

### Rate Limits
**Significant.** Multiple requests within ~30 seconds receive HTTP 429. Safe cadence appears to be 1 request per 30–60 seconds. The 2017 history depth test failed due to rate limiting — history depth cannot be independently confirmed in this session. GDELT's own documentation states 2017+ for the DOC API. Based on the successful 2021 query, DOC API is functionally accessible.

### Timestamp Granularity
The DOC API returns **daily** aggregated data points (one value per calendar day). The date format is `YYYYMMDDTHHMMSSZ` but always resolves to midnight UTC — i.e., daily granularity. The API does not provide 15-minute batches in `timelinevol` mode (15-minute mode exists in GKG but has a different endpoint and heavier schema).

### Ticker → Query Mapping Difficulty
**High friction.** GDELT indexes news article text — you must map ticker symbol to company name(s) and handle variants. `"GameStop"` works; `"GME"` would not. Ambiguous names (e.g., `"Apple"`) will include non-financial articles. Query design requires per-company curation. GDELT is better suited to high-publicity events (meme stocks, M&A announcements) than routine coverage.

### PIT Safety
GDELT article counts are computed from a fixed historical corpus — past counts do not change retroactively. **PIT SAFE** for volume. The value scale is normalized across the corpus and may shift as new articles are ingested, but the relative spike shape is stable.

### Rate Limit Caveats for Production Use
Single-machine backtesting is workable with throttling. Parallel cohort scans will hit 429 immediately. Cache all GDELT calls to disk before any analysis loop.

---

## AUDIT 3 — EDGAR 8-K Cache (Already on Disk)

**Verdict: GO** — a high-quality, PIT-safe news event stream is already on disk. Item codes 2.02 (earnings results) and 7.01 (Reg FD / press release) are present and itemized. `acceptanceDateTime` provides second-precision UTC arrival timestamps.

### Coverage
- **564 companies** in cache; **467 have ≥1 8-K filing** with item codes
- **62,656 total 8-K item occurrences** across all companies
- Filing date range: **2006 to 2026** (sample; full universe likely shorter for most companies)
- The `submissions` JSON stores the **1,000 most recent filings per company** from EDGAR. For high-frequency filers this covers ~10–20 years; for low-frequency filers potentially longer.

### Key Item Codes Present
| Code | Count | Meaning |
|------|-------|---------|
| 9.01 | 48,174 | Financial statements and exhibits (always present with substantive items) |
| 2.02 | 19,675 | **Results of Operations (earnings release)** |
| 7.01 | 14,733 | **Regulation FD Disclosure (press release / guidance)** |
| 8.01 | 13,879 | Other events |
| 5.02 | 12,206 | Departure/appointment of directors or officers |
| 1.01 | 8,677 | Entry into material agreement |

### Sample Records (5 companies)

```
Company: AMD | Filing: 2026-05-05 | Accepted: 2026-05-05T20:16:06.000Z | Items: 2.02,7.01,9.01
Company: AAL | Filing: 2026-04-23 | Accepted: 2026-04-23T11:01:15.000Z | Items: 2.02,7.01,9.01
Company: ATRO | Filing: 2026-06-01 | Accepted: 2026-06-01T13:05:02.000Z | Items: 7.01,9.01
Company: GCO  | Filing: 2026-05-29 | Accepted: 2026-05-29T11:07:04.000Z | Items: 2.02,9.01
Company: CRK  | Filing: 2026-05-05 | Accepted: 2026-05-05T20:27:31.000Z | Items: 2.02,9.01
```

### Fields Available
- `accessionNumber` — unique EDGAR filing ID (linkable to full document)
- `filingDate` — calendar date (string, YYYY-MM-DD)
- `reportDate` — period the filing covers
- **`acceptanceDateTime`** — exact UTC timestamp when EDGAR received and accepted the filing (ISO 8601, second precision). **This is the canonical PIT timestamp.**
- `form` — "8-K", "8-K/A", etc.
- `items` — comma-separated item codes
- `primaryDocument` — filename of the main document (allows fetching full text)

### PIT Safety Assessment
**HIGH CONFIDENCE PIT SAFE.** `acceptanceDateTime` is EDGAR's own recorded ingestion timestamp — it cannot be retroactively changed. This is the moment the filing became publicly visible. Using `acceptanceDateTime` as the signal arrival time is the correct PIT-safe approach.

### What's Missing / Caveats
- The `efts/` subdirectory contains 154 files but all are empty `[]` arrays — this cache layer (full-text search results) has not been populated.
- History depth per company is capped at ~1,000 most recent filings. For companies with >1,000 total filings, older history is not on disk. EDGAR's full submission history goes back to 1993 but requires additional download.
- Item codes 2.02 and 7.01 together give earnings releases + Reg FD press releases — sufficient for an earnings-event or news-event signal stream.

---

## AUDIT 4 — Finnhub and FMP Free Tier

**Verdict: NO-GO without paid key. Both require authentication even for historical data.**

### Finnhub
- Endpoint: `/api/v1/stock/upgrade-downgrade` (individual actions) and `/api/v1/stock/recommendation` (consensus trends)
- Free tier: **API key required.** Without key, returns HTTP 401.
- With free key: per Finnhub's published docs, upgrade/downgrade history goes back ~2+ years for most tickers. Depth is not competitive with yfinance (~14 years).
- License: commercial use of Finnhub data requires a paid plan; free tier is for personal/non-commercial use only.
- **Assessment:** Even if a free key is obtained, depth is inferior to yfinance and license prohibits research commercialization.

### Financial Modeling Prep (FMP)
- Endpoint: `/api/v3/upgrades-downgrades?symbol=AAPL`
- Free tier: **API key required.** Demo key returns HTTP 401.
- Per FMP published pricing: upgrade/downgrade history is available on the free tier (250 requests/day) but depth is limited to ~6 months to 1 year for most endpoints. Some sources cite longer history at paid tiers.
- License: FMP free tier allows personal and research use; resale or redistribution prohibited.
- **Assessment:** Shallow depth and daily request caps make it unsuitable as a primary source when yfinance provides 14-year history for free and unauthenticated.

---

## Summary Table

| Source | Verdict | Depth | PIT Safe? | Anchor | Key Caveat |
|--------|---------|-------|-----------|--------|------------|
| yfinance upgrades_downgrades | **CONDITIONAL GO** | 10–14 yrs depending on ticker | YES (determinism PASSED) | PASS | No formal immutability guarantee; coverage sparse for micro-caps |
| GDELT DOC API | **CONDITIONAL GO** | 2017+ (doc'd), 2021 confirmed | YES | PASS 138x spike | Daily granularity only; rate-limited (~1 req/60s); text→ticker mapping is manual |
| EDGAR 8-K on disk | **GO** | 2006–2026 (sample) | YES (highest confidence) | N/A | Coverage capped at ~1,000 most recent filings/company; efts layer empty |
| Finnhub free tier | **NO-GO** | ~2 yrs (doc'd) | Unknown | N/A | Requires key; non-commercial license; depth inferior to yfinance |
| FMP free tier | **NO-GO** | ~6 mo–1 yr (doc'd) | Unknown | N/A | Requires key; 250 req/day cap; depth inferior to yfinance |

---

## Plain-English Summary

*(For a non-expert reader. Every term is defined inline.)*

**What this audit was checking:** Are there free, reliable sources of analyst opinion events (e.g., "Citigroup upgraded Apple from Hold to Buy on March 5") and news events that we can use in a backtest — meaning we can be confident the data reflects what was actually known on a given historical date, not information that only appeared later.

**Source 1 — yfinance analyst actions:**  
yfinance (a free data library that scrapes Yahoo Finance) gives us a table of every upgrade, downgrade, and "reiteration" (when an analyst reaffirms a rating without changing it) for a given stock. For Apple we got 968 records going back to 2012 — over 13 years. For NVIDIA we got 986 records back to 2016. For smaller stocks like GoPro we got 132 records over 12 years, which is enough. The timestamps are accurate to the second (e.g., "2026-06-05 13:15:19") — this matters because an upgrade that arrived at 7:00 AM can be traded at the 9:30 AM market open, while one that arrived at 11:00 AM is already in the market. We ran the same query twice 5 minutes apart and got identical historical records — a key reliability test (if the data changed between pulls, it would mean Yahoo is rewriting history, which would make backtesting impossible). That test passed. **Bottom line: good source, use it.**

**Source 2 — GDELT news volume:**  
GDELT is a free database that counts how many news articles mention a given topic each day. We tested it on GameStop (the famous "meme stock" that exploded in late January 2021): volume went from an average of 0.009 on a normalized scale in December 2020 to a peak of 1.294 on January 29, 2021 — a 138x spike, exactly when you'd expect. The data is free, no login required. Downsides: it only gives you daily totals (not hour-by-hour); you have to map stock tickers to company names manually (searching "GME" would find nothing — you need "GameStop"); and it rate-limits heavily (it started blocking requests after 2–3 calls in a row, so you can't run it in bulk without pauses and caching). **Bottom line: useful for detecting whether a stock was in the news on a given day (event confirmation), but not for high-frequency signals. Use with caching and throttling.**

**Source 3 — EDGAR 8-K filings already on disk:**  
The company already has 467 companies' SEC filing records cached locally. An 8-K is a mandatory disclosure companies file when something important happens — earnings results (item code 2.02), press releases under "Regulation FD" (item code 7.01), management changes, acquisitions, etc. The cache has 19,675 earnings-result events and 14,733 press-release/guidance events. Each record includes the exact second when the SEC received it (`acceptanceDateTime`) — the most reliable possible "public knowledge" timestamp. This is better than any external API because it's the SEC's own timestamp, not a third-party estimate, and it doesn't change retroactively. **Bottom line: this is the best news-event stream available. It's already on disk, requires no new data, and is PIT-safe by construction. Build on this first.**

**Source 4 — Finnhub and FMP:**  
Both financial data services require an API key (a password you register for) even on their free tiers — and both returned errors without one. Even with a free key, their documented history is only 2 years or less for analyst actions, which is far shallower than what yfinance provides for free. License terms also restrict commercial use. **Bottom line: skip both. yfinance gives more for less effort.**

---

*Generated: 2026-06-06 | Artifacts: /Users/jroxenhed/Documents/strategylab/.run/F346/*
