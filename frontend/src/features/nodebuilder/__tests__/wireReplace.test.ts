/**
 * F457: a wire dropped on an input port that already has one replaces it
 * (Houdini). One graph change, the consumer's default read follows the new
 * source, and every other wire rule still refuses (self-loop, cycle, no
 * port, across a network edge). Frame ports (`in<k>`, `bnd:<id>`) and
 * routed wires replace the same way.
 */

import { describe, it, expect } from 'vitest'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import {
  connectReplacing,
  dropOccupant,
  liftWire,
  networkConnectionProblem,
  networkOccupant,
  occupantOf,
  replaceNetworkProblem,
  replaceProblemIgnoring,
} from '../operations/wires'
import { BOUNDARY_HANDLE_PREFIX } from '../rfMapping'

function gnode(id: string, type: string, params: Record<string, unknown> = {}, parent: string | null = null): GraphNode {
  return { id, type, name: id, parent, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

/** t feeds two RSIs; r1 feeds the comparison `cmp` (a = @rsi), which feeds the entry. */
function plainGraph(a: string = '@rsi'): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      r1: gnode('r1', 'rsi', { period: 14, source: '@close', out: '@rsi' }),
      r2: gnode('r2', 'rsi', { period: 7, source: '@close', out: '@rsi_fast' }),
      cmp: gnode('cmp', 'above', { a, threshold: 70, out: '@above' }),
      ent: gnode('ent', 'entry', { signal: '@above' }),
    },
    wires: [
      { id: 'w_t_r1', from: 't', to: 'r1', from_port: 'out', to_port: 'in0' },
      { id: 'w_t_r2', from: 't', to: 'r2', from_port: 'out', to_port: 'in0' },
      { id: 'w_r1_cmp', from: 'r1', to: 'cmp', from_port: 'out', to_port: 'in0' },
      { id: 'w_cmp_ent', from: 'cmp', to: 'ent', from_port: 'out', to_port: 'in0' },
    ],
  }
}

const into = (g: Graph, to: string, port: string) => g.wires.filter(w => w.to === to && w.to_port === port)

describe('replace on a plain port', () => {
  const c = { source: 'r2', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' }

  it('finds the wire on the port, and the old check calls it full', () => {
    const g = plainGraph()
    expect(occupantOf(g, c)?.id).toBe('w_r1_cmp')
    expect(networkOccupant(g, c)?.id).toBe('w_r1_cmp')
    expect(networkConnectionProblem(g, c)).toBe('full')
    expect(replaceNetworkProblem(g, c)).toBeNull()
    expect(replaceProblemIgnoring(g, c, null)).toBeNull()
  })

  it('swaps the wire in one change and the read follows the new source', () => {
    const next = connectReplacing(plainGraph(), c)
    expect(next.wires.some(w => w.id === 'w_r1_cmp')).toBe(false)
    expect(into(next, 'cmp', 'in0').map(w => w.from)).toEqual(['r2'])
    expect(next.nodes.cmp.params.a).toBe('@rsi_fast')
  })

  it('keeps a read the user picked', () => {
    const next = connectReplacing(plainGraph('@close'), c)
    expect(into(next, 'cmp', 'in0').map(w => w.from)).toEqual(['r2'])
    expect(next.nodes.cmp.params.a).toBe('@close')
  })

  it('dropping the same source on the port again changes nothing', () => {
    const g = plainGraph()
    expect(connectReplacing(g, { ...c, source: 'r1' })).toBe(g)
  })

  it('a free port is a plain new wire', () => {
    const g = plainGraph()
    const free = { source: 'r2', target: 'cmp', sourceHandle: 'out', targetHandle: 'in1' }
    expect(networkOccupant(g, free)).toBeNull()
    const next = connectReplacing(g, free)
    expect(next.wires).toHaveLength(5)
    expect(into(next, 'cmp', 'in0').map(w => w.id)).toEqual(['w_r1_cmp'])
  })

  it('still refuses a loop, a self-loop and a port past the last one', () => {
    const g = plainGraph()
    // cmp → r1 in0 (taken by t → r1) would close r1 → cmp → r1.
    expect(replaceNetworkProblem(g, { source: 'cmp', target: 'r1', sourceHandle: 'out', targetHandle: 'in0' })).toBe('cycle')
    expect(() => connectReplacing(g, { source: 'cmp', target: 'r1', sourceHandle: 'out', targetHandle: 'in0' })).toThrow()
    expect(replaceNetworkProblem(g, { source: 'cmp', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' })).toBe('self')
    expect(replaceNetworkProblem(g, { source: 'r2', target: 'cmp', sourceHandle: 'out', targetHandle: 'in9' })).toBe('no_port')
  })

  it('a wire being moved is never its own occupant', () => {
    const g = plainGraph()
    expect(occupantOf(g, c, 'w_r1_cmp')).toBeNull()
    expect(networkOccupant(g, c, 'w_r1_cmp')).toBeNull()
  })
})

describe('liftWire', () => {
  it('empties the read the old wire filled, and leaves a picked one', () => {
    const wire = plainGraph().wires.find(w => w.id === 'w_r1_cmp')!
    expect(liftWire(plainGraph(), wire).nodes.cmp.params.a).toBeNull()
    expect(liftWire(plainGraph('@close'), wire).nodes.cmp.params.a).toBe('@close')
  })

  it('takes back the term an AND list got from the wire', () => {
    const g: Graph = {
      ...emptyGraph(),
      nodes: {
        c1: gnode('c1', 'above', { threshold: 1, out: '@c1' }),
        c2: gnode('c2', 'below', { threshold: 1, out: '@c2' }),
        and: gnode('and', 'and', { terms: ['@c1'], out: '@and' }),
      },
      wires: [{ id: 'w1', from: 'c1', to: 'and', from_port: 'out', to_port: 'in0' }],
    }
    const next = connectReplacing(g, { source: 'c2', target: 'and', sourceHandle: 'out', targetHandle: 'in0' })
    expect(into(next, 'and', 'in0').map(w => w.from)).toEqual(['c2'])
    expect(next.nodes.and.params.terms).toEqual(['@c2'])
  })
})

/**
 * An Output Group `grp` with a port in0 (boundary `b0`, fed by t) into an
 * RSI `rin`, and an output boundary `so` fed by `rin`. Outside: a second
 * Ticker `t2`, and `grp2`, another group.
 */
function netGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      t2: gnode('t2', 'ticker', { symbol: 'MSFT', interval: '1d' }),
      grp: gnode('grp', 'subnet', {}),
      b0: gnode('b0', 'subnet_input', { port: 0 }, 'grp'),
      rin: gnode('rin', 'rsi', { period: 14, out: '@rsi' }, 'grp'),
      rin2: gnode('rin2', 'rsi', { period: 7, out: '@rsi7' }, 'grp'),
      so: gnode('so', 'subnet_output', {}, 'grp'),
      grp2: gnode('grp2', 'output_group', { direction: 'short' }),
      cmp2: gnode('cmp2', 'above', { threshold: 70 }, 'grp2'),
    },
    wires: [
      { id: 'w_t_grp', from: 't', to: 'grp', from_port: 'out', to_port: 'in0' },
      { id: 'w_b0_rin', from: 'b0', to: 'rin', from_port: 'out', to_port: 'in0' },
      { id: 'w_rin_so', from: 'rin', to: 'so', from_port: 'out', to_port: 'in0' },
    ],
  }
}

describe('replace on network ports', () => {
  it("a frame's inner output handle (bnd:) replaces the wire into its boundary node", () => {
    const g = netGraph()
    const c = { source: 'rin2', target: 'grp', sourceHandle: 'out', targetHandle: `${BOUNDARY_HANDLE_PREFIX}so` }
    expect(networkOccupant(g, c)?.id).toBe('w_rin_so')
    expect(networkConnectionProblem(g, c)).toBe('full')
    expect(replaceNetworkProblem(g, c)).toBeNull()
    const next = connectReplacing(g, c)
    expect(into(next, 'so', 'in0').map(w => w.from)).toEqual(['rin2'])
  })

  it("a frame's top port replaces the outside wire into it; the boundary node stays", () => {
    const g = netGraph()
    const c = { source: 't2', target: 'grp', sourceHandle: 'out', targetHandle: 'in0' }
    expect(networkOccupant(g, c)?.id).toBe('w_t_grp')
    const next = connectReplacing(g, c)
    expect(into(next, 'grp', 'in0').map(w => w.from)).toEqual(['t2'])
    expect(next.nodes.b0).toBeDefined()
    expect(into(next, 'rin', 'in0').map(w => w.id)).toEqual(['w_b0_rin'])
  })

  it('a routed wire replaces the inside wire on the port it routes to', () => {
    const g = netGraph()
    const c = { source: 't2', target: 'rin', sourceHandle: 'out', targetHandle: 'in0' }
    expect(networkOccupant(g, c)?.id).toBe('w_b0_rin')
    const next = connectReplacing(g, c)
    const feeds = into(next, 'rin', 'in0')
    expect(feeds).toHaveLength(1)
    const boundary = next.nodes[feeds[0].from]
    expect(boundary.type).toBe('subnet_input')
    expect(boundary.parent).toBe('grp')
    // The new boundary's frame port is fed by t2, from outside.
    const port = `in${boundary.params.port}`
    expect(into(next, 'grp', port).map(w => w.from)).toEqual(['t2'])
  })

  it('a wire across a network edge is still refused', () => {
    const g = netGraph()
    const c = { source: 'rin', target: 'cmp2', sourceHandle: 'out', targetHandle: 'in0' }
    expect(networkOccupant(g, c)).toBeNull()
    expect(replaceNetworkProblem(g, c)).toBe('crosses')
    expect(() => connectReplacing(g, c)).toThrow()
  })
})

/**
 * Outside: t feeds ema (@ema) and sma (@sma). Inside the subnet `grp`:
 * `cmp` (Above, a = @ema) is fed from ema through boundary b1 (port 0), as
 * a routed connect leaves it; `rin` (@rsi) sits inside too.
 */
function routedGraph(a: string = '@ema'): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      ema: gnode('ema', 'ema', { period: 10, source: '@close', out: '@ema' }),
      sma: gnode('sma', 'sma', { period: 20, source: '@close', out: '@sma' }),
      grp: gnode('grp', 'subnet', {}),
      b1: gnode('b1', 'subnet_input', { port: 0 }, 'grp'),
      cmp: gnode('cmp', 'above', { a, threshold: 1, out: '@above' }, 'grp'),
      rin: gnode('rin', 'rsi', { period: 14, source: '@close', out: '@rsi' }, 'grp'),
    },
    wires: [
      { id: 'w_t_ema', from: 't', to: 'ema', from_port: 'out', to_port: 'in0' },
      { id: 'w_t_sma', from: 't', to: 'sma', from_port: 'out', to_port: 'in0' },
      { id: 'w_ema_grp', from: 'ema', to: 'grp', from_port: 'out', to_port: 'in0' },
      { id: 'w_b1_cmp', from: 'b1', to: 'cmp', from_port: 'out', to_port: 'in0' },
    ],
  }
}

describe('replace inside a frame: reads follow, no port left over (F457 LG-1, LG-4, LG-5)', () => {
  it('a routed replace makes the consumer read the new source and drops the old frame port', () => {
    const next = connectReplacing(routedGraph(), { source: 'sma', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' })
    expect(next.nodes.cmp.params.a).toBe('@sma')
    // b1 fed nothing else: it goes, with its outside wire.
    expect(next.nodes.b1).toBeUndefined()
    expect(next.wires.some(w => w.from === 'ema' && w.to === 'grp')).toBe(false)
    const feed = into(next, 'cmp', 'in0')
    expect(feed).toHaveLength(1)
    const port = `in${next.nodes[feed[0].from].params.port}`
    expect(into(next, 'grp', port).map(w => w.from)).toEqual(['sma'])
  })

  it('a sibling inside the frame replaces a port wire the same way', () => {
    const next = connectReplacing(routedGraph(), { source: 'rin', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' })
    expect(into(next, 'cmp', 'in0').map(w => w.from)).toEqual(['rin'])
    expect(next.nodes.cmp.params.a).toBe('@rsi')
    expect(next.nodes.b1).toBeUndefined()
    expect(into(next, 'grp', 'in0')).toEqual([])
  })

  it('a read the user picked is kept, and a port that still feeds another node stays', () => {
    const g = routedGraph('@close')
    g.nodes.cmp2 = gnode('cmp2', 'below', { a: '@ema', threshold: 1, out: '@below' }, 'grp')
    g.wires.push({ id: 'w_b1_cmp2', from: 'b1', to: 'cmp2', from_port: 'out', to_port: 'in0' })
    const next = connectReplacing(g, { source: 'rin', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' })
    expect(next.nodes.cmp.params.a).toBe('@close')
    expect(next.nodes.b1).toBeDefined()
    expect(into(next, 'grp', 'in0').map(w => w.id)).toEqual(['w_ema_grp'])
    expect(next.nodes.cmp2.params.a).toBe('@ema')
  })

  it("a replace on the frame's top port makes the nodes inside read the new source", () => {
    const g = routedGraph()
    g.nodes.cmp2 = gnode('cmp2', 'below', { a: '@close', threshold: 1, out: '@below' }, 'grp')
    g.wires.push({ id: 'w_b1_cmp2', from: 'b1', to: 'cmp2', from_port: 'out', to_port: 'in0' })
    const next = connectReplacing(g, { source: 'sma', target: 'grp', sourceHandle: 'out', targetHandle: 'in0' })
    expect(into(next, 'grp', 'in0').map(w => w.from)).toEqual(['sma'])
    expect(next.nodes.cmp.params.a).toBe('@sma')
    // A read the user picked inside stays.
    expect(next.nodes.cmp2.params.a).toBe('@close')
    expect(next.nodes.b1).toBeDefined()
    expect(into(next, 'cmp', 'in0').map(w => w.id)).toEqual(['w_b1_cmp'])
  })

  it('dropping the same outside source on a routed port again changes nothing', () => {
    const c = { source: 'ema', target: 'cmp', sourceHandle: 'out', targetHandle: 'in0' }
    const g = routedGraph()
    expect(connectReplacing(g, c)).toBe(g)
    const g2 = connectReplacing(g, { ...c, source: 'sma' })
    expect(connectReplacing(g2, { ...c, source: 'sma' })).toBe(g2)
  })
})

describe('replace preview: the wire a drop would take out (F457 UX-1)', () => {
  const ends = (from: [string, string, 'source' | 'target'], to: [string, string, 'source' | 'target']) => ({
    fromHandle: { nodeId: from[0], id: from[1], type: from[2] },
    toHandle: { nodeId: to[0], id: to[1], type: to[2] },
  })

  it('names the wire on an occupied input, from either end of the drag', () => {
    const g = plainGraph()
    expect(dropOccupant(g, ends(['r2', 'out', 'source'], ['cmp', 'in0', 'target']))?.id).toBe('w_r1_cmp')
    expect(dropOccupant(g, ends(['cmp', 'in0', 'target'], ['r2', 'out', 'source']))?.id).toBe('w_r1_cmp')
  })

  it('names nothing on a free port, a refused drop, or the wire being moved', () => {
    const g = plainGraph()
    expect(dropOccupant(g, ends(['r2', 'out', 'source'], ['cmp', 'in1', 'target']))).toBeNull()
    // cmp → r1 in0 would close a loop: no replace, nothing marked.
    expect(dropOccupant(g, ends(['cmp', 'out', 'source'], ['r1', 'in0', 'target']))).toBeNull()
    expect(dropOccupant(g, ends(['r2', 'out', 'source'], ['cmp', 'in0', 'target']), 'w_r1_cmp')).toBeNull()
    expect(dropOccupant(g, { fromHandle: null, toHandle: null })).toBeNull()
  })

  it('names the inside wire a routed drop replaces', () => {
    expect(dropOccupant(routedGraph(), ends(['sma', 'out', 'source'], ['cmp', 'in0', 'target']))?.id).toBe('w_b1_cmp')
  })
})
