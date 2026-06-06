# Research Handoff — where we are, what's next

*Living doc: overwrite at each session close. Last updated 2026-06-07 early (the R-1 explore execution session). Read alongside [PROGRAM.md](PROGRAM.md) (axioms, methodology rules, program state).*

## Where we are, in one paragraph

R-1's explore ran end-to-end for the first time — full instrument stack built same-session (`r1_dose.py` / `r1_analysis.py` / `run_r1_explore.py` / `probe_r1_explore.py`, 66 tests, 4-persona review wave, 15 findings fixed pre-run), F338 probe 13 PASS / 0 FAIL / 2 honest NOT-RUN — and the verdict is **UNTESTABLE — underpowered**: Q5−Q1 63td MDE 60.4pp vs the 1.0pp abort floor. The §3a abort fired exactly as pre-registered: confirm window untouched, no alpha spent, Form-4 corollary evidence unchanged. The root cause is the DATA, not the design: the Form 4 cache is the old turnaround program's stratified sample (pond-lineage tickers at quarterly dates; 63% of waves below the tradeability floor; 30 quintile-valid events). **John's 2026-06-07 catch: the old-program-DNA warning applied to the data layer, not just the premises.** The fix is F353 — full Form 4 fetch for the ~4,700-ticker liquid universe.

## Done (2026-06-06 late → 06-07 early, this session)

- **R-1 instrument stack** shipped + F338-validated (probe green; FS-B peer-fallback / FS-C peer-univ-corr anchors honestly NOT-RUN at n=42 <50, re-attach to F353-scale).
- **Two §10 pre-outcome charter fixes, John-approved, zero outcome contact:** dedup 21→30 calendar days (charter's 21-bday/21-calendar arithmetic error, ADV-02, bias toward hypothesis); rare non-evidential regime state = RISK_OFF not STRESS (counted from real artifact: RISK_OFF 3 days in 2015–2020, STRESS 11.1%; evidential trio now {RISK_ON, NEUTRAL, STRESS}). Charter sha untouched; logged in PROGRAM.md + code comments.
- **First real FDR ledger entries** (harness stream + `…_r1_family` charter-family entry). Ledger-pollution incident caught + structurally fixed: ledger_path=None now SKIPS (the first test-suite run wrote 108 fixture entries into the real ledger; deleted pre-first-real-entry).
- **F338 closed** (overnight-builder-guide §3.5 now carries the binding rule; bin helper rejected — per-instrument probes are the pattern).
- **Wave-morphology probe** (no outcomes): 60% of insider waves are single-day bursts; 35% of multi-day waves dollar-back-loaded → John's conviction-accumulation hunch has empirical legs → IDEAS "dose-escalation event clock".

## Next actions

1. **F353 [next]** — full Form 4 EDGAR fetch, liquid universe (~4,700 tickers, 2015–2024), non-stratified cache in a NEW dir (`form4_full/`); never overwrite `form4_stratified/` (it is the frozen R-1-as-run source). Reuse F331 pacing/circuit-breaker. Multi-hour wall-clock; plan as overnight or background task.
2. **F354 [gated]** — R-1 rerun at scale needs John's gate decision: §10 source amendment vs clean R-1b charter + fresh ledger draw. The frozen design carries over verbatim either way.
3. **R-2 explore** (distress recovery, charter `2f0cf24c…`) needs NO Form 4 data — can proceed independently any session. Its confirm window is 2025+.
4. **F352** — ledger file lock still open; single-writer discipline holds until then.

## Operational notes for the next session

- R-1 artifact: `backend/data/turnaround/event_studies/r1_insider_clusters_explore_2015_2020/` (incl. `r1_explore_verdict.json`, `run.log`). Run telemetry: `.run/R1-explore/`.
- Drivers now tee logging to `<study_dir>/run.log` — `tail -f` is the progress channel (John's rule: long tasks always followable; NEVER pipe background launches through `tail`, it buffers).
- py-spy installed in backend venv (needs sudo on macOS) — `sudo backend/venv/bin/py-spy dump --pid <pid> --locals` decodes a live run.
- Full-run wall-clock on the M1 Air: ~75 min for 113 events × 4,678-ticker universe × 3 horizons (calibration --calibrate mode: ~5 min, smoke ledger, DELETEME dir).

## Working agreements worth remembering

Plain language always (define every term inline). Blind authors for all charter text. F338: pre-stated anchors before believing any instrument; skipped checks report NOT-RUN, never PASS. §10 pre-outcome fixes are legal but John approves anything touching a charter he signed. Ledger writes are explicit-path-only. Promote durable findings immediately. Long-running tasks always followable.
