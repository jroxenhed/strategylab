# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-07 (the R-1b full-run session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

**R-1b ran at full scale and the verdict is UNTESTABLE-underpowered — on an instrument that passed all 15 of its anchors.** The insider-cluster dose-response premise produced its strongest evidence yet (Q5−Q1 +3.28pp p=0.040, Q5 absolute +3.87pp p=0.021, Spearman 0.80, 3/3 FDR-survived, 9/9 perturbation-stable, peer lens agrees at +3.76pp), but the §3a power gate binds: MDE 3.40pp vs the 1.0pp abort floor → confirm window SEALED, nothing confirmed, no alpha spent. Reaching 1.0pp needs ~12× more valid events than 2015–2020 Form 4 P-buys contain. The full record (funnel, anchors, ledger, launch drama) is in PROGRAM.md under "R-1b EXPLORE RAN AT FULL SCALE". The artifact lives at `backend/data/turnaround/event_studies/r1b_insider_clusters_explore_2015_2020/` (synced to M1); the FDR ledger is at 5 entries on both hosts.

## Done (2026-06-07, this session)

- **F359 CLOSED** — midnight-UTC timestamps genuine (19/19 EDGAR-web census), keep all. The 0.6% headline was whole-cache scope error → F364 (population-scope rule, now in playbook + review-wave prompts).
- **R-1b charter blind-authored, John-approved, sha-pinned** (`ff1d329c…`); SEED kept verbatim (pre-data provenance argument).
- **Execution path built + review-waved + gate-calibrated**: `build_r1b_events` (ingest-sourced dose builder), `run_r1b_explore.py` (sha + matrix-vintage pre-flight gates), `matrix_strict` point-of-use enforcement. 4 P1s fixed from the 13-agent wave.
- **F355/F365/F366 shipped** (bootstrap vectorization, parallel harness, parallel ingest) — every change bit-exactness-proven before use. John's standing directive recorded: parallelize-by-default, fix failing parallel paths rather than fall back.
- **2,861-company companyfacts prefetch** (16 min, 0 errors) after the pre-launch coverage check caught 95% of events scoreless against R-1's 312-CIK derived cache.
- **Full run on the worker** (attempt 2; attempt 1 killed when a WARNING revealed missing regime_states.json + real FDR ledger → F368). §7 probe 15/15. Artifact + ledger synced back.
- **Premortem doc** (`docs/postmortems/2026-06-07-r1b-premortem-mission-critical-practices.md`): mission-critical practice mapping + failure-mode autopsy + the stochastic-components/deterministic-systems section (John's framing). Worth reading before designing R-1c.

## Next actions

1. **The R-1c question (John's gate, unhurried):** the premise now has strong-but-uncallable evidence. Options on the table: longer period (Form 4 datasets go back to 2006 — more events, but era heterogeneity grows), relaxed floors with stated caveats, or park the family and run R-2 first. The premortem's survivorship item (universe contains zero dead companies) should weigh on any R-1c design — and on how seriously to read the +3.3pp at all.
2. **R-2 explore** (distress recovery, charter `2f0cf24c…`) — needs NO Form 4 data; runnable any session; confirm window 2025+ only. The natural next run while R-1c is pondered.
3. **F368 [next]** — worker pre-flight data manifest + sync script (the two-missing-artifacts near-miss). Include the dose-stage disk-only loader opt-in (~8 min of doomed yfinance 404s per worker run otherwise).
4. **F367 [easy]** — STRESS regime cells must print non-evidential regardless of n (charter §2e; cosmetic, zero verdict impact).
5. Open hardening carried: F361, F362, F358, F352, F360, F364 (review-wave.js half done — population-scope rule shipped 2026-06-07; playbook half also done; item can probably close after a doc-check).

## Operational notes for the next session

- **Worker**: fully commissioned for research runs now — code at HEAD, matrix symlinked (`universe_matrix.parquet → universe_matrix_run2.parquet`, F357-verified bit-identical), price cache 4.1GB, submissions+older_pages, form4 datasets, derived/v1 (3,076 files), regime_states.json, real fdr_ledger.json all present. **But F368 first before trusting any future launch** — gitignored data artifacts do NOT travel with git pull.
- **Ledger discipline**: fdr_ledger.json is now 5 entries; pre-R-1b backup at `fdr_ledger.json.pre-r1b.bak`. Cross-host rule used tonight: worker ledger must be a verified clean extension of M1's before install — never blind-overwrite.
- **Monitoring pattern that worked**: remote runs watched via run.log tail loop over SSH + the CPU-time-proxy (`ps -o cputimes` delta) for liveness; `tail -N` on the remote pipe buffers until exit (known anti-pattern, bit us again) — the driver's own run.log is the real channel.
- R-1b study artifact: `event_studies/r1b_insider_clusters_explore_2015_2020/` (events.ndjson, meta.json, r1_explore_verdict.json, r1b_meta.json, run.log). Run telemetry in `.run/F354/`, `.run/F365/`, `.run/F366/`.

## Working agreements worth remembering

Plain language always (define every term inline). Blind authors for all charter text. F338: pre-stated anchors before believing any instrument; skipped checks report NOT-RUN, never PASS. §10 pre-outcome fixes are legal but John approves anything touching a charter he signed. Ledger writes are explicit-path-only; cross-host ledger installs require clean-extension verification. Promote durable findings immediately. Long-running tasks always followable. Decisiveness applies within agreements, never to them. Probe before estimating. **Parallelize by default — "it's a one-shot run" is not a valid serial justification; fix failing parallel paths, don't discard them (John, 2026-06-07).** Population statistics in findings must state the population measured (F364).
