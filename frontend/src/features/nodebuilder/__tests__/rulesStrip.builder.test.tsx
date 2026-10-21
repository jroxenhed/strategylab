/**
 * The Rules strip in the running builder (F454): it sits under the chart
 * bar, describes the open graph (with the sidebar's symbol and interval as
 * fallbacks only), and refreshes after an edit.
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
vi.mock('../../../api/describe', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/describe')>()
  return { ...orig, describeGraph: vi.fn() }
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
import { describeGraph } from '../../../api/describe'
import '../canvasPlugins'
import NodeBuilder from '../NodeBuilder'
import type { GraphWindow } from '../graphRun'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import { clearNotices } from '../notices'
import { RULES_DEBOUNCE_MS } from '../RulesStrip'
import { RUN_WINDOW_TIP } from '../RunWindowChip'
import { STRATEGY_STORAGE_KEY } from '../graphRun'
import { GRAPH_RUN_SETTINGS_EVENT } from '../ownership'

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
  vi.mocked(describeGraph).mockReset()
  vi.mocked(describeGraph).mockResolvedValue({
    groups: [],
    lines: ['Buy SPY (5m) when RSI(14) is below 40.', 'Sell when RSI(14) is above 60.'],
    warnings: [],
  })
  st().discardEdits()
  st().resetCooks()
  useNodeBuilderStore.setState({ cook: IDLE_COOK, autoCook: true })
})
afterEach(() => cleanup())

describe('the Rules strip in the builder (F454)', () => {
  it('describes the open graph under the chart bar and refreshes after an edit', async () => {
    st().openGraph(g1(), { id: 'g_1', rev: 1, name: 'G1' })
    renderBuilder()
    const strip = screen.getByTestId('nb-rules')
    // Under the chart bar, above the Data Sheet, inside the split.
    expect(screen.getByTestId('nb-split').contains(strip)).toBe(true)
    expect(screen.getByTestId('nb-chart-bar').compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    await wait(RULES_DEBOUNCE_MS + 100)
    expect(vi.mocked(describeGraph)).toHaveBeenCalledTimes(1)
    const [graph, fallback] = vi.mocked(describeGraph).mock.calls[0]
    expect(Object.keys(graph.nodes).sort()).toEqual(['entry', 'tick'])
    // F453 contract: the run's own symbol and interval (the Ticker's SPY 5m,
    // not the sidebar's RIVN 30m) and the settings-panel direction (none saved).
    expect(fallback).toEqual({ ticker: 'SPY', interval: '5m', direction: null })
    expect(screen.getByTestId('nb-rules-summary').textContent)
      .toBe('Buy SPY (5m) when RSI(14) is below 40. Sell when RSI(14) is above 60.')

    await act(async () => { st().updateNodeParams('tick', { symbol: 'qqq' }) })
    await wait(RULES_DEBOUNCE_MS + 100)
    expect(vi.mocked(describeGraph)).toHaveBeenCalledTimes(2)
    expect(vi.mocked(describeGraph).mock.calls[1][0].nodes.tick.params.symbol).toBe('qqq')
  })

  it('opens to one sentence per line', async () => {
    st().openGraph(g1(), { id: 'g_1', rev: 1, name: 'G1' })
    renderBuilder()
    await wait(RULES_DEBOUNCE_MS + 100)
    fireEvent.click(screen.getByTestId('nb-rules-bar'))
    expect(screen.getAllByTestId('nb-rules-line').map(e => e.textContent))
      .toEqual(['Buy SPY (5m) when RSI(14) is below 40.', 'Sell when RSI(14) is above 60.'])
  })

  it('follows the settings-panel direction (UX-1)', async () => {
    st().openGraph(g1(), { id: 'g_1', rev: 1, name: 'G1' })
    renderBuilder()
    await wait(RULES_DEBOUNCE_MS + 100)
    expect(vi.mocked(describeGraph).mock.calls[0][1]).toMatchObject({ direction: null })
    await act(async () => {
      localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({ direction: 'short' }))
      window.dispatchEvent(new Event(GRAPH_RUN_SETTINGS_EVENT))
    })
    await wait(RULES_DEBOUNCE_MS + 100)
    expect(vi.mocked(describeGraph)).toHaveBeenCalledTimes(2)
    expect(vi.mocked(describeGraph).mock.calls[1][1]).toEqual({ ticker: 'SPY', interval: '5m', direction: 'short' })
  })

  it('the toolbar chip names what a run uses, with who sets what (UX-2)', async () => {
    st().openGraph(g1(), { id: 'g_1', rev: 1, name: 'G1' })
    renderBuilder()
    const chip = screen.getByTestId('nb-run-window')
    expect(chip.textContent).toBe('SPY · 5m')
    expect(chip.getAttribute('title')).toBe(RUN_WINDOW_TIP)
    // An empty symbol: the sidebar's symbol and interval, and the tooltip says so.
    await act(async () => { st().updateNodeParams('tick', { symbol: '' }) })
    expect(screen.getByTestId('nb-run-window').textContent).toBe('RIVN · 30m')
    expect(screen.getByTestId('nb-run-window').getAttribute('title')).toContain('no symbol')
  })
})
