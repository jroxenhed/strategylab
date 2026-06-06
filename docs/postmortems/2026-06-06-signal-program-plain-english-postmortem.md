# What We Actually Did: A Plain-English Postmortem

*Covering the research marathon of June 5, 2026. Written for a reader with no statistics background. Every term of art gets defined the first time it appears.*

---

## The one-paragraph version

We took roughly 7,000 US stocks, went back through ten years of daily prices (2015–2024), and tested five different "rules for picking stocks" the same way every time: pretend it's a specific day in the past, pick stocks using only information that existed on that day, then check how those picks did over the next 1 to 6 months compared to ordinary stocks from the same moment. **None of the five rules worked well enough to trust.** Along the way we caught seven bugs in our own measuring equipment before they could fool us, which is arguably the real achievement of the day.

---

## The dataset: what stocks, what data

- **Prices:** daily prices for every US-listed stock Yahoo Finance knows about — roughly 7,000 tickers — from 2015 through 2024. Cached on disk so re-runs take minutes, not hours.
- **Company filings:** SEC EDGAR documents (the paperwork all public companies must file) — revenue numbers, insider purchases (Form 4), delisting notices.
- **The honest limitation:** Yahoo only has stocks that *still exist today*. A company that went bankrupt in 2019 is invisible to us. This is called **survivorship bias** — our data only contains the survivors, which makes the past look rosier than it was. It's worst among cheap stocks (the ones most likely to die), so:
- **The "investable universe" rule:** we only test stocks priced **above $5** with **healthy trading volume** (≥500k shares/day average). Below that line, the data is too survivor-contaminated to trust *and* the stocks are barely tradeable anyway. After this filter, roughly **800–1,100 stocks qualify at any given moment**.

## The measuring stick: how every rule was scored

Every experiment used the exact same procedure:

1. **Pick four "pretend days" per year** — Feb 15, May 15, Aug 15, Nov 15 — for ten years. We call each pretend-day's batch of picks a *cohort* (just "the group of stocks picked that day").
2. **Apply the rule on that day** using only information that was public *on or before* that day. No peeking at the future, ever — even revenue numbers only count once the filing was actually submitted.
3. **Measure forward returns** at three distances: about 1 month, 3 months, and 6 months ahead (21, 63, and 126 trading days).
4. **Compare against "ordinary":** each pick's return is compared to the *median return of every other qualifying stock from that same day*. The difference is the **excess** — did the rule's picks beat a dart-throwing monkey working the same day? This matters because in a year when everything went up 30%, a pick that made 20% was actually a *bad* pick.
5. **Train/test split:** rules were designed and sanity-checked on **2015–2020** ("explore"), then graded — once, with no second chances — on **2021–2024** ("confirm"), which nobody and nothing touched during design. This is the standard guard against fooling yourself: any rule can look great on data it was tuned on.

## The anti-self-deception machinery (why so much ceremony?)

Markets data is a hall of mirrors — test enough rules and something will look great by pure luck. Our defenses, in plain words:

- **Write the rule down and lock it before looking.** Every experiment started with a "charter": exact thresholds, exact hypotheses, exact pass/fail bars, written by an agent that was *forbidden from seeing any results data*, then fingerprinted (cryptographic hash) so it couldn't be quietly edited later.
- **A separate, blind judge.** The 2021–2024 grading was always run by a fresh agent that never saw the 2015–2020 practice results — so it couldn't be tempted to nudge.
- **A limited budget of attempts.** We pre-committed to exactly 4 experiments and divided our "luck allowance" between them (statisticians call this an alpha budget). Testing 100 rules and reporting the best one is how you manufacture fake discoveries; we capped ourselves at 4.
- **Trust nothing until it touches real data.** Each new piece of measuring code had to pass a smoke test on real data with pre-stated expectations ("March 2020 must register as a crash") before its output could be believed. This rule exists because it kept catching real bugs — see below.

## What we tried, experiment by experiment

### Background: the original idea that started everything (before today)

For weeks, the lab's premise was: **buy beaten-down stocks whose financials show a turnaround**. "Beaten down" meant stocks that had fallen 50%+ from their highs — internally we nicknamed this group **"the pond"** (as in: the pond we fished in). Think GoPro after its collapse, Enphase, Corsair after 2021 — that class of name. Two full validation runs killed this idea: the strict version of the rule only found about **one stock per year** (you can't prove or disprove a rule that rare — you'd need decades), and a measurement error had made the comparison look much worse than it was (we compared a "sell on a spike" strategy against "hold to the end" — apples vs oranges). The autopsy of that failure produced the hunch that drove today: maybe the *premise* was the problem; let the data speak instead.

### Experiment 1: Does "market weather" predict anything?

- **The idea:** classify every day into four weather states — calm-and-rising, neutral, stormy, crisis — using only the S&P 500's trend, its recent choppiness, and how many stocks sat above their own 200-day average. Then check: do stocks picked on "calm" days do better over the next 3 months than stocks picked on "stormy" days?
- **Why we thought so:** in the old beaten-down experiments, *which year* you bought mattered enormously (hit rates ranged from 24% to 84% by year). Maybe timing was the real signal.
- **Result: no.** On 2021–2024, "calm" days and "neutral" days produced essentially identical outcomes (51.4% vs 51.5%). Also, our "crisis" state turned out so rare (6 days in a decade) that most of the planned comparisons couldn't even be tested. The old year-to-year effect appears to have been a quirk of the beaten-down group, not a general truth.

### Experiment 2: Momentum — buy stocks near their 52-week high

- **The idea:** the most documented pattern in finance literature: stocks near their highs, in uptrends, tend to keep going. Rule: price within 5% of its 1-year high *and* above a rising 200-day average.
- **First result looked great:** picks beat their same-day peers by ~5 points over 3 months, consistently. Briefly our first confirmed win.
- **Then two honesty checks demolished it.** First: was it really "momentum," or were we just picking *jumpy, volatile* stocks in a period when jumpy stocks happened to win? When each pick was compared only against peers of *similar volatility*, the advantage shrank below provability. Second, and worse: we discovered our $5/volume filter had silently not been applied — and when we re-ran with the junk properly excluded *on both sides of the comparison*, the headline advantage dropped from +5.4 points to +1.9 points, which is not distinguishable from zero. **Final verdict: a real-looking lean in the right direction, not provable, and possibly just a volatility effect.**

### Experiment 3: Short the deteriorating — bet *against* crashed stocks still reporting decent revenue

- **The idea:** from the old pond autopsies — companies like GoPro circa 2015 whose price had collapsed while their reported revenue still looked fine often kept falling (the bad news was still arriving). So: bet against stocks down 50%+ from highs, near their lows, with revenue still growing. This was the only idea with evidence from our own data.
- **Result: backwards.** Among stocks that pass the $5/volume filter, the crashed-but-still-reporting names actually did slightly *better* than their peers over the following months. Betting against them loses money even before borrowing costs. The "keeps falling" pattern apparently lives among sub-$5 stocks — exactly where the data is unreliable and where shorting is impractical anyway. Bonus irony: survivorship bias *favored* this experiment (the dead would-have-been-winners are missing from our data), and it still failed.
- One caveat: we couldn't test the "revenue still growing" part properly, because two-thirds of crashed small companies have no machine-readable revenue data in EDGAR. We tested the price-only version and pre-registered that limitation.

### Experiment 4: Price information vs. filing information — a head-to-head

- **The idea:** the lab's working motto has been *"price leads, filings trail"* — meaning the stock price reacts to news months before it shows up in official filings, so price-based signals should beat filing-based ones. We made it a fair fight: each quarter, pick the top 50 stocks by *recent price performance* (Team Price) and the top 50 by *recent revenue growth from filings* (Team Filings). Same universe, same scoring.
- **Result: a draw at zero.** Team Filings showed no advantage over ordinary stocks (+0.14 points — basically the definition of nothing). But Team Price *didn't beat Team Filings either* (it actually trailed slightly, well within noise). So half our motto held (filings carry no edge) but the half that mattered (price carries one) didn't. The motto survives only as a story about *how fast information travels*, not as a way to pick stocks.

## The bugs we caught in our own equipment (and why they matter)

Seven measurement bugs were caught *before* any of them became a believed conclusion. The greatest hits:

1. **The timezone bug:** Yahoo timestamps carry New York timezone info; our code compared them against plain dates, every comparison silently failed, and the market-weather tool classified all 2,516 days as "not enough data yet." Caught because we require sanity checks like "March 2020 must read as a crisis" before trusting output.
2. **The backwards short:** the profit-taking logic for short bets triggered when the price *rose* (a losing short) instead of when it fell. Caught in code review before any short was evaluated.
3. **The missing comparison group:** the first momentum run produced picks but zero "ordinary stocks" to compare against, making all its scores meaningless. Caught by reading the output before interpreting it.
4. **The junk-stock leak:** the $5-and-volume filter existed but was never wired in. First exposed by absurdities in the shorting data — one "stock" showed a price of $51 million (a data artifact from reverse stock splits) and another supposedly fell 11 million percent. Fixing this is what downgraded momentum from "confirmed" to "unproven."

The pattern worth remembering: **every one of these passed its own unit tests.** Synthetic test data shares the blind spots of whoever wrote the code. Only contact with real, messy data — checked against common-sense expectations stated *in advance* — caught them. That's now a standing rule in the lab (we file it as F338).

## What we assumed (and what we deliberately didn't)

**Assumed:**
- Yahoo's prices are accurate enough once corrupt-looking data is filtered (we exclude anything with a >10× single-day price jump in its history).
- Quarterly snapshots (4 decision days/year) are a fair way to sample — we did not test monthly or daily trading.
- Simple cost handling: a small slippage charge per trade, a 10%/year borrowing cost for shorts. No modeling of taxes, market impact, or partial fills.
- 1-to-6-month holding periods. Nothing shorter (day-trading) or longer (multi-year) was tested.

**Deliberately NOT assumed:**
- That any idea was true because it sounded smart, came from our own history, or appeared in famous papers — everything got the same locked-rules, blind-grading treatment.
- That our own code worked — hence the smoke tests.
- That a good-looking number means anything before checking what it was compared against (the original kill verdict, our biggest momentum number, and the shorting data all turned out to be comparison artifacts at first pass).

## So what did we actually learn?

1. **On clean, free data — stocks over $5 with real volume, judged over 1–6 months — none of the simple selection rules we tested beats just holding ordinary stocks from the same day.** Not market timing by weather, not momentum (provably), not betting against crashed companies, not recent-winners ranking, not revenue-growth ranking.
2. **The apparent magic in earlier results lived in cheap, illiquid, data-corrupted stocks** — the segment where we can't measure honestly (survivors only) and mostly couldn't trade anyway.
3. **Our measuring equipment now works and is trustworthy**, which is a genuine asset: locked-rule experiments, blind grading, automatic comparison groups, data-quality filters, and a test suite that grew by ~160 checks in one day.
4. **What remains untested** (the honest to-do list): event-driven ideas (reacting to specific filings like 8-K announcements or insider stock purchases within days, rather than ranking stocks quarterly), combinations of factors, shorter horizons, and anything below the $5 line (would require buying professional survivorship-free data).

## Where it stands

The formal program is complete: four experiments, zero confirmed edges, full records in `docs/plans/` (one verdict document per experiment, plus the decision document `2026-06-05-decision-gate-9.md`). The next move — chase event-driven ideas, sharpen the momentum question once more, build platform features instead, or buy better data — is an open decision, deliberately left to a human.

*A note on the jargon this document translates: "the pond" = the beaten-down stock group from the original premise; "cohort" = one quarter's batch; "excess" = return relative to same-day ordinary stocks; "explore/confirm" = practice years vs. graded years; "charter" = the locked rulebook; "H1/H2" = the numbered predictions inside a charter; "floors" = the $5/volume filter; "F338" = the real-data smoke-test rule. If a future document uses a term not defined here, that's a bug in the document.*
