/**
 * Flags, the unsupported-node card and port hover text as drawn on the
 * canvas (F435 W3 item 3.B, specs S16, S13, S08).
 *
 * Mounts the real Canvas on the store graph, so a flag click goes through
 * the store and the node redraws from the committed graph.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, fireEvent, cleanup, act, screen } from '@testing-library/react'
import Canvas from '../Canvas'
import { runCommand } from '../commands'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import { HOVER_TIP_DELAY_MS, endHoverTip, shownHoverTip } from '../nodes/hoverTip'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'

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
  endHoverTip()
  vi.useRealTimers()
  resetDiagnostics()
  useNodeBuilderStore.getState().discardEdits()
})

function node(id: string, type: string, y: number, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [0, y], display: false, bypass: false, ...extra }
}

function makeGraph(extra: GraphNode[] = []): Graph {
  const nodes = [
    node('t', 'ticker', 0, { params: { symbol: 'AAPL', interval: '1d' } }),
    node('a', 'sma', 120, { params: { period: 20 }, display: true }),
    node('b', 'rsi', 240, { params: { period: 14 } }),
    node('e', 'entry', 360),
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
      { id: 'w2', from: 'a', to: 'b', from_port: 'out', to_port: 'in0' },
      { id: 'w3', from: 'b', to: 'e', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

/** The canvas on the live store graph, as NodeBuilder draws it. */
function Live() {
  const graph = useNodeBuilderStore(s => s.graph)
  return graph ? <Canvas graph={graph} /> : null
}

function mount(g: Graph = makeGraph(), opts: { readOnly?: boolean } = {}) {
  if (opts.readOnly) {
    // openGraph always makes an editable copy; the read-only view sets it directly.
    useNodeBuilderStore.setState({ graph: { ...g, readOnly: true } })
  } else {
    useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
  }
  const utils = render(
    <div className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      <Live />
    </div>,
  )
  const q = (id: string) => utils.container.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
  return { ...utils, q }
}

const state = () => useNodeBuilderStore.getState()

describe('flag dots (S16)', () => {
  it('draws display and bypass dots with aria-pressed, by node kind', () => {
    const { q } = mount()
    expect(q('nb-flag-display-a')?.getAttribute('aria-pressed')).toBe('true')
    expect(q('nb-flag-display-b')?.getAttribute('aria-pressed')).toBe('false')
    expect(q('nb-flag-bypass-b')?.getAttribute('aria-pressed')).toBe('false')
    expect(q('nb-flag-display-b')?.getAttribute('aria-label')).toBe('Display flag')
    expect(q('nb-flag-bypass-b')?.getAttribute('aria-label')).toBe('Bypass flag')
    // Not tab stops on the canvas.
    expect(q('nb-flag-bypass-b')?.tabIndex).toBe(-1)
    // A Ticker: display only. A terminal: neither.
    expect(q('nb-flag-display-t')).not.toBeNull()
    expect(q('nb-flag-bypass-t')).toBeNull()
    expect(q('nb-flag-display-e')).toBeNull()
    expect(q('nb-flag-bypass-e')).toBeNull()
  })

  it('clicking display on B moves it from A in one step; the lit dot adds none', () => {
    const { q } = mount()
    act(() => { fireEvent.click(q('nb-flag-display-b')!) })
    expect(q('nb-flag-display-b')?.getAttribute('aria-pressed')).toBe('true')
    expect(q('nb-flag-display-a')?.getAttribute('aria-pressed')).toBe('false')
    expect(state().past).toHaveLength(1)
    act(() => { fireEvent.click(q('nb-flag-display-b')!) })
    expect(state().past).toHaveLength(1)
    expect(q('nb-flag-display-b')?.getAttribute('aria-pressed')).toBe('true')
  })

  it('a dot click never changes the selection', () => {
    const { q } = mount()
    act(() => { state().setSelection({ nodeIds: ['a'] }) })
    act(() => {
      fireEvent.pointerDown(q('nb-flag-bypass-b')!)
      fireEvent.mouseDown(q('nb-flag-bypass-b')!)
      fireEvent.click(q('nb-flag-bypass-b')!)
    })
    expect(state().graph!.nodes.b.bypass).toBe(true)
    expect(state().selectedNodeIds).toEqual(['a'])
  })

  it('a bypassed node reads `bypassed`, dims its body to 0.45 and draws the amber bar', () => {
    const { q } = mount()
    expect(q('nb-bypass-bar-b')).toBeNull()
    act(() => { fireEvent.click(q('nb-flag-bypass-b')!) })
    expect(q('nb-node-type-b')?.textContent).toBe('bypassed')
    expect(getComputedStyle(q('nb-node-body-b')!).opacity).toBe('0.45')
    expect(q('nb-bypass-bar-b')).not.toBeNull()
    expect(q('nb-flag-bypass-b')?.getAttribute('aria-pressed')).toBe('true')
    // Undo brings the node back.
    act(() => { state().undo() })
    expect(q('nb-node-type-b')).toBeNull()
    expect(q('nb-bypass-bar-b')).toBeNull()
  })

  it('the B key toggles bypass on the selection through the canvas', () => {
    const { container } = mount()
    act(() => { state().setSelection({ nodeIds: ['a', 'b'], primary: 'b' }) })
    // jsdom draws nothing; the canvas only takes keys while it is on screen.
    const canvas = container.querySelector('.nodebuilder-root .nodebuilder-root') as HTMLElement
    canvas.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
    act(() => { fireEvent.keyDown(document.body, { key: 'b' }) })
    expect(state().graph!.nodes.a.bypass).toBe(true)
    expect(state().graph!.nodes.b.bypass).toBe(true)
    expect(state().past).toHaveLength(1)
  })

  it('a read-only graph draws no dots but keeps the flag looks', () => {
    const g = makeGraph()
    g.nodes.b = { ...g.nodes.b, bypass: true }
    const { q } = mount(g, { readOnly: true })
    expect(q('nb-flag-display-a')).toBeNull()
    expect(q('nb-flag-bypass-b')).toBeNull()
    expect(q('nb-node-type-b')?.textContent).toBe('bypassed')
  })

  it('hovering a dot shows its key after 400 ms', () => {
    vi.useFakeTimers()
    const { q } = mount()
    act(() => { fireEvent.pointerEnter(q('nb-flag-display-a')!) })
    expect(shownHoverTip()).toBeNull()
    act(() => { vi.advanceTimersByTime(HOVER_TIP_DELAY_MS) })
    expect(shownHoverTip()).toBe('Display (D) · already shown')
    act(() => { fireEvent.pointerLeave(q('nb-flag-display-a')!) })
    expect(shownHoverTip()).toBeNull()
    act(() => { fireEvent.pointerEnter(q('nb-flag-bypass-b')!); vi.advanceTimersByTime(HOVER_TIP_DELAY_MS) })
    expect(shownHoverTip()).toBe('Bypass (B)')
  })
})

describe('port hover text (S08)', () => {
  it('shows after 400 ms, hides on leave, and keeps the output title for later', () => {
    vi.useFakeTimers()
    const { q } = mount()
    const out = q('nb-port-a-out')!
    const title = out.getAttribute('title')
    expect(title).toMatch(/^out/)
    act(() => { fireEvent.pointerEnter(out) })
    // The native tooltip is moved aside while ours is pending.
    expect(out.getAttribute('title')).toBeNull()
    act(() => { vi.advanceTimersByTime(HOVER_TIP_DELAY_MS - 1) })
    expect(shownHoverTip()).toBeNull()
    act(() => { vi.advanceTimersByTime(1) })
    expect(shownHoverTip()).toBe(title)
    act(() => { fireEvent.pointerLeave(out) })
    expect(shownHoverTip()).toBeNull()
    expect(out.getAttribute('title')).toBe(title)

    const input = q('nb-port-b-in0')!
    act(() => { fireEvent.pointerEnter(input); vi.advanceTimersByTime(HOVER_TIP_DELAY_MS) })
    expect(shownHoverTip()).toMatch(/^source/)
    act(() => { fireEvent.pointerDown(input) })
    expect(shownHoverTip()).toBeNull()
  })
})

describe('unsupported or unknown node (S13)', () => {
  const unknown = () =>
    makeGraph([node('x', 'stochastic_rising', 480, { params: { k: 14, smooth: 'yes' } })])

  it('draws the inert card: ? glyph, type in amber, params as rows, a red badge, no flags', () => {
    const g = unknown()
    g.wires.push({ id: 'w4', from: 'b', to: 'x', from_port: 'out', to_port: 'in0' })
    const { q } = mount(g)
    const card = q('nb-node-unsupported-x')!
    expect(card).not.toBeNull()
    expect(card.getAttribute('aria-label')).toBe('unsupported node x of type stochastic_rising')
    expect(card.querySelector('.nb-unsupported__glyph')?.textContent).toBe('?')
    const type = card.querySelector('.nb-unsupported__type') as HTMLElement
    expect(type.textContent).toBe('stochastic_rising')
    expect(type.getAttribute('title')).toBe('Not supported yet')
    const rows = [...card.querySelectorAll('.nb-unsupported__row')].map(r => r.textContent)
    expect(rows).toEqual(['k14', 'smoothyes'])
    expect(card.textContent).toContain('unsupported · replace this node')
    // Before /validate answers, the badge already says why.
    expect(q('nb-diag-badge-x')?.getAttribute('aria-label')).toBe('1 error: Unsupported in graphs: stochastic_rising')
    expect(q('nb-flag-display-x')).toBeNull()
    expect(q('nb-flag-bypass-x')).toBeNull()
    // The wire into it keeps its port; no spare port invites a new one.
    expect(q('nb-port-x-in0')).not.toBeNull()
    expect(q('nb-port-x-in1')).toBeNull()
    expect(q('nb-port-x-in0')?.classList.contains('connectable')).toBe(false)
  })

  it('Replace with… swaps it for a real node in place, moving its wires, in one undo step', () => {
    const g = unknown()
    g.wires.push(
      { id: 'w4', from: 'b', to: 'x', from_port: 'out', to_port: 'in0' },
      { id: 'w5', from: 'x', to: 'e2', from_port: 'out', to_port: 'in0' },
    )
    g.nodes.e2 = node('e2', 'exit', 600)
    mount(g)
    act(() => { state().setSelection({ nodeIds: ['x'] }) })
    act(() => { expect(runCommand('nodes.replace')).toBe(true) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: 'rising' } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    const s = state()
    expect(s.graph!.nodes.x).toBeUndefined()
    const fresh = Object.values(s.graph!.nodes).find(n => n.type === 'rising')!
    expect(fresh.position).toEqual([0, 480])
    expect(s.graph!.wires.filter(w => w.to === fresh.id || w.from === fresh.id).map(w => [w.from, w.to, w.to_port]))
      .toEqual([['b', fresh.id, 'in0'], [fresh.id, 'e2', 'in0']])
    expect(s.past).toHaveLength(1)
    expect(s.selectedNodeId).toBe(fresh.id)
    act(() => { s.undo() })
    expect(state().graph!.nodes.x).toBeDefined()
  })

  it('uses the server message once it arrives', () => {
    const { q } = mount(unknown())
    const d: Diagnostic = {
      node_id: 'x', path: '/x', severity: 'error', code: 'unknown_node_type',
      message: 'Unknown node type: stochastic_rising', param: null, port: null,
      line: null, col: null, end_line: null, end_col: null,
    }
    act(() => { setServerDiagnostics([d]) })
    expect(q('nb-diag-badge-x')?.getAttribute('aria-label')).toBe('1 error: Unknown node type: stochastic_rising')
  })

  it('a known node the server marks unsupported uses the card too; read-only hides the hint', () => {
    const { q, unmount } = mount(unknown(), { readOnly: true })
    expect(q('nb-node-unsupported-x')?.textContent).not.toContain('replace this node')
    unmount()
    const m = mount()
    expect(m.q('nb-node-unsupported-b')).toBeNull()
    act(() => {
      setServerDiagnostics([{
        node_id: 'b', path: '/b', severity: 'error', code: 'unsupported_node',
        message: 'Unsupported in graphs: rsi [long]', param: null, port: null,
        line: null, col: null, end_line: null, end_col: null,
      }])
    })
    expect(m.q('nb-node-unsupported-b')).not.toBeNull()
  })
})
