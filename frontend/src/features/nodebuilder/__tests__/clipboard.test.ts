/**
 * Clipboard (F435 W3 item 3.C): copy, cut, paste at the cursor, duplicate
 * and Alt-drag duplicate. Ids are remapped, names and written attributes
 * made unique, internal wires kept with their reads pointed at the new
 * names; a paste is one undo step and never lands in a read-only graph.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import type { MouseEvent as ReactMouseEvent } from 'react'
import type { Node as RFNode } from '@xyflow/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { copyFromGraph, pastePayload, remapNodeIds, swapIdsForDrag } from '../clipboard'
import { DUPLICATE_OFFSET } from '../store/clipboard'
import { getCommand, listCommands, type Command, type CommandCtx } from '../commands'
import type { CanvasCtx } from '../canvasPlugins'
import { ReadOnlyGraphError } from '../operations'
import { plugin as altDrag, _altDragOpen } from '../plugins/altDragDuplicate'

const s = () => useNodeBuilderStore.getState()

function node(id: string, type: string, name: string, x: number, y: number, params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name, parent: null, params, position: [x, y], display: false, bypass: false }
}

/** ticker -> rsi -> rising -> entry, plus a box around rsi and rising and a note. */
function graph(): Graph {
  const g = emptyGraph()
  g.nodes = {
    t: node('t', 'ticker', 'ticker', 0, 0),
    r: node('r', 'rsi', 'rsi', 100, 200, { period: 14, source: '@close', out: '@rsi' }),
    x: { ...node('x', 'rising', 'rising', 140, 320, { a: '@rsi', out: '@rising' }), display: true, bypass: true },
    e: node('e', 'entry', 'entry', 140, 440),
  }
  g.wires = [
    { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
    { id: 'w2', from: 'r', to: 'x', from_port: 'out', to_port: 'in0' },
    { id: 'w3', from: 'x', to: 'e', from_port: 'out', to_port: 'in0' },
  ]
  g.annotations = {
    boxes: [{ id: 'b1', label: 'signal', color: 'network', rect: [80, 180, 200, 200], members: ['r', 'x'], parent: null }],
    notes: [{ id: 'n1', text: 'hello', rect: [400, 50, 200, 88], color: 'amber', parent: null }],
  }
  return g
}

function fakeCanvas(over: Partial<CanvasCtx> = {}): CanvasCtx {
  return {
    rf: {} as CanvasCtx['rf'],
    store: useNodeBuilderStore,
    graphPosition: n => [n.position.x, n.position.y],
    pointer: () => ({ x: 600, y: 700 }),
    pointerOnCanvas: () => true,
    graph: () => s().graph!,
    editable: () => true,
    container: () => null,
    focus: () => {},
    openTabMenu: () => false,
    deleteSelection: () => false,
    ...over,
  }
}

function cmd(id: string): Command {
  const c = getCommand(id)
  if (!c) throw new Error(`no command ${id}`)
  return c
}

function runCmd(id: string, canvas: CanvasCtx | null, event: KeyboardEvent | null = null) {
  const ctx: CommandCtx = { canvas, store: useNodeBuilderStore, event }
  return cmd(id).run(ctx)
}

function rf(id: string, x: number, y: number): RFNode {
  return { id, position: { x, y }, data: {} }
}

/** openGraph always makes a graph editable; put a read-only one in directly. */
function setReadOnly() {
  useNodeBuilderStore.setState({ graph: { ...graph(), readOnly: true }, past: [], future: [] })
}

const ALT = { altKey: true } as unknown as ReactMouseEvent
const PLAIN = { altKey: false } as unknown as ReactMouseEvent

beforeEach(() => {
  s().openGraph(graph(), { id: null, rev: 0, name: 'test' })
  useNodeBuilderStore.setState({ clipboard: null, flash: null })
})

afterEach(() => {
  // Never leave a batch open for the next test.
  while (s().batch) s().endBatch()
  vi.useRealTimers()
})

describe('copyFromGraph', () => {
  it('copies the nodes and only the wires between them', () => {
    const p = copyFromGraph(graph(), { nodeIds: ['r', 'x'] })!
    expect(p.nodes.map(n => n.id)).toEqual(['r', 'x'])
    expect(p.wires.map(w => w.id)).toEqual(['w2'])
    expect(p.origin).toEqual([100, 200])
    expect(p.boxes).toEqual([])
  })

  it('a selected box brings its members; notes come as selected', () => {
    const p = copyFromGraph(graph(), { annotationIds: ['b1', 'n1'] })!
    expect(p.nodes.map(n => n.id)).toEqual(['r', 'x'])
    expect(p.boxes.map(b => b.id)).toEqual(['b1'])
    expect(p.notes.map(n => n.id)).toEqual(['n1'])
    expect(p.origin).toEqual([80, 50])
  })

  it('a network node brings the nodes inside it', () => {
    const g = graph()
    g.nodes.sub = node('sub', 'subnet', 'sub', 0, 0)
    g.nodes.inner = { ...node('inner', 'rsi', 'rsi', 5, 5), parent: 'sub' }
    const p = copyFromGraph(g, { nodeIds: ['sub'] })!
    expect(p.nodes.map(n => n.id).sort()).toEqual(['inner', 'sub'])
  })

  it('returns null when nothing selected exists', () => {
    expect(copyFromGraph(graph(), { nodeIds: ['nope'] })).toBeNull()
  })

  it('is a deep copy', () => {
    const g = graph()
    const p = copyFromGraph(g, { nodeIds: ['r'] })!
    p.nodes[0].params.period = 99
    expect(g.nodes.r.params.period).toBe(14)
  })
})

describe('pastePayload', () => {
  it('remaps ids, uniques names and writes, keeps internal wires and remaps their reads', () => {
    const g = graph()
    const p = copyFromGraph(g, { nodeIds: ['r', 'x'] })!
    const { graph: out, nodeIds, wireIds, idMap } = pastePayload(g, p, { at: { x: 500, y: 500 } })
    expect(nodeIds).toHaveLength(2)
    for (const id of nodeIds) expect(id in g.nodes).toBe(false)
    const r2 = out.nodes[idMap.get('r')!]
    const x2 = out.nodes[idMap.get('x')!]
    // Names: Houdini style among siblings.
    expect(r2.name).toBe('rsi1')
    expect(x2.name).toBe('rising1')
    // Writes unique in the graph; the pasted reader follows the rename.
    expect(r2.params.out).toBe('@rsi_2')
    expect(x2.params.out).toBe('@rising_2')
    expect(x2.params.a).toBe('@rsi_2')
    // A read from outside the copy is left alone.
    expect(r2.params.source).toBe('@close')
    // Only the internal wire, re-pointed at the copies, same port.
    expect(wireIds).toHaveLength(1)
    const w = out.wires.find(w => w.id === wireIds[0])!
    expect(w).toMatchObject({ from: r2.id, to: x2.id, from_port: 'out', to_port: 'in0' })
    expect(out.wires).toHaveLength(4)
    // Placed with the copy's top-left at the cursor.
    expect(r2.position).toEqual([500, 500])
    expect(x2.position).toEqual([540, 620])
    // Flags: bypass kept, display never copied (one per network).
    expect(x2.bypass).toBe(true)
    expect(x2.display).toBe(false)
    // The original is untouched.
    expect(out.nodes.x).toBe(g.nodes.x)
  })

  it('keeps names and writes when they are free (paste into another graph)', () => {
    const p = copyFromGraph(graph(), { nodeIds: ['r', 'x'] })!
    const other = emptyGraph()
    const { graph: out, nodeIds } = pastePayload(other, p, { offset: [0, 0] })
    expect(nodeIds.map(id => out.nodes[id].name)).toEqual(['rsi', 'rising'])
    expect(out.nodes[nodeIds[1]].params.a).toBe('@rsi')
  })

  it('a copied reader without its writer keeps its read', () => {
    const g = graph()
    const p = copyFromGraph(g, { nodeIds: ['x'] })!
    const { graph: out, nodeIds } = pastePayload(g, p, { offset: [24, 24] })
    expect(out.nodes[nodeIds[0]].params.a).toBe('@rsi')
  })

  it('pastes boxes with remapped members and new ids, notes moved by the same amount', () => {
    const g = graph()
    const p = copyFromGraph(g, { annotationIds: ['b1', 'n1'] })!
    const { graph: out, annotationIds, idMap } = pastePayload(g, p, { offset: [10, 20] })
    expect(annotationIds).toHaveLength(2)
    const box = out.annotations.boxes.find(b => b.id === annotationIds[0])!
    expect(box.id).not.toBe('b1')
    expect(box.members).toEqual([idMap.get('r'), idMap.get('x')])
    expect(box.rect).toEqual([90, 200, 200, 200])
    const note = out.annotations.notes.find(n => n.id === annotationIds[1])!
    expect(note.text).toBe('hello')
    expect(note.rect).toEqual([410, 70, 200, 88])
    // React Flow shares one id space: no pasted id clashes with anything.
    expect(new Set([...Object.keys(out.nodes), ...annotationIds]).size).toBe(Object.keys(out.nodes).length + 2)
  })

  it('refuses a read-only graph', () => {
    const g = { ...graph(), readOnly: true }
    const p = copyFromGraph(g, { nodeIds: ['r'] })!
    expect(() => pastePayload(g, p)).toThrow(ReadOnlyGraphError)
  })
})

describe('store: copy, paste, duplicate', () => {
  it('paste is one undo step and undo leaves the graph deep-equal to before', () => {
    s().setSelection({ nodeIds: ['r', 'x'] })
    s().copySelection()
    const before = s().graph
    const snapshot = JSON.parse(JSON.stringify(before))
    const pastLen = s().past.length
    const ids = s().pasteClipboard({ x: 300, y: 300 })!
    expect(ids).toHaveLength(2)
    expect(s().past.length).toBe(pastLen + 1)
    expect(Object.keys(s().graph!.nodes)).toHaveLength(6)
    // The pasted items become the selection.
    expect(s().selectedNodeIds).toEqual(ids)
    expect(s().selectedNodeId).toBe(ids[0])
    s().undo()
    expect(s().graph).toEqual(snapshot)
    expect(s().graph).toBe(before)
  })

  it('the clipboard is not history and survives opening another graph', () => {
    s().setSelection({ nodeIds: ['r'] })
    s().copySelection()
    expect(s().dirty).toBe(false)
    expect(s().past).toHaveLength(0)
    s().openGraph(emptyGraph(), { id: null, rev: 0, name: 'other' })
    expect(s().clipboard?.nodes).toHaveLength(1)
    const ids = s().pasteClipboard({ x: 0, y: 0 })!
    expect(s().graph!.nodes[ids[0]].name).toBe('rsi')
  })

  it('never pastes into a read-only graph', () => {
    s().setSelection({ nodeIds: ['r'] })
    s().copySelection()
    setReadOnly()
    const before = s().graph
    expect(s().pasteClipboard({ x: 0, y: 0 })).toBeNull()
    expect(s().duplicateSelection()).toBeNull()
    expect(s().graph).toBe(before)
    expect(s().past).toHaveLength(0)
  })

  it('duplicate moves the copy by (24, 24) and leaves the clipboard alone', () => {
    s().setSelection({ nodeIds: ['r'] })
    const ids = s().duplicateSelection()!
    expect(DUPLICATE_OFFSET).toEqual([24, 24])
    expect(s().graph!.nodes[ids[0]].position).toEqual([124, 224])
    expect(s().clipboard).toBeNull()
    expect(s().selectedNodeIds).toEqual(ids)
  })

  it('pasting twice uniques again', () => {
    s().setSelection({ nodeIds: ['r'] })
    s().copySelection()
    const a = s().pasteClipboard({ x: 0, y: 0 })!
    const b = s().pasteClipboard({ x: 50, y: 0 })!
    const g = s().graph!
    expect([g.nodes[a[0]].name, g.nodes[b[0]].name]).toEqual(['rsi1', 'rsi2'])
    expect([g.nodes[a[0]].params.out, g.nodes[b[0]].params.out]).toEqual(['@rsi_2', '@rsi_3'])
  })
})

describe('commands', () => {
  it('binds mod+c, mod+x, mod+v and mod+d in canvas scope with menu tags', () => {
    const byKey = (k: string) => listCommands().find(c => c.keys?.includes(k))
    expect(byKey('mod+c')?.id).toBe('clipboard.copy')
    expect(byKey('mod+x')?.id).toBe('clipboard.cut')
    expect(byKey('mod+v')?.id).toBe('clipboard.paste')
    expect(byKey('mod+d')?.id).toBe('clipboard.duplicate')
    for (const id of ['clipboard.copy', 'clipboard.cut', 'clipboard.paste', 'clipboard.duplicate']) {
      expect(cmd(id).scope).toBe('canvas')
    }
    expect(cmd('clipboard.paste').menu).toContain('pane')
    expect(cmd('clipboard.duplicate').menu).toContain('node')
  })

  it('paste is disabled with an empty clipboard and on a read-only graph', () => {
    const paste = cmd('clipboard.paste')
    expect(paste.when!(s())).toBe(false)
    expect(paste.disabledReason!(s())).toBe('Nothing to paste')
    s().setSelection({ nodeIds: ['r'] })
    s().copySelection()
    expect(paste.when!(s())).toBe(true)
    setReadOnly()
    expect(paste.when!(s())).toBe(false)
    expect(cmd('clipboard.duplicate').when!(s())).toBe(false)
    expect(cmd('clipboard.cut').when!(s())).toBe(false)
  })

  it('copy then paste lands at the cursor and selects the copy', () => {
    s().setSelection({ nodeIds: ['r', 'x'] })
    expect(runCmd('clipboard.copy', fakeCanvas())).not.toBe(false)
    expect(s().flash?.text).toBe('Copied 2 nodes')
    expect(runCmd('clipboard.paste', fakeCanvas())).toBe(true)
    const [first] = s().selectedNodeIds
    expect(s().graph!.nodes[first].position).toEqual([600, 700])
  })

  it('copy reads the graph on screen (the read-only view keeps it outside the store)', () => {
    const view = { ...graph(), readOnly: true }
    useNodeBuilderStore.setState({ graph: null })
    s().setSelection({ nodeIds: ['r'] })
    runCmd('clipboard.copy', fakeCanvas({ graph: () => view, editable: () => false }))
    expect(s().clipboard?.nodes.map(n => n.id)).toEqual(['r'])
  })

  it('paste does nothing while the canvas shows a read-only graph', () => {
    s().setSelection({ nodeIds: ['r'] })
    s().copySelection()
    const before = s().graph
    expect(runCmd('clipboard.paste', fakeCanvas({ editable: () => false }))).toBe(false)
    expect(s().graph).toBe(before)
  })

  it('cut copies then deletes through the canvas', () => {
    s().setSelection({ nodeIds: ['x'] })
    const deleteSelection = vi.fn(() => true)
    expect(runCmd('clipboard.cut', fakeCanvas({ deleteSelection }))).toBe(true)
    expect(deleteSelection).toHaveBeenCalledWith({ rewire: true })
    expect(s().clipboard?.nodes.map(n => n.id)).toEqual(['x'])
  })

  it('Cmd+C leaves text selected outside the canvas to the browser', () => {
    const panel = document.createElement('p')
    panel.textContent = 'some description'
    const canvasEl = document.createElement('div')
    document.body.append(panel, canvasEl)
    const range = document.createRange()
    range.selectNodeContents(panel)
    window.getSelection()!.removeAllRanges()
    window.getSelection()!.addRange(range)
    try {
      s().setSelection({ nodeIds: ['r'] })
      const ev = new KeyboardEvent('keydown', { key: 'c', metaKey: true })
      expect(runCmd('clipboard.copy', fakeCanvas({ container: () => canvasEl }), ev)).toBe(false)
      expect(s().clipboard).toBeNull()
    } finally {
      window.getSelection()!.removeAllRanges()
      panel.remove()
      canvasEl.remove()
    }
  })
})

describe('Alt-drag duplicate', () => {
  it('swapIdsForDrag: the grabbed ids become the copy, the originals keep everything under new ids', () => {
    const g = graph()
    const { graph: out, copyIds, originalIds } = swapIdsForDrag(g, ['r'], new Map([['r', [110, 210]]]))!
    expect(copyIds).toEqual(['r'])
    const orig = out.nodes[originalIds.get('r')!]
    expect(orig).toMatchObject({ name: 'rsi', position: [100, 200], params: { out: '@rsi' } })
    // Outside wires and box membership stay with the original.
    expect(out.wires.find(w => w.id === 'w1')).toMatchObject({ from: 't', to: orig.id })
    expect(out.wires.find(w => w.id === 'w2')).toMatchObject({ from: orig.id, to: 'x' })
    expect(out.annotations.boxes[0].members).toEqual([orig.id, 'x'])
    // The copy: new name, new write, no outside wires, where React Flow shows it.
    expect(out.nodes.r).toMatchObject({ name: 'rsi1', position: [110, 210], params: { out: '@rsi_2' } })
    expect(out.wires.some(w => w.from === 'r' || w.to === 'r')).toBe(false)
  })

  it('remapNodeIds changes ids only', () => {
    const g = graph()
    const out = remapNodeIds(g, new Map([['x', 'x2']]))
    expect(Object.keys(out.nodes)).toEqual(['t', 'r', 'x2', 'e'])
    expect(out.nodes.x2).toEqual({ ...g.nodes.x, id: 'x2' })
    expect(out.wires.map(w => [w.from, w.to])).toEqual([['t', 'r'], ['r', 'x2'], ['x2', 'e']])
  })

  it('duplicate plus drop is one undo step; undo restores the graph exactly', () => {
    const before = s().graph
    const snapshot = JSON.parse(JSON.stringify(before))
    const ctx = fakeCanvas()
    const grabbed = [rf('r', 100, 200), rf('x', 140, 320)]
    altDrag.onNodeDragStart!(ALT, grabbed[0], grabbed, ctx)
    expect(_altDragOpen()).toBe(true)
    // The copies hold the grabbed ids; internal wire kept between them.
    expect(s().graph!.nodes.r.name).toBe('rsi1')
    expect(s().graph!.nodes.x.params.a).toBe('@rsi_2')
    expect(s().graph!.wires.some(w => w.from === 'r' && w.to === 'x')).toBe(true)
    // The canvas's drop: its own batch around the plugins plus the move.
    s().beginBatch()
    altDrag.onNodeDragStop!(PLAIN, grabbed[0], grabbed, ctx)
    s().moveNodes(['r', 'x'], [[200, 0], [200, 0]])
    s().endBatch()
    expect(_altDragOpen()).toBe(false)
    expect(s().batch).toBeNull()
    expect(s().past).toHaveLength(1)
    expect(s().graph!.nodes.r.position).toEqual([300, 200])
    s().undo()
    expect(s().graph).toEqual(snapshot)
    // The next edit is its own step.
    s().moveNodes(['t'], [[1, 1]])
    expect(s().past).toHaveLength(1)
  })

  it('without Alt, or on a read-only graph, nothing is duplicated', () => {
    const before = s().graph
    altDrag.onNodeDragStart!(PLAIN, rf('r', 100, 200), [rf('r', 100, 200)], fakeCanvas())
    expect(s().graph).toBe(before)
    setReadOnly()
    const ro = s().graph
    altDrag.onNodeDragStart!(ALT, rf('r', 100, 200), [rf('r', 100, 200)], fakeCanvas())
    expect(s().graph).toBe(ro)
    expect(_altDragOpen()).toBe(false)
  })

  it('boxes and notes in the drag just move (only graph nodes are duplicated)', () => {
    altDrag.onNodeDragStart!(ALT, rf('n1', 0, 0), [rf('n1', 0, 0), rf('r', 100, 200)], fakeCanvas())
    expect(Object.keys(s().graph!.nodes)).toHaveLength(5)
    expect(s().graph!.annotations.notes).toHaveLength(1)
    altDrag.onNodeDragStop!(PLAIN, rf('r', 0, 0), [], fakeCanvas())
  })

  it('closes its batch after the mouse is released even if no drag stop arrives', () => {
    vi.useFakeTimers()
    altDrag.onNodeDragStart!(ALT, rf('r', 100, 200), [rf('r', 100, 200)], fakeCanvas())
    expect(s().batch).not.toBeNull()
    window.dispatchEvent(new MouseEvent('mouseup'))
    vi.runAllTimers()
    expect(_altDragOpen()).toBe(false)
    expect(s().batch).toBeNull()
  })
})
