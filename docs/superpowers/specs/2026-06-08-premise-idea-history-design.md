# Premise Idea History + Dispositions (design)

**Date:** 2026-06-08
**Status:** Approved (brainstorm, "try it and change it later"), build via orchestrator cycle (F397)
**Builds on:** F388–F390 (the premise workbench), F395 (s1 instrument)

## Problem
The Premise Library shows only a status chip. After an idea runs, the user can't see *what happened* (the verdict/why) or record *what they concluded* and revisit it later with a sharper premise. The first real user premise (`p-1569aa97`, UNTESTABLE — 0 events) felt "not saved" because the outcome was never surfaced and the list didn't live-refresh. (No data-loss bug: the store persists + the list fetches; the gap is display + a small data model.)

## Goal
Turn the library into a real **idea history**: per idea show the machine outcome + the user's disposition/why, with lineage for revisits, so parked ideas can be returned to (sharper premise, or after fetching more data).

## Data model (backend, `premise_store.py` + `routes/premises.py`)
Per premise:
- `disposition` — frozen set, default `active`: `active` · `parked_needs_data` · `parked_sharpen` · `rejected` · `promising`.
- `disposition_note` — freetext (≤ a sane cap, e.g. 2000 chars).
- `derived_from` — premise_id of the original when cloned (None otherwise).
- **machine outcome is DERIVED** (not stored) from the latest `run_history` entry: no-run → `"—"`; failed → `"failed: <error_note>"`; untestable → `"UNTESTABLE — N events"`; explored → `"<explore_decision> · <key stat>"`. Single source of truth.

Endpoints (agent-native — UI and operator share them):
- `PUT /api/premises/{id}/disposition` — body `{disposition, note}`; validates disposition ∈ frozen set (422 otherwise); allowed in any state (it's metadata, not a run).
- `POST /api/premises/{id}/duplicate` — clones `premise_text` + `spec` into a new `draft`, sets `derived_from`, returns `{premise_id}`. Original untouched. premise_id format-guarded like other routes.
- `GET /api/premises` list item extended with: `disposition`, `derived_from`, and a derived `machine_outcome` one-liner (so the list renders history with no N+1 fetches). Keep the existing optional `?status=` filter; add optional `?disposition=` filter (validated).

## UI (frontend, `PremiseLibrary.tsx` + `PremiseDetail.tsx` + `api/premises.ts`)
- **Library rows:** idea text (truncated) · status chip · machine-outcome one-liner · colored disposition tag. Sorted most-recently-updated first. A disposition filter (All / Active / Parked / Rejected / Promising).
- **Detail pane:** a "Disposition" block — dropdown (5 values) + note textarea + Save (`PUT /disposition`); a **"Duplicate as new premise"** button (`POST /duplicate`, opens the new draft); if `derived_from` set, a `↳ sharper version of p-XXXX` backlink at top (opens the original).
- **Live refresh:** while the Premises subpane is open, poll `listPremises` every ~15s (cleanup on unmount), on top of refresh-after-action + manual ↻. Fixes the "looked unsaved" issue.
- No new view — the master-detail *is* the history (YAGNI).

## Boundaries / non-goals
- No separate History tab. No auto-disposition (the machine outcome is derived; the user sets disposition). No edit-in-place changes (already works via the spec editor). No cross-idea analytics.

## Testing
- Backend: disposition validation (valid set / 422 on junk); duplicate clones text+spec, sets derived_from, leaves original untouched, new id format-valid; machine_outcome derivation for each state (no-run/failed/untestable/explored); `?disposition=` filter.
- Frontend: `npm run build` + render probe (library row shows outcome + tag; detail disposition block saves; duplicate opens new draft; derived backlink renders).

## Follow-ups likely (iterate later, per "change it later")
Disposition history/audit (who/when changed), richer machine-outcome stats in the row, grouping by lineage tree.
