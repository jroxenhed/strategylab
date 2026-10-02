/**
 * The graph/chart split (F435 W4 item 4.D, spec S28).
 *
 * - Graph trades render as chart markers: the app's one Chart, with a
 *   mocked lightweight-charts API, gets 12 markers for 6 round trips.
 * - The chart bar under the canvas shows the last run's summary, reads
 *   "running…" while a run cooks, and starts with "stale · " after a commit.
 * - The workspace keys and toolbar items are registered (A, S, Shift+V;
 *   Auto cook at order 20, the sheet toggle with the panel toggles).
 *
 * jsdom has no layout, so the panel's open/closed sizes are checked in the
 * browser probe (bin/probe-graph-split.mjs), not here.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

// ---- lightweight-charts mock (same surface as src/test/chart-mount.test.tsx) ----

const markerCalls = vi.hoisted(() => ({ lists: [] as unknown[][] }))

vi.mock('lightweight-charts', () => {
  function series() {
    return {
      setData: vi.fn(), update: vi.fn(), applyOptions: vi.fn(),
      priceScale: vi.fn(() => ({ width: vi.fn(() => 50), applyOptions: vi.fn() })),
      coordinateToPrice: vi.fn(() => 0), priceToCoordinate: vi.fn(() => 0),
    }
  }
  function chart() {
    const ts = {
      subscribeVisibleLogicalRangeChange: vi.fn(), unsubscribeVisibleLogicalRangeChange: vi.fn(),
      getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 100 })), setVisibleLogicalRange: vi.fn(),
      fitContent: vi.fn(), scrollToPosition: vi.fn(), applyOptions: vi.fn(),
      getVisibleRange: vi.fn(() => null), width: vi.fn(() => 800),
    }
    const scales = new Map<string, { width: () => number; applyOptions: () => void }>()
    return {
      addSeries: vi.fn(() => series()), removeSeries: vi.fn(), applyOptions: vi.fn(), remove: vi.fn(),
      timeScale: vi.fn(() => ts),
      priceScale: vi.fn((id: string) => {
        if (!scales.has(id)) scales.set(id, { width: vi.fn(() => 50), applyOptions: vi.fn() })
        return scales.get(id)!
      }),
      subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), resize: vi.fn(),
      setCrosshairPosition: vi.fn(), clearCrosshairPosition: vi.fn(),
    }
  }
  return {
    createChart: vi.fn(() => chart()),
    createSeriesMarkers: vi.fn((_s: unknown, markers: unknown[]) => {
      markerCalls.lists.push(markers)
      return { setMarkers: vi.fn((m: unknown[]) => { markerCalls.lists.push(m) }), detach: vi.fn(), destroy: vi.fn() }
    }),
    CandlestickSeries: 'CandlestickSeries', LineSeries: 'LineSeries', HistogramSeries: 'HistogramSeries',
    ColorType: { Solid: 'Solid' }, LineType: { Simple: 0, WithSteps: 1 },
  }
})

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// ---- NodeBuilder mocks -----------------------------------------------------------

const CLEAN_DIAG = {
  diagnostics: [] as unknown[], byNode: {}, errorCount: 0, warningCount: 0,
  pending: false, offline: false, offlineDetail: null as string | null, hasResult: true,
}
vi.mock('../useDiagnostics', () => ({
  useDiagnostics: () => CLEAN_DIAG,
  useDiagnosticsController: vi.fn(),
  setServerDiagnostics: vi.fn(),
  retryValidation: vi.fn().mockResolvedValue(undefined),
  getDiagnosticsView: () => CLEAN_DIAG,
}))
vi.mock('../DiagnosticsPopover', () => ({ DiagnosticsPopover: () => null }))
vi.mock('../Canvas', () => ({ default: () => <div data-testid="canvas-stub" /> }))
vi.mock('../../../api/nodebuilder', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilder')>()
  return { ...orig, fetchGraphBacktest: vi.fn(), fetchAutoRender: vi.fn() }
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
    getGraph: vi.fn(), createGraph: vi.fn(), saveGraph: vi.fn(), deleteGraph: vi.fn(), seedLegacyGraphs: vi.fn(),
  }
})

// Results' rule-only panels: stubs (they would call the API on their own).
vi.mock('../../strategy/OptimizerPanel', () => ({ default: () => <div data-testid="optimizer-stub">optimizer</div> }))
vi.mock('../../strategy/WalkForwardPanel', () => ({ default: () => <div data-testid="wfa-stub">wfa</div> }))
vi.mock('../../strategy/SensitivityPanel', () => ({ default: () => <div data-testid="sensitivity-stub">sensitivity</div> }))

import { fetchGraphBacktest } from '../../../api/nodebuilder'
import Results from '../../strategy/Results'
import type { StrategyRequest } from '../../../shared/types'
// The canvas (mocked here) normally loads the plugins/ modules that fill the slots.
import '../canvasPlugins'
import Chart, { parseSavedRange } from '../../chart/Chart'
import { GraphResultHint } from '../graphResultUi'
import NodeBuilder from '../NodeBuilder'
import { ChartBar } from '../GraphChartSplit'
import { graphHeaderText, graphResultAsBacktest, graphTrades, type GraphResultState } from '../graphRun'
import { getCommand, listCommands } from '../commands'
import { listSlot } from '../slots'
import { useSheetUi } from '../datasheet/sheetUi'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import { clearNotices } from '../notices'

// ---- fixtures ------------------------------------------------------------------

function sixRoundTrips(): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = []
  for (let i = 0; i < 6; i++) {
    out.push({ type: 'buy', date: `2025-10-${String(2 + i * 4).padStart(2, '0')}`, price: 100 + i, shares: 10 })
    out.push({ type: 'sell', date: `2025-10-${String(4 + i * 4).padStart(2, '0')}`, price: 104 + i, shares: 10, pnl: 40 })
  }
  return out
}

function dailyBars() {
  const bars = []
  for (let d = 1; d <= 31; d++) {
    bars.push({ time: `2025-10-${String(d).padStart(2, '0')}`, open: 100, high: 105, low: 95, close: 102, volume: 1000 })
  }
  return bars
}

function response() {
  return {
    summary: {
      num_trades: 6, total_return_pct: 25.8, sharpe_ratio: 1.4, exit_connected: true, open_position: null,
      initial_capital: 10000, final_value: 12580, buy_hold_return_pct: 10, win_rate_pct: 100, max_drawdown_pct: -3,
    },
    trades: sixRoundTrips(),
    equity_curve: [],
    baseline_curve: [],
    cook_id: 'ck_9',
  }
}

function state(): GraphResultState {
  return {
    origin: 'graph', graphId: 'g_1', rev: 1, displayedGroup: 'main', graphName: 'alpha', graphKey: 'k', cookedAt: 0,
    request: { graph: emptyGraph(), ticker: 'AAPL', start: '2025-10-01', end: '2025-10-31', interval: '1d', source: 'yahoo' },
    response: response() as unknown as GraphResultState['response'],
  }
}

function node(id: string, type: string, name: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}
function smallGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: { n_t: node('n_t', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }), n_e: node('n_e', 'entry', 'entry') },
  }
}

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <NodeBuilder request={null} graphViewActive />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  markerCalls.lists.length = 0
  vi.mocked(fetchGraphBacktest).mockReset()
  useNodeBuilderStore.getState().discardEdits()
  useNodeBuilderStore.getState().resetCooks()
  useNodeBuilderStore.setState({ cook: IDLE_COOK })
})
afterEach(() => cleanup())

// ---- tests ---------------------------------------------------------------------

describe('graph trades on the chart (S28)', () => {
  it('renders 12 markers for 6 round trips through the one Chart', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <Chart
          data={dailyBars()}
          showSpy={false}
          showQqq={false}
          indicators={[]}
          instanceData={{}}
          trades={graphTrades(state())}
          viewInterval="1d"
          backtestInterval="1d"
          ticker="AAPL"
          interval="1d"
          from="2025-10-01"
          to="2025-10-31"
        />
      </QueryClientProvider>,
    )
    const last = markerCalls.lists.at(-1) as { shape: string; position: string; text: string }[]
    expect(last).toHaveLength(12)
    const entries = last.filter(m => m.text === 'B')
    const exits = last.filter(m => m.text === 'S')
    expect(entries).toHaveLength(6)
    expect(exits).toHaveLength(6)
    expect(entries.every(m => m.shape === 'arrowUp' && m.position === 'belowBar')).toBe(true)
    expect(exits.every(m => m.shape === 'arrowDown' && m.position === 'aboveBar')).toBe(true)
  })
})

describe('the chart bar (S28)', () => {
  it('reads "no result yet" with no run, and reflects open in aria-expanded', () => {
    const onToggle = vi.fn()
    const { rerender } = render(
      <ChartBar model={{ result: null, stale: false, running: false, ticker: 'AAPL' }} open={false} onToggle={onToggle} />,
    )
    const bar = screen.getByTestId('nb-chart-bar')
    expect(bar.getAttribute('aria-expanded')).toBe('false')
    expect(bar.getAttribute('aria-controls')).toBe('nb-chart-panel')
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('no result yet · Run backtest (⌘↵)')
    fireEvent.click(bar)
    expect(onToggle).toHaveBeenCalledTimes(1)
    rerender(<ChartBar model={{ result: state(), stale: true, running: false, ticker: 'AAPL' }} open onToggle={onToggle} />)
    expect(bar.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('stale · AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(bar.getAttribute('aria-label')).toBe('Chart: stale · AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')
  })

  it('shows the run summary under the canvas, "running…" while cooking, and "stale · " after a commit', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    type Result = Awaited<ReturnType<typeof fetchGraphBacktest>>
    let resolve: (v: Result) => void = () => {}
    vi.mocked(fetchGraphBacktest).mockReturnValueOnce(new Promise<Result>(r => { resolve = r }))
    renderBuilder()
    expect(screen.getByTestId('nb-split')).toBeInTheDocument()
    expect(screen.getByTestId('nb-split').contains(screen.getByTestId('canvas-stub'))).toBe(true)
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('no result yet · Run backtest (⌘↵)')

    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('running…')
    await act(async () => resolve(response() as unknown as Result))
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(useNodeBuilderStore.getState().cooks.backtest).toMatchObject({ phase: 'cooked', cookId: 'ck_9' })
    // The old results strip is gone: the summary shows in one place.
    expect(screen.getAllByText(/6 trades/)).toHaveLength(1)

    // A second run keeps the last summary on screen while it cooks.
    vi.mocked(fetchGraphBacktest).mockReturnValueOnce(new Promise<Result>(r => { resolve = r }))
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('running… · 6 trades · +25.8% · Sharpe 1.40')
    await act(async () => resolve(response() as unknown as Result))

    await act(async () => {
      useNodeBuilderStore.getState().updateNodeParams('n_t', { symbol: 'MSFT' })
    })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('stale · AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')
    expect(useNodeBuilderStore.getState().cooks.backtest.stale).toBe(true)
  })

  it('keeps the run when the post-load tidy moves nodes, and drops it on a new graph', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    vi.mocked(fetchGraphBacktest).mockResolvedValue(response() as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>)
    renderBuilder()
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')

    // The elk tidy after "Edit this graph" (3.F): new positions and a new
    // layout epoch, same graph. The run still belongs to it.
    await act(async () => {
      useNodeBuilderStore.setState(st => {
        const g = st.graph!
        const moved = { ...g, nodes: Object.fromEntries(Object.entries(g.nodes).map(([id, n]) => [id, { ...n, position: [40, 80] as [number, number] }])) }
        return { graph: moved, savedGraph: moved, layoutEpoch: st.layoutEpoch + 1 }
      })
    })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('AAPL 1d · 6 trades · +25.8% · Sharpe 1.40')

    // A different graph drops it.
    await act(async () => {
      useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_2', rev: 1, name: 'beta' })
    })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toBe('no result yet · Run backtest (⌘↵)')
  })

  it('the Data Sheet drawer sits under the split, inside the canvas column', () => {
    useSheetUi.setState({ open: true })
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    renderBuilder()
    const split = screen.getByTestId('nb-split')
    expect(split.contains(screen.getByTestId('nb-sheet'))).toBe(true)
    const column = split.closest('[data-nb-column]')
    expect(column).not.toBeNull()
  })
})

describe('chart range per view (CI-01)', () => {
  it('restores a saved range only onto the series it was saved on', () => {
    const saved = JSON.stringify({ range: { from: 3400, to: 3900 }, fitKey: 'AAPL|5m|2025-01-02|2025-03-01' })
    expect(parseSavedRange(saved, 'AAPL|5m|2025-01-02|2025-03-01')).toEqual({ from: 3400, to: 3900 })
    // The other view (or another window) gets fitContent instead.
    expect(parseSavedRange(saved, 'AAPL|1d|2025-01-02|2025-03-01')).toBeNull()
    expect(parseSavedRange(null, 'x')).toBeNull()
    expect(parseSavedRange('not json', 'x')).toBeNull()
    // A bare range saved before the key existed still restores.
    expect(parseSavedRange(JSON.stringify({ from: 1, to: 9 }), 'x')).toEqual({ from: 1, to: 9 })
  })
})

describe('graph results in the Results panel (S30)', () => {
  function renderResults(props: Partial<Parameters<typeof Results>[0]>) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={client}>
        <Results
          result={graphResultAsBacktest(state())}
          activeTab="optimizer"
          onTabChange={() => {}}
          bucket={null}
          onBucketChange={() => {}}
          lastRequest={null}
          showBaseline={false}
          onShowBaselineChange={() => {}}
          logScale={false}
          onLogScaleChange={() => {}}
          viewInterval="1d"
          backtestInterval="1d"
          {...props}
        />
      </QueryClientProvider>,
    )
  }

  it('a graph result shows the header and "Not available" for the rule-only tabs', () => {
    const back = vi.fn()
    const st = { ...state(), cookedAt: new Date(2026, 8, 11, 12, 4, 31).getTime() }
    renderResults({ graphInfo: { headerText: graphHeaderText(st), stale: true, onBackToChart: back } })
    const header = screen.getByTestId('results-graph-header')
    expect(header.getAttribute('role')).toBe('status')
    const text = `${within(header).getByText('GRAPH').textContent} ${screen.getByTestId('results-graph-header-text').textContent}`
    expect(text).toMatch(/^GRAPH .+ @ rev \d+ · [A-Z.]+ \w+ · \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} · cooked \d{2}:\d{2}:\d{2}$/)
    expect(screen.getByTestId('results-graph-stale')).toBeInTheDocument()
    // No "Show graph" in graph view (no handler given).
    expect(screen.queryByTestId('results-show-graph')).toBeNull()

    const block = screen.getByTestId('results-not-available')
    expect(block.textContent).toContain('Not available for graph results')
    expect(screen.queryByTestId('optimizer-stub')).toBeNull()
    fireEvent.click(screen.getByTestId('results-back-to-chart'))
    expect(back).toHaveBeenCalledTimes(1)

    // The three tabs stay, dimmed when not active, through a class whose
    // :focus-visible rule restores full contrast (UX-15), not an inline opacity.
    expect(screen.getByRole('button', { name: 'Sensitivity' }).className).toBe('results-tab-dim')
    expect(screen.getByRole('button', { name: 'Walk-Forward' }).className).toBe('results-tab-dim')
    expect(screen.getByRole('button', { name: 'Optimizer' }).className).toBe('')
    expect(screen.getByRole('button', { name: 'Sensitivity' }).style.opacity).toBe('')
    // CI-06: no macro bucket buttons for a graph result (they need a rule request).
    expect(screen.queryByRole('button', { name: 'W' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Detail' })).toBeNull()
  })

  it('a graph result ignores a stored macro bucket (CI-06)', () => {
    renderResults({ activeTab: 'summary', bucket: 'M', graphInfo: { headerText: 'x', stale: false } })
    expect(screen.queryByRole('button', { name: 'M' })).toBeNull()
    expect(screen.queryByText(/No macro data/)).toBeNull()
  })

  it('the chart view hint offers Show graph and carries the full header as its title (UX-14)', () => {
    const show = vi.fn()
    render(<GraphResultHint headerText="alpha @ rev 3 · MSFT 1d · 2025-01-02 → 2025-12-31 · cooked 12:04:31" onShowGraph={show} />)
    expect(screen.getByTestId('results-graph-hint').textContent).toContain('Graph result available')
    fireEvent.click(screen.getByTestId('results-show-graph'))
    expect(show).toHaveBeenCalledTimes(1)
  })

  it('the summary tab of a graph result has no block', () => {
    renderResults({ activeTab: 'summary', graphInfo: { headerText: 'x', stale: false } })
    expect(screen.queryByTestId('results-not-available')).toBeNull()
    expect(screen.queryByTestId('results-graph-stale')).toBeNull()
  })

  it('a rule result shows neither the header nor the block', () => {
    const req = { ticker: 'AAPL', start: '2025-01-02', end: '2025-12-31', interval: '1d', buy_rules: [], sell_rules: [] } as unknown as StrategyRequest
    renderResults({ lastRequest: req })
    expect(screen.queryByTestId('results-graph-header')).toBeNull()
    expect(screen.queryByTestId('results-not-available')).toBeNull()
    expect(screen.getByTestId('optimizer-stub')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sensitivity' }).className).toBe('')
    expect(screen.getByRole('button', { name: 'W' })).toBeInTheDocument()
  })
})

describe('workspace keys and toolbar items (S25, S27, S28)', () => {
  it('binds A, S and Shift+V', () => {
    const byId = (id: string) => listCommands().find(c => c.id === id)
    expect(byId('cook.toggleAuto')?.keys).toEqual(['a'])
    expect(byId('panels.toggleSheet')?.keys).toEqual(['s'])
    expect(byId('panels.toggleChart')?.keys).toEqual(['shift+v'])
  })

  it('S toggles the Data Sheet, and its checked state follows the sheet', () => {
    useSheetUi.setState({ open: false })
    const cmd = getCommand('panels.toggleSheet')!
    expect(cmd.checked?.(useNodeBuilderStore.getState())).toBe(false)
    cmd.run({ canvas: null, store: useNodeBuilderStore, event: null })
    expect(useSheetUi.getState().open).toBe(true)
    expect(cmd.checked?.(useNodeBuilderStore.getState())).toBe(true)
  })

  it('puts Auto cook at order 20 and the sheet toggle with the panel toggles', () => {
    const right = listSlot('toolbarRight')
    const auto = right.find(e => e.id === 'autocook')
    const sheet = right.find(e => e.id === 'sheetToggle')
    expect(auto?.order).toBe(20)
    expect(sheet?.order).toBeGreaterThanOrEqual(90)
  })

  it('draws the Auto cook switch and the sheet toggle in the toolbar while editing', () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    renderBuilder()
    expect(screen.getByTestId('nb-autocook')).toBeInTheDocument()
    expect(screen.getByTestId('nb-btn-sheet')).toBeInTheDocument()
  })
})
