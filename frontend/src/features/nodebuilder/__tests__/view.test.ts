/**
 * View (F435 W3 item 3.D): framing (F, H, Home), the snap-to-grid toggle
 * (G), Space+drag pan, the gentle wheel zoom, per-network viewport memory,
 * and framing a node from a diagnostics row.
 *
 * Marquee, Shift+click and wheel zoom are asserted as the props the canvas
 * hands React Flow (React Flow itself is not re-tested here).
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, cleanup, act, fireEvent, screen } from '@testing-library/react'
import { createElement } from 'react'
import type { ReactFlowInstance } from '@xyflow/react'
import Canvas from '../Canvas'
import DiagnosticsPopover from '../DiagnosticsPopover'
import { dispatchKey, findCommands, getActiveCanvas, getCommand, listCommands, runCommand, setActiveCanvas } from '../commands'
import type { CanvasCtx } from '../canvasPlugins'
import { useNodeBuilderStore } from '../store'
import { HOME_VIEWPORT, ROOT_NETWORK, SNAP_GRID } from '../store/view'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import {
  focusNode,
  frameNode,
  isNodeOnScreen,
  MAX_ZOOM,
  MIN_ZOOM,
  wheelIsForSomethingElse,
  wheelViewport,
} from '../viewOps'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import type { Graph } from '../../../api/nodebuilder'

// Record the props the canvas gives React Flow, and still draw the real one.
const rfProps = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }))
vi.mock('@xyflow/react', async importOriginal => {
  const mod = await importOriginal<typeof import('@xyflow/react')>()
  const react = await import('react')
  function ReactFlowSpy(props: Record<string, unknown>) {
    rfProps.current = props
    return react.createElement(mod.ReactFlow as unknown as React.ComponentType<Record<string, unknown>>, props)
  }
  return { ...mod, ReactFlow: ReactFlowSpy }
})

function makeGraph(readOnly = false): Graph {
  const node = (id: string, type: string, params: Record<string, string | number>, y: number) => ({
    id, type, name: id, parent: null, params, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 2,
    stream_schema: 1,
    readOnly,
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
    annotations: { boxes: [], notes: [] },
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

afterEach(() => {
  cleanup()
  setActiveCanvas(null)
  resetDiagnostics()
  const s = useNodeBuilderStore.getState()
  s.setSnapToGrid(false)
  s.setNetwork(ROOT_NETWORK)
  s.discardEdits()
  rfProps.current = null
})

const s = () => useNodeBuilderStore.getState()
const nextFrame = () => act(() => new Promise<void>(r => requestAnimationFrame(() => r())))
// React Flow reports the end of a move from a timer (up to 150 ms later).
const settle = () => act(() => new Promise<void>(r => setTimeout(r, 200)))

function mountCanvas(graph: Graph) {
  return render(createElement('div', { style: { width: 800, height: 600 } }, createElement(Canvas, { graph })))
}

/** Open the test graph in the store and draw it, editable. */
function mountEditable() {
  s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
  return mountCanvas(s().graph!)
}

// ── A canvas stand-in for the command and framing logic ─────────────────────

interface FakeNode { id: string; x: number; y: number; w: number; h: number }

function fakeCanvas(opts: { nodes?: FakeNode[]; viewport?: { x: number; y: number; zoom: number }; size?: [number, number] } = {}) {
  const nodes = opts.nodes ?? [
    { id: 't', x: 0, y: 0, w: 100, h: 50 },
    { id: 'r', x: 0, y: 150, w: 100, h: 50 },
    { id: 'e', x: 0, y: 300, w: 100, h: 50 },
  ]
  const viewport = opts.viewport ?? { x: 0, y: 0, zoom: 1 }
  const [width, height] = opts.size ?? [800, 600]
  const container = document.createElement('div')
  container.getBoundingClientRect = () => ({ left: 0, top: 0, right: width, bottom: height, width, height, x: 0, y: 0, toJSON() {} }) as DOMRect
  const rf = {
    fitView: vi.fn(() => Promise.resolve(true)),
    getNodes: vi.fn(() => nodes.map(n => ({ id: n.id }))),
    getNode: vi.fn((id: string) => nodes.find(n => n.id === id)),
    getInternalNode: vi.fn((id: string) => {
      const n = nodes.find(m => m.id === id)
      return n && { id, internals: { positionAbsolute: { x: n.x, y: n.y } }, measured: { width: n.w, height: n.h } }
    }),
    getViewport: vi.fn(() => viewport),
    setViewport: vi.fn(() => Promise.resolve(true)),
  }
  const ctx = {
    rf: rf as unknown as ReactFlowInstance,
    store: useNodeBuilderStore,
    pointer: () => ({ x: 0, y: 0 }),
    pointerOnCanvas: () => false,
    graph: () => s().graph ?? makeGraph(),
    editable: () => true,
    container: () => container,
    focus: () => {},
    openTabMenu: () => false,
    deleteSelection: () => false,
  } as unknown as CanvasCtx
  return { ctx, rf }
}

// ── React Flow props ─────────────────────────────────────────────────────────

describe('React Flow props', () => {
  it('editing: left drag on empty canvas is a partial marquee; middle drag or Space+drag pans', () => {
    mountEditable()
    const p = rfProps.current!
    expect(p.selectionOnDrag).toBe(true)
    expect(p.selectionMode).toBe('partial')
    expect(p.panOnDrag).toEqual([1])
    expect(p.panActivationKeyCode).toBe('Space')
  })

  it('Shift+click toggles a node in the selection (Cmd/Ctrl+click too)', () => {
    mountEditable()
    expect(rfProps.current!.multiSelectionKeyCode).toEqual(expect.arrayContaining(['Shift', 'Meta', 'Control']))
  })

  it('the wheel zoom is the canvas own (React Flow scroll zoom off, pinch on, range 0.15 to 3)', () => {
    mountEditable()
    const p = rfProps.current!
    expect(p.zoomOnScroll).toBe(false)
    expect(p.panOnScroll).toBe(false)
    expect(p.zoomOnPinch).toBe(true)
    expect(p.zoomActivationKeyCode).toBeNull()
    expect(p.minZoom).toBe(0.15)
    expect(p.maxZoom).toBe(3)
    expect(MIN_ZOOM).toBe(0.15)
    expect(MAX_ZOOM).toBe(3)
  })

  it('snap-to-grid follows the store, on the 24px grid', () => {
    mountEditable()
    expect(rfProps.current!.snapToGrid).toBe(false)
    expect(rfProps.current!.snapGrid).toEqual([24, 24])
    act(() => { runCommand('view.toggleSnap') })
    expect(rfProps.current!.snapToGrid).toBe(true)
    expect(SNAP_GRID).toEqual([24, 24])
  })

  it('read-only: a left drag pans, no marquee, no snap', () => {
    act(() => { s().setSnapToGrid(true) })
    mountCanvas(makeGraph(true))
    const p = rfProps.current!
    expect(p.panOnDrag).toBe(true)
    expect(p.selectionOnDrag).toBe(false)
    expect(p.snapToGrid).toBe(false)
  })
})

// ── Wheel ────────────────────────────────────────────────────────────────────

describe('wheel', () => {
  const vp = { x: 0, y: 0, zoom: 1 }
  const wheel = (o: Partial<{ deltaX: number; deltaY: number; deltaMode: number; metaKey: boolean; shiftKey: boolean }>) => ({
    deltaX: 0, deltaY: 0, deltaMode: 0, metaKey: false, shiftKey: false, ...o,
  })

  it('zooms 1.08x per 100px, toward the pointer', () => {
    const inV = wheelViewport(vp, wheel({ deltaY: -100 }), { x: 200, y: 100 })!
    expect(inV.zoom).toBeCloseTo(1.08, 6)
    // The flow point under the pointer stays under it.
    expect((200 - inV.x) / inV.zoom).toBeCloseTo(200, 6)
    expect((100 - inV.y) / inV.zoom).toBeCloseTo(100, 6)
    const outV = wheelViewport(vp, wheel({ deltaY: 100 }), { x: 0, y: 0 })!
    expect(outV.zoom).toBeCloseTo(1 / 1.08, 6)
    // A 600px step is gentle (React Flow went from 1.94 to 0.37).
    expect(wheelViewport({ ...vp, zoom: 1.94 }, wheel({ deltaY: 600 }), { x: 0, y: 0 })!.zoom).toBeGreaterThan(1.2)
    // Lines count as 25px each.
    expect(wheelViewport(vp, wheel({ deltaY: -4, deltaMode: 1 }), { x: 0, y: 0 })!.zoom).toBeCloseTo(1.08, 6)
  })

  it('stays inside 0.15 to 3 and reports no change at the limit', () => {
    expect(wheelViewport({ ...vp, zoom: 2.99 }, wheel({ deltaY: -1000 }), { x: 0, y: 0 })!.zoom).toBe(3)
    expect(wheelViewport({ ...vp, zoom: 3 }, wheel({ deltaY: -100 }), { x: 0, y: 0 })).toBeNull()
    expect(wheelViewport({ ...vp, zoom: 0.16 }, wheel({ deltaY: 5000 }), { x: 0, y: 0 })!.zoom).toBe(0.15)
  })

  it('Cmd+wheel pans vertically, Shift+wheel and a sideways swipe pan horizontally', () => {
    expect(wheelViewport(vp, wheel({ deltaY: 50, metaKey: true }), { x: 0, y: 0 })).toEqual({ x: 0, y: -50, zoom: 1 })
    expect(wheelViewport(vp, wheel({ deltaY: 50, shiftKey: true }), { x: 0, y: 0 })).toEqual({ x: -50, y: 0, zoom: 1 })
    expect(wheelViewport(vp, wheel({ deltaX: 30, shiftKey: true }), { x: 0, y: 0 })).toEqual({ x: -30, y: 0, zoom: 1 })
    expect(wheelViewport(vp, wheel({ deltaX: 30 }), { x: 0, y: 0 })).toEqual({ x: -30, y: 0, zoom: 1 })
  })

  it('leaves a pinch, a .nowheel part and the minimap to React Flow', () => {
    const el = document.createElement('div')
    el.className = 'nowheel'
    const inner = document.createElement('span')
    el.appendChild(inner)
    const evt = (target: EventTarget, ctrlKey = false) => {
      const e = new WheelEvent('wheel', { deltaY: 10, ctrlKey })
      Object.defineProperty(e, 'target', { value: target })
      return e
    }
    expect(wheelIsForSomethingElse(evt(inner))).toBe(true)
    expect(wheelIsForSomethingElse(evt(document.body, true))).toBe(true)
    expect(wheelIsForSomethingElse(evt(document.body))).toBe(false)
  })

  it('a wheel event on the mounted canvas zooms it gently and stops the page scroll', async () => {
    const { container } = mountEditable()
    await nextFrame()
    const rf = getActiveCanvas()!.rf
    await act(async () => { await rf.setViewport({ x: 0, y: 0, zoom: 1 }) })
    const pane = container.querySelector('.react-flow__pane') as HTMLElement
    const e = new WheelEvent('wheel', { deltaY: -100, clientX: 0, clientY: 0, bubbles: true, cancelable: true })
    act(() => { pane.dispatchEvent(e) })
    expect(e.defaultPrevented).toBe(true)
    expect(rf.getViewport().zoom).toBeCloseTo(1.08, 6)
    // The move end is saved as this network's view.
    await settle()
    expect(s().rememberedViewport()?.zoom).toBeCloseTo(1.08, 6)
  })
})

// ── Commands and keys ────────────────────────────────────────────────────────

describe('view commands', () => {
  it('binds F, H, Home, G and Space', () => {
    const bound = new Map<string, string>()
    for (const c of listCommands()) for (const k of c.keys ?? []) bound.set(k, c.id)
    expect(bound.get('f')).toBe('view.frameSelection')
    expect(bound.get('h')).toBe('view.frameAll')
    expect(bound.get('home')).toBe('view.frameAll')
    expect(bound.get('g')).toBe('view.toggleSnap')
    expect(bound.get('space')).toBe('view.pan')
    expect(bound.get(' ')).toBe('view.pan')
  })

  it('menus: Frame on nodes, Frame all and Snap to grid (checked) on the pane', () => {
    const menus = (id: string) => [getCommand(id)?.menu].flat()
    expect(menus('view.frameSelection')).toContain('node')
    expect(menus('view.frameAll')).toContain('pane')
    expect(menus('view.toggleSnap')).toContain('pane')
    const snap = getCommand('view.toggleSnap')!
    expect(snap.checked?.(s())).toBe(false)
    act(() => { s().setSnapToGrid(true) })
    expect(snap.checked?.(s())).toBe(true)
  })

  it('H and Home frame everything: 80px padding, zoom 0.15 to 1.0', () => {
    const { ctx, rf } = fakeCanvas()
    setActiveCanvas(ctx)
    for (const key of ['h', 'Home']) {
      rf.fitView.mockClear()
      const handled = dispatchKey(new KeyboardEvent('keydown', { key, cancelable: true }), { scopes: new Set(['canvas']), canvas: ctx })
      expect(handled).toBe(true)
      expect(rf.fitView).toHaveBeenCalledTimes(1)
      const opts = (rf.fitView.mock.calls[0] as unknown[])[0] as Record<string, unknown>
      expect(opts).toMatchObject({ padding: '80px', minZoom: 0.15, maxZoom: 1 })
      expect(opts.nodes).toBeUndefined()
    }
  })

  it('Reset view runs the same command (runCommand view.frameAll)', () => {
    const { ctx, rf } = fakeCanvas()
    setActiveCanvas(ctx)
    expect(runCommand('view.frameAll')).toBe(true)
    expect(rf.fitView).toHaveBeenCalledTimes(1)
  })

  it('F frames the selected nodes; a selected wire frames both ends; nothing selected frames all', () => {
    s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const { ctx, rf } = fakeCanvas()
    setActiveCanvas(ctx)
    const framed = () => {
      const opts = (rf.fitView.mock.calls.at(-1) as unknown[])[0] as { nodes?: Array<{ id: string }> }
      return opts.nodes?.map(n => n.id).sort() ?? 'all'
    }
    act(() => { s().setSelection({ nodeIds: ['t', 'e'], primary: 't' }) })
    runCommand('view.frameSelection')
    expect(framed()).toEqual(['e', 't'])
    act(() => { s().setSelection({ wireIds: ['w2'] }) })
    runCommand('view.frameSelection')
    expect(framed()).toEqual(['e', 'r'])
    act(() => { s().setSelection({}) })
    runCommand('view.frameSelection')
    expect(framed()).toBe('all')
  })

  it('framing is disabled with nothing on the canvas', () => {
    const { ctx, rf } = fakeCanvas({ nodes: [] })
    setActiveCanvas(ctx)
    expect(runCommand('view.frameAll')).toBe(false)
    expect(runCommand('view.frameSelection')).toBe(false)
    expect(getCommand('view.frameAll')!.disabledReason?.(s())).toBe('Nothing to frame')
    expect(rf.fitView).not.toHaveBeenCalled()
  })

  it('G toggles snap, says so, and is not an undo step', () => {
    s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const { ctx } = fakeCanvas()
    setActiveCanvas(ctx)
    act(() => { dispatchKey(new KeyboardEvent('keydown', { key: 'g' }), { scopes: new Set(['canvas']), canvas: ctx }) })
    expect(s().snapToGrid).toBe(true)
    expect(s().flash?.text).toBe('Snap to grid on')
    act(() => { runCommand('view.toggleSnap') })
    expect(s().snapToGrid).toBe(false)
    expect(s().flash?.text).toBe('Snap to grid off')
    expect(s().canUndo).toBe(false)
    expect(s().dirty).toBe(false)
  })

  it('Space is listed but left to React Flow (no preventDefault)', () => {
    const { ctx } = fakeCanvas()
    expect(findCommands(' ', new Set(['canvas'])).map(c => c.id)).toContain('view.pan')
    const e = new KeyboardEvent('keydown', { key: ' ', cancelable: true })
    expect(dispatchKey(e, { scopes: new Set(['canvas']), canvas: ctx })).toBe(false)
    expect(e.defaultPrevented).toBe(false)
  })
})

// ── frameNode / focusNode ────────────────────────────────────────────────────

describe('frameNode and focusNode', () => {
  it('isNodeOnScreen: wholly inside the canvas, under the current pan and zoom', () => {
    const { ctx } = fakeCanvas({ nodes: [{ id: 'a', x: 100, y: 100, w: 100, h: 50 }] })
    expect(isNodeOnScreen(ctx, 'a')).toBe(true)
    const panned = fakeCanvas({ nodes: [{ id: 'a', x: 100, y: 100, w: 100, h: 50 }], viewport: { x: -150, y: 0, zoom: 1 } })
    expect(isNodeOnScreen(panned.ctx, 'a')).toBe(false)  // half off the left edge
    const zoomed = fakeCanvas({ nodes: [{ id: 'a', x: 700, y: 100, w: 100, h: 50 }], viewport: { x: 0, y: 0, zoom: 0.5 } })
    expect(isNodeOnScreen(zoomed.ctx, 'a')).toBe(true)
    expect(isNodeOnScreen(ctx, 'missing')).toBe(false)
  })

  it('frames one node; with onlyIfOffscreen only when it is not fully visible', () => {
    const { ctx, rf } = fakeCanvas({ nodes: [{ id: 'near', x: 10, y: 10, w: 100, h: 50 }, { id: 'far', x: 5000, y: 10, w: 100, h: 50 }] })
    setActiveCanvas(ctx)
    expect(frameNode('near', { onlyIfOffscreen: true })).toBe(false)
    expect(rf.fitView).not.toHaveBeenCalled()
    expect(frameNode('far', { onlyIfOffscreen: true })).toBe(true)
    expect(rf.fitView).toHaveBeenLastCalledWith(expect.objectContaining({ nodes: [{ id: 'far' }], padding: '80px', maxZoom: 1 }))
    expect(frameNode('near')).toBe(true)
    expect(frameNode('missing')).toBe(false)
    setActiveCanvas(null)
    expect(frameNode('far')).toBe(false)
  })

  it('focusNode selects only the node and frames it when off-screen', () => {
    s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const { ctx, rf } = fakeCanvas({ nodes: [{ id: 'e', x: 0, y: 3000, w: 100, h: 50 }] })
    setActiveCanvas(ctx)
    act(() => { focusNode('e') })
    expect(s().selectedNodeId).toBe('e')
    expect(rf.fitView).toHaveBeenCalledWith(expect.objectContaining({ nodes: [{ id: 'e' }] }))
  })

  it('a diagnostics popover row selects the node and frames it when off-screen (S05)', () => {
    s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const { ctx, rf } = fakeCanvas({ nodes: [{ id: 't', x: 0, y: 0, w: 100, h: 50 }, { id: 'e', x: 0, y: 3000, w: 100, h: 50 }] })
    setActiveCanvas(ctx)
    const diag = (node_id: string): Diagnostic => ({
      node_id, path: `/${node_id}`, severity: 'error', code: 'missing_input', message: `${node_id} needs an input`,
      param: null, port: null, line: null, col: null, end_line: null, end_col: null,
    } as unknown as Diagnostic)
    act(() => { setServerDiagnostics([diag('e'), diag('t')]) })
    const onSelectNode = vi.fn((id: string) => s().select(id))
    render(createElement(DiagnosticsPopover, { open: true, anchorEl: document.body, onClose: () => {}, onSelectNode }))
    // `e` is far below the visible canvas: selected and framed.
    fireEvent.click(screen.getByTestId('nb-diag-row-0'))
    expect(onSelectNode).toHaveBeenCalledWith('e')
    expect(rf.fitView).toHaveBeenCalledTimes(1)
    expect(rf.fitView).toHaveBeenLastCalledWith(expect.objectContaining({ nodes: [{ id: 'e' }] }))
    // `t` is on screen: selected, the view stays.
    fireEvent.click(screen.getByTestId('nb-diag-row-1'))
    expect(onSelectNode).toHaveBeenLastCalledWith('t')
    expect(rf.fitView).toHaveBeenCalledTimes(1)
  })
})

// ── Viewport memory ──────────────────────────────────────────────────────────

describe('per-network viewport memory', () => {
  it('remembers per network, and forgets on the next load', () => {
    s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    expect(s().rememberedViewport()).toBeNull()
    act(() => { s().setViewport({ x: 10, y: 20, zoom: 0.5 }) })
    expect(s().rememberedViewport()).toEqual({ x: 10, y: 20, zoom: 0.5 })

    act(() => { s().setNetwork('/long_leg') })
    expect(s().viewport).toEqual(HOME_VIEWPORT)
    expect(s().rememberedViewport()).toBeNull()
    act(() => { s().setViewport({ x: 1, y: 2, zoom: 2 }) })
    act(() => { s().setNetwork(ROOT_NETWORK) })
    expect(s().viewport).toEqual({ x: 10, y: 20, zoom: 0.5 })
    expect(s().rememberedViewport('/long_leg')).toEqual({ x: 1, y: 2, zoom: 2 })

    // Open another graph: the old views do not carry over.
    s().openGraph(makeGraph(), { id: 'g2', rev: 1, name: 'other' })
    expect(s().rememberedViewport()).toBeNull()
    expect(s().rememberedViewport('/long_leg')).toBeNull()
    act(() => { s().setViewport({ x: 5, y: 5, zoom: 1 }) })
    expect(s().viewports).toEqual({ [ROOT_NETWORK]: { x: 5, y: 5, zoom: 1 } })
  })

  it('an unchanged viewport writes nothing', () => {
    s().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    act(() => { s().setViewport({ x: 3, y: 4, zoom: 1 }) })
    const before = s().viewports
    act(() => { s().setViewport({ x: 3, y: 4, zoom: 1 }) })
    expect(s().viewports).toBe(before)
  })

  it('the canvas restores the saved view when it mounts again, and after the read-only view', async () => {
    const saved = { x: 33, y: 44, zoom: 0.7 }
    const first = mountEditable()
    await nextFrame()
    await settle()
    act(() => { s().setViewport(saved) })
    first.unmount()

    const { rerender } = mountCanvas(s().graph!)
    await nextFrame()
    expect(getActiveCanvas()!.rf.getViewport()).toEqual(saved)

    // Back from the read-only view: the saved view, not a re-fit.
    const wrap = (graph: Graph) => createElement('div', { style: { width: 800, height: 600 } }, createElement(Canvas, { graph }))
    rerender(wrap(makeGraph(true)))
    await nextFrame()
    await act(async () => { await getActiveCanvas()!.rf.setViewport({ x: 0, y: 0, zoom: 2 }) })
    await settle()  // a read-only pan is not saved
    expect(s().rememberedViewport()).toEqual(saved)
    rerender(wrap(s().graph!))
    await nextFrame()
    expect(getActiveCanvas()!.rf.getViewport()).toEqual(saved)
  })

  it('a newly loaded graph is not given the previous graph view', async () => {
    const first = mountEditable()
    await nextFrame()
    await settle()
    act(() => { s().setViewport({ x: 33, y: 44, zoom: 0.7 }) })
    first.unmount()
    s().openGraph(makeGraph(), { id: 'g2', rev: 1, name: 'other' })
    mountCanvas(s().graph!)
    await nextFrame()
    expect(getActiveCanvas()!.rf.getViewport()).not.toEqual({ x: 33, y: 44, zoom: 0.7 })
  })
})
