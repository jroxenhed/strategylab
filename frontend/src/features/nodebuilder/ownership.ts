/**
 * Who owns each setting in graph view (plan D11, spec S29).
 *
 * The strategy settings panel (StrategyBuilder.tsx) and the graph must never
 * both decide the same value. In graph view the graph owns the fields below:
 * the settings panel greys them ("Set by graph") and `buildGraphRequest`
 * (graphRun.ts) never sends them, so a greyed field can never change a graph
 * result. The backend then takes the graph's own node value, or its default.
 *
 * This is the ONE list. StrategyBuilder.tsx imports it; do not copy it.
 * Wave 5 adds direction, trailing_stop, max_bars_held and borrow_rate_annual.
 */

/** Request fields the graph owns in graph view (W4 list, D11). */
export const GRAPH_OWNED_FIELDS = [
  'position_size',
  'stop_loss_pct',
  'slippage_bps',
  'commission_pct',
] as const

export type GraphOwnedField = (typeof GRAPH_OWNED_FIELDS)[number]

/**
 * Sidebar fields with no graph node yet. They still apply to graph runs, so
 * the settings panel marks them "applies to graph" (S29) and graph runs send
 * them (D11 "Dynamic sizing, skip-after-stop, trading hours").
 */
export const GRAPH_APPLIES_FIELDS = ['dynamic_sizing', 'skip_after_stop', 'trading_hours'] as const

export type GraphAppliesField = (typeof GRAPH_APPLIES_FIELDS)[number]

/**
 * Settings the graph owns only from W5 (D11 rows "Direction", "Trailing stop",
 * "Time stop", "Costs ... borrow W5"). Until then graph runs send the
 * settings panel's values for them, and the panel marks them "applies to
 * graph" (UX-04, UX-05). A graph node for one of them (for example a
 * trailing_stop settings node) still wins on the backend (D11 precedence).
 * Wave 5 moves these into GRAPH_OWNED_FIELDS.
 */
export const GRAPH_APPLIES_UNTIL_W5_FIELDS = ['direction', 'trailing_stop', 'max_bars_held', 'borrow_rate_annual'] as const

/** True when the graph owns this request field in graph view. */
export function isGraphOwned(field: string): boolean {
  return (GRAPH_OWNED_FIELDS as readonly string[]).includes(field)
}

// ---- S29 copy -------------------------------------------------------------

/** Tooltip on a greyed field group and on its GRAPH pill. */
export const SET_BY_GRAPH = 'Set by graph'
/** Pill text on a greyed field. */
export const GRAPH_PILL = 'GRAPH'
/** Pill text on a field that still applies to graph runs. */
export const APPLIES_PILL = 'APPLIES TO GRAPH'
/** Tooltip on the APPLIES TO GRAPH pill. */
export const APPLIES_TITLE = 'Sent with graph runs too'
/** Tooltip on the APPLIES TO GRAPH pill of a field the graph takes over in W5. */
export const APPLIES_UNTIL_W5_TITLE = 'Sent with graph runs too, until the graph owns it. A graph node for it wins.'
/** The note at the top of the settings panel in graph view. */
export const GRAPH_NOTE_TEXT =
  'Graph view: the graph sets size, stop loss, slippage and commission. Date range, capital, data source and the settings marked APPLIES TO GRAPH still come from here.'
/** Element id of that note; greyed controls point at it with aria-describedby. */
export const GRAPH_NOTE_ID = 'sb-graph-note'

// ---- settings-change signal (CI-09) ----------------------------------------

/**
 * Fired on window after StrategyBuilder saves its settings, so a shown graph
 * result can tell that capital or an APPLIES TO GRAPH setting changed since
 * its run (graphRun.useGraphRunSettingsKey).
 */
export const GRAPH_RUN_SETTINGS_EVENT = 'strategylab-graph-run-settings'

export function notifyGraphRunSettingsChanged(): void {
  try { window.dispatchEvent(new Event(GRAPH_RUN_SETTINGS_EVENT)) } catch { /* no window */ }
}
