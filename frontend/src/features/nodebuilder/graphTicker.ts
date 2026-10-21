/**
 * The graph owns its symbol and interval (F453 item 1).
 *
 * A graph with no Output Group has one implicit group, `main`. Its primary
 * Ticker is the first root Ticker with no `prefix`, else the first root
 * Ticker (the same pick as graphGroups.listGraphGroups and the server's
 * routes/graphs.implicit_ticker, which spawn uses). Every editor run (Run backtest, auto
 * cook, the Data Sheet, the chart) uses that Ticker's own symbol and
 * interval, so the editor tests exactly what "Spawn bots" spawns. Only a
 * Ticker with an empty symbol falls back to the sidebar.
 *
 * The date range and the data source always come from the sidebar. A graph
 * with Output Groups is left alone: each group already runs on its own
 * primary Ticker on the server.
 */

import type { Graph, GraphNode } from '../../api/nodebuilder'
import type { GraphWindow } from './graphRun'
import { graphHasGroups } from './ownership'

/** The interval spawn uses when the Ticker has none (routes/graphs.group_bot_fields). */
export const DEFAULT_INTERVAL = '1d'

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

/**
 * The implicit group's primary Ticker, or null (a graph with groups, or no
 * root Ticker). When every root Ticker has a prefix, the first one counts,
 * as spawn picks it (FL-6): Run and Spawn then use the same symbol.
 */
export function implicitPrimaryTicker(graph: Pick<Graph, 'nodes'> | null | undefined): GraphNode | null {
  if (!graph?.nodes || graphHasGroups(graph)) return null
  const roots = Object.values(graph.nodes).filter(n => n.type === 'ticker' && n.parent == null)
  return roots.find(n => str(n.params?.prefix) === '') ?? roots[0] ?? null
}

/**
 * The symbol and interval the graph sets for its runs, or null when the
 * sidebar decides (a graph with groups, no primary Ticker, or an empty
 * symbol). The symbol is upper case and an empty interval reads "1d", as
 * spawn does.
 */
export function graphOwnTicker(graph: Pick<Graph, 'nodes'> | null | undefined): { ticker: string; interval: string } | null {
  const t = implicitPrimaryTicker(graph)
  if (!t) return null
  const symbol = str(t.params?.symbol)
  if (symbol === '') return null
  return { ticker: symbol.toUpperCase(), interval: str(t.params?.interval) || DEFAULT_INTERVAL }
}

/** One string for `graphOwnTicker`, so a store selector re-renders only when it changes. */
export function graphOwnTickerKey(graph: Pick<Graph, 'nodes'> | null | undefined): string {
  const own = graphOwnTicker(graph)
  return own ? `${own.ticker}|${own.interval}` : ''
}

/** Read a key from `graphOwnTickerKey` back; null for ''. */
export function parseOwnTickerKey(key: string): { ticker: string; interval: string } | null {
  if (!key) return null
  const [ticker, interval] = key.split('|')
  return { ticker, interval }
}

/**
 * The window a graph run uses: the sidebar's, with the symbol and interval
 * the graph owns put in. Returns `win` itself when nothing changes.
 */
export function withGraphTicker<W extends Pick<GraphWindow, 'ticker' | 'interval'>>(
  win: W,
  own: { ticker: string; interval: string } | null,
): W {
  if (!own || (own.ticker === win.ticker && own.interval === win.interval)) return win
  return { ...win, ticker: own.ticker, interval: own.interval }
}

/** The run window for this graph (see `withGraphTicker`). */
export function graphRunWindow<W extends Pick<GraphWindow, 'ticker' | 'interval'>>(
  graph: Pick<Graph, 'nodes'> | null | undefined,
  win: W,
): W {
  return withGraphTicker(win, graphOwnTicker(graph))
}

/**
 * The params of a new node. A new Ticker in the app starts on the sidebar's
 * symbol and interval (F453); without a sidebar (tests, standalone) it keeps
 * the catalog defaults.
 */
export function newNodeParams(
  type: string,
  defaults: Record<string, unknown>,
  sidebar: Pick<GraphWindow, 'ticker' | 'interval'> | null | undefined,
): Record<string, unknown> {
  const params = { ...defaults }
  if (type === 'ticker' && sidebar) {
    if (str(sidebar.ticker) !== '') params.symbol = sidebar.ticker.trim().toUpperCase()
    if (str(sidebar.interval) !== '') params.interval = sidebar.interval
  }
  return params
}

/**
 * True when the "Copy from sidebar" action has something to do: the Ticker's
 * symbol or interval differs from the sidebar's.
 */
export function differsFromSidebar(
  params: Record<string, unknown> | undefined,
  sidebar: Pick<GraphWindow, 'ticker' | 'interval'> | null | undefined,
): boolean {
  if (!sidebar || str(sidebar.ticker) === '') return false
  const symbol = str(params?.symbol).toUpperCase()
  const interval = str(params?.interval)
  return symbol !== sidebar.ticker.trim().toUpperCase() || interval !== sidebar.interval
}
