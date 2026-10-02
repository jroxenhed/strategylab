/**
 * Canvas extension points (F435 W3 pre-step 3.0): the canvas draws its
 * chrome without React Flow Controls (amendment A2), draws node types and
 * extra nodes registered from outside, calls plugins from its handlers,
 * registers itself as the active canvas for commands, mirrors React Flow's
 * selection into the store and applies a selection set from the store.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useRef } from 'react'
import type { NodeProps, Node as RFNode } from '@xyflow/react'
import Canvas from '../Canvas'
import { useGlobalKeys } from '../commands/useGlobalKeys'
import { getActiveCanvas, runCommand } from '../commands'
import { anyPluginHandled, registerCanvasPlugin, type CanvasCtx, type CanvasPlugin } from '../canvasPlugins'
import { registerNodeType, getNodeTypes } from '../nodeTypes'
import { registerEdgeType, getEdgeTypes } from '../edgeTypes'
import { registerRfNodeSource } from '../rfMapping'
import { useNodeBuilderStore } from '../store'
import { focusDiagnosticWire, resetDiagnostics } from '../useDiagnostics'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import type { Graph } from '../../../api/nodebuilder'

function makeGraph(): Graph {
  const node = (id: string, type: string, params: Record<string, string | number>, y: number) => ({
    id, type, name: id, parent: null, params, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 2,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d' }, 0),
      r: node('r', 'rsi', { period: 14 }, 150),
      e: node('e', 'entry', {}, 300),
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'r', to: 'e', from_port: 'out', to_port: 'in0' },
    ],
    annotations: {
      boxes: [],
      notes: [{ id: 'note1', text: 'hello', rect: [400, 0, 200, 88], color: 'amber', parent: null }],
    },
  }
}

beforeAll(() => {
  // React Flow reads this in jsdom; a no-op stand-in is enough here.
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
  resetDiagnostics()
  useNodeBuilderStore.getState().discardEdits()
})

/** The builder root as NodeBuilder has it: global keys are dispatched here. */
function BuilderRoot({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useGlobalKeys(ref)
  return (
    <div ref={ref} className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      {children}
    </div>
  )
}

function mount() {
  useNodeBuilderStore.getState().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
  const g = useNodeBuilderStore.getState().graph!
  const utils = render(
    <BuilderRoot>
      <Canvas graph={g} />
    </BuilderRoot>,
  )
  const root = utils.container.querySelector('.nodebuilder-root') as HTMLElement
  root.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  const canvas = utils.container.querySelector('.nodebuilder-root .nodebuilder-root') as HTMLElement
  canvas.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  return { ...utils, canvas, root }
}

describe('CanvasChrome', () => {
  it('draws the grid and the minimap, and no React Flow Controls (A2)', () => {
    const { container } = mount()
    expect(container.querySelector('.react-flow__background')).not.toBeNull()
    expect(container.querySelector('.react-flow__minimap')).not.toBeNull()
    expect(container.querySelector('.react-flow__controls')).toBeNull()
  })
})

describe('node and edge types, node sources', () => {
  it('keeps one map identity until a type is registered', () => {
    const before = getNodeTypes()
    expect(getNodeTypes()).toBe(before)
    expect(Object.keys(before)).toEqual(
      expect.arrayContaining(['ticker', 'indicator', 'comparison', 'logic', 'settings', 'nbOutput']),
    )
    function Thing() { return null }
    const off = registerNodeType('nbThing', Thing)
    expect(getNodeTypes()).not.toBe(before)
    expect(getNodeTypes().nbThing).toBeDefined()
    off()
    expect(getNodeTypes().nbThing).toBeUndefined()
    const edgesBefore = getEdgeTypes()
    expect(edgesBefore.attr).toBeDefined()
    const offEdge = registerEdgeType('nbThingEdge', (() => null) as unknown as Parameters<typeof registerEdgeType>[1])
    expect(getEdgeTypes().nbThingEdge).toBeDefined()
    offEdge()
  })

  it('draws a registered node type for the nodes a registered source adds', () => {
    function TestNote({ data }: NodeProps) {
      return <div data-testid="nb-test-note">{String((data as { text: string }).text)}</div>
    }
    offs.push(registerNodeType('nbTestNote', TestNote))
    offs.push(registerRfNodeSource({
      id: 'test-notes',
      nodes: g => g.annotations.notes.map((n): RFNode => ({
        id: `test-${n.id}`, type: 'nbTestNote', position: { x: n.rect[0], y: n.rect[1] }, data: { text: n.text },
      })),
    }))
    const { container } = mount()
    expect(container.querySelector('.react-flow__node-nbTestNote')).not.toBeNull()
    expect(screen.getByTestId('nb-test-note')).toHaveTextContent('hello')
    // The graph nodes are still drawn by their own renderers.
    expect(container.querySelector('.react-flow__node-nbOutput')).not.toBeNull()
  })
})

describe('plugins', () => {
  it('get the pane context menu, pointer moves in flow units and pointer leave', () => {
    const onPaneContextMenu = vi.fn()
    const onPointerMove = vi.fn()
    const onPointerLeave = vi.fn()
    offs.push(registerCanvasPlugin({ id: 't.plugin', onPaneContextMenu, onPointerMove, onPointerLeave }))
    const { canvas } = mount()
    const pane = canvas.querySelector('.react-flow__pane') as HTMLElement
    fireEvent.contextMenu(pane)
    expect(onPaneContextMenu).toHaveBeenCalledTimes(1)
    const ctx = onPaneContextMenu.mock.calls[0][1] as CanvasCtx
    expect(ctx.graph()).toBe(useNodeBuilderStore.getState().graph)
    expect(ctx.editable()).toBe(true)
    expect(ctx.store).toBe(useNodeBuilderStore)
    fireEvent.pointerMove(canvas, { clientX: 120, clientY: 80 })
    expect(onPointerMove).toHaveBeenCalledTimes(1)
    const [pos] = onPointerMove.mock.calls[0] as [{ x: number; y: number }]
    expect(Number.isFinite(pos.x) && Number.isFinite(pos.y)).toBe(true)
    expect(ctx.pointerOnCanvas()).toBe(true)
    fireEvent.pointerLeave(canvas)
    expect(onPointerLeave).toHaveBeenCalledTimes(1)
    expect(ctx.pointerOnCanvas()).toBe(false)
  })

  it('anyPluginHandled calls every plugin, ORs their answers and survives a throwing one', () => {
    const calls: string[] = []
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const a: CanvasPlugin = { id: 't.a', onDeleteSelection: () => { calls.push('a'); return true } }
    const b: CanvasPlugin = { id: 't.b', onDeleteSelection: () => { calls.push('b'); throw new Error('boom') } }
    const c: CanvasPlugin = { id: 't.c', onDeleteSelection: () => { calls.push('c') } }
    offs.push(registerCanvasPlugin(a), registerCanvasPlugin(b), registerCanvasPlugin(c))
    const req = { nodeIds: [], wireIds: [], otherIds: [], rewire: true }
    expect(anyPluginHandled(p => p.onDeleteSelection?.(req, {} as CanvasCtx))).toBe(true)
    expect(calls).toEqual(['a', 'b', 'c'])
    expect(err).toHaveBeenCalled()
    err.mockRestore()
  })

  it('Delete hands boxes and notes to onDeleteSelection in the same undo step as the nodes', () => {
    // The real annotations source (plugins/boxDrag.ts, auto-loaded) draws note1.
    const seen: Array<{ nodeIds: string[]; otherIds: string[]; rewire: boolean }> = []
    offs.push(registerCanvasPlugin({
      id: 't.notes',
      onDeleteSelection(req, ctx) {
        seen.push({ nodeIds: req.nodeIds, otherIds: req.otherIds, rewire: req.rewire })
        if (req.otherIds.length === 0) return
        ctx.store.getState().commit('delete note', g => ({
          ...g, annotations: { ...g.annotations, notes: g.annotations.notes.filter(n => !req.otherIds.includes(n.id)) },
        }))
        return true
      },
    }))
    mount()
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['r'], annotationIds: ['note1'] }) })
    const before = useNodeBuilderStore.getState().graph
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    expect(seen).toEqual([{ nodeIds: ['r'], otherIds: ['note1'], rewire: true }])
    const s = useNodeBuilderStore.getState()
    expect(s.graph!.nodes.r).toBeUndefined()
    expect(s.graph!.annotations.notes).toHaveLength(0)
    expect(s.past).toHaveLength(1)
    s.undo()
    expect(useNodeBuilderStore.getState().graph).toBe(before)
  })
})

describe('active canvas and commands', () => {
  it('the mounted canvas is the active canvas; runCommand opens the Tab menu through it', () => {
    mount()
    const ctx = getActiveCanvas()
    expect(ctx).not.toBeNull()
    expect(ctx!.graph()).toBe(useNodeBuilderStore.getState().graph)
    let ran = false
    act(() => { ran = runCommand('edit.addNode') })
    expect(ran).toBe(true)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    cleanup()
    expect(getActiveCanvas()).toBeNull()
  })

  it('openTabMenu with onCreate wires the new node itself, in one undo step', () => {
    mount()
    const ctx = getActiveCanvas()!
    act(() => { useNodeBuilderStore.getState().select('r') })
    const onCreate = vi.fn((nodeId: string, c: CanvasCtx) => {
      c.store.getState().moveNode(nodeId, [999, 999])
    })
    act(() => { ctx.openTabMenu({ screen: { x: 100, y: 100 }, onCreate }) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: 'above' } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    const s = useNodeBuilderStore.getState()
    const above = Object.values(s.graph!.nodes).find(n => n.type === 'above')!
    expect(onCreate).toHaveBeenCalledWith(above.id, ctx)
    expect(above.position).toEqual([999, 999])
    // No auto-wire from the selected RSI: onCreate decides.
    expect(s.graph!.wires.some(w => w.to === above.id)).toBe(false)
    expect(s.past).toHaveLength(1)
  })
})

describe('selection between the canvas and the store', () => {
  it('setSelection selects several nodes on the canvas', () => {
    const { container } = mount()
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['t', 'r'] }) })
    const selected = Array.from(container.querySelectorAll('.react-flow__node.selected')).map(
      el => el.getAttribute('data-id'),
    )
    expect(selected.sort()).toEqual(['r', 't'])
    // React Flow reports the same selection back; the store keeps it.
    expect(useNodeBuilderStore.getState().selectedNodeIds).toEqual(['t', 'r'])
  })

  it('a click on a box or note selects it with no primary node, and it stays selected', () => {
    // The real annotations source (plugins/boxDrag.ts, auto-loaded) draws note1.
    const { container } = mount()
    const nodeEl = (id: string) => container.querySelector(`.react-flow__node[data-id="${id}"]`) as HTMLElement
    act(() => { fireEvent.click(nodeEl('r')) })
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('r')
    act(() => { fireEvent.click(nodeEl('note1')) })
    let s = useNodeBuilderStore.getState()
    expect(s.selectedAnnotationIds).toEqual(['note1'])
    expect(s.selectedNodeIds).toEqual([])
    expect(s.selectedNodeId).toBeNull()
    expect(nodeEl('note1').classList.contains('selected')).toBe(true)
    expect(nodeEl('r').classList.contains('selected')).toBe(false)
    // Selecting a graph node from the store replaces it.
    act(() => { useNodeBuilderStore.getState().select('t') })
    s = useNodeBuilderStore.getState()
    expect(s.selectedNodeIds).toEqual(['t'])
    expect(s.selectedAnnotationIds).toEqual([])
    expect(nodeEl('note1').classList.contains('selected')).toBe(false)
  })

  it("mirrors React Flow's wire selection into the store", () => {
    mount()
    const diag: Diagnostic = {
      node_id: 'e', path: null, severity: 'error', code: 'attr_missing', message: 'm', param: null, port: 'in0',
      line: null, col: null, end_line: null, end_col: null,
    }
    act(() => { focusDiagnosticWire(diag) })
    const s = useNodeBuilderStore.getState()
    expect(s.selectedWireIds).toEqual(['w2'])
    expect(s.selectedNodeIds).toEqual([])
  })
})
