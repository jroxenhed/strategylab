/**
 * resultsStrip.ts — pure text logic for the graph backtest results strip and
 * its error banner. Kept apart from NodeBuilder.tsx so it can be unit-tested.
 */

import type { BacktestSummary, Graph } from '../../api/nodebuilder'
import { apiErrorDetail } from '../../shared/utils/errors'

export interface ResultsStripModel {
  /** What the run was on, e.g. "AAPL · 1d". */
  context: string
  /** e.g. "6 trades", "1 trade". */
  trades: string
  /** e.g. "+42.30%", or "—" when missing. */
  returnText: string
  returnSign: 'pos' | 'neg' | 'none'
  /** e.g. "Sharpe 1.234". */
  sharpe: string
  /** e.g. "1 open position (+4.2%)", or null when nothing is open. */
  openPosition: string | null
  /** Hover text with the open position's side and entry price. */
  openPositionTitle: string | null
  /** "Exit not connected" when the Exit terminal has no input, else null. */
  exitWarning: string | null
  /** True when the graph changed after this run; the strip dims itself. */
  stale: boolean
}

export const STALE_LABEL = 'stale'
export const STALE_TITLE = 'The graph changed after this run. Run the backtest again to update.'
export const STALE_REQUEST_TITLE =
  "The chart's backtest settings (dates, capital) changed after this run. Run the backtest again to update."

/**
 * A key for what a graph backtest computes: every node's type, params and
 * bypass flag, and every wire. Positions and the display flag are left out,
 * so tidying the layout does not mark a result stale.
 */
export function graphEvalKey(graph: Graph | null): string {
  if (graph == null) return ''
  const nodes = Object.keys(graph.nodes).sort().map(id => {
    const n = graph.nodes[id]
    return { id: n.id, type: n.type, params: n.params, bypass: n.bypass }
  })
  return JSON.stringify({ nodes, wires: graph.wires })
}
export const EXIT_NOT_CONNECTED = 'Exit not connected'
/** What an unwired Exit means for the result (hover text on the warning). */
export const EXIT_NOT_CONNECTED_TITLE =
  'Nothing is wired into Exit, so trades close only on a stop or at the end of the test.'

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function signed(v: number, digits: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`
}

/**
 * Build the strip's text from a backtest summary.
 *
 * @param summary - The summary from POST /api/nodebuilder/backtest.
 * @param run     - The symbol and interval the run used.
 * @param stale   - True when the graph (or request) changed since the run.
 */
export function buildResultsStrip(
  summary: BacktestSummary,
  run: { ticker: string; interval: string },
  stale: boolean,
): ResultsStripModel {
  const n = isNum(summary.num_trades) ? summary.num_trades : 0
  const ret = summary.total_return_pct
  const sharpe = summary.sharpe_ratio

  let openPosition: string | null = null
  let openPositionTitle: string | null = null
  const op = summary.open_position
  if (op != null) {
    openPosition = isNum(op.unrealized_pct)
      ? `1 open position (${signed(op.unrealized_pct, 1)}%)`
      : '1 open position'
    const entry = isNum(op.entry_price) ? ` from ${op.entry_price.toFixed(2)}` : ''
    openPositionTitle =
      `A ${op.direction ?? 'long'} position${entry} was still open when the test ended. ` +
      'The return includes its unrealized gain or loss; the trade count does not.'
  }

  return {
    context: `${run.ticker} · ${run.interval}`,
    trades: `${n} ${n === 1 ? 'trade' : 'trades'}`,
    returnText: isNum(ret) ? `${signed(ret, 2)}%` : '—',
    returnSign: isNum(ret) ? (ret >= 0 ? 'pos' : 'neg') : 'none',
    sharpe: `Sharpe ${isNum(sharpe) ? sharpe.toFixed(3) : '—'}`,
    openPosition,
    openPositionTitle,
    // Only warn on an explicit false, so an older server without the field stays quiet.
    exitWarning: summary.exit_connected === false ? EXIT_NOT_CONNECTED : null,
    stale,
  }
}

/** Pull node_id out of a graph error, if the server sent one. */
export function errorNodeId(e: unknown): string | null {
  if (!e || typeof e !== 'object') return null
  const data = (e as { response?: { data?: unknown } }).response?.data
  if (!data || typeof data !== 'object') return null
  const top = (data as { node_id?: unknown }).node_id
  if (typeof top === 'string' && top) return top
  // FastAPI HTTPException(detail={...}) nests the body one level down.
  const detail = (data as { detail?: unknown }).detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const inner = (detail as { node_id?: unknown }).node_id
    if (typeof inner === 'string' && inner) return inner
  }
  return null
}

/** Pull the message out of a nested {detail: {detail|message, node_id}} body. */
function nestedMessage(e: unknown): string | null {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  for (const key of ['detail', 'message', 'msg'] as const) {
    const v = (detail as Record<string, unknown>)[key]
    if (typeof v === 'string' && v) return v
  }
  return null
}

/**
 * Turn a failed graph backtest into a message for the user: the server's own
 * detail text, plus which node is at fault when the server names one.
 */
export function describeBacktestError(e: unknown, graph: Graph | null): string {
  const base = nestedMessage(e) ?? apiErrorDetail(e, 'Graph backtest failed')
  const nodeId = errorNodeId(e)
  if (nodeId == null) return base
  // Most server messages already name the node; don't say it twice.
  if (base.includes(nodeId)) return base
  const node = graph?.nodes[nodeId]
  return node ? `${base} (node: ${node.type} ${nodeId})` : `${base} (node: ${nodeId})`
}
