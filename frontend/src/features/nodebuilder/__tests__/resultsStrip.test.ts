/**
 * resultsStrip.ts — results strip text and graph error messages (F435 W0 0.E).
 */

import { describe, it, expect } from 'vitest'
import type { Graph } from '../../../api/nodebuilder'
import {
  buildResultsStrip,
  describeBacktestError,
  errorNodeId,
  EXIT_NOT_CONNECTED,
  graphEvalKey,
} from '../resultsStrip'

const RUN = { ticker: 'MSFT', interval: '1h' }

describe('buildResultsStrip', () => {
  it('names the symbol and interval and formats the headline', () => {
    const m = buildResultsStrip(
      { num_trades: 6, total_return_pct: 25.8, sharpe_ratio: 1.23456, exit_connected: true, open_position: null },
      RUN,
      false,
    )
    expect(m.context).toBe('MSFT · 1h')
    expect(m.trades).toBe('6 trades')
    expect(m.returnText).toBe('+25.80%')
    expect(m.returnSign).toBe('pos')
    expect(m.sharpe).toBe('Sharpe 1.235')
    expect(m.openPosition).toBeNull()
    expect(m.exitWarning).toBeNull()
    expect(m.stale).toBe(false)
  })

  it('uses the singular for one trade and handles a negative return', () => {
    const m = buildResultsStrip({ num_trades: 1, total_return_pct: -3.1 }, RUN, false)
    expect(m.trades).toBe('1 trade')
    expect(m.returnText).toBe('-3.10%')
    expect(m.returnSign).toBe('neg')
  })

  it('shows dashes when numbers are missing', () => {
    const m = buildResultsStrip({}, RUN, false)
    expect(m.trades).toBe('0 trades')
    expect(m.returnText).toBe('—')
    expect(m.returnSign).toBe('none')
    expect(m.sharpe).toBe('Sharpe —')
  })

  it('passes the stale flag through', () => {
    expect(buildResultsStrip({ num_trades: 2 }, RUN, true).stale).toBe(true)
  })

  it('shows one open position with its unrealized percent', () => {
    const m = buildResultsStrip(
      {
        num_trades: 0,
        total_return_pct: 42.3,
        open_position: { direction: 'long', entry_price: 101.5, unrealized_pct: 42.34 },
      },
      RUN,
      false,
    )
    expect(m.openPosition).toBe('1 open position (+42.3%)')
    expect(m.openPositionTitle).toContain('long')
    expect(m.openPositionTitle).toContain('101.50')
  })

  it('shows a losing open short position with a minus sign', () => {
    const m = buildResultsStrip(
      { open_position: { direction: 'short', entry_price: 50, unrealized_pct: -2.06 } },
      RUN,
      false,
    )
    expect(m.openPosition).toBe('1 open position (-2.1%)')
    expect(m.openPositionTitle).toContain('short')
  })

  it('warns "Exit not connected" only on an explicit false', () => {
    expect(buildResultsStrip({ exit_connected: false }, RUN, false).exitWarning).toBe(EXIT_NOT_CONNECTED)
    expect(EXIT_NOT_CONNECTED).toBe('Exit not connected')
    expect(buildResultsStrip({ exit_connected: true }, RUN, false).exitWarning).toBeNull()
    // Older server without the field: stay quiet.
    expect(buildResultsStrip({}, RUN, false).exitWarning).toBeNull()
  })
})

function axiosError(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data },
  })
}

const GRAPH: Graph = {
  _version: 1,
  readOnly: false,
  nodes: {
    '/entry': { id: '/entry', type: 'entry', params: {}, position: [0, 0], display: false, bypass: false },
  },
  wires: [],
}

describe('describeBacktestError', () => {
  it('shows the server detail instead of the axios message', () => {
    const e = axiosError(400, { detail: 'Graph has no Exit terminal', node_id: null })
    expect(describeBacktestError(e, GRAPH)).toBe('Graph has no Exit terminal')
  })

  it('names the node when node_id is set and the node is in the graph', () => {
    const e = axiosError(400, { detail: 'Entry has no input', node_id: '/entry' })
    expect(describeBacktestError(e, GRAPH)).toBe('Entry has no input (node: entry /entry)')
  })

  it('falls back to the raw node_id when the node is not in the graph', () => {
    const e = axiosError(400, { detail: 'Unsupported node', node_id: '/entry/rising_1' })
    expect(describeBacktestError(e, GRAPH)).toBe('Unsupported node (node: /entry/rising_1)')
  })

  it('reads a nested FastAPI HTTPException body', () => {
    const e = axiosError(400, { detail: { detail: 'Too many families', node_id: '/entry' } })
    expect(describeBacktestError(e, GRAPH)).toBe('Too many families (node: entry /entry)')
  })

  it('gives the node id to select on the canvas (UXP-13)', () => {
    expect(errorNodeId(axiosError(400, { detail: 'x', node_id: '/entry' }))).toBe('/entry')
    expect(errorNodeId(axiosError(400, { detail: { detail: 'x', node_id: '/entry' } }))).toBe('/entry')
    expect(errorNodeId(axiosError(400, { detail: 'x', node_id: null }))).toBeNull()
  })

  it('does not repeat the node when the server message already names it (UXP-13)', () => {
    const e = axiosError(400, { detail: "Entry '/entry' has 2 inputs; it takes one.", node_id: '/entry' })
    expect(describeBacktestError(e, GRAPH)).toBe("Entry '/entry' has 2 inputs; it takes one.")
  })

  it('falls back to the error message when there is no response body', () => {
    expect(describeBacktestError(new Error('Network Error'), null)).toBe('Network Error')
  })
})

describe('graphEvalKey (FC-8 / UXP-5)', () => {
  function g(y: number, display = false, threshold = 30): Graph {
    return {
      _version: 1,
      readOnly: false,
      nodes: {
        a: { id: 'a', type: 'rsi', params: { period: 14 }, position: [0, y], display, bypass: false },
        b: { id: 'b', type: 'below', params: { threshold }, position: [0, y + 100], display: false, bypass: false },
      },
      wires: [{ id: 'w', from: 'a', to: 'b', attr: '@rsi' }],
    } as unknown as Graph
  }

  it('ignores a pure layout move and the display flag', () => {
    expect(graphEvalKey(g(0))).toBe(graphEvalKey(g(250, true)))
  })

  it('changes when a param changes', () => {
    expect(graphEvalKey(g(0))).not.toBe(graphEvalKey(g(0, false, 40)))
  })

  it('is empty for no graph', () => {
    expect(graphEvalKey(null)).toBe('')
  })
})
