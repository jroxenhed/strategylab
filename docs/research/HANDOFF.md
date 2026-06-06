# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-06 late (the gate-approval + preconditions session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

John approved both charters at the gate (R-1 insider clusters, R-2 distress recovery), held the Sharadar spend, and named the program **The Desk**. Every R-1 precondition then shipped same-day: the sector-peer lens (F349, SIC cache = 100% of the SEC registry, 8,007 CIKs), the regime lens (F350 — counted from the real file: RISK_OFF is the rare 6-day "crisis" state, not STRESS), and the median-performance fix (F351 — shared per-date return vector, equivalence-diff byte-identical, F331 prefetch wired in). The harness ran on real data for the first time (17 Form 4 events) and passed its F338 plumbing gate 9 PASS / 1 NOT-RUN. **R-1's explore run is fully unblocked and is the next action.**

## Done (2026-06-06, this session)

- **Gate decisions recorded** — R-1 + R-2 APPROVED (shas in PROGRAM.md); Sharadar HELD; name locked: The Desk.
- **F349 + F350** — both lenses in `event_study.py`; 16 review findings fixed (headline: triple-confirmed str-vs-int key killing `peer_median_excess_pct`); smoke probe reports three honest states (PASS / FAIL / NOT-RUN — skipped ≠ passed).
- **F351** — universe + peer medians from one shared `_ReturnVector` per (date, horizon); leave-one-out = dict-drop; equivalence-diff verified twice (pre- and post-review-fixes). 83 tests.
- **F331 + F336 + F305** — prefetch (6 workers, ~5 req/s pacing, circuit breaker → sequential fallback), cache staleness/manual-prune-with-guardrails, atomic TODO tooling writes. 97 tests 3× stable, probe 8/8.
- **First real study artifact** — `backend/data/turnaround/event_studies/form4_smoke_2015_2020/` (driver: `backend/research/run_smoke_study.py`; FDR writes redirected to a smoke ledger — the real ledger is still pristine/nonexistent until the first real explore).
- **Stuck-agent watchdog** added to the orchestrator playbook after the smoke-driver loop (~35 min, est. 300–400k tokens; John's catch).

## Next action: R-1 explore run

1. Re-read the R-1 charter (`docs/plans/2026-06-06-R1-charter-DRAFT.md`, sha `517ddd4f…`) — run exactly what's pre-registered: frozen continuous score, monotonicity headline, perturbation band, sampling clock + MDE gate (`power_audit.py`) before interpreting.
2. **Re-evaluate the NOT-RUN statistical anchors first** (regime distribution ≥50 events; peer fallback rate <20%; peer/universe corr >0.6 at full scale) — they are a precondition on believing the explore output (F338).
3. Explore window 2015–2020 ONLY; 2025+ stays sealed. This run writes the REAL FDR ledger for the first time — F352 (ledger file lock) is open; single-writer discipline until then.
4. R-2 explore can follow immediately after (its confirm window is 2025+, the long clock).

## Open items relevant to The Desk

- **F352** — FDR ledger file lock (pre-existing gap; single-writer until fixed).
- **F348** — fundamental-surprise payloads (unlocks PEAD-family charters); ungated.
- **Explore mill** — standing screener for charters 3+ (must come from OUTSIDE the insider/crashed-stock lineage — diversification guard).
- **Desk product surface** — stays in IDEAS.md until a first charter confirms.

## Working agreements worth remembering

Plain language always (define every term inline). Blind authors for all charter text. F338: pre-stated anchors before believing any instrument; skipped checks report NOT-RUN, never PASS. Stuck agents: expected duration in every dispatch, 2-attempt cap, bounded transcript peek at 2× budget, kill + salvage from disk. Promote durable findings immediately.
