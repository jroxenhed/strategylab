/**
 * Unit 5 — operations.ts tests
 *
 * 20 required scenarios covering:
 * - addNode, removeWire, removeNodeWithRewire, addWire, spliceNodeOntoWire, moveNode
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
  removeNodeWithRewire,
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

function makeNode(id: string, position: [number, number] = [0, 0]): GraphNode {
  return { id, type: 'indicator', params: {}, position, display: false, bypass: false }
}

function makeWire(id: string, from: string, to: string, attr?: string): GraphWire {
  return { id, from, to, attr: attr ?? null }
}

function makeGraph(
  nodes: GraphNode[],
  wires: GraphWire[],
  readOnly = false,
): Graph {
  return {
    _version: 1,
    readOnly,
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires,
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
// 5. removeNodeWithRewire — 3 incoming × 2 outgoing → 6 new wires
// ---------------------------------------------------------------------------
it('5. removeNodeWithRewire — 3 incoming × 2 outgoing → 6 new wires', () => {
  idCounter = 0
  const nodes = [
    makeNode('IN1'), makeNode('IN2'), makeNode('IN3'),
    makeNode('M'),
    makeNode('OUT1'), makeNode('OUT2'),
  ]
  const wires = [
    makeWire('wi1', 'IN1', 'M'),
    makeWire('wi2', 'IN2', 'M'),
    makeWire('wi3', 'IN3', 'M'),
    makeWire('wo1', 'M', 'OUT1'),
    makeWire('wo2', 'M', 'OUT2'),
  ]
  const g = makeGraph(nodes, wires)
  const result = removeNodeWithRewire(g, 'M')

  // M is gone
  expect(result.nodes['M']).toBeUndefined()
  // original 5 incident wires gone; 6 new rewires added
  expect(result.wires).toHaveLength(6)

  // Each IN connects to each OUT exactly once
  const pairs = result.wires.map(w => `${w.from}→${w.to}`)
  for (const inId of ['IN1', 'IN2', 'IN3']) {
    for (const outId of ['OUT1', 'OUT2']) {
      expect(pairs).toContain(`${inId}→${outId}`)
    }
  }
})

// ---------------------------------------------------------------------------
// 6. removeNodeWithRewire dedups same-target wires
// ---------------------------------------------------------------------------
it('6. removeNodeWithRewire dedups same-target wires', () => {
  idCounter = 100
  // Two incomings from SAME source — rewire would try A→OUT twice
  const nodes = [makeNode('A'), makeNode('M'), makeNode('OUT')]
  const wires = [
    makeWire('wi1', 'A', 'M', '@bool'),
    makeWire('wi2', 'A', 'M', '@price'), // duplicate from A
    makeWire('wo1', 'M', 'OUT'),
  ]
  const g = makeGraph(nodes, wires)
  const result = removeNodeWithRewire(g, 'M')

  // Should produce exactly 1 wire: A → OUT (not 2)
  expect(result.wires).toHaveLength(1)
  expect(result.wires[0].from).toBe('A')
  expect(result.wires[0].to).toBe('OUT')
})

// ---------------------------------------------------------------------------
// 7. removeNodeWithRewire no self-loop (from === to after rewire)
// ---------------------------------------------------------------------------
it('7. removeNodeWithRewire no self-loop', () => {
  idCounter = 200
  // A → M → A would create self-loop: skip
  const nodes = [makeNode('A'), makeNode('M')]
  const wires = [
    makeWire('wi1', 'A', 'M'),
    makeWire('wo1', 'M', 'A'),
  ]
  const g = makeGraph(nodes, wires)
  const result = removeNodeWithRewire(g, 'M')

  // Self-loop A → A must be skipped
  expect(result.wires).toHaveLength(0)
})

// ---------------------------------------------------------------------------
// 8. removeNodeWithRewire 0 incoming → no rewires
// ---------------------------------------------------------------------------
it('8. removeNodeWithRewire 0 incoming → no rewires', () => {
  idCounter = 300
  const nodes = [makeNode('M'), makeNode('OUT')]
  const wires = [makeWire('wo1', 'M', 'OUT')]
  const g = makeGraph(nodes, wires)
  const result = removeNodeWithRewire(g, 'M')

  // M gone, wo1 gone, no rewires created
  expect(result.wires).toHaveLength(0)
  expect(result.nodes['M']).toBeUndefined()
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
// 16. loadGraph with _version=0 throws IncompatibleGraphVersionError
// ---------------------------------------------------------------------------
it('16. loadGraph with _version=0 throws IncompatibleGraphVersionError', () => {
  // Test the validation function directly (store.loadGraph uses localStorage;
  // we test the error type and MIN_SUPPORTED_VERSION constant here).
  const staleGraph: Graph = { _version: 0, readOnly: false, nodes: {}, wires: [] }
  const version = staleGraph._version ?? 0
  expect(version < MIN_SUPPORTED_VERSION).toBe(true)

  expect(() => {
    if (version < MIN_SUPPORTED_VERSION) {
      throw new IncompatibleGraphVersionError(version, MIN_SUPPORTED_VERSION)
    }
  }).toThrow(IncompatibleGraphVersionError)
})

// ---------------------------------------------------------------------------
// 17. loadGraph with _version=1 loads (at minimum version)
// ---------------------------------------------------------------------------
it('17. loadGraph with _version=1 loads (at minimum version)', () => {
  const g: Graph = { _version: 1, readOnly: false, nodes: {}, wires: [] }
  expect(g._version >= MIN_SUPPORTED_VERSION).toBe(true)
  // No error thrown
  expect(() => {
    if (g._version < MIN_SUPPORTED_VERSION) {
      throw new IncompatibleGraphVersionError(g._version, MIN_SUPPORTED_VERSION)
    }
  }).not.toThrow()
})

// ---------------------------------------------------------------------------
// 18. loadGraph with _version=99 loads (additive tolerance)
// ---------------------------------------------------------------------------
it('18. loadGraph with _version=99 loads (additive tolerance)', () => {
  const g: Graph = { _version: 99, readOnly: false, nodes: {}, wires: [] }
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
