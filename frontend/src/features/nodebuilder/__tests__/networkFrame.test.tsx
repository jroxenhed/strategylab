/**
 * Network frames (W5 item 5.E, specs S31, S32a, S32b).
 *
 * - rfMapping: a network becomes one frame; its children become React Flow
 *   children (`parentId`) with frame-relative positions that convert back to
 *   the same absolute position; boundary nodes are ports, not cards; parents
 *   come before children; boundary wires attach to the frame's ports.
 * - networkOps: routing a wire into a network through a port (FA2), the
 *   refusal across two groups, drop-in and drag-out reparenting, and frame
 *   drags moving everything inside.
 * - The canvas draws one frame, its child cards, one port per boundary
 *   input and no boundary cards; the group header reads
 *   `O long_leg LONG AAPL · 1d 1×`; the direction pill and the ghost cards
 *   edit the store.
 */

import { describe, it, expect, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import type { Graph, GraphNode, GraphWire } from '../../../api/nodebuilder'
import Canvas from '../Canvas'
import { useNodeBuilderStore } from '../store'
import {
  FRAME_PAD,
  FRAME_RF_TYPE,
  FRAME_TAB_ROOM,
  absoluteLookup,
  computeFrameLayouts,
  createEdgeMapper,
  createNodeMapper,
  graphPositionOf,
  missingTerminals,
  type FrameNodeData,
} from '../rfMapping'
import { frameMoveDeltas, reparentNode, routeInto } from '../networkOps'
import { reparentTargetOf } from '../plugins/networkFrames'
import { terminalNote, terminalRowParams, terminalTitle } from '../nodes/networkFormat'

// ---------------------------------------------------------------------------
// Fixture: AAPL ticker and an RSI at the root, an Output Group `long_leg`
// with four terminals and two boundary inputs.
// ---------------------------------------------------------------------------

function node(id: string, type: string, parent: string | null, position: [number, number], params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent, params, position, display: false, bypass: false }
}

function makeGraph(): Graph {
  const nodes: Record<string, GraphNode> = {
    aapl: node('aapl', 'ticker', null, [0, 0], { symbol: 'AAPL', interval: '1d' }),
    rsi: node('rsi', 'rsi', null, [0, 150], { period: 14 }),
    long_leg: node('long_leg', 'output_group', null, [300, 300], { direction: 'long', ticker: '/aapl', capital_weight: 1 }),
    // Listed out of port order on purpose: ports follow the `port` param.
    in1: node('in1', 'subnet_input', 'long_leg', [600, 380], { port: 1 }),
    in0: node('in0', 'subnet_input', 'long_leg', [400, 380], { port: 0 }),
    entry: node('entry', 'entry', 'long_leg', [400, 500], { signal: '@go_long' }),
    exit: node('exit', 'exit', 'long_leg', [530, 500], { signal: '@go_flat' }),
    size: node('size', 'size', 'long_leg', [660, 500], { constant: 1 }),
    stop: node('stop', 'stop', 'long_leg', [790, 500], { constant: 2.5 }),
  }
  const wires: GraphWire[] = [
    { id: 'w_rsi', from: 'aapl', to: 'rsi', from_port: 'out', to_port: 'in0' },
    { id: 'w_out0', from: 'rsi', to: 'long_leg', from_port: 'out', to_port: 'in0' },
    { id: 'w_in0', from: 'in0', to: 'entry', from_port: 'out', to_port: 'in0' },
    { id: 'w_in1', from: 'in1', to: 'exit', from_port: 'out', to_port: 'in0' },
  ]
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes,
    wires,
    annotations: { boxes: [], notes: [] },
  }
}

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

afterEach(() => {
  cleanup()
  useNodeBuilderStore.getState().discardEdits()
})

// ---------------------------------------------------------------------------
// rfMapping
// ---------------------------------------------------------------------------

describe('rfMapping: network frames', () => {
  it('maps a group to one frame with 4 child cards, 2 ports and no boundary cards', () => {
    const g = makeGraph()
    const rf = createNodeMapper()(g.nodes, true)
    const frame = rf.find(n => n.id === 'long_leg')!
    expect(frame.type).toBe(FRAME_RF_TYPE)
    const kids = rf.filter(n => n.parentId === 'long_leg')
    expect(kids.map(n => n.id).sort()).toEqual(['entry', 'exit', 'size', 'stop'])
    expect(rf.some(n => n.id === 'in0' || n.id === 'in1')).toBe(false)
    const data = frame.data as FrameNodeData
    expect(data.frame.inputs.map(p => p.handle)).toEqual(['in0', 'in1'])
    expect(data.frame.inputs.map(p => p.boundaryId)).toEqual(['in0', 'in1'])
    expect(data.frame.childCount).toBe(4)
    expect(data.groupTicker).toMatchObject({ id: 'aapl', symbol: 'AAPL', interval: '1d' })
  })

  it('draws the frame around its children plus padding and the tab room', () => {
    const g = makeGraph()
    const l = computeFrameLayouts(g.nodes).get('long_leg')!
    expect(l.x).toBe(400 - FRAME_PAD)
    expect(l.y).toBe(500 - FRAME_PAD - FRAME_TAB_ROOM)
    expect(l.missing).toEqual([])
  })

  it('puts parents before children and keeps child positions frame-relative (round trip ±0.5)', () => {
    const g = makeGraph()
    const rf = createNodeMapper()(g.nodes, true)
    const idx = (id: string) => rf.findIndex(n => n.id === id)
    for (const k of ['entry', 'exit', 'size', 'stop']) expect(idx('long_leg')).toBeLessThan(idx(k))
    const abs = absoluteLookup(rf)
    for (const k of ['entry', 'exit', 'size', 'stop', 'aapl']) {
      const n = rf.find(x => x.id === k)!
      const [x, y] = graphPositionOf(n, abs)
      expect(Math.abs(x - g.nodes[k].position[0])).toBeLessThanOrEqual(0.5)
      expect(Math.abs(y - g.nodes[k].position[1])).toBeLessThanOrEqual(0.5)
    }
    // entry sits FRAME_PAD in from the frame's left edge.
    expect(rf.find(x => x.id === 'entry')!.position).toEqual({ x: FRAME_PAD, y: FRAME_PAD + FRAME_TAB_ROOM })
  })

  it('reuses unchanged children but moves all of them when the frame corner moves', () => {
    const g = makeGraph()
    const map = createNodeMapper()
    const a = map(g.nodes, true)
    // Same graph: same array.
    expect(map(g.nodes, true)).toBe(a)
    // Move `entry` left: the frame corner moves, so every sibling is re-placed.
    const moved = { ...g.nodes, entry: { ...g.nodes.entry, position: [300, 500] as [number, number] } }
    const b = map(moved, true)
    const exitA = a.find(n => n.id === 'exit')!
    const exitB = b.find(n => n.id === 'exit')!
    expect(exitB).not.toBe(exitA)
    expect(exitB.position.x).toBe(exitA.position.x + 100)
    // The ticker at the root is untouched.
    expect(b.find(n => n.id === 'aapl')).toBe(a.find(n => n.id === 'aapl'))
  })

  it('orders ports by the `port` param (default 0) and leaves out a port that is taken twice', () => {
    const g = makeGraph()
    g.nodes.in2 = node('in2', 'subnet_input', 'long_leg', [700, 380], { port: 1 })
    const l = computeFrameLayouts(g.nodes).get('long_leg')!
    expect(l.inputs.map(p => `${p.handle}:${p.boundaryId}`)).toEqual(['in0:in0', 'in1:in1'])
    const h = makeGraph()
    h.nodes.in0 = { ...h.nodes.in0, params: {} }
    expect(computeFrameLayouts(h.nodes).get('long_leg')!.inputs[0]).toMatchObject({ handle: 'in0', boundaryId: 'in0' })
  })

  it('attaches boundary wires to the frame ports', () => {
    const g = makeGraph()
    const edges = createEdgeMapper()({
      graph: g, selectedWireId: null, labels: {}, placements: {}, lowZoom: false, diagByWire: new Map(),
    })
    const byId = Object.fromEntries(edges.map(e => [e.id, e]))
    expect(byId.w_out0).toMatchObject({ source: 'rsi', target: 'long_leg', targetHandle: 'in0' })
    expect(byId.w_in0).toMatchObject({ source: 'long_leg', sourceHandle: 'bnd:in0', target: 'entry', targetHandle: 'in0' })
    expect(byId.w_in1).toMatchObject({ source: 'long_leg', sourceHandle: 'bnd:in1', target: 'exit' })
  })

  it('lists missing terminals as ghosts (regime_switch needs both sides and a regime)', () => {
    const g = makeGraph()
    delete g.nodes.exit
    expect(missingTerminals(g.nodes, 'long_leg').map(m => m.label)).toEqual(['+ exit'])
    g.nodes.long_leg = { ...g.nodes.long_leg, params: { ...g.nodes.long_leg.params, direction: 'regime_switch' } }
    g.nodes.entry = { ...g.nodes.entry, params: { signal: '@a', side: 'long' } }
    expect(missingTerminals(g.nodes, 'long_leg').map(m => m.label)).toEqual([
      '+ entry (short)', '+ exit (long)', '+ exit (short)', '+ regime',
    ])
  })
})

// ---------------------------------------------------------------------------
// networkOps
// ---------------------------------------------------------------------------

describe('networkOps', () => {
  it('routes an outside node into an inside port: one subnet_input, one wire out, one wire in', () => {
    const g = makeGraph()
    const before = Object.keys(g.nodes).length
    const next = routeInto(g, 'aapl', 'long_leg', 'size', 'in0')!
    const added = Object.values(next.nodes).filter(n => !(n.id in g.nodes))
    expect(added).toHaveLength(1)
    expect(added[0]).toMatchObject({ type: 'subnet_input', parent: 'long_leg', params: { port: 2 } })
    expect(Object.keys(next.nodes)).toHaveLength(before + 1)
    const newWires = next.wires.filter(w => !g.wires.some(x => x.id === w.id))
    expect(newWires).toHaveLength(2)
    expect(newWires).toContainEqual(expect.objectContaining({ from: 'aapl', to: 'long_leg', to_port: 'in2' }))
    expect(newWires).toContainEqual(expect.objectContaining({ from: added[0].id, to: 'size', to_port: 'in0' }))
  })

  it('reuses the port a source already feeds', () => {
    const g = makeGraph()
    const next = routeInto(g, 'rsi', 'long_leg', 'size', 'in0')!
    expect(Object.keys(next.nodes)).toHaveLength(Object.keys(g.nodes).length)
    expect(next.wires.filter(w => !g.wires.some(x => x.id === w.id))).toEqual([
      expect.objectContaining({ from: 'in0', to: 'size', to_port: 'in0' }),
    ])
  })

  it('refuses a wire between two different groups', () => {
    const g = makeGraph()
    g.nodes.short_leg = node('short_leg', 'output_group', null, [1200, 300], { direction: 'short' })
    g.nodes.s_entry = node('s_entry', 'entry', 'short_leg', [1250, 500])
    expect(routeInto(g, 'entry', 'short_leg', 's_entry', 'in0')).toBeNull()
  })

  it('drop-in: a root node joins the group and its input is routed through a port', () => {
    const g = makeGraph()
    // rsi reads aapl; drop rsi into long_leg.
    const res = reparentNode(g, 'rsi', 'long_leg', [450, 420])
    expect(res.graph.nodes.rsi).toMatchObject({ parent: 'long_leg', position: [450, 420] })
    // rsi fed long_leg.in0; now inside, the port collapses: rsi feeds entry
    // directly and the unused boundary in0 goes (FE-01). Nothing is lost.
    expect(res.removedWires).toBe(0)
    expect(res.graph.wires).toContainEqual(expect.objectContaining({ from: 'rsi', to: 'entry', to_port: 'in0' }))
    expect(res.graph.nodes.in0).toBeUndefined()
    expect(res.graph.wires.some(w => w.to === 'long_leg' && w.to_port === 'in0')).toBe(false)
    const bnd = Object.values(res.graph.nodes).find(n => n.type === 'subnet_input' && !(n.id in g.nodes))!
    expect(res.graph.wires).toContainEqual(expect.objectContaining({ from: 'aapl', to: 'long_leg' }))
    expect(res.graph.wires).toContainEqual(expect.objectContaining({ from: bnd.id, to: 'rsi', to_port: 'in0' }))
    expect(res.graph.wires.some(w => w.from === 'aapl' && w.to === 'rsi')).toBe(false)
  })

  it('drag-out: an inside node fed by a port reads the port source directly', () => {
    const g = makeGraph()
    const res = reparentNode(g, 'entry', null, [900, 900])
    expect(res.graph.nodes.entry.parent).toBeNull()
    expect(res.removedWires).toBe(0)
    expect(res.graph.wires).toContainEqual(expect.objectContaining({ from: 'rsi', to: 'entry', to_port: 'in0' }))
    expect(res.graph.wires.some(w => w.from === 'in0' && w.to === 'entry')).toBe(false)
  })

  it('a dragged frame moves itself and everything inside by the same delta', () => {
    const g = makeGraph()
    const l = computeFrameLayouts(g.nodes).get('long_leg')!
    const { ids, deltas } = frameMoveDeltas(g.nodes, [
      { id: 'long_leg', position: [l.x + 50, l.y + 20] },
      // A selected child dragged along is not moved twice.
      { id: 'entry', position: [999, 999] },
    ])
    const d = Object.fromEntries(ids.map((id, i) => [id, deltas[i]]))
    for (const id of ['long_leg', 'in0', 'in1', 'entry', 'exit', 'size', 'stop']) expect(d[id]).toEqual([50, 20])
    expect(d.aapl).toBeUndefined()
  })

  it('drop target: over a frame joins it; half over the current frame stays; fully outside leaves', () => {
    const g = makeGraph()
    const layouts = computeFrameLayouts(g.nodes)
    const l = layouts.get('long_leg')!
    const none = new Set<string>()
    expect(reparentTargetOf(g.nodes, layouts, 'rsi', { x: l.x + 20, y: l.y + 40, w: 100, h: 50 }, none)).toBe('long_leg')
    expect(reparentTargetOf(g.nodes, layouts, 'entry', { x: l.x + l.w - 30, y: l.y + 40, w: 100, h: 50 }, none)).toBeUndefined()
    expect(reparentTargetOf(g.nodes, layouts, 'entry', { x: l.x + l.w + 200, y: l.y, w: 100, h: 50 }, none)).toBeNull()
    // A boundary node never moves between networks.
    expect(reparentTargetOf(g.nodes, layouts, 'in0', { x: -900, y: -900, w: 10, h: 10 }, none)).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Terminal cards (S32b)
// ---------------------------------------------------------------------------

describe('terminal cards', () => {
  it('names terminals by type and shows the type-slot notes', () => {
    expect(terminalTitle('trailing_stop')).toBe('trailing')
    expect(terminalTitle('time_stop')).toBe('time stop')
    expect(terminalNote('trailing_stop', { pct: 2.5, activate_on_profit: true, activate_pct: 1 }, 'long')).toBe('>+1.0 %')
    expect(terminalNote('entry', { side: 'short' }, 'regime_switch')).toBe('short')
    expect(terminalNote('entry', { side: 'short' }, 'long')).toBeUndefined()
    expect(terminalNote('size', { constant: 0.5 }, 'long')).toBe('const')
    expect(terminalNote('size', { value: '@size_frac', constant: 0.5 }, 'long')).toBeUndefined()
  })

  it('hides side outside regime_switch, attr reads, and the constant when an attribute is set', () => {
    const attrs = new Set(['signal', 'value'])
    expect(terminalRowParams('entry', { signal: '@a', side: 'long' }, attrs, 'long')).toEqual({})
    expect(terminalRowParams('entry', { signal: '@a', side: 'long' }, attrs, 'regime_switch')).toEqual({ side: 'long' })
    expect(terminalRowParams('size', { value: '@f', constant: 1 }, attrs, 'long')).toEqual({})
    expect(terminalRowParams('trailing_stop', { pct: 2, source: 'close', activate_on_profit: true, activate_pct: 1 }, attrs, 'long'))
      .toEqual({ pct: 2, source: 'close' })
  })
})

// ---------------------------------------------------------------------------
// The canvas
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

describe('Canvas: network frame (S31, S32a)', () => {
  it('renders one frame, 4 child cards, one port per boundary input and no boundary cards', () => {
    const { container } = mount(makeGraph())
    expect(container.querySelectorAll('[data-testid^="nb-frame-tab-"]')).toHaveLength(1)
    const frame = screen.getByTestId('nb-frame-long_leg')
    expect(frame).toHaveAttribute('role', 'group')
    expect(frame).toHaveAttribute('aria-label', 'Output group long_leg, long, AAPL 1d')
    for (const k of ['entry', 'exit', 'size', 'stop']) {
      expect(container.querySelector(`.react-flow__node[data-id="${k}"]`)).not.toBeNull()
    }
    expect(container.querySelector('.react-flow__node[data-id="in0"]')).toBeNull()
    expect(container.querySelector('.react-flow__node[data-id="in1"]')).toBeNull()
    expect(screen.getByTestId('nb-frame-port-long_leg-in0')).toBeTruthy()
    expect(screen.getByTestId('nb-frame-port-long_leg-in1')).toBeTruthy()
    expect(container.querySelectorAll('[data-testid^="nb-frame-port-long_leg-"]')).toHaveLength(2)
  })

  it('tab reads O long_leg LONG AAPL · 1d 1× in that order', () => {
    mount(makeGraph())
    const tab = screen.getByTestId('nb-frame-tab-long_leg')
    expect(tab.textContent).toBe('Olong_legLONGAAPL · 1d1×')
  })

  it('choosing SWITCH writes params.direction = regime_switch (one commit)', () => {
    mount(makeGraph())
    fireEvent.click(screen.getByTestId('nb-group-direction-long_leg'))
    const list = screen.getByRole('listbox', { name: 'Direction' })
    fireEvent.click(within(list).getByText('SWITCH'))
    expect(useNodeBuilderStore.getState().graph!.nodes.long_leg.params.direction).toBe('regime_switch')
  })

  it('shows a + exit ghost when exit is missing; clicking it adds exit inside the group', () => {
    const g = makeGraph()
    delete g.nodes.exit
    g.wires = g.wires.filter(w => w.to !== 'exit')
    mount(g)
    const ghost = screen.getByTestId('nb-frame-ghost-long_leg-exit')
    expect(ghost.textContent).toBe('+ exit')
    fireEvent.click(ghost)
    const added = Object.values(useNodeBuilderStore.getState().graph!.nodes).filter(n => n.type === 'exit')
    expect(added).toHaveLength(1)
    expect(added[0].parent).toBe('long_leg')
  })

  it('a weight of 0 reads 0× with the no-capital tooltip', () => {
    const g = makeGraph()
    g.nodes.long_leg = { ...g.nodes.long_leg, params: { ...g.nodes.long_leg.params, capital_weight: 0 } }
    mount(g)
    const w = screen.getByTestId('nb-group-weight-long_leg')
    expect(w.textContent).toBe('0×')
    expect(w).toHaveAttribute('title', 'This group gets no capital and will not trade.')
  })

  it('an empty subnet frame shows the empty text', () => {
    const g = makeGraph()
    g.nodes.sub = node('sub', 'subnet', null, [0, 900])
    mount(g)
    expect(within(screen.getByTestId('nb-frame-sub')).getByText('Empty network. Drag nodes in, or press Tab inside it.')).toBeTruthy()
  })
})
