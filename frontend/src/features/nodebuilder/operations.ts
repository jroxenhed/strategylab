/**
 * operations.ts — Pure graph operations for the Node Strategy Builder (Unit 5).
 *
 * All functions return a NEW Graph; they never mutate the input.
 * All mutation operations throw ReadOnlyGraphError when graph.readOnly is true.
 */

import type { Graph, GraphNode, GraphWire } from '../../api/nodebuilder'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const MIN_SUPPORTED_VERSION = 1

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export class ReadOnlyGraphError extends Error {
  constructor(opName: string) {
    super(`Cannot perform "${opName}" on a read-only graph.`)
    this.name = 'ReadOnlyGraphError'
  }
}

export class IncompatibleGraphVersionError extends Error {
  constructor(actual: number, minimum: number) {
    super(`Graph _version=${actual} < MIN_SUPPORTED_VERSION=${minimum}`)
    this.name = 'IncompatibleGraphVersionError'
  }
}

// ---------------------------------------------------------------------------
// Internal ID generator — exported for test mocking
// ---------------------------------------------------------------------------

export function _genId(): string {
  return crypto.randomUUID()
}

// ---------------------------------------------------------------------------
// Guard helpers
// ---------------------------------------------------------------------------

function assertEditable(graph: Graph, opName: string): void {
  if (graph.readOnly) {
    throw new ReadOnlyGraphError(opName)
  }
}

// ---------------------------------------------------------------------------
// Cycle detection
// ---------------------------------------------------------------------------

/**
 * Returns true if adding a wire from `fromPath` → `toPath` would create a cycle
 * in the current graph. Uses DFS from `toPath` following outgoing edges; if the
 * DFS reaches `fromPath`, the proposed edge would close a cycle.
 */
export function wouldCreateCycle(
  graph: Graph,
  fromPath: string,
  toPath: string,
): boolean {
  // Build adjacency from existing wires
  const adj = new Map<string, string[]>()
  for (const w of graph.wires) {
    if (!adj.has(w.from)) adj.set(w.from, [])
    adj.get(w.from)!.push(w.to)
  }

  // DFS from toPath; if we reach fromPath → cycle
  const visited = new Set<string>()
  const stack = [toPath]
  while (stack.length > 0) {
    const current = stack.pop()!
    if (current === fromPath) return true
    if (visited.has(current)) continue
    visited.add(current)
    const neighbors = adj.get(current) ?? []
    for (const n of neighbors) {
      stack.push(n)
    }
  }
  return false
}

// ---------------------------------------------------------------------------
// Pure operations
// ---------------------------------------------------------------------------

/**
 * Add a node to the graph. Node id must be unique (callers ensure this).
 */
export function addNode(graph: Graph, node: GraphNode): Graph {
  assertEditable(graph, 'addNode')
  return {
    ...graph,
    nodes: { ...graph.nodes, [node.id]: node },
  }
}

/**
 * Remove a wire by id.
 */
export function removeWire(graph: Graph, wireId: string): Graph {
  assertEditable(graph, 'removeWire')
  return {
    ...graph,
    wires: graph.wires.filter(w => w.id !== wireId),
  }
}

/**
 * Add a wire. Rejects if the wire would create a cycle.
 */
export function addWire(graph: Graph, wire: GraphWire): Graph {
  assertEditable(graph, 'addWire')
  if (wouldCreateCycle(graph, wire.from, wire.to)) {
    throw new Error(`Cannot add wire: "${wire.from}" → "${wire.to}" would create a cycle.`)
  }
  return {
    ...graph,
    wires: [...graph.wires, wire],
  }
}

/**
 * Move a node to a new [x, y] position.
 */
export function moveNode(
  graph: Graph,
  nodeId: string,
  position: [number, number],
): Graph {
  assertEditable(graph, 'moveNode')
  const existing = graph.nodes[nodeId]
  if (!existing) return graph
  return {
    ...graph,
    nodes: {
      ...graph.nodes,
      [nodeId]: { ...existing, position },
    },
  }
}

/**
 * Delete-rewire: remove a node and reconnect incoming → outgoing edges
 * using the Cartesian product, deduplicating and refusing cycles/self-loops.
 *
 * Algorithm:
 * 1. Collect incoming wires (w.to === nodeId) and outgoing wires (w.from === nodeId).
 * 2. Remove the node and all incident wires from the graph.
 * 3. For each (incoming, outgoing) pair:
 *    - Skip if w_in.from === w_out.to (self-loop).
 *    - Skip if a wire w_in.from → w_out.to already exists (dedup).
 *    - Skip if adding the wire would create a cycle.
 *    - Otherwise add a new wire inheriting attr from incoming or outgoing.
 */
export function removeNodeWithRewire(graph: Graph, nodeId: string): Graph {
  assertEditable(graph, 'removeNodeWithRewire')

  const incoming = graph.wires.filter(w => w.to === nodeId)
  const outgoing = graph.wires.filter(w => w.from === nodeId)

  // Remove the node itself
  const nodesWithout = { ...graph.nodes }
  delete nodesWithout[nodeId]

  // Remove all incident wires
  const incidentIds = new Set([
    ...incoming.map(w => w.id),
    ...outgoing.map(w => w.id),
  ])
  const baseWires = graph.wires.filter(w => !incidentIds.has(w.id))

  // Build the graph-so-far to use for cycle checks
  let workingGraph: Graph = { ...graph, nodes: nodesWithout, wires: baseWires }

  // Track existing from→to pairs (deduplicate)
  const existingPairs = new Set(baseWires.map(w => `${w.from}|${w.to}`))

  const newWires: GraphWire[] = []

  for (const wIn of incoming) {
    for (const wOut of outgoing) {
      const from = wIn.from
      const to = wOut.to

      if (from === to) continue // self-loop
      const pairKey = `${from}|${to}`
      if (existingPairs.has(pairKey)) continue // dedup

      // Check cycle on workingGraph (which grows as we add rewires)
      if (wouldCreateCycle(workingGraph, from, to)) continue

      const newWire: GraphWire = {
        id: _genId(),
        from,
        to,
        attr: wIn.attr ?? wOut.attr ?? null,
      }
      newWires.push(newWire)
      existingPairs.add(pairKey)
      // Update workingGraph adjacency so subsequent cycle checks see the new wire
      workingGraph = { ...workingGraph, wires: [...workingGraph.wires, newWire] }
    }
  }

  return {
    ...workingGraph,
    wires: [...baseWires, ...newWires],
  }
}

/**
 * Splice-on-Alt-drag: insert nodeId into wireId (which connects A → B).
 * Result: A → nodeId, nodeId → B. Original wire removed.
 * Wire attr is preserved on both new wires.
 */
export function spliceNodeOntoWire(
  graph: Graph,
  nodeId: string,
  wireId: string,
): Graph {
  assertEditable(graph, 'spliceNodeOntoWire')

  const wire = graph.wires.find(w => w.id === wireId)
  if (!wire) return graph

  const A = wire.from
  const B = wire.to

  const withoutWire = graph.wires.filter(w => w.id !== wireId)

  const newWires: GraphWire[] = [
    { id: _genId(), from: A, to: nodeId, attr: wire.attr ?? null },
    { id: _genId(), from: nodeId, to: B, attr: wire.attr ?? null },
  ]

  return {
    ...graph,
    wires: [...withoutWire, ...newWires],
  }
}
