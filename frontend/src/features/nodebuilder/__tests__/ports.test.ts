/**
 * Ports and connecting (F435 W2 item 2.E, spec S08, plan D4).
 *
 * Pure checks of `portsOf`, `connectionProblem` and `connectWire`, on the
 * real catalog and on W2-shaped test entries (w2Catalog.fixture.ts), so
 * node types that arrive with the regenerated catalog are covered too.
 */

import { describe, it, expect, vi } from 'vitest'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import {
  connectionProblem,
  connectWire,
  defaultReadPatch,
  portFraction,
  portsOf,
  readParamForPort,
  withUniqueWrites,
  uniqueAttrName,
} from '../streamLabels'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

function node(id: string, type: string, params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false }
}

function wire(id: string, from: string, to: string, to_port: string, attr?: string): GraphWire {
  return { id, from, to, from_port: 'out', to_port, ...(attr ? { attr } : {}) }
}

function graphOf(nodes: GraphNode[], wires: GraphWire[] = []): Graph {
  return { ...emptyGraph(), nodes: Object.fromEntries(nodes.map(n => [n.id, n])), wires }
}

describe('portsOf', () => {
  it('a comparison draws two ports labelled a and b and no spare', () => {
    const ports = portsOf('crosses_above', [])
    expect(ports.map(p => [p.id, p.label, p.spare])).toEqual([['in0', 'a', false], ['in1', 'b', false]])
    expect(ports[1].optional).toBe(true)
  })

  it('a dynamic node with two wires draws three ports, the last a spare', () => {
    const ports = portsOf('and', ['in0', 'in1'])
    expect(ports.map(p => p.id)).toEqual(['in0', 'in1', 'in2'])
    expect(ports.map(p => p.spare)).toEqual([false, false, true])
    expect(ports.map(p => p.connected)).toEqual([true, true, false])
  })

  it('a dynamic node with no wires shrinks to its minimum plus the spare', () => {
    expect(portsOf('and', []).map(p => p.id)).toEqual(['in0', 'in1'])
    expect(portsOf('t2_and', []).map(p => p.spare)).toEqual([false, true])
  })

  it('a dynamic node at its maximum draws no spare', () => {
    const ports = portsOf('t2_and', ['in0', 'in1', 'in2', 'in3'])
    expect(ports).toHaveLength(4)
    expect(ports.some(p => p.spare)).toBe(false)
  })

  it('a Ticker has no input port; Entry has one', () => {
    expect(portsOf('ticker', [])).toEqual([])
    expect(portsOf('entry', []).map(p => p.label)).toEqual(['signal'])
  })

  it('an unknown type draws the ports its wires use (at least one)', () => {
    expect(portsOf('no_such_node', []).map(p => p.id)).toEqual(['in0'])
    expect(portsOf('no_such_node', ['in2']).map(p => p.id)).toEqual(['in0', 'in1', 'in2'])
  })

  it('a wired port past the spec is still drawn, so the wire stays visible', () => {
    expect(portsOf('rsi', ['in0', 'in1']).map(p => p.id)).toEqual(['in0', 'in1'])
  })

  it('places ports evenly along the top edge', () => {
    expect(portFraction(0, 1)).toBe(0.5)
    expect([0, 1].map(k => portFraction(k, 2))).toEqual([0.3335, 0.6665])
  })
})

describe('connectionProblem (isValidConnection)', () => {
  const g = graphOf(
    [node('t', 'ticker'), node('r', 'rsi'), node('x', 'crosses_above'), node('a', 'and'), node('e', 'entry')],
    [wire('w1', 't', 'r', 'in0'), wire('w2', 'r', 'x', 'in0'), wire('w3', 'x', 'a', 'in0')],
  )
  const c = (source: string, target: string, targetHandle: string | null = 'in0') =>
    connectionProblem(g, { source, target, sourceHandle: 'out', targetHandle })

  it('allows a free port', () => {
    expect(c('r', 'x', 'in1')).toBeNull()
    expect(c('x', 'e')).toBeNull()
  })

  it('blocks a self loop', () => {
    expect(c('r', 'r')).toBe('self')
  })

  it('blocks a wire that closes a cycle', () => {
    expect(c('a', 'r', 'in0')).not.toBeNull()
    expect(c('x', 'r', 'in1')).not.toBeNull()
    const g2 = graphOf([node('p', 'rsi'), node('q', 'rsi')], [wire('w', 'p', 'q', 'in0')])
    expect(connectionProblem(g2, { source: 'q', target: 'p', sourceHandle: 'out', targetHandle: null })).toBe('cycle')
  })

  it('blocks a second wire into in0 of an rsi node', () => {
    const g2 = graphOf([node('t', 'ticker'), node('t2', 'ticker'), node('r', 'rsi')], [wire('w', 't', 'r', 'in0')])
    expect(connectionProblem(g2, { source: 't2', target: 'r', sourceHandle: 'out', targetHandle: 'in0' })).toBe('full')
  })

  it('allows the spare of a dynamic node but not a used port', () => {
    expect(c('r', 'a', 'in1')).toBeNull()
    expect(c('r', 'a', 'in0')).toBe('full')
  })

  it('blocks a port past the node\'s last one', () => {
    expect(c('r', 'x', 'in2')).toBe('no_port')
  })

  it('blocks wiring into a Ticker or out of a terminal', () => {
    expect(c('r', 't')).toBe('no_port')
    expect(c('e', 'x', 'in1')).toBe('no_port')
  })

  it('blocks a missing end and a wrong handle', () => {
    expect(connectionProblem(g, { source: null, target: 'r' })).toBe('missing')
    expect(connectionProblem(g, { source: 't', target: 'r', sourceHandle: 'in0', targetHandle: 'in0' })).toBe('direction')
  })
})

describe('connectWire', () => {
  it('wires the spare port of a dynamic node by handle id', () => {
    const g = graphOf(
      [node('r1', 'rsi'), node('r2', 'rsi'), node('r3', 'rsi'), node('a', 'and')],
      [wire('w1', 'r1', 'a', 'in0'), wire('w2', 'r2', 'a', 'in1')],
    )
    const next = connectWire(g, { id: 'w3', from: 'r3', to: 'a', to_port: 'in2' })
    expect(next.wires.at(-1)).toMatchObject({ id: 'w3', from_port: 'out', to_port: 'in2' })
    expect(portsOf('and', next.wires.filter(w => w.to === 'a').map(w => w.to_port)).map(p => p.id))
      .toEqual(['in0', 'in1', 'in2', 'in3'])
  })

  it('writes no wire attr into a consumer that has params but reads none, even in a v2 graph (FP-9)', () => {
    // merge passes the whole stream; t2_tod (time_of_day) reads nothing.
    // Only a consumer with no param specs at all keeps the v2 label.
    const g = { ...graphOf([node('t', 'ticker'), node('m', 'merge'), node('tod', 't2_tod')]), _version: 2 as const }
    let next = connectWire(g, { id: 'w1', from: 't', to: 'm' })
    next = connectWire(next, { id: 'w2', from: 't', to: 'tod' })
    expect(next.wires.map(w => w.attr)).toEqual([undefined, undefined])
  })

  it('refuses a second wire into an explicit port that already has one (FP-1)', () => {
    // The Tab-menu create after a drag from a wired input passes that port.
    const g = graphOf(
      [node('t', 'ticker'), node('r', 'rsi'), node('s', 'sma')],
      [wire('w1', 't', 'r', 'in0')],
    )
    expect(() => connectWire(g, { id: 'w2', from: 's', to: 'r', to_port: 'in0' })).toThrow(/already has a wire/)
    // A full port of a dynamic node is refused too; the spare is fine.
    const a = graphOf(
      [node('r1', 'rsi'), node('r2', 'rsi'), node('a', 'and')],
      [wire('w1', 'r1', 'a', 'in0')],
    )
    expect(() => connectWire(a, { id: 'w2', from: 'r2', to: 'a', to_port: 'in0' })).toThrow()
    expect(connectWire(a, { id: 'w2', from: 'r2', to: 'a', to_port: 'in1' }).wires).toHaveLength(2)
  })

  it('never writes a wire attr into a v3 graph (plan 4.1)', () => {
    const g = graphOf([node('t', 'ticker'), node('m', 'merge'), node('r', 'rsi')])
    expect(g._version).toBe(3)
    let next = connectWire(g, { id: 'w1', from: 't', to: 'm' })
    next = connectWire(next, { id: 'w2', from: 't', to: 'r', attr: '@close' })
    expect(next.wires.map(w => w.attr)).toEqual([undefined, undefined])
    expect(next.nodes.r.params.source).toBe('@close')
  })

  it('fills an empty attr param from the source primary write, in one graph change', () => {
    const g = graphOf([node('t', 'ticker'), node('i', 't2_ind', { period: 14, source: null, out: '@t2' })])
    const next = connectWire(g, { id: 'w', from: 't', to: 'i', to_port: 'in0' })
    expect(next.nodes.i.params.source).toBe('@close')
    // A v3 wire carries no label: the param is the operand.
    expect(next.wires[0].attr).toBeUndefined()
  })

  it('operands come from the port: a wire into b fills b, not a', () => {
    const g = graphOf([
      node('i', 't2_ind', { source: '@close', out: '@t2' }),
      node('x', 't2_cmp', { a: null, b: null, out: '@cmp' }),
    ])
    const next = connectWire(g, { id: 'w', from: 'i', to: 'x', to_port: 'in1' })
    expect(next.nodes.x.params).toMatchObject({ a: null, b: '@t2' })
  })

  it('never overwrites a param that is already set', () => {
    const g = graphOf([
      node('i', 't2_ind', { source: '@close', out: '@t2' }),
      node('x', 't2_cmp', { a: '@other', b: null, out: '@cmp' }),
    ])
    expect(defaultReadPatch(g, { from: 'i', to: 'x', to_port: 'in0' })).toBeNull()
  })

  it('adds to an attr_list on a dynamic node', () => {
    const g = graphOf([
      node('c', 't2_cmp', { a: '@close', b: null, out: '@cmp' }),
      node('a', 't2_and', { terms: ['@x'], out: '@all' }),
    ])
    expect(readParamForPort('t2_and', 'in1')?.name).toBe('terms')
    const next = connectWire(g, { id: 'w', from: 'c', to: 'a', to_port: 'in1' })
    expect(next.nodes.a.params.terms).toEqual(['@x', '@cmp'])
  })

  it('throws on a cycle like addWire', () => {
    const g = graphOf([node('p', 'rsi'), node('q', 'rsi')], [wire('w', 'p', 'q', 'in0')])
    expect(() => connectWire(g, { id: 'w2', from: 'q', to: 'p' })).toThrow()
  })
})

describe('unique write names for a new node', () => {
  it('numbers a taken name', () => {
    expect(uniqueAttrName('@rsi', new Set())).toBe('@rsi')
    expect(uniqueAttrName('@rsi', new Set(['@rsi']))).toBe('@rsi_2')
    expect(uniqueAttrName('@rsi', new Set(['@rsi', '@rsi_2']))).toBe('@rsi_3')
  })

  it('makes every write param of a new node unique in the graph', () => {
    const g = graphOf([node('m', 't2_macd', { source: '@close', out_line: '@macd', out_signal: '@macd_signal', out_hist: '@macd_hist' })])
    const params = withUniqueWrites(g, 't2_macd', { source: null, out_line: '@macd', out_signal: '@macd_signal', out_hist: '@macd_hist' })
    expect(params).toMatchObject({ out_line: '@macd_2', out_signal: '@macd_signal_2', out_hist: '@macd_hist_2' })
  })
})
