/**
 * Write chips `+@name` with inline rename (F435 W2 item 2.E, spec S10).
 *
 * A rename is one store commit through `renameAttr`: the writer's param and
 * every downstream reader change together, and one undo restores both.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { WriteChip, WRITE_NAME_HELP } from '../nodes/WriteChip'
import { wireLabels } from '../streamLabels'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

function node(id: string, type: string, params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false }
}
function wire(id: string, from: string, to: string): GraphWire {
  return { id, from, to, from_port: 'out', to_port: 'in0' }
}

const s = useNodeBuilderStore.getState
const graph = () => s().graph!

/** A chip that follows the store, like a node does. */
function Chip({ editable = true }: { editable?: boolean }) {
  const name = useNodeBuilderStore(st => st.graph?.nodes.rsi.params.out) as string
  return <WriteChip nodeId="rsi" param="out" name={name} editable={editable} />
}

beforeEach(() => {
  resetDiagnostics()
  const g: Graph = {
    ...emptyGraph(),
    nodes: {
      aapl: node('aapl', 'ticker'),
      rsi: node('rsi', 't2_ind', { period: 14, source: '@close', out: '@rsi' }),
      xb: node('xb', 't2_cmp', { a: '@rsi', b: null, out: '@xb' }),
    },
    wires: [wire('w1', 'aapl', 'rsi'), wire('w2', 'rsi', 'xb')],
  }
  act(() => { s().openGraph(g, { id: null, rev: 0, name: 'test' }) })
})

afterEach(() => cleanup())

function startEdit() {
  act(() => { fireEvent.doubleClick(screen.getByTestId('nb-write-chip-rsi-out')) })
  return screen.getByTestId('nb-write-chip-input') as HTMLInputElement
}

describe('WriteChip', () => {
  it('renames the write and its reader in one undo step', () => {
    render(<Chip />)
    expect(screen.getByTestId('nb-write-chip-rsi-out').textContent).toBe('+@rsi')
    const input = startEdit()
    expect(input.value).toBe('rsi')
    const before = s().past.length
    act(() => { fireEvent.change(input, { target: { value: 'rsi14' } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    expect(graph().nodes.rsi.params.out).toBe('@rsi14')
    expect(graph().nodes.xb.params.a).toBe('@rsi14')
    expect(wireLabels(graph(), {}).w2.text).toBe('@rsi14')
    expect(s().past.length).toBe(before + 1)
    expect(screen.getByTestId('nb-write-chip-rsi-out').textContent).toBe('+@rsi14')
    act(() => { s().undo() })
    expect(graph().nodes.rsi.params.out).toBe('@rsi')
    expect(graph().nodes.xb.params.a).toBe('@rsi')
  })

  it('an invalid name is flagged and Enter does not commit', () => {
    render(<Chip />)
    const input = startEdit()
    act(() => { fireEvent.change(input, { target: { value: 'RSI' } }) })
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.parentElement?.title).toBe(WRITE_NAME_HELP)
    const before = s().past.length
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    expect(s().past.length).toBe(before)
    expect(screen.getByTestId('nb-write-chip-input')).toBeInTheDocument()
  })

  it('Esc reverts, and blur after Esc does not commit', () => {
    render(<Chip />)
    const input = startEdit()
    act(() => { fireEvent.change(input, { target: { value: 'other' } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Escape' }) })
    expect(graph().nodes.rsi.params.out).toBe('@rsi')
    expect(screen.queryByTestId('nb-write-chip-input')).toBeNull()
  })

  it('blur commits a valid name; the same name makes no history step', () => {
    render(<Chip />)
    let input = startEdit()
    const before = s().past.length
    act(() => { fireEvent.blur(input) })
    expect(s().past.length).toBe(before)
    input = startEdit()
    act(() => { fireEvent.change(input, { target: { value: 'r2' } }) })
    act(() => { fireEvent.blur(input) })
    expect(graph().nodes.rsi.params.out).toBe('@r2')
  })

  it('F2 starts editing; keys typed do not reach the canvas', () => {
    const onKey = vi.fn()
    document.addEventListener('keydown', onKey)
    try {
      render(<Chip />)
      act(() => { fireEvent.keyDown(screen.getByTestId('nb-write-chip-rsi-out'), { key: 'F2' }) })
      const input = screen.getByTestId('nb-write-chip-input')
      act(() => { fireEvent.keyDown(input, { key: 'd' }) })
      act(() => { fireEvent.keyDown(input, { key: 'Delete' }) })
      expect(onKey).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', onKey)
    }
  })

  it('is not editable in a read-only view or for a fixed write', () => {
    render(<><Chip editable={false} /><WriteChip nodeId="aapl" param={null} name="@close" editable /></>)
    act(() => { fireEvent.doubleClick(screen.getByTestId('nb-write-chip-rsi-out')) })
    act(() => { fireEvent.doubleClick(screen.getByTestId('nb-write-chip-aapl-close')) })
    expect(screen.queryByTestId('nb-write-chip-input')).toBeNull()
  })

  it('shows a clash from the server on the chip', () => {
    const d: Diagnostic = {
      node_id: 'rsi', path: null, severity: 'error', code: 'attr_clash',
      message: '@rsi is already on the stream (written by rsi_fast). Pick another name.',
      param: 'out', port: null, line: null, col: null, end_line: null, end_col: null,
    }
    act(() => setServerDiagnostics([d]))
    render(<Chip />)
    const chip = screen.getByTestId('nb-write-chip-rsi-out')
    expect(chip.className).toContain('nb-chip--clash')
    expect(chip.title).toBe(d.message)
  })

  it('shows the clash the server reports on a READER of this write (FP-6)', () => {
    // A second writer of @rsi meets rsi at xb; the server reports the clash
    // on xb's read param `a`, never on the writer.
    act(() => {
      s().commit('add rsi2', g => ({
        ...g,
        nodes: { ...g.nodes, rsi2: node('rsi2', 't2_ind', { source: '@close', out: '@rsi' }) },
        wires: [...g.wires, wire('w3', 'aapl', 'rsi2'), { ...wire('w4', 'rsi2', 'xb'), to_port: 'in1' }],
      }))
    })
    const d: Diagnostic = {
      node_id: 'xb', path: null, severity: 'error', code: 'attr_clash',
      message: "t2_cmp 'xb' reads @rsi, but @rsi comes from 'rsi' and 'rsi2'.  Rename one of them.",
      param: 'a', port: null, line: null, col: null, end_line: null, end_col: null,
    }
    render(<Chip />)
    expect(screen.getByTestId('nb-write-chip-rsi-out').className).not.toContain('nb-chip--clash')
    act(() => setServerDiagnostics([d]))
    const chip = screen.getByTestId('nb-write-chip-rsi-out')
    expect(chip.className).toContain('nb-chip--clash')
    expect(chip.getAttribute('aria-invalid')).toBe('true')
    expect(chip.title).toBe(d.message)
  })
})
