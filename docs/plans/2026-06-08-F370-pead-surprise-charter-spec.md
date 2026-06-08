# F370 — PEAD / earnings-surprise charter — DESIGN SPEC

*This is the design spec (non-blind: orchestrator + John's design decisions). The CHARTER itself (frozen, hashed, pre-registered) is authored separately by an outcome-blind agent from this spec, then John approves + locks. Load alongside [PROGRAM.md](../research/PROGRAM.md). Authored 2026-06-08 via the brainstorming flow with John.*

## One-paragraph premise (plain English)
After a company files its quarterly/annual report (10-Q / 10-K), does the stock keep drifting in the direction of how that quarter's fundamentals changed **versus the company's own past** — revenue/earnings growth, acceleration, margin shift, dilution — measured with **only what was public on the filing day** (no analyst estimates, no look-ahead)? This is post-earnings-announcement drift (PEAD), tested estimate-free on the F348 surprise payload. The F369 census says this is the one event family with enough events to (potentially) resolve a tradeable edge.

## Why this family (census grounding)
- PEAD 10-Q/10-K: n_valid ≈ 13,828; one-sample MDE **0.56pp**; F371 placebo event-specific net **+0.56pp**.
- It is the **only** family where F348's fundamental-surprise dose attaches *natively*: the 10-Q/10-K filing is exactly what populates the derived cache, so the current quarter's numbers are point-in-time available at the filing timestamp. (8-K 2.02 announces earnings *before* the XBRL is filed → F348 can't dose it cleanly; 8-K 8.01/5.02 aren't earnings events.)

## The power bind (the central design constraint — read before anything else)
The census measured TWO MDEs for this family (MDE = smallest detectable edge; floor = 1.0pp = smallest edge worth trading at John's account scale):
- **One-sample ("high-surprise beats market"): MDE 0.56pp — powered, BUT F371 proved this is mostly a size/survivorship baseline artifact, not signal.** Powered-but-contaminated.
- **Dose-response Q5−Q1 ("drift scales with surprise"): MDE ≈ 1.8pp (√10 × one-sample) — the CLEAN test (the quintile gap cancels the common baseline automatically, F371), but above the 1.0pp floor.** This is the exact wall R-1b hit (testable binary, underpowered dose-response).

**Resolution (two parts):**
1. **Standardized SUE as the power lever.** Dividing the earnings surprise by the company's OWN historical surprise-volatility sharpens the dose ordering → wider Q5−Q1 spread for the same true effect → real power gain. (Also the textbook Bernard–Thomas SUE.)
2. **Explore IS the power gate.** `power_audit.py` reports the real design MDE before lock; explore measures the ACTUAL Q5−Q1 per dose. A dose graduates to confirm **only if its explore effect clears its own MDE** (a real, *callable* edge) AND is sign-stable across sub-eras. If none clear it, F370 ends at explore — cheaply, honestly — rather than burning a confirm to relearn the wall. This makes it structurally impossible for F370 to repeat R-1b's wasted confirm.

## Dose candidates (explore tries all; ONE graduates to confirm)
All computed point-in-time from F348 at the filing date (`as_of` = filing ET date):
1. `earnings_yoy` — seasonal-random-walk earnings surprise (canonical estimate-free SUE proxy). [F348 present]
2. `revenue_yoy` — revenue surprise (higher coverage 72%, harder to manage). [F348 present]
3. `composite` — one frozen standardized blend (e.g. z(earnings_yoy) + z(revenue_accel) + z(net_margin_infl_pp) − z(dilution_yoy); exact formula frozen + hashed in the charter). [F348 present]
4. `std_sue` — `earnings_yoy` ÷ (company's own historical stdev of past earnings_yoy surprises, PIT). **Requires a small F348 add** (one PIT field: trailing surprise-volatility from the derived cache). [F348 ADD — build only if staged feasibility warrants, see below]

### Staged execution (probe before building)
- **Explore-0 (zero-build feasibility):** run doses 1–3 (no F348 change) on the explore window; read the Q5−Q1 effect *magnitudes*.
  - If all are dead-flat (magnitudes far below ~1.8pp and small in absolute terms), **stop** — SUE sharpens ordering, not the underlying drift magnitude; it won't rescue a flat effect. F370 closes at explore-0 as "PEAD-on-filing shows no callable dose" (a real finding consistent with the price-leads-filings axiom).
  - If any dose shows a rescuable magnitude (effect near/above its MDE, or a clear monotone climb), **build the F348 std_sue field and run the full explore** (doses 1–4).
- **Heavier power levers held in reserve (F370c, only on a near-miss):** extend n with pre-2015 years (un-pins the F357 matrix; +√2 power), or a differenced/placebo-net outcome (variance reduction). Not front-loaded.

### Graduation rule (pre-stated, applied to explore output — NEVER peeking at confirm)
A dose graduates to the single confirm touch iff ALL hold on the **explore** window:
1. Q5−Q1 dose-response is statistically significant (block-bootstrap p, BH-FDR-ledgered) AND its point estimate **> the design MDE** from `power_audit.py` (callable, not just significant).
2. Monotonic climb across quintiles (Spearman ρ over quintile means, sign-correct).
3. Sign-stable across explore sub-eras (era-consistency rule #4) and across the perturbation band.
4. Coverage ≥ 50% of in-universe events (so the dose is broadly applicable, not a thin-slice artifact).
If multiple doses qualify, the one with the **largest MDE-clearing margin** graduates (tie-break: higher coverage). If none qualify → close at explore, no confirm.

## Outcome, windows, clock
- **Outcome:** forward excess return vs universe median (event_study.py harness standard); the Q5−Q1 dose-response on this is the **primary bar** (baseline cancels by construction).
- **Horizons:** 21 / 63 / 126 td; **primary = 63td** (classic PEAD drift window); 21/126 reported.
- **Universe:** matrix-pinned (4,678 floor-checked, "universe medians from matrix build 2026-06-07" — F357 vintage freeze).
- **Clock:** event-time; entry = next open after the filing's public timestamp (F342 COR-02 after-hours correction).
- **Explore window:** 2015-01-01 → 2020-12-31. **Confirm window:** 2021-01-01 → 2024-12-31. (2025+ stays hard-guarded as a future fresh-confirm reserve.)
- **Dedup:** same-ticker events within 30 calendar days collapse to first (rare for quarterly filings; matches R-1b).

## Lens stack (report-only honesty lenses; none add a pass/fail bar — §6a)
1. **Peer excess** (same-SIC siblings — beat the herd or ride it? fail → CONFIRMED-SECTOR-CARRIED).
2. **Era breakdown** (per explore sub-era sign agreement gates confirm; confirm reports per-era).
3. **Regime breakdown** (effect per market-weather state; crisis never load-bearing; single-regime → regime-carried flag).
4. **Perturbation band** (sign stability under small parameter wiggles — quintile boundaries, window ±, tolerance days).
5. **Announcement-to-filing drift lens (John's "measure the gap"):** for each event, link the 10-Q/10-K to its matching 8-K item 2.02 (same fiscal period; reuse census 8-K-2.02 identification) and measure the return from the 2.02 announcement date to the filing date. This **quantifies how much of the surprise's price impact predated our entry** — turning the "price leads filings" axiom into a measured number. Report the distribution + its correlation with the dose; report-only, never load-bearing.

## Gates & process (methodology-binding)
- **power_audit.py run before lock** (rule #3); the design MDE is stated in the charter.
- **F338 anchor probe** on the first explore artifact with pre-stated face-validity anchors before any number is interpreted (rule #2); skipped checks report NOT-RUN, never PASS.
- **Charter text is blind-authored** by an outcome-blind agent from this spec; **John approves before lock**; charter is hash-fingerprinted.
- **Confirm grading by a fresh sealed agent**; alpha/FDR-ledgered (explicit ledger path only; never default to the real ledger — rule #2 corollary).
- All compute on the worker by default (`bin/worker-dispatch.sh`); long runs log to a file in the artifact dir.

## Out of scope
No execution/Desk wiring (gated on first confirmed playbook). No paid consensus data. No new event family beyond 10-Q/10-K (8-K families deferred — content/timing mismatch with F348). No backtester changes.

## Deliverables (the build order this spec implies)
1. (If staged feasibility warrants) F348 `std_sue` add + its F338 micro-anchor.
2. F370 explore driver (all candidate doses, dose-response per dose, lens stack incl. the gap lens, power_audit, F338 anchors) — on the worker.
3. Blind-authored confirm charter (frozen winner per the graduation rule) — pending explore outcome + John's approval.
