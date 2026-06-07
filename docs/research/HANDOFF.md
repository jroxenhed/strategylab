# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-07 (the F356+F357 build session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

Both F354-rerun prerequisites moved this session. **F356 (Form 4 dataset ingest) is DONE**: `form4_ingest.py` parses the 45 quarterly SEC TSVs into dose-builder events, Tier C review wave fixed 2 P0s (4/A amendment double-count — supersession dedup, 14 dropped / 22 ambiguous kept+counted in 2018q1; wrong-instrument ticker resolution — issuer's filed symbol now wins over the map primary), final F338 probe 5/5 PASS (exact 7,718; cross-diff 99.1% / 321 forms / 3 classes; acceptance join 100%). **F357 (returns matrix) code is DONE, build is NOT**: builder/loader byte-identical to the live median path at calibration scale, completeness sidecar + partial-refusing loader, spawn path smoke-tested — the full 2015→2024 build on the 14900k is the open half. Four measured XML-path divergences are recorded in PROGRAM.md as F354 gate facts for John.

## Done (2026-06-07, this session)

- **F356 shipped** (commit `cdadc62`): ingest + 34 tests + probe. Review: 4 personas, 2 P0 + 7 P1 fixed (2 orchestrator post-fixes beyond the fix agent: unknown-symbol ticker branch, dedup key ticker→issuer_cik). COR-01 honesty-valve outcome: ingest universe = structural 6,544 CIKs by design (study's floor-checked 4,678 applies downstream; unpriceable events self-exclude, counted).
- **F357 code shipped** (commit `1f5847d`): builder/loader/CLI + event_study matrix path + runbook `bin/build-returns-matrix-remote.sh` (worker assumptions marked `# VERIFY:`). Review: 2 personas → completeness-honesty redesign (atomic dir commit incl. sidecar; loader raises on partial).
- **Tooling:** playwright-mcp installed (user scope; fresh session needed to expose tools); live-browser-verification.md "Which MCP" section added. Shakedown plan: chart-view snapshot-size + latency comparison vs chrome-devtools.

## Next actions

1. **F357 remainder [next]** — full matrix build on `strategylab-worker` (user `john`): verify the `# VERIFY:` assumptions in `bin/build-returns-matrix-remote.sh` (worker OS/python/paths), rsync price cache (~15-20GB — John's network call on timing), run with file logging, pull artifact, **full-scale equivalence-diff one real study vs the live path before anything trusts it** (F351 precedent). Seal guard: build ≤2024-12-31 only.
2. **F359** — midnight-UTC ADT investigation (~20-sample check vs EDGAR web) BEFORE any R-1 rerun interpretation; events are flagged `adt_midnight_utc` so exclusion needs no re-ingest.
3. **F354 [gated: F357 build + John's gate decision]** — R-1 rerun gate. John decides §10 source amendment vs clean R-1b charter. The four measured divergences (PROGRAM.md "F356 ingest shipped" bullet) are the decision inputs: 2dp price rounding (≤0.2% on D), amendment supersession (toggleable), kept dup-4 collisions, filed-symbol ticker resolution.
4. **R-2 explore** (distress recovery, charter `2f0cf24c…`) still needs NO Form 4 data — can proceed independently any session; its confirm window is 2025+.
5. **Playwright-mcp shakedown (first move of the next session — installed this session, tools only visible to fresh sessions).** App must be up (`./start.sh`; frontend 5173, backend 8000). The measured comparison, same 4 steps on both MCPs against the chart view (the page whose a11y tree blew chrome-devtools' token cap at ~117k chars, F217): (1) navigate to `http://localhost:5173`, (2) snapshot — record payload size, (3) ONE batched evaluate call returning small JSON (e.g. `{charts: document.querySelectorAll('.tv-lightweight-charts').length, errors: console-count}`), (4) screenshot. Compare per-call latency + snapshot size vs `chrome-devtools` equivalents; write the verdict into live-browser-verification.md "Which MCP" (replace the provisional defaults with measured ones). Known-traps re-check is NOT required for the shakedown — only flag if the `focus()`/`mouseleave` traps demonstrably differ under playwright.
6. **F352** (ledger file lock) and **F358** (universe-loader consolidation, 3 copies) remain open hardening.

## Operational notes for the next session

- Run telemetry: `.run/F356/` + `.run/F357/` (briefs, review JSONs, decisions.md, fix-wave.md, probe outputs, equivalence diffs). `python3 bin/run-state.py report F356` / `F357` for per-agent usage tables.
- F356 probe wall-clock: ~6 min (Anchor 2 scans all 45 quarters). Calibration matrix build: ~60s local.
- The fix-agent lesson twice this session: green agent work + plausible digest ≠ done — both P0 "fixes" needed orchestrator post-verification against artifacts (a counter delta and a branch read caught them).
- playwright-mcp shakedown queued for next fresh session (this one predates the install).

## Working agreements worth remembering

Plain language always (define every term inline). Blind authors for all charter text. F338: pre-stated anchors before believing any instrument; skipped checks report NOT-RUN, never PASS. §10 pre-outcome fixes are legal but John approves anything touching a charter he signed. Ledger writes are explicit-path-only. Promote durable findings immediately. Long-running tasks always followable.
