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
 */

/**
 * Request fields the graph always owns in graph view (D11). Wave 4 owned
 * size, stop loss, slippage and commission. Wave 5 adds trailing stop
 * (`trailing_stop` terminal), time stop (`time_stop` terminal, sent as
 * max_bars_held) and borrow rate (the `borrow_rate` settings node).
 */
export const GRAPH_OWNED_FIELDS = [
  'position_size',
  'stop_loss_pct',
  'slippage_bps',
  'commission_pct',
  'trailing_stop',
  'max_bars_held',
  'borrow_rate_annual',
] as const

/**
 * Request fields the graph owns only when it has one or more Output Groups
 * (D7, D11). Each group sets its own `direction`; a graph with no
 * `output_group` (the implicit `main` group) takes the direction from the
 * request, so the settings panel keeps it and graph runs send it.
 */
export const GROUP_OWNED_FIELDS = ['direction'] as const

export type GraphOwnedField = (typeof GRAPH_OWNED_FIELDS)[number] | (typeof GROUP_OWNED_FIELDS)[number]

/** True when the graph has at least one `output_group` node. */
export function graphHasGroups(graph: { nodes: Record<string, { type: string }> } | null | undefined): boolean {
  if (!graph) return false
  for (const n of Object.values(graph.nodes)) if (n.type === 'output_group') return true
  return false
}

/**
 * True when the sidebar owns this Ticker's symbol and interval (W5, D7):
 * only the implicit group's primary, so a Ticker with no `prefix` in a
 * graph with no Output Group. A reference Ticker (with a prefix) and the
 * Tickers of a graph with groups fetch their own symbol and interval (S32c).
 */
export function sidebarOwnsTicker(
  params: Record<string, unknown> | undefined,
  graph: { nodes: Record<string, { type: string }> } | null | undefined,
): boolean {
  const p = params?.prefix
  if (typeof p === 'string' && p.trim() !== '') return false
  return !graphHasGroups(graph)
}

/** Every field the graph owns, given whether it has Output Groups. */
export function ownedFields(hasGroups: boolean): readonly GraphOwnedField[] {
  return hasGroups ? [...GRAPH_OWNED_FIELDS, ...GROUP_OWNED_FIELDS] : GRAPH_OWNED_FIELDS
}

/**
 * Sidebar fields with no graph node yet. They still apply to graph runs, so
 * the settings panel marks them "applies to graph" (S29) and graph runs send
 * them (D11 "Dynamic sizing, skip-after-stop, trading hours").
 */
export const GRAPH_APPLIES_FIELDS = ['dynamic_sizing', 'skip_after_stop', 'trading_hours'] as const

export type GraphAppliesField = (typeof GRAPH_APPLIES_FIELDS)[number]

/** True when the graph owns this request field in graph view (`hasGroups`: the graph has Output Groups). */
export function isGraphOwned(field: string, hasGroups = false): boolean {
  return (ownedFields(hasGroups) as readonly string[]).includes(field)
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
/** The note at the top of the settings panel in graph view. */
export const GRAPH_NOTE_TEXT =
  'Graph view: the graph sets size, stop loss, trailing stop, time stop, slippage, commission and borrow rate, and each Output Group sets its direction. Date range, capital, data source and the settings marked APPLIES TO GRAPH still come from here.'
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
