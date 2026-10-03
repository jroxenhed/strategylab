/**
 * Selection mirroring never loops (F435 W7 integration).
 *
 * Seen in a browser right after a Vite hot reload of Canvas.tsx: "Maximum
 * update depth exceeded" from React Flow's SelectionListener calling
 * Canvas -> store mirrorSelection. These tests check the normal-load paths
 * that could feed such a loop: a multi-selection kept while the graph
 * changes (edit, add, delete, undo, redo), and a mirror that reports the
 * same selection again, in the same or another order.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import Canvas from '../Canvas'
import { useNodeBuilderStore } from '../store'
import type { Graph } from '../../../api/nodebuilder'

function makeGraph(): Graph {
  const node = (id: string, type: string, params: Record<string, string | number>, y: number) => ({
    id, type, name: id, parent: null, params, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 2,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d', source: 'yahoo' }, 0),
      r: node('r', 'rsi', { period: 14 }, 150),
      s: node('s', 'sma', { period: 20 }, 150),
      e: node('e', 'entry', {}, 300),
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0', attr: '@close' },
      { id: 'w2', from: 't', to: 's', from_port: 'out', to_port: 'in0', attr: '@close' },
      { id: 'w3', from: 'r', to: 'e', from_port: 'out', to_port: 'in0', attr: '@rsi' },
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
  useNodeBuilderStore.getState().discardEdits()
  vi.restoreAllMocks()
})

/** The canvas fed from the store graph, as NodeBuilder does. */
function LiveCanvas() {
  const g = useNodeBuilderStore(s => s.graph)
  return g ? <div style={{ width: 800, height: 600 }}><Canvas graph={g} /></div> : null
}

describe('selection mirroring', () => {
  it('a multi-selection survives graph edits, undo and redo without a render loop', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    useNodeBuilderStore.getState().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    render(<LiveCanvas />)
    let writes = 0
    const unsub = useNodeBuilderStore.subscribe((s, p) => {
      if (s.selectedNodeIds !== p.selectedNodeIds || s.selectedNodeId !== p.selectedNodeId
        || s.selectedWireIds !== p.selectedWireIds || s.selectedAnnotationIds !== p.selectedAnnotationIds) writes += 1
    })
    const st = () => useNodeBuilderStore.getState()
    act(() => { st().setSelection({ nodeIds: ['r', 's'], primary: 'r' }) })
    expect(st().selectedNodeIds).toEqual(['r', 's'])
    act(() => {
      st().commit('edit period', g => ({ ...g, nodes: { ...g.nodes, r: { ...g.nodes.r, params: { period: 7 } } } }))
    })
    act(() => {
      st().commit('move', g => ({ ...g, nodes: { ...g.nodes, s: { ...g.nodes.s, position: [40, 150] } } }))
    })
    act(() => {
      st().commit('add', g => ({
        ...g,
        nodes: { ...g.nodes, x: { id: 'x', type: 'sma', name: 'x', parent: null, params: { period: 5 }, position: [200, 150], display: false, bypass: false } },
      }))
    })
    act(() => {
      st().commit('delete s', g => {
        const { s: _gone, ...nodes } = g.nodes
        void _gone
        return { ...g, nodes, wires: g.wires.filter(w => w.to !== 's') }
      })
    })
    act(() => { st().undo() })
    act(() => { st().redo() })
    unsub()
    expect(st().selectedNodeIds).toEqual(['r'])
    expect(st().selectedNodeId).toBe('r')
    // A handful of real changes (the set, the delete), never a cascade.
    expect(writes).toBeLessThan(10)
    const loop = errors.mock.calls.filter(c => String(c[0]).includes('Maximum update depth'))
    expect(loop).toEqual([])
  })
})

describe('mirrorSelection guard', () => {
  it('reporting the same selection again (any order) writes nothing', () => {
    useNodeBuilderStore.getState().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const st = () => useNodeBuilderStore.getState()
    act(() => { st().mirrorSelection({ nodeIds: ['r', 's'], wireIds: ['w1', 'w2'], annotationIds: [] }) })
    let writes = 0
    const unsub = useNodeBuilderStore.subscribe(() => { writes += 1 })
    st().mirrorSelection({ nodeIds: ['r', 's'], wireIds: ['w1', 'w2'], annotationIds: [] })
    st().mirrorSelection({ nodeIds: ['s', 'r'], wireIds: ['w2', 'w1'] })
    st().mirrorSelection({ nodeIds: ['r', 's', 'r'], wireIds: ['w1', 'w2'] })
    unsub()
    expect(writes).toBe(0)
    expect(st().selectedNodeId).toBe('r')
  })

  it('a real change is still written, once', () => {
    useNodeBuilderStore.getState().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' })
    const st = () => useNodeBuilderStore.getState()
    act(() => { st().mirrorSelection({ nodeIds: ['r', 's'] }) })
    let writes = 0
    const unsub = useNodeBuilderStore.subscribe(() => { writes += 1 })
    st().mirrorSelection({ nodeIds: ['s'] })
    st().mirrorSelection({ nodeIds: ['s'] })
    unsub()
    expect(writes).toBe(1)
    expect(st().selectedNodeIds).toEqual(['s'])
    expect(st().selectedNodeId).toBe('s')
  })
})
