# Next Session — Note to Self

Read this first. Delete or archive once acted on.

## Immediate priority: morning ce:review on PR #31

PR #31 is open on `claude/jolly-babbage-KxUMl` — build 23 shipped F37 + F38 + F70 + F76 + F81 + F85, plus partial closes of F43 and F45.

Workflow:
1. `gh pr checkout 31`
2. **Run `python3 bin/sync-todo-index.py` first** — the build-23 commit shipped a stale TODO.md index (Critical / Up Next / Open Work counts all reflect pre-build-23 state). The pre-commit hook didn't run because hooks weren't installed in the routine env at the time. Sync first so reviewers see the correct state, then `git add TODO.md && git commit --amend --no-edit` (or stage as a separate fixup commit, your call).
3. Run ce:review with the 4-persona morning calibration roster: **adversarial, agent-native, security, reliability** (per F80 codification). Skip the dups the builder already covered (correctness, testing, kieran-python).
4. Apply confirmed P1s, file deferrals, merge.

## Why the stale index won't bite us going forward

This session shipped commit `e5a0325` ("protocol: tune overnight builder per build-23 self-review"). Key changes:

- **`bin/install-hooks.sh` switched to `git config core.hooksPath .githooks`** — fresh routine clones now activate the pre-commit hook automatically once the bootstrap runs `bash bin/install-hooks.sh`. Build 24 onwards will sync TODO.md on commit.
- **Overnight prompt patch** (`docs/overnight-builder-prompt-patch.md`) updated: MCP for pre-flight #1 (no `gh` CLI), AST+import-time smoke test (no venv needed), explicit `python3 bin/sync-todo-index.py` step before commit, round-2 skip license, `subagent_type` table for personas, F-bucket tag required on new F-items.
- **CLAUDE.md** — replaced absolute "delegate ALL work to subagents" with conditional rule (dispatch for parallel / specialized capability / tier-arbitrage; don't dispatch for same-tier serial work). Resolves the long-standing tension with the overnight builder doc.

## Pending follow-ups (lower priority)

1. **Stop-hook noise during parallel reviews.** Build-23 self-review noted the repo's stop-hook fires "uncommitted changes" repeatedly during long parallel review windows (5x in build 23). This is a `.claude/settings.json` config issue, separate from the protocol docs. Worth fixing before a future review window — either teach the hook to detect agent activity, or commit before review.
2. **JOURNAL.md archiving.** Still deferred from the previous session. JOURNAL.md is at ~470 lines with structural drift (duplicate `## 2026-05-08` and `## 2026-05-09` headers from older builds). Plan: weekly archive cycle to `docs/journal-archive/YYYY-MM.md`.
3. **Short-title bug in Up Next / Critical sections.** `bin/sync-todo-index.py` extracts short titles up to the first em-dash. F37 has no early em-dash so its short title runs long; F91 has `[P1] [easy]` doubled because of source-bullet ordering. Easy patch: cap short title at ~60 chars regardless of em-dash position.
4. **Two-seed-mapping override calls.** v4 backfill on F17, F28e, F41 was borderline (arch vs hardening). Worth a sanity-check pass once you've got eyes on the file.
5. **F97 (provision `backend/venv/` in routine container)** is filed but it's an infra change, not application code. Lives outside this repo most likely.

## What landed this session (newest first)

- `e5a0325` — protocol: tune overnight builder per build-23 self-review
- `bd16521` — todo: tag F37/F38/F70/F76 as [next]
- `0e93fae` — docs: TODO/JOURNAL reorg + sync script + hook + IDEAS.md
- `070a261` — docs: codify two-tier review (F80) — trim builder roster, drop ce:review probe
- `d274f54` — review: morning ce:review pass on PR #30 (already merged via PR #30 → `cb59411`)

## Quick orientation

- TODO.md is now 190 lines (down from 301). Index regenerates via `bin/sync-todo-index.py`.
- F-section split into 4 buckets: Architecture / Hardening / Polish / Testing & Infra.
- IDEAS.md exists for half-formed thoughts; graduate to TODO.md when concrete.
- Pre-commit hook auto-runs sync + auto-stamps new items with `(added YYYY-MM-DD)`.
- Two-tier review: builder runs 4-6 manual personas (coverage); morning runs 4 ce:review personas (calibration). Don't consolidate.

After acting on the priority item, delete this file.
