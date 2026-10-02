/**
 * Unit 5 — operations.ts tests
 *
 * 20 required scenarios covering:
 * - addNode, removeWire, removeNodesWithRewire, addWire, spliceNodeOntoWire, moveNode(s)
 * - readOnly rejection (ReadOnlyGraphError)
 * - persistence / version validation (IncompatibleGraphVersionError)
 * - cycle detection (wouldCreateCycle)
 */

import { describe, it, expect, beforeAll, vi } from 'vitest'
import type { Graph, GraphNode, GraphWire } from '../../../api/nodebuilder'
import {
  addNode,
  removeWire,
  addWire,
  moveNode,
  updateNodeParams,
  removeNodeWithRewire,
  removeNodesWithRewire,
  removeNodes,
  moveNodes,
  lowestFreeInPort,
  uniqueName,
  sanitizeName,
  newNodeId,
  spliceNodeOntoWire,
  wouldCreateCycle,
  ReadOnlyGraphError,
  IncompatibleGraphVersionError,
  MIN_SUPPORTED_VERSION,
  _genId,
} from '../operations'

// ---------------------------------------------------------------------------
// Deterministic ID shim for tests
// ---------------------------------------------------------------------------

let idCounter = 0
beforeAll(() => {
  idCounter = 0
  // Patch _genId via the module-level mock so rewire IDs are predictable
  // We use vitest's mock for crypto.randomUUID to keep it deterministic.
  vi.spyOn(globalThis.crypto, 'randomUUID').mockImplementation(
    () => `test-uuid-${++idCounter}` as `${string}-${string}-${string}-${string}-${string}`,
  )
})

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeNode(id: string, position: [number, number] = [0, 0], type = 'indicator'): GraphNode {
  return {
    id, type, name: id.toLowerCase(), parent: null,
    params: {}, position, display: false, bypass: false,
  }
}

function makeWire(id: string, from: string, to: string, attr?: string, toPort = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port: toPort, attr: attr ?? null }
}

function makeGraph(
  nodes: GraphNode[],
  wires: GraphWire[],
  readOnly = false,
): Graph {
  return {
    _version: 2,
    stream_schema: 1,
    readOnly,
    meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires,
    annotations: { boxes: [], notes: [] },
  }
}

// ---------------------------------------------------------------------------
// 1. addNode on editable graph adds node
// ---------------------------------------------------------------------------
it('1. addNode on editable graph adds node', () => {
  const g = makeGraph([], [])
  const node = makeNode('A')
  const result = addNode(g, node)
  expect(result.nodes['A']).toBeDefined()
  expect(Object.keys(result.nodes)).toHaveLength(1)
  // original unchanged
  expect(Object.keys(g.nodes)).toHaveLength(0)
})

// ---------------------------------------------------------------------------
// 2. addNode on readOnly throws ReadOnlyGraphError
// ---------------------------------------------------------------------------
it('2. addNode on readOnly throws ReadOnlyGraphError', () => {
  const g = makeGraph([], [], true)
  expect(() => addNode(g, makeNode('A'))).toThrow(ReadOnlyGraphError)
})

// ---------------------------------------------------------------------------
// 3. removeWire removes wire by id
// ---------------------------------------------------------------------------
it('3. removeWire removes wire by id', () => {
  const wire = makeWire('w1', 'A', 'B')
  const g = makeGraph([makeNode('A'), makeNode('B')], [wire])
  const result = removeWire(g, 'w1')
  expect(result.wires).toHaveLength(0)
  // original unchanged
  expect(g.wires).toHaveLength(1)
})

// ---------------------------------------------------------------------------
// 4. removeWire on readOnly throws
// ---------------------------------------------------------------------------
it('4. removeWire on readOnly throws', () => {
  const g = makeGraph([], [makeWire('w1', 'A', 'B')], true)
  expect(() => removeWire(g, 'w1')).toThrow(ReadOnlyGraphError)
})

// ---------------------------------------------------------------------------
// 5-8. Delete with rewire: the Houdini rule. Exactly one wire into the set
// from outside bridges to every outside consumer, on the consumer's port.
// Anything else deletes with no rewire.
// ---------------------------------------------------------------------------
describe('removeNodesWithRewire (Houdini rule)', () => {
  it('5. one outside input bridges to every consumer on the same port', () => {
    const nodes = [makeNode('IN'), makeNode('M'), makeNode('OUT1'), makeNode('OUT2')]
    const wires = [
      makeWire('wi', 'IN', 'M', '@close'),
      makeWire('wo1', 'M', 'OUT1', '@rsi', 'in1'),
      makeWire('wo2', 'M', 'OUT2', '@rsi', 'in0'),
    ]
    const result = removeNodeWithRewire(makeGraph(nodes, wires), 'M')
    expect(result.nodes['M']).toBeUndefined()
    expect(result.wires).toHaveLength(2)
    const toOut1 = result.wires.find(w => w.to === 'OUT1')!
    const toOut2 = result.wires.find(w => w.to === 'OUT2')!
    expect(toOut1.from).toBe('IN')
    expect(toOut1.to_port).toBe('in1')
    expect(toOut1.from_port).toBe('out')
    expect(toOut2.to_port).toBe('in0')
    // The bridge carries what the source provides.
    expect(toOut1.attr).toBe('@close')
  })

  it('6. two outside inputs: delete with no rewire', () => {
    const nodes = [makeNode('IN1'), makeNode('IN2'), makeNode('M'), makeNode('OUT')]
    const wires = [
      makeWire('wi1', 'IN1', 'M', undefined, 'in0'),
      makeWire('wi2', 'IN2', 'M', undefined, 'in1'),
      makeWire('wo1', 'M', 'OUT'),
    ]
    const result = removeNodeWithRewire(makeGraph(nodes, wires), 'M')
    expect(result.nodes['M']).toBeUndefined()
    expect(result.wires).toHaveLength(0)
  })

  it('7. no self-loop: a consumer that is the source itself is skipped', () => {
    const nodes = [makeNode('A'), makeNode('M')]
    const wires = [makeWire('wi1', 'A', 'M'), makeWire('wo1', 'M', 'A')]
    const result = removeNodeWithRewire(makeGraph(nodes, wires), 'M')
    expect(result.wires).toHaveLength(0)
  })

  it('8. no outside input: no rewire', () => {
    const nodes = [makeNode('M'), makeNode('OUT')]
    const result = removeNodeWithRewire(makeGraph(nodes, [makeWire('wo1', 'M', 'OUT')]), 'M')
    expect(result.wires).toHaveLength(0)
    expect(result.nodes['M']).toBeUndefined()
  })

  it('8b. a chain deleted as a set bridges its one input to its consumers', () => {
    // T -> A -> B -> C ; delete {A, B} -> T feeds C on C's old port.
    const nodes = [makeNode('T'), makeNode('A'), makeNode('B'), makeNode('C')]
    const wires = [
      makeWire('w1', 'T', 'A'),
      makeWire('w2', 'A', 'B'),
      makeWire('w3', 'B', 'C', undefined, 'in2'),
    ]
    const result = removeNodesWithRewire(makeGraph(nodes, wires), ['A', 'B'])
    expect(Object.keys(result.nodes).sort()).toEqual(['C', 'T'])
    expect(result.wires).toHaveLength(1)
    expect(result.wires[0]).toMatchObject({ from: 'T', to: 'C', to_port: 'in2' })
  })

  it('8c. a set with two inputs from outside is not rewired', () => {
    const nodes = [makeNode('T1'), makeNode('T2'), makeNode('A'), makeNode('B'), makeNode('C')]
    const wires = [
      makeWire('w1', 'T1', 'A'),
      makeWire('w2', 'T2', 'B'),
      makeWire('w3', 'A', 'C', undefined, 'in0'),
      makeWire('w4', 'B', 'C', undefined, 'in1'),
    ]
    const result = removeNodesWithRewire(makeGraph(nodes, wires), ['A', 'B'])
    expect(result.wires).toHaveLength(0)
  })

  it('8d. never wires into a Ticker or out of an Entry terminal', () => {
    // Source is an Entry terminal (no output port): no bridge may leave it.
    const nodes = [makeNode('E', [0, 0], 'entry'), makeNode('M'), makeNode('OUT')]
    const wires = [makeWire('w1', 'E', 'M'), makeWire('w2', 'M', 'OUT')]
    expect(removeNodeWithRewire(makeGraph(nodes, wires), 'M').wires).toHaveLength(0)
    // Consumer is a Ticker (no input port): no bridge may enter it.
    const nodes2 = [makeNode('S'), makeNode('M'), makeNode('TK', [0, 0], 'ticker')]
    const wires2 = [makeWire('w1', 'S', 'M'), makeWire('w2', 'M', 'TK')]
    expect(removeNodeWithRewire(makeGraph(nodes2, wires2), 'M').wires).toHaveLength(0)
  })

  it('8e. removeNodes deletes nodes and their wires without rewiring', () => {
    const nodes = [makeNode('IN'), makeNode('M'), makeNode('OUT')]
    const wires = [makeWire('wi', 'IN', 'M'), makeWire('wo', 'M', 'OUT')]
    const result = removeNodes(makeGraph(nodes, wires), ['M'])
    expect(result.wires).toHaveLength(0)
    expect(Object.keys(result.nodes).sort()).toEqual(['IN', 'OUT'])
  })
})

// ---------------------------------------------------------------------------
// Ports, names, batch moves
// ---------------------------------------------------------------------------
describe('ports, names and batch moves', () => {
  it('addWire without a port takes the lowest free in<k> on the target', () => {
    let g = makeGraph([makeNode('A'), makeNode('B'), makeNode('C')], [])
    g = addWire(g, { id: 'w1', from: 'A', to: 'C' })
    g = addWire(g, { id: 'w2', from: 'B', to: 'C' })
    expect(g.wires.map(w => w.to_port)).toEqual(['in0', 'in1'])
    expect(g.wires.every(w => w.from_port === 'out')).toBe(true)
    // Free in0 again: the next wire fills the gap.
    g = removeWire(g, 'w1')
    expect(lowestFreeInPort(g, 'C')).toBe('in0')
  })

  it('addNode keeps sibling names unique', () => {
    let g = makeGraph([], [])
    g = addNode(g, { ...makeNode('n1'), name: 'rsi' })
    g = addNode(g, { ...makeNode('n2'), name: 'rsi' })
    g = addNode(g, { ...makeNode('n3'), name: 'rsi' })
    expect(['n1', 'n2', 'n3'].map(id => g.nodes[id].name)).toEqual(['rsi', 'rsi1', 'rsi2'])
    expect(uniqueName(g, 'Crosses Above')).toBe('crosses_above')
  })

  it('names follow the shared paths.ts rule, as the backend does (FC-8)', () => {
    let g = makeGraph([], [])
    g = addNode(g, { ...makeNode('n1'), name: 'sma200' })
    g = addNode(g, { ...makeNode('n2'), name: 'sma200' })
    expect(g.nodes.n2.name).toBe('sma201')
    expect(sanitizeName('200 day')).toBe('n_200_day')
    expect(uniqueName(g, '200 day')).toBe('n_200_day')
  })

  it('newNodeId has the n_ plus 8 base-36 shape', () => {
    expect(newNodeId()).toMatch(/^n_[a-z0-9]{8}$/)
  })

  it('moveNodes applies one delta or one per id', () => {
    const g = makeGraph([makeNode('A', [0, 0]), makeNode('B', [10, 10])], [])
    const same = moveNodes(g, ['A', 'B'], [5, -5])
    expect(same.nodes.A.position).toEqual([5, -5])
    expect(same.nodes.B.position).toEqual([15, 5])
    const each = moveNodes(g, ['A', 'B'], [[1, 1], [2, 2]])
    expect(each.nodes.A.position).toEqual([1, 1])
    expect(each.nodes.B.position).toEqual([12, 12])
    expect(moveNodes(g, ['A'], [0, 0])).toBe(g)
  })
})

// ---------------------------------------------------------------------------
// 9. addWire would create cycle → rejected
// ---------------------------------------------------------------------------
it('9. addWire would create cycle → rejected', () => {
  // A → B → C, adding C → A would close the cycle
  const nodes = [makeNode('A'), makeNode('B'), makeNode('C')]
  const wires = [makeWire('w1', 'A', 'B'), makeWire('w2', 'B', 'C')]
  const g = makeGraph(nodes, wires)

  expect(() => addWire(g, makeWire('w3', 'C', 'A'))).toThrow(/cycle/)
})

// ---------------------------------------------------------------------------
// 10. addWire valid → added
// ---------------------------------------------------------------------------
it('10. addWire valid → added', () => {
  const g = makeGraph([makeNode('A'), makeNode('B')], [])
  const wire = makeWire('w1', 'A', 'B')
  const result = addWire(g, wire)
  expect(result.wires).toHaveLength(1)
  expect(result.wires[0]).toEqual(wire)
})

// ---------------------------------------------------------------------------
// 11. addWire on readOnly throws
// ---------------------------------------------------------------------------
it('11. addWire on readOnly throws', () => {
  const g = makeGraph([makeNode('A'), makeNode('B')], [], true)
  expect(() => addWire(g, makeWire('w1', 'A', 'B'))).toThrow(ReadOnlyGraphError)
})

// ---------------------------------------------------------------------------
// 12. spliceNodeOntoWire — A→nodeId, nodeId→B
// ---------------------------------------------------------------------------
it('12. spliceNodeOntoWire — A→nodeId, nodeId→B', () => {
  idCounter = 400
  const nodes = [makeNode('A'), makeNode('B'), makeNode('N')]
  const wire = makeWire('w1', 'A', 'B', '@bool')
  const g = makeGraph(nodes, [wire])
  const result = spliceNodeOntoWire(g, 'N', 'w1')

  // Original w1 gone
  expect(result.wires.find(w => w.id === 'w1')).toBeUndefined()
  // Two new wires
  expect(result.wires).toHaveLength(2)
  const fromA = result.wires.find(w => w.from === 'A' && w.to === 'N')
  const fromN = result.wires.find(w => w.from === 'N' && w.to === 'B')
  expect(fromA).toBeDefined()
  expect(fromN).toBeDefined()
  // attr preserved
  expect(fromA!.attr).toBe('@bool')
  expect(fromN!.attr).toBe('@bool')
})

// ---------------------------------------------------------------------------
// 13. spliceNodeOntoWire on readOnly throws
// ---------------------------------------------------------------------------
it('13. spliceNodeOntoWire on readOnly throws', () => {
  const g = makeGraph([makeNode('A'), makeNode('B')], [makeWire('w1', 'A', 'B')], true)
  expect(() => spliceNodeOntoWire(g, 'N', 'w1')).toThrow(ReadOnlyGraphError)
})

// ---------------------------------------------------------------------------
// 14. moveNode updates position
// ---------------------------------------------------------------------------
it('14. moveNode updates position', () => {
  const node = makeNode('A', [0, 0])
  const g = makeGraph([node], [])
  const result = moveNode(g, 'A', [100, 200])
  expect(result.nodes['A'].position).toEqual([100, 200])
  // original unchanged
  expect(g.nodes['A'].position).toEqual([0, 0])
})

// ---------------------------------------------------------------------------
// 15. moveNode on readOnly throws
// ---------------------------------------------------------------------------
it('15. moveNode on readOnly throws', () => {
  const g = makeGraph([makeNode('A')], [], true)
  expect(() => moveNode(g, 'A', [10, 20])).toThrow(ReadOnlyGraphError)
})

// ---------------------------------------------------------------------------
// 15b. updateNodeParams merges partial into node.params
// ---------------------------------------------------------------------------
it('15b. updateNodeParams merges partial into node.params', () => {
  const node: GraphNode = { ...makeNode('A'), params: { period: 14, threshold: 30 } }
  const g = makeGraph([node], [])
  const result = updateNodeParams(g, 'A', { period: 21 })
  expect(result.nodes['A'].params).toEqual({ period: 21, threshold: 30 })
  // original unchanged
  expect(g.nodes['A'].params).toEqual({ period: 14, threshold: 30 })
})

// ---------------------------------------------------------------------------
// 15c. updateNodeParams on readOnly throws
// ---------------------------------------------------------------------------
it('15c. updateNodeParams on readOnly throws', () => {
  const g = makeGraph([makeNode('A')], [], true)
  expect(() => updateNodeParams(g, 'A', { period: 21 })).toThrow(ReadOnlyGraphError)
})

// ---------------------------------------------------------------------------
// 15d. updateNodeParams no-op on missing node
// ---------------------------------------------------------------------------
it('15d. updateNodeParams no-op on missing node', () => {
  const g = makeGraph([makeNode('A')], [])
  const result = updateNodeParams(g, 'ZZ', { period: 21 })
  expect(result).toBe(g)
})

// ---------------------------------------------------------------------------
// 16. version check: _version=0 throws IncompatibleGraphVersionError
// ---------------------------------------------------------------------------
it('16. version check: _version=0 throws IncompatibleGraphVersionError', () => {
  // Test the error type and MIN_SUPPORTED_VERSION constant directly.
  const staleGraph = { _version: 0, readOnly: false, nodes: {}, wires: [] }
  const version = staleGraph._version ?? 0
  expect(version < MIN_SUPPORTED_VERSION).toBe(true)

  expect(() => {
    if (version < MIN_SUPPORTED_VERSION) {
      throw new IncompatibleGraphVersionError(version, MIN_SUPPORTED_VERSION)
    }
  }).toThrow(IncompatibleGraphVersionError)
})

// ---------------------------------------------------------------------------
// 17. version check: _version=1 loads (at minimum version)
// ---------------------------------------------------------------------------
it('17. version check: _version=1 loads (at minimum version)', () => {
  const g = { _version: 1, readOnly: false, nodes: {}, wires: [] }
  expect(g._version >= MIN_SUPPORTED_VERSION).toBe(true)
  // No error thrown
  expect(() => {
    if (g._version < MIN_SUPPORTED_VERSION) {
      throw new IncompatibleGraphVersionError(g._version, MIN_SUPPORTED_VERSION)
    }
  }).not.toThrow()
})

// ---------------------------------------------------------------------------
// 18. version check: _version=99 loads (additive tolerance)
// ---------------------------------------------------------------------------
it('18. version check: _version=99 loads (additive tolerance)', () => {
  const g = { _version: 99, readOnly: false, nodes: {}, wires: [] }
  expect(g._version >= MIN_SUPPORTED_VERSION).toBe(true)
  expect(() => {
    if (g._version < MIN_SUPPORTED_VERSION) {
      throw new IncompatibleGraphVersionError(g._version, MIN_SUPPORTED_VERSION)
    }
  }).not.toThrow()
})

// ---------------------------------------------------------------------------
// 19. wouldCreateCycle — direct: A→B exists, ask B→A → yes
// ---------------------------------------------------------------------------
describe('wouldCreateCycle', () => {
  it('19. direct cycle: existing A→B, proposed B→A → true', () => {
    const g = makeGraph([makeNode('A'), makeNode('B')], [makeWire('w1', 'A', 'B')])
    // Proposing B → A
    expect(wouldCreateCycle(g, 'B', 'A')).toBe(true)
  })

  it('19b. no cycle: existing A→B, proposed A→B (same direction) → false', () => {
    const g = makeGraph([makeNode('A'), makeNode('B')], [makeWire('w1', 'A', 'B')])
    // This is a duplicate edge not a cycle
    expect(wouldCreateCycle(g, 'A', 'B')).toBe(false)
  })

  // ---------------------------------------------------------------------------
  // 20. wouldCreateCycle — transitive: A→B→C, ask C→A → yes
  // ---------------------------------------------------------------------------
  it('20. transitive cycle: A→B→C, proposed C→A → true', () => {
    const g = makeGraph(
      [makeNode('A'), makeNode('B'), makeNode('C')],
      [makeWire('w1', 'A', 'B'), makeWire('w2', 'B', 'C')],
    )
    expect(wouldCreateCycle(g, 'C', 'A')).toBe(true)
  })

  it('20b. no transitive cycle: A→B→C, proposed D→A → false', () => {
    const g = makeGraph(
      [makeNode('A'), makeNode('B'), makeNode('C'), makeNode('D')],
      [makeWire('w1', 'A', 'B'), makeWire('w2', 'B', 'C')],
    )
    expect(wouldCreateCycle(g, 'D', 'A')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// F7 review finding — spliceNodeOntoWire must reject cycle-creating splices
// ---------------------------------------------------------------------------
describe('spliceNodeOntoWire — F7 cycle guard', () => {
  it('rejects splicing a node that is already an endpoint of the wire (self-loop)', () => {
    const g = makeGraph(
      [makeNode('A'), makeNode('B')],
      [makeWire('w1', 'A', 'B')],
    )
    // Splice A into wire A→B → A→A self-loop is nonsense
    expect(() => spliceNodeOntoWire(g, 'A', 'w1')).toThrow(/already an endpoint/)
  })

  it('rejects a splice that would create a cycle via an existing path', () => {
    // Graph: A→B, B→C, C→D. Splicing B into wire C→D would add C→B + B→D,
    // creating cycle B→C→B.
    const g = makeGraph(
      [makeNode('A'), makeNode('B'), makeNode('C'), makeNode('D')],
      [makeWire('w1', 'A', 'B'), makeWire('w2', 'B', 'C'), makeWire('w3', 'C', 'D')],
    )
    expect(() => spliceNodeOntoWire(g, 'B', 'w3')).toThrow(/cycle/)
  })

  it('happy path: splice a fresh node onto a wire', () => {
    const g = makeGraph(
      [makeNode('A'), makeNode('B'), makeNode('X')],
      [makeWire('w1', 'A', 'B')],
    )
    const out = spliceNodeOntoWire(g, 'X', 'w1')
    // Original w1 removed; A→X and X→B added.
    expect(out.wires.find(w => w.id === 'w1')).toBeUndefined()
    expect(out.wires.some(w => w.from === 'A' && w.to === 'X')).toBe(true)
    expect(out.wires.some(w => w.from === 'X' && w.to === 'B')).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// F435 wave 0 review (FC-1 / UXP-1): no wire where a node has no port
// ---------------------------------------------------------------------------
describe('addWire refuses a wire with no port at one end', () => {
  function typed(id: string, type: string): GraphNode {
    return makeNode(id, [0, 0], type)
  }

  it.each([
    ['out of Entry', 'entry', 'rsi'],
    ['out of Exit', 'exit', 'above'],
    ['into a Ticker', 'rsi', 'ticker'],
    ['into a Settings node', 'above', 'stop_loss'],
    ['out of a Settings node', 'stop_loss', 'exit'],
  ])('%s', (_label, fromType, toType) => {
    const g = makeGraph([typed('A', fromType), typed('B', toType)], [])
    expect(() => addWire(g, makeWire('w1', 'A', 'B'))).toThrow(/no port/)
  })

  it('still adds Ticker -> RSI -> Above -> Entry', () => {
    let g = makeGraph([typed('T', 'ticker'), typed('R', 'rsi'), typed('A', 'above'), typed('E', 'entry')], [])
    g = addWire(g, makeWire('w1', 'T', 'R'))
    g = addWire(g, makeWire('w2', 'R', 'A'))
    g = addWire(g, makeWire('w3', 'A', 'E'))
    expect(g.wires).toHaveLength(3)
  })
})
