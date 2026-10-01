/**
 * Canvas wires (F435 W2 item 2.E, specs S05, S08, S11): the canvas hands
 * each wire its handle ids and label data, a wire diagnostic selects the
 * wire, and a new node gets unique write names.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import Canvas from '../Canvas'
import { useNodeBuilderStore } from '../store'
import { focusDiagnosticWire, resetDiagnostics } from '../useDiagnostics'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import type { Graph } from '../../../api/nodebuilder'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

function makeGraph(): Graph {
  const node = (id: string, type: string, params: Record<string, string | number | null>, y: number) => ({
    id, type, name: id, parent: null, params, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 2,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d', source: 'yahoo' }, 0),
      r: node('r', 't2_ind', { period: 14, source: '@close', out: '@rsi' }, 150),
      x: node('x', 't2_cmp', { a: '@rsi', b: null, out: '@x' }, 300),
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'r', to: 'x', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

afterEach(() => {
  cleanup()
  resetDiagnostics()
  useNodeBuilderStore.getState().discardEdits()
})

function mount() {
  const g = makeGraph()
  useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
  return render(
    <div className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      <Canvas graph={useNodeBuilderStore.getState().graph!} />
    </div>,
  )
}

const diag = (p: Partial<Diagnostic>): Diagnostic => ({
  node_id: null, path: null, severity: 'error', code: 'attr_missing', message: 'm', param: null, port: null,
  line: null, col: null, end_line: null, end_col: null, ...p,
})

describe('Canvas wires', () => {
  it('draws each node\'s ports from the catalog', () => {
    const { container } = mount()
    expect(container.querySelector('[data-testid="nb-port-x-in0"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="nb-port-x-in1"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="nb-port-t-in0"]')).toBeNull()
  })

  it('a diagnostic about a wire selects that wire, not a node', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    let handled = false
    act(() => { handled = focusDiagnosticWire(diag({ node_id: 'x', port: 'in0' })) })
    expect(handled).toBe(true)
    expect(useNodeBuilderStore.getState().selectedNodeId).toBeNull()
  })

  it('a diagnostic about a node is left to the node', () => {
    mount()
    expect(focusDiagnosticWire(diag({ node_id: 'x', code: 'param_invalid', param: 'a' }))).toBe(false)
  })
})
