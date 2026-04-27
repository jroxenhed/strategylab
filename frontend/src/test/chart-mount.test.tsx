/**
 * Chart mount/unmount smoke tests — exercises the teardown path that
 * historically caused "paneWidgets[0]" crashes when refs were accessed
 * after chart.remove().
 *
 * lightweight-charts needs a real canvas, so we mock the module with stubs
 * that expose the same API surface Chart.tsx calls.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

/* ── lightweight-charts mock ─────────────────────────────────────── */

// Track all created chart instances so tests can assert on cleanup calls
const chartInstances: ReturnType<typeof makeChart>[] = []

function makeSeriesApi() {
  const markers: any[] = []
  return {
    setData: vi.fn(),
    update: vi.fn(),
    applyOptions: vi.fn(),
    priceScale: vi.fn(() => ({ width: vi.fn(() => 50) })),
    setMarkers: vi.fn((m: any[]) => { markers.push(...m) }),
    markers: () => markers,
    // lightweight-charts v5 createSeriesMarkers expects a series ref
    coordinateToPrice: vi.fn(() => 0),
    priceToCoordinate: vi.fn(() => 0),
  }
}

function makeTimeScale() {
  let rangeHandler: ((range: any) => void) | null = null
  return {
    subscribeVisibleLogicalRangeChange: vi.fn((fn: any) => { rangeHandler = fn }),
    unsubscribeVisibleLogicalRangeChange: vi.fn(() => { rangeHandler = null }),
    getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 100 })),
    setVisibleLogicalRange: vi.fn(),
    fitContent: vi.fn(),
    scrollToPosition: vi.fn(),
    applyOptions: vi.fn(),
    // Expose for potential test introspection
    _rangeHandler: () => rangeHandler,
  }
}

function makeChart() {
  const ts = makeTimeScale()
  const priceScales = new Map<string, ReturnType<typeof makePriceScale>>()

  function makePriceScale() {
    return { width: vi.fn(() => 50), applyOptions: vi.fn() }
  }

  const chart = {
    addSeries: vi.fn(() => makeSeriesApi()),
    removeSeries: vi.fn(),
    applyOptions: vi.fn(),
    remove: vi.fn(),
    timeScale: vi.fn(() => ts),
    priceScale: vi.fn((id: string) => {
      if (!priceScales.has(id)) priceScales.set(id, makePriceScale())
      return priceScales.get(id)!
    }),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    resize: vi.fn(),
  }

  chartInstances.push(chart)
  return chart
}

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn((_container: HTMLElement, _opts?: any) => makeChart()),
  createSeriesMarkers: vi.fn((_series: any, markers: any[]) => ({
    setMarkers: vi.fn(),
    destroy: vi.fn(),
  })),
  CandlestickSeries: 'CandlestickSeries',
  LineSeries: 'LineSeries',
  HistogramSeries: 'HistogramSeries',
  ColorType: { Solid: 'Solid' },
}))

/* ── ResizeObserver stub (jsdom lacks it) ────────────────────────── */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as any).ResizeObserver = ResizeObserverStub
}

/* ── import Chart after mocks are in place ───────────────────────── */

// Dynamic import so vi.mock() is hoisted before the module loads
const { default: Chart } = await import('../features/chart/Chart')

/* ── helpers ─────────────────────────────────────────────────────── */

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client }, children)
}

const MINIMAL_BARS = [
  { time: '2024-01-02', open: 100, high: 105, low: 99, close: 104, volume: 1000 },
  { time: '2024-01-03', open: 104, high: 108, low: 103, close: 107, volume: 1200 },
  { time: '2024-01-04', open: 107, high: 110, low: 106, close: 109, volume: 900 },
]

function renderChart(overrides = {}) {
  const props = {
    data: MINIMAL_BARS,
    showSpy: false,
    showQqq: false,
    indicators: [],
    instanceData: {},
    trades: [],
    emaOverlays: [],
    viewInterval: '1d',
    backtestInterval: '1d',
    ...overrides,
  }
  return render(createElement(Chart, props), { wrapper })
}

/* ── tests ───────────────────────────────────────────────────────── */

describe('Chart mount/unmount lifecycle', () => {
  beforeEach(() => {
    chartInstances.length = 0
  })
  afterEach(() => {
    cleanup()
  })

  it('mounts without throwing', () => {
    expect(() => renderChart()).not.toThrow()
    // At least the main chart should be created
    expect(chartInstances.length).toBeGreaterThanOrEqual(1)
  })

  it('unmounts without throwing (exercises teardown guards)', () => {
    const { unmount } = renderChart()
    expect(() => unmount()).not.toThrow()

    // chart.remove() should have been called on every instance
    for (const chart of chartInstances) {
      expect(chart.remove).toHaveBeenCalled()
    }
  })

  it('survives mount → unmount → remount cycle', () => {
    // First mount
    const { unmount } = renderChart()
    const firstBatchCount = chartInstances.length

    // Unmount
    expect(() => unmount()).not.toThrow()

    // Remount with fresh data (simulates ticker change)
    chartInstances.length = 0
    expect(() => renderChart()).not.toThrow()
    expect(chartInstances.length).toBeGreaterThanOrEqual(1)
  })

  it('mount → unmount with trades does not throw', () => {
    const trades = [
      { type: 'buy' as const, date: '2024-01-02', price: 100, shares: 10 },
      { type: 'sell' as const, date: '2024-01-04', price: 109, shares: 10, pnl: 90 },
    ]
    const { unmount } = renderChart({ trades })
    expect(() => unmount()).not.toThrow()
  })

  it('mount with indicators does not throw', () => {
    const indicators = [
      { id: 'rsi-1', type: 'rsi', params: { period: 14 }, enabled: true, pane: 'sub' },
    ]
    const instanceData = {
      'rsi-1': {
        rsi: [
          { time: '2024-01-02', value: null },
          { time: '2024-01-03', value: 55 },
          { time: '2024-01-04', value: 60 },
        ],
      },
    }
    expect(() => renderChart({ indicators, instanceData })).not.toThrow()
  })

  it('calls onChartReady with chart on mount and null on unmount', () => {
    const onChartReady = vi.fn()
    const { unmount } = renderChart({ onChartReady })

    // Should have been called with a chart-like object on mount
    expect(onChartReady).toHaveBeenCalled()
    const firstCall = onChartReady.mock.calls[0][0]
    expect(firstCall).not.toBeNull()

    // On unmount, should be called with null
    onChartReady.mockClear()
    unmount()
    expect(onChartReady).toHaveBeenCalledWith(null)
  })

  it('unsubscribes from timeScale range changes on unmount', () => {
    const { unmount } = renderChart()
    unmount()

    // The main chart's timeScale should have unsubscribe called
    const mainChart = chartInstances[0]
    const ts = mainChart.timeScale()
    expect(ts.unsubscribeVisibleLogicalRangeChange).toHaveBeenCalled()
  })

  it('unsubscribes crosshair move on unmount', () => {
    const { unmount } = renderChart()
    unmount()

    const mainChart = chartInstances[0]
    expect(mainChart.unsubscribeCrosshairMove).toHaveBeenCalled()
  })
})
