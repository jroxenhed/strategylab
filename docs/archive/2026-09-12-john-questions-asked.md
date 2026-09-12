# Questions for John (StrategyLab)

These are decisions that only John can make, each with the evidence already gathered and a recommendation. Ask them one at a time, biggest unlocked backlog first, and move an answered question into JOURNAL.md. Drafted 2026-09-12.

## 1. Start the phase 1 discovery build now that all four data panels are in?

Evidence: Phase 0 is finished. Prices for 9,222 tickers, 411k rating events and 2.9M short-interest rows sit on the research worker; the 5.19M-row news panel is on the Mac.
Evidence: The design for the scan is written and locked. Nothing technical blocks a start, but it is a large build that needs its own spec, plan and build cycle, and the first step has to run where the panels are.
Recommendation: Start it as the next big research build. The data is the expensive part and it is already paid for, and the scan is what turns the panels into test ideas instead of storage.
Unlocks: F404 itself, and it feeds new premise cards into the existing Desk premise flow.
Items: F404

## 2. Which time window should the one-shot fresh-data test of the insider-sell premise use?

Evidence: The idea, filed as p-495676dd, is that when insiders at small and mid-size companies sell shares by choice, the stock does worse afterwards. The first look showed minus 11.74 percentage points over 30 trading days, but the direction was read off that same data, so it is not proof.
Evidence: A fresh-data test uses years the idea never saw, and the result goes into a permanent record of every test run, which keeps us honest about how many tries it took. The cache of insider filings stops in late 2023, and the brief says there are only about three qualifying sales per year.
Evidence: Option (a) 2021 to 2024 is too small to see the effect. Option (b) 2021 to 2026H1 is the only window big enough, but it spends the whole untouched reserve and needs the filing cache rebuilt past 2023. Option (c) splitting into a test plus a 2025+ reserve leaves both halves too small.
Recommendation: Either extend the cache and test once on 2021 to 2026H1, accepting the reserve is spent, or declare the premise too small to test and require a bigger net first. Do not split the window.
Unlocks: F393, the first real confirm run and record entry in the premise workbench, plus the confirmed state in the Desk flow.
Items: F393

## 3. Approve a charter for "for whom do filings lead price?"

Evidence: The event harness (F342) shipped and the analyst ratings panel (F401) is built, with 411,000 rating actions covering 4,396 of 7,053 stocks, so only your approval is missing.
Evidence: One caution on the data: analyst coverage is thin below mid-cap, which is the very population the test is about, so the coverage split may be coarse for the smallest names.
Evidence: The axiom "price leads, filings trail" is a working hypothesis, and this is its direct test: does drift after a filing depend on how many people watch the stock? Power looks plausible here, because the 10-Q/10-K and 8-K families hold about 13,000 events each with a detection floor near 0.6pp, unlike the rare-event studies that came back untestable.
Recommendation: Approve a symmetric charter: split filing events into attention buckets (market cap, dollar volume, analyst coverage) and test the drift difference between the lowest and highest bucket. Symmetric matters: the design must be able to show filings trailing price for small names, not just confirm the axiom. Note that F370 already tested the fundamental-surprise dose in this same filing family and got a null, so the attention split, not the dose, is the new claim.
Unlocks: F347 build and run; a third premise family outside the insider lineage.
Items: F347

## 4. Re-open paid delisted-price data, or stay on free sources?

Evidence: Every research study so far runs on a universe with zero dead companies in it, so results are biased, and the record says the bias direction is asymmetric: the do-nothing baseline is inflated more than the signal, which makes the measured edge understated to unknown rather than understated by a known amount.
Evidence: The free route is closed: the Stooq probe found no delisted prices at all. The one paid option found, Sharadar at about 50 dollars a month, was rejected in June on license grounds, because it deletes your data when you cancel, which breaks reproducible research. So there is currently no usable source at any price that has been checked.
Evidence: The harness already knows how to price a company that dies inside a holding window, but that code has only ever run against made-up test data.
Recommendation: Stay on free sources and keep the item gated. Every study that stalled so far stalled on power (too few events), not on survivorship, so a delisted feed would not have changed a single verdict yet. Revisit when a study produces a result you would act on, and at that point shop for a source whose license allows keeping the data after cancellation, rather than re-opening Sharadar.
Unlocks: F318 itself, plus a real-data test of the harness delisting path that today has only fake-data coverage.
Items: F318

## 5. Cast a wider net for the insider-buy study, or shelve it? (R-1c, from F343)

Evidence: The insider cluster-buy study ran twice, the second time at full scale on 38,425 Form 4 filings. The signal pointed the right way every time: the top-scoring group beat the bottom by 3.28pp over three months, the strongest group beat the market by 3.87pp, and all nine robustness variants held their sign.
Evidence: The design could only reliably see effects above 3.40pp, and the smallest edge worth trading at your account size is 1.0pp, so the pre-agreed rule closed the study with nothing confirmed and no alpha spent. Getting to a 1.0pp floor needs about twelve times more valid events than the 2015 to 2020 window contains.
Evidence: No backlog item currently owns this decision: F372 covers the distress-recovery study only.
Recommendation: Shelve it for now. The only routes to twelve times more events are a much longer history or relaxed liquidity floors, and relaxed floors mean trading names you would struggle to get filled in, which changes what is being tested. The filing families (10-Q, 10-K, 8-K) already clear the power floor with the data on hand, so spend the next charter there and revisit insider buys if a longer Form 4 history becomes cheap.
Unlocks: Closes the insider lineage cleanly, or opens an R-1c item with a stated net and caveats.
Items: F343 (no open TODO item; the work would be filed fresh)

## 6. Shelve the R-2 distress-recovery study, or widen the event net before running it?

Evidence: R-2 buys stocks that have already crashed and bets they recover, so it looks for outperformance. The power census counted only 447 qualifying events. That means the smallest effect the test could reliably see, in either direction, is 4.63 percentage points, far above the 1.0pp floor that makes an edge worth trading at your account size.
Evidence: Running the charter as written would spend a full-scale explore run and return UNTESTABLE, the same wall R-1b hit. R-1b's abort rule then kept its fresh test window sealed, so nothing was confirmed and no test budget was spent.
Evidence: A bigger net means a longer period or looser entry floors, both of which need stated caveats and a fresh charter.
Recommendation: Shelve the R-2 design as written. Only revisit it as a new charter with a structurally bigger net, the way R-1b was closed.
Unlocks: The R-2 distress-recovery study, which cannot start until this is settled. It has no open TODO item today, so a yes would file a fresh one.
Items: F372 (no open TODO item; the old charter item F345 is retired)

## 7. Do we split the two biggest backend files, or leave them long?

Evidence: backend/routes/backtest.py is 1267 lines and backend/bot_manager.py is 697, against a 500-line guide.
Evidence: The easy splits already happened: quick, macro, sweep and optimizer routes each live in their own file, so what is left in backtest.py is the core run path that calls into all of them. Splitting further would cut each file in half but spread one run path across more files.
Evidence: The original item gated this on the next architectural audit, and no audit is scheduled.
Recommendation: Leave both files as they are. The 500-line guide is a smell test, not a rule, and these two files are long because they hold one coherent job each. Revisit only if a change to either file starts needing edits in three places at once.
Unlocks: Either closes F63 for good, or turns it into real work with a clear shape instead of a standing open question about file size.
Items: F63

## 8. Does the node based strategy builder grow richer nodes, or stay at small nodes?

Evidence: Inline parameter editing and the selection ring shipped. No node has more than three parameters, in the frontend catalog or the backend node list.
Evidence: The Code node that motivated a side panel does not exist, "code" is only a category label. No other node builder item is open, so no planned work will create a node that needs a panel.
Recommendation: Say the builder stays at small nodes for now and drop the panel. Bring F272 back only when you ask for a node with many parameters or a multi-line value, such as a Code node.
Unlocks: F272, the side panel that edits the parameters of the selected node.
Items: F272 (no open TODO item; it waits on this answer)

## 9. Should each dose level get its own random seed, or keep the shared one?

Evidence: The study is post-earnings drift, meaning how a stock keeps moving in the weeks after it files its quarterly or annual report. The explore run sorts events into three dose levels, small to large, and draws random resamples to put an error band around each one. All three bands come from one starting seed, so they move together instead of independently.
Evidence: The code carries a written reason: one seed keeps the doses comparable and makes a parallel run produce the exact same bytes as a single-core run, which is how we prove a run is reproducible. Switching to one seed per dose changes every band and voids that proof, so a new baseline has to be recorded.
Evidence: The only study that used this driver, F370, closed as a null result, so nothing live depends on the answer. The next dose-response study will inherit the same pattern.
Recommendation: Keep the shared seed and promote the existing code note to the decision. Revisit when the next dose-response study is built, where independent bands would actually be read. Fix the XOR seed helper regardless, since that is a plain code issue.
Unlocks: F382 itself, which then closes as documented behaviour or becomes a small seeding change with a fresh reproducibility baseline.
Items: F382

## 10. Do we still want to measure parallel against sequential review dispatch?

Evidence: The measuring tools are finished: run-state.py records tokens per agent and prints a review subtotal, and the Slack summary carries a Review cost line.
Evidence: What is missing is data, and it cannot be collected because no overnight build has run since 8 June; reviews now go through the review-wave skill instead. One review-wave run cost about 695k tokens over 15 agents, but there is nothing to compare it against.
Recommendation: Retire the comparison. Keep parallel dispatch as the default, keep the cost line in the report as a running number, and drop F280. If overnight builds start again and cost becomes a concern, the measurement takes one run each way to redo.
Unlocks: Clears the last open infra item from the old overnight-builder era and lets the cost line stand as plain reporting rather than an open experiment.
Items: F280
