/**
 * F457: Y + drag cuts wires (Houdini).
 *
 * The crossing test (wireCutGeometry.ts) is pure. The gesture (WireCut.tsx)
 * runs on window listeners, so it is driven here with real DOM events on a
 * stand-in canvas whose edges carry the paths AttrEdge draws.
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, act, screen } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import type { CanvasCtx } from '../canvasPlugins'
import { setActiveCanvas } from '../screen'
import { useNodeBuilderStore } from '../store'
import { wirePath } from '../streamLabels'
import WireCut, { CUTTING_CLASS } from '../WireCut'
import {
  cubicPoint,
  cutCrossesWire,
  isCutKey,
  parseWirePath,
  segmentsCross,
  wiresCutBy,
} from '../wireCutGeometry'
import { shortcutGroups } from '../shortcutList'

// ── Geometry ────────────────────────────────────────────────────────────────

describe('wire cut geometry', () => {
  it('reads the cubic AttrEdge draws', () => {
    const c = parseWirePath(wirePath(10, 20, 110, 220))!
    expect(c.p0).toEqual({ x: 10, y: 20 })
    expect(c.p3).toEqual({ x: 110, y: 220 })
    expect(cubicPoint(c, 0)).toEqual({ x: 10, y: 20 })
    expect(cubicPoint(c, 1)).toEqual({ x: 110, y: 220 })
    expect(parseWirePath('M0,0 L10,10')).toBeNull()
    expect(parseWirePath(null)).toBeNull()
  })

  it('reads negative and fractional numbers', () => {
    const c = parseWirePath('M-1.5,2 C-1.5,42 30.25,-8 30.25,32')!
    expect(c.p0).toEqual({ x: -1.5, y: 2 })
    expect(c.p2).toEqual({ x: 30.25, y: -8 })
  })

  it('crosses, touches, misses and runs parallel', () => {
    const o = { x: 0, y: 0 }
    expect(segmentsCross(o, { x: 10, y: 10 }, { x: 0, y: 10 }, { x: 10, y: 0 })).toBe(true)
    expect(segmentsCross(o, { x: 10, y: 0 }, { x: 5, y: 0 }, { x: 5, y: 5 })).toBe(true)
    expect(segmentsCross(o, { x: 10, y: 0 }, { x: 0, y: 1 }, { x: 10, y: 1 })).toBe(false)
    expect(segmentsCross(o, { x: 10, y: 0 }, { x: 11, y: -5 }, { x: 11, y: 5 })).toBe(false)
  })

  it('a cut across a wire cuts it; one beside it or of zero length does not', () => {
    const wire = parseWirePath(wirePath(0, 0, 0, 200))!
    expect(cutCrossesWire(wire, { x: -50, y: 100 }, { x: 50, y: 100 })).toBe(true)
    expect(cutCrossesWire(wire, { x: 10, y: 100 }, { x: 50, y: 100 })).toBe(false)
    expect(cutCrossesWire(wire, { x: -50, y: 300 }, { x: 50, y: 300 })).toBe(false)
    expect(cutCrossesWire(wire, { x: 0, y: 100 }, { x: 0, y: 100 })).toBe(false)
  })

  it('follows the curve, not the straight line between the ends', () => {
    // A wire that runs upward bows out below its source (control 120).
    const wire = parseWirePath(wirePath(0, 0, 200, -200))!
    // The straight line from (0, 0) to (200, -200) never goes below y = 0.
    expect(cutCrossesWire(wire, { x: -20, y: 15 }, { x: 40, y: 15 })).toBe(true)
    expect(cutCrossesWire(wire, { x: -20, y: 40 }, { x: 40, y: 40 })).toBe(false)
  })

  it('lists the cut wires in order and skips paths it cannot read', () => {
    const wires = [
      { id: 'a', d: wirePath(0, 0, 0, 200) },
      { id: 'bad', d: 'M0,0 L0,200' },
      { id: 'b', d: wirePath(100, 0, 100, 200) },
      { id: 'c', d: wirePath(300, 0, 300, 200) },
    ]
    expect(wiresCutBy(wires, { x: -10, y: 100 }, { x: 150, y: 100 })).toEqual(['a', 'b'])
  })

  it('Y arms the cut; Cmd, Ctrl or Alt with it do not', () => {
    const k = (key: string, mods: Partial<KeyboardEvent> = {}) =>
      isCutKey({ key, metaKey: false, ctrlKey: false, altKey: false, ...mods } as KeyboardEvent)
    expect(k('y')).toBe(true)
    expect(k('Y')).toBe(true)
    expect(k('y', { metaKey: true })).toBe(false)
    expect(k('y', { ctrlKey: true })).toBe(false)
    expect(k('y', { altKey: true })).toBe(false)
    expect(k('t')).toBe(false)
  })

  it('the shortcut overlay lists the gesture', () => {
    const mouse = shortcutGroups('cut wires', []).find(g => g.key === 'mouse')
    expect(mouse?.rows.map(r => [r.label, r.caps[0]])).toEqual([['Cut wires', 'Y+drag across them']])
  })
})

// ── The gesture ─────────────────────────────────────────────────────────────

function gnode(id: string, type: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

/** t feeds a and b, each by a vertical wire (w1 at x = 0, w2 at x = 100). */
function makeGraph(readOnly = false): Graph {
  return {
    ...emptyGraph(),
    readOnly,
    nodes: {
      t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      a: gnode('a', 'rsi', { period: 14, source: '@close', out: '@rsi' }),
      b: gnode('b', 'sma', { period: 20, source: '@close', out: '@sma' }),
    },
    wires: [
      { id: 'w1', from: 't', to: 'a', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 't', to: 'b', from_port: 'out', to_port: 'in0' },
    ],
  }
}

const st = () => useNodeBuilderStore.getState()

/** A canvas root holding the two drawn wires; flow = screen. */
function mountCanvas(opts: {
  editable?: boolean
  pointerOnCanvas?: boolean | (() => boolean)
  snap?: number
} = {}) {
  const root = document.createElement('div')
  root.innerHTML = `
    <div class="react-flow__pane" data-testid="pane"></div>
    <svg>
      <g class="react-flow__edge" data-id="w1"><path class="nb-edge__path" d="${wirePath(0, 0, 0, 200)}"/></g>
      <g class="react-flow__edge" data-id="w2"><path class="nb-edge__path" d="${wirePath(100, 0, 100, 200)}"/></g>
    </svg>`
  // jsdom lays nothing out; the gesture only runs on a canvas that is on screen.
  root.getClientRects = () => [{}] as unknown as DOMRectList
  document.body.appendChild(root)
  const edgeData = new Map<string, Record<string, unknown>>()
  // React Flow snaps to the grid by default when the flow has snapToGrid on.
  const snapTo = (v: number) => (opts.snap ? Math.round(v / opts.snap) * opts.snap : v)
  const ctx = {
    rf: {
      screenToFlowPosition: (p: { x: number; y: number }, o?: { snapToGrid?: boolean }) =>
        (o?.snapToGrid === false ? p : { x: snapTo(p.x), y: snapTo(p.y) }),
      updateEdgeData: (id: string, patch: Record<string, unknown>) => {
        edgeData.set(id, { ...edgeData.get(id), ...patch })
      },
    },
    store: useNodeBuilderStore,
    pointer: () => ({ x: 0, y: 0 }),
    pointerOnCanvas: () => (typeof opts.pointerOnCanvas === 'function' ? opts.pointerOnCanvas() : opts.pointerOnCanvas ?? true),
    graph: () => st().graph!,
    editable: () => opts.editable ?? true,
    container: () => root,
    focus: () => {},
    openTabMenu: () => true,
    deleteSelection: () => true,
  } as unknown as CanvasCtx
  act(() => setActiveCanvas(ctx))
  const marked = (flag: string) => [...edgeData].filter(([, d]) => d[flag]).map(([id]) => id)
  return { root, pane: root.querySelector('[data-testid="pane"]') as HTMLElement, marked }
}

function key(type: 'keydown' | 'keyup', k: string, target: EventTarget = window) {
  act(() => { target.dispatchEvent(new KeyboardEvent(type, { key: k, bubbles: true, cancelable: true })) })
}

function pointer(type: string, target: EventTarget, x: number, y: number): MouseEvent {
  const e = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 })
  act(() => { target.dispatchEvent(e) })
  return e
}

/** Press at a, move to b, release at b. */
function drag(target: EventTarget, a: [number, number], b: [number, number]) {
  pointer('pointerdown', target, ...a)
  pointer('pointermove', window, ...b)
  pointer('pointerup', window, ...b)
}

const wireIds = () => st().graph!.wires.map(w => w.id)

beforeEach(() => {
  act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
  render(<WireCut />)
})

afterEach(() => {
  cleanup()
  act(() => setActiveCanvas(null))
  document.body.innerHTML = ''
  st().discardEdits()
  vi.useRealTimers()
})

describe('Y + drag (WireCut)', () => {
  it('cuts every wire the line crosses, in one undo step', () => {
    const { root, pane } = mountCanvas()
    key('keydown', 'y')
    expect(root.classList.contains(CUTTING_CLASS)).toBe(true)
    pointer('pointerdown', pane, -20, 100)
    pointer('pointermove', window, 150, 100)
    expect(screen.getByTestId('nb-wire-cut')).toBeInTheDocument()
    pointer('pointerup', window, 150, 100)
    expect(screen.queryByTestId('nb-wire-cut')).toBeNull()
    expect(wireIds()).toEqual([])
    expect(st().past).toHaveLength(1)
    act(() => st().undo())
    expect(wireIds()).toEqual(['w1', 'w2'])
    key('keyup', 'y')
    expect(root.classList.contains(CUTTING_CLASS)).toBe(false)
  })

  it('cuts only the wires crossed', () => {
    const { pane } = mountCanvas()
    key('keydown', 'Y')
    drag(pane, [50, 100], [150, 100])
    expect(wireIds()).toEqual(['w1'])
  })

  it('a line that crosses nothing changes nothing', () => {
    const { pane } = mountCanvas()
    key('keydown', 'y')
    drag(pane, [20, 100], [80, 100])
    expect(wireIds()).toEqual(['w1', 'w2'])
    expect(st().past).toHaveLength(0)
  })

  it('releasing Y before the mouse cancels', () => {
    const { pane } = mountCanvas()
    key('keydown', 'y')
    pointer('pointerdown', pane, -20, 100)
    pointer('pointermove', window, 150, 100)
    key('keyup', 'y')
    expect(screen.queryByTestId('nb-wire-cut')).toBeNull()
    pointer('pointerup', window, 150, 100)
    expect(wireIds()).toEqual(['w1', 'w2'])
    expect(st().past).toHaveLength(0)
  })

  it('Esc cancels', () => {
    const { pane } = mountCanvas()
    key('keydown', 'y')
    pointer('pointerdown', pane, -20, 100)
    pointer('pointermove', window, 150, 100)
    key('keydown', 'Escape')
    pointer('pointerup', window, 150, 100)
    expect(wireIds()).toEqual(['w1', 'w2'])
  })

  it('while Y is held the press never reaches the canvas (no node drag, marquee or pan)', () => {
    const { root, pane } = mountCanvas()
    const seen = vi.fn()
    root.addEventListener('pointerdown', seen)
    root.addEventListener('mousedown', seen)
    key('keydown', 'y')
    const down = pointer('pointerdown', pane, -20, 100)
    const mdown = pointer('mousedown', pane, -20, 100)
    expect(seen).not.toHaveBeenCalled()
    expect(down.defaultPrevented).toBe(true)
    expect(mdown.defaultPrevented).toBe(true)
    pointer('pointerup', window, -20, 100)
    // Without Y the canvas gets its presses again.
    key('keyup', 'y')
    pointer('pointerdown', pane, -20, 100)
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('the click after a cut does not reach the canvas', () => {
    vi.useFakeTimers()
    const { root, pane } = mountCanvas()
    const clicked = vi.fn()
    root.addEventListener('click', clicked)
    key('keydown', 'y')
    drag(pane, [-20, 100], [150, 100])
    pointer('click', pane, 150, 100)
    expect(clicked).not.toHaveBeenCalled()
  })

  it('does nothing in a read-only graph', () => {
    const { root, pane } = mountCanvas({ editable: false })
    key('keydown', 'y')
    expect(root.classList.contains(CUTTING_CLASS)).toBe(false)
    const down = pointer('pointerdown', pane, -20, 100)
    expect(down.defaultPrevented).toBe(false)
    pointer('pointerup', window, 150, 100)
    expect(wireIds()).toEqual(['w1', 'w2'])
  })

  it('does nothing while typing in a field or the code editor', () => {
    const { pane, root } = mountCanvas()
    const input = document.createElement('input')
    document.body.appendChild(input)
    key('keydown', 'y', input)
    const editor = document.createElement('div')
    editor.className = 'nb-code-editor'
    const inner = document.createElement('div')
    editor.appendChild(inner)
    root.appendChild(editor)
    key('keydown', 'y', inner)
    expect(root.classList.contains(CUTTING_CLASS)).toBe(false)
    drag(pane, [-20, 100], [150, 100])
    expect(wireIds()).toEqual(['w1', 'w2'])
  })

  it('does nothing when the pointer is not over the canvas', () => {
    const { pane } = mountCanvas({ pointerOnCanvas: false })
    key('keydown', 'y')
    drag(pane, [-20, 100], [150, 100])
    expect(wireIds()).toEqual(['w1', 'w2'])
  })

  it('a press outside the canvas is left alone', () => {
    mountCanvas()
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    key('keydown', 'y')
    const down = pointer('pointerdown', outside, -20, 100)
    expect(down.defaultPrevented).toBe(false)
    pointer('pointerup', window, 150, 100)
    expect(wireIds()).toEqual(['w1', 'w2'])
  })
})

// ── Review fixes (F457) ─────────────────────────────────────────────────────

describe('Y + drag: review fixes', () => {
  it('tests the drawn line, not one snapped to the grid (LG-3)', () => {
    const { pane } = mountCanvas({ snap: 24 })
    key('keydown', 'y')
    // Snapped, both ends land on x = 0 and the line has no length.
    drag(pane, [-7, 100], [7, 100])
    expect(wireIds()).toEqual(['w2'])
  })

  it('a cut cancelled by Esc or by releasing Y lets no click through (LG-6)', () => {
    vi.useFakeTimers()
    const { root, pane } = mountCanvas()
    const clicked = vi.fn()
    root.addEventListener('click', clicked)
    key('keydown', 'y')
    pointer('pointerdown', pane, -20, 100)
    key('keydown', 'Escape')
    pointer('pointerup', window, 150, 100)
    pointer('click', pane, 150, 100)
    key('keyup', 'y')
    key('keydown', 'y')
    pointer('pointerdown', pane, -20, 100)
    key('keyup', 'y')
    pointer('pointerup', window, 150, 100)
    pointer('click', pane, 150, 100)
    expect(clicked).not.toHaveBeenCalled()
    expect(wireIds()).toEqual(['w1', 'w2'])
  })

  it('two quick cut strokes do not open the Tab menu (LG-7)', () => {
    const { root, pane } = mountCanvas()
    const dbl = vi.fn()
    root.addEventListener('dblclick', dbl)
    key('keydown', 'y')
    drag(pane, [20, 100], [80, 100])
    pointer('click', pane, 80, 100)
    drag(pane, [20, 110], [80, 110])
    pointer('click', pane, 80, 110)
    pointer('dblclick', pane, 80, 110)
    key('keyup', 'y')
    pointer('dblclick', pane, 80, 110)
    expect(dbl).not.toHaveBeenCalled()
  })

  it('Y pressed before the pointer reaches the canvas arms the cut there (LG-8)', () => {
    let over = false
    const { pane } = mountCanvas({ pointerOnCanvas: () => over })
    key('keydown', 'y')
    over = true
    drag(pane, [-20, 100], [150, 100])
    expect(wireIds()).toEqual([])
  })

  it('marks the wires the line crosses while dragging, and clears them after', () => {
    const { pane, marked } = mountCanvas()
    key('keydown', 'y')
    pointer('pointerdown', pane, -20, 100)
    pointer('pointermove', window, 50, 100)
    expect(marked('cutting')).toEqual(['w1'])
    pointer('pointermove', window, 150, 100)
    expect(marked('cutting')).toEqual(['w1', 'w2'])
    pointer('pointermove', window, 50, 100)
    expect(marked('cutting')).toEqual(['w1'])
    key('keydown', 'Escape')
    expect(marked('cutting')).toEqual([])
  })

  it('says how many wires were cut, and says so on a miss without an undo step', () => {
    const { pane } = mountCanvas()
    key('keydown', 'y')
    drag(pane, [-20, 100], [150, 100])
    expect(st().flash?.text).toBe('Cut 2 wires')
    act(() => st().undo())
    drag(pane, [50, 100], [150, 100])
    expect(st().flash?.text).toBe('Cut 1 wire')
    act(() => st().undo())
    drag(pane, [20, 100], [80, 100])
    expect(st().flash?.text).toBe('No wires crossed')
    expect(st().past).toHaveLength(0)
  })

  it('a click with Y held (a stroke shorter than the minimum) does nothing', () => {
    const { pane } = mountCanvas()
    key('keydown', 'y')
    act(() => st().showFlash('before'))
    drag(pane, [-2, 100], [2, 100])
    expect(wireIds()).toEqual(['w1', 'w2'])
    expect(st().past).toHaveLength(0)
    expect(st().flash?.text).toBe('before')
  })

  it('focus moving into a text field cancels the cut and disarms Y', () => {
    const { root, pane } = mountCanvas()
    const input = document.createElement('input')
    document.body.appendChild(input)
    key('keydown', 'y')
    pointer('pointerdown', pane, -20, 100)
    pointer('pointermove', window, 150, 100)
    act(() => { input.focus() })
    expect(root.classList.contains(CUTTING_CLASS)).toBe(false)
    expect(screen.queryByTestId('nb-wire-cut')).toBeNull()
    pointer('pointerup', window, 150, 100)
    expect(wireIds()).toEqual(['w1', 'w2'])
  })

  it('the cut line is not drawn like an error wire', async () => {
    const nodeFs = await import(/* @vite-ignore */ ['node', 'fs'].join(':')) as { readFileSync(p: string, enc: string): string }
    const cwd = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
    const css = nodeFs.readFileSync(`${cwd}/src/features/nodebuilder/edges/wireOps.css`, 'utf8')
    const rules = css.match(/\.nb-wire-cut [^{]*\{[^}]*\}/g) ?? []
    expect(rules.length).toBeGreaterThan(0)
    expect(rules.join('\n')).not.toContain('--nb-wire-invalid')
  })
})

// ── Wire ends take the pointer only on the hovered wire (F457) ──────────────

describe('reconnect anchors', () => {
  it('only the hovered wire’s end anchors take the pointer', async () => {
    // jsdom does not hit-test, so the rule itself is checked. Without it a
    // later wire's anchor sits over an earlier wire at a shared port and
    // steals its click (and Delete removes the wrong wire).
    // Vitest leaves CSS imports empty, so the file is read from disk (the
    // module name is built at run time so the app's type check skips it).
    const nodeFs = await import(/* @vite-ignore */ ['node', 'fs'].join(':')) as { readFileSync(p: string, enc: string): string }
    const cwd = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
    const css = nodeFs.readFileSync(`${cwd}/src/features/nodebuilder/edges/wireOps.css`, 'utf8').replace(/\s+/g, ' ')
    expect(css).toContain('@media (hover: hover) { .nodebuilder-root .react-flow__edge:not(:hover) .react-flow__edgeupdater { pointer-events: none; } }')
  })
})
