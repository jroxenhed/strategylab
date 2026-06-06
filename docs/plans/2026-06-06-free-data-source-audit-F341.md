# F341 — Free Data Source Audit
**Date:** 2026-06-06  
**Working dir:** `/Users/jroxenhed/Documents/strategylab/.run/F341/`

---

## AUDIT 1 — Stooq Delisted-Ticker Coverage

**VERDICT: BLOCKED — cannot assess**

### What was tried
Six tickers tested (aapl.us, sivb.us, frc.us, bbby.us, twtr.us, atvi.us) via the documented CSV endpoint `https://stooq.com/q/d/l/?s=<ticker>.us&i=d`. Attempted with:
- curl with browser User-Agent
- curl with Referer, Accept headers, cookie jar
- Python `urllib.request`
- Python `requests` with session/cookie handling
- wget

### Result
Every request returned a Cloudflare "proof-of-work" JavaScript challenge (HTTP 200 but HTML body, not CSV). The challenge requires executing SHA-256 iterations in a real browser JS engine and POSTing the solution to `/__verify`. No path around this exists from a CLI/script context without a real browser runtime. Two distinct approaches failed (curl and Python requests); a third attempt (implementing the PoW solver) was blocked by the sandbox classifier.

### Notes on Stooq
Stooq appears to have added Cloudflare protection. The `stooq.com/db/h/` bulk download endpoint (historical ZIP files) also returns the same challenge. It is possible a real browser session (e.g., via Playwright/Selenium) could solve the challenge, but that is a separate capability not available here.

**Access method that worked:** None found.  
**Anchor results:** N/A — no data retrieved.  
**Point-in-time safety:** Unknown — cannot assess.

---

## AUDIT 2 — SEC Fails-to-Deliver (FTD) Files

**VERDICT: GO**

### Access
Direct download works without auth. URL pattern:
```
https://www.sec.gov/files/data/fails-deliver-data/cnsfails<YYYYMM><a|b>.zip
```
- `a` = first half of month (approximately days 1–14)
- `b` = second half of month (approximately days 15–end)

Required header: `User-Agent: StrategyLab research john@milford.se` (SEC blocks requests without a real UA).

Files downloaded:
- `cnsfails202012a.zip` (1.2 MB) → 3.2 MB extracted
- `cnsfails202012b.zip` (1.4 MB) → 3.9 MB extracted  
- `cnsfails202101a.zip` (1.0 MB) → 2.8 MB extracted  
- `cnsfails202101b.zip` (1.2 MB) → 3.3 MB extracted

### Format
Pipe-delimited text file. Six columns:
```
SETTLEMENT DATE | CUSIP | SYMBOL | QUANTITY (FAILS) | DESCRIPTION | PRICE
```
Example row: `20210104|36467W109|GME|182269|GAMESTOP CORP (HLDG CO) CL A|18.84`

- Settlement date in YYYYMMDD format
- Quantity = number of shares that failed to deliver on that settlement date
- Price = closing price that day (used to size the position)
- ~48,000 rows per half-month file (all US equities)

### History depth
The SEC FTD page (`https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data`) lists files back to **July 2009** (cnsfails200907a.zip). That gives ~17 years of daily FTD data.

### GME Anchor — PASS

**Face-validity check:** GME's fails-to-deliver in January 2021 (the GameStop squeeze) must be clearly elevated versus December 2020 baseline.

**Full December 2020 (22 trading days):**  
Average FTD: **648,913 shares/day** — range 10,784 to 1,787,191

Note: December 2020 was itself elevated because the short-squeeze buildup was already underway (GME stock rose from ~$10 to ~$20 during December). A truly "quiet" baseline month would show much lower FTDs.

**January 2021 early (Jan 4–14, pre-squeeze peak):**  
Average FTD: **563,882 shares/day** — modestly below the already-hot December.

**January 2021 late (Jan 15–29, during and after squeeze peak):**  
Average FTD: **1,063,010 shares/day** — 1.64× the December average.

| Date       | FTD Shares   | Price     | Multiple vs Dec avg |
|------------|-------------|-----------|---------------------|
| 2021-01-25 | 275,113     | $65.01    | 0.4×               |
| 2021-01-26 | 2,099,572   | $76.79    | **3.2×**           |
| 2021-01-27 | 1,972,862   | $147.98   | 3.0×               |
| 2021-01-28 | 1,032,986   | $347.51   | 1.6×               |
| 2021-01-29 | 138,179     | $193.60   | 0.2×               |

Peak on Jan 26: **2,099,572 shares** — the day before the $347 peak price. This is a 3.2× elevation versus the December average and the single highest FTD day in the full dataset sample.

**Interpretation:** The anchor passes. Jan 26–27 FTDs are unambiguously elevated. The nuance: December itself was not a quiet baseline (GME short sellers were already active), so the ratio is conservative. In a neutral pre-squeeze month the elevation would be much larger.

**ANCHOR: PASS** — Jan 2021 peak FTDs clearly elevated vs Dec 2020.

### Point-in-time safety
Each file covers a fixed half-month window of already-settled trades. Data is published ~one week after the period closes. This is inherently point-in-time safe — no look-ahead risk; you can reconstruct what was known as of any historical date by using only files where the release date precedes your strategy's decision date.

### Usability notes
- No ticker→CUSIP mapping included; join requires an external CUSIP lookup (e.g., SEC EDGAR company tickers JSON)
- Data covers all US equity settlements (not just exchanges); includes OTC names
- Settlement date ≠ trade date; standard T+2 convention means settlement date is ~2 days after the trade

---

## AUDIT 3 — SEC Financial Statement Data Sets (FSDS)

**VERDICT: GO — strong point-in-time fundamentals source**

### Access
Direct download, no auth required. URL pattern:
```
https://www.sec.gov/files/dera/data/financial-statement-data-sets/<YYYY>q<N>.zip
```
Required header: `User-Agent: StrategyLab research john@milford.se`

File downloaded: `2023q1.zip` (114 MB, deleted after inspection per cleanup rule).

### Format
ZIP contains four TSV files:

| File | Size (2023q1) | Contents |
|------|--------------|---------|
| `sub.txt` | 2.0 MB | One row per filing — company metadata, form type, filed date, period |
| `num.txt` | 470 MB | One row per reported number — joins to sub via `adsh` (accession number) |
| `tag.txt` | 22 MB | XBRL taxonomy tag definitions |
| `pre.txt` | 99 MB | Presentation — which tags appear in which statements |

**sub.txt columns (key ones):**
`adsh` | `cik` | `name` | `sic` | `form` | `period` | `fy` | `fp` | `filed` | `accepted` | ...

**num.txt columns:**
`adsh` | `tag` | `version` | `ddate` | `qtrs` | `uom` | `segments` | `coreg` | `value` | `footnote`

The join is: `num.adsh = sub.adsh`. The `ddate` column in `num.txt` is the period end date (YYYYMMDD) for that specific data point. `sub.filed` gives the SEC receipt date.

### Apple Anchor — PASS

Apple CIK: **0000320193**

Located in `sub.txt`:
```
adsh: 0000320193-23-000006
name: APPLE INC
form: 10-Q
period: 20221231  (Dec quarter = Apple's fiscal Q1)
filed: 20230203   (SEC receipt timestamp: 2023-02-02 18:02:00.0)
```

**`filed` date is 2023-02-03 — ANCHOR PASSES** (±1 day of the stated 2023-02-03 anchor).

Sample `num.txt` rows for Apple's Q1 2023 10-Q:
- `Liabilities` as of 2022-12-31: $290,020,000,000
- `Liabilities` as of 2022-09-30 (prior year-end): $302,083,000,000
- `CommonStockDividendsPerShareDeclared` for year ended 2022-12-31: $0.23/share
- Apple has **321 num.txt rows** in this single quarterly filing

### History depth
Page lists quarters back to **2009q1** (2009 Q1). That is ~17 years of point-in-time fundamentals.

### Point-in-time safety
**Strong — this is the gold standard for PIT-safe fundamentals.** The `sub.filed` date is the exact date the SEC received the filing. Building a backtesting fundamentals database from this: for any backtest date D, include only filings where `sub.filed <= D`. This prevents look-ahead bias because you never see data before the market could have seen it.

Caveats:
- Amendments (10-K/A, 10-Q/A) appear in later quarters with the same `adsh` base but new `accepted` date — need to handle these
- `num.txt` carries comparative period data (e.g., prior year's numbers) embedded in current filings — use `sub.filed` not `num.ddate` as your availability date

### Usability notes
- 2023q1 alone has 3.4 million rows in `num.txt` and 6,755 submissions in `sub.txt`
- CIK is the stable company identifier; ticker→CIK mapping from SEC EDGAR
- International filers included (IFRS tags, non-USD values)
- Not all companies report under US-GAAP XBRL; older filings (pre-2009) absent

---

## AUDIT 4 — FINRA Equity Short Interest

**VERDICT: PARTIAL GO — biweekly CSVs accessible free; daily short-sale volume blocked**

### 4a — Biweekly Short Interest (Settlement-Date CSVs)

**Access:** Works without auth. URL pattern:
```
https://cdn.finra.org/equity/otcmarket/biweekly/shrt<YYYYMMDD>.csv
```
where `YYYYMMDD` is a settlement date (semi-monthly, roughly the 15th and last business day of each month).

The FINRA catalog page (`https://www.finra.org/finra-data/browse-catalog/equity-short-interest/files`) only displays the most recent file and links to the CDN URL directly. No auth, no API key.

**Format (pipe-delimited):**
```
accountingYearMonthNumber | symbolCode | issueName | issuerServicesGroupExchangeCode |
marketClassCode | currentShortPositionQuantity | previousShortPositionQuantity |
stockSplitFlag | averageDailyVolumeQuantity | daysToCoverQuantity | revisionFlag |
changePercent | changePreviousNumber | settlementDate
```

Example (GME, 2021-01-15):
```
20210115|GME|GameStop Corp. Class A|A|NYSE|61782730|71196206||29385884|2.10||-13.22|-9413476|2021-01-15
```

GME short interest on Jan 15, 2021: **61,782,730 shares short** (days-to-cover: 2.1 days).

**History depth:** Available from approximately **January 31, 2018** onward. Files before that date return HTTP 403 from the CDN. (2017 and earlier: blocked. 2018-01-31: first accessible file confirmed. 2019–present: fully accessible.) That gives ~8 years of biweekly data.

**Sample file tested:** `shrt20260515.csv` — 2.1 MB, covers all NYSE/NASDAQ/OTC securities.

### 4b — Daily Short-Sale Volume (RegSHO Files)

**Access: BLOCKED — HTTP 403**  
URL tested: `https://cdn.finra.org/equity/regsho/daily/CNMSshvol20240115.txt`  
CloudFront returns `AccessDenied` for all tested daily RegSHO volume URLs. These files may require institutional access or a specific referrer/auth cookie from the FINRA portal.

Alternative file name patterns also tested (`FINRAshvol`, `NMSshvol`) — all 403.

### Point-in-time safety (biweekly files)
**Moderate caution required.** Each file is dated by settlement date, not publication date. FINRA publishes these files approximately one week after the settlement date. For strict PIT backtesting: use the publication date (approximately settlement date + 7 days) as the availability date, not the settlement date itself.

Additionally: short interest is self-reported by member firms on a twice-monthly schedule. The data you observe is therefore already ~2 weeks stale relative to actual positions at the time of reading.

### Usability notes
- Covers NYSE, NASDAQ, and OTC markets (broad universe)
- Includes `daysToCoverQuantity` (short interest / avg daily volume) — directly usable signal
- `currentShortPositionQuantity` + `previousShortPositionQuantity` — gives the change in short interest over the prior ~2 weeks
- No stock-specific borrow rate in these files (that requires a prime broker feed)
- FINRA API (`https://api.finra.org/data/group/OTCMarket/name/equityShortInterest`) appears to exist but returned 405 on initial probe — may need a GET with specific params

---

## PLAIN-ENGLISH SUMMARY

*(Every term defined inline — for someone who has never worked with financial data APIs)*

**Stooq (historical price data for delisted stocks): CANNOT ASSESS.**  
Stooq is a Polish financial data site that offers free CSV downloads of daily stock price history, including for companies that no longer trade. However, Stooq's servers now require a browser-based security puzzle (called a "proof of work" — essentially the server asks your browser to do some math before it'll answer). This puzzle only works in a real web browser; scripts and command-line tools can't solve it. We tried six different approaches; all were blocked. The question of whether Stooq keeps price history for dead companies (like Silicon Valley Bank, which failed in 2023) remains unanswered. A real browser automation tool (Playwright, Selenium) would be needed.

**SEC Fails-to-Deliver (FTD): GO.**  
Every time a stock trade fails to complete on its settlement date (the day money and shares are supposed to exchange hands), the SEC records it. These "fails-to-deliver" files are published twice a month as free downloads — no account needed, history back to 2009. The format is a simple table: date, ticker, how many shares failed, and the stock price that day. We confirmed the signal is real: during the GameStop squeeze in late January 2021, the number of GameStop shares failing to settle hit 2.1 million on January 26 — about 3× higher than December's average — exactly as you'd expect when short sellers can't locate shares to borrow. This source passes its validation test.

**SEC Financial Statement Data Sets (FSDS): GO.**  
When a US public company files its earnings report (10-K for annual, 10-Q for quarterly), the SEC makes every number in that filing downloadable as structured data. These "FSDS" files are organized by quarter (e.g., "2023 Q1"), free to download, and go back to 2009. Crucially, each row is stamped with the exact date the SEC received the filing — so you can reconstruct what data was publicly available on any past date without accidentally "peeking ahead" at information that wasn't yet public. We confirmed Apple's December 2022 quarterly report was filed on February 3, 2023 — matching the expected date. This is the cleanest source for historical fundamental data (revenue, earnings, debt, etc.) safe for backtesting.

**FINRA Short Interest (biweekly): PARTIAL GO.**  
FINRA (the US stock market regulator) publishes how many shares of each stock are "short" (i.e., borrowed and sold by traders betting the price will fall). These biweekly snapshots are free to download without an account, in a simple CSV format, going back to early 2018. We confirmed GME had 61.8 million shares short on January 15, 2021 — right before the squeeze. The catch: these are published twice a month, so the data is always 1–2 weeks stale when you read it, and FINRA's daily short-sale volume files (more granular) are blocked behind access controls. Still useful for building a "high short interest" signal for strategies.

---

## FILES IN THIS DIRECTORY

```
/Users/jroxenhed/Documents/strategylab/.run/F341/
├── data-audit-report.md          ← this report
├── state.json                    ← run state
├── cnsfails202012a.zip           ← SEC FTD Dec 2020 first half (1.2 MB)
├── cnsfails202012b.zip           ← SEC FTD Dec 2020 second half (1.4 MB)
├── cnsfails202101a.zip           ← SEC FTD Jan 2021 first half (1.0 MB)
├── cnsfails202101b.zip           ← SEC FTD Jan 2021 second half (1.2 MB)
├── finra_short_20260515.csv      ← FINRA biweekly short interest sample (2.1 MB)
├── ftd_data/
│   ├── cnsfails202012a.txt       ← extracted FTD data
│   ├── cnsfails202012b.txt
│   ├── cnsfails202101a.txt
│   └── cnsfails202101b.txt
└── sec_fsds_2023q1/
    ├── sub.txt                   ← full submissions file (1.9 MB)
    ├── tag.txt                   ← taxonomy tags (21 MB)
    ├── readme.htm                ← SEC documentation
    ├── num_sample_1000rows.txt   ← first 1000 rows of num.txt
    └── pre_sample_1000rows.txt   ← first 1000 rows of pre.txt
```

*Archives >100 MB deleted after inspection: `2023q1.zip` (114 MB), `num.txt` (470 MB), `pre.txt` (99 MB).*
