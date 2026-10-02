/**
 * F435 Wave 5 frontend integration: the pieces that join the W5 items.
 *
 * - FA2 on connect: a wire from outside a network to a node inside it goes
 *   through a frame port; a wire across two networks is refused with the
 *   `wire_crosses_network` copy (operations/wires.ts, 5.E Needs 5).
 * - Deleting a network takes its children and boundary nodes along
 *   (operations.ts, 5.E Needs 6).
 * - Prefixed Ticker writes (S32c): a prefix `msft` writes `@msft_open` ...
 *   `@msft_volume`; an empty prefix writes the plain names. The Ticker card
 *   shows symbol, interval and prefix, never source (FA4).
 * - The sidebar owns only the implicit group's primary Ticker (D7).
 * - Terminal and network types: `regime_net` is the network, `regime` the
 *   terminal; trailing and time stops hide their `out` write row (5.A).
 */

import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { ReactFlow, type Node, type NodeTypes } from '@xyflow/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import {
  connectInNetworks,
  networkConnectionProblem,
  planConnection,
  WIRE_CROSSES_NETWORK_TEXT,
} from '../operations/wires'
import { removeNodes, removeNodesWithRewire, withDescendants } from '../operations'
import { prefixedName, primaryWriteOf, tickerPrefixOf, writesOf, writtenNames } from '../streamLabels'
import { NETWORK_TYPES, rfTypeFor, TERMINAL_TYPES } from '../rfMapping'
import { terminalRowParams } from '../nodes/networkFormat'
import { SidebarWindowContext } from '../sidebarWindow'
import { sidebarOwnsTicker } from '../ownership'
import { menuCatalog } from '../canvasHelpers'
import { NODE_CATALOG } from '../catalog'
import { useNodeBuilderStore } from '../store'
import TickerNode from '../nodes/TickerNode'
import type { GraphWindow } from '../graphRun'

beforeAll(() => {
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
    }
  }
})

function gnode(id: string, type: string, params: Record<string, unknown> = {}, parent: string | null = null): GraphNode {
  return { id, type, name: id, parent, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

/**
 * Root: Ticker `t`, an Output Group `grp` with an RSI `rsi` and an entry
 * inside, and a second group `grp2` with a comparison `cmp`.
 */
function netGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      grp: gnode('grp', 'output_group', { direction: 'long', ticker: 't' }),
      rsi: gnode('rsi', 'rsi', { period: 14 }, 'grp'),
      ent: gnode('ent', 'entry', {}, 'grp'),
      grp2: gnode('grp2', 'output_group', { direction: 'short' }),
      cmp: gnode('cmp', 'above', { threshold: 70 }, 'grp2'),
    },
    wires: [{ id: 'w1', from: 'rsi', to: 'ent', from_port: 'out', to_port: 'in0' }],
  }
}

afterEach(() => cleanup())

describe('FA2: wires and networks on connect (5.E Needs 5)', () => {
  it('a wire between siblings is a plain wire', () => {
    expect(planConnection(netGraph(), { source: 'rsi', target: 'ent', targetHandle: 'in0' }).kind).toBe('plain')
  })

  it('an outside source feeding a node inside a group goes through a new frame port', () => {
    const g = netGraph()
    const c = { source: 't', target: 'rsi', sourceHandle: 'out', targetHandle: 'in0' }
    expect(planConnection(g, c)).toMatchObject({ kind: 'route', from: 't', networkId: 'grp', to: 'rsi', toPort: 'in0' })
    expect(networkConnectionProblem(g, c)).toBeNull()
    const next = connectInNetworks(g, c)
    const boundary = Object.values(next.nodes).find(n => n.type === 'subnet_input')!
    expect(boundary.parent).toBe('grp')
    expect(boundary.params.port).toBe(0)
    // Outside wire into the frame port, inside wire from the boundary.
    expect(next.wires.some(w => w.from === 't' && w.to === 'grp' && w.to_port === 'in0')).toBe(true)
    expect(next.wires.some(w => w.from === boundary.id && w.to === 'rsi' && w.to_port === 'in0')).toBe(true)
    // No wire crosses the frame edge.
    for (const w of next.wires) {
      expect(next.nodes[w.from].parent ?? null, w.id).toBe(next.nodes[w.to].parent ?? null)
    }
  })

  it('a wire between two groups is refused with the S31 copy', () => {
    const g = netGraph()
    const c = { source: 'rsi', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' }
    expect(planConnection(g, c).kind).toBe('crosses')
    expect(networkConnectionProblem(g, c)).toBe('crosses')
    expect(() => connectInNetworks(g, c)).toThrow(WIRE_CROSSES_NETWORK_TEXT)
    expect(WIRE_CROSSES_NETWORK_TEXT).toBe('Wires connect nodes in the same network. Route through a network port.')
  })

  it('a wire out of a group to the root is refused too (only the way in is routed)', () => {
    const g = netGraph()
    expect(networkConnectionProblem(g, { source: 'rsi', target: 't', sourceHandle: 'out', targetHandle: 'in0' })).toBe('crosses')
  })

  it('a full inside port refuses the routed wire', () => {
    const g = netGraph()
    expect(networkConnectionProblem(g, { source: 't', target: 'ent', sourceHandle: 'out', targetHandle: 'in0' })).toBe('full')
  })

  it('a graph without networks keeps the S08 rules', () => {
    const g: Graph = { ...emptyGraph(), nodes: { t: gnode('t', 'ticker'), r: gnode('r', 'rsi') } }
    expect(networkConnectionProblem(g, { source: 't', target: 'r', sourceHandle: 'out', targetHandle: 'in0' })).toBeNull()
    expect(networkConnectionProblem(g, { source: 't', target: 't' })).toBe('self')
  })
})

describe('deleting a network (5.E Needs 6)', () => {
  it('takes its children and boundary nodes along, so no parent dangles', () => {
    const g = connectInNetworks(netGraph(), { source: 't', target: 'rsi', sourceHandle: 'out', targetHandle: 'in0' })
    const boundaryId = Object.values(g.nodes).find(n => n.type === 'subnet_input')!.id
    expect([...withDescendants(g, ['grp'])].sort()).toEqual(['ent', 'grp', 'rsi', boundaryId].sort())
    for (const out of [removeNodes(g, ['grp']), removeNodesWithRewire(g, ['grp'])]) {
      expect(Object.keys(out.nodes).sort()).toEqual(['cmp', 'grp2', 't'])
      for (const n of Object.values(out.nodes)) {
        if (n.parent) expect(out.nodes[n.parent], n.id).toBeDefined()
      }
      expect(out.wires).toEqual([])
    }
  })

  it('a plain node delete is as before', () => {
    const out = removeNodes(netGraph(), ['rsi'])
    expect(Object.keys(out.nodes)).not.toContain('rsi')
    expect(out.nodes.grp).toBeDefined()
    expect(out.nodes.ent).toBeDefined()
  })
})

describe('prefixed Ticker writes (S32c)', () => {
  it('a prefix writes @<prefix>_open ... _volume; an empty prefix the plain names', () => {
    const ref = gnode('m', 'ticker', { symbol: 'MSFT', interval: '1d', prefix: 'msft' })
    expect(writesOf(ref).map(w => w.name)).toEqual(['@msft_open', '@msft_high', '@msft_low', '@msft_close', '@msft_volume'])
    expect(primaryWriteOf(ref)).toBe('@msft_close')
    expect(tickerPrefixOf(ref)).toBe('msft')
    for (const prefix of ['', '   ', undefined, 3]) {
      const plain = gnode('a', 'ticker', { symbol: 'AAPL', prefix })
      // The plain Ticker also writes @time and @index (backend nodes_data); a prefixed one does not.
      expect(writesOf(plain).map(w => w.name).slice(0, 5)).toEqual(['@open', '@high', '@low', '@close', '@volume'])
      expect(writesOf(plain).length).toBeGreaterThanOrEqual(5)
      expect(primaryWriteOf(plain)).toBe('@close')
    }
  })

  it('only Tickers take a prefix, and the graph-wide written names follow it', () => {
    expect(tickerPrefixOf(gnode('r', 'rsi', { prefix: 'x' }))).toBeNull()
    expect(prefixedName('@close', 'spy')).toBe('@spy_close')
    expect(prefixedName('@close', null)).toBe('@close')
    const g: Graph = {
      ...emptyGraph(),
      nodes: { a: gnode('a', 'ticker'), s: gnode('s', 'ticker', { prefix: 'spy' }) },
    }
    const names = writtenNames(g)
    expect(names.has('@close')).toBe(true)
    expect(names.has('@spy_close')).toBe(true)
  })

  it('a new wire out of a reference Ticker reads its prefixed close', () => {
    const g: Graph = {
      ...emptyGraph(),
      nodes: { s: gnode('s', 'ticker', { prefix: 'spy' }), r: gnode('r', 'rsi', {}) },
    }
    const next = connectInNetworks(g, { source: 's', target: 'r', sourceHandle: 'out', targetHandle: 'in0' })
    const readParam = Object.entries(next.nodes.r.params).find(([, v]) => v === '@spy_close')
    expect(readParam).toBeDefined()
  })
})

describe('the Ticker card (S32c, FA4)', () => {
  const WINDOW: GraphWindow = { ticker: 'MSFT', start: '2024-01-02', end: '2024-06-28', interval: '5m', source: 'yahoo' }
  const nodeTypes: NodeTypes = { ticker: TickerNode }

  function card(id: string, params: Record<string, unknown>): Node {
    return {
      id, type: 'ticker', position: { x: 0, y: 0 },
      data: { backendType: 'ticker', catalog: null, params, display: false, bypass: false, nodePath: `/${id}`, editable: true },
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
    useNodeBuilderStore.getState().openGraph({
      ...emptyGraph(),
      nodes: {
        a: gnode('a', 'ticker', { symbol: 'AAPL', interval: '1d', source: 'yahoo' }),
        s: gnode('s', 'ticker', { symbol: 'SPY', interval: '1d', prefix: 'spy', source: 'yahoo' }),
      },
    }, { id: 'g_1', rev: 1, name: 'alpha' })
  })

  it('shows symbol, interval and prefix rows and never a source row', () => {
    renderCard(card('s', { symbol: 'SPY', interval: '1d', prefix: 'spy', source: 'yahoo' }), null)
    expect(screen.getByTestId('nb-param-s-symbol')).toBeInTheDocument()
    expect(screen.getByTestId('nb-param-s-interval')).toBeInTheDocument()
    expect(screen.getByTestId('nb-param-s-prefix')).toBeInTheDocument()
    expect(screen.queryByTestId('nb-param-s-source')).toBeNull()
  })

  it('a reference Ticker shows its own symbol and prefixed chips, not the sidebar', () => {
    const { container } = renderCard(card('s', { symbol: 'SPY', interval: '1d', prefix: 'spy' }), WINDOW)
    expect(screen.getAllByText('SPY').length).toBeGreaterThan(0)
    expect(screen.queryByText('MSFT')).toBeNull()
    expect(container.textContent).toContain('@spy_close')
    expect(screen.getByTestId('nb-param-s-symbol').dataset.fromSidebar).toBeUndefined()
  })

  it('the implicit primary (no prefix, no group) still shows the sidebar values (UX-01)', () => {
    renderCard(card('a', { symbol: 'AAPL', interval: '1d' }), WINDOW)
    const row = screen.getByTestId('nb-param-a-symbol')
    expect(row.dataset.fromSidebar).toBe('true')
    expect(within(row).getByText('MSFT')).toBeInTheDocument()
  })

  it('the sidebar owns no Ticker of a graph with Output Groups (D7)', () => {
    expect(sidebarOwnsTicker({ symbol: 'AAPL' }, emptyGraph())).toBe(true)
    expect(sidebarOwnsTicker({ symbol: 'SPY', prefix: 'spy' }, emptyGraph())).toBe(false)
    const withGroup = { ...emptyGraph(), nodes: { g: gnode('g', 'output_group') } }
    expect(sidebarOwnsTicker({ symbol: 'AAPL' }, withGroup)).toBe(false)
  })
})

describe('network and terminal types (5.A, 5.C)', () => {
  it('regime_net is the network; regime is a terminal card', () => {
    expect([...NETWORK_TYPES].sort()).toEqual(['output_group', 'regime_net', 'subnet'])
    expect(rfTypeFor('regime_net')).toBe('nbNetworkFrame')
    expect(rfTypeFor('regime')).toBe('nbOutput')
    for (const t of TERMINAL_TYPES) expect(rfTypeFor(t), t).toBe('nbOutput')
  })

  it('trailing and time stops hide their write param out', () => {
    expect(terminalRowParams('trailing_stop', { type: 'pct', value: 5, out: '@trail_value' }, new Set(), null))
      .toEqual({ type: 'pct', value: 5 })
    expect(terminalRowParams('time_stop', { max_bars: 10, out: '@max_bars' }, new Set(), null)).toEqual({ max_bars: 10 })
  })

  it('the Tab menu never offers a boundary node on its own', () => {
    const fake = [
      ...NODE_CATALOG,
      { ...NODE_CATALOG[0], name: 'subnet_input', compileActive: true },
      { ...NODE_CATALOG[0], name: 'subnet_output', compileActive: true },
    ]
    const names = menuCatalog(fake).map(e => e.name)
    expect(names).not.toContain('subnet_input')
    expect(names).not.toContain('subnet_output')
  })
})
