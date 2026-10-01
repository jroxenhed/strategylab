/**
 * Ports and chips as drawn on the canvas (F435 W2 item 2.E, specs S08, S10).
 *
 * Renders the real node renderers inside React Flow and checks the handles
 * (ids, labels, spare, connected), the write chips, and the folded chip row.
 */

import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { ReactFlow, type Edge, type Node, type NodeTypes } from '@xyflow/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { NODE_CATALOG } from '../catalog'
import IndicatorNode from '../nodes/IndicatorNode'
import ComparisonNode from '../nodes/ComparisonNode'
import LogicNode from '../nodes/LogicNode'
import TickerNode from '../nodes/TickerNode'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

beforeAll(() => {
  // jsdom has no DOMMatrixReadOnly; React Flow reads it when measuring nodes.
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor(_t?: string) {}
    }
  }
})

afterEach(() => cleanup())

const nodeTypes: NodeTypes = {
  ticker: TickerNode,
  indicator: IndicatorNode,
  comparison: ComparisonNode,
  logic: LogicNode,
}

const BY_NAME = new Map(NODE_CATALOG.map(e => [e.name, e]))

function rfNode(id: string, rfType: string, backendType: string, params: Record<string, unknown>, editable: boolean): Node {
  return {
    id,
    type: rfType,
    position: { x: 0, y: 0 },
    data: {
      backendType,
      catalog: BY_NAME.get(backendType) ?? null,
      params,
      display: false,
      bypass: false,
      nodePath: id,
      editable,
    },
  }
}

function edge(id: string, source: string, target: string, targetHandle: string): Edge {
  return { id, source, target, sourceHandle: 'out', targetHandle }
}

function draw(nodes: Node[], edges: Edge[] = []) {
  // The store holds the same nodes, so pickers and chips can read them.
  const g: Graph = {
    ...emptyGraph(),
    nodes: Object.fromEntries(nodes.map(n => {
      const d = n.data as { backendType: string; params: GraphNode['params'] }
      return [n.id, { id: n.id, type: d.backendType, name: n.id, parent: null, params: d.params, position: [0, 0], display: false, bypass: false } as GraphNode]
    })),
    wires: edges.map(e => ({ id: e.id, from: e.source, to: e.target, from_port: 'out' as const, to_port: e.targetHandle! })),
  }
  act(() => { useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' }) })
  return render(
    <div style={{ width: 800, height: 600 }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} />
    </div>,
  ).container
}

const q = (c: HTMLElement, testId: string) => c.querySelector(`[data-testid="${testId}"]`) as HTMLElement | null

describe('read-only indicator title (W2)', () => {
  it('shows settings only, not the stream reads and writes', () => {
    const c = draw([rfNode('r', 'indicator', 'rsi', { period: 14, type: 'wilder', source: '@close', out: '@rsi' }, false)])
    const node = c.querySelector('.react-flow__node[data-id="r"]') as HTMLElement
    expect(node.textContent).toContain('RSI(14,wilder)')
    expect(node.textContent).not.toContain('@close,')
    expect(node.textContent).not.toContain(',@rsi)')
  })
})

describe('node ports (S08)', () => {
  it('a crosses_above node draws inputs a and b and one output', () => {
    const c = draw([rfNode('x', 'comparison', 'crosses_above', { threshold: 0 }, true)])
    expect(q(c, 'nb-port-x-in0')?.getAttribute('aria-label')).toBe('input a')
    expect(q(c, 'nb-port-x-in1')?.getAttribute('aria-label')).toBe('input b')
    expect(q(c, 'nb-port-x-out')?.getAttribute('aria-label')).toBe('output')
    const labels = [...c.querySelectorAll('.react-flow__node[data-id="x"] .nb-port-label')].map(e => e.textContent)
    expect(labels).toEqual(['a', 'b'])
    // Two inputs: labels always show (no hover-only class).
    expect(c.querySelector('.react-flow__node[data-id="x"] .nb-port-label--hover')).toBeNull()
  })

  it('an and node with two wires draws three inputs, the last dashed', () => {
    const c = draw(
      [
        rfNode('r1', 'indicator', 'rsi', { period: 14 }, true),
        rfNode('r2', 'indicator', 'rsi', { period: 7 }, true),
        rfNode('a', 'logic', 'and', {}, true),
      ],
      [edge('w1', 'r1', 'a', 'in0'), edge('w2', 'r2', 'a', 'in1')],
    )
    const ins = [...c.querySelectorAll('.react-flow__node[data-id="a"] .react-flow__handle.target')]
    expect(ins.map(h => h.getAttribute('data-handleid'))).toEqual(['in0', 'in1', 'in2'])
    expect(ins.map(h => h.classList.contains('nb-port--spare'))).toEqual([false, false, true])
    expect(ins.map(h => h.classList.contains('nb-port--connected'))).toEqual([true, true, false])
  })

  it('places inputs evenly along the top edge', () => {
    const c = draw([rfNode('x', 'comparison', 'crosses_above', {}, true)])
    expect(q(c, 'nb-port-x-in0')?.style.left).toBe('33.35%')
    expect(q(c, 'nb-port-x-in1')?.style.left).toBe('66.65%')
  })

  it('a single input shows its label on hover only', () => {
    const c = draw([rfNode('r', 'indicator', 'rsi', { period: 14 }, true)])
    expect(c.querySelector('.react-flow__node[data-id="r"] .nb-port-label--hover')?.textContent).toBe('source')
  })

  it('a Ticker draws no input; ports do not connect in the read-only view', () => {
    const c = draw([
      rfNode('t', 'ticker', 'ticker', { symbol: 'AAPL' }, false),
      rfNode('r', 'indicator', 'rsi', { period: 14 }, false),
    ])
    expect(c.querySelectorAll('.react-flow__node[data-id="t"] .react-flow__handle.target')).toHaveLength(0)
    expect(q(c, 'nb-port-r-in0')?.classList.contains('connectable')).toBe(false)
  })

  it('the output hover text lists the writes', () => {
    const c = draw([rfNode('i', 'indicator', 't2_ind', { period: 14, source: '@close', out: '@t2' }, true)])
    expect(q(c, 'nb-port-i-out')?.title).toBe('out · +@t2')
  })
})

describe('write chips on a node (S10)', () => {
  it('a node with write params shows one renamable chip per write', () => {
    const c = draw([rfNode('m', 'indicator', 't2_macd', { source: '@close', out_line: '@macd', out_signal: '@macd_signal', out_hist: '@macd_hist' }, true)])
    const chips = ['out_line', 'out_signal', 'out_hist'].map(p => q(c, `nb-write-chip-m-${p}`))
    expect(chips.map(ch => ch?.textContent)).toEqual(['+@macd', '+@macd_signal', '+@macd_hist'])
    expect(chips[0]?.getAttribute('aria-label')).toBe('writes @macd. Double-click to rename')
    // The picker shows the read, so there is no read chip too.
    expect(q(c, 'nb-attr-chip-m-source')).not.toBeNull()
  })

  it('a node without write params shows its fixed writes', () => {
    const c = draw([rfNode('t', 'ticker', 'ticker', { symbol: 'AAPL', interval: '1d' }, true)])
    expect(q(c, 'nb-write-chip-t-close')?.textContent).toBe('+@close')
  })

  it('a logic node with no param rows of its own still gets its terms picker', () => {
    const c = draw([rfNode('a', 'logic', 't2_and', { terms: ['@x'], out: '@all' }, true)])
    expect(q(c, 'nb-attr-chip-a-terms-0')?.textContent).toContain('@x')
  })

  it('more than 6 chips fold into the first 5 and a +N chip', () => {
    const terms = ['@a', '@b', '@c', '@d', '@e', '@f']
    const c = draw([rfNode('a', 'logic', 't2_and', { terms, out: '@all' }, false)])
    const row = c.querySelector('.react-flow__node[data-id="a"] .nb-chip-row') as HTMLElement
    const texts = [...row.children].map(e => e.textContent)
    expect(texts).toEqual(['@a', '@b', '@c', '@d', '@e', '+2'])
  })
})
