/**
 * Graph runs (F435 W4 item 4.D; plan D10, D11; specs S28, S30).
 *
 * - buildGraphRequest never includes a field from GRAPH_OWNED_FIELDS, and
 *   carries only its allowlist.
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

interface ResultsCall { origin: 'graph' | 'rule'; lastRequest: unknown; header: string | null; result: unknown }
const seen = vi.hoisted(() => ({
  results: [] as ResultsCall[],
  sbGraphView: [] as boolean[],
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
    default: function StrategyBuilderStub(p: { graphViewActive?: boolean; onResult: (r: unknown, req?: unknown) => void }) {
      seen.sbGraphView.push(!!p.graphViewActive)
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
    default: (p: { result: unknown; lastRequest: unknown; graphInfo?: { headerText: string } | null }) => {
      const origin = p.graphInfo ? 'graph' : 'rule'
      seen.results.push({ origin, lastRequest: p.lastRequest, header: p.graphInfo?.headerText ?? null, result: p.result })
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
import {
  buildGraphRequest,
  chartBarSummary,
  chartBarText,
  GRAPH_REQUEST_FIELDS,
  graphHeaderText,
  graphResultAsBacktest,
  graphTrades,
  NO_RESULT_TEXT,
  readGraphRunSettings,
  STRATEGY_STORAGE_KEY,
  windowKey,
  windowOfRequest,
  type GraphResultState,
  type GraphSidebar,
} from '../graphRun'
import { GRAPH_OWNED_FIELDS, notifyGraphRunSettingsChanged } from '../ownership'
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
