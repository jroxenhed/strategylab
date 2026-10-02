/**
 * Graph runs (F435 W4 item 4.D; plan D10, D11; specs S28, S30).
 *
 * - buildGraphRequest never includes a field from GRAPH_OWNED_FIELDS, and
 *   carries only its allowlist. The direction goes only with a graph that
 *   has no Output Group (W5, D7).
 * - graphTrades follows the displayed group (S33).
 * - readGraphRunSettings reads the capital and the "applies to graph"
 *   settings, never the graph-owned ones.
 * - The chart bar and Results header texts.
 * - App state through the real run handler: a graph run never changes
 *   lastRequest (nor its saved copy), and the graph result reaches the
 *   Results panel with origin "graph".
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import type { BacktestResult, StrategyRequest } from '../../../shared/types'

// ---- mocks for the App test (hoisted) -------------------------------------------

const CLEAN_DIAG = {
  diagnostics: [] as unknown[], byNode: {}, errorCount: 0, warningCount: 0,
  pending: false, offline: false, offlineDetail: null as string | null, hasResult: true,
}

interface ResultsCall {
  origin: 'graph' | 'rule'; lastRequest: unknown; header: string | null; result: unknown
  graphGroups?: { displayedGroup: string; onSelect: (k: string) => void; onFrameGroup?: (g: unknown) => void } | null
}
const seen = vi.hoisted(() => ({
  results: [] as ResultsCall[],
  sbGraphView: [] as boolean[],
  sbHasGroups: [] as boolean[],
  charts: 0,
}))

vi.mock('../useDiagnostics', () => ({
  useDiagnostics: () => CLEAN_DIAG,
  useDiagnosticsController: vi.fn(),
  setServerDiagnostics: vi.fn(),
  retryValidation: vi.fn().mockResolvedValue(undefined),
  getDiagnosticsView: () => CLEAN_DIAG,
}))
vi.mock('../DiagnosticsPopover', () => ({ DiagnosticsPopover: () => null }))
vi.mock('../Canvas', async () => {
  const { createElement: h } = await import('react')
  return { default: () => h('div', { 'data-testid': 'canvas-stub' }) }
})
vi.mock('../../../api/nodebuilder', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilder')>()
  return { ...orig, fetchGraphBacktest: vi.fn(), fetchAutoRender: vi.fn().mockResolvedValue(null) }
})
vi.mock('../../../api/nodebuilderInspect', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilderInspect')>()
  return {
    ...orig,
    preview: vi.fn().mockResolvedValue({ cook_id: 'ck_1', nodes: {} }),
    inspect: vi.fn().mockReturnValue(new Promise(() => {})),
  }
})
vi.mock('../../../api/graphs', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/graphs')>()
  return {
    ...orig,
    listGraphs: vi.fn().mockResolvedValue([]),
    getGraph: vi.fn(),
    createGraph: vi.fn(),
    saveGraph: vi.fn(),
    deleteGraph: vi.fn(),
    seedLegacyGraphs: vi.fn(),
  }
})
vi.mock('../../sidebar/Sidebar', () => ({ default: () => null }))
vi.mock('../../chart/Chart', async () => {
  const { createElement: h, useEffect } = await import('react')
  return {
    default: function ChartStub(p: { ticker?: string; trades?: unknown[] }) {
      useEffect(() => {
        seen.charts += 1
        return () => { seen.charts -= 1 }
      }, [])
      return h('div', { 'data-testid': 'chart-stub', 'data-ticker': p.ticker, 'data-trades': String(p.trades?.length ?? 0) })
    },
  }
})
vi.mock('../../chart/ChartSkeleton', () => ({ default: () => null }))
vi.mock('../../strategy/StrategyComparison', () => ({ default: () => null }))
vi.mock('../../trading/PaperTrading', () => ({ default: () => null }))
vi.mock('../../discovery/Discovery', () => ({ default: () => null }))
vi.mock('../../desk/Desk', () => ({ default: () => null }))
vi.mock('../../watchlist/WatchlistPanel', () => ({ default: () => null }))
vi.mock('../../../shared/utils/seedFromLocalStorage', () => ({ seedFromLocalStorageIfAny: vi.fn() }))
vi.mock('../../../shared/hooks/useOHLCV', () => ({
  useOHLCV: () => ({
    data: [{ time: '2024-01-02', open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }],
    isLoading: false, isFetching: false, isError: false, refetch: vi.fn(),
  }),
  useInstanceIndicators: () => ({
    data: {}, refetch: vi.fn(), isLoading: false, loadingByInstance: {}, isError: false, errorMessage: null,
  }),
}))
vi.mock('../../strategy/StrategyBuilder', async () => {
  const { createElement: h } = await import('react')
  return {
    // React 19 passes `ref` as a plain prop; the stub has no handle to give.
    default: function StrategyBuilderStub(p: { graphViewActive?: boolean; graphHasGroups?: boolean; onResult: (r: unknown, req?: unknown) => void }) {
      seen.sbGraphView.push(!!p.graphViewActive)
      seen.sbHasGroups.push(!!p.graphHasGroups)
      return h('button', {
        type: 'button',
        'data-testid': 'sb-stub-run',
        onClick: () => p.onResult(RULE_RESULT_FOR_MOCK(), RULE_REQUEST_FOR_MOCK()),
      }, 'rule run')
    },
  }
})
vi.mock('../../strategy/Results', async () => {
  const { createElement: h } = await import('react')
  return {
    default: (p: { result: unknown; lastRequest: unknown; graphInfo?: { headerText: string } | null; graphGroups?: ResultsCall['graphGroups'] }) => {
      const origin = p.graphInfo ? 'graph' : 'rule'
      seen.results.push({
        origin, lastRequest: p.lastRequest, header: p.graphInfo?.headerText ?? null, result: p.result,
        graphGroups: p.graphGroups ?? null,
      })
      return h('div', { 'data-testid': `results-stub-${origin}` }, p.graphInfo?.headerText ?? 'rule')
    },
  }
})

// Rule data the StrategyBuilder stub sends (functions, so the hoisted mock can read them).
function RULE_REQUEST_FOR_MOCK(): StrategyRequest {
  return {
    ticker: 'MSFT', start: '2024-01-02', end: '2024-06-28', interval: '1d',
    buy_rules: [{ indicator: 'rsi', condition: 'below', value: 30 }] as StrategyRequest['buy_rules'],
    sell_rules: [{ indicator: 'rsi', condition: 'above', value: 70 }] as StrategyRequest['sell_rules'],
    buy_logic: 'AND', sell_logic: 'AND',
    initial_capital: 5000, position_size: 0.5, stop_loss_pct: 3, slippage_bps: 7,
    per_share_rate: 0.0035, min_per_order: 0.35, source: 'yahoo', direction: 'long',
  } as StrategyRequest
}
function RULE_RESULT_FOR_MOCK(): BacktestResult {
  return {
    summary: {
      initial_capital: 5000, final_value: 5100, total_return_pct: 2, buy_hold_return_pct: 1,
      num_trades: 1, win_rate_pct: 100, sharpe_ratio: 0.5, max_drawdown_pct: -1,
    },
    trades: [], equity_curve: [],
  }
}

import { fetchGraphBacktest } from '../../../api/nodebuilder'
import { getGraph } from '../../../api/graphs'
import { requestOpenGraph, requestOpenTrading } from '../graphLinks'
import {
  buildGraphRequest,
  chartBarSummary,
  chartBarText,
  currentRunSettingsKey,
  GRAPH_REQUEST_FIELDS,
  graphHeaderText,
  graphResultAsBacktest,
  graphTrades,
  NO_RESULT_TEXT,
  readGraphRunSettings,
  requestSettingsKey,
  STRATEGY_STORAGE_KEY,
  windowKey,
  windowOfRequest,
  type GraphResultState,
  type GraphSidebar,
} from '../graphRun'
import { GRAPH_OWNED_FIELDS, GROUP_OWNED_FIELDS, notifyGraphRunSettingsChanged } from '../ownership'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import { clearNotices } from '../notices'

// ---- fixtures --------------------------------------------------------------------

function node(id: string, type: string, name: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

function smallGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      n_t: node('n_t', 'ticker', 'aapl', { symbol: 'NVDA', interval: '1h' }),
      n_e: node('n_e', 'entry', 'entry'),
    },
  }
}

/** smallGraph with one Output Group: the group sets its own direction (D11). */
function groupGraph(): Graph {
  const g = smallGraph()
  return { ...g, nodes: { ...g.nodes, n_g: node('n_g', 'output_group', 'long_leg', { direction: 'long', ticker: 'aapl' }) } }
}

const SIDEBAR: GraphSidebar = {
  ticker: 'AAPL', start: '2025-09-11', end: '2026-09-11', interval: '1d', source: 'yahoo', initial_capital: 10000,
}

/** Six round trips: 12 trade records (buy then sell). */
function sixRoundTrips() {
  const trades: Record<string, unknown>[] = []
  for (let i = 0; i < 6; i++) {
    const d = (n: number) => `2025-${String(10 + Math.floor(n / 28)).padStart(2, '0')}-${String((n % 28) + 1).padStart(2, '0')}`
    trades.push({ type: 'buy', date: d(i * 8), price: 100 + i, shares: 10 })
    trades.push({ type: 'sell', date: d(i * 8 + 4), price: 104 + i, shares: 10, pnl: 40, pnl_pct: 4 })
  }
  return trades
}

function graphResponse() {
  return {
    summary: {
      num_trades: 6, total_return_pct: 25.8, sharpe_ratio: 1.4, initial_capital: 10000, final_value: 12580,
      buy_hold_return_pct: 10, win_rate_pct: 100, max_drawdown_pct: -3, exit_connected: true, open_position: null,
    },
    trades: sixRoundTrips(),
    equity_curve: [{ time: '2025-10-01', value: 10000 }],
    baseline_curve: [],
    cook_id: 'ck_1',
  }
}

function resultState(over: Partial<GraphResultState> = {}): GraphResultState {
  const graph = smallGraph()
  return {
    origin: 'graph',
    graphId: 'g_1',
    rev: 12,
    request: buildGraphRequest(graph, SIDEBAR),
    response: graphResponse() as unknown as GraphResultState['response'],
    displayedGroup: 'main',
    graphName: 'regime_filtered_rsi',
    graphKey: 'k',
    cookedAt: new Date(2026, 8, 11, 12, 4, 31).getTime(),
    ...over,
  }
}

// ---- pure helpers ---------------------------------------------------------------------

describe('buildGraphRequest (D11)', () => {
  it('never includes a field from GRAPH_OWNED_FIELDS, even when the inputs carry them', () => {
    const graph = smallGraph()
    const polluted = {
      ...SIDEBAR, position_size: 0.5, stop_loss_pct: 3, slippage_bps: 9, commission_pct: 1,
      per_share_rate: 0.0035, min_per_order: 0.35,
    } as unknown as GraphSidebar
    const extras = {
      direction: 'short', dynamic_sizing: { enabled: true }, position_size: 0.2, stop_loss_pct: 5,
      slippage_bps: 4, commission_pct: 2,
    } as unknown as Parameters<typeof buildGraphRequest>[2]
    const req = buildGraphRequest(graph, polluted, extras) as unknown as Record<string, unknown>
    for (const f of GRAPH_OWNED_FIELDS) expect(f in req, f).toBe(false)
    expect('per_share_rate' in req).toBe(false)
    expect('min_per_order' in req).toBe(false)
    for (const k of Object.keys(req)) expect(GRAPH_REQUEST_FIELDS as readonly string[]).toContain(k)
    expect(req).toMatchObject({
      ticker: 'AAPL', start: '2025-09-11', end: '2026-09-11', interval: '1d', source: 'yahoo',
      initial_capital: 10000, direction: 'short', dynamic_sizing: { enabled: true },
    })
    expect(req.graph).toBe(graph)
  })

  it('a graph with Output Groups never sends the direction (D7, D11)', () => {
    const extras = { direction: 'short', dynamic_sizing: { enabled: true } } as const
    const req = buildGraphRequest(groupGraph(), SIDEBAR, extras) as unknown as Record<string, unknown>
    for (const f of [...GRAPH_OWNED_FIELDS, ...GROUP_OWNED_FIELDS]) expect(f in req, f).toBe(false)
    expect(req.dynamic_sizing).toEqual({ enabled: true })
    // Without groups the same extras send the sidebar direction.
    expect(buildGraphRequest(smallGraph(), SIDEBAR, extras).direction).toBe('short')
  })

  it('never sends trailing stop, time stop or borrow rate (graph-owned from W5)', () => {
    const extras = {
      direction: 'long', trailing_stop: { type: 'pct', value: 5 }, max_bars_held: 30, borrow_rate_annual: 1.25,
    } as unknown as Parameters<typeof buildGraphRequest>[2]
    for (const g of [smallGraph(), groupGraph()]) {
      const req = buildGraphRequest(g, SIDEBAR, extras) as unknown as Record<string, unknown>
      for (const f of ['trailing_stop', 'max_bars_held', 'borrow_rate_annual']) expect(f in req, f).toBe(false)
    }
  })

  it('the settings key follows the same rule, so a groups result is not stale on a direction change', () => {
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({ capital: 20000, direction: 'short' }))
    const s = readGraphRunSettings()
    const sent = (g: Graph) => requestSettingsKey(buildGraphRequest(g, { ...SIDEBAR, initial_capital: s.initial_capital }, s.extras))
    expect(currentRunSettingsKey(false)).toBe(sent(smallGraph()))
    expect(currentRunSettingsKey(true)).toBe(sent(groupGraph()))
    expect(currentRunSettingsKey(true)).not.toBe(currentRunSettingsKey(false))
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({ capital: 20000, direction: 'long' }))
    expect(currentRunSettingsKey(true)).toBe(sent(groupGraph()))
    localStorage.clear()
  })

  it('leaves out the optional settings that are off', () => {
    const req = buildGraphRequest(smallGraph(), SIDEBAR, {}) as unknown as Record<string, unknown>
    expect(Object.keys(req).sort()).toEqual(
      ['end', 'graph', 'initial_capital', 'interval', 'source', 'start', 'ticker'],
    )
  })

  it('the sidebar ticker and interval win over the Ticker node until W5 (D11)', () => {
    const req = buildGraphRequest(smallGraph(), SIDEBAR)
    expect(req.ticker).toBe('AAPL')
    expect(req.interval).toBe('1d')
  })

  it('windowOfRequest and windowKey round-trip the window', () => {
    const w = windowOfRequest(buildGraphRequest(smallGraph(), SIDEBAR))
    expect(w).toEqual({ ticker: 'AAPL', start: '2025-09-11', end: '2026-09-11', interval: '1d', source: 'yahoo' })
    expect(windowKey(w)).toBe('AAPL|2025-09-11|2026-09-11|1d|yahoo')
    expect(windowKey(null)).toBe('')
  })
})

describe('readGraphRunSettings', () => {
  beforeEach(() => localStorage.clear())

  it('reads capital, direction and the enabled "applies to graph" settings only', () => {
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({
      capital: 25000, direction: 'short', posSize: 40, stopLoss: 4, slippageBps: 9, perShareRate: 0.0035,
      dynamicSizing: { enabled: true, consec_sls: 2, reduced_pct: 25, trigger: 'sl' },
      skipAfterStop: { enabled: false, count: 1, trigger: 'sl' },
      tradingHours: { enabled: true, start_time: '09:30', end_time: '16:00', skip_ranges: [] },
    }))
    const s = readGraphRunSettings()
    expect(s.initial_capital).toBe(25000)
    expect(s.extras).toEqual({
      direction: 'short',
      dynamic_sizing: { enabled: true, consec_sls: 2, reduced_pct: 25, trigger: 'sl' },
      trading_hours: { enabled: true, start_time: '09:30', end_time: '16:00', skip_ranges: [] },
    })
  })

  it('falls back to the given values, then the engine default, on bad or missing data', () => {
    expect(readGraphRunSettings({ initial_capital: 7000, direction: 'long' })).toEqual({
      initial_capital: 7000, extras: { direction: 'long' },
    })
    localStorage.setItem(STRATEGY_STORAGE_KEY, '{not json')
    expect(readGraphRunSettings().initial_capital).toBe(10000)
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({ capital: -5 }))
    expect(readGraphRunSettings().initial_capital).toBe(10000)
  })
})

describe('result texts (S28, S30)', () => {
  it('the chart bar reads the S28 summary', () => {
    const sum = chartBarSummary(resultState())
    expect(chartBarText(sum, false)).toBe('AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(chartBarText(sum, true)).toBe('stale · AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(sum.returnSign).toBe('pos')
    expect(NO_RESULT_TEXT).toBe('no result yet · Run backtest (⌘↵)')
  })

  it('adds an open position and an unwired Exit', () => {
    const st = resultState()
    st.response.summary.open_position = { direction: 'long', entry_price: 100, unrealized_pct: 4.2 }
    st.response.summary.exit_connected = false
    expect(chartBarText(chartBarSummary(st), false))
      .toBe('AAPL 1d · 6 trades · +25.8% · Sharpe 1.40 · open +4.2% · Exit not connected')
  })

  it('the Results header matches the S30 pattern', () => {
    const text = `GRAPH ${graphHeaderText(resultState())}`
    expect(text).toMatch(/^GRAPH .+ @ rev \d+ · [A-Z.]+ \w+ · \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} · cooked \d{2}:\d{2}:\d{2}$/)
    expect(text).toBe('GRAPH regime_filtered_rsi @ rev 12 · AAPL 1d · 2025-09-11 → 2026-09-11 · cooked 12:04:31')
    expect(graphHeaderText(resultState({ graphName: null, rev: null }))).toMatch(/^untitled graph · AAPL 1d/)
  })

  it('graphTrades follows the displayed group, and Combined merges the groups (S33)', () => {
    const st = resultState()
    const trades = st.response.trades as unknown as Record<string, unknown>[]
    const group = (name: string, n: number) => ({
      name, node_id: `n_${name}`, path: `/${name}`, symbol: 'AAPL', interval: '1d', direction: 'long',
      weight: 1, capital: 5000, summary: { num_trades: n / 2 }, trades: trades.slice(0, n), equity_curve: [],
    })
    st.response = {
      ...st.response,
      trades: [],
      groups: [group('long_leg', 4), group('short_leg', 2)],
      combined: { summary: { exposure_pct: 10, gross_deployed_pct: 5 }, equity_curve: [] },
    } as unknown as GraphResultState['response']
    // 'main' is no tab: the first tab, Combined, shows every group's trades.
    expect(graphTrades(st)).toHaveLength(6)
    expect(graphTrades({ ...st, displayedGroup: 'short_leg' })).toHaveLength(2)
    expect(graphTrades({ ...st, displayedGroup: 'long_leg' })).toHaveLength(4)
  })

  it('adapts the result for Results and the chart', () => {
    const st = resultState()
    expect(graphTrades(st)).toHaveLength(12)
    expect(graphTrades(null)).toEqual([])
    const bt = graphResultAsBacktest(st)
    expect(bt.summary.num_trades).toBe(6)
    expect(bt.trades).toHaveLength(12)
    expect('signal_trace' in bt).toBe(false)
  })
})

// ---- App state through the run handler (D10, S30) ----------------------------------------

// App reads its saved settings when the module loads: set them first.
localStorage.setItem('strategylab-settings', JSON.stringify({
  ticker: 'MSFT', start: '2024-01-02', end: '2024-06-28', interval: '1d', dataSource: 'yahoo',
}))
const { default: App } = await import('../../../App')

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client }, children)
}

function lastResults(origin: 'graph' | 'rule'): ResultsCall | undefined {
  return [...seen.results].reverse().find(r => r.origin === origin)
}

describe('App: a graph run through the run handler (D10)', () => {
  beforeEach(() => {
    clearNotices()
    seen.results.length = 0
    seen.sbGraphView.length = 0
    localStorage.removeItem('strategylab-last-backtest')
    localStorage.setItem('nodebuilder-graph-view-active', 'false')
    localStorage.setItem('activeTab', 'chart')
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({
      capital: 25000, direction: 'long', posSize: 40, stopLoss: 4, slippageBps: 9, perShareRate: 0.0035, minPerOrder: 0.35,
    }))
    vi.mocked(fetchGraphBacktest).mockReset()
    useNodeBuilderStore.getState().discardEdits()
    useNodeBuilderStore.getState().resetCooks()
    useNodeBuilderStore.setState({ cook: IDLE_COOK })
  })
  afterEach(() => cleanup())

  it('never changes lastRequest, and the graph result reaches Results as origin "graph"', async () => {
    vi.mocked(fetchGraphBacktest).mockResolvedValue(
      graphResponse() as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>,
    )
    render(createElement(App), { wrapper })

    // A rule backtest sets lastRequest (and its saved copy).
    fireEvent.click(screen.getByTestId('sb-stub-run'))
    const ruleReq = RULE_REQUEST_FOR_MOCK()
    expect(lastResults('rule')?.lastRequest).toEqual(ruleReq)
    const savedBefore = localStorage.getItem('strategylab-last-backtest')
    expect(JSON.parse(savedBefore!).request).toEqual(ruleReq)
    expect(screen.getAllByTestId('chart-stub')).toHaveLength(1)

    // Graph view, with a saved graph open.
    fireEvent.click(screen.getByRole('button', { name: 'View as Graph' }))
    expect(seen.sbGraphView.at(-1)).toBe(true)
    await act(async () => {
      useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 3, name: 'alpha' })
    })
    // One chart, moved: in graph view it sits in the split's chart panel
    // (jsdom has no layout, so the panel counts as open here), never in
    // the hidden chart view as well.
    expect(screen.getAllByTestId('chart-stub')).toHaveLength(1)
    expect(screen.getByTestId('nb-chart-body').querySelector('[data-testid="chart-stub"]')).not.toBeNull()
    expect(seen.charts).toBe(1)

    // Run through the toolbar (NodeBuilder -> App's handler).
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    await waitFor(() => expect(screen.getByTestId('results-stub-graph')).toBeInTheDocument())

    // The request: the sidebar window and the settings capital, no owned field.
    expect(fetchGraphBacktest).toHaveBeenCalledTimes(1)
    const sent = vi.mocked(fetchGraphBacktest).mock.calls[0][0] as unknown as Record<string, unknown>
    expect(sent).toMatchObject({
      ticker: 'MSFT', start: '2024-01-02', end: '2024-06-28', interval: '1d', source: 'yahoo',
      initial_capital: 25000, direction: 'long',
    })
    for (const f of GRAPH_OWNED_FIELDS) expect(f in sent, f).toBe(false)
    expect('per_share_rate' in sent).toBe(false)

    // lastRequest is untouched (the rule Results still gets it; the saved copy is the same).
    expect(lastResults('rule')?.lastRequest).toEqual(ruleReq)
    expect(localStorage.getItem('strategylab-last-backtest')).toBe(savedBefore)

    // The graph result: origin graph, no rule request, the S30 header.
    const g = lastResults('graph')!
    expect(g.lastRequest).toBeNull()
    expect(g.header).toMatch(/^alpha @ rev 3 · MSFT 1d · 2024-01-02 → 2024-06-28 · cooked \d{2}:\d{2}:\d{2}$/)
    expect((g.result as BacktestResult).trades).toHaveLength(12)
    // The backtest cook carries the cook id (auto cook refreshes sparklines from it).
    expect(useNodeBuilderStore.getState().cooks.backtest.cookId).toBe('ck_1')
    // The chart bar shows the summary, and the one chart gets the graph's trades.
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('MSFT 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(screen.getByTestId('chart-stub').dataset).toMatchObject({ ticker: 'MSFT', trades: '12' })

    // Back to the chart view: the rule result and its request are as before.
    fireEvent.click(screen.getByRole('button', { name: 'Back to Chart' }))
    expect(lastResults('rule')?.lastRequest).toEqual(ruleReq)
    // CI-04: the graph Results stays mounted, hidden (display:none survival).
    expect(screen.getByTestId('graph-results-panel').style.display).toBe('none')
    // UX-14: the chart view offers the graph result through one hint line.
    expect(screen.getByTestId('results-graph-hint')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('results-show-graph'))
    expect(screen.getByTestId('graph-results-panel').style.display).toBe('block')
    fireEvent.click(screen.getByRole('button', { name: 'Back to Chart' }))
    expect(screen.queryByTestId('results-graph-hint')).toBeInTheDocument()
    expect(screen.getAllByTestId('chart-stub')).toHaveLength(1)
    expect(screen.getByTestId('chart-stub').dataset.trades).toBe('0')
    expect(seen.charts).toBe(1)
  })

  it('a settings change marks the result stale, and a failed re-run keeps the stale badge (CI-09, UX-03)', async () => {
    vi.mocked(fetchGraphBacktest).mockResolvedValueOnce(
      graphResponse() as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>,
    )
    render(createElement(App), { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'View as Graph' }))
    await act(async () => {
      useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 3, name: 'alpha' })
    })
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    await waitFor(() => expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('MSFT 1d · 6 trades · +25.8% · Sharpe 1.40'))
    expect(useNodeBuilderStore.getState().cooks.backtest.stale).toBe(false)

    // The settings panel saves a new capital: the shown result no longer
    // matches what Run would send.
    const saved = JSON.parse(localStorage.getItem(STRATEGY_STORAGE_KEY)!)
    await act(async () => {
      localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({ ...saved, capital: 30000 }))
      notifyGraphRunSettingsChanged()
    })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toMatch(/^stale · /)
    expect(useNodeBuilderStore.getState().cooks.backtest.stale).toBe(true)

    // A re-run that fails leaves the old result on screen, still marked stale
    // in the Results header (the run start must not clear it).
    vi.mocked(fetchGraphBacktest).mockRejectedValueOnce(new Error('boom'))
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    await waitFor(() => expect(useNodeBuilderStore.getState().cooks.backtest.phase).toBe('failed'))
    expect(useNodeBuilderStore.getState().cooks.backtest.stale).toBe(true)

    // Back to the settings the result ran with: fresh again.
    await act(async () => {
      localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify(saved))
      notifyGraphRunSettingsChanged()
    })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('MSFT 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(useNodeBuilderStore.getState().cooks.backtest.stale).toBe(false)
  })
})

// ---- W5: Output Groups in App (S33), links from Trading (S34, S35) --------------------

function twoGroupGraph(): Graph {
  const g = smallGraph()
  return {
    ...g,
    nodes: {
      ...g.nodes,
      n_long: node('n_long', 'output_group', 'long_leg', { direction: 'long', ticker: 'aapl' }),
      n_short: node('n_short', 'output_group', 'short_leg', { direction: 'short', ticker: 'aapl' }),
    },
  }
}

function twoGroupResponse() {
  const base = graphResponse()
  const trades = base.trades
  const group = (name: string, symbol: string, n: number) => ({
    name, node_id: `n_${name.split('_')[0]}`, path: `/${name}`, symbol, interval: '1h', direction: 'long',
    weight: 1, capital: 12500, summary: { num_trades: n / 2, total_return_pct: 1 }, trades: trades.slice(0, n), equity_curve: [],
  })
  return {
    ...base,
    trades: [],
    groups: [group('long_leg', 'AAPL', 4), group('short_leg', 'MSFT', 2)],
    combined: { summary: { exposure_pct: 61.2, gross_deployed_pct: 48 }, equity_curve: [] },
  }
}

describe('App with Output Groups and Trading links (W5)', () => {
  beforeEach(() => {
    clearNotices()
    seen.results.length = 0
    seen.sbGraphView.length = 0
    seen.sbHasGroups.length = 0
    localStorage.removeItem('strategylab-last-backtest')
    localStorage.setItem('nodebuilder-graph-view-active', 'false')
    localStorage.setItem('activeTab', 'chart')
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({ capital: 25000, direction: 'short' }))
    vi.mocked(fetchGraphBacktest).mockReset()
    vi.mocked(getGraph).mockReset()
    useNodeBuilderStore.getState().discardEdits()
    useNodeBuilderStore.getState().resetCooks()
    useNodeBuilderStore.setState({ cook: IDLE_COOK })
  })
  afterEach(() => cleanup())

  it('a group tab moves the chart to that group; Combined shows the S33 hint; no direction is sent', async () => {
    vi.mocked(fetchGraphBacktest).mockResolvedValue(
      twoGroupResponse() as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>,
    )
    render(createElement(App), { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'View as Graph' }))
    await act(async () => {
      useNodeBuilderStore.getState().openGraph(twoGroupGraph(), { id: 'g_2', rev: 1, name: 'pair' })
    })
    // The settings panel greys the direction for a graph with groups.
    expect(seen.sbHasGroups.at(-1)).toBe(true)
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    await waitFor(() => expect(screen.getByTestId('results-stub-graph')).toBeInTheDocument())
    const sent = vi.mocked(fetchGraphBacktest).mock.calls[0][0] as unknown as Record<string, unknown>
    expect('direction' in sent).toBe(false)

    // Combined first: the first group's candles and markers, and the hint.
    const groups = lastResults('graph')!.graphGroups!
    expect(groups.displayedGroup).toBe('main')
    expect(screen.getByTestId('chart-stub').dataset).toMatchObject({ ticker: 'AAPL', trades: '4' })
    expect(screen.getByTestId('graph-chart-hint').textContent).toBe('Candles: long_leg (AAPL). Markers: long_leg. Equity: combined.')

    // A group pill: that group's ticker and markers, its frame selected, no hint.
    act(() => groups.onSelect('short_leg'))
    expect(lastResults('graph')!.graphGroups!.displayedGroup).toBe('short_leg')
    expect(screen.getByTestId('chart-stub').dataset).toMatchObject({ ticker: 'MSFT', trades: '2' })
    expect(screen.queryByTestId('graph-chart-hint')).toBeNull()
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('n_short')
    // Back on Combined the candles stay on the last group chosen.
    act(() => lastResults('graph')!.graphGroups!.onSelect('combined'))
    expect(screen.getByTestId('chart-stub').dataset).toMatchObject({ ticker: 'MSFT', trades: '2' })
    expect(screen.getByTestId('graph-chart-hint').textContent).toBe('Candles: short_leg (MSFT). Markers: short_leg. Equity: combined.')
    // One chart all along, and no rule state touched.
    expect(seen.charts).toBe(1)
    expect(lastResults('rule')).toBeUndefined()
  })

  it('the Trading links switch the tab, and the graph link opens that graph at its group', async () => {
    render(createElement(App), { wrapper })
    act(() => requestOpenTrading('bot_1'))
    expect(localStorage.getItem('activeTab')).toBe('trading')

    vi.mocked(getGraph).mockResolvedValue({
      id: 'g_9', rev: 4, name: 'pair', graph: twoGroupGraph(),
    } as unknown as Awaited<ReturnType<typeof getGraph>>)
    await act(async () => { requestOpenGraph({ graphId: 'g_9', group: 'short_leg', spawn: false }) })
    expect(localStorage.getItem('activeTab')).toBe('chart')
    expect(seen.sbGraphView.at(-1)).toBe(true)
    await waitFor(() => expect(useNodeBuilderStore.getState().graphMeta?.id).toBe('g_9'))
    expect(getGraph).toHaveBeenCalledWith('g_9')
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('n_short')

    // The same graph again: no second load, the group is selected.
    await act(async () => { requestOpenGraph({ graphId: 'g_9', group: 'long_leg', spawn: false }) })
    expect(getGraph).toHaveBeenCalledTimes(1)
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('n_long')
  })

  it('the graph link asks about unsaved edits before it switches the view; Cancel changes nothing (FE-08)', async () => {
    render(createElement(App), { wrapper })
    await act(async () => {
      useNodeBuilderStore.getState().openGraph(twoGroupGraph(), { id: 'g_1', rev: 1, name: 'mine' })
    })
    act(() => {
      useNodeBuilderStore.getState().commit('nudge', g => ({
        ...g, nodes: { ...g.nodes, n_long: { ...g.nodes.n_long, position: [5, 5] } },
      }))
    })
    expect(useNodeBuilderStore.getState().dirty).toBe(true)
    act(() => requestOpenTrading(null))
    expect(localStorage.getItem('activeTab')).toBe('trading')
    const viewBefore = seen.sbGraphView.at(-1)

    vi.mocked(getGraph).mockResolvedValue({
      id: 'g_9', rev: 4, name: 'pair', graph: twoGroupGraph(),
    } as unknown as Awaited<ReturnType<typeof getGraph>>)
    await act(async () => { requestOpenGraph({ graphId: 'g_9', group: 'short_leg', spawn: false }) })
    // The prompt first: the tab and the view have not moved yet.
    expect(await screen.findByTestId('nb-save-changes-dialog')).toBeInTheDocument()
    expect(localStorage.getItem('activeTab')).toBe('trading')
    expect(seen.sbGraphView.at(-1)).toBe(viewBefore)

    // Cancel: same tab, same view, same graph, nothing loaded.
    await act(async () => { fireEvent.click(screen.getByTestId('nb-dialog-cancel')) })
    expect(screen.queryByTestId('nb-save-changes-dialog')).toBeNull()
    expect(localStorage.getItem('activeTab')).toBe('trading')
    expect(seen.sbGraphView.at(-1)).toBe(viewBefore)
    expect(useNodeBuilderStore.getState().graphMeta?.id).toBe('g_1')
    expect(getGraph).not.toHaveBeenCalled()

    // Discard: now the view switches and the graph loads.
    await act(async () => { requestOpenGraph({ graphId: 'g_9', group: 'short_leg', spawn: false }) })
    await act(async () => { fireEvent.click(await screen.findByTestId('nb-save-changes-discard')) })
    expect(localStorage.getItem('activeTab')).toBe('chart')
    expect(seen.sbGraphView.at(-1)).toBe(true)
    await waitFor(() => expect(useNodeBuilderStore.getState().graphMeta?.id).toBe('g_9'))
  })
})
