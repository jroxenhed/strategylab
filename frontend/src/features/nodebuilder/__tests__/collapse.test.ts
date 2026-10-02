/**
 * Collapse selection into subnet (F435 W6 item 6.D, spec S39).
 *
 * The op (operations/collapse.ts) and the Shift+C command
 * (commands/collapse.ts): boundary wiring, the merge for two outgoing
 * nodes, one undo step that restores the exact graph, the refusal cases
 * and the JSON shape the backend's flatten expects.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { findCommand, runCommand } from '../commands'
import { resetAssetUi, useAssetUi } from '../assetUi'
import { COLLAPSE_TEXT, collapseIntoSubnet, collapseProblem, collapseToast } from '../operations/collapse'

function node(id: string, type: string, position: [number, number], params: GraphNode['params'] = {}, parent: string | null = null): GraphNode {
  return { id, type, name: id, parent, params, position, display: false, bypass: false }
}

function wire(id: string, from: string, to: string, toPort = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port: toPort }
}

/** A -> B -> C -> D at the root (ticker, rsi, ema, above), D reads by name. */
function chain(): Graph {
  const g = emptyGraph()
  g.nodes = {
    a: node('a', 'ticker', [0, 0], { symbol: 'AAPL' }),
    b: node('b', 'rsi', [0, 120], { period: 14 }),
    c: node('c', 'ema', [0, 240], { period: 5, source: '@rsi' }),
    d: node('d', 'above', [0, 360], { left: '@ema', threshold: 50 }),
  }
  g.wires = [wire('w1', 'a', 'b'), wire('w2', 'b', 'c'), wire('w3', 'c', 'd')]
  return g
}

const s = () => useNodeBuilderStore.getState()

function load(g: Graph) {
  s().discardEdits()
  s().openGraph(g, { id: 'g_000000000001', rev: 1, name: 'chain' })
}

beforeEach(() => {
  resetAssetUi()
})

describe('collapseIntoSubnet (pure op)', () => {
  it('B and C of A->B->C->D: one input, one output, inner wire untouched', () => {
    const g = chain()
    const r = collapseIntoSubnet(g, ['b', 'c'])
    const next = r.graph
    const sub = next.nodes[r.subnetId]
    expect(sub.type).toBe('subnet')
    expect(sub.name).toBe('subnet1')
    expect(sub.parent).toBeNull()
    expect(sub.meta).toEqual({ view: 'card' })

    // Children B and C, plus the two boundary nodes.
    expect(next.nodes.b.parent).toBe(r.subnetId)
    expect(next.nodes.c.parent).toBe(r.subnetId)
    const inside = Object.values(next.nodes).filter(n => n.parent === r.subnetId)
    const inputs = inside.filter(n => n.type === 'subnet_input')
    const outputs = inside.filter(n => n.type === 'subnet_output')
    expect(inputs).toHaveLength(1)
    expect(outputs).toHaveLength(1)
    expect(inputs[0].name).toBe('in0')
    expect(inputs[0].params).toEqual({ port: 0 })
    expect(outputs[0].name).toBe('out')

    const has = (from: string, to: string, toPort: string) =>
      next.wires.some(w => w.from === from && w.to === to && w.to_port === toPort)
    // A -> subnet.in0, in0 -> B (same consumer port).
    expect(has('a', r.subnetId, 'in0')).toBe(true)
    expect(has(inputs[0].id, 'b', 'in0')).toBe(true)
    // C -> out, subnet -> D.
    expect(has('c', outputs[0].id, 'in0')).toBe(true)
    expect(has(r.subnetId, 'd', 'in0')).toBe(true)
    // B -> C unchanged (same object).
    expect(next.wires.find(w => w.id === 'w2')).toBe(g.wires[1])
    // No wire still crosses the subnet border.
    expect(next.wires.some(w => w.from === 'a' && w.to === 'b')).toBe(false)
    expect(next.wires.some(w => w.from === 'c' && w.to === 'd')).toBe(false)

    // Every consumer's attr params are untouched.
    expect(next.nodes.c.params).toEqual(g.nodes.c.params)
    expect(next.nodes.d.params).toEqual(g.nodes.d.params)
    // Positions stay absolute (nothing jumps).
    expect(next.nodes.b.position).toEqual([0, 120])
    expect(next.nodes.c.position).toEqual([0, 240])

    expect(r).toMatchObject({ count: 2, inputs: 1, outputs: 1, merged: false })
    // The input never mutates.
    expect(g).toEqual(chain())
  })

  it('keeps crossing wires at their own place in the wire list', () => {
    const g = chain()
    const { graph: next } = collapseIntoSubnet(g, ['b', 'c'])
    expect(next.wires.slice(0, 3).map(w => w.id)).toEqual(['w1', 'w2', 'w3'])
  })

  it('one source feeding two inside nodes gets one boundary', () => {
    const g = emptyGraph()
    g.nodes = {
      a: node('a', 'ticker', [0, 0]),
      b: node('b', 'rsi', [0, 120]),
      c: node('c', 'sma', [200, 120]),
      d: node('d', 'above', [100, 300]),
    }
    g.wires = [wire('w1', 'a', 'b'), wire('w2', 'a', 'c'), wire('w3', 'b', 'd', 'in0'), wire('w4', 'c', 'd', 'in1')]
    const r = collapseIntoSubnet(g, ['b', 'c'])
    const inputs = Object.values(r.graph.nodes).filter(n => n.type === 'subnet_input')
    expect(inputs).toHaveLength(1)
    expect(r.graph.wires.filter(w => w.from === inputs[0].id).map(w => w.to).sort()).toEqual(['b', 'c'])
    expect(r.graph.wires.filter(w => w.to === r.subnetId)).toHaveLength(1)
  })

  it('inputs are numbered by the sources\' x position', () => {
    const g = emptyGraph()
    g.nodes = {
      right: node('right', 'ticker', [400, 0]),
      left: node('left', 'ticker', [0, 0]),
      x: node('x', 'above', [200, 200]),
    }
    g.wires = [wire('w1', 'right', 'x', 'in1'), wire('w2', 'left', 'x', 'in0')]
    const r = collapseIntoSubnet(g, ['x'])
    const inputs = Object.values(r.graph.nodes).filter(n => n.type === 'subnet_input')
    const byName = Object.fromEntries(inputs.map(n => [n.name, n]))
    expect(r.graph.wires.find(w => w.from === 'left' && w.to === r.subnetId)?.to_port).toBe('in0')
    expect(r.graph.wires.find(w => w.from === 'right' && w.to === r.subnetId)?.to_port).toBe('in1')
    expect(byName.in0.params.port).toBe(0)
    expect(byName.in1.params.port).toBe(1)
  })

  it('two inside nodes feeding outside: one merge_out, one subnet_output, consumers read the subnet', () => {
    const g = emptyGraph()
    g.nodes = {
      a: node('a', 'ticker', [0, 0]),
      b: node('b', 'rsi', [0, 120]),
      c: node('c', 'sma', [200, 120]),
      d: node('d', 'above', [0, 300], { a: '@rsi', b: '@sma' }),
      e: node('e', 'entry', [200, 400]),
    }
    g.wires = [wire('w1', 'a', 'b'), wire('w2', 'a', 'c'), wire('w3', 'b', 'd', 'in0'), wire('w4', 'c', 'd', 'in1'), wire('w5', 'd', 'e')]
    const r = collapseIntoSubnet(g, ['b', 'c'])
    const next = r.graph
    const merges = Object.values(next.nodes).filter(n => n.type === 'merge' && n.parent === r.subnetId)
    const outs = Object.values(next.nodes).filter(n => n.type === 'subnet_output')
    expect(merges).toHaveLength(1)
    expect(merges[0].name).toBe('merge_out')
    expect(outs).toHaveLength(1)
    expect(next.wires.filter(w => w.to === merges[0].id).map(w => w.from).sort()).toEqual(['b', 'c'])
    expect(next.wires.some(w => w.from === merges[0].id && w.to === outs[0].id && w.to_port === 'in0')).toBe(true)
    // D's two ports now both read the subnet; its named reads are unchanged.
    expect(next.wires.filter(w => w.to === 'd').map(w => [w.from, w.to_port])).toEqual([[r.subnetId, 'in0'], [r.subnetId, 'in1']])
    expect(next.nodes.d.params).toEqual({ a: '@rsi', b: '@sma' })
    expect(r).toMatchObject({ merged: true, mergedFrom: 2, outputs: 1 })
    expect(collapseToast(r, 'subnet1')).toBe('Collapsed 2 nodes into subnet1 · 1 input · 1 output (merged from 2 nodes)')
  })

  it('the whole content of a network may be collapsed; the new subnet sits in that network', () => {
    const g = emptyGraph()
    g.nodes = {
      net: { ...node('net', 'subnet', [0, 0]) },
      x: node('x', 'rsi', [0, 100], {}, 'net'),
      y: node('y', 'sma', [0, 200], {}, 'net'),
    }
    g.wires = [wire('w1', 'x', 'y')]
    const r = collapseIntoSubnet(g, ['x', 'y'])
    expect(r.graph.nodes[r.subnetId].parent).toBe('net')
    expect(Object.values(r.graph.nodes).filter(n => n.parent === 'net').map(n => n.id)).toEqual([r.subnetId])
  })

  it('moves boxes and notes that sit inside the selection', () => {
    const g = chain()
    g.annotations = {
      boxes: [
        { id: 'bx1', label: 'mine', color: 'blue', rect: [-10, 100, 200, 220], members: ['b', 'c'], parent: null },
        { id: 'bx2', label: 'other', color: 'blue', rect: [-10, -10, 50, 50], members: ['a'], parent: null },
      ],
      notes: [
        { id: 'nt1', text: 'inside', rect: [10, 130, 40, 40], color: 'yellow', parent: null },
        { id: 'nt2', text: 'outside', rect: [500, 500, 40, 40], color: 'yellow', parent: null },
      ],
    }
    const r = collapseIntoSubnet(g, ['b', 'c'])
    const boxes = Object.fromEntries(r.graph.annotations.boxes.map(b => [b.id, b]))
    const notes = Object.fromEntries(r.graph.annotations.notes.map(n => [n.id, n]))
    expect(boxes.bx1.parent).toBe(r.subnetId)
    expect(boxes.bx2.parent).toBeNull()
    expect(notes.nt1.parent).toBe(r.subnetId)
    expect(notes.nt2.parent).toBeNull()
  })

  it('refuses terminals, groups, boundary nodes, mixed parents and locked assets', () => {
    const g = chain()
    g.nodes.e = node('e', 'entry', [0, 480])
    g.nodes.grp = node('grp', 'output_group', [400, 0])
    g.nodes.lk = { ...node('lk', 'subnet', [600, 0]), locked: true, asset_ref: { name: 'f', version: 1 } }
    g.nodes.inner = node('inner', 'rsi', [600, 100], {}, 'lk')
    g.nodes.sub = node('sub', 'subnet', [800, 0])
    g.nodes.bin = node('bin', 'subnet_input', [800, 100], { port: 0 }, 'sub')
    expect(collapseProblem(g, [])).toBe('nothing')
    expect(collapseProblem(g, ['ghost'])).toBe('nothing')
    expect(collapseProblem(g, ['b', 'e'])).toBe('terminal')
    expect(collapseProblem(g, ['b', 'grp'])).toBe('terminal')
    expect(collapseProblem(g, ['bin'])).toBe('terminal')
    expect(collapseProblem(g, ['b', 'inner'])).toBe('mixedParents')
    expect(collapseProblem(g, ['inner'])).toBe('locked')
    // A locked instance itself may be nested into a new subnet.
    expect(collapseProblem(g, ['lk', 'b'])).toBeNull()
    expect(() => collapseIntoSubnet(g, ['b', 'e'])).toThrow(COLLAPSE_TEXT.terminal)
    expect(() => collapseIntoSubnet({ ...g, readOnly: true }, ['b'])).toThrow(/read-only/)
  })
})

describe('Shift+C command', () => {
  it('is the canvas command for shift+c', () => {
    load(chain())
    expect(findCommand('shift+c', new Set(['canvas']))?.id).toBe('subnet.collapse')
  })

  it('collapses as ONE undo step, selects the subnet, and undo restores the exact graph', () => {
    const fixture = chain()
    load(fixture)
    const before = s().graph!
    const pastBefore = s().past.length
    s().setSelection({ nodeIds: ['b', 'c'], primary: 'b' })
    expect(runCommand('subnet.collapse')).toBe(true)
    expect(s().past.length).toBe(pastBefore + 1)
    const id = s().selectedNodeId!
    expect(s().graph!.nodes[id].type).toBe('subnet')
    expect(s().selectedNodeIds).toEqual([id])
    expect(useAssetUi.getState().toast?.text).toBe('Collapsed 2 nodes into subnet1 · 1 input · 1 output')

    s().undo()
    expect(s().graph).toBe(before)
    expect(s().graph).toEqual(fixture)
  })

  it('nothing selected: a status flash, no change', () => {
    load(chain())
    const g0 = s().graph
    s().setSelection({ nodeIds: [] })
    runCommand('subnet.collapse')
    expect(s().graph).toBe(g0)
    expect(s().flash?.text).toBe('Select nodes to collapse')
  })

  it('a selection with a terminal leaves the graph unchanged and says why', () => {
    const g = chain()
    g.nodes.e = node('e', 'entry', [0, 480])
    load(g)
    const g0 = s().graph
    s().setSelection({ nodeIds: ['c', 'e'] })
    runCommand('subnet.collapse')
    expect(s().graph).toBe(g0)
    expect(s().past.length).toBe(0)
    expect(useAssetUi.getState().toast?.text).toBe('Terminals and groups cannot go inside a subnet.')
  })

  it('mixed parents say so', () => {
    const g = chain()
    g.nodes.net = node('net', 'subnet', [400, 0])
    g.nodes.x = node('x', 'rsi', [400, 100], {}, 'net')
    load(g)
    s().setSelection({ nodeIds: ['b', 'x'] })
    runCommand('subnet.collapse')
    expect(useAssetUi.getState().toast?.text).toBe('Select nodes in one network to collapse them.')
  })

  it('gives the JSON shape the backend flatten expects', () => {
    load(chain())
    s().setSelection({ nodeIds: ['b', 'c'] })
    runCommand('subnet.collapse')
    const g = s().graph!
    const id = s().selectedNodeId!
    const types = Object.values(g.nodes).filter(n => n.parent === id).map(n => n.type).sort()
    expect(types).toEqual(['ema', 'rsi', 'subnet_input', 'subnet_output'])
    // Every wire connects siblings (D7) and every port id is in<k> / in.
    for (const w of g.wires) {
      expect(g.nodes[w.from].parent ?? null).toBe(g.nodes[w.to].parent ?? null)
      expect(w.to_port).toMatch(/^in\d*$/)
      expect(w.from_port).toBe('out')
    }
    // A JSON round trip keeps it (nothing unserialisable).
    expect(JSON.parse(JSON.stringify(g))).toEqual(g)
  })
})

describe('collapse review fixes (FE-01, FE-12)', () => {
  it('FE-01: every wire into the new subnet_output uses a numbered port the backend knows', () => {
    for (const ids of [['b', 'c'], ['b'], ['c']]) {
      const r = collapseIntoSubnet(chain(), ids)
      const out = Object.values(r.graph.nodes).find(n => n.type === 'subnet_output' && n.parent === r.subnetId)!
      const into = r.graph.wires.filter(w => w.to === out.id)
      expect(into).toHaveLength(1)
      expect(into[0].to_port).toBe('in0')
      expect(/^in\d+$/.test(into[0].to_port)).toBe(true)
    }
  })

  it('FE-12: refuses a selection with more outgoing producers than one merge takes', () => {
    const g = emptyGraph()
    const ids: string[] = []
    g.nodes = { sink: node('sink', 'or', [0, 900]) }
    g.wires = []
    for (let k = 0; k < 17; k++) {
      const id = `p${k}`
      ids.push(id)
      g.nodes[id] = node(id, 'rsi', [k * 200, 0])
      g.wires.push(wire(`w${k}`, id, 'sink', `in${k % 16}`))
    }
    expect(collapseProblem(g, ids)).toBe('tooManyOutputs')
    expect(() => collapseIntoSubnet(g, ids)).toThrow(COLLAPSE_TEXT.tooManyOutputs)
    // 16 producers still collapse, through one merge on in0..in15.
    expect(collapseProblem(g, ids.slice(0, 16))).toBeNull()
  })

  it('FE-12: a frame-view member counts by its drawn rectangle, not its stale stored position', () => {
    const g = emptyGraph()
    g.nodes = {
      // A frame network stored far away; its child sits near the other member.
      fr: node('fr', 'subnet', [5000, 5000]),
      kid: node('kid', 'rsi', [300, 0], {}, 'fr'),
      x: node('x', 'sma', [0, 0]),
    }
    const r = collapseIntoSubnet(g, ['fr', 'x'])
    const pos = r.graph.nodes[r.subnetId].position
    expect(pos[0]).toBeLessThan(1000)
    expect(pos[1]).toBeLessThan(1000)
  })
})

describe('collapse keeps what outside consumers read (2026-10-03 browser defect)', () => {
  /** tick -> rsi; rsi -> lo (below, @lo) -> entry; rsi -> hi (above, @hi) -> exit. Terminal reads left empty. */
  function rsiBand(): Graph {
    const g = emptyGraph()
    g.nodes = {
      tick: node('tick', 'ticker', [0, 0], { symbol: 'AAPL', interval: '1d' }),
      rsi: node('rsi', 'rsi', [0, 120], { period: 14 }),
      lo: node('lo', 'below', [0, 240], { threshold: 38, out: '@lo' }),
      hi: node('hi', 'above', [200, 240], { threshold: 62, out: '@hi' }),
      entry: node('entry', 'entry', [0, 400]),
      exit: node('exit', 'exit', [200, 400]),
    }
    g.wires = [
      wire('w0', 'tick', 'rsi'), wire('w1', 'rsi', 'lo'), wire('w2', 'rsi', 'hi'),
      wire('w3', 'lo', 'entry'), wire('w4', 'hi', 'exit'),
    ]
    return g
  }

  it('two outgoing producers: each terminal names the write it read before', () => {
    const g = rsiBand()
    const r = collapseIntoSubnet(g, ['rsi', 'lo', 'hi'])
    const next = r.graph
    expect(r).toMatchObject({ merged: true, mergedFrom: 2, outputs: 1 })
    expect(next.wires.filter(w => w.to === 'entry' || w.to === 'exit').map(w => [w.from, w.to])).toEqual([[r.subnetId, 'entry'], [r.subnetId, 'exit']])
    expect(next.nodes.entry.params).toEqual({ signal: '@lo' })
    expect(next.nodes.exit.params).toEqual({ signal: '@hi' })
    // A v3 wire never gets an attr; the inside nodes keep their params.
    expect(next.wires.every(w => w.attr == null)).toBe(true)
    for (const id of ['rsi', 'lo', 'hi']) expect(next.nodes[id].params).toEqual(g.nodes[id].params)
    expect(g).toEqual(rsiBand())
  })

  it('three outgoing producers: single reads and an AND/OR term list keep their names, in port order', () => {
    const g = rsiBand()
    g.nodes.mid = node('mid', 'above', [400, 240], { threshold: 50, out: '@mid' })
    g.nodes.vol = node('vol', 'above', [600, 240], { a: '@volume', threshold: 1e6, out: '@vol_hi' })
    g.nodes.both = node('both', 'or', [500, 400])
    g.wires.push(wire('w5', 'rsi', 'mid'), wire('w6', 'tick', 'vol'), wire('w7', 'vol', 'both', 'in0'), wire('w8', 'mid', 'both', 'in1'))
    const r = collapseIntoSubnet(g, ['rsi', 'lo', 'hi', 'mid'])
    const next = r.graph
    expect(r).toMatchObject({ merged: true, mergedFrom: 3, outputs: 1 })
    expect(next.nodes.entry.params).toEqual({ signal: '@lo' })
    expect(next.nodes.exit.params).toEqual({ signal: '@hi' })
    // The outside wire (vol, in0) keeps its own name; the re-routed one (in1) gets @mid.
    expect(next.nodes.both.params).toEqual({ terms: ['@vol_hi', '@mid'] })
    expect(next.nodes.vol.params).toEqual(g.nodes.vol.params)
  })

  it('reads that already name an attribute are left alone', () => {
    const g = rsiBand()
    g.nodes.entry = { ...g.nodes.entry, params: { signal: '@hi' } }
    const next = collapseIntoSubnet(g, ['rsi', 'lo', 'hi']).graph
    expect(next.nodes.entry.params).toEqual({ signal: '@hi' })
    expect(next.nodes.exit.params).toEqual({ signal: '@hi' })
  })

  it('a selected subnet that feeds outside: the name comes from what feeds its output', () => {
    const g = rsiBand()
    // Put hi in an inner subnet first, then collapse rsi, lo and that subnet.
    const inner = collapseIntoSubnet(g, ['hi'])
    const r = collapseIntoSubnet(inner.graph, ['rsi', 'lo', inner.subnetId])
    expect(r.merged).toBe(true)
    expect(r.graph.nodes.entry.params).toEqual({ signal: '@lo' })
    expect(r.graph.nodes.exit.params).toEqual({ signal: '@hi' })
  })

  it('one outgoing producer: no merge, terminal reads stay empty (flatten resolves the subnet to the producer)', () => {
    const g = rsiBand()
    g.wires = g.wires.filter(w => w.id !== 'w2' && w.id !== 'w4')
    delete g.nodes.hi
    delete g.nodes.exit
    const r = collapseIntoSubnet(g, ['rsi', 'lo'])
    expect(r.merged).toBe(false)
    expect(r.graph.nodes.entry.params).toEqual({})
  })
})
