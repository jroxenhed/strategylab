# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-07 (F359 closure session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

**F357 is DONE and ACCEPTED**: the universe returns matrix (8,909,310 rows; 2,745/4,678 tickers × 2015–2024 × 21/63/126td) is the program's canonical forward-returns source. It was built twice — M1 and the newly-commissioned 14900k WSL worker — **bit-identical across hosts**, after a 30× perf fix (probe-diff-exact). The full-scale equivalence test vs the live path (real 113-event R-1 study) measured the only divergence at ~1e-5 ppt in fwd_excess, isolated to Yahoo cache-vintage drift — which the matrix permanently fixes by freezing one vintage. John accepted at the gate; all facts in PROGRAM.md ("F357 matrix SHIPPED + ACCEPTED"). Both F354 prerequisites (F356 ingest, F357 matrix) are now closed — **the R-1 rerun gate is John's decision away**, pending F359 first.

## Done (2026-06-07, the night session)

- **F357 shipped end-to-end** (perf commit `54c06fb` + close-out commit): 30× fix, double build, cross-host bit-identity, full-scale equivalence accepted, face-validity anchors (holidays/distributions/survivorship-quantified/seal-nuance). JOURNAL has the full arc.
- **strategylab-worker commissioned**: WSL2 Ubuntu, full venv mirror, git clone in place, runbook rewritten from measured reality. ~6× faster than the M1 on the matrix build (5.2 vs 21.3 min).
- **review-wave workflow adopted + piloted** (`.claude/workflows/review-wave.js`): playbook Tier-B/C review waves now run as one Workflow-engine call (schema findings, adversarial verify per P0/P1, `.run/` artifacts). Pilot on the perf commit ran same night; F362 tracks run-state usage integration.
- Earlier same calendar day: playwright-mcp shakedown (separate session — see that JOURNAL entry).

## Next actions

1. **F359 CLOSED (2026-06-07): midnight-UTC ADTs are GENUINE — keep all events.** Census of all 19 study-population events (not 0.6% — that was whole-cache incl. pre-2003 date-only era; study population is 19/~1.16M) vs EDGAR web: 19/19 agree to the second (19:00/20:00 ET on the filing date = 00:00 UTC next day); no look-ahead even counterfactually. No exclusion/clamp/code change. Verdict: `.run/F359/VERDICT.md`. F364 (new) carries the process lesson.
2. **F354 — the only remaining gate is the charter + John's approval.** GATE DECIDED (John, 2026-06-07): clean R-1b charter, not a §10 amendment (rationale + locked requirements in PROGRAM.md "F354 GATE DECIDED"). Next concrete step: **blind-author the R-1b charter** (R-1 design verbatim, F356 ingest as source, matrix-build pin "universe medians from matrix build 2026-06-07", FS anchors re-attach) → John approves → run explore at full scale on the worker (check F355 bootstrap perf first — wall-clock dominator at thousands of events).
3. **R-2 explore** (distress recovery, charter `2f0cf24c…`) — needs NO Form 4 data; runnable any session; confirm window 2025+.
4. **Open hardening:** F361 (coverage accounting wrong under chunk failure — cosmetic but dishonest logging), F362 (review-wave ↔ run-state usage), F358 (universe-loader consolidation, now 3+ copies), F352 (ledger file lock), F360 (re-measure MCP snapshots at populated chart state), F364 (review findings must state the population their statistics were measured on — the F359 lesson).

## Operational notes for the next session

- **Worker**: `ssh john@strategylab-worker` lands in Windows cmd; everything runs via `wsl bash -lc "..."`. Repo at `~/strategylab` (git clone, pull don't tar). Venv at `backend/venv`, full requirements installed. Price cache + edgar_cache synced as of tonight. **nohup dies with wsl.exe** — hold the SSH session open from the Mac side for long runs (see rewritten `bin/build-returns-matrix-remote.sh`).
- **Matrix artifact**: `backend/data/universe_matrix.parquet` (96MB dir incl. `_meta.json` sidecar; gitignored). Worker holds its own bit-identical copy (`universe_matrix_run2.parquet`). Loader refuses partial artifacts; sidecar has ticker-coverage accounting — **a nonzero `no_frame_count` means rerun and compare before trusting**.
- Run telemetry: `.run/F357/` (probe logs, equivalence diffs incl. `full-equivalence-diff.txt` with the 41 vintage-drift rows, runbook session logs, review-wave artifacts).
- The CPU-time-proxy progress trick (for legacy log-silent processes): `ps -C python3 -o cputimes=` sampled in a Monitor loop — used tonight on the live-path leg; retire it as instruments gain progress files.

## Working agreements worth remembering

Plain language always (define every term inline). Blind authors for all charter text. F338: pre-stated anchors before believing any instrument; skipped checks report NOT-RUN, never PASS. §10 pre-outcome fixes are legal but John approves anything touching a charter he signed. Ledger writes are explicit-path-only. Promote durable findings immediately. Long-running tasks always followable. **New tonight: decisiveness applies within agreements, never to them — plan deviations get flagged before acting; probe before estimating; John wants compute offloaded to his hardware (the worker earns its keep).**
