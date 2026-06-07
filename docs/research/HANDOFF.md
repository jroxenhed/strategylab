# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-07 (the F357-completion night session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

**F357 is DONE and ACCEPTED**: the universe returns matrix (8,909,310 rows; 2,745/4,678 tickers × 2015–2024 × 21/63/126td) is the program's canonical forward-returns source. It was built twice — M1 and the newly-commissioned 14900k WSL worker — **bit-identical across hosts**, after a 30× perf fix (probe-diff-exact). The full-scale equivalence test vs the live path (real 113-event R-1 study) measured the only divergence at ~1e-5 ppt in fwd_excess, isolated to Yahoo cache-vintage drift — which the matrix permanently fixes by freezing one vintage. John accepted at the gate; all facts in PROGRAM.md ("F357 matrix SHIPPED + ACCEPTED"). Both F354 prerequisites (F356 ingest, F357 matrix) are now closed — **the R-1 rerun gate is John's decision away**, pending F359 first.

## Done (2026-06-07, the night session)

- **F357 shipped end-to-end** (perf commit `54c06fb` + close-out commit): 30× fix, double build, cross-host bit-identity, full-scale equivalence accepted, face-validity anchors (holidays/distributions/survivorship-quantified/seal-nuance). JOURNAL has the full arc.
- **strategylab-worker commissioned**: WSL2 Ubuntu, full venv mirror, git clone in place, runbook rewritten from measured reality. ~6× faster than the M1 on the matrix build (5.2 vs 21.3 min).
- **review-wave workflow adopted + piloted** (`.claude/workflows/review-wave.js`): playbook Tier-B/C review waves now run as one Workflow-engine call (schema findings, adversarial verify per P0/P1, `.run/` artifacts). Pilot on the perf commit ran same night; F362 tracks run-state usage integration.
- Earlier same calendar day: playwright-mcp shakedown (separate session — see that JOURNAL entry).

## Next actions

1. **F359 [next]** — midnight-UTC ADT investigation (~20-sample check vs EDGAR web) BEFORE any R-1 rerun interpretation; events carry `adt_midnight_utc` flags so exclusion needs no re-ingest. The last technical prerequisite before F354.
2. **F354 — R-1 rerun gate (John's decision).** Both prerequisites closed. Decision inputs: the four F356 measured divergences + the F357 facts (both in PROGRAM.md). Whatever the §10-amend vs R-1b call, the charter text must **pin "universe medians from matrix build 2026-06-07"** (vintage freeze — see equivalence verdict). The rerun's compute target is the commissioned worker; check F355 (bootstrap vectorization) before full scale — it's the wall-clock dominator at thousands of events.
3. **R-2 explore** (distress recovery, charter `2f0cf24c…`) — needs NO Form 4 data; runnable any session; confirm window 2025+.
4. **Open hardening surfaced tonight:** F361 (coverage accounting wrong under chunk failure — cosmetic but dishonest logging), F362 (review-wave ↔ run-state usage), F358 (universe-loader consolidation, now 3+ copies), F352 (ledger file lock), F360 (re-measure MCP snapshots at populated chart state).

## Operational notes for the next session

- **Worker**: `ssh john@strategylab-worker` lands in Windows cmd; everything runs via `wsl bash -lc "..."`. Repo at `~/strategylab` (git clone, pull don't tar). Venv at `backend/venv`, full requirements installed. Price cache + edgar_cache synced as of tonight. **nohup dies with wsl.exe** — hold the SSH session open from the Mac side for long runs (see rewritten `bin/build-returns-matrix-remote.sh`).
- **Matrix artifact**: `backend/data/universe_matrix.parquet` (96MB dir incl. `_meta.json` sidecar; gitignored). Worker holds its own bit-identical copy (`universe_matrix_run2.parquet`). Loader refuses partial artifacts; sidecar has ticker-coverage accounting — **a nonzero `no_frame_count` means rerun and compare before trusting**.
- Run telemetry: `.run/F357/` (probe logs, equivalence diffs incl. `full-equivalence-diff.txt` with the 41 vintage-drift rows, runbook session logs, review-wave artifacts).
- The CPU-time-proxy progress trick (for legacy log-silent processes): `ps -C python3 -o cputimes=` sampled in a Monitor loop — used tonight on the live-path leg; retire it as instruments gain progress files.

## Working agreements worth remembering

Plain language always (define every term inline). Blind authors for all charter text. F338: pre-stated anchors before believing any instrument; skipped checks report NOT-RUN, never PASS. §10 pre-outcome fixes are legal but John approves anything touching a charter he signed. Ledger writes are explicit-path-only. Promote durable findings immediately. Long-running tasks always followable. **New tonight: decisiveness applies within agreements, never to them — plan deviations get flagged before acting; probe before estimating; John wants compute offloaded to his hardware (the worker earns its keep).**
