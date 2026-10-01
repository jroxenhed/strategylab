/**
 * Graph runs and their results (plan D10, D11; specs S28, S30).
 *
 * A graph run has its own state, `GraphResultState`, kept by App next to the
 * rule result. It is never written into `lastRequest` or `backtestResult`:
 * those feed the Optimizer, Walk-Forward and Sensitivity panels and the
 * auto-render, and a graph run would corrupt all of them (D10).
 *
 * `buildGraphRequest` builds the request from the sidebar window (date range,
 * capital, data source, plus the sidebar ticker and interval until W5) and
 * never includes a field the graph owns (`GRAPH_OWNED_FIELDS`, D11). It copies
 * an allowlist of fields, so a field nobody listed can never leak in either.
 */

import { fetchGraphBacktest, type Graph, type GraphBacktestRequest, type GraphBacktestResult } from '../../api/nodebuilder'
import type { BacktestResult, Trade } from '../../shared/types'
import { useSyncExternalStore } from 'react'
import { GRAPH_OWNED_FIELDS, GRAPH_RUN_SETTINGS_EVENT } from './ownership'
import { EXIT_NOT_CONNECTED, graphEvalKey } from './resultsStrip'

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
 * Settings-panel values that still go with a graph run: the direction until
 * W5 moves it into the graph, and the three "applies to graph" settings that
 * have no node yet (S29).
 */
export interface GraphRunExtras {
  direction?: 'long' | 'short'
  dynamic_sizing?: unknown
  skip_after_stop?: unknown
  trading_hours?: unknown
  // Graph-owned only from W5 (D11). Until then the settings panel's values go
  // with graph runs, and a graph node for one still wins on the backend.
  trailing_stop?: unknown
  max_bars_held?: number
  borrow_rate_annual?: number
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
  /** 'main' until W5 adds output groups. */
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
  'trailing_stop', 'max_bars_held', 'borrow_rate_annual',
] as const

/**
 * The request for a graph run. Graph-owned fields (position size, stop loss,
 * slippage, commission) are never in it, so the backend takes the graph's
 * own node value or its engine default (D11 precedence).
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
  if (extras.direction === 'long' || extras.direction === 'short') req.direction = extras.direction
  if (extras.dynamic_sizing != null) req.dynamic_sizing = extras.dynamic_sizing
  if (extras.skip_after_stop != null) req.skip_after_stop = extras.skip_after_stop
  if (extras.trading_hours != null) req.trading_hours = extras.trading_hours
  if (extras.trailing_stop != null) req.trailing_stop = extras.trailing_stop
  if (typeof extras.max_bars_held === 'number' && extras.max_bars_held > 0) req.max_bars_held = extras.max_bars_held
  if (typeof extras.borrow_rate_annual === 'number' && extras.borrow_rate_annual >= 0) req.borrow_rate_annual = extras.borrow_rate_annual
  // Belt and braces: the allowlist above already leaves these out.
  const loose = req as unknown as Record<string, unknown>
  for (const f of GRAPH_OWNED_FIELDS) delete loose[f]
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
  maxBarsHeld?: unknown
  trailingEnabled?: unknown
  trailingConfig?: unknown
  borrowRateAnnual?: unknown
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
  // Until W5 (D11): trailing stop, time stop and borrow rate (UX-04, CI-02).
  if (saved?.trailingEnabled === true && saved.trailingConfig != null && typeof saved.trailingConfig === 'object') {
    extras.trailing_stop = saved.trailingConfig
  }
  if (typeof saved?.maxBarsHeld === 'number' && Number.isFinite(saved.maxBarsHeld) && saved.maxBarsHeld > 0) {
    extras.max_bars_held = saved.maxBarsHeld
  }
  if (typeof saved?.borrowRateAnnual === 'number' && Number.isFinite(saved.borrowRateAnnual) && saved.borrowRateAnnual >= 0) {
    extras.borrow_rate_annual = saved.borrowRateAnnual
  }
  return { initial_capital: cap, extras }
}

/**
 * One string for the settings a graph request carries besides the graph and
 * the window: capital, direction and the APPLIES TO GRAPH settings. A shown
 * result whose request key differs from the current settings is stale (CI-09).
 */
export function requestSettingsKey(req: Partial<GraphBacktestRequest>): string {
  return JSON.stringify([
    req.initial_capital ?? null, req.direction ?? null, req.dynamic_sizing ?? null,
    req.skip_after_stop ?? null, req.trading_hours ?? null, req.trailing_stop ?? null,
    req.max_bars_held ?? null, req.borrow_rate_annual ?? null,
  ])
}

/** The settings key a graph run would send right now. */
export function currentRunSettingsKey(): string {
  const { initial_capital, extras } = readGraphRunSettings()
  const blank = { ticker: '', start: '', end: '', interval: '', source: '', initial_capital }
  // Same normalisation as a real run: build the request, then key it.
  return requestSettingsKey(buildGraphRequest(null as unknown as Graph, blank, extras))
}

let settingsKeyCache: string | null = null
function subscribeRunSettings(onChange: () => void): () => void {
  settingsKeyCache = null
  const on = () => { settingsKeyCache = null; onChange() }
  window.addEventListener(GRAPH_RUN_SETTINGS_EVENT, on)
  window.addEventListener('storage', on)
  return () => {
    window.removeEventListener(GRAPH_RUN_SETTINGS_EVENT, on)
    window.removeEventListener('storage', on)
  }
}
function runSettingsSnapshot(): string {
  if (settingsKeyCache == null) settingsKeyCache = currentRunSettingsKey()
  return settingsKeyCache
}

/** The current run settings key; re-renders when the settings panel saves. */
export function useGraphRunSettingsKey(): string {
  return useSyncExternalStore(subscribeRunSettings, runSettingsSnapshot, runSettingsSnapshot)
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

/** The graph's trades in the chart's trade shape (same records as rule runs). */
export function graphTrades(state: GraphResultState | null): Trade[] {
  return state ? (state.response.trades as unknown as Trade[]) : []
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
