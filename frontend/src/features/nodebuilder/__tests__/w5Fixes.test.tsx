/**
 * F435 Wave 5 review fixes, frontend (fixer C): network edits, paths,
 * frame layout, header popovers, terminal rows and the Ticker prefix row.
 *
 * - FE-01: nodes dropped into (or out of) a frame together move in ONE
 *   step, so a wire between them stays a plain wire; one undo.
 * - FE-02: a routed wire (outside -> inside a network) is refused when it
 *   would close a cycle through the network.
 * - FE-03: renaming or moving a group's primary Ticker (or a network that
 *   holds it) rewrites the group's `ticker` path.
 * - FE-04 / UX-06: one weightOf (0 stays 0) and a default LONG direction.
 * - FE-05 / UX-11: frames wrap measured card sizes.
 * - FE-06 / UX-01: the direction list opens in a portal, outside the frame.
 * - FE-09: the crossing flash reads the drop's own ends.
 * - FE-10: the header resolves a ticker path like the server.
 * - UX-02: size shows a percent, a zero stop shows `none`.
 * - UX-04: the Tab menu's frame under the cursor.
 * - UX-09: no prefix row on a group's primary Ticker.
 * - UX-14: a terminal with no side counts as long; SWITCH writes side long.
 * - Side rows on stop/size/trailing/time stop inside a regime_switch group.
 */

import { describe, it, expect, afterEach, beforeAll } from 'vitest'
import { act, render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import type { Node as RFNode } from '@xyflow/react'
import type { Graph, GraphNode, GraphWire } from '../../../api/nodebuilder'
import Canvas from '../Canvas'
import { useNodeBuilderStore } from '../store'
import {
  computeFrameLayouts,
  createNodeMapper,
  groupTickerOf,
  measuredSizeOf,
  missingTerminals,
  DEFAULT_NODE_SIZE,
  type FrameLayout,
} from '../rfMapping'
import { frameAtPoint, reparentNode, reparentNodes, withGroupDirection } from '../networkOps'
import { plugin as networkFramesPlugin } from '../plugins/networkFrames'
import type { CanvasCtx } from '../canvasPlugins'
import { getActiveCanvas } from '../screen'
import { joinPath, renameNode, rewritePathRefs } from '../paths'
import { dropCrossesNetwork, networkConnectionProblem } from '../operations/wires'
import { isGroupPrimary, listGraphGroups, splitCapital } from '../graphGroups'
import { terminalNote, terminalRowParams, terminalRowViews } from '../nodes/networkFormat'
import { tickerRowParams } from '../nodes/tickerRows'
import { ParamRows } from '../nodes/ParamRow'
import { viewText, viewValue } from '../nodes/paramFormat'

function node(id: string, type: string, parent: string | null, position: [number, number], params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent, params, position, display: false, bypass: false }
}

function graphOf(nodes: GraphNode[], wires: GraphWire[] = []): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires,
    annotations: { boxes: [], notes: [] },
  }
}

function wire(id: string, from: string, to: string, to_port = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port }
}

/** aapl -> a -> b at the root, and an empty-ish group `leg` at (600, 0). */
function chainGraph(): Graph {
  return graphOf([
    node('aapl', 'ticker', null, [0, 0], { symbol: 'AAPL', interval: '1d' }),
    node('a', 'rsi', null, [0, 150], { period: 14 }),
    node('b', 'comparison', null, [0, 300], {}),
    node('leg', 'output_group', null, [600, 0], { direction: 'long', ticker: '/aapl', capital_weight: 1 }),
    node('entry', 'entry', 'leg', [620, 60], { signal: null }),
  ], [wire('w1', 'aapl', 'a'), wire('w2', 'a', 'b')])
}

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
  if (!(globalThis as { ResizeObserver?: unknown }).ResizeObserver) {
    ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

afterEach(() => {
  cleanup()
})

// ---------------------------------------------------------------------------
// FE-01
// ---------------------------------------------------------------------------

describe('FE-01: reparenting a set of nodes in one step', () => {
  for (const order of [['a', 'b'], ['b', 'a']] as const) {
    it(`two wired nodes dropped into a group together keep their wire (order ${order.join(',')})`, () => {
      const g = chainGraph()
      const res = reparentNodes(g, order.map(id => ({ id, parent: 'leg', position: g.nodes[id].position })))
      expect(res.removedWires).toBe(0)
      expect(res.graph.nodes.a.parent).toBe('leg')
      expect(res.graph.nodes.b.parent).toBe('leg')
      // a -> b is still a plain wire between the two (now siblings).
      expect(res.graph.wires).toContainEqual(expect.objectContaining({ id: 'w2', from: 'a', to: 'b' }))
      // aapl stays outside and feeds a through one frame port.
      const bnd = Object.values(res.graph.nodes).find(n => n.type === 'subnet_input' && n.parent === 'leg')!
      expect(res.graph.wires).toContainEqual(expect.objectContaining({ from: 'aapl', to: 'leg' }))
      expect(res.graph.wires).toContainEqual(expect.objectContaining({ from: bnd.id, to: 'a' }))
      expect(res.graph.wires.filter(w => w.to === 'b')).toHaveLength(1)
    })
  }

  it('two wired nodes dragged out together keep their wire', () => {
    const g = chainGraph()
    const inside = reparentNodes(g, [
      { id: 'a', parent: 'leg', position: [620, 200] },
      { id: 'b', parent: 'leg', position: [620, 300] },
    ]).graph
    const out = reparentNodes(inside, [
      { id: 'b', parent: null, position: [0, 300] },
      { id: 'a', parent: null, position: [0, 150] },
    ])
    expect(out.removedWires).toBe(0)
    expect(out.graph.wires).toContainEqual(expect.objectContaining({ from: 'a', to: 'b' }))
    // a reads aapl straight again (the port's outside source).
    expect(out.graph.wires).toContainEqual(expect.objectContaining({ from: 'aapl', to: 'a' }))
  })

  it('one node at a time (the old path) loses the wire; the set does not', () => {
    const g = chainGraph()
    let step = reparentNode(g, 'b', 'leg', g.nodes.b.position).graph
    step = reparentNode(step, 'a', 'leg', g.nodes.a.position).graph
    // Moving b in alone routes a -> b through a port; moving a in after
    // collapses that port again, so even the one-by-one path ends whole.
    expect(step.wires).toContainEqual(expect.objectContaining({ from: 'a', to: 'b' }))
  })

  it('a drop of two wired nodes into a frame is one undo step and keeps the wire', () => {
    const g = chainGraph()
    useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
    const layout = computeFrameLayouts(g.nodes).get('leg')!
    // Both dropped well inside the frame.
    const rf = (id: string, x: number, y: number): RFNode => ({ id, position: { x, y }, data: {} })
    const nodes = [rf('a', layout.x + 20, layout.y + 50), rf('b', layout.x + 20, layout.y + 70)]
    const ctx = {
      rf: { getNode: () => undefined },
      store: useNodeBuilderStore,
      graph: () => useNodeBuilderStore.getState().graph!,
      graphPosition: (n: RFNode) => [n.position.x, n.position.y] as [number, number],
      editable: () => true,
    } as unknown as CanvasCtx
    const s = useNodeBuilderStore.getState()
    s.beginBatch('drop')
    const handled = networkFramesPlugin.onSelectionDragStop!({} as never, nodes, ctx)
    useNodeBuilderStore.getState().endBatch()
    expect(handled).toBe(true)
    const after = useNodeBuilderStore.getState().graph!
    expect(after.nodes.a.parent).toBe('leg')
    expect(after.nodes.b.parent).toBe('leg')
    expect(after.wires).toContainEqual(expect.objectContaining({ from: 'a', to: 'b' }))
    useNodeBuilderStore.getState().undo()
    const back = useNodeBuilderStore.getState().graph!
    expect(back.nodes.a.parent).toBeNull()
    expect(back.nodes.b.parent).toBeNull()
    expect(back.wires.map(w => w.id).sort()).toEqual(['w1', 'w2'])
  })
})

// ---------------------------------------------------------------------------
// FE-02
// ---------------------------------------------------------------------------

describe('FE-02: a routed wire runs the cycle check', () => {
  it('refuses X -> (into N) when N already feeds X', () => {
    const g = graphOf([
      node('n', 'subnet', null, [0, 0]),
      node('k', 'rsi', 'n', [20, 60]),
      node('nout', 'subnet_output', 'n', [20, 200]),
      node('x', 'rsi', null, [400, 400]),
    ], [wire('w1', 'k', 'nout'), wire('w2', 'n', 'x')])
    expect(networkConnectionProblem(g, { source: 'x', sourceHandle: 'out', target: 'k', targetHandle: 'in0' })).toBe('cycle')
  })

  it('still allows a routed wire with no cycle', () => {
    const g = graphOf([
      node('n', 'subnet', null, [0, 0]),
      node('k', 'rsi', 'n', [20, 60]),
      node('x', 'ticker', null, [400, 400], { symbol: 'AAPL', interval: '1d' }),
    ])
    expect(networkConnectionProblem(g, { source: 'x', sourceHandle: 'out', target: 'k', targetHandle: 'in0' })).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// FE-03
// ---------------------------------------------------------------------------

describe('FE-03: stored ticker paths follow renames and moves', () => {
  it('renaming the primary Ticker rewrites the group path', () => {
    const g = chainGraph()
    const next = renameNode(g, 'aapl', 'apple')
    expect(next.nodes.leg.params.ticker).toBe('/apple')
    expect(groupTickerOf(next.nodes, next.nodes.leg)?.id).toBe('aapl')
  })

  it('renaming a network that holds the Ticker rewrites the paths under it', () => {
    const g = graphOf([
      node('net', 'subnet', null, [0, 0]),
      node('aapl', 'ticker', 'net', [20, 60], { symbol: 'AAPL', interval: '1d' }),
      node('leg', 'output_group', null, [600, 0], { ticker: '/net/aapl' }),
    ])
    const next = renameNode(g, 'net', 'data')
    expect(next.nodes.leg.params.ticker).toBe('/data/aapl')
  })

  it('dragging the Ticker into a frame rewrites the path', () => {
    const g = chainGraph()
    const res = reparentNode(g, 'aapl', 'leg', [620, 200])
    expect(res.graph.nodes.leg.params.ticker).toBe('/leg/aapl')
    expect(groupTickerOf(res.graph.nodes, res.graph.nodes.leg)?.id).toBe('aapl')
  })

  it('a relative path that still lands keeps its form', () => {
    const g = graphOf([
      node('leg', 'output_group', null, [600, 0], { ticker: 'aapl' }),
      node('aapl', 'ticker', 'leg', [620, 60], { symbol: 'AAPL', interval: '1d' }),
    ])
    // The group moves its own name; `aapl` relative to the group still works.
    const next = renameNode(g, 'leg', 'leg2')
    expect(next.nodes.leg.params.ticker).toBe('aapl')
    const moved = rewritePathRefs(renameNode(g, 'aapl', 'apple'), '/nothing', '/else')
    expect(moved.nodes.leg.params.ticker).toBe('/leg/apple')
  })

  it('joinPath follows the Houdini rules', () => {
    expect(joinPath('/a/b', '../c')).toBe('/a/c')
    expect(joinPath('/a', 'b/./c')).toBe('/a/b/c')
    expect(joinPath('/', '..')).toBeNull()
    expect(joinPath('/a', '/x')).toBe('/x')
  })
})

// ---------------------------------------------------------------------------
// FE-04 / UX-06, FE-10
// ---------------------------------------------------------------------------

describe('FE-04 / UX-06 / FE-10: one weight rule, one ticker resolver', () => {
  it('a weight-0 group keeps 0 and gets no capital; no direction reads long', () => {
    const g = graphOf([
      node('t1', 'ticker', null, [0, 0], { symbol: 'AAPL', interval: '1d' }),
      node('g1', 'output_group', null, [0, 0], { direction: 'long', ticker: '/t1', capital_weight: 1 }),
      node('g2', 'output_group', null, [0, 0], { ticker: '/t1', capital_weight: 0 }),
    ])
    const groups = listGraphGroups(g)
    expect(groups.map(x => x.weight)).toEqual([1, 0])
    expect(groups[1].direction).toBe('long')
    expect(splitCapital(10000, groups)).toEqual([10000, 0])
  })

  it('the header finds a Ticker inside the group by a relative path, as the server does', () => {
    const g = graphOf([
      node('leg', 'output_group', null, [600, 0], { ticker: 'aapl' }),
      node('aapl', 'ticker', 'leg', [620, 60], { symbol: 'AAPL', interval: '1d' }),
    ])
    expect(groupTickerOf(g.nodes, g.nodes.leg)).toMatchObject({ id: 'aapl', symbol: 'AAPL' })
    expect(isGroupPrimary(g, 'aapl')).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// FE-05 / UX-11
// ---------------------------------------------------------------------------

describe('FE-05: frames wrap measured card sizes', () => {
  function tallGroup(): Graph {
    return graphOf([
      node('leg', 'output_group', null, [0, 0], { direction: 'long' }),
      node('entry', 'entry', 'leg', [100, 100], { signal: null }),
      node('exit', 'exit', 'leg', [300, 100], { signal: null }),
      node('trail', 'trailing_stop', 'leg', [100, 300], {}),
    ])
  }

  it('a tall card stretches the frame; the default applies until measured', () => {
    const g = tallGroup()
    const plain = computeFrameLayouts(g.nodes).get('leg')!
    const sizes = new Map([['trail', { w: 118, h: 140 }]])
    const measured = computeFrameLayouts(g.nodes, measuredSizeOf(sizes)).get('leg')!
    expect(measured.h - plain.h).toBe(140 - DEFAULT_NODE_SIZE.h)
    // The frame's bottom edge is below the tall card's bottom.
    expect(measured.y + measured.h).toBeGreaterThan(300 + 140)
  })

  it('the node mapper takes the sizes: the frame node grows, other nodes are reused', () => {
    const g = tallGroup()
    const map = createNodeMapper()
    const first = map(g.nodes, true)
    const second = map(g.nodes, true, new Map([['trail', { w: 118, h: 140 }]]))
    const frame1 = first.find(n => n.id === 'leg')!
    const frame2 = second.find(n => n.id === 'leg')!
    expect(frame2.height).toBe((frame1.height as number) + 140 - DEFAULT_NODE_SIZE.h)
    expect(second.find(n => n.id === 'entry')).toBe(first.find(n => n.id === 'entry'))
  })
})

// ---------------------------------------------------------------------------
// FE-06 / UX-01, UX-14 header
// ---------------------------------------------------------------------------

function mount(g: Graph) {
  useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
  const graph = useNodeBuilderStore.getState().graph!
  return render(
    <div className="nodebuilder-root" style={{ width: 1200, height: 900 }}>
      <Canvas graph={graph} />
    </div>,
  )
}

describe('FE-06: header lists open outside the frame', () => {
  it('the direction list is portalled out of the frame node', () => {
    mount(chainGraph())
    fireEvent.click(screen.getByTestId('nb-group-direction-leg'))
    const list = screen.getByRole('listbox', { name: 'Direction' })
    const frameNode = document.querySelector('.react-flow__node[data-id="leg"]')!
    expect(frameNode).not.toBeNull()
    expect(frameNode.contains(list)).toBe(false)
    expect(list.closest('.nb-popover')).not.toBeNull()
  })

  it('a pick inside the portal list still applies (no outside-close first), as one commit', () => {
    mount(chainGraph())
    const before = useNodeBuilderStore.getState().past.length
    fireEvent.click(screen.getByTestId('nb-group-direction-leg'))
    const list = screen.getByRole('listbox', { name: 'Direction' })
    const row = within(list).getByText('SWITCH')
    fireEvent.pointerDown(row)
    fireEvent.mouseDown(row)
    fireEvent.click(row)
    const g = useNodeBuilderStore.getState().graph!
    expect(g.nodes.leg.params.direction).toBe('regime_switch')
    // UX-14: the existing entry is now explicitly the long side.
    expect(g.nodes.entry.params.side).toBe('long')
    expect(useNodeBuilderStore.getState().past.length - before).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// FE-09
// ---------------------------------------------------------------------------

describe('FE-09: the crossing flash reads the drop target', () => {
  const g = graphOf([
    node('leg', 'output_group', null, [0, 0], {}),
    node('other', 'output_group', null, [600, 0], {}),
    node('in_a', 'rsi', 'leg', [20, 60], {}),
    node('in_b', 'rsi', 'other', [620, 60], {}),
    node('root', 'rsi', null, [0, 600], {}),
  ])
  it('a drop on a port across two groups crosses', () => {
    expect(dropCrossesNetwork(g, {
      fromHandle: { nodeId: 'in_a', id: 'out', type: 'source' },
      toHandle: { nodeId: 'in_b', id: 'in0', type: 'target' },
    })).toBe(true)
  })
  it('a drop on no port (a node body or empty space) never crosses', () => {
    expect(dropCrossesNetwork(g, { fromHandle: { nodeId: 'in_a', id: 'out', type: 'source' }, toHandle: null })).toBe(false)
    expect(dropCrossesNetwork(g, null)).toBe(false)
  })
  it('a drop on a fine port does not cross', () => {
    expect(dropCrossesNetwork(g, {
      fromHandle: { nodeId: 'root', id: 'out', type: 'source' },
      toHandle: { nodeId: 'in_a', id: 'in0', type: 'target' },
    })).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// UX-04
// ---------------------------------------------------------------------------

describe('UX-04: the frame under the Tab-menu cursor', () => {
  it('picks the innermost frame at the point, inside the network on screen only', () => {
    const g = graphOf([
      node('outer', 'subnet', null, [0, 0], {}),
      node('leg', 'output_group', 'outer', [40, 80], {}),
      node('entry', 'entry', 'leg', [60, 140], {}),
      node('far', 'output_group', null, [2000, 2000], {}),
    ])
    const layouts: Map<string, FrameLayout> = computeFrameLayouts(g.nodes)
    const leg = layouts.get('leg')!
    const inLeg = { x: leg.x + 5, y: leg.y + 5 }
    expect(frameAtPoint(g.nodes, layouts, inLeg, null)).toBe('leg')
    expect(frameAtPoint(g.nodes, layouts, inLeg, 'outer')).toBe('leg')
    expect(frameAtPoint(g.nodes, layouts, { x: -500, y: -500 }, null)).toBeNull()
    // Diving into `leg` itself: it is the network on screen, not a target.
    expect(frameAtPoint(g.nodes, layouts, inLeg, 'leg')).toBeNull()
  })
})

describe('UX-04: the Tab menu over a frame adds the node into it', () => {
  function tabCreateAt(flow: { x: number; y: number }, query: string) {
    act(() => { getActiveCanvas()!.openTabMenu({ flow }) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: query } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
  }

  it('a terminal made over the group joins it; one made elsewhere stays at the root', () => {
    const g = chainGraph()
    mount(g)
    const leg = computeFrameLayouts(g.nodes).get('leg')!
    tabCreateAt({ x: leg.x + 10, y: leg.y + leg.h - 30 }, 'exit')
    let graph = useNodeBuilderStore.getState().graph!
    const exit = Object.values(graph.nodes).find(n => n.type === 'exit')!
    expect(exit.parent).toBe('leg')
    tabCreateAt({ x: -800, y: -800 }, 'rsi')
    graph = useNodeBuilderStore.getState().graph!
    const rsis = Object.values(graph.nodes).filter(n => n.type === 'rsi' && n.id !== 'a')
    expect(rsis).toHaveLength(1)
    expect(rsis[0].parent).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// UX-02
// ---------------------------------------------------------------------------

describe('UX-02: size reads as a percent, a zero stop as none', () => {
  it('view helpers convert both ways', () => {
    expect(viewText(1, 'percent')).toBe('100')
    expect(viewText(0.25, 'percent')).toBe('25')
    expect(viewValue(50, 'percent')).toBe(0.5)
    expect(viewText(0, 'stop')).toBe('')
    expect(viewText(2.5, 'stop')).toBe('2.5')
    expect(terminalRowViews('size')).toEqual({ constant: 'percent' })
    expect(terminalRowViews('stop')).toEqual({ constant: 'stop' })
  })

  it('typing 50 in a size constant stores 0.5 and shows 50 %', () => {
    const g = graphOf([node('sz', 'size', null, [0, 0], { value: null, constant: 1 })])
    useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
    render(
      <div className="nodebuilder-root">
        <ParamRows nodeId="sz" params={{ constant: 1 }} paramTypes={{ constant: { type: 'number', unit: 'frac' } }} views={{ constant: 'percent' }} />
      </div>,
    )
    const input = screen.getByTestId('nb-param-sz-constant') as HTMLInputElement
    expect(input.value).toBe('100')
    expect(screen.getByTestId('param-unit').textContent).toBe('%')
    act(() => { input.focus() })
    fireEvent.change(input, { target: { value: '50' } })
    act(() => { input.blur() })
    expect(useNodeBuilderStore.getState().graph!.nodes.sz.params.constant).toBe(0.5)
    expect(input.value).toBe('50')
  })

  it('a zero stop shows an empty field with a dim none placeholder', () => {
    const g = graphOf([node('st', 'stop', null, [0, 0], { value: null, constant: 0 })])
    useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
    render(<ParamRows nodeId="st" params={{ constant: 0 }} paramTypes={{ constant: { type: 'number' } }} views={{ constant: 'stop' }} />)
    const input = screen.getByTestId('nb-param-st-constant') as HTMLInputElement
    expect(input.value).toBe('')
    expect(input.placeholder).toBe('none')
    expect(input.className).toContain('nb-param-none')
  })
})

// ---------------------------------------------------------------------------
// UX-09
// ---------------------------------------------------------------------------

describe('UX-09: no prefix row on a primary Ticker', () => {
  it('hides the prefix on a primary, keeps it on a reference and on a primary with a stray prefix', () => {
    expect(tickerRowParams({ symbol: 'AAPL', interval: '1d' }, true)).toEqual({ symbol: 'AAPL', interval: '1d' })
    expect(tickerRowParams({ symbol: 'SPY', interval: '1d' }, false)).toEqual({ symbol: 'SPY', interval: '1d', prefix: '' })
    expect(tickerRowParams({ symbol: 'AAPL', prefix: 'aapl' }, true)).toEqual({ symbol: 'AAPL', prefix: 'aapl' })
  })

  it('the Ticker card of a group primary renders no prefix field', () => {
    mount(chainGraph())
    expect(screen.queryByTestId('nb-param-aapl-prefix')).toBeNull()
    expect(screen.getByTestId('nb-param-aapl-symbol')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// UX-14 and the side rows
// ---------------------------------------------------------------------------

describe('UX-14: a missing side reads long', () => {
  it('a regime_switch group with a side-less entry asks only for the short entry', () => {
    const g = graphOf([
      node('leg', 'output_group', null, [0, 0], { direction: 'regime_switch' }),
      node('entry', 'entry', 'leg', [20, 60], { signal: null }),
    ])
    const labels = missingTerminals(g.nodes, 'leg').map(m => m.label)
    expect(labels).not.toContain('+ entry (long)')
    expect(labels).toContain('+ entry (short)')
    expect(terminalNote('entry', {}, 'regime_switch')).toBe('long')
  })

  it('switching to SWITCH writes side long on side-less entry and exit only', () => {
    const g = graphOf([
      node('leg', 'output_group', null, [0, 0], { direction: 'long' }),
      node('entry', 'entry', 'leg', [20, 60], { signal: null }),
      node('exit', 'exit', 'leg', [140, 60], { signal: null, side: 'short' }),
      node('stop', 'stop', 'leg', [260, 60], { constant: 2 }),
    ])
    const next = withGroupDirection(g, 'leg', 'regime_switch')
    expect(next.nodes.leg.params.direction).toBe('regime_switch')
    expect(next.nodes.entry.params.side).toBe('long')
    expect(next.nodes.exit.params.side).toBe('short')
    expect('side' in next.nodes.stop.params).toBe(false)
    // Any other direction leaves the terminals alone.
    expect(withGroupDirection(g, 'leg', 'short').nodes.entry.params.side).toBeUndefined()
  })

  it('stop, size, trailing and time stop show a side row only in a regime_switch group', () => {
    const none = new Set<string>()
    for (const t of ['stop', 'size', 'trailing_stop', 'time_stop']) {
      expect(terminalRowParams(t, { side: 'short' }, none, 'regime_switch')).toMatchObject({ side: 'short' })
      expect(terminalRowParams(t, { side: 'short' }, none, 'long')).not.toHaveProperty('side')
      // No stored side: the row still shows (reads long) when the catalog has the param.
      expect(terminalRowParams(t, {}, none, 'regime_switch', true)).toMatchObject({ side: 'long' })
      expect(terminalRowParams(t, {}, none, 'regime_switch', false)).not.toHaveProperty('side')
      expect(terminalNote(t, { side: 'short' }, 'regime_switch')).toBe('short')
    }
  })
})
