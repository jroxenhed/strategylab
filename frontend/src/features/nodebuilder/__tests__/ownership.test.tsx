/**
 * "Set by graph" greyed fields (F435 W4 item 4.D; plan D11; spec S29).
 *
 * - With graphViewActive, the four W4 graph-owned groups in the settings
 *   panel are disabled, titled "Set by graph" and carry a GRAPH pill; the
 *   sidebar-owned fields (capital) are untouched; the three settings with
 *   no node yet carry "APPLIES TO GRAPH".
 * - Without graph view the panel is exactly as before (no note, no pills,
 *   nothing disabled).
 * - Changing a greyed value does not change the request buildGraphRequest
 *   builds (while changing the capital does, so the check can fail).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { emptyGraph } from '../../../api/nodebuilder'

vi.mock('../../../shared/hooks/useSlippage', () => ({ useSlippage: () => ({ data: undefined }) }))
vi.mock('../../strategy/savedStrategies', async importOriginal => {
  const orig = await importOriginal<typeof import('../../strategy/savedStrategies')>()
  return { ...orig, loadSavedStrategies: vi.fn().mockResolvedValue([]), saveSavedStrategies: vi.fn() }
})
vi.mock('../../../api/client', () => ({
  api: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn().mockResolvedValue({ data: {} }) },
}))

import StrategyBuilder from '../../strategy/StrategyBuilder'
import { buildGraphRequest, readGraphRunSettings } from '../graphRun'
import {
  APPLIES_TITLE,
  APPLIES_UNTIL_W5_TITLE,
  GRAPH_APPLIES_FIELDS,
  GRAPH_NOTE_ID,
  GRAPH_NOTE_TEXT,
  GRAPH_OWNED_FIELDS,
  isGraphOwned,
  SET_BY_GRAPH,
} from '../ownership'

function renderPanel(graphViewActive: boolean) {
  return render(
    <StrategyBuilder
      ticker="AAPL"
      start="2025-01-02"
      end="2025-12-31"
      interval="1d"
      dataSource="yahoo"
      onResult={() => {}}
      graphViewActive={graphViewActive}
    />,
  )
}

/** The request a graph run would send, from what the settings panel saved. */
function graphRequestNow() {
  const s = readGraphRunSettings()
  return buildGraphRequest(
    emptyGraph(),
    { ticker: 'AAPL', start: '2025-01-02', end: '2025-12-31', interval: '1d', source: 'yahoo', initial_capital: s.initial_capital },
    s.extras,
  )
}

function inputIn(field: string): HTMLInputElement {
  return screen.getByTestId(`sb-owned-${field}`).querySelector('input') as HTMLInputElement
}

beforeEach(() => localStorage.clear())
afterEach(() => cleanup())

describe('the owned-field list (D11)', () => {
  it('is the W4 list, in one place', () => {
    expect([...GRAPH_OWNED_FIELDS]).toEqual(['position_size', 'stop_loss_pct', 'slippage_bps', 'commission_pct'])
    expect([...GRAPH_APPLIES_FIELDS]).toEqual(['dynamic_sizing', 'skip_after_stop', 'trading_hours'])
    expect(isGraphOwned('stop_loss_pct')).toBe(true)
    expect(isGraphOwned('initial_capital')).toBe(false)
  })
})

describe('StrategyBuilder in graph view (S29)', () => {
  it('greys the four graph-owned groups with "Set by graph" and a GRAPH pill', () => {
    renderPanel(true)
    const note = screen.getByTestId('sb-graph-note')
    expect(note.id).toBe(GRAPH_NOTE_ID)
    expect(note.textContent).toBe(GRAPH_NOTE_TEXT)

    const stop = screen.getByTestId('sb-owned-stop_loss_pct')
    expect(stop.getAttribute('title')).toBe(SET_BY_GRAPH)
    expect(stop.style.opacity).toBe('0.5')
    expect(within(stop).getByText('Stop Loss (%)')).toBeInTheDocument()
    expect(within(stop).getByText('GRAPH')).toHaveAttribute('title', SET_BY_GRAPH)
    const stopInput = inputIn('stop_loss_pct')
    expect(stopInput).toBeDisabled()
    expect(stopInput.hasAttribute('disabled')).toBe(true)
    expect(stopInput.getAttribute('aria-describedby')).toBe(GRAPH_NOTE_ID)

    for (const f of GRAPH_OWNED_FIELDS) {
      const group = screen.getByTestId(`sb-owned-${f}`)
      expect(group.getAttribute('title'), f).toBe(SET_BY_GRAPH)
      expect(within(group).getByText('GRAPH'), f).toBeInTheDocument()
      for (const el of group.querySelectorAll('input, select')) expect(el, f).toBeDisabled()
    }
    // Commission: the preset and its two rates are all greyed.
    expect(screen.getByTestId('sb-owned-commission_pct').querySelectorAll('input, select')).toHaveLength(3)
  })

  it('leaves the sidebar-owned capital alone and marks the settings that still apply', () => {
    renderPanel(true)
    const capital = screen.getByText('Capital ($)').parentElement!.querySelector('input')!
    expect(capital).not.toBeDisabled()
    expect(screen.getAllByText('GRAPH')).toHaveLength(GRAPH_OWNED_FIELDS.length)
    const applies = screen.getAllByText('APPLIES TO GRAPH')
    // 3 settings with no node yet + direction, time stop and trailing stop
    // (graph-owned only from W5; borrow rate shows only for shorts).
    expect(applies.filter(p => p.getAttribute('title') === APPLIES_TITLE)).toHaveLength(3)
    expect(applies.filter(p => p.getAttribute('title') === APPLIES_UNTIL_W5_TITLE)).toHaveLength(3)
    expect(applies).toHaveLength(6)
  })

  it('marks time stop, trailing stop and (for shorts) borrow rate APPLIES TO GRAPH (UX-04)', () => {
    localStorage.setItem('strategylab-strategy', JSON.stringify({ direction: 'short' }))
    renderPanel(true)
    for (const label of ['Time Stop (bars)', 'Trailing Stop', 'Borrow rate (%/yr)']) {
      const row = screen.getByText(label).closest('div')!
      expect(within(row).getByTestId('sb-applies-w5'), label).toHaveAttribute('title', APPLIES_UNTIL_W5_TITLE)
      for (const el of row.querySelectorAll('input')) expect(el, label).not.toBeDisabled()
    }
  })

  it('shows the direction in graph view, marked APPLIES TO GRAPH, and a click changes the run (UX-05)', () => {
    renderPanel(true)
    const row = screen.getByTestId('sb-graph-direction')
    expect(within(row).getByTestId('sb-applies-w5')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Long' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(row).getByRole('button', { name: 'Short' }))
    expect(within(row).getByRole('button', { name: 'Short' })).toHaveAttribute('aria-pressed', 'true')
    expect(graphRequestNow().direction).toBe('short')
  })

  it('greys the per-direction stop and size too when regime is on (CI-03)', () => {
    localStorage.setItem('strategylab-strategy', JSON.stringify({ regime: { enabled: true } }))
    renderPanel(true)
    for (const id of ['stop_loss_pct-long', 'stop_loss_pct-short', 'position_size-long', 'position_size-short']) {
      const group = screen.getByTestId(`sb-owned-${id}`)
      expect(group.getAttribute('title'), id).toBe(SET_BY_GRAPH)
      expect(group.querySelector('input'), id).toBeDisabled()
    }
  })

  it('without graph view the panel is as before: no note, no pills, nothing disabled', () => {
    renderPanel(false)
    expect(screen.queryByTestId('sb-graph-note')).toBeNull()
    expect(screen.queryByText('GRAPH')).toBeNull()
    expect(screen.queryByText('APPLIES TO GRAPH')).toBeNull()
    expect(screen.queryByTestId('sb-graph-direction')).toBeNull()
    const stop = screen.getByTestId('sb-owned-stop_loss_pct')
    expect(stop.getAttribute('title')).toBeNull()
    expect(inputIn('stop_loss_pct')).not.toBeDisabled()
  })

  it('greying never clears a value', () => {
    localStorage.setItem('strategylab-strategy', JSON.stringify({ stopLoss: 4, posSize: 40, slippageBps: 9 }))
    renderPanel(true)
    expect(inputIn('stop_loss_pct').value).toBe('4')
    expect(inputIn('position_size').value).toBe('40')
    expect(inputIn('slippage_bps').value).toBe('9')
  })
})

describe('a greyed field cannot change a graph run (D11)', () => {
  it('changing stop, size, slippage or commission leaves buildGraphRequest unchanged', () => {
    // Out of graph view the fields are editable, so the user can change them.
    renderPanel(false)
    const before = graphRequestNow()
    fireEvent.change(inputIn('stop_loss_pct'), { target: { value: '7' } })
    fireEvent.change(inputIn('position_size'), { target: { value: '25' } })
    fireEvent.change(inputIn('slippage_bps'), { target: { value: '15' } })
    fireEvent.change(screen.getByTestId('sb-owned-commission_pct').querySelector('select')!, { target: { value: 'ibkr' } })
    // The panel saved the new values...
    const saved = JSON.parse(localStorage.getItem('strategylab-strategy')!)
    expect(saved).toMatchObject({ stopLoss: 7, posSize: 25, slippageBps: 15, perShareRate: 0.0035, minPerOrder: 0.35 })
    // ...and the graph request is the same.
    expect(graphRequestNow()).toEqual(before)

    // The capital is the sidebar's: changing it does change the request.
    const capital = screen.getByText('Capital ($)').parentElement!.querySelector('input')!
    fireEvent.change(capital, { target: { value: '25000' } })
    expect(graphRequestNow().initial_capital).toBe(25000)
  })
})

describe('settings the graph owns only from W5 still reach graph runs (UX-04, CI-02)', () => {
  it('sends trailing stop, time stop and borrow rate from the settings panel', () => {
    const trailing = { type: 'pct', value: 5, source: 'high', activate_on_profit: false, activate_pct: 0 }
    localStorage.setItem('strategylab-strategy', JSON.stringify({
      direction: 'short', trailingEnabled: true, trailingConfig: trailing, maxBarsHeld: 30, borrowRateAnnual: 1.25,
      stopLoss: 4, posSize: 40, slippageBps: 9, perShareRate: 0.0035, minPerOrder: 0.35,
    }))
    const req = graphRequestNow() as unknown as Record<string, unknown>
    expect(req).toMatchObject({ direction: 'short', trailing_stop: trailing, max_bars_held: 30, borrow_rate_annual: 1.25 })
    // The W4 graph-owned fields still never go out.
    for (const f of GRAPH_OWNED_FIELDS) expect(f in req, f).toBe(false)
    expect('per_share_rate' in req).toBe(false)
    expect('min_per_order' in req).toBe(false)
  })

  it('leaves them out when off: trailing disabled, no time stop', () => {
    localStorage.setItem('strategylab-strategy', JSON.stringify({
      trailingEnabled: false, trailingConfig: { type: 'pct', value: 5 }, maxBarsHeld: '',
    }))
    const req = graphRequestNow() as unknown as Record<string, unknown>
    expect('trailing_stop' in req).toBe(false)
    expect('max_bars_held' in req).toBe(false)
  })

  it('the panel UI changes reach the request', () => {
    renderPanel(true)
    const timeStop = screen.getByText('Time Stop (bars)').parentElement!.querySelector('input')!
    fireEvent.change(timeStop, { target: { value: '12' } })
    fireEvent.click(screen.getByText('Trailing Stop').querySelector('input')!)
    const req = graphRequestNow()
    expect(req.max_bars_held).toBe(12)
    expect(req.trailing_stop).toMatchObject({ type: 'pct' })
  })
})

describe('Cmd+Enter in graph view (B32 x F435 W4)', () => {
  // The node builder's cook.run owns Cmd+Enter in graph view. StrategyBuilder's
  // window listener runs after it, so it must not also start a rule backtest.
  async function postsAfterCmdEnter(graphViewActive: boolean) {
    const { api } = await import('../../../api/client')
    const post = vi.mocked(api.post)
    post.mockClear()
    renderPanel(graphViewActive)
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true })
    await new Promise(r => setTimeout(r, 0))
    return post.mock.calls.filter(c => c[0] === '/api/backtest').length
  }

  it('runs a rule backtest in chart view', async () => {
    expect(await postsAfterCmdEnter(false)).toBe(1)
  })

  it('runs no rule backtest in graph view', async () => {
    expect(await postsAfterCmdEnter(true)).toBe(0)
  })
})
