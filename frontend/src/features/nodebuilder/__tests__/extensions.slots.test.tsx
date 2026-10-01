/**
 * Layout slots and the NodeBuilder frame (F435 W3 pre-step 3.0): entries
 * draw in order, a broken entry does not blank the builder, NodeBuilder
 * draws all seven slots in every mode, slot components reach the session
 * and Run through useBuilder(), and the cook state reaches the store's
 * status slice for the status bar.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect } from 'react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

const CLEAN_DIAG = {
  diagnostics: [] as unknown[], byNode: {}, errorCount: 0, warningCount: 0,
  pending: false, offline: false, offlineDetail: null as string | null, hasResult: true,
}

vi.mock('../useDiagnostics', () => ({
  useDiagnostics: () => CLEAN_DIAG,
  useDiagnosticsController: vi.fn(),
  setServerDiagnostics: vi.fn(),
  retryValidation: vi.fn().mockResolvedValue(undefined),
  getDiagnosticsView: () => CLEAN_DIAG,
}))
vi.mock('../DiagnosticsPopover', () => ({
  DiagnosticsPopover: (p: { open: boolean }) => (p.open ? <div data-testid="nb-diag-popover" /> : null),
}))
vi.mock('../Canvas', () => ({ default: () => <div data-testid="canvas-stub" /> }))
vi.mock('../../../api/nodebuilder', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilder')>()
  return { ...orig, fetchGraphBacktest: vi.fn(), fetchAutoRender: vi.fn() }
})
vi.mock('../../../api/graphs', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/graphs')>()
  return {
    ...orig,
    listGraphs: vi.fn().mockResolvedValue([]),
    getGraph: vi.fn(),
    createGraph: vi.fn(),
    saveGraph: vi.fn(),
    deleteGraph: vi.fn(),
    seedLegacyGraphs: vi.fn(),
  }
})

import { fetchGraphBacktest } from '../../../api/nodebuilder'
import NodeBuilder from '../NodeBuilder'
import { listSlot, registerSlot, Slot, SLOT_NAMES, useBuilder, type SlotName } from '../slots'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import { clearNotices } from '../notices'

function node(id: string, type: string, name: string): GraphNode {
  return { id, type, name, parent: null, params: {}, position: [0, 0], display: false, bypass: false }
}

function smallGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: { n_t: node('n_t', 'ticker', 'aapl'), n_e: node('n_e', 'entry', 'entry') },
  }
}

function renderNodeBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <NodeBuilder request={null} graphViewActive />
    </QueryClientProvider>,
  )
}

const offs: Array<() => void> = []

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  vi.mocked(fetchGraphBacktest).mockReset()
  useNodeBuilderStore.getState().discardEdits()
  useNodeBuilderStore.setState({ cook: IDLE_COOK })
})

afterEach(() => {
  cleanup()
  for (const off of offs.splice(0)) off()
})

describe('slot registry', () => {
  it('orders entries by order, keeps registration order on ties, and replaces by id', () => {
    const A = () => <span>a</span>
    const B = () => <span>b</span>
    const C = () => <span>c</span>
    offs.push(registerSlot('statusBar', 't.b', B, 20))
    offs.push(registerSlot('statusBar', 't.a', A, 10))
    offs.push(registerSlot('statusBar', 't.c', C, 20))
    expect(listSlot('statusBar').map(e => e.id)).toEqual(['t.a', 't.b', 't.c'])
    const A2 = () => <span>a2</span>
    offs.push(registerSlot('statusBar', 't.a', A2, 30))
    expect(listSlot('statusBar').map(e => e.id)).toEqual(['t.b', 't.c', 't.a'])
  })

  it('Slot draws the entries in a range, and nothing for an empty slot', () => {
    offs.push(registerSlot('toolbarRight', 't.reset', () => <button>Reset view</button>, 10))
    offs.push(registerSlot('toolbarRight', 't.panel', () => <button>Inspector</button>, 90))
    const { container } = render(
      <>
        <div data-testid="early"><Slot name="toolbarRight" maxOrder={49} /></div>
        <div data-testid="late"><Slot name="toolbarRight" minOrder={50} /></div>
        <div data-testid="empty"><Slot name="bottomPanel" style={{ padding: 1 }} /></div>
      </>,
    )
    expect(screen.getByTestId('early')).toHaveTextContent('Reset view')
    expect(screen.getByTestId('early')).not.toHaveTextContent('Inspector')
    expect(screen.getByTestId('late')).toHaveTextContent('Inspector')
    expect(screen.getByTestId('empty').childElementCount).toBe(0)
    expect(container.querySelector('[data-nb-slot]')).toBeNull()
  })

  it('a slot component that throws is dropped and logged; the rest still draw', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Broken(): never { throw new Error('boom') }
    offs.push(registerSlot('overlays', 't.broken', Broken, 1))
    offs.push(registerSlot('overlays', 't.fine', () => <div data-testid="fine" />, 2))
    render(<Slot name="overlays" />)
    expect(screen.getByTestId('fine')).toBeInTheDocument()
    err.mockRestore()
  })
})

describe('slot rendering and overrides (W3 review fixes)', () => {
  it('a host re-render does not re-render slot components (IP-1)', () => {
    const rendered = vi.fn()
    const Probe = () => { rendered(); return <span>probe</span> }
    offs.push(registerSlot('bottomPanel', 't.probe', Probe))
    const Host = ({ n }: { n: number }) => <div data-n={n}><Slot name="bottomPanel" /></div>
    const { rerender } = render(<Host n={0} />)
    expect(rendered).toHaveBeenCalledTimes(1)
    for (let i = 1; i <= 5; i++) rerender(<Host n={i} />)
    expect(rendered).toHaveBeenCalledTimes(1)
  })

  it('removing an override brings the replaced entry back (EA-12)', () => {
    const A = () => <span>a</span>
    const B = () => <span>b</span>
    offs.push(registerSlot('dialogs', 't.same', A, 10))
    const off = registerSlot('dialogs', 't.same', B, 10)
    expect(listSlot('dialogs').find(e => e.id === 't.same')!.Component).toBe(B)
    off()
    expect(listSlot('dialogs').find(e => e.id === 't.same')!.Component).toBe(A)
    off() // a second call changes nothing
    expect(listSlot('dialogs').find(e => e.id === 't.same')!.Component).toBe(A)
  })
})

describe('NodeBuilder frame', () => {
  it('the browser menu never shows inside the builder, except in text fields (UX-05)', () => {
    renderNodeBuilder()
    const root = document.querySelector('[data-nb-builder]') as HTMLElement
    const toolbar = screen.getByTestId('nb-toolbar')
    const evt = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    toolbar.dispatchEvent(evt)
    expect(evt.defaultPrevented).toBe(true)
    const input = document.createElement('input')
    root.appendChild(input)
    const inField = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    input.dispatchEvent(inField)
    expect(inField.defaultPrevented).toBe(false)
    input.remove()
  })

  function registerAll() {
    for (const name of SLOT_NAMES) {
      const Probe = () => <div data-testid={`slot-${name}`} />
      offs.push(registerSlot(name as SlotName, `t.${name}`, Probe))
    }
  }

  it('draws every slot with nothing loaded', () => {
    registerAll()
    renderNodeBuilder()
    for (const name of SLOT_NAMES) expect(screen.getByTestId(`slot-${name}`)).toBeInTheDocument()
    expect(screen.getByTestId('nb-toolbar')).toBeInTheDocument()
  })

  it('draws every slot while editing, with the canvas in the column', () => {
    registerAll()
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    renderNodeBuilder()
    for (const name of SLOT_NAMES) expect(screen.getByTestId(`slot-${name}`)).toBeInTheDocument()
    expect(screen.getByTestId('canvas-stub')).toBeInTheDocument()
    // The Inspector slot sits beside the column that holds the toolbar and canvas.
    const right = screen.getByTestId('slot-rightPanel')
    const column = screen.getByTestId('canvas-stub').closest('[data-nb-column]')
    expect(column).not.toBeNull()
    expect(column!.contains(right)).toBe(false)
    expect(column!.contains(screen.getByTestId('slot-bottomPanel'))).toBe(true)
  })

  it('slot components reach Run and the diagnostics list through useBuilder()', async () => {
    const seen: { builder: ReturnType<typeof useBuilder> } = { builder: null }
    function Probe() {
      const b = useBuilder()
      useEffect(() => { seen.builder = b })
      return <button type="button" data-testid="probe-diag" onClick={e => b!.openDiagnostics(e.currentTarget)} />
    }
    offs.push(registerSlot('statusBar', 't.probe', Probe))
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    vi.mocked(fetchGraphBacktest).mockResolvedValue({
      summary: { num_trades: 0 }, trades: [], equity_curve: [],
    } as unknown as Awaited<ReturnType<typeof fetchGraphBacktest>>)
    renderNodeBuilder()
    const builder = seen.builder
    expect(builder).not.toBeNull()
    expect(typeof builder!.session.save).toBe('function')
    fireEvent.click(screen.getByTestId('probe-diag'))
    expect(screen.getByTestId('nb-diag-popover')).toBeInTheDocument()
    await act(async () => { builder!.runBacktest() })
    expect(fetchGraphBacktest).toHaveBeenCalledTimes(1)
  })
})

describe('cook state in the status slice', () => {
  it('goes cooking, then cooked, on a successful run', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    type Result = Awaited<ReturnType<typeof fetchGraphBacktest>>
    let resolve: (v: Result) => void = () => {}
    vi.mocked(fetchGraphBacktest).mockReturnValue(new Promise<Result>(r => { resolve = r }))
    renderNodeBuilder()
    await act(async () => fireEvent.click(screen.getByTestId('nb-btn-run')))
    expect(useNodeBuilderStore.getState().cook.phase).toBe('cooking')
    expect(useNodeBuilderStore.getState().cook.startedAt).not.toBeNull()
    await act(async () => resolve({ summary: { num_trades: 0 }, trades: [], equity_curve: [] } as unknown as Result))
    const cook = useNodeBuilderStore.getState().cook
    expect(cook.phase).toBe('cooked')
    expect(cook.endedAt).not.toBeNull()
    expect(cook.stale).toBe(false)
  })

  it('goes failed with the node the server names', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    vi.mocked(fetchGraphBacktest).mockRejectedValue({
      response: { status: 400, data: { detail: 'Entry is not connected.', node_id: 'n_e', code: 'missing_input' } },
    })
    renderNodeBuilder()
    await act(async () => fireEvent.click(screen.getByTestId('nb-btn-run')))
    const cook = useNodeBuilderStore.getState().cook
    expect(cook.phase).toBe('failed')
    expect(cook.failedNodeId).toBe('n_e')
  })
})
