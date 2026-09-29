/**
 * editNotices.ts + store edit-copy actions (F435 W0 0.E): regime strip,
 * vertical spacing, unsupported-node list, edit tracking and discard.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import {
  EDIT_VERTICAL_SPACING,
  describeUnsupportedNodes,
  findUnsupportedNodes,
  prepareEditableCopy,
} from '../editNotices'
import { hasEdits, useNodeBuilderStore } from '../store'

function node(id: string, type: string, y = 100): GraphNode {
  return { id, type, params: {}, position: [10, y], display: false, bypass: false }
}

function autoGraph(): Graph {
  return {
    _version: 1,
    readOnly: true,
    nodes: {
      '/ticker': node('/ticker', 'ticker', 0),
      '/entry/rsi': node('/entry/rsi', 'rsi', 100),
      '/entry/rising': node('/entry/rising', 'rising', 200),
      '/entry': node('/entry', 'entry', 300),
      '/regime/ma': node('/regime/ma', 'sma', 50),
    },
    wires: [
      { id: 'w1', from: '/ticker', to: '/entry/rsi' },
      { id: 'w2', from: '/entry/rsi', to: '/entry' },
      { id: 'w3', from: '/ticker', to: '/regime/ma' },
    ],
  }
}

describe('prepareEditableCopy', () => {
  it('removes regime nodes and their wires, and reports them', () => {
    const { graph, regimeRemoved } = prepareEditableCopy(autoGraph())
    expect(regimeRemoved).toEqual(['/regime/ma'])
    expect(Object.keys(graph.nodes)).not.toContain('/regime/ma')
    expect(graph.wires.map(w => w.id)).toEqual(['w1', 'w2'])
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

  it('reports nothing when there is no regime', () => {
    const g = autoGraph()
    delete g.nodes['/regime/ma']
    g.wires = g.wires.filter(w => w.id !== 'w3')
    expect(prepareEditableCopy(g).regimeRemoved).toEqual([])
  })
})

describe('findUnsupportedNodes', () => {
  it('lists unknown types and catalog entries marked not compile-active', () => {
    const g = autoGraph()
    g.nodes['/size'] = node('/size', 'size')
    g.wires.push({ id: 'ws', from: '/entry/rsi', to: '/size' })
    const list = findUnsupportedNodes(g)
    expect(list).toEqual([
      { id: '/entry/rising', type: 'rising', reason: 'unknown' },
      { id: '/size', type: 'size', reason: 'inactive' },
    ])
  })

  it('does not warn about an unwired Size or Stop terminal (compile ignores it)', () => {
    const g = autoGraph()
    delete g.nodes['/entry/rising']
    g.nodes['/size'] = node('/size', 'size')
    g.nodes['/stop'] = node('/stop', 'stop')
    expect(findUnsupportedNodes(g)).toEqual([])
  })

  it('lists per-direction settings and comparisons with a rule detail, as compile refuses them', () => {
    const g = autoGraph()
    delete g.nodes['/entry/rising']
    g.nodes['/sl_long'] = { ...node('/sl_long', 'stop_loss'), params: { pct: 3, direction: 'long' } }
    g.nodes['/cmp'] = { ...node('/cmp', 'above'), params: { threshold: 1, condition_extra: 'atr_pct' } }
    expect(findUnsupportedNodes(g)).toEqual([
      { id: '/sl_long', type: 'stop_loss', reason: 'direction', detail: 'long' },
      { id: '/cmp', type: 'above', reason: 'rule', detail: 'atr_pct' },
    ])
    expect(describeUnsupportedNodes(findUnsupportedNodes(g))).toContain('stop_loss [long] (/sl_long)')
  })

  it('skips a bypassed per-direction setting (compile skips it too)', () => {
    const g = autoGraph()
    delete g.nodes['/entry/rising']
    g.nodes['/sl_long'] = { ...node('/sl_long', 'stop_loss'), params: { direction: 'long' }, bypass: true }
    expect(findUnsupportedNodes(g)).toEqual([])
  })

  it('returns nothing for a null graph or a fully supported graph', () => {
    expect(findUnsupportedNodes(null)).toEqual([])
    const g = autoGraph()
    delete g.nodes['/entry/rising']
    expect(findUnsupportedNodes(g)).toEqual([])
  })

  it('describes the list for the banner', () => {
    expect(describeUnsupportedNodes([])).toBeNull()
    const one = describeUnsupportedNodes([{ id: '/a', type: 'rising', reason: 'unknown' }])
    expect(one).toBe('This node cannot run yet, so Run Backtest will fail until you remove it: rising (/a)')
    const two = describeUnsupportedNodes([
      { id: '/a', type: 'rising', reason: 'unknown' },
      { id: '/b', type: 'stop', reason: 'inactive' },
    ])
    expect(two).toContain('These nodes cannot run yet')
    expect(two).toContain('rising (/a), stop (/b)')
  })
})

describe('store edit copy', () => {
  beforeEach(() => {
    useNodeBuilderStore.getState().discardEdits()
  })

  it('loadFromAutoRender keeps the removed regime ids and starts with no edits', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    const s = useNodeBuilderStore.getState()
    expect(s.regimeRemoved).toEqual(['/regime/ma'])
    expect(s.graph?.nodes['/entry/rsi'].position).toEqual([10, 180])
    expect(hasEdits(s)).toBe(false)
  })

  it('counts a param change as an edit', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    useNodeBuilderStore.getState().updateNodeParams('/entry/rsi', { period: 7 })
    expect(hasEdits(useNodeBuilderStore.getState())).toBe(true)
  })

  it('newEmptyGraph starts clean and clears the regime notice', () => {
    useNodeBuilderStore.getState().loadFromAutoRender(autoGraph())
    useNodeBuilderStore.getState().newEmptyGraph()
    const s = useNodeBuilderStore.getState()
    expect(s.regimeRemoved).toEqual([])
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
    expect(s.regimeRemoved).toEqual([])
    expect(hasEdits(s)).toBe(false)
  })

  it('keeps the store action named updateNodeParams', () => {
    // Browser verification finds this action by its name (handoff trap).
    expect(typeof useNodeBuilderStore.getState().updateNodeParams).toBe('function')
  })
})
