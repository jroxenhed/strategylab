/**
 * editNotices.ts + store edit-copy actions (F435 W0 0.E): the copy keeps
 * regime (W5), vertical spacing, unsupported-node list, edit tracking and
 * discard.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { Graph, GraphNode, GraphWire } from '../../../api/nodebuilder'
import {
  EDIT_VERTICAL_SPACING,
  describeUnsupportedNodes,
  findUnsupportedNodes,
  prepareEditableCopy,
} from '../editNotices'
import { NODE_CATALOG, type NodeCatalogEntry } from '../catalog'
import { hasEdits, useNodeBuilderStore } from '../store'

/** The catalog with `compileActive` forced for the named types (independent of the generated file). */
function catalogWith(active: Record<string, boolean>): NodeCatalogEntry[] {
  return NODE_CATALOG.map(e => (e.name in active ? { ...e, compileActive: active[e.name] } : e))
}

function node(id: string, type: string, y = 100): GraphNode {
  const name = id.split('/').pop() || type
  return { id, type, name, parent: null, params: {}, position: [10, y], display: false, bypass: false }
}

function wire(id: string, from: string, to: string, to_port = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port }
}

function autoGraph(): Graph {
  return {
    _version: 2,
    stream_schema: 1,
    readOnly: true,
    meta: {},
    annotations: { boxes: [], notes: [] },
    nodes: {
      '/ticker': node('/ticker', 'ticker', 0),
      '/entry/rsi': node('/entry/rsi', 'rsi', 100),
      // A rule name no node type registers (rising is a real node since W2).
      '/entry/odd': node('/entry/odd', 'is_above_signal', 200),
      '/entry': node('/entry', 'entry', 300),
      '/regime/ma': node('/regime/ma', 'sma', 50),
    },
    wires: [
      wire('w1', '/ticker', '/entry/rsi'),
      wire('w2', '/entry/rsi', '/entry'),
      wire('w3', '/ticker', '/regime/ma'),
    ],
  }
}

describe('prepareEditableCopy', () => {
  it('keeps regime nodes and their wires (W5 runs regime)', () => {
    const { graph } = prepareEditableCopy(autoGraph())
    expect(Object.keys(graph.nodes)).toContain('/regime/ma')
    expect(graph.wires.map(w => w.id)).toEqual(['w1', 'w2', 'w3'])
    expect(graph.readOnly).toBe(false)
  })

  it('spreads rows apart vertically and keeps x', () => {
    const { graph } = prepareEditableCopy(autoGraph())
    expect(EDIT_VERTICAL_SPACING).toBe(1.8)
    expect(graph.nodes['/entry/rsi'].position).toEqual([10, 180])
    expect(graph.nodes['/entry'].position).toEqual([10, 540])
  })

  it('does not change the input graph', () => {
    const g = autoGraph()
    prepareEditableCopy(g)
    expect(g.nodes['/entry/rsi'].position).toEqual([10, 100])
    expect(g.readOnly).toBe(true)
  })
})

describe('findUnsupportedNodes', () => {
  it('lists unknown types and catalog entries marked not compile-active', () => {
    const g = autoGraph()
    g.nodes['/size'] = node('/size', 'size')
    g.wires.push(wire('ws', '/entry/rsi', '/size'))
    const list = findUnsupportedNodes(g, catalogWith({ size: false }))
    expect(list).toEqual([
      { id: '/entry/odd', type: 'is_above_signal', reason: 'unknown' },
      { id: '/size', type: 'size', reason: 'inactive' },
    ])
  })

  it('judges an unwired Size or Stop like any node: its constant applies since W5', () => {
    const g = autoGraph()
    delete g.nodes['/entry/odd']
    g.nodes['/size'] = node('/size', 'size')
    g.nodes['/stop'] = node('/stop', 'stop')
    expect(findUnsupportedNodes(g, catalogWith({ size: true, stop: true }))).toEqual([])
    // No special case left: a catalog that cannot run them lists them, wired or not.
    expect(findUnsupportedNodes(g, catalogWith({ size: false, stop: false })).map(u => u.id)).toEqual(['/size', '/stop'])
  })

  it('lists per-direction settings and comparisons with a rule detail, as compile refuses them', () => {
    const g = autoGraph()
    delete g.nodes['/entry/odd']
    g.nodes['/sl_long'] = { ...node('/sl_long', 'stop_loss'), params: { pct: 3, direction: 'long' } }
    g.nodes['/cmp'] = { ...node('/cmp', 'above'), params: { threshold: 1, condition_extra: 'atr_pct' } }
    expect(findUnsupportedNodes(g)).toEqual([
      { id: '/sl_long', type: 'stop_loss', reason: 'direction', detail: 'long' },
      { id: '/cmp', type: 'above', reason: 'rule', detail: 'atr_pct' },
    ])
    expect(describeUnsupportedNodes(findUnsupportedNodes(g))).toContain('stop_loss [long], above [atr_pct]')
  })

  it('skips a bypassed per-direction setting (compile skips it too)', () => {
    const g = autoGraph()
    delete g.nodes['/entry/odd']
    g.nodes['/sl_long'] = { ...node('/sl_long', 'stop_loss'), params: { direction: 'long' }, bypass: true }
    expect(findUnsupportedNodes(g)).toEqual([])
  })

  it('returns nothing for a null graph or a fully supported graph', () => {
    expect(findUnsupportedNodes(null)).toEqual([])
    const g = autoGraph()
    delete g.nodes['/entry/odd']
    expect(findUnsupportedNodes(g)).toEqual([])
  })

  it('describes the list for the banner with the S07 copy', () => {
    expect(describeUnsupportedNodes([])).toBeNull()
    const one = describeUnsupportedNodes([{ id: '/a', type: 'rising', reason: 'unknown' }])
    expect(one).toBe('Unsupported in graphs: rising. The graph cannot run until these are replaced.')
    const two = describeUnsupportedNodes([
      { id: '/a', type: 'rising', reason: 'unknown' },
      { id: '/b', type: 'stop', reason: 'inactive' },
    ])
    expect(two).toBe('Unsupported in graphs: rising, stop. The graph cannot run until these are replaced.')
  })
})

describe('store edit copy', () => {
  beforeEach(() => {
    useNodeBuilderStore.getState().discardEdits()
  })

  it('loadFromAutoRender keeps regime nodes and starts with no edits', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    const s = useNodeBuilderStore.getState()
    expect(s.graph?.nodes['/regime/ma']).toBeDefined()
    expect('regimeRemoved' in s).toBe(false)
    expect(s.graph?.nodes['/entry/rsi'].position).toEqual([10, 180])
    expect(hasEdits(s)).toBe(false)
  })

  it('counts a param change as an edit', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    useNodeBuilderStore.getState().updateNodeParams('/entry/rsi', { period: 7 })
    expect(hasEdits(useNodeBuilderStore.getState())).toBe(true)
  })

  it('newGraph starts clean', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    useNodeBuilderStore.getState().newGraph()
    const s = useNodeBuilderStore.getState()
    expect(hasEdits(s)).toBe(false)
    useNodeBuilderStore.getState().addNode(node('/ticker', 'ticker'))
    expect(hasEdits(useNodeBuilderStore.getState())).toBe(true)
  })

  it('discardEdits goes back to the view mode', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    useNodeBuilderStore.getState().updateNodeParams('/entry/rsi', { period: 7 })
    useNodeBuilderStore.getState().discardEdits()
    const s = useNodeBuilderStore.getState()
    expect(s.graph).toBeNull()
    expect(hasEdits(s)).toBe(false)
  })

  it('keeps the store action named updateNodeParams', () => {
    // Browser verification finds this action by its name (handoff trap).
    expect(typeof useNodeBuilderStore.getState().updateNodeParams).toBe('function')
  })
})
