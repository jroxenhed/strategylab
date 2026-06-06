# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-06 (the post-Gate-9 pivot session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

The old quarterly-clock program closed with zero confirmed edges — and we then proved (F340) its design couldn't have detected a realistic edge anyway (minimum detectable effect ~3.0pp; its own momentum result was 1.9pp). The new program ("Radar" — name pending John's blessing) tests reactions to *events* (filings, insider trades, ratings) at their actual arrival time. Its engine is built, panel-reviewed, and hardened; its first two experiments are drafted and waiting at John's gate. The product end-state is captured in IDEAS.md: an event inbox in Discovery with conviction-graded playbook cards, paper-tracking every signal forever.

## Done (2026-06-06)

- **`backend/research/event_study.py`** — event-time harness, 56 tests. Entry next open after public timestamp; block-bootstrap primary test; persistent FDR ledger; MDE self-report; era-consistency blocks; delisting = terminal-value exits both sides; 2025+ hard-guarded as a future fresh confirm window. Survived a 4-persona panel (36 findings, 7 P0 — all fixed with regression tests).
- **`backend/research/power_audit.py`** — measures any design's detection floor (the F340 instrument).
- **`backend/research/outcome_table.py`** — 9,527-row table from the dead experiments; surfaced the D2-reversal-as-long candidate.
- **`backend/edgar.py` derived cache (F320)** — fundamentals reads ~instant, identity-verified, lookahead-safe.
- **Data audits** — GO: SEC fails-to-deliver (2009+), SEC Financial Statement Data Sets (2009+, as-filed dates), FINRA short interest (~2018+), yfinance rating actions (~13yr, determinism-probed), EDGAR 8-K already on disk (19.6k earnings + 14.7k press releases). NO-GO: Stooq delisted prices (recycled tickers), Finnhub/FMP free tiers.
- **Methodology codified in PROGRAM.md** — the lens stack (universe + peer + era + regime + perturbation band), dose-response over cliff-edges, instrument-first budgeting, premise-family diversification guard, "charter the clock + state the MDE". The price-leads axiom honestly demoted to working hypothesis.
- **Charters R-1 + R-2 drafted blind, iterated v1→v4 on John's critiques** — final shas R-1 `517ddd4f…`, R-2 `2f0cf24c…`, in `docs/plans/2026-06-06-R*-charter-DRAFT.md`.

## Awaiting John (the gate)

1. **R-1 insider-cluster charter** — read §0 (plain English, ~3 min) → approve / edit / reject. Approval triggers: build F349+F350 (charter preconditions), extend SIC coverage, run explore + MDE gate; sealed confirm only if gates pass.
2. **R-2 distress-recovery charter** — same; note its confirm window is 2025+ only (starts a long clock; explore runs immediately).
3. **Sharadar $50/mo** — only path to delisted prices; defer until a charter needs it (none current does).
4. **The name** — "Radar" is the standing proposal.

## Next work, independent of the gate

- **F349 + F350** — sector-peer benchmark + regime lens in the harness (charter-blocking; build first regardless).
- **F348** — fundamental-surprise event payloads (revenue acceleration, dilution) on the derived cache → unlocks PEAD-family + F347 conditioning.
- **Explore mill** — the standing instrument that screens premise-grid cells on 2015–2020 only, FDR-ledgered; the long-term source of charters 3+ (diversification guard: they must come from OUTSIDE the insider/crashed-stock lineage).
- **F331 + F336** tagged `[next]` for overnight (price prefetch, cache staleness).
- **Radar desk** stays in IDEAS.md until a first charter confirms; its two charter-independent pieces (live EDGAR poller on the harness schema; paper-tracking tier) can graduate to F-items any time.

## Working agreements worth remembering

Plain language always (define every term inline). Orchestrator-with-judgment over scripted fan-out, even for builds. Blind authors for all charter text — the main session has seen outcomes and must never write charter content. Every instrument needs a real-data smoke probe with pre-stated anchors (F338) before its output is believed.
