/**
 * Tests for WalkForwardPanel.
 *
 * Mocks:
 *   - lightweight-charts  →  stub (no canvas needed)
 *   - ../../api/client    →  api.defaults.baseURL + api.post stub
 *   - globalThis.fetch    →  SSE stream mock (WalkForwardPanel uses fetch(), not api.post)
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { createElement } from 'react'
import type { StrategyRequest } from '../../shared/types'

// ---------------------------------------------------------------------------
// Mock: lightweight-charts
// ---------------------------------------------------------------------------

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({
      setData: vi.fn(),
      applyOptions: vi.fn(),
      priceScale: vi.fn(() => ({ width: vi.fn(() => 50) })),
    })),
    timeScale: vi.fn(() => ({
      fitContent: vi.fn(),
      scrollToPosition: vi.fn(),
      setVisibleLogicalRange: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      applyOptions: vi.fn(),
    })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    resize: vi.fn(),
  })),
  LineSeries: 'LineSeries',
  ColorType: { Solid: 'Solid' },
}))

// ---------------------------------------------------------------------------
// Mock: api client
// WalkForwardPanel uses api.defaults.baseURL to compute the fetch URL, then
// calls native fetch() directly (SSE stream can't go through axios).
// ---------------------------------------------------------------------------

const mockApiPost = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
    defaults: { baseURL: '' },
  },
}))

// ---------------------------------------------------------------------------
// SSE fetch mock helpers
// ---------------------------------------------------------------------------

/**
 * Build a mock fetch Response whose body is a ReadableStream emitting
 * SSE-formatted events.  Each entry in `events` is serialised as:
 *   data: <JSON>\n\n
 * The WalkForwardPanel SSE parser expects this exact framing.
 */
function makeSseFetchMock(responseData: unknown) {
  // Encode the single "result" SSE event the component needs.
  const resultEvent = `data: ${JSON.stringify({ type: 'result', ...(responseData as object) })}\n\n`
  const encoded = new TextEncoder().encode(resultEvent)

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoded)
      controller.close()
    },
  })

  return vi.fn().mockResolvedValue({
    ok: true,
    body: stream,
    text: () => Promise.resolve(''),
  })
}

/** Restore the original fetch after each test that overrides it. */
let originalFetch: typeof globalThis.fetch

// ---------------------------------------------------------------------------
// ResizeObserver stub
// ---------------------------------------------------------------------------

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// ---------------------------------------------------------------------------
// Dynamic import (after mocks are hoisted)
// ---------------------------------------------------------------------------

const { default: WalkForwardPanel } = await import('./WalkForwardPanel')

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRequest(overrides: Partial<StrategyRequest> = {}): StrategyRequest {
  return {
    ticker: 'AAPL',
    interval: '1d',
    start: '2020-01-01',
    end: '2024-12-31',
    initial_capital: 10000,
    buy_rules: [],
    sell_rules: [],
    stop_loss_pct: null,
    take_profit_pct: null,
    trailing_stop: null,
    slippage_bps: 2,
    per_share_rate: 0,
    min_per_order: 0,
    borrow_rate_annual: 0.5,
    direction: 'long',
    source: 'yahoo',
    regime: null,
    ...overrides,
  } as StrategyRequest
}

function makeTwoWindowResponse() {
  return {
    data: {
      windows: [
        {
          window_index: 0,
          is_start: '2020-01-02',
          is_end: '2020-12-31',
          oos_start: '2021-01-04',
          oos_end: '2021-03-31',
          best_params: { 'rules.0.value': 14 },
          is_sharpe: 1.2,
          is_metrics: { sharpe_ratio: 1.2, total_return_pct: 12.0, num_trades: 40, win_rate_pct: 55.0, max_drawdown_pct: -8.0, final_value: 11200 },
          oos_metrics: { sharpe_ratio: 0.8, total_return_pct: 5.0, num_trades: 12, win_rate_pct: 50.0, max_drawdown_pct: -4.0, final_value: 10500 },
          stability_tag: 'stable_plateau',
          is_combo_count: 5,
          scale_factor: 1.0,
        },
        {
          window_index: 1,
          is_start: '2021-01-04',
          is_end: '2021-12-31',
          oos_start: '2022-01-03',
          oos_end: '2022-03-31',
          best_params: { 'rules.0.value': 18 },
          is_sharpe: 1.5,
          is_metrics: { sharpe_ratio: 1.5, total_return_pct: 18.0, num_trades: 45, win_rate_pct: 60.0, max_drawdown_pct: -6.0, final_value: 11800 },
          oos_metrics: { sharpe_ratio: 0.4, total_return_pct: 2.0, num_trades: 8, win_rate_pct: 45.0, max_drawdown_pct: -5.0, final_value: 10200 },
          stability_tag: 'spike',
          is_combo_count: 5,
          scale_factor: 1.05,
        },
      ],
      stitched_equity: [
        { time: '2021-01-04', value: 10000 },
        { time: '2021-03-31', value: 10500 },
        { time: '2022-01-03', value: 11025 },
        { time: '2022-03-31', value: 10710 },
      ],
      wfe: 0.549,
      param_cv: { 'rules.0.value': 0.185 },
      total_combos: 10,
      total_oos_trades: 20,
      low_trades_is_count: 0,
      low_windows_warn: true,
      timed_out: false,
    },
  }
}

function renderPanel(reqOverrides: Partial<StrategyRequest> = {}) {
  const req = makeRequest(reqOverrides)
  return render(createElement(WalkForwardPanel, { lastRequest: req }))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WalkForwardPanel', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    // Restore fetch if a test overrode it.
    if (originalFetch !== undefined) {
      globalThis.fetch = originalFetch
      originalFetch = undefined as unknown as typeof fetch
    }
    // Clear persisted panel config between tests so saved state from one test
    // doesn't leak into the next.
    try { localStorage.clear() } catch { /* jsdom may not have localStorage */ }
  })

  it('renders form with default values', () => {
    renderPanel()
    // IS and OOS bar inputs exist (pre-filled with interval defaults for 1d: 252 / 63)
    const inputs = screen.getAllByRole('spinbutton')
    expect(inputs.length).toBeGreaterThanOrEqual(2)
    // IS bars pre-filled to 252 for 1d interval
    expect((inputs[0] as HTMLInputElement).value).toBe('252')
    // OOS bars pre-filled to 63 for 1d interval
    expect((inputs[1] as HTMLInputElement).value).toBe('63')
    // Run button is present
    expect(screen.getByRole('button', { name: /run walk-forward/i })).toBeInTheDocument()
    // No result shown initially
    expect(screen.queryByText('WFE')).not.toBeInTheDocument()
  })

  it('validates empty is_bars', () => {
    renderPanel()
    // IS bars are pre-filled; clear it first so validation triggers
    const allInputs = screen.getAllByRole('spinbutton')
    fireEvent.change(allInputs[0], { target: { value: '' } })
    const runBtn = screen.getByRole('button', { name: /run walk-forward/i })
    fireEvent.click(runBtn)
    expect(screen.getByText(/IS bars must be a positive integer/i)).toBeInTheDocument()
    expect(mockApiPost).not.toHaveBeenCalled()
  })

  it('run button disabled when estimatedCombos > 1000', () => {
    renderPanel()
    // The first active param row has a Steps input (value='5' by default).
    // _MAX_COMBOS_PER_WINDOW = 1000, so we need steps > 1000 to trigger the guard.
    const allInputs = screen.getAllByRole('spinbutton')
    const stepsInput = allInputs.find(el => (el as HTMLInputElement).value === '5')
    if (stepsInput) {
      fireEvent.change(stepsInput, { target: { value: '1001' } })
    }
    // With 1001 steps, estimatedCombos = 1001 > 1000 → button disabled/faded
    const runBtn = screen.getByRole('button', { name: /run walk-forward/i })
    const isDisabledOrFaded =
      (runBtn as HTMLButtonElement).disabled ||
      (runBtn as HTMLElement).style.opacity === '0.6'
    expect(isDisabledOrFaded).toBe(true)
  })

  it('renders result table after successful response', async () => {
    // WalkForwardPanel calls native fetch() with SSE stream (not api.post).
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(makeTwoWindowResponse().data)

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    // The result should show the 2-window table rows
    // Check for WFE label which appears in summary bar
    const wfeLabel = await screen.findByText('WFE')
    expect(wfeLabel).toBeInTheDocument()

    // 2 windows should produce 2 table data rows — check stability badge text (Fix 5 labels)
    expect(screen.getByText('Plateau')).toBeInTheDocument()
    expect(screen.getByText('Spike')).toBeInTheDocument()
  })

  it('renders low_windows_warn callout when low_windows_warn=true', async () => {
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(makeTwoWindowResponse().data)  // low_windows_warn: true

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    // The low-windows callout says "statistically thin"
    const callout = await screen.findByText(/statistically thin/i)
    expect(callout).toBeInTheDocument()
  })

  it('renders timed_out callout when timed_out=true', async () => {
    const timedOutData = {
      ...makeTwoWindowResponse().data,
      timed_out: true,
      low_windows_warn: false,
    }
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(timedOutData)

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    // The timeout callout mentions "timed out" — may match multiple elements (callout + inline warning)
    const callouts = await screen.findAllByText(/timed out/i)
    expect(callouts.length).toBeGreaterThanOrEqual(1)
  })

  it('renders a distinct badge for each stability_tag value', async () => {
    // 5 windows — one per stability tag
    const allTagsResponse = {
      data: {
        windows: [
          {
            window_index: 0,
            is_start: '2020-01-01', is_end: '2020-06-30',
            oos_start: '2020-07-01', oos_end: '2020-09-30',
            best_params: { stop_loss_pct: 3.0 },
            is_sharpe: 1.0,
            is_metrics: { sharpe_ratio: 1.0, total_return_pct: 5.0, num_trades: 20, win_rate_pct: 55.0, max_drawdown_pct: -4.0, final_value: 10500 },
            oos_metrics: { sharpe_ratio: 0.8, total_return_pct: 3.0, num_trades: 10, win_rate_pct: 50.0, max_drawdown_pct: -3.0, final_value: 10300 },
            stability_tag: 'stable_plateau' as const,
            is_combo_count: 3,
            scale_factor: 1.0,
          },
          {
            window_index: 1,
            is_start: '2020-10-01', is_end: '2021-03-31',
            oos_start: '2021-04-01', oos_end: '2021-06-30',
            best_params: { stop_loss_pct: 5.0 },
            is_sharpe: 2.0,
            is_metrics: { sharpe_ratio: 2.0, total_return_pct: 8.0, num_trades: 25, win_rate_pct: 60.0, max_drawdown_pct: -3.0, final_value: 10800 },
            oos_metrics: { sharpe_ratio: 0.3, total_return_pct: 1.0, num_trades: 5, win_rate_pct: 40.0, max_drawdown_pct: -6.0, final_value: 10100 },
            stability_tag: 'spike' as const,
            is_combo_count: 3,
            scale_factor: 1.03,
          },
          {
            window_index: 2,
            is_start: '2021-07-01', is_end: '2021-12-31',
            oos_start: '2022-01-01', oos_end: '2022-03-31',
            best_params: { stop_loss_pct: 4.0 },
            is_sharpe: 0.5,
            is_metrics: { sharpe_ratio: 0.5, total_return_pct: 2.0, num_trades: 3, win_rate_pct: 35.0, max_drawdown_pct: -7.0, final_value: 10200 },
            oos_metrics: { sharpe_ratio: 0.4, total_return_pct: 1.5, num_trades: 4, win_rate_pct: 45.0, max_drawdown_pct: -5.0, final_value: 10150 },
            stability_tag: 'low_trades_is' as const,
            is_combo_count: 3,
            scale_factor: 1.01,
          },
          {
            window_index: 3,
            is_start: '2022-04-01', is_end: '2022-09-30',
            oos_start: '2022-10-01', oos_end: '2022-12-31',
            best_params: { stop_loss_pct: 3.0 },
            is_sharpe: 1.2,
            is_metrics: { sharpe_ratio: 1.2, total_return_pct: 6.0, num_trades: 18, win_rate_pct: 55.0, max_drawdown_pct: -4.0, final_value: 10600 },
            oos_metrics: { sharpe_ratio: 0.0, total_return_pct: 0.0, num_trades: 0, win_rate_pct: 0.0, max_drawdown_pct: 0.0, final_value: 10000 },
            stability_tag: 'no_oos_trades' as const,
            is_combo_count: 3,
            scale_factor: 1.02,
          },
          {
            window_index: 4,
            is_start: '2023-01-01', is_end: '2023-06-30',
            oos_start: '2023-07-01', oos_end: '2023-09-30',
            best_params: {},
            is_sharpe: 0.0,
            is_metrics: {},
            oos_metrics: { num_trades: 0 },
            stability_tag: 'no_is_trades' as const,
            is_combo_count: 0,
            scale_factor: 1.0,
          },
        ],
        stitched_equity: [],
        wfe: 0.6,
        param_cv: { stop_loss_pct: 0.2 },
        total_combos: 15,
        total_oos_trades: 29,
        low_trades_is_count: 1,
        low_windows_warn: false,
        timed_out: false,
      },
    }
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(allTagsResponse.data)

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    // Wait for results to render
    await screen.findByText('WFE')

    // Each of the 5 stability_tag values should render a distinct badge (Fix 5 short labels)
    expect(screen.getByText('Plateau')).toBeInTheDocument()
    expect(screen.getByText('Spike')).toBeInTheDocument()
    expect(screen.getByText('Thin IS')).toBeInTheDocument()
    expect(screen.getByText('No OOS')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('WFE badge is green for wfe >= 0.7, amber for >= 0.5, red for < 0.5', async () => {
    // jsdom normalises hex colours to rgb() in both style attributes and computed style.
    // Map expected hex → rgb() equivalent for assertion.
    function hexToRgb(hex: string): string {
      const r = parseInt(hex.slice(1, 3), 16)
      const g = parseInt(hex.slice(3, 5), 16)
      const b = parseInt(hex.slice(5, 7), 16)
      return `rgb(${r}, ${g}, ${b})`
    }

    for (const [wfe, expectedHex] of [
      [0.8, '#26a69a'],   // green
      [0.55, '#f0883e'],  // amber
      [0.3, '#ef5350'],   // red
    ] as [number, string][]) {
      const respData = {
        ...makeTwoWindowResponse().data,
        wfe,
        low_windows_warn: false,
        timed_out: false,
      }
      originalFetch = globalThis.fetch
      globalThis.fetch = makeSseFetchMock(respData)

      renderPanel()

      fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

      const wfeLabel = await screen.findByText('WFE')
      // The badge span is the sibling span next to the WFE label
      const wfeBadge = wfeLabel.nextElementSibling as HTMLElement | null
      expect(wfeBadge).not.toBeNull()
      // jsdom normalises hex to rgb() in style attributes — compare using rgb form
      const styleAttr = wfeBadge!.getAttribute('style') ?? ''
      expect(styleAttr).toContain(hexToRgb(expectedHex))

      cleanup()
      vi.clearAllMocks()
    }
  })

  // ---------------------------------------------------------------------------
  // New tests for Fix 1 (interval-aware defaults) and Fix 2 (pre-flight estimate)
  // ---------------------------------------------------------------------------

  it('pre-fills IS=1560 OOS=390 for 5m interval', () => {
    renderPanel({ interval: '5m' })
    const inputs = screen.getAllByRole('spinbutton')
    expect((inputs[0] as HTMLInputElement).value).toBe('1560')
    expect((inputs[1] as HTMLInputElement).value).toBe('390')
  })

  it('pre-fills IS=252 OOS=63 for 1d interval', () => {
    renderPanel({ interval: '1d' })
    const inputs = screen.getAllByRole('spinbutton')
    expect((inputs[0] as HTMLInputElement).value).toBe('252')
    expect((inputs[1] as HTMLInputElement).value).toBe('63')
  })

  it('pre-flight estimate shows windows and backtests', () => {
    // 5yr date range with 1d interval → enough bars for windows to appear
    renderPanel({ interval: '1d', start: '2019-01-01', end: '2024-01-01' })
    // Estimate line should include "windows" and "backtests"
    expect(screen.getByText(/windows/i)).toBeInTheDocument()
    expect(screen.getByText(/backtests/i)).toBeInTheDocument()
  })

  it('persists config to localStorage and restores it on re-mount', () => {
    // First mount: edit IS bars away from default
    const { unmount } = renderPanel({ ticker: 'AAPL', interval: '1d' })
    const allInputs = screen.getAllByRole('spinbutton')
    fireEvent.change(allInputs[0], { target: { value: '500' } })  // IS bars
    fireEvent.change(allInputs[1], { target: { value: '125' } })  // OOS bars

    // Re-mount with the SAME strategy identity → should restore the saved values
    unmount()
    cleanup()
    renderPanel({ ticker: 'AAPL', interval: '1d' })
    const reInputs = screen.getAllByRole('spinbutton')
    expect((reInputs[0] as HTMLInputElement).value).toBe('500')
    expect((reInputs[1] as HTMLInputElement).value).toBe('125')

    // Switching to a different strategy identity → fresh defaults, not the saved 500/125
    unmount()
    cleanup()
    renderPanel({ ticker: 'TSLA', interval: '1d' })
    const tslaInputs = screen.getAllByRole('spinbutton')
    expect((tslaInputs[0] as HTMLInputElement).value).toBe('252')  // default for 1d
    expect((tslaInputs[1] as HTMLInputElement).value).toBe('63')
  })

  it('pre-flight estimate respects provider intraday cap (yahoo clamps, alpaca does not)', () => {
    // Yahoo caps 5m at 60 days → ~4 windows. Alpaca has years of history → many more.
    const { unmount } = render(
      <WalkForwardPanel
        lastRequest={{
          ticker: 'AAPL', interval: '5m', start: '2021-01-01', end: '2024-01-01',
          buy_rules: [], sell_rules: [],
          source: 'yahoo',
        } as unknown as StrategyRequest}
      />
    )
    const yahooText = screen.getByText(/Estimated:/).textContent ?? ''
    const yahooWindows = parseInt(yahooText.match(/~(\d+) windows/)?.[1] ?? '999')
    unmount()
    cleanup()

    render(
      <WalkForwardPanel
        lastRequest={{
          ticker: 'AAPL', interval: '5m', start: '2021-01-01', end: '2024-01-01',
          buy_rules: [], sell_rules: [],
          source: 'alpaca',
        } as unknown as StrategyRequest}
      />
    )
    const alpacaText = screen.getByText(/Estimated:/).textContent ?? ''
    const alpacaWindows = parseInt(alpacaText.match(/~(\d+) windows/)?.[1] ?? '0')

    // Yahoo clamp: 60-day cap on 5m → small window count.
    // Alpaca: 3 years of 5m → many more windows.
    expect(yahooWindows).toBeLessThan(10)
    expect(alpacaWindows).toBeGreaterThan(50)
  })

  it('pre-flight estimate flags step_bars < oos_bars with red status', () => {
    // step_bars < oos_bars is rejected by the backend with 422; surface it before Run.
    renderPanel({ interval: '1d', start: '2019-01-01', end: '2024-01-01' })
    const stepInput = screen.getByPlaceholderText(/= OOS/i)
    fireEvent.change(stepInput, { target: { value: '10' } })  // 10 < default oos_bars=63
    expect(screen.getByText(/must be ≥ oos_bars/i)).toBeInTheDocument()
  })

  it('shortParamLabel disambiguates two rules with the same indicator+param', async () => {
    // buy_rule_0_params_period and buy_rule_1_params_period must produce DIFFERENT
    // short labels (buy0.period vs buy1.period), not the same label.
    const base = makeTwoWindowResponse().data
    const dupResponse = {
      data: {
        ...base,
        windows: [
          {
            ...base.windows[0],
            best_params: {
              buy_rule_0_params_period: 14,
              buy_rule_1_params_period: 28,
            },
          },
        ],
      },
    }
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(dupResponse.data)

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))
    await screen.findByText('WFE')
    expect(screen.getByText(/buy0\.period=14/)).toBeInTheDocument()
    expect(screen.getByText(/buy1\.period=28/)).toBeInTheDocument()
  })

  it('interpretation callout shows correct text for low IS trades', async () => {
    // All windows below IS trade minimum (low_trades_is_count >= windows.length * 0.5)
    const lowTradesResponse = {
      data: {
        ...makeTwoWindowResponse().data,
        low_trades_is_count: 2,  // 2 of 2 windows = 100%
        low_windows_warn: false,
        timed_out: false,
        wfe: -2.12,
      },
    }
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(lowTradesResponse.data)

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    await screen.findByText('WFE')
    expect(screen.getByText(/IS windows too small/i)).toBeInTheDocument()
  })

  it('interpretation callout warns when wfe >= 0.7 but every window is a Spike', async () => {
    // The "all-Spike + healthy WFE" paradox: optimizer picked isolated peaks each
    // window, but one outlier OOS pulled WFE up. Should NOT show "Healthy" green.
    const base = makeTwoWindowResponse().data
    const allSpikeResponse = {
      data: {
        ...base,
        wfe: 1.776,
        low_trades_is_count: 0,
        low_windows_warn: false,
        timed_out: false,
        windows: base.windows.map((w) => ({ ...w, stability_tag: 'spike' })),
      },
    }
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(allSpikeResponse.data)

    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    await screen.findByText('WFE')
    expect(screen.getByText(/every window is a "Spike"/i)).toBeInTheDocument()
    // Must NOT show the healthy callout text — the spike branch should win
    expect(screen.queryByText(/^Healthy walk-forward\.$/)).not.toBeInTheDocument()
  })

  it('interpretation callout shows healthy text for wfe >= 0.7', async () => {
    const healthyResponse = {
      data: {
        ...makeTwoWindowResponse().data,
        wfe: 0.8,
        low_trades_is_count: 0,
        low_windows_warn: false,
        timed_out: false,
      },
    }
    originalFetch = globalThis.fetch
    globalThis.fetch = makeSseFetchMock(healthyResponse.data)

    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /run walk-forward/i }))

    await screen.findByText('WFE')
    expect(screen.getByText(/Healthy walk-forward/i)).toBeInTheDocument()
  })
})
