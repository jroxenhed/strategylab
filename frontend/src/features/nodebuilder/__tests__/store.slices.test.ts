/**
 * The store split into slices (F435 W3 pre-step 3.0): every slice is in the
 * one store, undo covers whatever lives in the graph (annotations, flags),
 * view and status state are never history, and a commit drops selected ids
 * that no longer exist.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { IDLE_COOK } from '../store/status'
import { ROOT_NETWORK } from '../store/view'

const s = () => useNodeBuilderStore.getState()

function node(id: string, type: string): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [0, 0], display: false, bypass: false }
}

function graph(): Graph {
  const g = emptyGraph()
  g.nodes = { t: node('t', 'ticker'), r: node('r', 'rsi'), e: node('e', 'entry') }
  g.wires = [
    { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
    { id: 'w2', from: 'r', to: 'e', from_port: 'out', to_port: 'in0' },
  ]
  g.annotations = {
    boxes: [{ id: 'b1', label: 'box', color: 'network', rect: [0, 0, 320, 200], members: ['t'], parent: null }],
    notes: [],
  }
  return g
}

beforeEach(() => {
  s().openGraph(graph(), { id: null, rev: 0, name: 'test' })
  useNodeBuilderStore.setState({ flash: null, cook: IDLE_COOK })
})

describe('slices in one store', () => {
  it('has the members of every slice', () => {
    const st = s()
    // graph slice
    expect(typeof st.commit).toBe('function')
    expect(typeof st.undo).toBe('function')
    // Browser verification finds this action by name (standing rule).
    expect(typeof st.updateNodeParams).toBe('function')
    // selection slice
    expect(st.selectedNodeIds).toEqual([])
    expect(typeof st.setSelection).toBe('function')
    expect(typeof st.mirrorSelection).toBe('function')
    // view slice
    expect(st.network).toBe(ROOT_NETWORK)
    expect(typeof st.setViewport).toBe('function')
    // status slice
    expect(typeof st.showFlash).toBe('function')
    expect(st.cook.phase).toBe('idle')
    // store.ts itself
    expect(typeof st.loadFromAutoRender).toBe('function')
  })
})

describe('undo covers what lives in the graph', () => {
  it('an annotation edit through commit is one undo step', () => {
    const g0 = s().graph
    s().commit('rename box', g => ({
      ...g,
      annotations: { ...g.annotations, boxes: g.annotations.boxes.map(b => ({ ...b, label: 'signals' })) },
    }))
    expect(s().graph!.annotations.boxes[0].label).toBe('signals')
    expect(s().past).toHaveLength(1)
    expect(s().dirty).toBe(true)
    s().undo()
    expect(s().graph).toBe(g0)
    expect(s().graph!.annotations.boxes[0].label).toBe('box')
  })

  it('a flag edit through commit is undoable', () => {
    s().commit('bypass r', g => ({ ...g, nodes: { ...g.nodes, r: { ...g.nodes.r, bypass: true } } }))
    expect(s().graph!.nodes.r.bypass).toBe(true)
    s().undo()
    expect(s().graph!.nodes.r.bypass).toBe(false)
    s().redo()
    expect(s().graph!.nodes.r.bypass).toBe(true)
  })
})

describe('view and status are not history', () => {
  it('setViewport records per network and adds no undo step', () => {
    const seq = s().commitSeq
    s().setViewport({ x: 10, y: 20, zoom: 0.5 })
    expect(s().viewport).toEqual({ x: 10, y: 20, zoom: 0.5 })
    expect(s().viewports[ROOT_NETWORK]).toEqual({ x: 10, y: 20, zoom: 0.5 })
    expect(s().past).toHaveLength(0)
    expect(s().dirty).toBe(false)
    expect(s().commitSeq).toBe(seq)
  })

  it('a new graph starts from the home view', () => {
    s().setViewport({ x: 10, y: 20, zoom: 0.5 })
    s().newGraph()
    expect(s().viewport).toEqual({ x: 0, y: 0, zoom: 1 })
    expect(s().viewports).toEqual({})
  })

  it('showFlash bumps seq even for the same text', () => {
    s().showFlash('Select a node first')
    const first = s().flash!
    s().showFlash('Select a node first')
    expect(s().flash!.text).toBe('Select a node first')
    expect(s().flash!.seq).toBe(first.seq + 1)
    expect(s().past).toHaveLength(0)
  })

  it('setCook merges fields and writes nothing when they match', () => {
    s().setCook({ phase: 'cooking', startedAt: 5 })
    expect(s().cook).toMatchObject({ phase: 'cooking', startedAt: 5, kind: 'backtest' })
    const before = s().cook
    s().setCook({ phase: 'cooking' })
    expect(s().cook).toBe(before)
  })
})

describe('selection slice', () => {
  it('setSelection sets every list, picks the primary and bumps the request seq', () => {
    const seq = s().selectionRequestSeq
    s().setSelection({ nodeIds: ['t', 'r'], wireIds: ['w1'], annotationIds: ['b1'] })
    expect(s().selectedNodeIds).toEqual(['t', 'r'])
    expect(s().selectedNodeId).toBe('t')
    expect(s().selectedWireIds).toEqual(['w1'])
    expect(s().selectedAnnotationIds).toEqual(['b1'])
    expect(s().selectionRequestSeq).toBe(seq + 1)
    s().setSelection({ nodeIds: ['t', 'r'], primary: 'r' })
    expect(s().selectedNodeId).toBe('r')
  })

  it('mirrorSelection keeps the primary while it is still selected and does not bump the seq', () => {
    s().select('r')
    const seq = s().selectionRequestSeq
    s().mirrorSelection({ nodeIds: ['t', 'r'] })
    expect(s().selectedNodeId).toBe('r')
    expect(s().selectedNodeIds).toEqual(['t', 'r'])
    expect(s().selectionRequestSeq).toBe(seq)
    let writes = 0
    const unsub = useNodeBuilderStore.subscribe(() => { writes++ })
    s().mirrorSelection({ nodeIds: ['t', 'r'] })
    unsub()
    expect(writes).toBe(0)
  })

  it('select keeps a multi-selection that holds the node, and collapses it otherwise', () => {
    s().setSelection({ nodeIds: ['t', 'r'] })
    s().select('r')
    expect(s().selectedNodeId).toBe('r')
    expect(s().selectedNodeIds).toEqual(['t', 'r'])
    s().select('e')
    expect(s().selectedNodeIds).toEqual(['e'])
    s().select(null)
    expect(s().selectedNodeIds).toEqual([])
    expect(s().selectedNodeId).toBeNull()
  })

  it('a commit drops selected nodes, wires and boxes that are gone; undo does not bring them back', () => {
    s().setSelection({ nodeIds: ['r', 'e'], wireIds: ['w1', 'w2'], annotationIds: ['b1'], primary: 'r' })
    s().commit('delete', g => {
      const nodes = { ...g.nodes }
      delete nodes.r
      return { ...g, nodes, wires: g.wires.filter(w => w.id === 'w2' ? false : true), annotations: { boxes: [], notes: [] } }
    })
    expect(s().selectedNodeIds).toEqual(['e'])
    expect(s().selectedNodeId).toBeNull()
    expect(s().selectedWireIds).toEqual(['w1'])
    expect(s().selectedAnnotationIds).toEqual([])
    s().undo()
    expect(s().selectedNodeIds).toEqual(['e'])
  })

  it('a commit that removes nothing selected keeps the same list objects', () => {
    s().setSelection({ nodeIds: ['t'], wireIds: ['w1'] })
    const { selectedNodeIds, selectedWireIds } = s()
    s().moveNode('e', [5, 5])
    expect(s().selectedNodeIds).toBe(selectedNodeIds)
    expect(s().selectedWireIds).toBe(selectedWireIds)
  })

  it('loading a graph clears the selection', () => {
    s().setSelection({ nodeIds: ['t'], wireIds: ['w1'], annotationIds: ['b1'] })
    s().openGraph(graph(), { id: null, rev: 0, name: 'again' })
    expect(s().selectedNodeIds).toEqual([])
    expect(s().selectedWireIds).toEqual([])
    expect(s().selectedAnnotationIds).toEqual([])
  })
})
