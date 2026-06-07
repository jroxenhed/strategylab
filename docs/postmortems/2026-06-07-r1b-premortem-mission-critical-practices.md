# Why This Research Process Looks Like Aerospace Engineering — and a Premortem for R-1b

*Written 2026-06-07, the night before R-1b's full-scale run, at John's request. Plain English throughout; every term of art defined where it first appears. Jargon glossary at the foot.*

---

## Part 1 — The failure we're actually defending against

Most software fails *loudly*: a crash, an error page, a wrong number a user notices. Trading research fails **silently and pleasantly**. The three classic ways:

- **Look-ahead bias** — the backtest accidentally "knows" something before the market did (a filing dated a day early, a price adjusted with future information). The result: a beautiful historical track record that no real trader could have earned.
- **Peeking** — you look at the results, then adjust the rules "just a little," then look again. Each peek feels innocent. After ten of them, your strategy is hand-fitted to the past and worthless for the future.
- **Multiplicity** — test enough ideas and one will look great by pure luck. If you don't *count your attempts*, you can't tell luck from signal.

Notice what these have in common: **none of them throw an error.** They all produce a green dashboard, a high Sharpe ratio, and a warm feeling — right up until real money is on the table. That's the same failure *shape* as the famous engineering disasters: the O-rings eroded on many flights before Challenger, and each time it was normalized because nothing visibly failed. The system *looked* fine because the failure was invisible by design.

Richard Feynman's closing line in the Challenger report was: *"reality must take precedence over public relations, for nature cannot be fooled."* Substitute **the market** for nature, and you have this research program's founding axiom. The market doesn't care how good our backtest looks. It will grade the real bet.

So we borrowed the defenses from the industries that learned them in blood and treasure.

---

## Part 2 — The practices, their ancestors, and the night they earned their keep

Each row below is one practice we use, where it comes from, what silent failure it blocks — and where possible, a *real catch from this very project* proving it's not theater.

### 1. The charter — borrowed from clinical-trial pre-registration

**What it is here:** before any experiment runs, a "charter" document freezes everything: the exact hypothesis, the scoring formula, every constant, the pass/fail bars, even what we'll do if the test turns out too weak to be meaningful. The document is fingerprinted (a "sha256" — a digital fingerprint that changes if even one character of the file changes), and the experiment code verifies that fingerprint at startup, refusing to run against a tampered charter.

**The ancestor:** drug trials were once plagued by researchers quietly changing what counted as "success" after seeing patient data. The cure was **pre-registration**: publish your endpoints *before* the trial starts, in a registry you can't edit. Moving the goalposts becomes visible, hence impossible.

**What it blocks:** peeking-and-tuning. If the rules are frozen and fingerprinted before any outcome exists, "just adjusting one threshold" requires a formal, logged amendment — and our amendment rule (§10) only allows changes *before outcomes are seen*.

**Earned its keep:** R-1's de-clustering window was amended (21 → 30 calendar days) — *legally*, because the fix happened before any outcome was computed, and it was logged in three places rather than by silently editing the frozen file.

### 2. Blind authoring and the sealed confirm window — borrowed from double-blinding

**What it is here:** charter text is written by an "author" (an agent session) that is *barred from reading any results* — no outcome tables, no ledgers, no journals. Later, the experiment's final out-of-sample test (the "confirm window" — data the strategy has never touched) is graded by a separate session that never saw the exploratory results.

**The ancestor:** in medicine, neither the patient nor the doctor knows who got the placebo, because *knowing changes behavior* — even honest people unconsciously steer. Double-blinding removes the steering wheel.

**What it blocks:** the subtlest peeking of all — the kind that happens *inside one mind* (or one chat context) that has seen the answer and can't unsee it.

**Earned its keep:** tonight. The blind author of the R-1b charter, unable to read program records, *invented* a plausible-sounding list of "full-scale anchors." The orchestrator — allowed to read the records — caught the invention and corrected it against the real probe definitions. Blindness plus an informed verifier: each covers the other's weakness.

### 3. Pre-stated anchors and "NOT-RUN is never PASS" — borrowed from aviation checklists and acceptance testing

**What it is here:** before believing any new instrument, we write down — *in advance* — the specific sanity checks its output must pass on real data ("anchors": e.g. "the midnight-timestamp rate should be ~0.6%; if wildly off, stop"). And a check that couldn't run reports **NOT-RUN**, never quietly counts as passed.

**The ancestor:** aviation learned that experienced pilots die from skipped checklist items, so the checklist became sacred: every item gets an explicit answer, and "we didn't check" must *look different* from "we checked and it's fine." Acceptance criteria written *before* the test is also standard practice anywhere failure is expensive.

**What it blocks:** the green-by-default illusion — systems that report success because nothing recorded a failure.

**Earned its keep:** twice in one night, beautifully. (1) The F359 timestamp investigation's first anchor — "the rate should be ~0.6%" — FAILED immediately, which *was* the finding: the 0.6% had been measured on the wrong population (the whole filing cache back to 1994, when filings had no time-of-day) — the real study population's rate was 0.0016%, three orders of magnitude smaller. The anchor turned a planned sample into a complete 19-event census, all verified genuine. (2) The F338 rule ("never believe a new instrument until it touches real data") caught the calibrate crash: the new dose-builder read a field its own test fixtures had *fabricated* — every synthetic test passed; the first real filing crashed in six minutes.

### 4. Frozen constants + formal change control — borrowed from configuration management

**What it is here:** every numeric constant in the scoring formula is frozen with a one-line rationale ("0.5 — chosen as a modest fixed amplification, not a tuned optimum"). The random seed is frozen. The data vintage is frozen (see #5). Changing anything requires either a pre-outcome amendment or a whole new charter — tonight John chose the *new charter* route (R-1b) precisely so the final result would carry "zero amendment-legality footnotes."

**The ancestor:** aerospace configuration management — you must be able to say *exactly* which version of every part, drawing, and parameter flew. "Roughly this design" is not an answer when something explodes.

**What it blocks:** the slow drift where a system's actual behavior diverges from its documented behavior one innocent tweak at a time.

### 5. The frozen returns matrix — borrowed from "no moving reference frames"

**What it is here:** the benchmark every stock is compared against (the median return of all tradeable stocks that day) is read from a single frozen snapshot — the "matrix," built once, fingerprinted, used forever. Why? We *measured* that the price vendor retroactively rewrites history: re-downloading the same stock's past prices on different days gives microscopically different numbers (dividend adjustments get restated). Tiny — but it means a study re-run next month wouldn't reproduce itself bit-for-bit. The matrix freezes one vintage permanently.

**The ancestor:** metrology's prime directive — you cannot measure with a ruler that changes length. Standards bodies keep literal reference artifacts in vaults for this reason.

**What it blocks:** irreproducibility — and with it, the inability to distinguish "the strategy changed" from "the data changed under us."

**Earned its keep:** tonight the strict mode guarding this pin *fired on a false positive* (it demanded a benchmark for a Saturday — a non-trading day — due to a calendar bug in the validation). The gate failing *loudly on the wrong thing* is the system working: we fixed the validator, and the gate now enforces the pin at the exact point of use.

### 6. Bit-exact equivalence probes — borrowed from independent verification & validation (IV&V)

**What it is here:** whenever we touch the measurement instrument (faster bootstrap, parallel processing), the new code must produce **bit-identical** output to the old code — not "statistically similar," *identical to the last binary digit* — proven on real data before any experiment uses it. NASA calls the discipline IV&V: verification done by a party other than the builder.

**What it blocks:** the "improvement" that silently changes results. A 4× speedup that shifts a p-value in the third decimal is not a speedup; it's a different instrument wearing the old one's name.

**Earned its keep:** twice tonight. The parallel-harness probe initially reported FAIL — five differences. All five were `NaN vs NaN`: fields that were *identical*, flagged because (by the floating-point standard) NaN never equals itself. The comparator was fixed and the rerun proved all 23 outcomes bit-identical. And the bootstrap vectorization (F355) was only accepted after 230 values matched across four random seeds — with the probe *forbidden from printing the values themselves*, so even the verification couldn't leak outcomes.

### 7. The FDR ledger — borrowed from multiplicity accounting

**What it is here:** an append-only ledger that counts **every hypothesis the program has ever tested**. Each new experiment "draws" against it, and the statistical bar for declaring anything real rises as the count grows ("false-discovery-rate control" — the math that answers "out of everything you've ever tried, how many of your 'wins' are probably luck?"). R-1b took its own fresh draw; the aborted R-1's draw was formally retired.

**The ancestor:** particle physics, which learned that with enough detector channels, three-sigma blips appear constantly — hence the brutal five-sigma standard and the "look-elsewhere effect" corrections.

**What it blocks:** the survivor-story fallacy — running twenty ideas, publishing the one that worked, and forgetting the nineteen bodies.

### 8. Adversarial review waves — borrowed from red teams and independent design review

**What it is here:** after a build, several reviewer agents with different specialties (correctness, data integrity, security of the data flow, an adversary constructing failure scenarios) attack the code in parallel — and every serious finding then goes to a *second* agent whose explicit stance is "this finding is wrong until the code proves it." Findings citing population statistics must now state the population measured (a rule born from catch #3 above).

**The ancestor:** independent design review in aerospace, red teams in security: the builder's eye cannot see the builder's blind spots, structurally.

**Earned its keep:** tonight's wave on the R-1b execution path: 13 agents, 4 confirmed serious findings (including a hole where partial benchmark coverage would have silently violated the vintage pin, and a gap where a wrong-vintage matrix would have passed the sidecar check), and 5 *refuted* findings that the adversarial verifiers correctly killed before they wasted fix effort.

### 9. Telemetry and followable progress — borrowed from flight telemetry

**What it is here:** every long-running job writes a live log file in its artifact directory; the rule is "John can always follow progress." Tonight John watched core utilization in Activity Monitor and caught a single-core bottleneck *from outside the system entirely* — out-of-band human telemetry.

**The ancestor:** you don't fly without downlink. A vehicle that can't tell you what it's doing is a vehicle you learn about only at the crater.

### 10. Postmortems and premortems — borrowed from blameless-postmortem culture and risk premortems

**What it is here:** big arcs get a plain-English postmortem (what happened, what we learned, written for a non-expert). And this very document is a **premortem** — a technique from decision science: *assume the project already failed, then write the story of how.* It surfaces risks while they're still cheap.

Which brings us to the question underneath all ten practices.

---

## Part 3 — Stochastic components, deterministic systems (why this works with AI in the loop — and always worked with humans)

A common complaint about building anything serious with AI: *"LLMs aren't deterministic"* (deterministic = same input, same output, every time; stochastic = there's randomness in what comes out). The complaint is factually right and misses the point entirely — because it demands determinism from the **component**, when determinism only ever belonged to the **system**.

Here is the uncomfortable truth the complaint skips: **engineering never had deterministic components.** Humans are stochastic. Tired engineers transpose digits. Experienced pilots skip checklist items. Brilliant researchers fool themselves — Feynman again: *"the first principle is that you must not fool yourself, and you are the easiest person to fool."* Aviation and aerospace did not respond to this by demanding deterministic humans. They built systems whose *outputs* are deterministic and verifiable despite being produced by unreliable parts. That is what a checklist *is*. That is what independent verification *is*. John von Neumann wrote the founding paper on the idea in 1952 — building reliable machines from unreliable components — and every practice in Part 2 is a descendant of it.

This project was built by AI agents, and the night before R-1b's full run those agents made (at least) five genuinely stochastic mistakes: an invented anchor list, a test fixture that fabricated a field the real data didn't have, a function that couldn't cross a process boundary, a date validator that demanded market data for a Saturday, and a comparison routine tripped by the one number that never equals itself (NaN). A different session might have made none of these, or five different ones. **Every single one was caught by the machinery in Part 2** — not by luck, and not by the agents being careful, but by gates that refuse to pass unverified work.

And what came out the other side is deterministic in the strictest sense the word has: the same frozen seed plus the same frozen data vintage produces **the same result to the last binary digit** — proven identical across different CPU architectures, across independent implementations of the same calculation, across serial and parallel execution. The stochasticity of the builders was consumed by the gates. The artifact that survives is *more* reproducible than most human-written research code has ever been.

There's a second half, and it matters just as much: the randomness is a **feature** in the right phase. The adversarial review wave works *because* thirteen agents vary — different perspectives surface different flaws, and the independence of the verifiers is precisely what makes their agreement evidence. Variance is what you want during *generation and search*; determinism is what you demand at *verification and commitment*. Stochastic explore, sealed confirm. The development process and the research design are the same shape, and that is not a coincidence.

So the honest framing is: **an LLM without verification machinery is a stochastic system; an LLM inside verification machinery is a deterministic system with a stochastic search heuristic inside it.** The second thing is just engineering — and it's the reason this document's conclusions hold *no matter what R-1b's final number says*. The result may confirm or it may not; either way it will be a number we can trust, reproduce, and reason from. That property was never going to come from the model. It comes from the system.

Which brings us to Part 4.

---

## Part 4 — The premortem: it's June 2027 and R-1b fooled us. What happened?

R-1b ran, the gates passed, the verdict said CONFIRMED, money was traded, and it lost. Here are the most plausible autopsy findings — and, honestly, which ones our defenses cover versus where we're still exposed.

**1. "The universe had no dead companies in it."** *Our biggest known, open exposure.* The 4,678-stock universe contains **zero delisted names** — every company that died or got acquired before our data fetch simply isn't there (free price sources don't carry the dead). Insider buying at companies that later *died* would have produced losing trades our backtest never saw. We've quantified this (it's why F318 — paid survivorship-complete data — stays open), the harness handles deaths *correctly when it sees them* (terminal-value semantics), but it can't see deaths absent from the data. **Status: known, documented, NOT fixed. The verdict language must carry this caveat, and a confirmed signal should be sized with it in mind.**

**2. "The timestamps lied — the market knew before we thought it did."** A filing timestamped Tuesday evening that actually became public Wednesday would let the backtest buy a day early. **Status: well defended.** Tonight's census checked every suspicious timestamp in the study population against the SEC's own website: all genuine. The entry rule (next market open *after* the public timestamp) is conservative by construction.

**3. "We peeked without noticing."** Some result leaked into a design decision. **Status: structurally defended** — blind authors, sealed confirm, fingerprinted charters, probes that print only equal/not-equal. The residual risk is *social*: the human (or orchestrator) carrying impressions across sessions. The clean-supersession decision for R-1b (rather than amending R-1) was made exactly to keep this surface minimal.

**4. "One ticker, one bad join."** A symbol mapped to the wrong instrument (we found Navient's map pointing at a $25 bond note), an amendment double-counted, a duplicate filing. Any one event is noise; a *systematic* join error biases everything. **Status: actively defended** — the ingest counts every drop, dedup, collision, and fallback rather than silently fixing them, and the four known data-source quirks are stated as binding facts in the charter. Residual: unknown unknowns in SEC's own data entry.

**5. "The test was too weak, and we believed a fluke."** With few events, a lucky spread between top and bottom buckets passes a bar it shouldn't. **Status: strongly defended** — this is exactly what killed R-1, on purpose: the power gate (the "MDE" check — the smallest edge the test could reliably detect) reported the test was blind below 60 percentage points, and the run aborted rather than report anything. The same gate arms R-1b.

**6. "It worked in-sample, in one era, in one market weather."** The effect existed 2015–2020 but was a regime artifact (e.g., only in the 2016–2018 melt-up). **Status: defended in layers** — era-consistency blocks, the regime lens (effect reported per market-weather state), the sector-peer lens (did it beat its industry or just ride it?), the perturbation band (the verdict must survive small wiggles in every frozen constant), and a sealed 2021–2024 confirm window. No single layer is decisive; jointly they make a one-era fluke hard to crown.

**7. "Costs ate it."** A 4-basis-point round-trip cost model is honest for liquid names but optimistic for the small-caps where insider signals concentrate; at ~$10k account scale, per-trade frictions are first-order. **Status: partially defended.** The cost model is frozen and applied, the H2 "tradeable-threshold" test exists precisely to ask whether the edge survives real-world implementation — but the model itself is simple. A confirmed signal's playbook must re-check costs against the *actual* names it would trade.

**8. "The instrument itself was buggy in a way all our tests shared."** The deepest fear: a flaw in the harness replicated into every fixture and probe (the F338 lesson: fixtures share the implementer's blind spots). **Status: mitigated, never eliminated** — real-data probes with pre-stated anchors, cross-host bit-identical builds, equivalence proofs between independent implementations (live path vs matrix; serial vs parallel; XML path vs TSV path cross-diffed at 99.1% with every divergence explained). Each independent re-derivation that agrees shrinks the space where a shared bug can hide.

**The premortem's bottom line:** items 2–6 and 8 are covered by machinery that demonstrably fires (it fired all night). Item 7 needs a re-check at playbook time. **Item 1 — survivorship — is the one we'd write on the wall.** If R-1b confirms, the result inherits a universe where nobody dies; the honest reading is "edge among the survivors," and the F318 decision (paid delisted data) graduates from nice-to-have to prerequisite-for-sizing.

---

## Jargon glossary (footer)

**Backtest** — replaying a strategy against historical data to see how it would have done. **Look-ahead bias** — the backtest using information before the market actually had it. **Peeking** — adjusting the rules after seeing results, which invisibly fits the strategy to the past. **Pre-registration** — publicly freezing a test's rules before running it. **Charter** — our pre-registration document, fingerprinted and frozen. **sha256** — a digital fingerprint of a file; changes if one character changes. **Blind author** — an agent writing charter text while barred from reading any results. **Confirm window** — held-out data (2021–2024) the strategy never touches until one final, sealed test. **Anchor** — a sanity check on an instrument's output, written down before the instrument runs. **NOT-RUN** — the honest status of a check that couldn't execute; never reported as a pass. **F338** — the house rule that new instruments must pass real-data anchors before their output is believed. **Configuration management** — knowing exactly which version of everything produced a result. **Returns matrix** — our frozen snapshot of every stock's forward returns; the unchanging ruler. **Vintage** — which day's download of the data you're holding (vendors restate history). **IV&V** — independent verification & validation; the checker is not the builder. **Bit-identical** — equal to the last binary digit, not merely "close". **NaN** — "not a number," the placeholder for missing values; by the floating-point standard it never equals itself, even itself. **Bootstrap** — estimating uncertainty by resampling the data many times (ours: 999 draws, fixed seed). **Seed** — the starting value that makes "random" draws exactly reproducible. **MDE** — minimum detectable effect; the smallest true edge a test could reliably see — if that's bigger than any edge worth trading, the test is declared too weak and aborted. **FDR ledger** — the append-only count of every hypothesis ever tested, so luck can't masquerade as skill. **Multiplicity** — testing many things and crowning the lucky one. **Regime / market weather** — the day's broad-market state (calm uptrend / neutral / risk-off / crisis), measured purely from price. **Perturbation band** — the pre-registered set of small nudges to frozen constants under which a verdict must stay stable. **Survivorship bias** — datasets that only contain companies that lived; the dead leave no prices in free data. **Terminal-value semantics** — how the harness scores a position when a stock dies mid-holding (it takes the loss; it doesn't pretend the trade never happened). **Premortem** — assuming failure already happened and writing its story, to find risks while they're cheap. **basis point (bps)** — one hundredth of a percent; 4 bps = 0.04%. **Deterministic** — same input always produces the same output. **Stochastic** — randomness in the output; same input may produce different results. **LLM** — large language model; the kind of AI that built this project, stochastic by nature. **Von Neumann (1952)** — "Probabilistic Logics and the Synthesis of Reliable Organisms from Unreliable Components," the founding paper on getting dependable systems out of undependable parts.
