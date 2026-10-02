/**
 * Display and bypass flags (F435 W3 item 3.B, spec S16): the pure ops, the
 * D and B commands, undo, and the graph JSON the backend gets.
 *
 * The last block runs the shared vector backend/tests/nodebuilder/vectors/
 * flags.json: B on the EMA must produce exactly `after_bypass`, which
 * pytest (test_flags_vector.py) cooks and checks is pass-through.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import vectors from '../../../../../backend/tests/nodebuilder/vectors/flags.json'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import { listCommands, getCommand, runCommand } from '../commands'
import { clickBypassFlag, clickDisplayFlag } from '../commands/flags'
import { replaceableNode } from '../commands/unsupported'
import {
  ReadOnlyGraphError,
  flagProblem,
  flagProblemForType,
  replaceNode,
  setFlag,
  setFlags,
} from '../operations'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics } from '../useDiagnostics'

function node(id: string, type: string, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [0, 0], display: false, bypass: false, ...extra }
}

function graphOf(nodes: GraphNode[], wires: Graph['wires'] = []): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires,
    annotations: { boxes: [], notes: [] },
  }
}

const wire = (id: string, from: string, to: string, to_port = 'in0') => ({ id, from, to, from_port: 'out' as const, to_port })

/** ticker -> sma -> rsi -> below -> entry, plus a settings node. */
function chain(): Graph {
  return graphOf(
    [node('t', 'ticker'), node('a', 'sma'), node('b', 'rsi'), node('c', 'below'), node('e', 'entry'), node('s', 'stop_loss')],
    [wire('w1', 't', 'a'), wire('w2', 'a', 'b'), wire('w3', 'b', 'c'), wire('w4', 'c', 'e')],
  )
}

function load(g: Graph) {
  useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
}

function select(ids: string[], primary = ids[ids.length - 1]) {
  useNodeBuilderStore.getState().setSelection({ nodeIds: ids, primary })
}

const state = () => useNodeBuilderStore.getState()
const flags = (flag: 'display' | 'bypass') =>
  Object.fromEntries(Object.values(state().graph!.nodes).map(n => [n.id, n[flag]]))

beforeEach(() => {
  resetDiagnostics()
  state().discardEdits()
  state().newGraph?.()
})

// ---------------------------------------------------------------------------
// Pure ops
// ---------------------------------------------------------------------------

describe('which nodes carry which flag (foundation 4.6)', () => {
  it('display: only nodes with an output', () => {
    expect(flagProblemForType('sma', 'display')).toBeNull()
    expect(flagProblemForType('ticker', 'display')).toBeNull()
    expect(flagProblemForType('below', 'display')).toBeNull()
    expect(flagProblemForType('entry', 'display')).toBe('Terminals have no display flag')
    expect(flagProblemForType('stop_loss', 'display')).toBe('Settings nodes have no display flag')
    expect(flagProblemForType('no_such_type', 'display')).toBe('Unsupported nodes have no display flag')
  })

  it('bypass: every node but Tickers and terminals', () => {
    expect(flagProblemForType('sma', 'bypass')).toBeNull()
    expect(flagProblemForType('and', 'bypass')).toBeNull()
    expect(flagProblemForType('stop_loss', 'bypass')).toBeNull()
    expect(flagProblemForType('ticker', 'bypass')).toBe('Tickers cannot be bypassed')
    expect(flagProblemForType('entry', 'bypass')).toBe('Terminals cannot be bypassed')
    expect(flagProblemForType('exit', 'bypass')).toBe('Terminals cannot be bypassed')
    expect(flagProblemForType('no_such_type', 'bypass')).toBe('Unsupported nodes cannot be bypassed')
    expect(flagProblem(chain(), 'missing', 'bypass')).toBe('Select a node first')
  })
})

describe('setFlag', () => {
  it('display moves within a network and leaves other networks alone', () => {
    const g = graphOf([
      node('a', 'sma', { display: true }),
      node('b', 'rsi'),
      node('net', 'sma'),
      node('inner', 'ema', { parent: 'net', display: true }),
    ])
    const out = setFlag(g, 'b', 'display', true)
    expect(out.nodes.a.display).toBe(false)
    expect(out.nodes.b.display).toBe(true)
    // A node in another network keeps its own display flag.
    expect(out.nodes.inner.display).toBe(true)
    expect(g.nodes.a.display).toBe(true) // input untouched
  })

  it('returns the same graph when nothing changes', () => {
    const g = graphOf([node('a', 'sma', { display: true })])
    expect(setFlag(g, 'a', 'display', true)).toBe(g)
    expect(setFlag(g, 'a', 'bypass', false)).toBe(g)
    expect(setFlag(g, 'missing', 'bypass', true)).toBe(g)
  })

  it('refuses to turn a flag on where it does not belong, but always turns one off', () => {
    const g = graphOf([node('t', 'ticker'), node('e', 'entry', { bypass: true })])
    expect(setFlag(g, 't', 'bypass', true)).toBe(g)
    expect(setFlag(g, 'e', 'display', true)).toBe(g)
    expect(setFlag(g, 'e', 'bypass', false).nodes.e.bypass).toBe(false)
  })

  it('setFlags sets many nodes in one step and skips the ones that cannot carry it', () => {
    const out = setFlags(chain(), ['t', 'a', 'b', 'e'], 'bypass', true)
    expect(out.nodes.a.bypass).toBe(true)
    expect(out.nodes.b.bypass).toBe(true)
    expect(out.nodes.t.bypass).toBe(false)
    expect(out.nodes.e.bypass).toBe(false)
  })

  it('throws on a read-only graph', () => {
    const g = { ...chain(), readOnly: true }
    expect(() => setFlag(g, 'a', 'bypass', true)).toThrow(ReadOnlyGraphError)
  })
})

describe('replaceNode (S13 Replace with…)', () => {
  it('puts the new node in place, moves the wires that fit and deletes the old one', () => {
    const g = graphOf(
      [
        node('t', 'ticker'),
        node('x', 'stochastic_rising', { position: [40, 80], params: { k: 14 }, display: true }),
        node('e', 'entry'),
        node('r', 'rising', { position: [999, 999] }),
      ],
      [wire('w1', 't', 'x'), wire('w2', 'x', 'e')],
    )
    const out = replaceNode(g, 'x', 'r')
    expect(out.nodes.x).toBeUndefined()
    expect(out.nodes.r.position).toEqual([40, 80])
    expect(out.nodes.r.display).toBe(true)
    expect(out.wires.map(w => [w.id, w.from, w.to, w.to_port])).toEqual([
      ['w1', 't', 'r', 'in0'],
      ['w2', 'r', 'e', 'in0'],
    ])
  })

  it('drops wires the new type has no port for', () => {
    const g = graphOf(
      [node('a', 'sma'), node('b', 'rsi'), node('x', 'mystery'), node('e', 'entry'), node('t2', 'ticker')],
      [wire('w1', 'a', 'x', 'in0'), wire('w2', 'b', 'x', 'in1'), wire('w3', 'x', 'e')],
    )
    // A Ticker takes no input: both input wires go; the output wire stays.
    const out = replaceNode(g, 'x', 't2')
    expect(out.wires.map(w => w.id)).toEqual(['w3'])
    expect(out.wires[0].from).toBe('t2')
  })
})

// ---------------------------------------------------------------------------
// Commands, store and undo
// ---------------------------------------------------------------------------

describe('D and B commands', () => {
  it('are registered with their keys and the node menu', () => {
    const d = getCommand('flags.setDisplay')
    const b = getCommand('flags.toggleBypass')
    expect(d?.keys).toEqual(['d'])
    expect(b?.keys).toEqual(['b'])
    expect(d?.menu).toBe('node')
    expect(b?.menu).toBe('node')
    const keys = listCommands().flatMap(c => c.keys ?? [])
    expect(keys).toEqual(expect.arrayContaining(['d', 'b']))
  })

  it('D sets display on the primary node, clears the old one, one undo step', () => {
    load(chain())
    select(['a'])
    expect(runCommand('flags.setDisplay')).toBe(true)
    expect(flags('display')).toMatchObject({ a: true, b: false })
    select(['a', 'b'], 'b')
    runCommand('flags.setDisplay')
    expect(flags('display')).toMatchObject({ a: false, b: true })
    expect(state().past).toHaveLength(2)
    // D again on the node that already shows: nothing recorded.
    runCommand('flags.setDisplay')
    expect(state().past).toHaveLength(2)
    state().undo()
    expect(flags('display')).toMatchObject({ a: true, b: false })
    state().redo()
    expect(flags('display')).toMatchObject({ a: false, b: true })
  })

  it('B makes a mixed selection uniform from the primary, in one step each', () => {
    const g = chain()
    g.nodes.a = { ...g.nodes.a, bypass: true }
    load(g)
    select(['b', 'a'], 'a') // X = a (bypassed, primary), Y = b
    runCommand('flags.toggleBypass')
    expect(flags('bypass')).toMatchObject({ a: false, b: false })
    expect(state().past).toHaveLength(1)
    runCommand('flags.toggleBypass')
    expect(flags('bypass')).toMatchObject({ a: true, b: true })
    expect(state().past).toHaveLength(2)
    expect(state().past[1]?.label ?? '').toMatch(/bypass/)
  })

  it('B skips Tickers and terminals in a selection', () => {
    load(chain())
    select(['t', 'a', 'e'], 'a')
    runCommand('flags.toggleBypass')
    expect(flags('bypass')).toMatchObject({ t: false, a: true, e: false })
  })

  it('flashes why when nothing can change', () => {
    load(chain())
    select([])
    runCommand('flags.setDisplay')
    expect(state().flash?.text).toBe('Select a node first')
    runCommand('flags.toggleBypass')
    expect(state().flash?.text).toBe('Select a node first')
    select(['e'])
    runCommand('flags.setDisplay')
    expect(state().flash?.text).toBe('Terminals have no display flag')
    runCommand('flags.toggleBypass')
    expect(state().flash?.text).toBe('Terminals cannot be bypassed')
    select(['t'])
    runCommand('flags.toggleBypass')
    expect(state().flash?.text).toBe('Tickers cannot be bypassed')
    expect(state().past).toHaveLength(0)
    expect(state().dirty).toBe(false)
  })

  it('do nothing on a read-only graph', () => {
    // openGraph always makes an editable copy, so set a read-only graph directly.
    useNodeBuilderStore.setState({ graph: { ...chain(), readOnly: true }, selectedNodeId: 'a', selectedNodeIds: ['a'] })
    expect(runCommand('flags.toggleBypass')).toBe(false)
    expect(clickBypassFlag('a')).toBe('Read-only graph')
    expect(flags('bypass').a).toBe(false)
  })

  it('the dot actions change one node, never the selection', () => {
    load(chain())
    select(['b'])
    clickDisplayFlag('a')
    clickBypassFlag('c')
    expect(flags('display').a).toBe(true)
    expect(flags('bypass').c).toBe(true)
    expect(state().selectedNodeIds).toEqual(['b'])
    expect(state().past).toHaveLength(2)
    // The lit display dot does nothing.
    clickDisplayFlag('a')
    expect(state().past).toHaveLength(2)
  })

  it('a deleted display node takes its flag with it', () => {
    load(chain())
    clickDisplayFlag('a')
    state().removeNodes(['a'])
    expect(Object.values(state().graph!.nodes).some(n => n.display)).toBe(false)
  })

  it('Replace with… is offered only for an unsupported node', () => {
    const g = chain()
    g.nodes.x = node('x', 'stochastic_rising')
    load(g)
    select(['a'])
    expect(replaceableNode(state())).toBeNull()
    select(['x'])
    expect(replaceableNode(state())).toBe('x')
  })
})

// ---------------------------------------------------------------------------
// End to end: the JSON the backend gets (shared vector)
// ---------------------------------------------------------------------------

describe('graph JSON round trip (vectors/flags.json)', () => {
  it('B on the EMA gives exactly the graph pytest cooks as pass-through', () => {
    load(vectors.before as unknown as Graph)
    select([vectors.bypass_node])
    runCommand('flags.toggleBypass')
    // What a save or a run sends: the graph, through JSON.
    const sent = JSON.parse(JSON.stringify(state().graph))
    expect(sent).toEqual(vectors.after_bypass)
    // Bypass keeps the node and its wires in the payload; the backend does the pass-through.
    expect(Object.keys(sent.nodes)).toContain(vectors.bypass_node)
    expect(sent.wires).toHaveLength(vectors.before.wires.length)
    // Undo gives back the exact graph.
    state().undo()
    expect(JSON.parse(JSON.stringify(state().graph))).toEqual(vectors.before)
  })

  it('D moves the one display flag as the vector says', () => {
    load(vectors.after_bypass as unknown as Graph)
    select([vectors.display_node])
    runCommand('flags.setDisplay')
    expect(flags('display')).toEqual(vectors.after_display)
  })
})
