/**
 * Ticker symbol and interval "from sidebar" (F435 W4 review UX-01; plan D11).
 *
 * Before W5 every graph run, preview and Data Sheet cook fetches the
 * sidebar's symbol and interval. So in the app (NodeBuilder given a
 * `graphWindow`) a Ticker node shows those values read-only, tagged
 * "from sidebar", on the card and in the Inspector, and has no field to
 * edit. Without a window (standalone) the params stay editable.
 */

import { describe, it, expect, afterEach, beforeAll, beforeEach } from 'vitest'
import { render, cleanup, screen, within } from '@testing-library/react'
import { ReactFlow, type Node, type NodeTypes } from '@xyflow/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import TickerNode from '../nodes/TickerNode'
import { ParamRow } from '../nodes/ParamRow'
import { MultiView } from '../inspector/MultiView'
import { FROM_SIDEBAR, SidebarWindowContext, sidebarTickerValue } from '../sidebarWindow'
import { useNodeBuilderStore } from '../store'
import type { GraphWindow } from '../graphRun'

beforeAll(() => {
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor(_t?: string) {}
    }
  }
})

const WINDOW: GraphWindow = { ticker: 'MSFT', start: '2024-01-02', end: '2024-06-28', interval: '5m', source: 'yahoo' }

function gnode(id: string, type: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

function graph(): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      t1: gnode('t1', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      t2: gnode('t2', 'ticker', { symbol: 'SPY', interval: '1h' }),
      r1: gnode('r1', 'rsi', { period: 14 }),
    },
  }
}

const nodeTypes: NodeTypes = { ticker: TickerNode }

function card(editable: boolean): Node {
  return {
    id: 't1',
    type: 'ticker',
    position: { x: 0, y: 0 },
    data: {
      backendType: 'ticker', catalog: null, params: { symbol: 'AAPL', interval: '1d' },
      display: false, bypass: false, nodePath: '/t1', editable,
    },
  }
}

function renderCard(win: GraphWindow | null, editable = true) {
  return render(
    <SidebarWindowContext.Provider value={win}>
      <div style={{ width: 800, height: 600 }}>
        <ReactFlow nodes={[card(editable)]} edges={[]} nodeTypes={nodeTypes} />
      </div>
    </SidebarWindowContext.Provider>,
  )
}

beforeEach(() => {
  useNodeBuilderStore.getState().openGraph(graph(), { id: 'g_1', rev: 1, name: 'alpha' })
})
afterEach(() => cleanup())

describe('Ticker symbol and interval come from the sidebar (UX-01)', () => {
  it('only a Ticker symbol or interval is sidebar-owned, and only with a window', () => {
    expect(sidebarTickerValue(WINDOW, 'ticker', 'symbol')).toBe('MSFT')
    expect(sidebarTickerValue(WINDOW, 'ticker', 'interval')).toBe('5m')
    expect(sidebarTickerValue(WINDOW, 'ticker', 'other')).toBeNull()
    expect(sidebarTickerValue(WINDOW, 'rsi', 'interval')).toBeNull()
    expect(sidebarTickerValue(null, 'ticker', 'symbol')).toBeNull()
  })

  it('the card shows the sidebar symbol and read-only rows tagged "from sidebar"', () => {
    renderCard(WINDOW)
    // Title and symbol row both read the sidebar symbol; the node's own is gone.
    expect(screen.getAllByText('MSFT')).toHaveLength(2)
    expect(screen.queryByText('AAPL')).toBeNull()
    for (const key of ['symbol', 'interval']) {
      const row = screen.getByTestId(`nb-param-t1-${key}`)
      expect(row.dataset.fromSidebar, key).toBe('true')
      expect(within(row).getByText(FROM_SIDEBAR)).toBeInTheDocument()
      expect(row.querySelector('input, select'), key).toBeNull()
    }
    expect(within(screen.getByTestId('nb-param-t1-interval')).getByText('5m')).toBeInTheDocument()
  })

  it('the read-only card subtitle says the interval is from the sidebar', () => {
    renderCard(WINDOW, false)
    expect(screen.getByText(`5m · ${FROM_SIDEBAR}`)).toBeInTheDocument()
  })

  it('the Inspector row is read-only too, and other params stay editable', () => {
    render(
      <SidebarWindowContext.Provider value={WINDOW}>
        <ParamRow nodeId="t1" paramKey="symbol" value="AAPL" variant="inspector" />
        <ParamRow nodeId="r1" paramKey="period" value={14} variant="inspector" />
      </SidebarWindowContext.Provider>,
    )
    const sym = screen.getByTestId('nb-param-inspector-t1-symbol')
    expect(sym.dataset.fromSidebar).toBe('true')
    expect(sym.textContent).toContain('MSFT')
    expect(screen.getByTestId('nb-param-inspector-r1-period').tagName).toBe('INPUT')
  })

  it('two selected Tickers share the sidebar values, not editable fields', () => {
    render(
      <SidebarWindowContext.Provider value={WINDOW}>
        <MultiView nodeIds={['t1', 't2']} editable />
      </SidebarWindowContext.Provider>,
    )
    const sym = screen.getByTestId('nb-inspector-shared-symbol')
    expect(sym.dataset.fromSidebar).toBe('true')
    expect(sym.textContent).toContain('MSFT')
  })

  it('without a window (standalone) the params stay editable', () => {
    renderCard(null)
    expect(screen.getByText('AAPL')).toBeInTheDocument()
    expect(screen.getByTestId('nb-param-t1-symbol').tagName).toBe('INPUT')
  })
})
