/**
 * The graph owns its symbol and interval (F453 item 1).
 *
 * The primary Ticker of a graph with no Output Group decides the symbol and
 * interval of every editor run, as spawn does, so the backtest tests what
 * the bot would trade. Its rows are editable on the card and in the
 * Inspector (no more read-only "from sidebar" rows). A Ticker with an empty
 * symbol still falls back to the sidebar. New Tickers start on the
 * sidebar's values, and the Inspector offers "Copy from sidebar (MSFT · 5m)".
 */

import { describe, it, expect, afterEach, beforeAll, beforeEach } from 'vitest'
import { render, cleanup, screen, fireEvent, act } from '@testing-library/react'
import { ReactFlow, type Node, type NodeTypes } from '@xyflow/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import TickerNode from '../nodes/TickerNode'
import { ParamRow } from '../nodes/ParamRow'
import { MultiView } from '../inspector/MultiView'
import { UseSidebarAction } from '../inspector/NodeSections'
import { SidebarWindowContext, USES_SIDEBAR } from '../sidebarWindow'
import { useNodeBuilderStore } from '../store'
import { buildGraphRequest, type GraphSidebar, type GraphWindow } from '../graphRun'
import { listGraphGroups } from '../graphGroups'
import Canvas from '../Canvas'
import { getActiveCanvas, runCommand, setActiveCanvas } from '../commands'
import {
  differsFromSidebar,
  graphOwnTicker,
  graphOwnTickerKey,
  graphRunWindow,
  implicitPrimaryTicker,
  newNodeParams,
} from '../graphTicker'

beforeAll(() => {
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

const WINDOW: GraphWindow = { ticker: 'MSFT', start: '2024-01-02', end: '2024-06-28', interval: '5m', source: 'yahoo' }
const SIDEBAR: GraphSidebar = { ...WINDOW, initial_capital: 10000 }

function gnode(id: string, type: string, params: Record<string, unknown> = {}, parent: string | null = null): GraphNode {
  return { id, type, name: id, parent, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

function graphOf(...nodes: GraphNode[]): Graph {
  return { ...emptyGraph(), nodes: Object.fromEntries(nodes.map(n => [n.id, n])) }
}

function graph(): Graph {
  return graphOf(
    gnode('t1', 'ticker', { symbol: 'AAPL', interval: '1d' }),
    gnode('t2', 'ticker', { symbol: 'SPY', interval: '1h' }),
    gnode('r1', 'rsi', { period: 14 }),
  )
}

const nodeTypes: NodeTypes = { ticker: TickerNode }

function card(id: string, params: Record<string, unknown>, editable = true): Node {
  return {
    id,
    type: 'ticker',
    position: { x: 0, y: 0 },
    data: { backendType: 'ticker', catalog: null, params, display: false, bypass: false, nodePath: `/${id}`, editable },
  }
}

function renderCard(n: Node, win: GraphWindow | null) {
  return render(
    <SidebarWindowContext.Provider value={win}>
      <div style={{ width: 800, height: 600 }}>
        <ReactFlow nodes={[n]} edges={[]} nodeTypes={nodeTypes} />
      </div>
    </SidebarWindowContext.Provider>,
  )
}

beforeEach(() => {
  useNodeBuilderStore.getState().openGraph(graph(), { id: 'g_1', rev: 1, name: 'alpha' })
})
afterEach(() => cleanup())

describe('graphOwnTicker: which symbol and interval the graph sets', () => {
  it('the first root Ticker with no prefix of a graph with no Output Group', () => {
    expect(implicitPrimaryTicker(graph())?.id).toBe('t1')
    expect(graphOwnTicker(graph())).toEqual({ ticker: 'AAPL', interval: '1d' })
    expect(graphOwnTickerKey(graph())).toBe('AAPL|1d')
  })

  it('skips reference Tickers and Tickers inside a network', () => {
    const g = graphOf(
      gnode('n', 'subnet'),
      gnode('inner', 'ticker', { symbol: 'QQQ', interval: '1h' }, 'n'),
      gnode('ref', 'ticker', { symbol: 'SPY', interval: '1d', prefix: 'spy' }),
      gnode('main', 'ticker', { symbol: 'spy', interval: '5m' }),
    )
    expect(graphOwnTicker(g)).toEqual({ ticker: 'SPY', interval: '5m' })
  })

  it('an empty symbol, a graph with Output Groups, or no Ticker leaves it to the sidebar', () => {
    expect(graphOwnTicker(graphOf(gnode('t', 'ticker', { symbol: '  ', interval: '5m' })))).toBeNull()
    expect(graphOwnTicker(graphOf(gnode('t', 'ticker', { symbol: 'SPY' }), gnode('g', 'output_group')))).toBeNull()
    // FL-6: a prefixed-only root Ticker is the primary, as spawn picks it.
    expect(graphOwnTicker(graphOf(gnode('t', 'ticker', { symbol: 'SPY', prefix: 'spy' })))).toEqual({ ticker: 'SPY', interval: '1d' })
    expect(graphOwnTicker(graphOf(gnode('e', 'entry')))).toBeNull()
    expect(graphOwnTicker(null)).toBeNull()
    expect(graphOwnTickerKey(null)).toBe('')
  })

  it('an empty interval reads 1d, as spawn does', () => {
    expect(graphOwnTicker(graphOf(gnode('t', 'ticker', { symbol: 'SPY', interval: '' })))).toEqual({ ticker: 'SPY', interval: '1d' })
  })

  it('graphRunWindow keeps the sidebar dates and source', () => {
    const w = graphRunWindow(graphOf(gnode('t', 'ticker', { symbol: 'SPY', interval: '5m' })), { ...WINDOW, interval: '30m' })
    expect(w).toEqual({ ...WINDOW, ticker: 'SPY', interval: '5m' })
    // Nothing to change: the same object back.
    const same = { ...WINDOW }
    expect(graphRunWindow(graphOf(gnode('t', 'ticker', { symbol: 'MSFT', interval: '5m' })), same)).toBe(same)
  })
})

describe('buildGraphRequest runs on the graph\'s own symbol and interval', () => {
  it('a graph with no Output Group sends its primary Ticker, not the sidebar\'s', () => {
    const g = graphOf(gnode('tick', 'ticker', { symbol: 'SPY', interval: '5m' }), gnode('r', 'rsi'))
    const req = buildGraphRequest(g, { ...SIDEBAR, ticker: 'RIVN', interval: '30m' })
    expect(req.ticker).toBe('SPY')
    expect(req.interval).toBe('5m')
    // The dates, source and capital stay the sidebar's.
    expect(req.start).toBe(SIDEBAR.start)
    expect(req.end).toBe(SIDEBAR.end)
    expect(req.source).toBe(SIDEBAR.source)
    expect(req.initial_capital).toBe(10000)
  })

  it('an empty symbol falls back to the sidebar', () => {
    const g = graphOf(gnode('tick', 'ticker', { symbol: '', interval: '5m' }))
    const req = buildGraphRequest(g, { ...SIDEBAR, ticker: 'RIVN', interval: '30m' })
    expect(req.ticker).toBe('RIVN')
    expect(req.interval).toBe('30m')
  })
})

describe('the Ticker card and Inspector rows are editable', () => {
  it('the implicit primary shows its own values in editable rows', () => {
    renderCard(card('t1', { symbol: 'AAPL', interval: '1d' }), WINDOW)
    expect(screen.queryByText('MSFT')).toBeNull()
    expect(screen.getAllByText('AAPL').length).toBeGreaterThan(0)
    const sym = screen.getByTestId('nb-param-t1-symbol')
    expect(sym.tagName).toBe('INPUT')
    expect(sym.dataset.fromSidebar).toBeUndefined()
    expect(screen.queryByText(/from sidebar/)).toBeNull()
  })

  it('the read-only card subtitle is the Ticker\'s own interval', () => {
    renderCard(card('t1', { symbol: 'AAPL', interval: '1d' }, false), WINDOW)
    expect(screen.getByText('1d')).toBeInTheDocument()
    expect(screen.queryByText(/sidebar/)).toBeNull()
  })

  it('a primary with an empty symbol shows the sidebar values it falls back to', () => {
    useNodeBuilderStore.getState().openGraph(
      graphOf(gnode('t1', 'ticker', { symbol: '', interval: '1d' })), { id: 'g_1', rev: 1, name: 'alpha' },
    )
    renderCard(card('t1', { symbol: '', interval: '1d' }, false), WINDOW)
    expect(screen.getByText('MSFT')).toBeInTheDocument()
    expect(screen.getByText(`5m · ${USES_SIDEBAR}`)).toBeInTheDocument()
  })

  it('the Inspector row is an input', () => {
    render(
      <SidebarWindowContext.Provider value={WINDOW}>
        <ParamRow nodeId="t1" paramKey="symbol" value="AAPL" variant="inspector" />
      </SidebarWindowContext.Provider>,
    )
    expect(screen.getByTestId('nb-param-inspector-t1-symbol').tagName).toBe('INPUT')
  })

  it('two selected Tickers share editable symbol and interval fields', () => {
    render(
      <SidebarWindowContext.Provider value={WINDOW}>
        <MultiView nodeIds={['t1', 't2']} editable />
      </SidebarWindowContext.Provider>,
    )
    expect(screen.getByTestId('nb-inspector-shared-symbol').tagName).toBe('INPUT')
    expect(screen.getByTestId('nb-inspector-shared-interval').tagName).toBe('SELECT')
  })
})

describe('new Tickers and "Copy from sidebar"', () => {
  it('a new Ticker takes the sidebar symbol and interval; other nodes and standalone keep the defaults', () => {
    const defaults = { symbol: 'AAPL', interval: '1d', prefix: '' }
    expect(newNodeParams('ticker', defaults, { ticker: 'rivn', interval: '30m' })).toEqual({ symbol: 'RIVN', interval: '30m', prefix: '' })
    expect(newNodeParams('ticker', defaults, null)).toEqual(defaults)
    expect(newNodeParams('rsi', { period: 14 }, WINDOW)).toEqual({ period: 14 })
    // The defaults object itself is never changed.
    expect(defaults.symbol).toBe('AAPL')
  })

  it('a Ticker made from the Tab menu starts on the sidebar values', () => {
    useNodeBuilderStore.getState().newGraph()
    render(
      <SidebarWindowContext.Provider value={WINDOW}>
        <div className="nodebuilder-root" style={{ width: 1200, height: 900 }}>
          <Canvas graph={useNodeBuilderStore.getState().graph!} />
        </div>
      </SidebarWindowContext.Provider>,
    )
    act(() => { runCommand('edit.addNode', { canvas: getActiveCanvas() }) })
    act(() => { fireEvent.click(screen.getByTestId('nb-tab-row-ticker')) })
    const tick = Object.values(useNodeBuilderStore.getState().graph!.nodes).find(n => n.type === 'ticker')
    expect(tick?.params).toMatchObject({ symbol: 'MSFT', interval: '5m' })
    setActiveCanvas(null)
  })

  it('differsFromSidebar compares symbol (any case) and interval', () => {
    expect(differsFromSidebar({ symbol: 'msft', interval: '5m' }, WINDOW)).toBe(false)
    expect(differsFromSidebar({ symbol: 'MSFT', interval: '1d' }, WINDOW)).toBe(true)
    expect(differsFromSidebar({ symbol: 'AAPL', interval: '5m' }, WINDOW)).toBe(true)
    expect(differsFromSidebar({ symbol: 'AAPL' }, null)).toBe(false)
  })

  function renderAction(nodeId: string, win: GraphWindow | null, editable = true) {
    const node = useNodeBuilderStore.getState().graph!.nodes[nodeId]
    return render(
      <SidebarWindowContext.Provider value={win}>
        <UseSidebarAction nodeId={nodeId} node={node} editable={editable} />
      </SidebarWindowContext.Provider>,
    )
  }

  it('copies the sidebar values into the Ticker as one undo step', () => {
    renderAction('t1', WINDOW)
    const btn = screen.getByTestId('nb-ticker-use-sidebar')
    expect(btn.textContent).toContain('Copy from sidebar (MSFT · 5m)')
    const seq = useNodeBuilderStore.getState().commitSeq
    fireEvent.click(btn)
    const s = useNodeBuilderStore.getState()
    expect(s.graph!.nodes.t1.params).toMatchObject({ symbol: 'MSFT', interval: '5m' })
    expect(s.commitSeq).toBe(seq + 1)
    s.undo()
    expect(useNodeBuilderStore.getState().graph!.nodes.t1.params).toMatchObject({ symbol: 'AAPL', interval: '1d' })
  })

  it('is hidden when the values match, without a sidebar, on a read-only view, or on another node', () => {
    useNodeBuilderStore.getState().updateNodeParams('t1', { symbol: 'MSFT', interval: '5m' })
    renderAction('t1', WINDOW)
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
    cleanup()
    renderAction('t2', null)
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
    cleanup()
    renderAction('t2', WINDOW, false)
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
    cleanup()
    renderAction('r1', WINDOW)
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
  })
})

describe('review fixes (F453 FL-5, FL-6, FL-7, UX-3, UX-8)', () => {
  it('FL-6: with only prefixed root Tickers, Run and Spawn use the same symbol', () => {
    const g = graphOf(
      gnode('q', 'ticker', { symbol: 'qqq', interval: '1h', prefix: 'qqq' }),
      gnode('e', 'entry'),
    )
    const spawn = listGraphGroups(g)[0]
    const req = buildGraphRequest(g, SIDEBAR)
    expect(spawn.symbol).toBe('QQQ')
    expect(req.ticker).toBe(spawn.symbol)
    expect(req.interval).toBe(spawn.interval)
    expect(implicitPrimaryTicker(g)?.id).toBe('q')
  })

  it('FL-6: a plain root Ticker still wins over a prefixed one listed first', () => {
    const g = graphOf(
      gnode('q', 'ticker', { symbol: 'QQQ', interval: '1h', prefix: 'qqq' }),
      gnode('t', 'ticker', { symbol: 'SPY', interval: '5m' }),
      gnode('e', 'entry'),
    )
    expect(implicitPrimaryTicker(g)?.id).toBe('t')
    expect(buildGraphRequest(g, SIDEBAR).ticker).toBe(listGraphGroups(g)[0].symbol)
  })

  it('FL-5 / UX-3: an editable empty-symbol primary says it uses the sidebar and hides its own interval row', () => {
    useNodeBuilderStore.getState().openGraph(
      graphOf(gnode('t1', 'ticker', { symbol: '', interval: '1d', prefix: '' })), { id: 'g_1', rev: 1, name: 'alpha' },
    )
    renderCard(card('t1', { symbol: '', interval: '1d', prefix: '' }, true), WINDOW)
    expect(screen.getByText('MSFT')).toBeInTheDocument()
    expect(screen.getByText(`5m · ${USES_SIDEBAR}`)).toBeInTheDocument()
    // The run uses the sidebar's 5m, so the stored 1d is not shown as if it ran.
    expect(screen.getByTestId('nb-param-t1-symbol')).toBeInTheDocument()
    expect(screen.queryByTestId('nb-param-t1-interval')).toBeNull()
  })

  it('FL-5: with a symbol, the editable card shows its own interval row and no subtitle', () => {
    renderCard(card('t1', { symbol: 'AAPL', interval: '1d' }, true), WINDOW)
    expect(screen.getByTestId('nb-param-t1-interval')).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(USES_SIDEBAR))).toBeNull()
  })

  function renderAction(nodeId: string) {
    const node = useNodeBuilderStore.getState().graph!.nodes[nodeId]
    return render(
      <SidebarWindowContext.Provider value={WINDOW}>
        <UseSidebarAction nodeId={nodeId} node={node} editable />
      </SidebarWindowContext.Provider>,
    )
  }

  it('FL-7: "Copy from sidebar" shows only on the primary Ticker, never on a reference Ticker', () => {
    useNodeBuilderStore.getState().openGraph(graphOf(
      gnode('t1', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      gnode('ref', 'ticker', { symbol: 'QQQ', interval: '1d', prefix: 'qqq' }),
      gnode('other', 'ticker', { symbol: 'IWM', interval: '1d' }),
    ), { id: 'g_1', rev: 1, name: 'alpha' })
    renderAction('t1')
    expect(screen.getByTestId('nb-ticker-use-sidebar').textContent).toBe('Copy from sidebar (MSFT · 5m)')
    cleanup()
    renderAction('ref')
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
    cleanup()
    renderAction('other')
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
  })

  it("FL-7: an Output Group's primary Ticker gets it too", () => {
    useNodeBuilderStore.getState().openGraph(graphOf(
      gnode('t1', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      gnode('g', 'output_group', { ticker: '/t1' }),
    ), { id: 'g_1', rev: 1, name: 'alpha' })
    renderAction('t1')
    expect(screen.getByTestId('nb-ticker-use-sidebar')).toBeInTheDocument()
  })

  it('FL-7: an empty-symbol primary gets a note instead of the button (it already follows the sidebar)', () => {
    useNodeBuilderStore.getState().openGraph(
      graphOf(gnode('t1', 'ticker', { symbol: '', interval: '1d' })), { id: 'g_1', rev: 1, name: 'alpha' },
    )
    renderAction('t1')
    expect(screen.queryByTestId('nb-ticker-use-sidebar')).toBeNull()
    expect(screen.getByTestId('nb-ticker-uses-sidebar').textContent).toContain('runs use the sidebar (MSFT · 5m)')
  })
})
