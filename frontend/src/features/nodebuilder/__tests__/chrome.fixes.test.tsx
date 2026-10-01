/**
 * W3 review fixes (fork B): context menu, status bar, shortcut overlay,
 * minimap, hover tips, note/box key handling, header rename, unsupported
 * counts. Ids: IP-9, UX-03, UX-07, UX-10, UX-11, UX-15, UX-16, UX-17,
 * UX-22, FC-4, FC-7.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, renderHook, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { createElement, useRef, type ReactNode } from 'react'
import '../canvasPlugins'
import Canvas from '../Canvas'
import StatusBar from '../StatusBar'
import { ContextMenuHost } from '../ContextMenu'
import { closeContextMenu, openContextMenu } from '../contextMenuModel'
import { formatChord, shortcutGroups } from '../shortcutList'
import { minimapNodeColorWith } from '../minimapColors'
import { HOVER_TIP_DELAY_MS, HOVER_TIP_WATCH_MS, endHoverTip, shownHoverTip, startHoverTip } from '../nodes/hoverTip'
import { useGlobalKeys } from '../commands/useGlobalKeys'
import { registerCommand, type Command } from '../commands'
import { mountCanvas, publishScreenGraph, setActiveCanvas } from '../screen'
import { useInspectorUi, resetInspectorUi } from '../inspector/state'
import {
  clientUnsupportedDiagnostics,
  getDiagnosticsView,
  resetDiagnostics,
  useDiagnosticsController,
} from '../useDiagnostics'
import { BuilderContext, type BuilderApi } from '../slots'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import type { CanvasCtx } from '../canvasPlugins'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import type { Node as RFNode } from '@xyflow/react'

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

const initialState = useNodeBuilderStore.getState()
const offs: Array<() => void> = []

afterEach(() => {
  cleanup()
  for (const off of offs.splice(0)) off()
  closeContextMenu()
  endHoverTip()
  vi.useRealTimers()
  resetDiagnostics()
  resetInspectorUi()
  setActiveCanvas(null)
  useNodeBuilderStore.getState().stopAnnotationEdit()
  useNodeBuilderStore.setState(initialState, true)
})

function node(id: string, type: string, y: number, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [0, y], display: false, bypass: false, ...extra }
}

function makeGraph(extra: GraphNode[] = []): Graph {
  const nodes = [
    node('t', 'ticker', 0, { params: { symbol: 'AAPL', interval: '1d' } }),
    node('a', 'rsi', 150, { params: { period: 14 } }),
    node('e', 'entry', 300),
    ...extra,
  ]
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires: [
      { id: 'w1', from: 't', to: 'a', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'a', to: 'e', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

const st = () => useNodeBuilderStore.getState()

function BuilderRoot({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useGlobalKeys(ref)
  // eslint-disable-next-line react-hooks/refs
  return createElement('div', { ref, className: 'nodebuilder-root', style: { width: 800, height: 600 } }, children)
}

function LiveCanvas() {
  const graph = useNodeBuilderStore(s => s.graph)
  return graph ? createElement(Canvas, { graph }) : null
}

function mount(graph: Graph) {
  st().openGraph(graph, { id: null, rev: 0, name: 'test' })
  const utils = render(createElement(BuilderRoot, null, createElement(LiveCanvas)))
  for (const el of utils.container.querySelectorAll<HTMLElement>('.nodebuilder-root')) {
    el.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  }
  return utils
}

function fakeCanvas(graph: Graph | null = null, editable = true): CanvasCtx {
  return {
    graph: () => graph,
    editable: () => editable,
    focus: () => {},
    container: () => null,
    pointer: () => ({ x: 0, y: 0 }),
    pointerOnCanvas: () => true,
    openTabMenu: () => false,
    deleteSelection: () => false,
  } as unknown as CanvasCtx
}

function renderBar() {
  const builder = {
    session: { save: vi.fn() },
    runBacktest: vi.fn(),
    stopBacktest: vi.fn(),
    openDiagnostics: vi.fn(),
  } as unknown as BuilderApi
  return render(
    <BuilderContext.Provider value={builder}>
      <div className="nodebuilder-root"><StatusBar /></div>
    </BuilderContext.Provider>,
  )
}

// ── Key caps and the `?` overlay (UX-15, UX-16) ─────────────────────────────

describe('shared key-cap formatter and overlay groups', () => {
  it('formats per platform: Mac glyphs, Ctrl+ elsewhere; Menu key and Delete named', () => {
    expect(formatChord('mod+z', true)).toBe('⌘Z')
    expect(formatChord('mod+z', false)).toBe('Ctrl+Z')
    expect(formatChord('shift+delete', true)).toBe('⇧⌫')
    expect(formatChord('shift+delete', false)).toBe('Shift+Del')
    expect(formatChord('contextmenu', true)).toBe('Menu')
    expect(formatChord('shift+f10', false)).toBe('Shift+F10')
  })

  it('files clipboard, nodes, layout and cook commands under the S21 groups', () => {
    const groups = shortcutGroups('', undefined, true)
    const titles = groups.map(g => g.title)
    for (const t of ['CLIPBOARD', 'NODES', 'LAYOUT', 'COOK']) expect(titles).not.toContain(t)
    const rowGroup = (id: string) => groups.find(g => g.rows.some(r => r.id === id))?.title
    expect(rowGroup('clipboard.copy')).toBe('EDIT')
    expect(rowGroup('layout.tidy')).toBe('VIEW')
    // The context-menu key reads `Menu`, not the raw key name.
    const ctxRow = groups.flatMap(g => g.rows).find(r => r.id === 'edit.contextMenu')
    if (ctxRow) expect(ctxRow.caps.join(' ')).not.toMatch(/contextmenu/)
  })
})

// ── Status bar (UX-11, FC-4) ────────────────────────────────────────────────

describe('status bar', () => {
  it('the cooking live region does not carry the ticking ms (UX-11)', () => {
    useNodeBuilderStore.setState({ cook: { ...IDLE_COOK, phase: 'cooking', startedAt: Date.now() } })
    renderBar()
    const seg = screen.getByTestId('nb-status-cook')
    expect(seg).toHaveAttribute('role', 'status')
    const ms = screen.getByTestId('nb-status-cook-ms')
    expect(ms).toHaveAttribute('aria-hidden', 'true')
    expect(ms.textContent).toMatch(/ms$/)
  })

  it('a failed cook is a real button inside the live region (UX-11)', () => {
    useNodeBuilderStore.setState({ cook: { ...IDLE_COOK, phase: 'failed', failedNodeId: 'a', endedAt: Date.now() } })
    renderBar()
    const btn = screen.getByTestId('nb-status-cook')
    expect(btn.tagName).toBe('BUTTON')
    expect(btn).not.toHaveAttribute('role')
    expect(btn.parentElement).toHaveAttribute('role', 'status')
    expect(screen.getByRole('button', { name: /cook failed/ })).toBe(btn)
  })

  it('reads the read-only graph on screen reactively (FC-4)', () => {
    const view = { ...makeGraph(), readOnly: true }
    const canvas = fakeCanvas(view, false)
    renderBar()
    expect(screen.getByTestId('nb-status-saved')).toHaveTextContent('—')
    act(() => {
      mountCanvas(canvas)
      publishScreenGraph(canvas, view, false)
    })
    expect(screen.getByTestId('nb-status-saved')).toHaveTextContent('view')
    act(() => { useNodeBuilderStore.setState({ selectedNodeIds: ['a'], selectedNodeId: 'a' }) })
    expect(screen.getByTestId('nb-status-selection')).toHaveTextContent('a')
    // A new read-only graph with the node renamed shows at once.
    const next = { ...view, nodes: { ...view.nodes, a: { ...view.nodes.a, name: 'rsi_fast' } } }
    act(() => { publishScreenGraph(canvas, next, false) })
    expect(screen.getByTestId('nb-status-selection')).toHaveTextContent('rsi_fast')
  })
})

// ── Context menu (IP-9, UX-17) ──────────────────────────────────────────────

describe('context menu', () => {
  it('an open menu does not rebuild on a flash or a cook tick, but does on a selection (IP-9)', () => {
    st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const when = vi.fn(() => true)
    offs.push(registerCommand({ id: 'test.probe', label: 'Probe row', menu: 'pane', when, run: () => {} }))
    render(<div className="nodebuilder-root"><ContextMenuHost /></div>)
    act(() => openContextMenu({ kind: 'pane', screen: { x: 10, y: 10 }, flow: { x: 0, y: 0 }, canvas: fakeCanvas() }))
    expect(screen.getByTestId('nb-menu-item-test.probe')).toBeInTheDocument()
    const calls = when.mock.calls.length
    act(() => { st().showFlash('hello') })
    act(() => { st().setCook({ phase: 'cooking', startedAt: Date.now() }) })
    expect(when.mock.calls.length).toBe(calls)
    act(() => { st().setSelection({ nodeIds: ['a'], primary: 'a' }) })
    expect(when.mock.calls.length).toBeGreaterThan(calls)
  })

  it('a hover-opened submenu leaves focus in the menu; Right opens it with focus (UX-17)', () => {
    st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const sw = (id: string, label: string) => ({ id, label, menu: 'pane', submenu: 'Shade', swatch: '#123456', run: () => {} }) as Command
    offs.push(registerCommand(sw('test.shadeA', 'Shade A')), registerCommand(sw('test.shadeB', 'Shade B')))
    offs.push(registerCommand({ id: 'test.plain', label: 'Plain row', menu: 'pane', run: () => {} }))
    render(<div className="nodebuilder-root"><ContextMenuHost /></div>)
    act(() => openContextMenu({ kind: 'pane', screen: { x: 10, y: 10 }, flow: { x: 0, y: 0 }, canvas: fakeCanvas() }))
    const subRow = screen.getByTestId('nb-menu-sub-Shade')
    const plain = screen.getByTestId('nb-menu-item-test.plain')
    act(() => { plain.focus() })
    // Hover: the submenu opens, focus stays on the main list.
    act(() => { fireEvent.mouseEnter(subRow) })
    expect(screen.getByTestId('nb-menu-item-test.shadeA')).toBeInTheDocument()
    expect(document.activeElement).toBe(plain)
    // Keyboard: Right on the row opens it and moves focus in.
    act(() => { fireEvent.mouseEnter(plain) })
    act(() => { subRow.focus() })
    act(() => { fireEvent.keyDown(subRow, { key: 'ArrowRight' }) })
    const first = screen.getByTestId('nb-menu-item-test.shadeA')
    expect(document.activeElement).toBe(first)
    // Moving the mouse to another main row while focus is in the submenu:
    // focus goes to that row, never to the page body.
    act(() => { fireEvent.mouseEnter(plain) })
    expect(screen.queryByTestId('nb-menu-item-test.shadeA')).toBeNull()
    expect(document.activeElement).toBe(plain)
  })
})

// ── Minimap (UX-22) ─────────────────────────────────────────────────────────

describe('minimap colors', () => {
  it('unsupported ids are dim; others keep their category color', () => {
    const rf = (id: string, cat: string) => ({ id, type: 'indicator', position: { x: 0, y: 0 }, data: { catalog: { cat } } }) as RFNode
    const color = minimapNodeColorWith(new Set(['bad']))
    expect(color(rf('good', 'indicator'))).toBe('#34d399')
    expect(color(rf('bad', 'indicator'))).toBe('#7a8296')
  })
})

// ── Hover tip (FC-7) ────────────────────────────────────────────────────────

describe('hover tip', () => {
  it('hides when its element is removed, and on a key press', () => {
    vi.useFakeTimers()
    const el = document.createElement('span')
    document.body.appendChild(el)
    startHoverTip(el, 'in0 · close')
    vi.advanceTimersByTime(HOVER_TIP_DELAY_MS)
    expect(shownHoverTip()).toBe('in0 · close')
    el.remove()
    vi.advanceTimersByTime(HOVER_TIP_WATCH_MS)
    expect(shownHoverTip()).toBeNull()

    const el2 = document.createElement('span')
    document.body.appendChild(el2)
    startHoverTip(el2, 'tip two')
    vi.advanceTimersByTime(HOVER_TIP_DELAY_MS)
    expect(shownHoverTip()).toBe('tip two')
    fireEvent.keyDown(document.body, { key: 'Delete' })
    expect(shownHoverTip()).toBeNull()
    el2.remove()
  })
})

// ── Note and box label keys (UX-03) ─────────────────────────────────────────

describe('note and box label editors pass Cmd chords on', () => {
  function saveSpy() {
    const run = vi.fn()
    offs.push(registerCommand({ id: 'test.save', label: 'Save', keys: ['mod+s'], scope: 'global', inFields: true, run }))
    return run
  }

  it('Cmd+S in a note saves (and keeps the typed text); plain keys stay in the note', () => {
    const run = saveSpy()
    const bRun = vi.fn()
    offs.push(registerCommand({ id: 'test.b', label: 'B', keys: ['b'], run: bRun }))
    const graph = makeGraph()
    graph.annotations.notes = [{ id: 'note1', text: '', rect: [400, 0, 200, 88], color: 'amber', parent: null }]
    mount(graph)
    fireEvent.doubleClick(screen.getByTestId('nb-note-note1'))
    const textarea = screen.getByTestId('nb-note-textarea')
    fireEvent.keyDown(textarea, { key: 'b' })
    expect(bRun).not.toHaveBeenCalled()
    fireEvent.change(textarea, { target: { value: 'hello' } })
    const ev = new KeyboardEvent('keydown', { key: 's', metaKey: true, bubbles: true, cancelable: true })
    act(() => { textarea.dispatchEvent(ev) })
    expect(run).toHaveBeenCalledTimes(1)
    expect(ev.defaultPrevented).toBe(true)
    expect(st().graph!.annotations.notes[0].text).toBe('hello')
  })

  it('Cmd+Enter in a note ends the edit and does not run the global Cmd+Enter', () => {
    const run = vi.fn()
    offs.push(registerCommand({ id: 'test.cook', label: 'Cook', keys: ['mod+enter'], scope: 'global', inFields: true, run }))
    const graph = makeGraph()
    graph.annotations.notes = [{ id: 'note1', text: '', rect: [400, 0, 200, 88], color: 'amber', parent: null }]
    mount(graph)
    fireEvent.doubleClick(screen.getByTestId('nb-note-note1'))
    const textarea = screen.getByTestId('nb-note-textarea')
    fireEvent.change(textarea, { target: { value: 'done' } })
    act(() => { fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true }) })
    expect(run).not.toHaveBeenCalled()
    expect(screen.queryByTestId('nb-note-textarea')).toBeNull()
    expect(st().graph!.annotations.notes[0].text).toBe('done')
  })

  it('Cmd+S in a box label saves and keeps the label', () => {
    const run = saveSpy()
    const graph = makeGraph()
    graph.annotations.boxes = [{ id: 'box1', label: 'Regime', color: 'network', rect: [-24, -24, 224, 258], members: ['t'], parent: null }]
    mount(graph)
    fireEvent.doubleClick(screen.getByTestId('nb-box-label-box1'))
    const input = screen.getByLabelText('Box label')
    fireEvent.change(input, { target: { value: 'Trend' } })
    act(() => { fireEvent.keyDown(input, { key: 's', metaKey: true }) })
    expect(run).toHaveBeenCalledTimes(1)
    expect(st().graph!.annotations.boxes[0].label).toBe('Trend')
  })
})

// ── Header double-click rename (UX-07) ──────────────────────────────────────

describe('node header double-click', () => {
  it('selects the node and asks the Inspector to rename it', () => {
    mount(makeGraph())
    act(() => { fireEvent.doubleClick(screen.getByTestId('nb-node-header-a')) })
    expect(useInspectorUi.getState().renameRequest?.nodeId).toBe('a')
    expect(st().selectedNodeIds).toEqual(['a'])
    // Nothing was committed.
    expect(st().past).toHaveLength(0)
  })

  it('an unsupported node has no renaming header', () => {
    mount(makeGraph([node('m', 'mystery_type', 450)]))
    expect(screen.queryByTestId('nb-node-header-m')).toBeNull()
  })
})

// ── Unsupported nodes count before /validate (UX-10) ────────────────────────

describe('unsupported nodes in the diagnostics view', () => {
  it('counts an unknown node as an error before the server answers, once', () => {
    vi.useFakeTimers()
    const graph = makeGraph([node('m', 'mystery_type', 450)])
    expect(clientUnsupportedDiagnostics(graph).map(d => d.node_id)).toEqual(['m'])
    expect(clientUnsupportedDiagnostics({ ...graph, readOnly: true })).toEqual([])
    st().openGraph(graph, { id: null, rev: 0, name: 'test' })
    const { unmount } = renderHook(() => useDiagnosticsController())
    const view = getDiagnosticsView()
    expect(view.errorCount).toBe(1)
    expect(view.byNode.m?.[0].code).toBe('unknown_node_type')
    // Deleting the node clears it.
    act(() => { st().removeNodes(['m']) })
    expect(getDiagnosticsView().errorCount).toBe(0)
    unmount()
  })
})
