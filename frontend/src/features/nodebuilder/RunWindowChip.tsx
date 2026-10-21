/**
 * The run window chip in the toolbar (F453, UX-2): "SPY · 5m".
 *
 * The graph's Ticker now decides the symbol and interval of every editor
 * run, so the sidebar's symbol field no longer does. This chip says what a
 * run uses, and its tooltip says who sets what. It shows only in the app (a
 * sidebar window exists) and for a graph with no Output Group: with groups,
 * each group runs on its own Ticker and its frame header says which.
 *
 * Registered in the toolbar's left slot by plugins/runWindowChip.ts.
 */

import { useNodeBuilderStore } from './store'
import { useSidebarWindow } from './sidebarWindow'
import { graphOwnTickerKey, parseOwnTickerKey, withGraphTicker } from './graphTicker'
import { graphHasGroups } from './ownership'

/** The tooltip (exact text from the review decision). */
export const RUN_WINDOW_TIP =
  "The graph's Ticker sets the symbol and interval. The sidebar sets the dates, data source and capital."
/** Added when the graph sets no symbol, so the sidebar's is used. */
export const RUN_WINDOW_SIDEBAR_TIP = "This graph's Ticker has no symbol, so the sidebar's symbol and interval are used."

export function RunWindowChip() {
  const win = useSidebarWindow()
  // The same pick as NodeBuilder's effective window (a string, so this
  // re-renders only when the symbol or interval changes).
  const ownKey = useNodeBuilderStore(s => (s.graph && !s.graph.readOnly ? graphOwnTickerKey(s.graph) : ''))
  const show = useNodeBuilderStore(s => s.graph != null && !graphHasGroups(s.graph))
  if (!win || !show) return null
  const run = withGraphTicker(win, parseOwnTickerKey(ownKey))
  const ticker = run.ticker.trim().toUpperCase()
  if (!ticker) return null
  const tip = ownKey === '' ? `${RUN_WINDOW_TIP} ${RUN_WINDOW_SIDEBAR_TIP}` : RUN_WINDOW_TIP
  return (
    <span
      className="nb-toolbar__window"
      // Focusable, so keyboard users can reach the tooltip too.
      tabIndex={0}
      title={tip}
      aria-label={`Runs on ${ticker} ${run.interval}. ${tip}`}
      data-testid="nb-run-window"
    >
      {ticker} · {run.interval}
    </span>
  )
}

export default RunWindowChip
