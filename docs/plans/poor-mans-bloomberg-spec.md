# StrategyLab — "Poor Man's Bloomberg" Turnaround Screen

**Status:** spec / design doc
**Target:** a new screen inside StrategyLab (React/FastAPI), reusing the existing backtester, scanner, charting, and Alpaca data layer.

---

## 1. North Star (read this first)

The goal is to surface a name like BlackBerry **at $6, while it's still hated** — not at $10 after the upgrade and the +60% run, which is just chasing.

This is **not** a cheaper Bloomberg. We can't out-Bloomberg Bloomberg, and their data is neutral and crowded — anything everyone sees is already priced in. We're building the opposite: an **opinion engine**. It encodes one specific thesis — *a washed-out name whose fundamentals are quietly inflecting* — and filters the market down to candidates that fit it. The data is the commodity; the bias is the product.

It only works because the use case is forgiving: turnaround swing-screening on a weekly cadence, not HFT. Commodity/free data (EDGAR + Alpaca) is plenty.


---

## 2. Design principles (these are constraints, not vibes)

These come straight out of StrategyLab's own findings and are non-negotiable, because the failure mode of a pretty scanner is **manufacturing false conviction** — a rage-deleted chart with a UI on top.

1. **Simpol.** Few parameters, principle-based filters. Every knob you add is a knob you can overfit. Fewer parameters → less curve-fit → more robust.
2. **No tuning to known winners.** Do **not** fit the filters to BlackBerry or any other name we already know ran. That's the in-sample trap — the method that "predicts" its own training data predicts nothing. Lock the filters on principle, *then* test.
3. **A signal isn't trusted until it beats a null.** Every filter config is backtested against a base rate (random washed-out names / buy-and-hold) through the existing engine. Hit-rate-vs-null is a first-class output, shown on the screen.
4. **Show the misses.** The screen displays the historical hit rate **and** the list of names that fit the filter but *didn't* run. If we only ever see the hits, we're fooling ourselves. The miss list is one click away, always.
5. **Catalysts are timing, not alpha.** The edge is in Stage 1 (the quiet pre-conditions, the "before"). Stage 2 (catalysts) just decides *when*. Scanning the whole market for catalysts is all false positives — and some catalysts (the upgrade) *are* the move, so firing on them is chasing.

---

## 3. Data sources

| Source | Cost | Used for |
|---|---|---|
| **SEC EDGAR** | free | 8-K (buyback/NCIB authorizations, earnings), 10-Q/10-K + XBRL (fundamentals), **Form 4 (insider buys)** |
| **Alpaca** (already integrated) | existing | price/volume history, multi-year lows, MA ribbon, fundamentals where available |
| Fundamentals (XBRL from EDGAR, or a cheap API as fallback) | free/cheap | revenue trend, margins, net-income trend, operating cash flow |
| *Later, optional* | — | analyst-rating feed, news, unusual options flow |

Reuse, don't rebuild: the backtester, the scanner module, the candlestick/indicator charts, the Alpaca pipe.

---

## 4. Stage 1 — The Universe Filter (the "before" = the alpha)

Runs across the universe on a weekly cadence. Produces a ranked candidate watchlist. Each filter is a few-parameter, principle-based rule (defaults shown — to be set on principle, then backtested, **not** tuned to BB).

- **Washed out.** Price within ~X% of its multi-year low / down ~Y% from its N-period high / below the long-term MA ribbon. *The market hates it.*
- **Fundamentals inflecting.** Revenue YoY growth has turned positive (or is accelerating) for ≥ K consecutive quarters; gross margin stable-to-improving; net-income trend climbing for ≥ K quarters; operating cash flow positive/improving. *The business is turning while the price hasn't noticed.*
- **Hidden growth engine.** ≥ 1 reported segment growing ≥ Z% YoY inside an otherwise "dead" name (the QNX-inside-BlackBerry pattern). *Note: segment data is the hardest to get programmatically — see Risks; may be v2 or LLM-assisted.*
- **Capital-allocation conviction.** Active buyback/NCIB authorization (8-K) **and/or** net insider buying (Form 4) over the trailing N months. *Insiders and the board are voting with cash.*
- **Still cheap.** Valuation (P/S or EV/S) below a threshold or below its own historical median. *Cheap before the multiple expands — not after.*

**Output:** ranked candidate list with the inflection metrics + conviction flags visible per row.

---

## 5. Stage 2 — The Catalyst Monitor (timing, not alpha)

Runs **only on Stage-1 names**. Never market-wide. Fires an alert when a watchlist name shows:

- Analyst upgrade / price-target hike
- Earnings beat or guidance raise
- Contract / certification / partnership news (e.g. an AtHoc FedRAMP recert, a QNX design win)
- **Technical confirmation** — unusual volume; price reclaiming the MA ribbon / breaking the downtrend; momentum *turning up* from an extreme. This is the StrategyLab entry principle: don't fire because RSI/stoch is low, fire because it **was** low and is now hooking up. Wait for the turn, not the reading.
- *Optional later:* unusual options flow

**Output:** alert = watchlist name + which catalyst fired + technical-confirmation state.

---

## 6. The Validation Hook (the heart of it — StrategyLab integration)

This is the part that makes it not a toy. StrategyLab already *is* the rigor layer — backtest, cost modeling, walk-forward, false-signal filtering. The screen plugs into it.

For any filter config, run through the existing engine:

> "If I'd bought Stage-1 candidates on Stage-2 confirmation across history — with slippage, commission, and stop modeled — what's the return distribution, win rate, drawdown, and **hit rate vs the base-rate null**?"

- **Walk-forward** the parameter set before trusting it (existing StrategyLab discipline).
- **Survivorship correction:** the historical test universe must include delisted/failed names, or the hit rate is fiction.
- A config does **not** graduate to the bots until it beats the null **out-of-sample**.

The screen surfaces, per config: historical hit rate, the null it must beat, walk-forward result, and the **miss list** (fitted-but-didn't-run names). Non-negotiable per principle #4.

---

## 7. The screen / UI (light — it's one StrategyLab screen)

- **Candidate table** — Stage-1 watchlist, ranked, with inflection metrics + conviction flags per row.
- **Catalyst feed** — alerts for watchlist names (Stage 2).
- **Per-candidate drill-down** — reuse the existing candlestick + indicator chart; add fundamental-trend sparklines (revenue / margin / net income / OCF); show the backtested **hit-rate badge** for the active config, with the **miss list** one click away.
- **Validation panel** — hit rate vs null, walk-forward results, the misses. Front and center, not buried.

---

## 8. Build phases (incremental, simpol, Claude-Code-ready)

| Phase | What | Why first |
|---|---|---|
| **1** | Stage-1 filter off EDGAR + Alpaca → static ranked watchlist (washed-out price + fundamental inflection + buyback/insider flags) | Cheapest, highest signal, runnable this week |
| **2** | Wire the watchlist into the backtester → measure the filter's historical hit rate vs the null | This is what proves or **kills** the idea — do it before any UI polish |
| **3** | Stage-2 catalyst monitor + alerts on watchlist names | Timing layer, only on validated candidates |
| **4** | The screen — candidate table, catalyst feed, validation panel, miss list | UI last; it's worthless before phase 2 |
| **5** *(optional)* | Feed validated configs to the bot army / paper trade | Only configs that beat the null out-of-sample |

Phase 2 is the gate. If the filter doesn't beat the null, the slick UI would just be a prettier way to lose money — so we find out before building it.

---

## 9. Open questions & risks (the honest list)

- **Segment data is hard.** "Hidden growth engine inside a dead name" is the strongest Stage-1 signal and the hardest to pull programmatically. Options: LLM-assisted parse of 10-Q MD&A, a paid fundamentals feed, or punt to v2 and lean on the other filters first.
- **Survivorship bias.** If the historical universe is "stocks that still trade today," every washout that died is invisible and the hit rate is inflated. Must include delisted names.
- **In-sample trap.** Resist tuning filters until BB lights up in backtest. That's fitting to the answer. Lock on principle, then test, then read the miss list honestly.
- **Small n.** True turnarounds are rare; the hit rate may carry wide error bars. Treat any result as a hypothesis with a confidence interval, not a fact. Size accordingly.
- **Catalysts lag.** By the time the upgrade prints, some of the move is gone. Measure how much edge survives entry *after* the catalyst — that's the realistic fill, not the pre-catalyst paper return.

---

*The whole point: the data's a commodity, the bias is the product, and StrategyLab is the lie detector that tells us whether the bias actually pays.*
