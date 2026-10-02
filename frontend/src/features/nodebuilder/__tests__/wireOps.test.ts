/**
 * Wire moves and right-click menus (F435 W3 item 3.G, specs S19, S23):
 * reconnect, drop-on-empty delete, splice by drag and by the Tab menu,
 * delete with rewire, the long-wire fade, and the context menus built from
 * the command registry.
 *
 * Plugin hooks are called with a stand-in canvas (React Flow cannot drag in
 * jsdom); the delete keys and the menus run on a mounted Canvas.
 */

import { describe, it, expect, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { createElement, type MouseEvent as ReactMouseEvent } from 'react'
import { Position, type Edge as RFEdge, type FinalConnectionState, type Node as RFNode } from '@xyflow/react'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import Canvas from '../Canvas'
import AttrEdge, { type AttrEdgeData } from '../edges/AttrEdge'
import { ContextMenuHost } from '../ContextMenu'
import { buildMenu, closeContextMenu, formatChord, getContextMenu, MENU_LAYOUTS, type MenuItemRow } from '../contextMenuModel'
import { formatChord as formatKeyChord } from '../shortcutList'
import { isEmptyCanvasTarget } from '../canvasHelpers'
import type { CanvasCtx } from '../canvasPlugins'
import { getCommand, isCommandEnabled, listCommands, registerCommands, runCommand } from '../commands'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics } from '../useDiagnostics'
import { pointOnWire, portFraction } from '../streamLabels'
import {
  canSpliceNode,
  fullPortNotice,
  reconnectWire,
  rewiredCount,
  spliceIntoWire,
  spliceProblem,
  WireOpError,
} from '../operations/wires'
import {
  INVALID_FLASH_MS,
  SPLICE_HOT_MS,
  _resetWireOps,
  plugin as wireOps,
  reconnectingWireId,
} from '../plugins/wireOps'
import { plugin as contextMenus } from '../plugins/contextMenus'

// ── Fixtures ────────────────────────────────────────────────────────────────

function node(id: string, type: string, params: GraphNode['params'], x: number, y: number): GraphNode {
  return { id, type, name: id, parent: null, params, position: [x, y], display: false, bypass: false }
}

/**
 * t (ticker) → x (crosses_below, a = @close) on in0, an unwired rsi `r`,
 * an sma `s`, and an entry `e` fed by x.
 */
function makeGraph(): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d' }, 0, 0),
      x: node('x', 'crosses_below', { a: '@close', b: null, threshold: null, out: '@xb' }, 0, 400),
      r: node('r', 'rsi', { period: 14, type: 'wilder', source: null, out: '@rsi' }, 600, 200),
      s: node('s', 'sma', { period: 20, source: '@close', out: '@sma' }, 300, 150),
      e: node('e', 'entry', {}, 0, 700),
    },
    wires: [
      { id: 'w1', from: 't', to: 'x', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 't', to: 's', from_port: 'out', to_port: 'in0' },
      { id: 'w3', from: 'x', to: 'e', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

function load(g: Graph = makeGraph()): void {
  act(() => { useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' }) })
}

const st = () => useNodeBuilderStore.getState()
const graph = () => st().graph!

/** A stand-in canvas: nodes are 176x60 at their graph positions, flow = screen. */
function fakeCtx() {
  const updates: Array<[string, Record<string, unknown>]> = []
  const rf = {
    getInternalNode(id: string) {
      const n = st().graph?.nodes[id]
      if (!n) return undefined
      return {
        internals: { positionAbsolute: { x: n.position[0], y: n.position[1] }, handleBounds: null },
        measured: { width: 176, height: 60 },
      }
    },
    updateEdgeData(id: string, patch: Record<string, unknown>) { updates.push([id, patch]) },
    screenToFlowPosition: (p: { x: number; y: number }) => p,
    flowToScreenPosition: (p: { x: number; y: number }) => p,
  }
  const ctx = {
    rf,
    store: useNodeBuilderStore,
    pointer: () => ({ x: 0, y: 0 }),
    pointerOnCanvas: () => true,
    graph: () => st().graph!,
    editable: () => true,
    container: () => null,
    focus: () => {},
    openTabMenu: vi.fn(() => true),
    deleteSelection: vi.fn(() => true),
  } as unknown as CanvasCtx
  return { ctx, updates }
}

const ev = (over: Partial<{ metaKey: boolean; ctrlKey: boolean; altKey: boolean }> = {}) =>
  ({ metaKey: false, ctrlKey: false, altKey: false, preventDefault() {}, ...over }) as unknown as ReactMouseEvent

const rfEdge = (id: string): RFEdge => {
  const w = graph().wires.find(x => x.id === id)!
  return { id, source: w.from, target: w.to, sourceHandle: 'out', targetHandle: w.to_port }
}

/** The middle of wire t → x in0 for the stand-in canvas geometry. */
function w1Middle() {
  const s = { x: 88, y: 60 }
  const t = { x: 176 * portFraction(0, 2), y: 400 }
  return pointOnWire(s.x, s.y, t.x, t.y, 0.5)
}

/** An RF node for `id` whose center sits at `c`. */
function rfNodeAt(id: string, c: { x: number; y: number }): RFNode {
  return { id, position: { x: c.x - 88, y: c.y - 30 }, data: {}, measured: { width: 176, height: 60 } }
}

/** A drag stop as the canvas runs it: plugins, then the move, in one batch. */
function dragStop(n: RFNode, ctx: CanvasCtx, e = ev()) {
  const s = st()
  const from = s.graph!.nodes[n.id].position
  s.beginBatch()
  try {
    wireOps.onNodeDragStop!(e, n, [n], ctx)
    st().moveNodes([n.id], [[n.position.x - from[0], n.position.y - from[1]]])
  } finally {
    st().endBatch()
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

beforeEach(() => load())

afterEach(() => {
  cleanup()
  _resetWireOps()
  act(() => closeContextMenu())
  resetDiagnostics()
  vi.useRealTimers()
  st().discardEdits()
})

// ── Pure operations ─────────────────────────────────────────────────────────

describe('reconnectWire', () => {
  it('moves the target end to another port, keeps the id and the place, fills the new read', () => {
    const g = reconnectWire(graph(), 'w1', { source: 't', target: 'x', targetHandle: 'in1' })
    expect(g.wires.map(w => w.id)).toEqual(['w1', 'w2', 'w3'])
    expect(g.wires[0]).toMatchObject({ id: 'w1', from: 't', to: 'x', to_port: 'in1' })
    expect(g.nodes.x.params.b).toBe('@close')
    // The old read is left alone (as after a delete).
    expect(g.nodes.x.params.a).toBe('@close')
  })

  it('moves the source end onto another output; its own port does not count as full', () => {
    const g = reconnectWire(graph(), 'w1', { source: 's', target: 'x', targetHandle: 'in0' })
    expect(g.wires[0]).toMatchObject({ id: 'w1', from: 's', to: 'x', to_port: 'in0' })
  })

  it('refuses a full port, a loop, a wire into a Ticker and a self-loop', () => {
    const problem = (to: Parameters<typeof reconnectWire>[2], id = 'w1') => {
      try {
        reconnectWire(graph(), id, to)
        return null
      } catch (err) {
        return err instanceof WireOpError ? err.problem : 'other'
      }
    }
    expect(problem({ source: 't', target: 's', targetHandle: 'in0' })).toBe('full')
    expect(problem({ source: 'e', target: 'x', targetHandle: 'in0' })).toBe('no_port')
    expect(problem({ source: 'x', target: 't', targetHandle: 'in0' })).toBe('no_port')
    expect(problem({ source: 'x', target: 'x', targetHandle: 'in1' })).toBe('self')
    // A loop: with s → x in place, moving x → e to x → s would close s → x → s.
    const looped = reconnectWire(graph(), 'w2', { source: 's', target: 'x', targetHandle: 'in1' })
    load(looped)
    expect(problem({ source: 'x', target: 's', targetHandle: 'in0' }, 'w3')).toBe('cycle')
  })
})

describe('spliceIntoWire', () => {
  it('rsi onto ticker → crosses_below: two wires on in0 and the old port, rsi reads @close', () => {
    const before = graph().nodes.x.params
    const g = spliceIntoWire(graph(), 'r', 'w1')
    expect(g.wires.some(w => w.id === 'w1')).toBe(false)
    const intoR = g.wires.filter(w => w.to === 'r')
    const outOfR = g.wires.filter(w => w.from === 'r')
    expect(intoR).toEqual([expect.objectContaining({ from: 't', to: 'r', to_port: 'in0' })])
    expect(outOfR).toEqual([expect.objectContaining({ from: 'r', to: 'x', to_port: 'in0' })])
    expect(g.nodes.r.params.source).toBe('@close')
    // The old target's reads are untouched.
    expect(g.nodes.x.params).toEqual(before)
    // The new wires sit where the old one was.
    expect(g.wires.slice(0, 2).map(w => w.from)).toEqual(['t', 'r'])
  })

  it('is port-aware: a node whose in0 is taken uses its next free input', () => {
    // x has a free b (in1): splice x into t → s.
    let g = graph()
    g = { ...g, wires: g.wires.filter(w => w.id !== 'w3') }
    const out = spliceIntoWire(g, 'x', 'w2')
    expect(out.wires.find(w => w.to === 'x' && w.from === 't' && w.to_port === 'in1')).toBeDefined()
    expect(out.wires.find(w => w.from === 'x' && w.to === 's' && w.to_port === 'in0')).toBeDefined()
  })

  it('refuses a loop, an endpoint, and nodes with no free input or no output', () => {
    // r → x exists, so putting x into t → r would loop.
    const g = spliceIntoWire(graph(), 'r', 'w1') // t → r → x
    const tr = g.wires.find(w => w.from === 't' && w.to === 'r')!
    expect(spliceProblem(g, 'x', tr.id)).toBe('cycle')
    expect(spliceProblem(graph(), 'x', 'w1')).toBe('self')
    expect(spliceProblem(graph(), 'e', 'w2')).toBe('no_port') // entry has no output
    expect(canSpliceNode(graph(), 'e')).toBe(false)
    expect(canSpliceNode(graph(), 't')).toBe(false) // a ticker has no input
    expect(canSpliceNode(graph(), 'r')).toBe(true)
    expect(() => spliceIntoWire(graph(), 'e', 'w2')).toThrow(WireOpError)
  })
})

describe('notices', () => {
  it('a drag from a full input port gets a notice; a free one, an output, or the moving wire does not', () => {
    expect(fullPortNotice(graph(), { fromNodeId: 'x', handleType: 'target', handleId: 'in0' }))
      .toBe('Input a of x already has a wire. Drag its wire end to move it.')
    expect(fullPortNotice(graph(), { fromNodeId: 'x', handleType: 'target', handleId: 'in1' })).toBeNull()
    expect(fullPortNotice(graph(), { fromNodeId: 'x', handleType: 'source', handleId: 'out' })).toBeNull()
    expect(fullPortNotice(graph(), { fromNodeId: 'x', handleType: 'target', handleId: 'in0' }, 'w1')).toBeNull()
  })

  it('counts the wires a delete-with-rewire adds back', () => {
    expect(rewiredCount(graph(), ['x'])).toBe(1)
    expect(rewiredCount(graph(), ['s'])).toBe(0)
  })
})

// ── Reconnect through the plugin ────────────────────────────────────────────

describe('reconnect (plugin)', () => {
  it('a drop on a port changes to_port, keeps the wire id, in one history step', () => {
    const { ctx } = fakeCtx()
    wireOps.onReconnectStart!(ev(), rfEdge('w1'), 'target', ctx)
    expect(reconnectingWireId()).toBe('w1')
    act(() => { wireOps.onReconnect!(rfEdge('w1'), { source: 't', target: 'x', sourceHandle: 'out', targetHandle: 'in1' }, ctx) })
    wireOps.onReconnectEnd!(new MouseEvent('mouseup'), rfEdge('w1'), 'target', { toHandle: {}, isValid: true } as unknown as FinalConnectionState, ctx)
    expect(reconnectingWireId()).toBeNull()
    expect(graph().wires.find(w => w.id === 'w1')).toMatchObject({ to: 'x', to_port: 'in1' })
    expect(st().past).toHaveLength(1)
    act(() => st().undo())
    expect(graph().wires.find(w => w.id === 'w1')!.to_port).toBe('in0')
  })

  it('refuses an invalid drop with a status flash and no history', () => {
    const { ctx } = fakeCtx()
    act(() => { wireOps.onReconnect!(rfEdge('w1'), { source: 't', target: 's', sourceHandle: 'out', targetHandle: 'in0' }, ctx) })
    expect(st().past).toHaveLength(0)
    expect(st().flash?.text).toBe('That input already has a wire')
  })

  it('a drop on empty canvas flashes the wire, then deletes it in one step', () => {
    vi.useFakeTimers()
    const { ctx, updates } = fakeCtx()
    const pane = document.createElement('div')
    pane.className = 'react-flow__pane'
    const up = new MouseEvent('mouseup')
    Object.defineProperty(up, 'target', { value: pane })
    wireOps.onReconnectStart!(ev(), rfEdge('w1'), 'target', ctx)
    wireOps.onReconnectEnd!(up, rfEdge('w1'), 'target', { toHandle: null, isValid: null } as unknown as FinalConnectionState, ctx)
    expect(updates).toContainEqual(['w1', { flash: true }])
    expect(graph().wires.some(w => w.id === 'w1')).toBe(true)
    act(() => { vi.advanceTimersByTime(INVALID_FLASH_MS) })
    expect(graph().wires.some(w => w.id === 'w1')).toBe(false)
    expect(st().past).toHaveLength(1)
  })

  it('an undo during the flash cancels the delete and keeps the redo step (FC-6)', () => {
    vi.useFakeTimers()
    const { ctx } = fakeCtx()
    act(() => { st().moveNode('t', [5, 5]) })
    const pane = document.createElement('div')
    pane.className = 'react-flow__pane'
    const up = new MouseEvent('mouseup')
    Object.defineProperty(up, 'target', { value: pane })
    wireOps.onReconnectStart!(ev(), rfEdge('w1'), 'target', ctx)
    wireOps.onReconnectEnd!(up, rfEdge('w1'), 'target', { toHandle: null, isValid: null } as unknown as FinalConnectionState, ctx)
    act(() => { st().undo() })
    act(() => { vi.advanceTimersByTime(INVALID_FLASH_MS * 2) })
    expect(graph().wires.some(w => w.id === 'w1')).toBe(true)
    expect(st().canRedo).toBe(true)
  })

  it('a drop inside a box or note (not on a port) counts as empty canvas (UX-02)', () => {
    vi.useFakeTimers()
    const { ctx } = fakeCtx()
    const box = document.createElement('div')
    box.className = 'react-flow__node react-flow__node-nbBox'
    const inner = document.createElement('div')
    box.appendChild(inner)
    const up = new MouseEvent('mouseup')
    Object.defineProperty(up, 'target', { value: inner })
    wireOps.onReconnectStart!(ev(), rfEdge('w1'), 'target', ctx)
    wireOps.onReconnectEnd!(up, rfEdge('w1'), 'target', { toHandle: null, isValid: null } as unknown as FinalConnectionState, ctx)
    act(() => { vi.advanceTimersByTime(INVALID_FLASH_MS * 2) })
    expect(graph().wires.some(w => w.id === 'w1')).toBe(false)
    // A port or a text field inside a box is not empty canvas.
    const handle = document.createElement('div')
    handle.className = 'react-flow__handle'
    box.appendChild(handle)
    const input = document.createElement('input')
    box.appendChild(input)
    expect(isEmptyCanvasTarget(handle)).toBe(false)
    expect(isEmptyCanvasTarget(input)).toBe(false)
    expect(isEmptyCanvasTarget(inner)).toBe(true)
    const note = document.createElement('div')
    note.className = 'react-flow__node react-flow__node-nbNote'
    expect(isEmptyCanvasTarget(note)).toBe(true)
    const graphNode = document.createElement('div')
    graphNode.className = 'react-flow__node react-flow__node-indicator'
    expect(isEmptyCanvasTarget(graphNode)).toBe(false)
  })

  it('a drop near a port that refuses it, or Esc, leaves the wire as it was', () => {
    vi.useFakeTimers()
    const { ctx } = fakeCtx()
    const pane = document.createElement('div')
    pane.className = 'react-flow__pane'
    const up = new MouseEvent('mouseup')
    Object.defineProperty(up, 'target', { value: pane })
    wireOps.onReconnectStart!(ev(), rfEdge('w1'), 'target', ctx)
    wireOps.onReconnectEnd!(up, rfEdge('w1'), 'target', { toHandle: { id: 'in0' }, isValid: false } as unknown as FinalConnectionState, ctx)
    act(() => { vi.advanceTimersByTime(INVALID_FLASH_MS * 2) })
    expect(graph().wires.some(w => w.id === 'w1')).toBe(true)

    // Esc, then a release that would otherwise be a valid drop.
    wireOps.onReconnectStart!(ev(), rfEdge('w1'), 'target', ctx)
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
    act(() => { wireOps.onReconnect!(rfEdge('w1'), { source: 't', target: 'x', sourceHandle: 'out', targetHandle: 'in1' }, ctx) })
    wireOps.onReconnectEnd!(up, rfEdge('w1'), 'target', { toHandle: null, isValid: null } as unknown as FinalConnectionState, ctx)
    act(() => { vi.advanceTimersByTime(INVALID_FLASH_MS * 2) })
    expect(graph().wires.find(w => w.id === 'w1')!.to_port).toBe('in0')
    expect(st().past).toHaveLength(0)
  })
})

// ── Splice by drag ──────────────────────────────────────────────────────────

describe('splice by drag (plugin)', () => {
  it('resting 150 ms on a wire makes it hot; dropping splices, with the move, in one step', () => {
    vi.useFakeTimers()
    const { ctx, updates } = fakeCtx()
    const at = rfNodeAt('r', w1Middle())
    wireOps.onNodeDragStart!(ev(), rfNodeAt('r', { x: 688, y: 230 }), [rfNodeAt('r', { x: 688, y: 230 })], ctx)
    wireOps.onNodeDrag!(ev(), at, [at], ctx)
    expect(updates.some(([, p]) => p.spliceHot === true)).toBe(false)
    act(() => { vi.advanceTimersByTime(SPLICE_HOT_MS + 10) })
    expect(updates).toContainEqual(['w1', { spliceHot: true }])
    act(() => dragStop(at, ctx))
    const g = graph()
    expect(g.wires.some(w => w.id === 'w1')).toBe(false)
    expect(g.wires.filter(w => w.to === 'r')).toEqual([expect.objectContaining({ from: 't', to_port: 'in0' })])
    expect(g.wires.filter(w => w.from === 'r')).toEqual([expect.objectContaining({ to: 'x', to_port: 'in0' })])
    expect(g.nodes.r.params.source).toBe('@close')
    expect(g.nodes.r.position[0]).toBeCloseTo(at.position.x, 6)
    expect(g.nodes.r.position[1]).toBeCloseTo(at.position.y, 6)
    expect(st().past).toHaveLength(1)
    // The hot look is cleared at the drop.
    expect(updates).toContainEqual(['w1', { spliceHot: false }])
  })

  it('does not splice with Cmd held, before 150 ms, or on a multi-node drag', () => {
    vi.useFakeTimers()
    const { ctx } = fakeCtx()
    const at = rfNodeAt('r', w1Middle())

    wireOps.onNodeDragStart!(ev(), at, [at], ctx)
    wireOps.onNodeDrag!(ev(), at, [at], ctx)
    act(() => { vi.advanceTimersByTime(SPLICE_HOT_MS + 10) })
    act(() => dragStop(at, ctx, ev({ metaKey: true })))
    expect(graph().wires.some(w => w.id === 'w1')).toBe(true)

    wireOps.onNodeDragStart!(ev(), at, [at], ctx)
    wireOps.onNodeDrag!(ev(), at, [at], ctx)
    act(() => dragStop(at, ctx))
    expect(graph().wires.some(w => w.id === 'w1')).toBe(true)

    const other = rfNodeAt('s', { x: 900, y: 900 })
    wireOps.onNodeDragStart!(ev(), at, [at, other], ctx)
    wireOps.onNodeDrag!(ev(), at, [at, other], ctx)
    act(() => { vi.advanceTimersByTime(SPLICE_HOT_MS + 10) })
    act(() => dragStop(at, ctx))
    expect(graph().wires.some(w => w.id === 'w1')).toBe(true)
  })

  it('a splice that would close a loop flashes the wire and only moves the node', () => {
    vi.useFakeTimers()
    // t → r → x: dragging x onto t → r would loop through r → x.
    load(spliceIntoWire(makeGraph(), 'r', 'w1'))
    act(() => { st().commit('move r', g => ({ ...g, nodes: { ...g.nodes, r: { ...g.nodes.r, position: [0, 200] } } })) })
    const pastBefore = st().past.length
    const tr = graph().wires.find(w => w.from === 't' && w.to === 'r')!
    const { ctx, updates } = fakeCtx()
    const s = { x: 88, y: 60 }
    const t = { x: 88, y: 200 } // rsi has one port: the middle
    const at = rfNodeAt('x', pointOnWire(s.x, s.y, t.x, t.y, 0.5))
    // x already feeds e and has a free b: it can be dragged.
    wireOps.onNodeDragStart!(ev(), at, [at], ctx)
    wireOps.onNodeDrag!(ev(), at, [at], ctx)
    act(() => { vi.advanceTimersByTime(SPLICE_HOT_MS + 10) })
    act(() => dragStop(at, ctx))
    expect(graph().wires.some(w => w.id === tr.id)).toBe(true)
    expect(updates).toContainEqual([tr.id, { flash: true }])
    expect(st().flash?.text).toBe('That would create a loop')
    expect(st().past.length).toBe(pastBefore + 1) // the move only
  })
})

// ── Insert node into a wire ─────────────────────────────────────────────────

describe('wires.insertNode', () => {
  it('opens the Tab menu at the wire middle; the picked node is spliced in', () => {
    const { ctx } = fakeCtx()
    act(() => st().setSelection({ wireIds: ['w1'] }))
    const opened = vi.fn((req: { screen?: { x: number; y: number }; onCreate?: (id: string, c: CanvasCtx) => void }) => {
      const s = st()
      s.beginBatch('add rsi')
      try {
        s.addNode(node('n', 'rsi', { period: 14, type: 'wilder', source: null, out: '@rsi_2' }, 0, 0))
        req.onCreate?.('n', ctx)
      } finally {
        st().endBatch()
      }
      return true
    })
    ;(ctx as unknown as { openTabMenu: typeof opened }).openTabMenu = opened
    let ran = false
    act(() => { ran = runCommand('wires.insertNode', { canvas: ctx }) })
    expect(ran).toBe(true)
    const mid = w1Middle()
    expect(opened.mock.calls[0][0].screen).toEqual(mid)
    const g = graph()
    expect(g.wires.some(w => w.id === 'w1')).toBe(false)
    expect(g.wires.find(w => w.from === 't' && w.to === 'n')).toBeDefined()
    expect(g.wires.find(w => w.from === 'n' && w.to === 'x' && w.to_port === 'in0')).toBeDefined()
    expect(st().past).toHaveLength(1)
  })

  it('Tab runs it only with exactly one wire selected', () => {
    const cmd = getCommand('wires.insertNode')!
    expect(cmd.keys).toContain('tab')
    act(() => st().setSelection({ wireIds: ['w1'] }))
    expect(cmd.when!(st())).toBe(true)
    act(() => st().setSelection({ wireIds: ['w1', 'w2'] }))
    expect(cmd.when!(st())).toBe(false)
  })
})

// ── Delete with rewire (mounted canvas) ─────────────────────────────────────

function mountCanvas(g: Graph = makeGraph()) {
  load(g)
  const utils = render(
    createElement('div', { className: 'nodebuilder-root', style: { width: 800, height: 600 } },
      createElement(Canvas, { graph: graph() }),
      createElement(ContextMenuHost),
    ),
  )
  const canvas = utils.container.querySelector('.nodebuilder-root .nodebuilder-root') as HTMLElement
  canvas.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  return { ...utils, canvas }
}

/** t → r, r → x (a) and r → y (a): one input, two outputs. */
function fanGraph(): Graph {
  const g = makeGraph()
  return {
    ...g,
    nodes: {
      t: g.nodes.t,
      r: { ...g.nodes.r, params: { ...g.nodes.r.params, source: '@close' } },
      x: { ...g.nodes.x, params: { ...g.nodes.x.params, a: '@rsi' } },
      y: node('y', 'crosses_below', { a: '@rsi', b: null, threshold: null, out: '@xb_2' }, 300, 400),
    },
    wires: [
      { id: 'a1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
      { id: 'a2', from: 'r', to: 'x', from_port: 'out', to_port: 'in0' },
      { id: 'a3', from: 'r', to: 'y', from_port: 'out', to_port: 'in0' },
    ],
  }
}

describe('delete and rewire', () => {
  it('Delete on a node with one input and two outputs rewires both, in one step, and says so', () => {
    mountCanvas(fanGraph())
    act(() => { st().setSelection({ nodeIds: ['r'], primary: 'r' }) })
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    const g = graph()
    expect(g.nodes.r).toBeUndefined()
    expect(g.wires.map(w => [w.from, w.to, w.to_port]).sort()).toEqual([['t', 'x', 'in0'], ['t', 'y', 'in0']])
    // The targets' reads are left as they were.
    expect(g.nodes.x.params.a).toBe('@rsi')
    expect(st().past).toHaveLength(1)
    expect(st().flash?.text).toBe('Deleted r and reconnected 2 wires')
  })

  it('Shift+Delete deletes without rewiring', () => {
    mountCanvas(fanGraph())
    act(() => { st().setSelection({ nodeIds: ['r'], primary: 'r' }) })
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete', shiftKey: true }) })
    expect(graph().nodes.r).toBeUndefined()
    expect(graph().wires).toHaveLength(0)
    expect(st().past).toHaveLength(1)
  })
})

// ── Long-wire fade ──────────────────────────────────────────────────────────

describe('long-wire fade', () => {
  function drawEdge(length: number, selected = false) {
    const data: AttrEdgeData = {
      from: 't', to: 'x', text: '@close', placeholder: false, reads: ['@close'], t: 0.5, dx: 0, hidden: null,
      lowZoom: false, hot: false, diag: null, fromName: 't', toName: 'x', portLabel: 'a',
    }
    const props = {
      id: 'w1', source: 't', target: 'x', sourceX: 0, sourceY: 0, targetX: 0, targetY: length,
      sourcePosition: Position.Bottom, targetPosition: Position.Top, selected, data,
    } as unknown as Parameters<typeof AttrEdge>[0]
    return render(createElement('svg', null, createElement(AttrEdge, props)))
  }
  const stroke = (c: HTMLElement) => c.querySelector('.nb-edge__path')!.getAttribute('stroke')

  it('a 700 wire fades through its own gradient; a 300 wire and a selected 700 wire do not', () => {
    let r = drawEdge(700)
    expect(stroke(r.container)).toBe('url(#nb-fade-w1)')
    const grad = r.container.querySelector('linearGradient#nb-fade-w1')!
    expect(grad.getAttribute('gradientUnits')).toBe('userSpaceOnUse')
    expect(Array.from(grad.querySelectorAll('stop')).map(s => s.getAttribute('offset'))).toEqual(['0', '0.3', '0.7', '1'])
    cleanup()
    r = drawEdge(300)
    expect(stroke(r.container)).toBeNull()
    cleanup()
    r = drawEdge(700, true)
    expect(stroke(r.container)).toBeNull()
    expect(r.container.querySelector('linearGradient')).toBeNull()
  })
})

// ── Context menus ───────────────────────────────────────────────────────────

const NODE_ORDER = [
  'nodes.replace', 'flags.setDisplay', 'flags.toggleBypass', 'view.frameSelection',
  'clipboard.cut', 'clipboard.copy', 'clipboard.paste', 'clipboard.duplicate',
  'wires.deleteRewire', 'edit.delete', 'edit.deleteNoRewire',
]

describe('buildMenu', () => {
  it('lists node rows in S19 order, with key caps from the commands', () => {
    act(() => st().setSelection({ nodeIds: ['s'], primary: 's' }))
    const rows = buildMenu('node', st(), true)
    const ids = rows.filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
    const expected = NODE_ORDER.filter(id => ids.includes(id))
    expect(ids.filter(id => NODE_ORDER.includes(id))).toEqual(expected)
    // One Delete row, then Delete without rewiring, disabled: s has a wire in but none out.
    const del = rows.filter((r): r is MenuItemRow => r.type === 'item' && r.label === 'Delete')
    expect(del).toHaveLength(1)
    const noRewire = rows.find((r): r is MenuItemRow => r.type === 'item' && r.cmd.id === 'edit.deleteNoRewire')!
    expect(noRewire.disabled).toBe(true)
    expect(noRewire.reason).toBe('No wires to reconnect')
    // No separator first, last or doubled.
    expect(rows[0].type).not.toBe('sep')
    expect(rows[rows.length - 1].type).not.toBe('sep')
    expect(rows.some((r, i) => r.type === 'sep' && rows[i + 1]?.type === 'sep')).toBe(false)
    // The unsupported-node row only shows for an unsupported node.
    expect(ids).not.toContain('nodes.replace')
  })

  it('a read-only graph keeps only the rows that do not edit', () => {
    const rows = buildMenu('node', st(), false)
    const ids = rows.filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
    for (const id of ids) {
      const cmd = getCommand(id)!
      const keys = cmd.keys ?? []
      expect(['view.frameSelection', 'clipboard.copy', 'sheet.showData'].includes(id) || keys.some(k => ['f', 'mod+c', 's'].includes(k))).toBe(true)
    }
    expect(buildMenu('wire', st(), false).some(r => r.type === 'item' && r.cmd.id === 'wires.delete')).toBe(false)
  })

  it('formats key caps with the shared platform-aware formatter (UX-15)', () => {
    expect(formatChord('mod+shift+z', true)).toBe('⌘⇧Z')
    expect(formatChord('mod+shift+z', false)).toBe('Ctrl+Shift+Z')
    expect(formatChord('mod+z', false)).toBe(formatKeyChord('mod+z', false))
    expect(formatChord('shift+delete', true)).toBe(formatKeyChord('shift+delete', true))
    expect(formatChord('backspace', true)).toBe('⌫')
    expect(formatChord('f2', true)).toBe('F2')
    expect(formatChord('b', false)).toBe('B')
    expect(formatChord('tab', true)).toBe('Tab')
  })

  it('a read-only menu keeps exactly the readOnlyOk commands (EA-11)', () => {
    act(() => st().setSelection({ nodeIds: ['s'], primary: 's' }))
    for (const kind of ['node', 'wire', 'pane'] as const) {
      for (const r of buildMenu(kind, st(), false)) {
        if (r.type === 'item') expect(r.cmd.readOnlyOk).toBe(true)
      }
    }
    const ids = buildMenu('node', st(), false).filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
    expect(ids).toContain('clipboard.copy')
    expect(ids).toContain('view.frameSelection')
  })

  it('menu layouts name only registered ids; a later command takes its place by key or menuSlot (EA-11)', () => {
    for (const rows of Object.values(MENU_LAYOUTS)) {
      for (const spec of rows) {
        if (typeof spec === 'object' && 'ids' in spec && spec.ids) {
          for (const id of spec.ids) expect(getCommand(id), id).not.toBeNull()
        }
      }
    }
    act(() => st().setSelection({ nodeIds: ['s'], primary: 's' }))
    const undo = registerCommands([
      { id: 'w6.saveAsset', label: 'Save as asset', menu: 'node', menuSlot: 'node.network', run: () => {} },
      { id: 'w4.showData', label: 'Show data', keys: ['s'], menu: 'node', readOnlyOk: true, run: () => {} },
    ])
    try {
      const ids = buildMenu('node', st(), true).filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
      // Show data sits right before Frame; Save as asset before the Delete group.
      expect(ids.indexOf('w4.showData')).toBe(ids.indexOf('view.frameSelection') - 1)
      expect(ids.indexOf('w6.saveAsset')).toBeLessThan(ids.indexOf('edit.deleteNoRewire'))
      expect(ids.indexOf('w6.saveAsset')).toBeGreaterThan(ids.indexOf('clipboard.duplicate'))
      // Read-only: the readOnlyOk one stays, the other goes.
      const ro = buildMenu('node', st(), false).filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
      expect(ro).toContain('w4.showData')
      expect(ro).not.toContain('w6.saveAsset')
    } finally {
      undo()
    }
  })

  it('every menu row runs a registered command', () => {
    for (const kind of ['node', 'wire', 'pane', 'box', 'note'] as const) {
      for (const r of buildMenu(kind, st(), true)) {
        if (r.type === 'item') expect(listCommands()).toContain(r.cmd)
        if (r.type === 'submenu') for (const i of r.items) expect(listCommands()).toContain(i.cmd)
      }
    }
  })
})

describe('context menus on the canvas', () => {
  const nodeEl = (c: HTMLElement, id: string) => c.querySelector(`.react-flow__node[data-id="${id}"]`) as HTMLElement

  it('right-click on a param row opens the param menu; Set to default is one undo step (UX-06)', () => {
    const { canvas } = mountCanvas()
    act(() => { st().updateNodeParams('r', { period: 21 }) })
    const input = screen.getByTestId('nb-param-r-period')
    const label = input.closest('label')!.querySelector('span')!
    let prevented = false
    act(() => {
      const evt = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 40 })
      label.dispatchEvent(evt)
      prevented = evt.defaultPrevented
    })
    expect(prevented).toBe(true)
    expect(getContextMenu()?.kind).toBe('param')
    expect(getContextMenu()?.target).toEqual({ nodeId: 'r', param: 'period' })
    // The node menu did not open instead (no node rows).
    expect(screen.queryByTestId('nb-menu-item-flags.toggleBypass')).toBeNull()
    const past = st().past.length
    act(() => { fireEvent.click(screen.getByTestId('nb-menu-item-params.reset')) })
    expect(st().graph!.nodes.r.params.period).toBe(14)
    expect(st().past.length).toBe(past + 1)
    act(() => { st().undo() })
    expect(st().graph!.nodes.r.params.period).toBe(21)
    // A right-click inside the text field keeps the browser's own menu.
    act(() => { closeContextMenu() })
    act(() => { input.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })) })
    expect(getContextMenu()).toBeNull()
    void canvas
  })

  it('right-click on a node selects only it and opens the node menu; Esc closes and focuses the canvas', () => {
    const { canvas } = mountCanvas()
    act(() => { st().setSelection({ nodeIds: ['t'], primary: 't' }) })
    let prevented = false
    act(() => {
      const evt = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 50, clientY: 60 })
      nodeEl(canvas, 's').dispatchEvent(evt)
      prevented = evt.defaultPrevented
    })
    expect(prevented).toBe(true)
    expect(st().selectedNodeIds).toEqual(['s'])
    const menu = screen.getByTestId('nb-context-menu')
    expect(menu.getAttribute('role')).toBe('menu')
    const rowIds = Array.from(menu.querySelectorAll('[data-testid^="nb-menu-item-"]'))
      .map(el => el.getAttribute('data-testid')!.slice('nb-menu-item-'.length))
    expect(rowIds.filter(id => NODE_ORDER.includes(id))).toEqual(NODE_ORDER.filter(id => rowIds.includes(id)))
    if (getCommand('flags.toggleBypass')) {
      const bypass = screen.getByTestId('nb-menu-item-flags.toggleBypass')
      expect(bypass.querySelector('kbd')?.textContent).toBe(formatChord(getCommand('flags.toggleBypass')!.keys![0]))
      expect(bypass.querySelector('kbd')?.textContent).toBe('B')
    }
    const paste = getCommand('clipboard.paste')
    if (paste && !isCommandEnabled(paste)) {
      const row = screen.getByTestId('nb-menu-item-clipboard.paste')
      expect(row.getAttribute('aria-disabled')).toBe('true')
      expect(row.getAttribute('title')).toBe('Nothing to paste')
    }
    act(() => { fireEvent.keyDown(document.activeElement ?? menu, { key: 'Escape' }) })
    expect(screen.queryByTestId('nb-context-menu')).toBeNull()
    expect(document.activeElement).toBe(canvas)
  })

  it('right-click on a selected node keeps the group; on the pane keeps the selection', () => {
    const { canvas } = mountCanvas()
    act(() => { st().setSelection({ nodeIds: ['t', 's'], primary: 't' }) })
    act(() => { fireEvent.contextMenu(nodeEl(canvas, 's'), { clientX: 10, clientY: 10 }) })
    expect([...st().selectedNodeIds].sort()).toEqual(['s', 't'])
    act(() => closeContextMenu())
    act(() => { fireEvent.contextMenu(canvas.querySelector('.react-flow__pane')!, { clientX: 300, clientY: 300 }) })
    expect(getContextMenu()?.kind).toBe('pane')
    expect([...st().selectedNodeIds].sort()).toEqual(['s', 't'])
  })

  it('an outside press closes the menu and does not reach the canvas', () => {
    const { canvas } = mountCanvas()
    act(() => { st().setSelection({ nodeIds: ['t'], primary: 't' }) })
    act(() => { fireEvent.contextMenu(canvas.querySelector('.react-flow__pane')!, { clientX: 300, clientY: 300 }) })
    expect(screen.getByTestId('nb-context-menu')).toBeInTheDocument()
    const pane = canvas.querySelector('.react-flow__pane') as HTMLElement
    const spy = vi.fn()
    pane.addEventListener('pointerdown', spy)
    act(() => { fireEvent.pointerDown(pane, { button: 0 }) })
    act(() => { fireEvent.click(pane) })
    expect(spy).not.toHaveBeenCalled()
    expect(screen.queryByTestId('nb-context-menu')).toBeNull()
    expect(st().selectedNodeIds).toEqual(['t'])
  })

  it.skipIf(!getCommand('annotations.newNote') || !getCommand('clipboard.paste'))(
    'right-click on the pane, then Down Down Enter, creates a sticky note',
    () => {
      const { canvas } = mountCanvas()
      act(() => { fireEvent.contextMenu(canvas.querySelector('.react-flow__pane')!, { clientX: 300, clientY: 300 }) })
      const menu = screen.getByTestId('nb-context-menu')
      const first = menu.querySelector('[data-row="0"]') as HTMLElement
      act(() => first.focus())
      expect(document.activeElement?.getAttribute('data-testid')).toBe('nb-menu-item-edit.addNode')
      act(() => { fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' }) })
      act(() => { fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' }) })
      expect(document.activeElement?.getAttribute('data-testid')).toBe('nb-menu-item-annotations.newNote')
      act(() => { fireEvent.keyDown(document.activeElement!, { key: 'Enter' }) })
      expect(graph().annotations.notes).toHaveLength(1)
      expect(screen.queryByTestId('nb-context-menu')).toBeNull()
    },
  )

  it('right-click on a wire selects it and opens the wire menu', () => {
    const { ctx } = fakeCtx()
    const e = { preventDefault: vi.fn(), clientX: 5, clientY: 5 } as unknown as ReactMouseEvent
    act(() => { contextMenus.onEdgeContextMenu!(e, rfEdge('w1'), ctx) })
    expect(e.preventDefault).toHaveBeenCalled()
    expect(st().selectedWireIds).toEqual(['w1'])
    expect(getContextMenu()?.kind).toBe('wire')
  })
})
