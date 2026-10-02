/**
 * Network boxes and sticky notes (F435 W3 item 3.E, specs S17 and S18).
 *
 * Store actions and pure ops, the box drag plugin (called the way the
 * canvas calls it: inside one store batch, then the canvas's own move), the
 * commands, and the mounted canvas (box and note rendering, label and note
 * editing through the keys).
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, renderHook, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { createElement, useRef, type ReactNode } from 'react'
import { ReactFlowProvider, useStoreApi, type Node as RFNode } from '@xyflow/react'
import Canvas from '../Canvas'
import { useGlobalKeys } from '../commands/useGlobalKeys'
import { getCommand, listCommands, registerCommand, runCommand, type Command } from '../commands'
import type { CanvasCtx } from '../canvasPlugins'
import { listRfNodeSources } from '../rfMapping'
import { getNodeTypes } from '../nodeTypes'
import { useNodeBuilderStore } from '../store'
import {
  BOX_PADDING,
  boundsOfNodes,
  boxTint,
  boxTintVar,
  newAnnotationId,
  noteColor,
  opMoveAnnotations,
  recomputeMembership,
  smallestBoxAt,
} from '../store/annotations'
import { plugin, createAnnotationNodeMapper, flushLive } from '../plugins/boxDrag'
import { fullyInsideMarquee, getHotBox, useFullMarqueeOnly } from '../nodes/annotationUi'
import type { Graph, NetworkBox } from '../../../api/nodebuilder'
import type { AnnotationCommand } from '../commands/annotations'

// Default node size used when React Flow has not measured a node (rfMapping).
const W = 176
const H = 60

function makeGraph(boxes: NetworkBox[] = []): Graph {
  const node = (id: string, type: string, params: Record<string, string | number>, x: number, y: number) => ({
    id, type, name: id, parent: null, params, position: [x, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d' }, 0, 0),
      r: node('r', 'rsi', { period: 14 }, 0, 150),
      e: node('e', 'entry', {}, 600, 300),
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'r', to: 'e', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes, notes: [] },
  }
}

const BOX: NetworkBox = { id: 'box1', label: 'Regime', color: 'network', rect: [-24, -24, 224, 258], members: ['t', 'r'], parent: null }

function open(graph: Graph = makeGraph()) {
  useNodeBuilderStore.getState().openGraph(graph, { id: null, rev: 0, name: 'test' })
}

const st = () => useNodeBuilderStore.getState()
const g = () => st().graph!

/** A canvas context with just what the plugin and the commands use. */
function fakeCtx(pointer = { x: 0, y: 0 }): CanvasCtx & { setNodesCalls: RFNode[][] } {
  let rfNodes: RFNode[] = []
  const setNodesCalls: RFNode[][] = []
  const ctx = {
    rf: {
      getNode: () => undefined,
      setNodes: (u: RFNode[] | ((c: RFNode[]) => RFNode[])) => {
        rfNodes = Object.values(g().nodes).map(n => ({ id: n.id, position: { x: n.position[0], y: n.position[1] }, data: {} }))
        const next = typeof u === 'function' ? u(rfNodes) : u
        setNodesCalls.push(next)
      },
    },
    store: useNodeBuilderStore,
    pointer: () => pointer,
    pointerOnCanvas: () => true,
    graph: () => g(),
    editable: () => !g().readOnly,
    container: () => null,
    focus: () => {},
    openTabMenu: () => false,
    deleteSelection: () => false,
    setNodesCalls,
  }
  return ctx as unknown as CanvasCtx & { setNodesCalls: RFNode[][] }
}

const rfNode = (id: string, x: number, y: number): RFNode => ({ id, position: { x, y }, data: {} })
const ev = {} as React.MouseEvent

/** What the canvas does on a node drag stop: one batch, plugins, then its own move of graph nodes. */
function dropLikeCanvas(node: RFNode, nodes: RFNode[], ctx: CanvasCtx) {
  st().beginBatch()
  try {
    const handled = plugin.onNodeDragStop!(ev, node, nodes, ctx)
    if (handled !== true) {
      const ids: string[] = []
      const deltas: Array<[number, number]> = []
      for (const n of nodes) {
        const gn = g().nodes[n.id]
        if (!gn) continue
        ids.push(n.id)
        deltas.push([n.position.x - gn.position[0], n.position.y - gn.position[1]])
      }
      if (ids.length) st().moveNodes(ids, deltas)
    }
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

const offs: Array<() => void> = []
afterEach(() => {
  cleanup()
  for (const off of offs.splice(0)) off()
  st().stopAnnotationEdit()
  st().discardEdits()
})

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe('pure helpers', () => {
  it('maps tints and note colors, with fallbacks', () => {
    expect(boxTint('blue')).toBe('network')
    expect(boxTint('weird')).toBe('network')
    expect(boxTint('logic')).toBe('logic')
    expect(boxTintVar('neutral')).toBe('var(--nb-text-muted)')
    expect(boxTintVar('ticker')).toBe('var(--nb-cat-ticker)')
    expect(noteColor('')).toBe('amber')
    expect(noteColor('green')).toBe('green')
  })

  it('new ids never clash with nodes, boxes or notes', () => {
    const graph = makeGraph([{ ...BOX }])
    graph.nodes.note1 = { ...graph.nodes.t, id: 'note1' }
    expect(newAnnotationId(graph, 'box')).toBe('box2')
    expect(newAnnotationId(graph, 'note')).toBe('note2')
  })

  it('a node joins the smallest box that contains its center', () => {
    const big: NetworkBox = { ...BOX, id: 'big', rect: [-100, -100, 1000, 1000], members: [] }
    const small: NetworkBox = { ...BOX, id: 'small', rect: [-10, -10, 300, 300], members: [] }
    expect(smallestBoxAt([big, small], { x: 50, y: 50 })?.id).toBe('small')
    expect(smallestBoxAt([big, small], { x: 500, y: 500 })?.id).toBe('big')
    expect(smallestBoxAt([big, small], { x: 50, y: 50 }, new Set(['small']))?.id).toBe('big')
    const graph = recomputeMembership(makeGraph([big, small]), ['t', 'e'], () => null)
    const [b, s] = graph.annotations.boxes
    expect(s.members).toEqual(['t'])
    expect(b.members).toEqual(['e'])
  })

  it('membership drops members that are no longer graph nodes and keeps unchanged boxes', () => {
    const stale: NetworkBox = { ...BOX, members: ['t', 'gone'] }
    const other: NetworkBox = { ...BOX, id: 'box2', rect: [5000, 5000, 200, 200], members: [] }
    const graph = makeGraph([stale, other])
    const next = recomputeMembership(graph, ['t'], () => null)
    expect(next.annotations.boxes[0].members).toEqual(['t'])
    expect(next.annotations.boxes[1]).toBe(other)
    // Nothing to change: the same graph comes back.
    expect(recomputeMembership(next, ['t'], () => null)).toBe(next)
  })

  it('moving a box moves its members, except those already moved', () => {
    const graph = makeGraph([{ ...BOX }])
    const next = opMoveAnnotations(graph, new Map([['box1', { x: 16, y: -24 }]]), new Set(['r']))
    expect(next.annotations.boxes[0].rect).toEqual([16, -24, 224, 258])
    expect(next.nodes.t.position).toEqual([40, 0])
    expect(next.nodes.r.position).toEqual([0, 150])
    expect(next.nodes.e).toBe(graph.nodes.e)
  })
})

// ---------------------------------------------------------------------------
// Store actions: each one undo step
// ---------------------------------------------------------------------------

describe('store actions', () => {
  it('add, update and remove are each one undo step, and undo restores the graph', () => {
    open()
    const g0 = g()
    const id = st().addBox({ label: 'A', rect: [0, 0, 320, 200] })!
    expect(id).toBe('box1')
    expect(st().past).toHaveLength(1)
    st().updateBox(id, { label: 'B', color: 'logic' })
    expect(st().past).toHaveLength(2)
    expect(g().annotations.boxes[0]).toMatchObject({ label: 'B', color: 'logic' })
    const nid = st().addNote({ text: 'hi' })!
    st().updateNote(nid, { text: 'hello' })
    expect(st().past).toHaveLength(4)
    // An update that changes nothing records nothing.
    st().updateNote(nid, { text: 'hello' })
    expect(st().past).toHaveLength(4)
    st().removeAnnotations([id, nid])
    expect(g().annotations).toEqual({ boxes: [], notes: [] })
    expect(g().nodes).toBe(g0.nodes)
    for (let i = 0; i < 5; i++) st().undo()
    expect(g()).toBe(g0)
    expect(st().dirty).toBe(false)
  })

  it('caps label and text lengths', () => {
    open()
    const id = st().addBox()!
    st().updateBox(id, { label: 'x'.repeat(60) })
    expect(g().annotations.boxes[0].label).toHaveLength(40)
    const nid = st().addNote({ text: 'y'.repeat(2500) })!
    expect(g().annotations.notes.find(n => n.id === nid)!.text).toHaveLength(2000)
  })

  it('does nothing on a read-only graph', () => {
    open()
    useNodeBuilderStore.setState({ graph: { ...g(), readOnly: true } })
    expect(st().addBox()).toBeNull()
    expect(st().addNote()).toBeNull()
    expect(st().past).toHaveLength(0)
  })

  it('resize updates the rect and membership in one step', () => {
    open(makeGraph([{ ...BOX, members: ['t'], rect: [-24, -24, 224, 108] }]))
    st().resizeBox('box1', [-24, -24, 224, 258])
    expect(g().annotations.boxes[0].members.sort()).toEqual(['r', 't'])
    expect(st().past).toHaveLength(1)
    st().resizeBox('box1', [-24, 120, 224, 120])
    expect(g().annotations.boxes[0].members).toEqual(['r'])
    st().undo()
    expect(g().annotations.boxes[0].members.sort()).toEqual(['r', 't'])
  })

  it('fit to contents wraps the members plus 24px', () => {
    open(makeGraph([{ ...BOX, rect: [-500, -500, 2000, 2000] }]))
    st().fitBox('box1')
    expect(g().annotations.boxes[0].rect).toEqual(boundsOfNodes(g(), ['t', 'r'], () => null))
    expect(g().annotations.boxes[0].rect).toEqual([-BOX_PADDING, -BOX_PADDING, W + 2 * BOX_PADDING, 150 + H + 2 * BOX_PADDING])
  })

  it('the editing id is UI state, not history', () => {
    open()
    st().startAnnotationEdit('note1')
    expect(st().editingAnnotationId).toBe('note1')
    expect(st().past).toHaveLength(0)
    expect(st().dirty).toBe(false)
    st().stopAnnotationEdit()
    expect(st().editingAnnotationId).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Plugin: drag, membership, delete
// ---------------------------------------------------------------------------

describe('box drag plugin', () => {
  it('dragging the box by (40, 0) moves both members by (40, 0) in one history step; undo moves all back', () => {
    open(makeGraph([{ ...BOX }]))
    const g0 = g()
    const ctx = fakeCtx()
    const start = rfNode('box1', -24, -24)
    plugin.onNodeDragStart!(ev, start, [start], ctx)
    const mid = rfNode('box1', -4, -24)
    plugin.onNodeDrag!(ev, mid, [mid], ctx)
    flushLive()
    // Members follow on screen during the drag (no commit yet).
    const live = ctx.setNodesCalls.at(-1)!
    expect(live.find(n => n.id === 't')!.position).toEqual({ x: 20, y: 0 })
    expect(live.find(n => n.id === 'e')!.position).toEqual({ x: 600, y: 300 })
    expect(st().past).toHaveLength(0)
    const end = rfNode('box1', 16, -24)
    dropLikeCanvas(end, [end], ctx)
    expect(g().annotations.boxes[0].rect).toEqual([16, -24, 224, 258])
    expect(g().nodes.t.position).toEqual([40, 0])
    expect(g().nodes.r.position).toEqual([40, 150])
    expect(g().nodes.e.position).toEqual([600, 300])
    expect(st().past).toHaveLength(1)
    st().undo()
    expect(g()).toBe(g0)
  })

  it('a member dragged together with its box is moved once, not twice', () => {
    open(makeGraph([{ ...BOX }]))
    const ctx = fakeCtx()
    const nodes = [rfNode('box1', -24, -24), rfNode('t', 0, 0)]
    plugin.onSelectionDragStart!(ev, nodes, ctx)
    const moved = [rfNode('box1', 16, -24), rfNode('t', 40, 0)]
    st().beginBatch()
    plugin.onSelectionDragStop!(ev, moved, ctx)
    st().moveNodes(['t'], [[40, 0]])
    st().endBatch()
    expect(g().nodes.t.position).toEqual([40, 0])
    expect(g().nodes.r.position).toEqual([40, 150])
    expect(st().past).toHaveLength(1)
  })

  it('dropping a third node inside the box adds it to members; dragging it out removes it', () => {
    open(makeGraph([{ ...BOX }]))
    const ctx = fakeCtx()
    const inside = rfNode('e', 0, 60)
    plugin.onNodeDragStart!(ev, rfNode('e', 600, 300), [rfNode('e', 600, 300)], ctx)
    plugin.onNodeDrag!(ev, inside, [inside], ctx)
    flushLive()
    // The "will join" signal while dragging.
    expect(getHotBox()).toBe('box1')
    dropLikeCanvas(inside, [inside], ctx)
    expect(getHotBox()).toBeNull()
    expect(g().annotations.boxes[0].members).toEqual(['t', 'r', 'e'])
    expect(g().nodes.e.position).toEqual([0, 60])
    // Node move and membership are one history step.
    expect(st().past).toHaveLength(1)

    const outside = rfNode('e', 900, 900)
    plugin.onNodeDrag!(ev, outside, [outside], ctx)
    flushLive()
    expect(getHotBox()).toBeNull()
    dropLikeCanvas(outside, [outside], ctx)
    expect(g().annotations.boxes[0].members).toEqual(['t', 'r'])
    expect(st().past).toHaveLength(2)
  })

  it('dragging a note commits its new place in one step', () => {
    const graph = makeGraph()
    graph.annotations.notes = [{ id: 'note1', text: 'x', rect: [10, 10, 200, 88], color: 'amber', parent: null }]
    open(graph)
    const ctx = fakeCtx()
    const end = rfNode('note1', 50, 70)
    plugin.onNodeDragStart!(ev, rfNode('note1', 10, 10), [rfNode('note1', 10, 10)], ctx)
    dropLikeCanvas(end, [end], ctx)
    expect(g().annotations.notes[0].rect).toEqual([50, 70, 200, 88])
    expect(st().past).toHaveLength(1)
  })

  it('Delete removes the selected box only; the nodes stay', () => {
    open(makeGraph([{ ...BOX }]))
    const ctx = fakeCtx()
    const handled = plugin.onDeleteSelection!({ nodeIds: [], wireIds: [], otherIds: ['box1'], rewire: true }, ctx)
    expect(handled).toBe(true)
    expect(g().annotations.boxes).toEqual([])
    expect(Object.keys(g().nodes).sort()).toEqual(['e', 'r', 't'])
    expect(st().past).toHaveLength(1)
    expect(plugin.onDeleteSelection!({ nodeIds: ['t'], wireIds: [], otherIds: [], rewire: true }, ctx)).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Node source
// ---------------------------------------------------------------------------

describe('node source', () => {
  it('is registered and draws boxes at z -2 and notes at z -1 under graph nodes', () => {
    expect(listRfNodeSources().some(s => s.id === 'annotations' && s.order !== 'after')).toBe(true)
    expect(Object.keys(getNodeTypes())).toEqual(expect.arrayContaining(['nbBox', 'nbNote']))
    const map = createAnnotationNodeMapper()
    const graph = makeGraph([{ ...BOX, members: ['t', 'gone'] }])
    graph.annotations.notes = [{ id: 'note1', text: 'x', rect: [10, 20, 200, 88], color: 'amber', parent: null }]
    const [box, note] = map(graph, true)
    expect(box).toMatchObject({ id: 'box1', type: 'nbBox', zIndex: -2, width: 224, height: 258, draggable: true, position: { x: -24, y: -24 } })
    expect(box.data).toMatchObject({ memberCount: 1, editable: true })
    expect(note).toMatchObject({ id: 'note1', type: 'nbNote', zIndex: -1, width: 200, height: 88 })
    // Same objects in: same nodes out.
    const again = map(graph, true)
    expect(again[0]).toBe(box)
    expect(again[1]).toBe(note)
    // Read-only: not draggable.
    expect(map(graph, false)[0].draggable).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

describe('commands', () => {
  it('registers the spec ids with their keys and menus', () => {
    const ids = listCommands().map(c => c.id)
    expect(ids).toEqual(expect.arrayContaining([
      'annotations.newBox', 'annotations.newNote', 'annotations.fitBox', 'annotations.deleteBox',
      'annotations.renameBox', 'annotations.editNote', 'annotations.deleteNote', 'annotations.edit',
    ]))
    expect(getCommand('annotations.newBox')!.keys).toEqual(['shift+b'])
    expect(getCommand('annotations.newNote')!.keys).toEqual(['shift+n'])
    expect(getCommand('annotations.edit')!.keys).toEqual(['f2'])
    const tints = listCommands().filter(c => (c as AnnotationCommand).submenu === 'Tint')
    expect(tints).toHaveLength(8)
    expect(tints.every(c => c.menu === 'box')).toBe(true)
    const colors = listCommands().filter(c => (c as AnnotationCommand).submenu === 'Color')
    expect(colors.map(c => c.label)).toEqual(['Amber', 'Blue', 'Green', 'Grey'])
  })

  it('Shift+B with two selected nodes boxes them with a 24px margin', () => {
    open()
    st().setSelection({ nodeIds: ['t', 'r'] })
    expect(runCommand('annotations.newBox', { canvas: fakeCtx() })).toBe(true)
    const box = g().annotations.boxes[0]
    expect(box.members).toEqual(['t', 'r'])
    expect(box.rect).toEqual([-24, -24, W + 48, 150 + H + 48])
    expect(box.color).toBe('network')
    expect(st().selectedAnnotationIds).toEqual([box.id])
    expect(st().past).toHaveLength(1)
  })

  it('Shift+B with nothing selected makes an empty 320x200 box at the cursor', () => {
    open()
    runCommand('annotations.newBox', { canvas: fakeCtx({ x: 300.4, y: 500 }) })
    expect(g().annotations.boxes[0]).toMatchObject({ rect: [300, 500, 320, 200], members: [] })
  })

  it('Shift+N makes a note at the cursor in edit mode', () => {
    open()
    runCommand('annotations.newNote', { canvas: fakeCtx({ x: 40, y: 80 }) })
    const note = g().annotations.notes[0]
    expect(note).toMatchObject({ rect: [40, 80, 200, 88], color: 'amber', text: '' })
    expect(st().editingAnnotationId).toBe(note.id)
    expect(st().selectedAnnotationIds).toEqual([note.id])
  })

  it('box menu rows act on the selected box: tint, fit, rename, delete', () => {
    open(makeGraph([{ ...BOX, rect: [-500, -500, 2000, 2000] }]))
    const tint = getCommand('annotations.tintBox.logic')!
    // Disabled with no box selected.
    expect(runCommand('annotations.tintBox.logic')).toBe(false)
    expect(tint.disabledReason!(st())).toBe('Select a box first')
    st().setSelection({ annotationIds: ['box1'] })
    expect(tint.checked!(st())).toBe(false)
    expect(runCommand('annotations.tintBox.logic', { canvas: null })).toBe(true)
    expect(g().annotations.boxes[0].color).toBe('logic')
    expect(tint.checked!(st())).toBe(true)
    runCommand('annotations.fitBox', { canvas: null })
    expect(g().annotations.boxes[0].rect).toEqual([-24, -24, W + 48, 150 + H + 48])
    runCommand('annotations.renameBox', { canvas: null })
    expect(st().editingAnnotationId).toBe('box1')
    st().stopAnnotationEdit()
    runCommand('annotations.deleteBox', { canvas: null })
    expect(g().annotations.boxes).toEqual([])
    expect(Object.keys(g().nodes)).toHaveLength(3)
    expect(st().past).toHaveLength(3)
  })

  it('note color and delete act on the selected note; F2 edits it but leaves a selected node alone', () => {
    const graph = makeGraph()
    graph.annotations.notes = [{ id: 'note1', text: 'x', rect: [0, 0, 200, 88], color: 'amber', parent: null }]
    open(graph)
    st().setSelection({ annotationIds: ['note1'] })
    runCommand('annotations.noteColor.blue', { canvas: null })
    expect(g().annotations.notes[0].color).toBe('blue')
    expect(runCommand('annotations.edit', { canvas: null })).toBe(true)
    expect(st().editingAnnotationId).toBe('note1')
    st().stopAnnotationEdit()
    st().setSelection({ nodeIds: ['t'], annotationIds: ['note1'] })
    expect(runCommand('annotations.edit', { canvas: null })).toBe(false)
    st().setSelection({ annotationIds: ['note1'] })
    runCommand('annotations.deleteNote', { canvas: null })
    expect(g().annotations.notes).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Mounted canvas
// ---------------------------------------------------------------------------

function BuilderRoot({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useGlobalKeys(ref)
  // The ref is only attached here, not read (the JSX form of this is fine).
  // eslint-disable-next-line react-hooks/refs
  return createElement('div', { ref, className: 'nodebuilder-root', style: { width: 800, height: 600 } }, children)
}

function LiveCanvas() {
  const graph = useNodeBuilderStore(s => s.graph)
  return graph ? createElement(Canvas, { graph }) : null
}

function mount(graph: Graph) {
  open(graph)
  // A read-only graph is drawn from the prop (the store holds an editable copy).
  const canvas = graph.readOnly ? createElement(Canvas, { graph }) : createElement(LiveCanvas)
  const utils = render(createElement(BuilderRoot, null, canvas))
  for (const el of utils.container.querySelectorAll<HTMLElement>('.nodebuilder-root')) {
    el.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  }
  return utils
}

describe('mounted canvas', () => {
  it('draws a box with its tab, grips and group label, and a note', () => {
    const graph = makeGraph([{ ...BOX }])
    graph.annotations.notes = [{ id: 'note1', text: 'pair trade', rect: [400, 0, 200, 88], color: 'green', parent: null }]
    const { container } = mount(graph)
    const box = screen.getByTestId('nb-box-box1')
    expect(box).toHaveAttribute('role', 'group')
    expect(box).toHaveAttribute('aria-label', 'Network box Regime, 2 nodes')
    expect(screen.getByTestId('nb-box-label-box1')).toHaveTextContent('Regime')
    for (const corner of ['top-left', 'top-right', 'bottom-left', 'bottom-right']) {
      expect(screen.getByTestId(`nb-box-grip-${corner}`)).toBeInTheDocument()
    }
    const note = screen.getByTestId('nb-note-note1')
    expect(note).toHaveAttribute('role', 'note')
    expect(note).toHaveAttribute('aria-label', 'pair trade')
    expect(note.className).toContain('nb-note--green')
    // Boxes and notes come before the graph nodes in the DOM.
    const ids = [...container.querySelectorAll('.react-flow__node')].map(n => n.getAttribute('data-id'))
    expect(ids.indexOf('box1')).toBeLessThan(ids.indexOf('t'))
  })

  it('a read-only graph shows boxes with no grips and no editing', () => {
    mount({ ...makeGraph([{ ...BOX, label: '' }]), readOnly: true })
    expect(screen.getByTestId('nb-box-label-box1')).toHaveTextContent('BOX')
    expect(screen.queryByTestId('nb-box-grip-top-left')).toBeNull()
    fireEvent.doubleClick(screen.getByTestId('nb-box-label-box1'))
    expect(screen.queryByLabelText('Box label')).toBeNull()
  })

  it('double-click the label to rename: Enter keeps it, Esc puts the old one back', () => {
    mount(makeGraph([{ ...BOX }]))
    fireEvent.doubleClick(screen.getByTestId('nb-box-label-box1'))
    let input = screen.getByLabelText('Box label') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Trend filter' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(g().annotations.boxes[0].label).toBe('Trend filter')
    expect(st().past).toHaveLength(1)
    expect(screen.queryByLabelText('Box label')).toBeNull()
    fireEvent.doubleClick(screen.getByTestId('nb-box-label-box1'))
    input = screen.getByLabelText('Box label') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(g().annotations.boxes[0].label).toBe('Trend filter')
    expect(st().past).toHaveLength(1)
  })

  it('Shift+N then typing hello and Esc is ONE history step with the text (S18, UX-18)', () => {
    mount(makeGraph())
    const g0 = g()
    act(() => { fireEvent.keyDown(document.body, { key: 'N', shiftKey: true }) })
    const textarea = screen.getByTestId('nb-note-textarea') as HTMLTextAreaElement
    expect(textarea).toHaveAttribute('aria-label', 'Sticky note text')
    expect(st().past.length).toBe(1)
    fireEvent.change(textarea, { target: { value: 'hello' } })
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(g().annotations.notes[0].text).toBe('hello')
    expect(st().past.length).toBe(1)
    expect(screen.queryByTestId('nb-note-textarea')).toBeNull()
    expect(st().editingAnnotationId).toBeNull()
    // One undo takes the note and its text back together.
    act(() => { st().undo() })
    expect(g()).toBe(g0)
    // A later edit of the same note is its own step.
    act(() => { st().redo() })
    const id = g().annotations.notes[0].id
    act(() => { st().updateNote(id, { text: 'bye' }, 'edit note', { coalesce: `note-create:${id}`, windowMs: Infinity, last: true }) })
    expect(st().past.length).toBe(2)
  })

  it('Shift+N then Esc with no text leaves no note and no history step (UX-18)', () => {
    mount(makeGraph())
    const g0 = g()
    act(() => { fireEvent.keyDown(document.body, { key: 'N', shiftKey: true }) })
    const textarea = screen.getByTestId('nb-note-textarea') as HTMLTextAreaElement
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(g()).toBe(g0)
    expect(st().past.length).toBe(0)
    expect(st().canRedo).toBe(false)
  })

  it('while editing a note, b types instead of running the b command', () => {
    const run = vi.fn()
    const cmd: Command = { id: 'test.b', label: 'B test', keys: ['b'], run }
    offs.push(registerCommand(cmd))
    const graph = makeGraph()
    graph.annotations.notes = [{ id: 'note1', text: '', rect: [400, 0, 200, 88], color: 'amber', parent: null }]
    mount(graph)
    // The command works on the canvas.
    act(() => { fireEvent.keyDown(document.body, { key: 'b' }) })
    expect(run).toHaveBeenCalledTimes(1)
    fireEvent.doubleClick(screen.getByTestId('nb-note-note1'))
    const textarea = screen.getByTestId('nb-note-textarea')
    fireEvent.keyDown(textarea, { key: 'b' })
    fireEvent.keyDown(textarea, { key: 'Delete' })
    expect(run).toHaveBeenCalledTimes(1)
    expect(g().annotations.notes).toHaveLength(1)
    // Blur keeps the text (one commit).
    fireEvent.change(textarea, { target: { value: 'b' } })
    fireEvent.blur(textarea)
    expect(g().annotations.notes[0].text).toBe('b')
    expect(st().past).toHaveLength(1)
  })

  it('Shift+B on the canvas boxes the selected nodes, and Delete removes the box but keeps them', () => {
    mount(makeGraph())
    act(() => { st().setSelection({ nodeIds: ['t', 'r'] }) })
    act(() => { fireEvent.keyDown(document.body, { key: 'B', shiftKey: true }) })
    expect(g().annotations.boxes).toHaveLength(1)
    expect(g().annotations.boxes[0].members).toEqual(['t', 'r'])
    expect(screen.getByTestId('nb-box-box1')).toBeInTheDocument()
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    expect(g().annotations.boxes).toHaveLength(0)
    expect(Object.keys(g().nodes)).toHaveLength(3)
    expect(st().past).toHaveLength(2)
  })
})

// ---------------------------------------------------------------------------
// Marquee: boxes and notes only when fully inside
// ---------------------------------------------------------------------------

describe('marquee', () => {
  it('fullyInsideMarquee converts pane pixels to flow units', () => {
    const rect = [100, 100, 200, 100] as const
    expect(fullyInsideMarquee(rect, { x: 0, y: 0, width: 400, height: 400 }, [0, 0, 1])).toBe(true)
    expect(fullyInsideMarquee(rect, { x: 0, y: 0, width: 250, height: 400 }, [0, 0, 1])).toBe(false)
    // Zoom 0.5, panned by (10, 10): flow (100,100)-(300,200) is pane (60,60)-(160,110).
    expect(fullyInsideMarquee(rect, { x: 50, y: 50, width: 120, height: 70 }, [10, 10, 0.5])).toBe(true)
    expect(fullyInsideMarquee(rect, { x: 70, y: 50, width: 120, height: 70 }, [10, 10, 0.5])).toBe(false)
  })

  it('drops a box the marquee only touched, keeps one fully inside or selected before', () => {
    open(makeGraph([{ ...BOX }]))
    const wrapper = ({ children }: { children: ReactNode }) => createElement(ReactFlowProvider, null, children)
    const run = (marquee: { x: number; y: number; width: number; height: number }, selectedBefore: boolean) => {
      st().setSelection({ annotationIds: selectedBefore ? ['box1'] : [] })
      const { result, rerender, unmount } = renderHook(
        ({ selected }) => { useFullMarqueeOnly('box1', BOX.rect, selected); return useStoreApi() },
        { wrapper, initialProps: { selected: selectedBefore } },
      )
      act(() => { result.current.setState({ userSelectionActive: true, userSelectionRect: { ...marquee, startX: 0, startY: 0 }, transform: [0, 0, 1] }) })
      // React Flow's marquee selects the box; the canvas mirrors it.
      act(() => { st().mirrorSelection({ annotationIds: ['box1'] }) })
      rerender({ selected: true })
      act(() => { result.current.setState({ userSelectionActive: false, userSelectionRect: null }) })
      const kept = st().selectedAnnotationIds.includes('box1')
      unmount()
      return kept
    }
    expect(run({ x: -100, y: -100, width: 150, height: 150 }, false)).toBe(false)
    expect(run({ x: -100, y: -100, width: 1000, height: 1000 }, false)).toBe(true)
    expect(run({ x: -100, y: -100, width: 150, height: 150 }, true)).toBe(true)
  })
})
