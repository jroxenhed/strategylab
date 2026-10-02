/**
 * Inspector tests (F435 W3 item 3.A, specs S14 and S15).
 *
 * Covers: the node view (header, path, every catalog param, the same value
 * as the on-node row); rename through rename_node semantics (path and name
 * follow, one undo step, invalid names refused, Esc, F2); multi-select
 * (count, Bulk, Shared parameters in one commit); the wire view; the empty
 * view (legend, flags, keys, legend click selects); the P toggle, width
 * persistence and the resize handle; diagnostics (section rows, count, a
 * row click flashes and focuses the param, the badge opens the Inspector
 * on Diagnostics); read-only graphs; changed-from-default reset, arrow
 * stepping and the slider; registration in the slots and the command
 * registry; and operations/rename.ts.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, within } from '@testing-library/react'
import type { Graph, GraphNode, ParamValue } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { useNodeBuilderStore } from '../store'
import Inspector from '../Inspector'
import { ParamRow } from '../nodes/ParamRow'
import { DiagnosticBadge } from '../nodes/DiagnosticBadge'
import { registerCommand, runCommand, listCommands, setActiveCanvas } from '../commands'
import type { CanvasCtx } from '../canvasPlugins'
import { listSlot } from '../slots'
import '../plugins/inspector'
import {
  INSPECTOR_STORAGE_KEY,
  reloadInspectorUi,
  resetInspectorUi,
  setInspectorWidth,
  toggleInspector,
  useInspectorUi,
} from '../inspector/state'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import { commitRename, renameNodeOp, renameProblem, RENAME_HELP } from '../operations/rename'
import { ReadOnlyGraphError } from '../operations'
import { PathError } from '../paths'
import { registerInspectorSection } from '../inspector/sections'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

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
    wires: [
      { id: 'w1', from: 'aapl', to: 'rsi', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

function load(graph: Graph = makeGraph()) {
  act(() => {
    useNodeBuilderStore.getState().openGraph(graph, { id: 'g_1', rev: 3, name: 'my graph' })
  })
}

function selectNodes(...ids: string[]) {
  act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ids }) })
}

function g(): Graph {
  return useNodeBuilderStore.getState().graph!
}

function pastLen(): number {
  return useNodeBuilderStore.getState().past.length
}

/** The on-node row for a param, reading the store like a node renderer does. */
function OnNodeRow({ nodeId, paramKey }: { nodeId: string; paramKey: string }) {
  const value = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.params[paramKey])
  return <ParamRow nodeId={nodeId} paramKey={paramKey} value={value} typeSpec={{ type: 'number' }} />
}

function renderInspector(extra?: React.ReactNode) {
  return render(
    <div className="nodebuilder-root">
      <Inspector />
      {extra}
    </div>,
  )
}

function diag(partial: Partial<Diagnostic>): Diagnostic {
  return {
    node_id: 'rsi',
    path: '/rsi',
    severity: 'error',
    code: 'param_invalid',
    message: 'Period must be at least 2.',
    param: null,
    port: null,
    line: null,
    col: null,
    end_line: null,
    end_col: null,
    ...partial,
  }
}

const cleanups: (() => void)[] = []

function setAppWidth(w: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true })
}

beforeEach(() => {
  setAppWidth(1600)
  resetInspectorUi()
  resetDiagnostics()
  act(() => { useNodeBuilderStore.getState().newGraph() })
})

afterEach(() => {
  cleanup()
  for (const c of cleanups.splice(0)) c()
  resetDiagnostics()
})

// ---------------------------------------------------------------------------
// Node view
// ---------------------------------------------------------------------------

describe('Inspector with a node selected (S14)', () => {
  it('shows the name, the path and every catalog param', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    expect(screen.getByTestId('nb-inspector')).toHaveAttribute('role', 'complementary')
    expect(screen.getByTestId('nb-inspector-name')).toHaveTextContent('rsi')
    expect(screen.getByTestId('nb-inspector-path')).toHaveTextContent('/rsi')
    for (const p of ['period', 'type', 'source', 'out']) {
      expect(screen.getByTestId(`nb-inspector-param-${p}`)).toBeInTheDocument()
    }
    // The catalog description sits under the header.
    expect(screen.getByText(/Relative Strength Index/)).toBeInTheDocument()
  })

  it('numeric fields stay type="text" inputMode="decimal"', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const input = within(screen.getByTestId('nb-inspector-param-period')).getByRole('textbox')
    expect(input).toHaveAttribute('type', 'text')
    expect(input).toHaveAttribute('inputMode', 'decimal')
  })

  it('editing period updates the store and the on-node row, as one undo step', () => {
    load()
    selectNodes('rsi')
    renderInspector(<div data-testid="on-node"><OnNodeRow nodeId="rsi" paramKey="period" /></div>)
    const before = pastLen()
    const input = within(screen.getByTestId('nb-inspector-param-period')).getByRole('textbox') as HTMLInputElement
    act(() => { input.focus() })
    fireEvent.change(input, { target: { value: '21' } })
    act(() => { input.blur() })
    expect(g().nodes.rsi.params.period).toBe(21)
    expect(pastLen()).toBe(before + 1)
    const onNode = within(screen.getByTestId('on-node')).getByRole('textbox') as HTMLInputElement
    expect(onNode.value).toBe('21')
  })

  it('a changed param shows the dot; right-clicking it resets to the default', () => {
    load()
    act(() => { useNodeBuilderStore.getState().updateNodeParams('rsi', { period: 21 }) })
    selectNodes('rsi')
    renderInspector()
    const dot = within(screen.getByTestId('nb-inspector-param-period')).getByLabelText('changed from default')
    expect(dot).toHaveAttribute('title', 'Changed from default 14. Right-click to reset')
    fireEvent.contextMenu(dot)
    expect(g().nodes.rsi.params.period).toBe(14)
    expect(within(screen.getByTestId('nb-inspector-param-period')).queryByLabelText('changed from default')).toBeNull()
  })

  it('Up and Down step a number field (Shift x10), and Enter commits', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const input = within(screen.getByTestId('nb-inspector-param-period')).getByRole('textbox') as HTMLInputElement
    act(() => { input.focus() })
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.value).toBe('15')
    fireEvent.keyDown(input, { key: 'ArrowUp', shiftKey: true })
    expect(input.value).toBe('25')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.value).toBe('24')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(g().nodes.rsi.params.period).toBe(24)
  })

  it('the slider under a min/max param commits once on release', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const before = pastLen()
    const slider = screen.getByTestId('nb-inspector-slider-period')
    expect(slider).toHaveAttribute('type', 'range')
    expect(slider).toHaveAttribute('min', '2')
    expect(slider).toHaveAttribute('max', '500')
    fireEvent.change(slider, { target: { value: '30' } })
    fireEvent.change(slider, { target: { value: '40' } })
    expect(g().nodes.rsi.params.period).toBe(14)
    fireEvent.pointerUp(slider)
    expect(g().nodes.rsi.params.period).toBe(40)
    expect(pastLen()).toBe(before + 1)
  })

  it('the Stream section lists reads, writes and the wired port', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const stream = screen.getByTestId('nb-inspector-section-stream')
    expect(within(stream).getByText('@close')).toBeInTheDocument()
    expect(within(stream).getByTestId('nb-inspector-port-in0')).toHaveTextContent('source ← aapl out')
    fireEvent.click(within(stream).getByText('Select wire'))
    expect(useNodeBuilderStore.getState().selectedWireIds).toEqual(['w1'])
  })

  it('an unwired required port shows not connected', () => {
    load()
    selectNodes('rsi2')
    renderInspector()
    expect(screen.getByTestId('nb-inspector-port-in0')).toHaveTextContent('source · not connected')
    expect(within(screen.getByTestId('nb-inspector-port-in0')).getByText('Connect…')).toBeInTheDocument()
  })

  it('does not take focus when the selection changes', () => {
    load()
    renderInspector()
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    selectNodes('rsi')
    expect(document.activeElement).toBe(outside)
    outside.remove()
  })

  it('falls back to the empty view when the node is deleted', () => {
    load()
    selectNodes('rsi2')
    renderInspector()
    expect(screen.getByTestId('nb-inspector-node')).toBeInTheDocument()
    act(() => { useNodeBuilderStore.getState().removeNodes(['rsi2']) })
    expect(screen.getByTestId('nb-inspector-empty')).toBeInTheDocument()
  })

  it('the flag dots set display and toggle bypass on this node, each one undo step', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const display = screen.getByTestId('nb-inspector-flag-display')
    expect(display).toHaveAttribute('aria-pressed', 'false')
    const before = pastLen()
    act(() => { fireEvent.click(display) })
    expect(g().nodes.rsi.display).toBe(true)
    expect(screen.getByTestId('nb-inspector-flag-display')).toHaveAttribute('aria-pressed', 'true')
    act(() => { fireEvent.click(screen.getByTestId('nb-inspector-flag-bypass')) })
    expect(g().nodes.rsi.bypass).toBe(true)
    expect(pastLen()).toBe(before + 2)
    // The lit display dot does nothing (Houdini).
    act(() => { fireEvent.click(screen.getByTestId('nb-inspector-flag-display')) })
    expect(pastLen()).toBe(before + 2)
  })

  it('a Ticker has a display dot and no bypass dot', () => {
    load()
    selectNodes('aapl')
    renderInspector()
    expect(screen.getByTestId('nb-inspector-flag-display')).toBeInTheDocument()
    expect(screen.queryByTestId('nb-inspector-flag-bypass')).toBeNull()
  })

  it('sections collapse, and the state persists in nb.inspector', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const head = within(screen.getByTestId('nb-inspector-section-stream')).getByRole('button', { name: /stream/i })
    expect(head).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('nb-inspector-port-in0')).toBeNull()
    expect(JSON.parse(localStorage.getItem(INSPECTOR_STORAGE_KEY)!).sections.stream).toBe(false)
  })

  it('hosts sections registered later (W7 code editors)', () => {
    load()
    selectNodes('rsi')
    cleanups.push(registerInspectorSection({
      id: 'code',
      title: 'Code',
      order: 20,
      Component: ({ node }) => <div data-testid="code-body">{node.name}</div>,
    }))
    act(() => { useInspectorUi.setState({ sections: { code: true } }) })
    renderInspector()
    const ids = [...screen.getByTestId('nb-inspector').querySelectorAll('[data-testid^="nb-inspector-section-"]')]
      .map(e => e.getAttribute('data-testid'))
    expect(ids).toEqual([
      'nb-inspector-section-parameters',
      'nb-inspector-section-code',
      'nb-inspector-section-stream',
      'nb-inspector-section-diagnostics',
    ])
    expect(screen.getByTestId('code-body')).toHaveTextContent('rsi')
  })
})

// ---------------------------------------------------------------------------
// Rename
// ---------------------------------------------------------------------------

describe('Inspector rename (rename_node)', () => {
  it('Enter renames: path and name follow, one history step', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const before = pastLen()
    fireEvent.click(screen.getByTestId('nb-inspector-name'))
    const input = screen.getByTestId('nb-inspector-name-input')
    fireEvent.change(input, { target: { value: 'rsi_fast' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(g().nodes.rsi.name).toBe('rsi_fast')
    expect(screen.getByTestId('nb-inspector-path')).toHaveTextContent('/rsi_fast')
    expect(screen.getByTestId('nb-inspector-name')).toHaveTextContent('rsi_fast')
    expect(pastLen()).toBe(before + 1)
    expect(useNodeBuilderStore.getState().past.at(-1)!.label).toBe('rename node')
    act(() => { useNodeBuilderStore.getState().undo() })
    expect(screen.getByTestId('nb-inspector-path')).toHaveTextContent('/rsi')
  })

  it('an invalid name shows the helper and does not commit', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const before = pastLen()
    fireEvent.click(screen.getByTestId('nb-inspector-name'))
    const input = screen.getByTestId('nb-inspector-name-input')
    fireEvent.change(input, { target: { value: 'RSI' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('nb-inspector-name-help')).toHaveTextContent(RENAME_HELP)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(g().nodes.rsi.name).toBe('rsi')
    expect(pastLen()).toBe(before)
  })

  it('a sibling name is refused', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    fireEvent.click(screen.getByTestId('nb-inspector-name'))
    const input = screen.getByTestId('nb-inspector-name-input')
    fireEvent.change(input, { target: { value: 'rsi2' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('nb-inspector-name-help')).toBeInTheDocument()
    expect(g().nodes.rsi.name).toBe('rsi')
  })

  it('Esc reverts', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    fireEvent.click(screen.getByTestId('nb-inspector-name'))
    const input = screen.getByTestId('nb-inspector-name-input')
    fireEvent.change(input, { target: { value: 'other' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByTestId('nb-inspector-name-input')).toBeNull()
    expect(g().nodes.rsi.name).toBe('rsi')
  })

  it('F2 (edit.rename) starts the rename in the Inspector', () => {
    load()
    selectNodes('rsi')
    act(() => { toggleInspector(false) })
    renderInspector()
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    act(() => { expect(runCommand('edit.rename')).toBe(true) })
    const input = screen.getByTestId('nb-inspector-name-input')
    expect(document.activeElement).toBe(input)
  })
})

describe('operations/rename.ts', () => {
  it('renameProblem checks the pattern and sibling names', () => {
    const graph = makeGraph()
    expect(renameProblem(graph, 'rsi', 'rsi_fast')).toBeNull()
    expect(renameProblem(graph, 'rsi', 'rsi')).toBeNull()
    expect(renameProblem(graph, 'rsi', 'RSI')).toBe(RENAME_HELP)
    expect(renameProblem(graph, 'rsi', '9x')).toBe(RENAME_HELP)
    expect(renameProblem(graph, 'rsi', 'rsi2')).toBe(RENAME_HELP)
  })

  it('renameNodeOp refuses a read-only graph and passes PathErrors through', () => {
    expect(() => renameNodeOp(makeGraph(true), 'rsi', 'x')).toThrow(ReadOnlyGraphError)
    expect(() => renameNodeOp(makeGraph(), 'rsi', 'rsi2')).toThrow(PathError)
    const next = renameNodeOp(makeGraph(), 'rsi', 'fast')
    expect(next.nodes.rsi.name).toBe('fast')
    expect(next.wires).toEqual(makeGraph().wires)
  })

  it('commitRename is one undo step and does nothing for a bad name', () => {
    load()
    const before = pastLen()
    expect(commitRename(useNodeBuilderStore, 'rsi', 'BAD')).toBe(false)
    expect(pastLen()).toBe(before)
    expect(commitRename(useNodeBuilderStore, 'rsi', 'good')).toBe(true)
    expect(pastLen()).toBe(before + 1)
  })
})

// ---------------------------------------------------------------------------
// Multi and wire views
// ---------------------------------------------------------------------------

describe('Inspector with several nodes or a wire', () => {
  it('two nodes: count, Bulk with registered bulk commands', () => {
    load()
    selectNodes('rsi', 'aapl')
    let bypassed = false
    cleanups.push(registerCommand({ id: 'flags.toggleBypass', label: 'Bypass', keys: ['b'], run: () => { bypassed = true } }))
    renderInspector()
    expect(screen.getByTestId('nb-inspector-count')).toHaveTextContent('2 nodes')
    const bulk = screen.getByTestId('nb-inspector-section-bulk')
    fireEvent.click(within(bulk).getByText('Bypass all'))
    expect(bypassed).toBe(true)
    // Mixed types: no shared section.
    expect(screen.queryByTestId('nb-inspector-section-shared')).toBeNull()
  })

  it('same type: a shared param edit writes every node in one commit', () => {
    load()
    act(() => { useNodeBuilderStore.getState().updateNodeParams('rsi2', { period: 20 }) })
    selectNodes('rsi', 'rsi2')
    renderInspector()
    const before = pastLen()
    const field = screen.getByTestId('nb-inspector-shared-period') as HTMLInputElement
    expect(field.value).toBe('')
    expect(field).toHaveAttribute('placeholder', 'mixed')
    expect(field).toHaveAttribute('type', 'text')
    expect(field).toHaveAttribute('inputMode', 'decimal')
    act(() => { field.focus() })
    fireEvent.change(field, { target: { value: '9' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(g().nodes.rsi.params.period).toBe(9)
    expect(g().nodes.rsi2.params.period).toBe(9)
    expect(pastLen()).toBe(before + 1)
    expect(field.value).toBe('9')
  })

  it('Esc in a shared field keeps the nodes as they were', () => {
    load()
    selectNodes('rsi', 'rsi2')
    renderInspector()
    const before = pastLen()
    const field = screen.getByTestId('nb-inspector-shared-period') as HTMLInputElement
    act(() => { field.focus() })
    fireEvent.change(field, { target: { value: '77' } })
    fireEvent.keyDown(field, { key: 'Escape' })
    expect(pastLen()).toBe(before)
    expect(g().nodes.rsi.params.period).toBe(14)
    expect(field.value).toBe('14')
  })

  it('a wire: ends, port and Delete wire', () => {
    load()
    act(() => { useNodeBuilderStore.getState().setSelection({ wireIds: ['w1'] }) })
    renderInspector()
    expect(screen.getByTestId('nb-inspector-wire-title')).toHaveTextContent('aapl → rsi')
    expect(screen.getByTestId('nb-inspector-wire-port')).toHaveTextContent('source')
    expect(within(screen.getByTestId('nb-inspector-wire-reads')).getByText('@close')).toBeInTheDocument()
    expect(screen.queryByTestId('nb-inspector-empty')).toBeNull()
    fireEvent.click(screen.getByText('Delete wire'))
    expect(g().wires).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// Empty view (S15)
// ---------------------------------------------------------------------------

describe('Inspector with no selection (S15)', () => {
  it('shows the hint, the graph, the legend, the flags and 12 keys', () => {
    load()
    renderInspector()
    expect(screen.getByTestId('nb-inspector-empty')).toBeInTheDocument()
    expect(screen.getByText('Click a node to inspect.')).toBeInTheDocument()
    expect(screen.getByTestId('nb-inspector-graph-name')).toHaveTextContent('my graph')
    expect(screen.getByTestId('nb-inspector-graph-counts')).toHaveTextContent('nodes 3 · wires 1')
    expect(screen.getByTestId('nb-legend-swatch-indicator').style.background).toBe('var(--nb-cat-indicator)')
    expect(screen.getByTestId('nb-legend-row-indicator')).toHaveAttribute('aria-label', 'Select all Indicators (2)')
    const keys = screen.getByTestId('nb-inspector-section-keys')
    expect(keys.querySelectorAll('kbd')).toHaveLength(12)
    expect(screen.getByTestId('nb-keys-row-0')).toHaveTextContent('Add node')
    expect(screen.getByTestId('nb-keys-row-11')).toHaveTextContent('Data sheet')
  })

  it('a legend row selects every node of the category', () => {
    load()
    renderInspector()
    fireEvent.click(screen.getByTestId('nb-legend-row-indicator'))
    expect(useNodeBuilderStore.getState().selectedNodeIds).toEqual(['rsi', 'rsi2'])
    // The selection request bumps the seq, so the canvas applies it.
    expect(useNodeBuilderStore.getState().selectionRequestSeq).toBeGreaterThan(0)
    expect(screen.getByTestId('nb-inspector-count')).toHaveTextContent('2 nodes')
  })

  it('Show all shortcuts runs the command bound to ?', () => {
    load()
    let opened = false
    cleanups.push(registerCommand({ id: 'help.shortcuts', label: 'Shortcuts', keys: ['?', 'shift+?'], run: () => { opened = true } }))
    renderInspector()
    fireEvent.click(screen.getByTestId('nb-inspector-all-shortcuts'))
    expect(opened).toBe(true)
  })

  it('an empty graph says how to start and opens Keys', () => {
    act(() => { useInspectorUi.setState({ sections: { keys: false } }) })
    renderInspector()
    expect(screen.getByText('Press Tab to add your first node.')).toBeInTheDocument()
    expect(screen.getByTestId('nb-inspector-section-keys').querySelectorAll('kbd')).toHaveLength(12)
  })

  it('the description is saved in the graph as one undo step', () => {
    load()
    renderInspector()
    const before = pastLen()
    const input = screen.getByTestId('nb-inspector-graph-description') as HTMLInputElement
    act(() => { input.focus() })
    fireEvent.change(input, { target: { value: 'pairs idea' } })
    act(() => { input.blur() })
    expect(g().meta.description).toBe('pairs idea')
    expect(pastLen()).toBe(before + 1)
  })
})

// ---------------------------------------------------------------------------
// Panel: toggle, width, read-only
// ---------------------------------------------------------------------------

describe('Inspector panel', () => {
  it('P toggles the panel', () => {
    load()
    renderInspector()
    expect(screen.getByTestId('nb-inspector')).toBeInTheDocument()
    act(() => { runCommand('panels.inspector') })
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    act(() => { runCommand('panels.inspector') })
    expect(screen.getByTestId('nb-inspector')).toBeInTheDocument()
  })

  it('the width survives a remount (nb.inspector)', () => {
    load()
    act(() => { setInspectorWidth(400) })
    act(() => { reloadInspectorUi() })
    renderInspector()
    expect(screen.getByTestId('nb-inspector').style.width).toBe('400px')
    expect(JSON.parse(localStorage.getItem(INSPECTOR_STORAGE_KEY)!).width).toBe(400)
  })

  it('the handle resizes by drag and keys, clamped, and double-click resets', () => {
    load()
    renderInspector()
    const panelEl = screen.getByTestId('nb-inspector')
    const handle = screen.getByTestId('nb-inspector-handle')
    expect(handle).toHaveAttribute('role', 'separator')
    const start = parseInt(panelEl.style.width, 10)
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 950, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 950, pointerId: 1 })
    expect(panelEl.style.width).toBe(`${start + 50}px`)
    expect(useInspectorUi.getState().width).toBe(start + 50)
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect(useInspectorUi.getState().width).toBe(start + 66)
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 0, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 0, pointerId: 1 })
    expect(useInspectorUi.getState().width).toBe(520)
    fireEvent.doubleClick(handle)
    expect(useInspectorUi.getState().width).toBeNull()
  })

  it('a read-only graph shows values as text, no flags, no rename', () => {
    act(() => { useNodeBuilderStore.setState({ graph: makeGraph(true) }) })
    selectNodes('rsi')
    renderInspector()
    expect(screen.getByTestId('nb-inspector-name')).toBeDisabled()
    expect(screen.queryByTestId('nb-inspector-flag-display')).toBeNull()
    expect(within(screen.getByTestId('nb-inspector-param-period')).queryByRole('textbox')).toBeNull()
    expect(screen.getByTestId('nb-inspector-param-period')).toHaveTextContent('14')
  })

  it('below 1100px it overlays at 280 and closes on Esc or an outside click', () => {
    setAppWidth(1000)
    load()
    renderInspector(<button data-testid="outside">x</button>)
    // UX-08: the overlay starts closed (it would cover the canvas on load).
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    act(() => { toggleInspector(true) })
    const el = screen.getByTestId('nb-inspector')
    expect(el).toHaveClass('nb-insp--overlay')
    expect(el.style.width).toBe('280px')
    expect(screen.queryByTestId('nb-inspector-handle')).toBeNull()
    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    act(() => { toggleInspector(true) })
    fireEvent.keyDown(screen.getByTestId('nb-inspector-graph-name'), { key: 'Escape' })
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
  })

  it('default width follows the app width', () => {
    setAppWidth(1280)
    renderInspector()
    expect(screen.getByTestId('nb-inspector').style.width).toBe('280px')
    cleanup()
    setAppWidth(2560)
    renderInspector()
    expect(screen.getByTestId('nb-inspector').style.width).toBe('360px')
  })

  it('shows the read-only view graph from the canvas when the store has none', () => {
    const view = makeGraph(true)
    act(() => { useNodeBuilderStore.setState({ graph: null }) })
    setActiveCanvas({ graph: () => view, focus: () => {} } as unknown as CanvasCtx)
    cleanups.push(() => setActiveCanvas(null))
    act(() => { useNodeBuilderStore.getState().select('rsi') })
    renderInspector()
    expect(screen.getByTestId('nb-inspector-path')).toHaveTextContent('/rsi')
    expect(screen.getByTestId('nb-inspector-name')).toBeDisabled()
  })

  it('Esc in a field hands focus back (the field is blurred)', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    const input = within(screen.getByTestId('nb-inspector-param-period')).getByRole('textbox') as HTMLInputElement
    act(() => { input.focus() })
    fireEvent.change(input, { target: { value: '99' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(document.activeElement).not.toBe(input)
    expect(g().nodes.rsi.params.period).toBe(14)
  })
})

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

describe('Inspector diagnostics', () => {
  it('lists the node problems with a red count; clean says so', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    expect(screen.getByTestId('nb-inspector-diag-clean')).toHaveTextContent('✓ no issues on this node')
    act(() => { setServerDiagnostics([diag({ param: 'period' })]) })
    const sec = screen.getByTestId('nb-inspector-section-diagnostics')
    expect(within(sec).getByText('1 error')).toHaveClass('nb-insp-sec__count--error')
    expect(screen.getByTestId('nb-inspector-diag-0')).toHaveTextContent('Period must be at least 2.')
    expect(screen.getByTestId('nb-inspector-diag-0')).toHaveTextContent('param_invalid')
  })

  it('a row click flashes the param row and focuses its field', () => {
    load()
    selectNodes('rsi')
    renderInspector()
    act(() => { setServerDiagnostics([diag({ param: 'period' })]) })
    fireEvent.click(screen.getByTestId('nb-inspector-diag-0'))
    const row = screen.getByTestId('nb-inspector-param-period')
    expect(row).toHaveClass('nb-insp-prow--flash')
    expect(document.activeElement).toBe(within(row).getByRole('textbox'))
  })

  it('the node badge opens the Inspector on Diagnostics and flashes the row', () => {
    load()
    act(() => {
      toggleInspector(false)
      useInspectorUi.setState({ sections: { diagnostics: false } })
    })
    const d = diag({ param: 'period' })
    act(() => { setServerDiagnostics([d]) })
    renderInspector(
      <DiagnosticBadge nodeId="rsi" diagnostics={[d]} onActivate={() => useNodeBuilderStore.getState().select('rsi')} />,
    )
    expect(screen.queryByTestId('nb-inspector')).toBeNull()
    fireEvent.click(screen.getByTestId('nb-diag-badge-rsi'))
    expect(screen.getByTestId('nb-inspector')).toBeInTheDocument()
    expect(within(screen.getByTestId('nb-inspector-section-diagnostics')).getByRole('button', { name: /diagnostics/i }))
      .toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('nb-inspector-param-period')).toHaveClass('nb-insp-prow--flash')
    // A canvas badge click never moves focus into the panel.
    expect(screen.getByTestId('nb-inspector').contains(document.activeElement)).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

describe('Inspector registration', () => {
  it('fills the rightPanel and toolbarRight slots', () => {
    expect(listSlot('rightPanel').some(e => e.id === 'inspector')).toBe(true)
    const toggle = listSlot('toolbarRight').find(e => e.id === 'inspectorToggle')
    expect(toggle?.order).toBe(90)
  })

  it('binds P and F2 in the command registry', () => {
    const keys = listCommands().flatMap(c => c.keys ?? [])
    expect(keys).toContain('p')
    expect(keys).toContain('f2')
    expect(listCommands().find(c => c.id === 'edit.rename')?.menu).toBe('node')
  })

  it('the toolbar toggle is pressed while open', () => {
    const Toggle = listSlot('toolbarRight').find(e => e.id === 'inspectorToggle')!.Component
    render(<div className="nodebuilder-root"><Toggle /></div>)
    const btn = screen.getByTestId('nb-btn-inspector')
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'false')
    expect(useInspectorUi.getState().open).toBe(false)
  })
})
