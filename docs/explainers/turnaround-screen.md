# The Turnaround Screen ("Poor Man's Bloomberg")

What it is, how it works, and why half of it is a lie detector — written for people who don't live in finance.

---

## The story in one paragraph

In the late 2010s, BlackBerry stock traded around $6. Everyone "knew" the company was dead — phones gone, brand a punchline. But quietly, inside the dead phone company, a software business (QNX, which runs the computers in millions of cars) was growing every quarter. The people who noticed *while the stock was still hated* made multiples of their money. The people who noticed after the Wall Street upgrades and the headlines paid $10+ for the same company and made far less. **This screen exists to find the $6 moment, not the $10 one.**

## What we're building (and what we're not)

We are **not** building a cheaper Bloomberg terminal. Bloomberg sells neutral data to everyone — and anything everyone can see is already reflected in the price. Instead, we're building an **opinion engine**: a screen that encodes one specific opinion about markets —

> *"The best buys are companies everyone has given up on, where the underlying business has quietly started improving — and the price doesn't show it yet."*

— and then scans thousands of companies looking for the handful that fit. The data we use is free and public. The opinion is the product.

## Stage 1 — The filter (this is where the edge lives)

Every week, the screen takes ~9,000 US-listed companies and pushes them through four gates. A company has to be interesting on **all** of them at once — and that conjunction is the whole point, because each gate alone is easy to satisfy and means little.

### Gate 1: "Is it hated?" (washed-out price)
The stock must be sitting near its lowest price in years — not just *down a lot* (lots of stocks are down a lot and still falling), but **at the bottom, now**: close to its multi-year low, down more than half from its high, and below its long-term average price.

*Live example:* Nike passed this gate — 62% off its high, 5% above its 3-year low. Intel failed it spectacularly: it's up 500% from its 2024 low and near its 3-year **high**. Whatever Intel is in 2026, it is not hated — buying it now is chasing.

### Gate 2: "Is the business quietly improving?" (fundamentals inflecting)
Using the financial reports every US company must file with the SEC, the screen checks whether the *business* (not the stock) has turned: revenue growing again for at least two quarters in a row, profit trend improving, actual cash coming in the door, margins not deteriorating.

*Live example:* Estée Lauder passed — revenue growing 3 straight quarters, profit improving 3 straight, after years of decline. Moderna failed catastrophically: revenue down 80% year-over-year. A falling stock with a melting business isn't a turnaround, it's a value trap.

### Gate 3: "Is it still cheap?" (valuation)
Even a hated, improving company can already be expensive if speculators piled in. We compare the company's total market price to its yearly sales (the price-to-sales ratio). Cheap means the market is still pricing in failure.

*Live example:* Moderna looks "cheap" on this measure alone (0.7× sales) — which is exactly why this gate can never work by itself. Cheap-and-melting is the trap Gate 2 exists to veto.

### Gate 4: "Are insiders putting money where their mouth is?" (conviction)
Two public signals: the company announcing it will buy back its own shares, and executives/directors buying stock with their own money (both must be disclosed to the SEC). These are bonus points, never requirements — plenty of real turnarounds happen without them.

**Output:** a short, ranked watchlist. Each name shows *why* it qualified — the numbers behind every gate are visible, so the screen gives you opinions with reasons, not just a list.

## Stage 2 — The trigger (timing, not edge)

Stage 1 names can stay hated for years. Stage 2 watches **only the watchlist** (never the whole market) for signs the turn is starting: an analyst upgrade, an earnings beat, a big contract — or the price itself confirming (unusual volume, climbing back above its long-term average, momentum *turning up* from a depressed level).

The house rule, borrowed from everything else StrategyLab has tested: **don't buy because a stock is beaten down — buy when it was beaten down and is now hooking up.** Wait for the turn, not the reading.

## The lie detector (the part that makes this not a toy)

Here's the uncomfortable truth about every stock screen ever built: it's easy to make one that *looks* brilliant. You tune the filters until they catch last decade's winners, admire the imaginary gains, and feel smart. That's called overfitting, and it predicts nothing. StrategyLab's whole identity is refusing to do that. So the screen is bolted to a testing rig:

**1. The filter is locked first, tested second.** The gate thresholds were chosen on principle (what *should* a hated-but-turning company look like?) — and are never adjusted to make a known winner like BlackBerry light up. Fitting the filter to the answer is cheating, and cheating here costs real money later.

**2. It must beat a "null."** We replay history quarter by quarter (using only information that was public *at that moment* — the system is strict about this) and ask: of the companies the screen picked, how many went on to rise 50%+ within a year? But that number alone is meaningless — beaten-down stocks bounce all the time for no reason. So we compare against a **null baseline**: companies that were *merely* beaten-down, with no business improvement. That's the coin-flip group. **The screen only earns trust if its picks beat the coin-flip group, after trading costs.** (Fun live example: Peloton in September 2022 was beaten-down with collapsing financials — the screen would have put it in the coin-flip group, not the picks. It then bounced +59% anyway. That's exactly the kind of noise the null exists to price in.)

**3. The misses stay on screen.** Every name that fit the filter and *didn't* run is kept in a visible list. If you only ever look at the hits, you fool yourself — this is non-negotiable in the design.

**4. Honest caveats are part of the output.** The biggest one: our historical test can only include companies that still exist today. Companies that got crushed and *delisted* are invisible, which makes every historical success rate look better than reality. The system says this on every result rather than hiding it. True turnarounds are also rare, so success rates come with error bars — they're hypotheses, not facts.

## Where the data comes from (all free)

- **SEC EDGAR** — the US government database where every public company must file its financial reports, insider trades, and major announcements. This is the same raw data the expensive terminals repackage.
- **Price history** — daily stock prices via the data providers StrategyLab already uses.

## What exists today, and what's next

| Phase | What | Status |
|---|---|---|
| 1 | The Stage-1 filter → ranked watchlist | ✅ built |
| 2 | The lie detector (replay history, compare vs null) | ✅ built — first full-history run in progress |
| 3 | Stage-2 trigger alerts on watchlist names | gated: only if Phase 2 shows the filter beats the null |
| 4 | The screen UI (table, alerts, validation panel, miss list) | gated: same condition |
| 5 | Feed validated configs to the trading bots | gated: same, plus out-of-sample proof |

That gate is deliberate. **If the filter can't beat the coin-flip group, the pretty UI would just be a nicer way to lose money — so we find out first.**

## Glossary

- **Washed-out** — a stock near its multi-year low that the market has given up on.
- **Fundamentals** — the business's actual numbers (sales, profit, cash flow), as opposed to its stock price.
- **Inflecting** — the moment a declining trend turns into an improving one.
- **P/S (price-to-sales)** — company's total market price ÷ yearly revenue. Rough "how much failure is priced in" gauge.
- **Null / base rate** — what happens to comparable stocks chosen *without* the special ingredient. The bar any signal must clear.
- **Overfitting** — tuning a system until it perfectly "predicts" the past, which destroys its ability to predict anything else.
- **Survivorship bias** — historical tests silently excluding companies that died, inflating success rates.
- **Point-in-time** — only using information that was actually public on the date being tested. No time machines.

---

*The one-line summary: the data is a commodity, the opinion is the product, and StrategyLab is the lie detector that tells us whether the opinion actually pays.*
