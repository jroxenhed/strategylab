/**
 * Graph runs and their results (plan D10, D11; specs S28, S30).
 *
 * A graph run has its own state, `GraphResultState`, kept by App next to the
 * rule result. It is never written into `lastRequest` or `backtestResult`:
 * those feed the Optimizer, Walk-Forward and Sensitivity panels and the
 * auto-render, and a graph run would corrupt all of them (D10).
 *
 * `buildGraphRequest` builds the request from the sidebar window (date range,
 * capital, data source, plus the sidebar ticker and interval for the
 * implicit group) and never includes a field the graph owns (`ownedFields`,
 * D11): the direction goes only with a graph that has no Output Group (D7).
 * It copies an allowlist of fields, so a field nobody listed can never leak
 * in either.
 */

import { fetchGraphBacktest, type Graph, type GraphBacktestRequest, type GraphBacktestResult } from '../../api/nodebuilder'
import type { BacktestResult, GroupResult, Trade } from '../../shared/types'
import { useSyncExternalStore } from 'react'
import { GRAPH_RUN_SETTINGS_EVENT, graphHasGroups, ownedFields } from './ownership'
import { EXIT_NOT_CONNECTED, graphEvalKey } from './resultsStrip'
import { COMBINED, displayedGroupResult, effectiveGroupKey, stripGroups } from './groupResults'

/** The sidebar values a graph run uses (D11). */
export interface GraphSidebar {
  start: string
  end: string
  initial_capital: number
  source: string
  ticker: string
  interval: string
}

/** The data window: what to fetch and cook (the sidebar's, D11). */
export interface GraphWindow {
  ticker: string
  start: string
  end: string
  interval: string
  source: string
}

/**
 * Settings-panel values that still go with a graph run: the direction (only
 * for a graph with no Output Group, D7) and the three "applies to graph"
 * settings that have no node yet (S29). Trailing stop, time stop and borrow
 * rate are graph-owned from W5 and never go out.
 */
export interface GraphRunExtras {
  direction?: 'long' | 'short'
  dynamic_sizing?: unknown
  skip_after_stop?: unknown
  trading_hours?: unknown
}

/** The backtest response; W4 adds the server's cook cache id (D6). */
export type GraphRunResponse = GraphBacktestResult & { cook_id?: string | null }

/** One finished graph run, as App keeps it (D10). */
export interface GraphResultState {
  origin: 'graph'
  graphId: string | null
  rev: number | null
  request: GraphBacktestRequest
  response: GraphRunResponse
  /**
   * The group tab shown (S33): `combined` or a group name. Any other value
   * (the initial 'main') shows the first tab, Combined.
   */
  displayedGroup: string
  /** The graph's name for the Results header (S30); null when it has none. */
  graphName: string | null
  /** What the graph computed (`graphEvalKey`); a different key later means stale. */
  graphKey: string
  /** Date.now() when the result arrived ("cooked 12:04:31", S30). */
  cookedAt: number
}

/** What NodeBuilder hands the run handler. */
export interface GraphRunArgs {
  graph: Graph
  graphId: string | null
  rev: number | null
  graphName: string | null
  signal?: AbortSignal
}

/** Runs one graph backtest and returns its result (it does not store it). */
export type GraphRunHandler = (args: GraphRunArgs) => Promise<GraphResultState>

/** The only fields a graph request carries. Graph-owned fields are not in it. */
export const GRAPH_REQUEST_FIELDS = [
  'graph', 'ticker', 'start', 'end', 'interval', 'source', 'initial_capital',
  'direction', 'dynamic_sizing', 'skip_after_stop', 'trading_hours',
] as const

/** Copy the settings-panel extras a graph run may carry onto `req`. */
function applyExtras(req: Partial<GraphBacktestRequest>, extras: GraphRunExtras, hasGroups: boolean): void {
  // D7: only the implicit group takes the request's direction.
  if (!hasGroups && (extras.direction === 'long' || extras.direction === 'short')) req.direction = extras.direction
  if (extras.dynamic_sizing != null) req.dynamic_sizing = extras.dynamic_sizing
  if (extras.skip_after_stop != null) req.skip_after_stop = extras.skip_after_stop
  if (extras.trading_hours != null) req.trading_hours = extras.trading_hours
  // Belt and braces: the allowlist above already leaves these out.
  const loose = req as unknown as Record<string, unknown>
  for (const f of ownedFields(hasGroups)) delete loose[f]
}

/**
 * The request for a graph run. Graph-owned fields (position size, stop loss,
 * slippage, commission, trailing stop, time stop, borrow rate, and the
 * direction of a graph with Output Groups) are never in it, so the backend
 * takes the graph's own node value or its engine default (D11 precedence).
 */
export function buildGraphRequest(
  graph: Graph,
  sidebar: GraphSidebar,
  extras: GraphRunExtras = {},
): GraphBacktestRequest {
  const req: GraphBacktestRequest = {
    graph,
    ticker: sidebar.ticker,
    start: sidebar.start,
    end: sidebar.end,
    interval: sidebar.interval,
    source: sidebar.source,
    initial_capital: sidebar.initial_capital,
  }
  applyExtras(req, extras, graphHasGroups(graph))
  return req
}

/** The window a request ran on. */
export function windowOfRequest(req: GraphBacktestRequest): GraphWindow {
  return {
    ticker: req.ticker,
    start: req.start,
    end: req.end,
    interval: req.interval ?? '1d',
    source: req.source ?? 'yahoo',
  }
}

/** One string per window, for "did the window change" checks. */
export function windowKey(w: GraphWindow | null | undefined): string {
  return w ? `${w.ticker}|${w.start}|${w.end}|${w.interval}|${w.source}` : ''
}

// ---- the settings panel's values -------------------------------------------

/** Where StrategyBuilder keeps its settings (it writes this on every change). */
export const STRATEGY_STORAGE_KEY = 'strategylab-strategy'

interface StoredStrategy {
  capital?: unknown
  direction?: unknown
  dynamicSizing?: { enabled?: unknown } | null
  skipAfterStop?: { enabled?: unknown } | null
  tradingHours?: { enabled?: unknown; start_time?: unknown; end_time?: unknown } | null
}

/**
 * The capital and the settings that still apply to graph runs, read from
 * the settings panel's saved state. App has no copy of the capital (it lives
 * in StrategyBuilder), so this reads what StrategyBuilder saves on every
 * change. Falls back to the given values, then to the engine defaults.
 */
export function readGraphRunSettings(
  fallback: { initial_capital?: number | null; direction?: string | null } = {},
): { initial_capital: number; extras: GraphRunExtras } {
  let saved: StoredStrategy | null = null
  try {
    const raw = localStorage.getItem(STRATEGY_STORAGE_KEY)
    saved = raw ? (JSON.parse(raw) as StoredStrategy) : null
  } catch {
    saved = null
  }
  const cap = typeof saved?.capital === 'number' && Number.isFinite(saved.capital) && saved.capital > 0
    ? saved.capital
    : (typeof fallback.initial_capital === 'number' && fallback.initial_capital > 0 ? fallback.initial_capital : 10000)
  const dirRaw = saved?.direction ?? fallback.direction
  const extras: GraphRunExtras = {}
  if (dirRaw === 'long' || dirRaw === 'short') extras.direction = dirRaw
  if (saved?.dynamicSizing && saved.dynamicSizing.enabled === true) extras.dynamic_sizing = saved.dynamicSizing
  if (saved?.skipAfterStop && saved.skipAfterStop.enabled === true) extras.skip_after_stop = saved.skipAfterStop
  const th = saved?.tradingHours
  if (th && th.enabled === true && typeof th.start_time === 'string' && typeof th.end_time === 'string') {
    extras.trading_hours = th
  }
  return { initial_capital: cap, extras }
}

/**
 * One string for the settings a graph request carries besides the graph and
 * the window: capital, direction (graphs with no Output Group only) and the
 * APPLIES TO GRAPH settings. A shown result whose request key differs from
 * the current settings is stale (CI-09).
 */
export function requestSettingsKey(req: Partial<GraphBacktestRequest>): string {
  return JSON.stringify([
    req.initial_capital ?? null, req.direction ?? null, req.dynamic_sizing ?? null,
    req.skip_after_stop ?? null, req.trading_hours ?? null,
  ])
}

/**
 * The settings key a graph run would send right now. `hasGroups`: the graph
 * has Output Groups, so its run sends no direction (D7).
 */
export function currentRunSettingsKey(hasGroups = false): string {
  const { initial_capital, extras } = readGraphRunSettings()
  const req: Partial<GraphBacktestRequest> = { initial_capital }
  // Same normalisation as a real run, then key it.
  applyExtras(req, extras, hasGroups)
  return requestSettingsKey(req)
}

// One cached key per kind of graph (with and without Output Groups).
let settingsKeyCache: [string | null, string | null] = [null, null]
function subscribeRunSettings(onChange: () => void): () => void {
  settingsKeyCache = [null, null]
  const on = () => { settingsKeyCache = [null, null]; onChange() }
  window.addEventListener(GRAPH_RUN_SETTINGS_EVENT, on)
  window.addEventListener('storage', on)
  return () => {
    window.removeEventListener(GRAPH_RUN_SETTINGS_EVENT, on)
    window.removeEventListener('storage', on)
  }
}
function runSettingsSnapshot(): string {
  return (settingsKeyCache[0] ??= currentRunSettingsKey(false))
}
function runSettingsSnapshotGroups(): string {
  return (settingsKeyCache[1] ??= currentRunSettingsKey(true))
}

/**
 * The current run settings key; re-renders when the settings panel saves.
 * `hasGroups`: the loaded graph has Output Groups (its runs send no direction).
 */
export function useGraphRunSettingsKey(hasGroups = false): string {
  const snap = hasGroups ? runSettingsSnapshotGroups : runSettingsSnapshot
  return useSyncExternalStore(subscribeRunSettings, snap, snap)
}

// ---- running ------------------------------------------------------------------

/** Build the request, run it, and wrap the answer as a graph result. */
export async function runGraphBacktest(
  args: GraphRunArgs,
  sidebar: GraphSidebar,
  extras: GraphRunExtras = {},
): Promise<GraphResultState> {
  const request = buildGraphRequest(args.graph, sidebar, extras)
  const response = (await fetchGraphBacktest(request, args.signal)) as GraphRunResponse
  return {
    origin: 'graph',
    graphId: args.graphId,
    rev: args.rev,
    request,
    response,
    displayedGroup: 'main',
    graphName: args.graphName,
    graphKey: graphEvalKey(args.graph),
    cookedAt: Date.now(),
  }
}

// ---- showing a result ------------------------------------------------------------

/**
 * The trades of the group shown (S33) in the chart's trade shape (same
 * records as rule runs). With two or more groups the legacy top-level
 * `trades` may be empty, so the displayed group's (or Combined's merged)
 * trades are used; otherwise the response's own.
 */
export function graphTrades(state: GraphResultState | null): Trade[] {
  if (!state) return []
  const shown = displayedGroupResult(state.response, effectiveGroupKey(state.response, state.displayedGroup))
  return shown?.result.trades ?? (state.response.trades as unknown as Trade[])
}

/**
 * The group whose candles and trade markers the chart shows (S33): the
 * group tab shown; on Combined, `candleGroup` when it names a group, else
 * the first group. Null when the result has no group strip (one group or
 * none): the chart then shows the request's window, as in W4.
 */
export function chartGroupOf(state: GraphResultState | null, candleGroup: string | null): GroupResult | null {
  if (!state) return null
  const groups = stripGroups(state.response)
  if (groups.length === 0) return null
  const key = effectiveGroupKey(state.response, state.displayedGroup)
  if (key && key !== COMBINED) return groups.find(g => g.name === key) ?? groups[0]
  return groups.find(g => g.name === candleGroup) ?? groups[0]
}

/**
 * The S33 hint over the chart on the Combined tab:
 * `Candles: long_leg (AAPL). Markers: long_leg. Equity: combined.` Null on
 * a group tab or without a group strip.
 */
export function combinedChartHint(state: GraphResultState | null, candleGroup: string | null): string | null {
  if (!state) return null
  const key = effectiveGroupKey(state.response, state.displayedGroup)
  if (key !== COMBINED) return null
  const g = chartGroupOf(state, candleGroup)
  if (!g) return null
  return `Candles: ${g.name} (${g.symbol}). Markers: ${g.name}. Equity: combined.`
}

/**
 * The graph result in the shape Results.tsx reads. The graph backtest runs
 * the rule simulator, so the summary, trades and curves have the same keys.
 */
export function graphResultAsBacktest(state: GraphResultState): BacktestResult {
  const r = state.response
  return {
    summary: r.summary as unknown as BacktestResult['summary'],
    trades: r.trades as unknown as BacktestResult['trades'],
    equity_curve: r.equity_curve as unknown as BacktestResult['equity_curve'],
    baseline_curve: r.baseline_curve as unknown as BacktestResult['baseline_curve'],
  }
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function signed(v: number, digits: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`
}

function two(n: number): string {
  return String(n).padStart(2, '0')
}

/** "12:04:31" in local time. */
export function clockTime(ms: number): string {
  const d = new Date(ms)
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}

/** The chart bar's summary (S28), in pieces so the return can be coloured. */
export interface ChartBarSummary {
  /** "AAPL 1d" */
  context: string
  /** "6 trades" */
  trades: string
  /** "+25.8%", or "—" */
  returnText: string
  returnSign: 'pos' | 'neg' | 'none'
  /** "Sharpe 1.40" */
  sharpe: string
  /** "open +4.2%" when a position is still open at the end, else null. */
  open: string | null
  /** "Exit not connected" when nothing is wired into Exit, else null. */
  exitWarning: string | null
}

export function chartBarSummary(state: GraphResultState): ChartBarSummary {
  const s = state.response.summary
  const w = windowOfRequest(state.request)
  const n = isNum(s.num_trades) ? s.num_trades : 0
  const ret = s.total_return_pct
  const sharpe = s.sharpe_ratio
  const op = s.open_position
  return {
    context: `${w.ticker} ${w.interval}`,
    trades: `${n} ${n === 1 ? 'trade' : 'trades'}`,
    returnText: isNum(ret) ? `${signed(ret, 1)}%` : '—',
    returnSign: isNum(ret) ? (ret >= 0 ? 'pos' : 'neg') : 'none',
    sharpe: `Sharpe ${isNum(sharpe) ? sharpe.toFixed(2) : '—'}`,
    open: op != null ? (isNum(op.unrealized_pct) ? `open ${signed(op.unrealized_pct, 1)}%` : 'open') : null,
    exitWarning: s.exit_connected === false ? EXIT_NOT_CONNECTED : null,
  }
}

/** The whole chart bar summary as one line (also its accessible name). */
export function chartBarText(sum: ChartBarSummary, stale: boolean): string {
  const parts = [sum.context, sum.trades, sum.returnText, sum.sharpe]
  if (sum.open) parts.push(sum.open)
  if (sum.exitWarning) parts.push(sum.exitWarning)
  return `${stale ? 'stale · ' : ''}${parts.join(' · ')}`
}

/** The chart bar text with no result yet (S28). */
export const NO_RESULT_TEXT = 'no result yet · Run backtest (⌘↵)'

/**
 * The Results header line after the GRAPH pill (S30):
 * `regime_filtered_rsi @ rev 12 · AAPL 1d · 2025-09-11 → 2026-09-11 · cooked 12:04:31`.
 */
export function graphHeaderText(state: GraphResultState): string {
  const w = windowOfRequest(state.request)
  const name = state.graphName && state.graphName.trim() ? state.graphName : 'untitled graph'
  const rev = state.rev != null ? ` @ rev ${state.rev}` : ''
  return `${name}${rev} · ${w.ticker} ${w.interval} · ${w.start} → ${w.end} · cooked ${clockTime(state.cookedAt)}`
}
