/**
 * RulesStrip (F454): the graph's strategy as plain text under the chart bar.
 * The describe call is mocked; these tests cover the debounce, the abort of
 * an old request, the quiet error, and what the bar and the body show.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { createElement } from 'react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import type { DescribeResponse } from '../../../api/describe'

const describeGraph = vi.fn()
vi.mock('../../../api/describe', async importOriginal => {
  const real = await importOriginal<typeof import('../../../api/describe')>()
  return { ...real, describeGraph: (...args: unknown[]) => describeGraph(...args) }
})

const { RulesStrip, rulesKey, RULES_DEBOUNCE_MS, RULES_OPEN_KEY } = await import('../RulesStrip')
const { SidebarWindowContext } = await import('../sidebarWindow')

function node(id: string, type: string, params: GraphNode['params'] = {}, position: [number, number] = [0, 0]): GraphNode {
  return { id, type, name: id, parent: null, params, position, display: false, bypass: false }
}

function graphWith(period = 14, pos: [number, number] = [0, 0]): Graph {
  const g = emptyGraph()
  g.nodes = {
    t1: node('t1', 'ticker', { symbol: 'SPY', interval: '5m' }),
    rsi1: node('rsi1', 'rsi', { period }, pos),
  }
  g.wires = [{ id: 'w1', from: 't1', to: 'rsi1', from_port: 'out', to_port: 'in0' }]
  return g
}

const G1_TEXT: DescribeResponse = {
  groups: [],
  lines: [
    'Buy SPY (5m) when RSI(14) is below 40.',
    'Sell when RSI(14) is above 60.',
    'Stop: 0.5 % below the entry price.',
  ],
  warnings: [],
}

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

/** Run the debounce and let the mocked promise settle. */
async function flush(ms = RULES_DEBOUNCE_MS) {
  await act(async () => { vi.advanceTimersByTime(ms) })
  await act(async () => { await Promise.resolve() })
}

beforeEach(() => {
  vi.useFakeTimers()
  describeGraph.mockReset()
  try { localStorage.removeItem(RULES_OPEN_KEY) } catch { /* ignore */ }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('rulesKey', () => {
  it('ignores node positions but not params', () => {
    expect(rulesKey(graphWith(14, [0, 0]))).toBe(rulesKey(graphWith(14, [300, 120])))
    expect(rulesKey(graphWith(14))).not.toBe(rulesKey(graphWith(20)))
  })
})

describe('RulesStrip', () => {
  it('asks once, after the debounce, and shows the text in the bar', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    render(createElement(RulesStrip, { graph: graphWith() }))
    await act(async () => { vi.advanceTimersByTime(RULES_DEBOUNCE_MS - 1) })
    expect(describeGraph).not.toHaveBeenCalled()
    await flush(1)
    expect(describeGraph).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('nb-rules-summary').textContent).toBe(G1_TEXT.lines.join(' '))
  })

  it('sends one request for quick edits, with the newest graph', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    const { rerender } = render(createElement(RulesStrip, { graph: graphWith(14) }))
    await act(async () => { vi.advanceTimersByTime(200) })
    rerender(createElement(RulesStrip, { graph: graphWith(15) }))
    await act(async () => { vi.advanceTimersByTime(200) })
    rerender(createElement(RulesStrip, { graph: graphWith(16) }))
    await flush()
    expect(describeGraph).toHaveBeenCalledTimes(1)
    expect((describeGraph.mock.calls[0][0] as Graph).nodes.rsi1.params.period).toBe(16)
  })

  it('does not ask again when only a node moved', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    const { rerender } = render(createElement(RulesStrip, { graph: graphWith(14, [0, 0]) }))
    await flush()
    rerender(createElement(RulesStrip, { graph: graphWith(14, [500, 80]) }))
    await flush()
    expect(describeGraph).toHaveBeenCalledTimes(1)
  })

  it('cancels the old request when the graph changes, and keeps only the new answer', async () => {
    const first = deferred<DescribeResponse>()
    describeGraph.mockReturnValueOnce(first.promise).mockResolvedValueOnce(G1_TEXT)
    const { rerender } = render(createElement(RulesStrip, { graph: graphWith(14) }))
    await flush()
    const firstSignal = describeGraph.mock.calls[0][2] as AbortSignal
    expect(firstSignal.aborted).toBe(false)
    rerender(createElement(RulesStrip, { graph: graphWith(20) }))
    expect(firstSignal.aborted).toBe(true)
    await flush()
    expect(describeGraph).toHaveBeenCalledTimes(2)
    // The old answer arriving late changes nothing.
    await act(async () => { first.resolve({ groups: [], lines: ['Old text.'], warnings: [] }) })
    expect(screen.getByTestId('nb-rules-summary').textContent).toBe(G1_TEXT.lines.join(' '))
  })

  it('cancels the request when the strip goes away', async () => {
    describeGraph.mockReturnValue(new Promise(() => {}))
    const { unmount } = render(createElement(RulesStrip, { graph: graphWith() }))
    await flush()
    const signal = describeGraph.mock.calls[0][2] as AbortSignal
    unmount()
    expect(signal.aborted).toBe(true)
  })

  it('shows an error quietly in the strip', async () => {
    describeGraph.mockRejectedValue(new Error('cycle in the graph'))
    render(createElement(RulesStrip, { graph: graphWith() }))
    await flush()
    expect(screen.getByTestId('nb-rules-summary').textContent).toBe("can't read the rules yet")
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByTestId('nb-rules-bar'))
    expect(screen.getByTestId('nb-rules-error').textContent).toBe("Can't read the rules yet: cycle in the graph")
  })

  it('opens to one line per sentence plus warnings, and remembers it is open', async () => {
    describeGraph.mockResolvedValue({ ...G1_TEXT, warnings: ["Node 'wr1' runs Python code; the text shows only its name."] })
    render(createElement(RulesStrip, { graph: graphWith() }))
    await flush()
    // UX-4: the count sits before the text, outside the part cut with "…".
    const count = screen.getByTestId('nb-rules-count')
    expect(count.textContent).toBe('1 warning')
    expect(count.compareDocumentPosition(screen.getByTestId('nb-rules-summary')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByTestId('nb-rules-summary').contains(count)).toBe(false)
    expect(screen.queryByTestId('nb-rules-body')).toBeNull()
    fireEvent.click(screen.getByTestId('nb-rules-bar'))
    expect(screen.getByTestId('nb-rules-bar').getAttribute('aria-expanded')).toBe('true')
    // Open: the bar shows only the title and the warning count, not the text again.
    expect(screen.queryByTestId('nb-rules-summary')).toBeNull()
    expect(screen.getByTestId('nb-rules-count').textContent).toBe('1 warning')
    expect(screen.getAllByTestId('nb-rules-line').map(e => e.textContent)).toEqual(G1_TEXT.lines)
    expect(screen.getAllByTestId('nb-rules-warning')).toHaveLength(1)
    expect(localStorage.getItem(RULES_OPEN_KEY)).toBe('1')
  })

  it('sends nothing for an empty graph', async () => {
    render(createElement(RulesStrip, { graph: emptyGraph() }))
    await flush()
    expect(describeGraph).not.toHaveBeenCalled()
    expect(screen.getByTestId('nb-rules-summary').textContent).toBe('no nodes yet')
  })

  // F453 contract (IN-1, UX-1): the strip sends what the editor run uses.
  const SIDEBAR_WIN = { ticker: 'RIVN', interval: '30m', start: '2024-01-01', end: '2024-06-01', source: 'yahoo' }

  it("sends the Ticker's own symbol and interval, as the run uses them", async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    render(createElement(SidebarWindowContext.Provider, { value: SIDEBAR_WIN }, createElement(RulesStrip, { graph: graphWith(), direction: null })))
    await flush()
    expect(describeGraph.mock.calls[0][1]).toEqual({ ticker: 'SPY', interval: '5m', direction: null })
  })

  it('a Ticker with an empty symbol sends the whole sidebar window (symbol and interval)', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    const g = graphWith()
    g.nodes.t1.params = { symbol: '', interval: '1d' }
    render(createElement(SidebarWindowContext.Provider, { value: SIDEBAR_WIN }, createElement(RulesStrip, { graph: g, direction: null })))
    await flush()
    expect(describeGraph.mock.calls[0][1]).toEqual({ ticker: 'RIVN', interval: '30m', direction: null })
  })

  it('a Ticker with a symbol and no interval sends 1d, as the run and spawn do', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    const g = graphWith()
    g.nodes.t1.params = { symbol: 'SPY', interval: '' }
    render(createElement(SidebarWindowContext.Provider, { value: SIDEBAR_WIN }, createElement(RulesStrip, { graph: g, direction: null })))
    await flush()
    expect(describeGraph.mock.calls[0][1]).toEqual({ ticker: 'SPY', interval: '1d', direction: null })
  })

  it('sends the given window as is', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    render(createElement(RulesStrip, { graph: graphWith(), window: { ticker: 'QQQ', interval: '1h' }, direction: 'long' }))
    await flush()
    expect(describeGraph.mock.calls[0][1]).toEqual({ ticker: 'QQQ', interval: '1h', direction: 'long' })
  })

  it('sends the direction and asks again when it changes (UX-1)', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    const { rerender } = render(createElement(RulesStrip, { graph: graphWith(), window: null, direction: 'long' }))
    await flush()
    expect(describeGraph.mock.calls[0][1]).toMatchObject({ direction: 'long' })
    rerender(createElement(RulesStrip, { graph: graphWith(), window: null, direction: 'short' }))
    await flush()
    expect(describeGraph).toHaveBeenCalledTimes(2)
    expect(describeGraph.mock.calls[1][1]).toMatchObject({ direction: 'short' })
  })

  it('sends no direction for a graph with Output Groups (each group sets its own)', async () => {
    describeGraph.mockResolvedValue(G1_TEXT)
    const g = graphWith()
    g.nodes.og = node('og', 'output_group', { direction: 'long' })
    render(createElement(RulesStrip, { graph: g, window: null, direction: 'short' }))
    await flush()
    expect(describeGraph.mock.calls[0][1]).toMatchObject({ direction: null })
  })
})
