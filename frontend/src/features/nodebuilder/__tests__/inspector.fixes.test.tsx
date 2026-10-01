/**
 * Inspector review fixes (F435 W3 review pass): IP-2 (no re-render of the
 * node view on unrelated edits), IP-3 (no re-render per handle move),
 * UX-08 (overlay is session state, nodes keep it open), UX-09 (Delete wire
 * takes back the AND term), UX-12 (rows keep their button role), UX-14
 * (unsupported nodes are inert), UX-15 (platform key caps), UX-20 (Alt
 * step on an int), UX-21 (aria-controls, forced-open sections), UX-23
 * (Un-bypass all), FC-1 (Connect… to a node with no output), FC-4 (the
 * read-only graph on screen is reactive), FC-8 (shared int params).
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, within } from '@testing-library/react'
import type { Graph, GraphNode, ParamValue } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import Inspector from '../Inspector'
import { registerCommand, setActiveCanvas } from '../commands'
import type { CanvasCtx, TabMenuRequest } from '../canvasPlugins'
import { mountCanvas, publishScreenGraph } from '../screen'
import { listInspectorSections, registerInspectorSection } from '../inspector/sections'
import { flashParam, requestRename, resetInspectorUi, toggleInspector, useInspectorUi } from '../inspector/state'
import { resetDiagnostics } from '../useDiagnostics'

function node(id: string, type: string, params: Record<string, ParamValue> = {}, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false, ...extra }
}

function makeGraph(readOnly = false): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly,
    meta: {},
    nodes: {
      aapl: node('aapl', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      rsi: node('rsi', 'rsi', { period: 14, type: 'wilder', source: '@close' }),
      rsi2: node('rsi2', 'rsi', { period: 14, type: 'wilder', source: '@close' }),
    },
    wires: [{ id: 'w1', from: 'aapl', to: 'rsi', from_port: 'out', to_port: 'in0' }],
    annotations: { boxes: [], notes: [] },
  }
}

function load(graph: Graph = makeGraph()) {
  act(() => { useNodeBuilderStore.getState().openGraph(graph, { id: 'g_1', rev: 3, name: 'my graph' }) })
}

function selectNodes(...ids: string[]) {
  act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ids }) })
}

const g = () => useNodeBuilderStore.getState().graph!

function renderInspector(extra?: React.ReactNode) {
  return render(
    <div className="nodebuilder-root">
      <Inspector />
      {extra}
    </div>,
  )
}

function setAppWidth(w: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true })
}

function setPlatform(p: string) {
  Object.defineProperty(navigator, 'platform', { value: p, configurable: true })
}

/** A section that counts its renders. */
function countingSection(): { renders: () => number; cleanup: () => void } {
  let n = 0
  const cleanupFn = registerInspectorSection({
    id: 'probe',
    title: 'Probe',
    order: 99,
    Component: ({ node: nd }) => { n += 1; return <div data-testid="probe">{nd.name}</div> },
  })
  return { renders: () => n, cleanup: cleanupFn }
}

const cleanups: (() => void)[] = []

beforeEach(() => {
  setAppWidth(1600)
  setPlatform('')
  resetInspectorUi()
  resetDiagnostics()
  act(() => { useNodeBuilderStore.getState().newGraph() })
})

afterEach(() => {
  cleanup()
  for (const c of cleanups.splice(0)) c()
  setActiveCanvas(null)
  resetDiagnostics()
})

describe('IP-2 / IP-3: the node view re-renders only for its own node', () => {
  it('an edit to another node does not re-render the sections; an edit to this one does', () => {
    load()
    const probe = countingSection()
    cleanups.push(probe.cleanup)
    selectNodes('rsi')
    renderInspector()
    const before = probe.renders()
    expect(before).toBeGreaterThan(0)
    act(() => { useNodeBuilderStore.getState().moveNodes(['aapl'], [[40, 0]]) })
    act(() => { useNodeBuilderStore.getState().updateNodeParams('rsi2', { period: 30 }) })
    expect(probe.renders()).toBe(before)
    act(() => { useNodeBuilderStore.getState().updateNodeParams('rsi', { period: 20 }) })
    expect(probe.renders()).toBe(before + 1)
  })

  it('a handle drag writes the width directly: no section render per pointer move', () => {
    load()
    const probe = countingSection()
    cleanups.push(probe.cleanup)
    selectNodes('rsi')
    renderInspector()
    const panel = screen.getByTestId('nb-inspector')
    const handle = screen.getByTestId('nb-inspector-handle')
    const start = parseInt(panel.style.width, 10)
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    const before = probe.renders()
    for (let i = 1; i <= 10; i++) fireEvent.pointerMove(handle, { clientX: 1000 - i * 5, pointerId: 1 })
    expect(probe.renders()).toBe(before)
    expect(panel.style.width).toBe(`${start + 50}px`)
    expect(handle).toHaveAttribute('aria-valuenow', String(start + 50))
    fireEvent.pointerUp(handle, { clientX: 950, pointerId: 1 })
    expect(useInspectorUi.getState().width).toBe(start + 50)
    expect(panel.style.width).toBe(`${start + 50}px`)
  })
})

describe('UX-08: the overlay Inspector', () => {
  it('is session state: dismissing it keeps the docked panel open for a wide window', () => {
    setAppWidth(1000)
    load()
    renderInspector(<button data-testid="outside">x</button>)
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    act(() => { toggleInspector(true) })
    expect(screen.getByTestId('nb-inspector')).toHaveClass('nb-insp--overlay')
    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    expect(useInspectorUi.getState().open).toBe(true)
    act(() => { setAppWidth(1600); window.dispatchEvent(new Event('resize')) })
    expect(screen.getByTestId('nb-inspector')).not.toHaveClass('nb-insp--overlay')
  })

  it('a press on a node keeps it open; a press on the empty pane or Esc closes it', () => {
    setAppWidth(1000)
    load()
    renderInspector(
      <>
        <div className="react-flow__node" data-testid="rf-node"><span data-testid="rf-node-inner">n</span></div>
        <div className="react-flow__pane" data-testid="rf-pane" />
      </>,
    )
    act(() => { toggleInspector(true) })
    fireEvent.pointerDown(screen.getByTestId('rf-node-inner'))
    expect(screen.getByTestId('nb-inspector')).toBeInTheDocument()
    fireEvent.pointerDown(screen.getByTestId('rf-pane'))
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    act(() => { toggleInspector(true) })
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
  })
})

describe('UX-09: Delete wire in the Inspector', () => {
  it('takes back the AND term, is one undo step, clears the selection, and sits at the bottom', () => {
    const graph: Graph = {
      ...makeGraph(),
      nodes: {
        rsi: node('rsi', 'rsi', { period: 14, out: '@rsi' }),
        and: node('and', 'and', { terms: ['@rsi'], out: '@and' }),
      },
      wires: [{ id: 'w9', from: 'rsi', to: 'and', from_port: 'out', to_port: 'in0' }],
    }
    load(graph)
    act(() => { useNodeBuilderStore.getState().setSelection({ wireIds: ['w9'] }) })
    renderInspector()
    const del = screen.getByTestId('nb-inspector-delete-wire')
    // Below every section, not inside the Wire section.
    expect(screen.getByTestId('nb-inspector-section-wire').contains(del)).toBe(false)
    const past = useNodeBuilderStore.getState().past.length
    fireEvent.click(del)
    expect(g().wires).toHaveLength(0)
    expect(g().nodes.and.params.terms).toEqual([])
    expect(useNodeBuilderStore.getState().past.length).toBe(past + 1)
    expect(useNodeBuilderStore.getState().selectedWireIds).toEqual([])
    act(() => { useNodeBuilderStore.getState().undo() })
    expect(g().wires).toHaveLength(1)
    expect(g().nodes.and.params.terms).toEqual(['@rsi'])
  })
})

describe('UX-12: list rows keep their button role', () => {
  it('legend rows are buttons inside list items', () => {
    load()
    renderInspector()
    const btn = screen.getByRole('button', { name: 'Select all Indicators (2)' })
    expect(btn.parentElement).toHaveAttribute('role', 'listitem')
    expect(btn).not.toHaveAttribute('role')
  })
})

describe('UX-14: an unsupported node is inert in the Inspector', () => {
  it('no rename, no param fields, no flags', () => {
    load({ ...makeGraph(), nodes: { ...makeGraph().nodes, m: node('m', 'mystery_type', { foo: 1 }) } })
    selectNodes('m')
    renderInspector()
    expect(screen.getByTestId('nb-inspector-name')).toBeDisabled()
    expect(within(screen.getByTestId('nb-inspector-section-parameters')).queryByRole('textbox')).toBeNull()
    expect(screen.queryByTestId('nb-inspector-flag-display')).toBeNull()
    expect(screen.queryByTestId('nb-inspector-flag-bypass')).toBeNull()
  })
})

describe('UX-15: key caps follow the platform', () => {
  it('Ctrl+Z and Backspace off a Mac, ⌘Z and ⌫ on a Mac', () => {
    load()
    setPlatform('Win32')
    renderInspector()
    expect(screen.getByTestId('nb-keys-row-9')).toHaveTextContent('Ctrl+Z')
    expect(screen.getByTestId('nb-keys-row-10')).toHaveTextContent('Backspace')
    expect(screen.getByTestId('nb-keys-row-4')).toHaveTextContent('Shift+click')
    cleanup()
    setPlatform('MacIntel')
    renderInspector()
    expect(screen.getByTestId('nb-keys-row-9')).toHaveTextContent('⌘Z')
    expect(screen.getByTestId('nb-keys-row-10')).toHaveTextContent('⌫')
  })

  it('bulk caps too', () => {
    load()
    setPlatform('Win32')
    cleanups.push(registerCommand({ id: 'network.collapseTest', label: 'Collapse', keys: ['shift+c'], run: () => {} }))
    selectNodes('rsi', 'aapl')
    renderInspector()
    const bulk = screen.getByTestId('nb-inspector-section-bulk')
    expect(within(bulk).getByText('Collapse into subnet').closest('button')).toHaveTextContent('Shift+C')
  })
})

describe('UX-20: Alt+arrow on an int param', () => {
  it('keeps the plain step', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const input = within(screen.getByTestId('nb-inspector-param-period')).getByRole('textbox') as HTMLInputElement
    act(() => { input.focus() })
    fireEvent.keyDown(input, { key: 'ArrowUp', altKey: true })
    expect(input.value).toBe('15')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(g().nodes.rsi.params.period).toBe(15)
  })
})

describe('UX-21: section headers', () => {
  it('point aria-controls at the body only while it is rendered', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const head = within(screen.getByTestId('nb-inspector-section-stream')).getByRole('button', { name: /Stream/ })
    const id = head.getAttribute('aria-controls')
    expect(id).toBeTruthy()
    expect(document.getElementById(id!)).not.toBeNull()
    fireEvent.click(head)
    expect(head).not.toHaveAttribute('aria-controls')
  })

  it('a forced-open section ignores a header click', () => {
    renderInspector()
    const head = within(screen.getByTestId('nb-inspector-section-keys')).getByRole('button', { name: /Keys/ })
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(useInspectorUi.getState().sections.keys).toBeUndefined()
  })
})

describe('UX-23: Bypass all', () => {
  it('says Un-bypass all when the primary is bypassed', () => {
    load()
    cleanups.push(registerCommand({ id: 'flags.toggleBypass', label: 'Bypass', keys: ['b'], checked: () => true, run: () => {} }))
    selectNodes('rsi', 'aapl')
    renderInspector()
    const bulk = screen.getByTestId('nb-inspector-section-bulk')
    expect(within(bulk).getByText('Un-bypass all')).toBeInTheDocument()
  })
})

describe('FC-1: Connect… to a node with no output', () => {
  it('flashes why and leaves the graph unwired instead of throwing', () => {
    load()
    let request: TabMenuRequest | null = null
    const fake = {
      openTabMenu: (r: TabMenuRequest) => { request = r; return true },
      graph: () => g(),
      focus: () => {},
      store: useNodeBuilderStore,
    } as unknown as CanvasCtx
    setActiveCanvas(fake)
    selectNodes('rsi2')
    renderInspector()
    fireEvent.click(within(screen.getByTestId('nb-inspector-port-in0')).getByText('Connect…'))
    expect(request).not.toBeNull()
    act(() => { useNodeBuilderStore.getState().commit('add', gr => ({ ...gr, nodes: { ...gr.nodes, e1: node('e1', 'entry') } })) })
    const wires = g().wires.length
    expect(() => act(() => { request!.onCreate!('e1', fake) })).not.toThrow()
    expect(g().wires).toHaveLength(wires)
    expect(useNodeBuilderStore.getState().flash?.text).toBe('Those two nodes cannot be wired')
    // A node with an output is wired.
    act(() => { useNodeBuilderStore.getState().commit('add', gr => ({ ...gr, nodes: { ...gr.nodes, t2: node('t2', 'ticker', { symbol: 'MSFT', interval: '1d' }) } })) })
    act(() => { request!.onCreate!('t2', fake) })
    expect(g().wires.some(w => w.from === 't2' && w.to === 'rsi2')).toBe(true)
  })
})

describe('FC-4: the read-only graph on screen is reactive', () => {
  it('a new graph published by the canvas re-renders the Inspector without a store write', () => {
    act(() => { useNodeBuilderStore.setState({ graph: null }) })
    const a = makeGraph(true)
    const b: Graph = { ...makeGraph(true), nodes: { ...makeGraph(true).nodes, rsi: node('rsi', 'rsi', { period: 9 }, { name: 'rsi_b' }) } }
    const ctx = { graph: () => a, focus: () => {} } as unknown as CanvasCtx
    act(() => { cleanups.push(mountCanvas(ctx)); publishScreenGraph(ctx, a, false) })
    act(() => { useNodeBuilderStore.getState().select('rsi') })
    renderInspector()
    expect(screen.getByTestId('nb-inspector-name')).toHaveTextContent('rsi')
    act(() => { publishScreenGraph(ctx, b, false) })
    expect(screen.getByTestId('nb-inspector-name')).toHaveTextContent('rsi_b')
    expect(screen.getByTestId('nb-inspector-path')).toHaveTextContent('/rsi_b')
  })
})

describe('FC-8: shared params coerce like the single-node row', () => {
  it('rounds an int and clamps to min and max', () => {
    load()
    selectNodes('rsi', 'rsi2')
    renderInspector()
    const field = screen.getByTestId('nb-inspector-shared-period') as HTMLInputElement
    const type = (text: string) => {
      act(() => { field.focus() })
      fireEvent.change(field, { target: { value: text } })
      fireEvent.keyDown(field, { key: 'Enter' })
    }
    type('2.5')
    expect(g().nodes.rsi.params.period).toBe(3)
    expect(g().nodes.rsi2.params.period).toBe(3)
    type('1')
    expect(g().nodes.rsi.params.period).toBe(2)
    type('9999')
    expect(g().nodes.rsi2.params.period).toBe(500)
  })
})

describe('EA-12: Inspector sections override and restore', () => {
  it('removing an override brings the built-in section back', () => {
    const builtIn = listInspectorSections().find(x => x.id === 'stream')!
    const remove = registerInspectorSection({ id: 'stream', title: 'Stream 2', order: 30, Component: () => null })
    expect(listInspectorSections().find(x => x.id === 'stream')?.title).toBe('Stream 2')
    remove()
    expect(listInspectorSections().find(x => x.id === 'stream')).toBe(builtIn)
    remove()
    expect(listInspectorSections().find(x => x.id === 'stream')).toBe(builtIn)
  })
})

describe('EA-5: Inspector requests follow the graph', () => {
  it('a delete drops a flash and a rename request for that node; undo and load too', () => {
    load()
    act(() => { flashParam('rsi', 'period'); requestRename('rsi') })
    act(() => { useNodeBuilderStore.getState().removeNodes(['rsi']) })
    expect(useInspectorUi.getState().flash).toBeNull()
    expect(useInspectorUi.getState().renameRequest).toBeNull()
    // Another node's request survives an unrelated commit.
    act(() => { flashParam('rsi2', 'period') })
    act(() => { useNodeBuilderStore.getState().moveNodes(['aapl'], [[10, 0]]) })
    expect(useInspectorUi.getState().flash?.nodeId).toBe('rsi2')
    act(() => { useNodeBuilderStore.getState().newGraph() })
    expect(useInspectorUi.getState().flash).toBeNull()
  })
})
