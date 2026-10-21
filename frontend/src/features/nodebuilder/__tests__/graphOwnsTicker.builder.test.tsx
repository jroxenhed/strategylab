/**
 * The graph owns its symbol and interval, in the running builder (F453 item 1).
 *
 * With the app's sidebar on RIVN 30m and a graph whose Ticker says SPY 5m:
 * - auto cook previews SPY 5m (the sidebar's dates and source);
 * - Run backtest sends SPY 5m, what "Spawn bots" would trade;
 * - an edit of the Ticker's symbol moves the cook and marks the result stale;
 * - the empty graph's "Ticker" button adds a Ticker on the sidebar's RIVN 30m.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

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

import { fetchGraphBacktest } from '../../../api/nodebuilder'
import { preview } from '../../../api/nodebuilderInspect'
import '../canvasPlugins'
import NodeBuilder from '../NodeBuilder'
import type { GraphWindow } from '../graphRun'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import { clearNotices } from '../notices'
import { AUTO_COOK_DEBOUNCE_MS } from '../useAutoCook'

const SIDEBAR: GraphWindow = { ticker: 'RIVN', start: '2026-01-02', end: '2026-06-30', interval: '30m', source: 'alpaca-iex' }

function node(id: string, type: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

function g1(): Graph {
  return {
    ...emptyGraph(),
    nodes: { tick: node('tick', 'ticker', { symbol: 'SPY', interval: '5m' }), entry: node('entry', 'entry') },
    wires: [{ id: 'w1', from: 'tick', to: 'entry', from_port: 'out', to_port: 'in0' }],
  }
}

function response() {
  return {
    summary: { num_trades: 0, total_return_pct: 0, sharpe_ratio: 0 },
    trades: [], equity_curve: [], baseline_curve: [], cook_id: 'ck_9',
  }
}

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <NodeBuilder request={null} graphViewActive graphWindow={SIDEBAR} />
    </QueryClientProvider>,
  )
}

const st = () => useNodeBuilderStore.getState()
const wait = (ms: number) => act(() => new Promise<void>(r => setTimeout(r, ms)))

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  vi.mocked(fetchGraphBacktest).mockReset()
  vi.mocked(preview).mockClear()
  st().discardEdits()
  st().resetCooks()
  useNodeBuilderStore.setState({ cook: IDLE_COOK, autoCook: true })
})
afterEach(() => cleanup())

describe('the builder runs on the graph\'s Ticker (F453)', () => {
  it('auto cook previews the Ticker\'s symbol and interval with the sidebar\'s dates and source', async () => {
    st().openGraph(g1(), { id: 'g_1', rev: 1, name: 'G1' })
    renderBuilder()
    await wait(AUTO_COOK_DEBOUNCE_MS + 100)
    const req = vi.mocked(preview).mock.calls.at(-1)![0]
    expect(req.window).toEqual({ ...SIDEBAR, ticker: 'SPY', interval: '5m' })

    // An edit of the Ticker moves the cook to the new symbol.
    vi.mocked(preview).mockClear()
    await act(async () => { st().updateNodeParams('tick', { symbol: 'qqq' }) })
    await wait(AUTO_COOK_DEBOUNCE_MS + 100)
    expect(vi.mocked(preview).mock.calls.at(-1)![0].window).toMatchObject({ ticker: 'QQQ', interval: '5m' })
  })

  it('Run backtest sends the Ticker\'s symbol and interval, and a Ticker edit makes the result stale', async () => {
    st().openGraph(g1(), { id: 'g_1', rev: 1, name: 'G1' })
    vi.mocked(fetchGraphBacktest).mockResolvedValue(response() as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>)
    renderBuilder()
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    const req = vi.mocked(fetchGraphBacktest).mock.calls[0][0]
    expect(req).toMatchObject({ ticker: 'SPY', interval: '5m', start: SIDEBAR.start, end: SIDEBAR.end, source: 'alpaca-iex' })
    expect(screen.getByTestId('nb-chart-bar-summary').textContent).toContain('SPY 5m')
    expect(st().cooks.backtest.stale).toBe(false)
    await act(async () => { st().updateNodeParams('tick', { interval: '15m' }) })
    expect(st().cooks.backtest.stale).toBe(true)
  })

  it('an empty symbol runs on the sidebar, as before', async () => {
    const g = g1()
    g.nodes.tick.params = { symbol: '', interval: '5m' } as GraphNode['params']
    st().openGraph(g, { id: 'g_1', rev: 1, name: 'G1' })
    vi.mocked(fetchGraphBacktest).mockResolvedValue(response() as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>)
    renderBuilder()
    await act(async () => { fireEvent.click(screen.getByTestId('nb-btn-run')) })
    expect(vi.mocked(fetchGraphBacktest).mock.calls[0][0]).toMatchObject({ ticker: 'RIVN', interval: '30m' })
  })

  it('the empty graph\'s Ticker button adds a Ticker on the sidebar\'s symbol and interval', async () => {
    act(() => st().newGraph())
    renderBuilder()
    await act(async () => { fireEvent.click(screen.getByTestId('nb-empty-ticker')) })
    const tick = Object.values(st().graph!.nodes).find(n => n.type === 'ticker')!
    expect(tick.params).toMatchObject({ symbol: 'RIVN', interval: '30m' })
  })
})
