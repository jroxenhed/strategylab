/**
 * Graph toolbar tests (F435 W1 item 1.F, surface S01, plus the S05 wiring
 * in NodeBuilder): Run is disabled with errors ("Fix N errors to run"),
 * the dirty dot and rev, the read-only view has no Save, Save on an
 * untitled graph asks for a name, and a Run 400 with diagnostics hands
 * them to the diagnostics store.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

const CLEAN_DIAG = {
  diagnostics: [] as unknown[], byNode: {}, errorCount: 0, warningCount: 0,
  pending: false, offline: false, offlineDetail: null as string | null, hasResult: true,
}
// A tiny external store, so a change re-renders the (memoized) NodeBuilder.
const diagState = vi.hoisted(() => {
  const listeners = new Set<() => void>()
  const state = {
    value: {} as typeof CLEAN_DIAG,
    set(v: typeof CLEAN_DIAG) {
      state.value = v
      listeners.forEach(l => l())
    },
    subscribe(l: () => void) {
      listeners.add(l)
      return () => { listeners.delete(l) }
    },
  }
  return state
})

vi.mock('../useDiagnostics', async () => {
  const { useSyncExternalStore } = await import('react')
  return {
    useDiagnostics: () => useSyncExternalStore(diagState.subscribe, () => diagState.value),
    useDiagnosticsController: vi.fn(),
    setServerDiagnostics: vi.fn(),
    retryValidation: vi.fn().mockResolvedValue(undefined),
    getDiagnosticsView: () => diagState.value,
  }
})
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
import { saveGraph, type GraphEnvelope } from '../../../api/graphs'
import { retryValidation, setServerDiagnostics } from '../useDiagnostics'
import NodeBuilder from '../NodeBuilder'
import GraphToolbar, { type GraphToolbarProps } from '../GraphToolbar'
import { diagnosticsLabel, modKeyCap, runDisabledReason } from '../graphText'
import { useNodeBuilderStore } from '../store'
import { clearNotices, pushNotice } from '../notices'

function node(id: string, type: string, name: string): GraphNode {
  return { id, type, name, parent: null, params: {}, position: [0, 0], display: false, bypass: false }
}

function smallGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: { n_t: node('n_t', 'ticker', 'aapl'), n_e: node('n_e', 'entry', 'entry') },
  }
}

function toolbarProps(over: Partial<GraphToolbarProps> = {}): GraphToolbarProps {
  const fn = () => vi.fn()
  return {
    mode: 'edit',
    name: 'alpha',
    rev: 4,
    dirty: false,
    hasNodes: true,
    errorCount: 0,
    warningCount: 0,
    onRun: fn(),
    onStop: fn(),
    onSave: fn(),
    onNew: fn(),
    onOpen: fn(),
    onSaveAs: fn(),
    onRename: fn(),
    onRenameInline: vi.fn().mockResolvedValue(null),
    onDuplicate: fn(),
    onExport: fn(),
    onImport: fn(),
    onDelete: fn(),
    onDiagnosticsClick: fn(),
    ...over,
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

/** Change what the mocked useDiagnostics returns, re-rendering subscribers. */
function setDiag(v: typeof CLEAN_DIAG) {
  act(() => diagState.set(v))
}

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  diagState.value = { ...CLEAN_DIAG }
  vi.mocked(setServerDiagnostics).mockClear()
  vi.mocked(retryValidation).mockClear()
  vi.mocked(fetchGraphBacktest).mockReset()
  useNodeBuilderStore.getState().discardEdits()
})

afterEach(() => cleanup())

describe('GraphToolbar', () => {
  it('words the Run reasons', () => {
    expect(runDisabledReason(1, true)).toBe('Fix 1 error to run')
    expect(runDisabledReason(3, true)).toBe('Fix 3 errors to run')
    expect(runDisabledReason(0, false)).toBe('Add a Ticker and an Output to run')
    expect(runDisabledReason(0, true)).toBeNull()
    expect(diagnosticsLabel(1, 2)).toBe('1 error, 2 warnings')
  })

  it('disables Run with one error and shows the chip', () => {
    const props = toolbarProps({ errorCount: 1, warningCount: 2 })
    render(<GraphToolbar {...props} />)
    const run = screen.getByTestId('nb-btn-run')
    expect(run).toBeDisabled()
    expect(run).toHaveAttribute('title', 'Fix 1 error to run')
    const chip = screen.getByTestId('nb-diag-chip')
    expect(chip).toHaveAttribute('aria-label', '1 error, 2 warnings')
    fireEvent.click(chip)
    expect(props.onDiagnosticsClick).toHaveBeenCalledWith(chip)
  })

  it('hides the chip when clean and enables Run', () => {
    render(<GraphToolbar {...toolbarProps()} />)
    expect(screen.queryByTestId('nb-diag-chip')).toBeNull()
    expect(screen.getByTestId('nb-btn-run')).not.toBeDisabled()
  })

  it('shows the dirty dot and rev; Save is disabled when clean', () => {
    const { rerender } = render(<GraphToolbar {...toolbarProps()} />)
    expect(screen.queryByTestId('nb-dirty-dot')).toBeNull()
    expect(screen.getByTestId('nb-rev')).toHaveTextContent('rev 4')
    expect(screen.getByTestId('nb-btn-save')).toBeDisabled()
    expect(screen.getByTestId('nb-btn-save')).toHaveAttribute('title', 'No changes to save')
    rerender(<GraphToolbar {...toolbarProps({ dirty: true })} />)
    expect(screen.getByTestId('nb-dirty-dot')).toHaveAttribute('aria-label', 'Unsaved changes')
    expect(screen.getByTestId('nb-btn-save')).not.toBeDisabled()
  })

  it('reads "untitled" with no rev for a new graph', () => {
    render(<GraphToolbar {...toolbarProps({ name: null, rev: null })} />)
    expect(screen.getByTestId('nb-graph-name')).toHaveTextContent('untitled')
    expect(screen.queryByTestId('nb-rev')).toBeNull()
    expect(screen.getByTestId('nb-btn-save')).not.toBeDisabled()
  })

  it('the read-only view has Edit this graph and no Save or Run', () => {
    render(<GraphToolbar {...toolbarProps({ mode: 'view', onEditThisGraph: vi.fn() })} />)
    expect(screen.queryByTestId('nb-btn-save')).toBeNull()
    expect(screen.queryByTestId('nb-btn-run')).toBeNull()
    expect(screen.getByRole('button', { name: 'Edit this graph' })).toBeInTheDocument()
  })

  it('opens the overflow menu with every graph action', async () => {
    const props = toolbarProps()
    render(<GraphToolbar {...props} />)
    fireEvent.click(screen.getByTestId('nb-btn-more'))
    const menu = await screen.findByTestId('nb-menu-more')
    const labels = Array.from(menu.querySelectorAll('[role="menuitem"]')).map(b => b.textContent)
    expect(labels).toEqual([
      'New',
      `Open…${modKeyCap('O')}`,
      'Save as…',
      'Rename…',
      'Duplicate',
      'Export JSON',
      'Import JSON…',
      'Delete…',
    ])
    fireEvent.click(screen.getByTestId('nb-menu-rename'))
    expect(props.onRename).toHaveBeenCalled()
    expect(screen.queryByTestId('nb-menu-more')).toBeNull()
  })

  it('renames inline from the name crumb; Esc reverts', async () => {
    const props = toolbarProps()
    render(<GraphToolbar {...props} />)
    fireEvent.click(screen.getByTestId('nb-graph-name'))
    const input = screen.getByTestId('nb-graph-name-input')
    fireEvent.change(input, { target: { value: 'beta' } })
    await act(async () => fireEvent.keyDown(input, { key: 'Enter' }))
    expect(props.onRenameInline).toHaveBeenCalledWith('beta')
    expect(screen.queryByTestId('nb-graph-name-input')).toBeNull()
    fireEvent.click(screen.getByTestId('nb-graph-name'))
    fireEvent.keyDown(screen.getByTestId('nb-graph-name-input'), { key: 'Escape' })
    expect(screen.queryByTestId('nb-graph-name-input')).toBeNull()
    expect(props.onRenameInline).toHaveBeenCalledTimes(1)
  })
})

describe('toolbar order (S01, 5.F)', () => {
  it('Spawn bots sits between Run and Save; the panel toggles stay after the menu', async () => {
    // Canvas (which auto-loads plugins/) is a stub here: load the spawn plugin itself.
    await import('../plugins/spawnBots')
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    renderNodeBuilder()
    const order = ['nb-btn-run', 'nb-btn-spawn', 'nb-btn-save', 'nb-btn-more'].map(id => screen.getByTestId(id))
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING, order[i].dataset.testid).toBeTruthy()
    }
  })
})

describe('NodeBuilder diagnostics wiring', () => {
  it('disables Run with "Fix 1 error to run" when validate reports one error', () => {
    diagState.value = { ...diagState.value, errorCount: 1, warningCount: 0 }
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    renderNodeBuilder()
    const run = screen.getByTestId('nb-btn-run')
    expect(run).toBeDisabled()
    expect(run).toHaveAttribute('title', 'Fix 1 error to run')
    fireEvent.click(screen.getByTestId('nb-diag-chip'))
    expect(screen.getByTestId('nb-diag-popover')).toBeInTheDocument()
    fireEvent.click(run)
    expect(fetchGraphBacktest).not.toHaveBeenCalled()
  })

  it('hands the diagnostics of a Run 400 to the diagnostics store', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    const diag = {
      node_id: 'n_e', path: '/entry', severity: 'error', code: 'missing_input', message: 'Entry is not connected.',
      param: null, port: null, line: null, col: null, end_line: null, end_col: null,
    }
    vi.mocked(fetchGraphBacktest).mockRejectedValue({
      response: {
        status: 400,
        data: { detail: 'Entry is not connected.', node_id: 'n_e', code: 'missing_input', diagnostics: [diag] },
      },
    })
    renderNodeBuilder()
    await act(async () => fireEvent.click(screen.getByTestId('nb-btn-run')))
    expect(setServerDiagnostics).toHaveBeenCalledWith([diag])
    // The run error has its own key, so a pushed save error cannot hide it (FC-10).
    const banner = await screen.findByTestId('nb-banner-run_error')
    expect(banner).toHaveTextContent('Entry is not connected.')
    // The node link selects the node.
    fireEvent.click(screen.getByRole('button', { name: 'entry' }))
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('n_e')
  })

  it('Save on an untitled graph opens the name dialog', async () => {
    useNodeBuilderStore.getState().newGraph()
    renderNodeBuilder()
    fireEvent.click(screen.getByTestId('nb-btn-save'))
    expect(await screen.findByTestId('nb-name-input')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Save as')
  })

  it('Stop drops the run in flight', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    let resolve!: (v: unknown) => void
    vi.mocked(fetchGraphBacktest).mockReturnValue(new Promise(r => { resolve = r }) as never)
    renderNodeBuilder()
    fireEvent.click(screen.getByTestId('nb-btn-run'))
    const signal = vi.mocked(fetchGraphBacktest).mock.calls[0][1]
    expect(signal).toBeInstanceOf(AbortSignal)
    expect(signal!.aborted).toBe(false)
    const stop = await screen.findByRole('button', { name: /Stop/ })
    fireEvent.click(stop)
    // Stop aborts the HTTP request, not only the late result (S01).
    expect(signal!.aborted).toBe(true)
    await waitFor(() => expect(screen.getByTestId('nb-btn-run')).toHaveTextContent('Run backtest'))
    await act(async () => resolve({ summary: {}, trades: [], equity_curve: [], baseline_curve: [] }))
    expect(screen.getByTestId('nb-btn-run')).toHaveTextContent('Run backtest')
  })

  it('the chip shows … only while the first validate result is pending (hasResult)', () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    diagState.value = { ...CLEAN_DIAG, pending: true, hasResult: false }
    renderNodeBuilder()
    expect(screen.getByTestId('nb-diag-chip')).toHaveAccessibleName('Checking the graph')
    // A later validate is pending, but a result exists: no `…` flicker.
    setDiag({ ...CLEAN_DIAG, pending: true, hasResult: true })
    expect(screen.queryByTestId('nb-diag-chip')).toBeNull()
  })

  it('validate offline shows one S07 banner per failure streak, with Retry', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    renderNodeBuilder()
    expect(screen.queryByTestId('nb-banner-validate_offline')).toBeNull()

    setDiag({ ...CLEAN_DIAG, offline: true, offlineDetail: 'Network Error' })
    const banner = await screen.findByTestId('nb-banner-validate_offline')
    expect(banner).toHaveTextContent('Could not reach the server to validate. Network Error')
    expect(banner).toHaveAttribute('role', 'alert')
    await act(async () => fireEvent.click(screen.getByTestId('nb-validate-retry')))
    expect(retryValidation).toHaveBeenCalledTimes(1)

    // Dismissed within the same streak: it does not come back.
    fireEvent.click(within(banner).getByTestId('nb-banner-dismiss'))
    expect(screen.queryByTestId('nb-banner-validate_offline')).toBeNull()
    setDiag({ ...CLEAN_DIAG, offline: true, offlineDetail: 'HTTP 502' })
    expect(screen.queryByTestId('nb-banner-validate_offline')).toBeNull()

    // The streak ends, then a new one starts: a new banner.
    setDiag({ ...CLEAN_DIAG })
    setDiag({ ...CLEAN_DIAG, offline: true, offlineDetail: 'HTTP 502' })
    expect(await screen.findByTestId('nb-banner-validate_offline')).toHaveTextContent('HTTP 502')

    // A successful validate resolves the open banner.
    setDiag({ ...CLEAN_DIAG })
    expect(screen.queryByTestId('nb-banner-validate_offline')).toBeNull()
  })
})

describe('W1 fix pass: toolbar', () => {
  it('a menu item hands focus back to ⋯ before it runs (UX-03)', async () => {
    let focusedAtSelect: Element | null = null
    const props = toolbarProps({ onRename: vi.fn(() => { focusedAtSelect = document.activeElement }) })
    render(<GraphToolbar {...props} />)
    fireEvent.click(screen.getByTestId('nb-btn-more'))
    await screen.findByTestId('nb-menu-more')
    fireEvent.click(screen.getByTestId('nb-menu-rename'))
    expect(props.onRename).toHaveBeenCalled()
    expect(focusedAtSelect).toBe(screen.getByTestId('nb-btn-more'))
  })

  it('leaving the name field keeps the typed name; an invalid one reverts (UX-15)', async () => {
    const props = toolbarProps()
    render(<GraphToolbar {...props} />)
    fireEvent.click(screen.getByTestId('nb-graph-name'))
    fireEvent.change(screen.getByTestId('nb-graph-name-input'), { target: { value: 'beta' } })
    await act(async () => fireEvent.blur(screen.getByTestId('nb-graph-name-input')))
    expect(props.onRenameInline).toHaveBeenCalledWith('beta')
    expect(screen.queryByTestId('nb-graph-name-input')).toBeNull()
    fireEvent.click(screen.getByTestId('nb-graph-name'))
    fireEvent.change(screen.getByTestId('nb-graph-name-input'), { target: { value: '   ' } })
    await act(async () => fireEvent.blur(screen.getByTestId('nb-graph-name-input')))
    expect(screen.queryByTestId('nb-graph-name-input')).toBeNull()
    expect(props.onRenameInline).toHaveBeenCalledTimes(1)
  })

  it('Cmd+S in the name field commits the name like Enter', async () => {
    const props = toolbarProps()
    render(<GraphToolbar {...props} />)
    fireEvent.click(screen.getByTestId('nb-graph-name'))
    const input = screen.getByTestId('nb-graph-name-input')
    fireEvent.change(input, { target: { value: 'gamma' } })
    let notPrevented = true
    await act(async () => { notPrevented = fireEvent.keyDown(input, { key: 's', metaKey: true }) })
    expect(notPrevented).toBe(false)
    expect(props.onRenameInline).toHaveBeenCalledWith('gamma')
  })

  it('key caps follow the platform (UX-11)', () => {
    expect(modKeyCap('S', true)).toBe('⌘S')
    expect(modKeyCap('S', false)).toBe('Ctrl+S')
    expect(modKeyCap('↵', false)).toBe('Ctrl+↵')
    render(<GraphToolbar {...toolbarProps({ dirty: true })} />)
    expect(screen.getByTestId('nb-btn-save')).toHaveAttribute('title', `Save (${modKeyCap('S')})`)
  })
})

/** The NodeBuilder root, made "on screen" for jsdom (no layout there). */
function builderRoot(): HTMLElement {
  const root = document.querySelector('[data-nb-builder]') as HTMLElement
  root.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  return root
}

function savedEnvelope(id: string, body: { rev: number; graph: Graph }): GraphEnvelope {
  return { id, rev: body.rev + 1, name: 'alpha', description: '', created_at: '', updated_at: '', graph: body.graph }
}

describe('W1 fix pass: global keys and banners in NodeBuilder', () => {
  it('Cmd+O opens the Graph Browser with nothing loaded; the browser gets neither Cmd+O nor Cmd+S (FC-3, UX-02)', async () => {
    renderNodeBuilder()
    builderRoot()
    ;(document.activeElement as HTMLElement | null)?.blur()
    let notPrevented = true
    act(() => { notPrevented = fireEvent.keyDown(document.body, { key: 'o', metaKey: true }) })
    expect(notPrevented).toBe(false)
    const browser = await screen.findByTestId('nb-graph-browser')
    // Inside the (modal) browser, Cmd+S runs nothing but is still stopped.
    act(() => { notPrevented = fireEvent.keyDown(screen.getByTestId('nb-browser-search'), { key: 's', metaKey: true }) })
    expect(notPrevented).toBe(false)
    expect(saveGraph).not.toHaveBeenCalled()
    expect(browser).toBeInTheDocument()
  })

  it('Cmd+S in a param field commits the field first, then saves (FC-4)', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    vi.mocked(saveGraph).mockReset()
    vi.mocked(saveGraph).mockImplementation(async (id: string, body: { rev: number; graph: Graph }) => savedEnvelope(id, body))
    renderNodeBuilder()
    const root = builderRoot()
    // A field that commits on blur, as ParamRow does.
    const input = document.createElement('input')
    input.addEventListener('blur', () => useNodeBuilderStore.getState().updateNodeParams('n_t', { symbol: 'MSFT' }))
    root.appendChild(input)
    input.focus()
    let notPrevented = true
    await act(async () => { notPrevented = fireEvent.keyDown(input, { key: 's', metaKey: true }) })
    expect(notPrevented).toBe(false)
    expect(saveGraph).toHaveBeenCalledTimes(1)
    expect(vi.mocked(saveGraph).mock.calls[0][1].graph.nodes.n_t.params.symbol).toBe('MSFT')
    input.remove()
  })

  it('Cmd+Enter in a param field runs the graph with the committed value (FC-4)', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    vi.mocked(fetchGraphBacktest).mockReturnValue(new Promise(() => {}) as never)
    renderNodeBuilder()
    const root = builderRoot()
    const input = document.createElement('input')
    input.addEventListener('blur', () => useNodeBuilderStore.getState().updateNodeParams('n_t', { symbol: 'NVDA' }))
    root.appendChild(input)
    input.focus()
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter', metaKey: true }) })
    expect(fetchGraphBacktest).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetchGraphBacktest).mock.calls[0][0].graph.nodes.n_t.params.symbol).toBe('NVDA')
    input.remove()
  })

  it('Run takes the data source from the sidebar, never from a stale Ticker source param (MD-07)', async () => {
    const g = smallGraph()
    g.nodes.n_t = { ...g.nodes.n_t, params: { symbol: 'AAPL', interval: '1d', source: 'ibkr' } }
    useNodeBuilderStore.getState().openGraph(g, { id: 'g_1', rev: 2, name: 'alpha' })
    vi.mocked(fetchGraphBacktest).mockReturnValue(new Promise(() => {}) as never)
    renderNodeBuilder()
    const root = builderRoot()
    const input = document.createElement('input')
    root.appendChild(input)
    input.focus()
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter', metaKey: true }) })
    expect(fetchGraphBacktest).toHaveBeenCalledTimes(1)
    // request is null here, so the sidebar default applies.
    expect(vi.mocked(fetchGraphBacktest).mock.calls[0][0].source).toBe('yahoo')
    input.remove()
  })

  it('a Run 400 landing after an edit leaves diagnostics alone; its banner survives a save error (FC-1, FC-10)', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    let reject!: (e: unknown) => void
    vi.mocked(fetchGraphBacktest).mockReturnValue(new Promise((_, r) => { reject = r }) as never)
    renderNodeBuilder()
    fireEvent.click(screen.getByTestId('nb-btn-run'))
    act(() => useNodeBuilderStore.getState().updateNodeParams('n_t', { symbol: 'MSFT' }))
    await act(async () => reject({
      response: { status: 400, data: { detail: 'Entry is not connected.', node_id: 'n_e', diagnostics: [{ node_id: 'n_e', message: 'x' }] } },
    }))
    expect(setServerDiagnostics).not.toHaveBeenCalled()
    expect(await screen.findByTestId('nb-banner-run_error')).toHaveTextContent('Entry is not connected.')
    act(() => pushNotice({ key: 'server_error', severity: 'error', text: 'Could not save: down' }))
    expect(screen.getByTestId('nb-banner-run_error')).toBeInTheDocument()
    expect(screen.getByTestId('nb-banner-server_error')).toBeInTheDocument()
  })

  it('a badly typed graph now answers 400 graph_invalid (was 422): detail shown, diagnostics handed over', async () => {
    useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g_1', rev: 2, name: 'alpha' })
    const diag = {
      node_id: null, path: null, severity: 'error', code: 'graph_invalid', message: 'nodes.n_t.position: Input should be a valid list',
      param: null, port: null, line: null, col: null, end_line: null, end_col: null,
    }
    vi.mocked(fetchGraphBacktest).mockRejectedValue({
      response: { status: 400, data: { detail: diag.message, node_id: null, code: 'graph_invalid', diagnostics: [diag] } },
    })
    renderNodeBuilder()
    await act(async () => fireEvent.click(screen.getByTestId('nb-btn-run')))
    expect(setServerDiagnostics).toHaveBeenCalledWith([diag])
    expect(await screen.findByTestId('nb-banner-run_error')).toHaveTextContent('nodes.n_t.position: Input should be a valid list')
  })

  it('an empty graph shows the foundation 7 empty state with Open and an Add Ticker card (UX-14)', async () => {
    useNodeBuilderStore.getState().newGraph()
    renderNodeBuilder()
    const empty = screen.getByTestId('nb-empty-graph')
    expect(empty).toHaveTextContent('Press Tab to add a node')
    expect(empty).toHaveTextContent('Start with a Ticker, then indicators, comparisons, and an Output Group')
    fireEvent.click(screen.getByTestId('nb-empty-ticker'))
    expect(Object.values(useNodeBuilderStore.getState().graph!.nodes).map(n => n.type)).toEqual(['ticker'])
    expect(screen.queryByTestId('nb-empty-graph')).toBeNull()
    act(() => useNodeBuilderStore.getState().newGraph())
    fireEvent.click(screen.getByTestId('nb-empty-open'))
    expect(await screen.findByTestId('nb-graph-browser')).toBeInTheDocument()
  })

  it('unsupported nodes use the S07 copy, and no regime notice is left (UX-09, W5)', () => {
    const g = smallGraph()
    g.nodes.n_x = node('n_x', 'bogus_type', 'weird')
    useNodeBuilderStore.getState().openGraph(g, { id: 'g_1', rev: 2, name: 'alpha' })
    renderNodeBuilder()
    const banner = screen.getByTestId('nb-banner-unsupported_nodes')
    expect(banner).toHaveTextContent('Unsupported in graphs: bogus_type. The graph cannot run until these are replaced.')
    fireEvent.click(within(banner).getByRole('button', { name: 'bogus_type' }))
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('n_x')
    // W5 runs regime in the graph: the old "Regime moved out" banner is gone.
    expect(screen.queryByTestId('nb-banner-regime_removed')).toBeNull()
  })
})
