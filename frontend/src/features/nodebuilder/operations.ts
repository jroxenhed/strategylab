/**
 * operations.ts — Pure graph operations for the Node Strategy Builder (Unit 5).
 *
 * All functions return a NEW Graph; they never mutate the input.
 * All mutation operations throw ReadOnlyGraphError when graph.readOnly is true.
 * The store wraps each of them in one `commit`, so each is one undo step.
 */

import type { Graph, GraphNode, GraphWire, ParamValue } from '../../api/nodebuilder'
import { canWire } from './catalog'
import { sanitizeName as pathsSanitizeName, siblingNames, uniqueName as pathsUniqueName } from './paths'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const MIN_SUPPORTED_VERSION = 1

/** Node names: lowercase letter or underscore first, then up to 63 more (plan D3). */
export const NODE_NAME_RE = /^[a-z_][a-z0-9_]{0,63}$/

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
// Id and name helpers
// ---------------------------------------------------------------------------

/** Internal random id. Exported so tests can mock it. */
export function _genId(): string {
  return crypto.randomUUID()
}

/**
 * 8 lowercase base-36 characters taken from a random UUID (the last 8
 * letters and digits of it; hex digits are base-36 digits too).
 */
function shortId(): string {
  const chars = _genId().toLowerCase().replace(/[^a-z0-9]/g, '')
  return chars.slice(-8).padStart(8, '0')
}

/** A new node id: `n_` plus 8 lowercase base-36 characters (plan D3). */
export function newNodeId(): string {
  return `n_${shortId()}`
}

/** A new wire id: `w_` plus 8 lowercase base-36 characters. */
export function newWireId(): string {
  return `w_${shortId()}`
}

/**
 * Turn any text into a valid node name. The shared rule from paths.ts
 * (vector-tested against the backend): lowercase, anything else than a-z,
 * 0-9 and _ becomes _, a leading digit gets n_ in front. Empty text gives
 * "node".
 */
export function sanitizeName(raw: string): string {
  return pathsSanitizeName(raw)
}

/**
 * A name that no sibling (same parent) uses yet, by the shared paths.ts rule
 * (Houdini style: rsi, rsi1, rsi2; sma200 taken gives sma201).
 */
export function uniqueName(
  graph: Graph,
  base: string,
  parent: string | null = null,
  ignoreId?: string,
): string {
  return pathsUniqueName(pathsSanitizeName(base), siblingNames(graph, parent, ignoreId))
}

/** The lowest `in<k>` port on `nodeId` that no wire uses yet (in0, in1, ...). */
export function lowestFreeInPort(graph: Graph, nodeId: string): string {
  const used = new Set(graph.wires.filter(w => w.to === nodeId).map(w => w.to_port))
  for (let k = 0; ; k++) {
    const port = `in${k}`
    if (!used.has(port)) return port
  }
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
 * A missing or clashing name is replaced by a free one among its siblings.
 */
export function addNode(graph: Graph, node: GraphNode): Graph {
  assertEditable(graph, 'addNode')
  const parent = node.parent ?? null
  const wanted = node.name || node.type
  const name = uniqueName(graph, wanted, parent)
  return {
    ...graph,
    nodes: { ...graph.nodes, [node.id]: { ...node, name, parent } },
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

/** Remove several wires by id in one step. */
export function removeWires(graph: Graph, wireIds: readonly string[]): Graph {
  assertEditable(graph, 'removeWires')
  if (wireIds.length === 0) return graph
  const doomed = new Set(wireIds)
  return { ...graph, wires: graph.wires.filter(w => !doomed.has(w.id)) }
}

/** What a caller passes to addWire: the ports are optional and filled in. */
export type NewWire = Omit<GraphWire, 'from_port' | 'to_port'> & {
  from_port?: 'out'
  to_port?: string
}

/**
 * Add a wire. Rejects a wire into a node with no input port (a Ticker, a
 * Settings node) or out of one with no output port (Entry, Exit, a Settings
 * node): the canvas can't draw it, so it could never be seen or deleted.
 * Also rejects a wire that would create a cycle.
 *
 * `from_port` is always 'out'. When `to_port` is not given, the wire takes
 * the lowest free `in<k>` port on the target.
 */
export function addWire(graph: Graph, wire: NewWire): Graph {
  assertEditable(graph, 'addWire')
  if (!canWire(graph.nodes[wire.from]?.type, graph.nodes[wire.to]?.type)) {
    throw new Error(`Cannot add wire: "${wire.from}" → "${wire.to}" has no port at one end.`)
  }
  if (wouldCreateCycle(graph, wire.from, wire.to)) {
    throw new Error(`Cannot add wire: "${wire.from}" → "${wire.to}" would create a cycle.`)
  }
  const full: GraphWire = {
    ...wire,
    from_port: 'out',
    to_port: wire.to_port ?? lowestFreeInPort(graph, wire.to),
  }
  return {
    ...graph,
    wires: [...graph.wires, full],
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
 * Move several nodes at once. `deltas` is either one [dx, dy] for all of
 * them, or one [dx, dy] per id (same order as `ids`). Unknown ids are
 * skipped. Returns the same graph when nothing moves.
 */
export function moveNodes(
  graph: Graph,
  ids: readonly string[],
  deltas: [number, number] | ReadonlyArray<[number, number]>,
): Graph {
  assertEditable(graph, 'moveNodes')
  const perId = Array.isArray(deltas[0])
  let nodes: Record<string, GraphNode> | null = null
  ids.forEach((id, i) => {
    const existing = graph.nodes[id]
    if (!existing) return
    const d = (perId ? (deltas as ReadonlyArray<[number, number]>)[i] : deltas) as [number, number] | undefined
    if (!d || (d[0] === 0 && d[1] === 0)) return
    nodes ??= { ...graph.nodes }
    nodes[id] = { ...existing, position: [existing.position[0] + d[0], existing.position[1] + d[1]] }
  })
  return nodes ? { ...graph, nodes } : graph
}

/**
 * Update params on a node (shallow merge of `partial` into existing params).
 * No-op when the node doesn't exist. Component-side coercion is the caller's
 * responsibility; backend graph validation surfaces semantic errors on run.
 */
export function updateNodeParams(
  graph: Graph,
  nodeId: string,
  partial: Record<string, unknown>,
): Graph {
  assertEditable(graph, 'updateNodeParams')
  const existing = graph.nodes[nodeId]
  if (!existing) return graph
  return {
    ...graph,
    nodes: {
      ...graph.nodes,
      [nodeId]: {
        ...existing,
        params: { ...(existing.params ?? {}), ...(partial as Record<string, ParamValue>) },
      },
    },
  }
}

/**
 * Remove nodes and every wire that touches them. No rewiring.
 */
export function removeNodes(graph: Graph, nodeIds: readonly string[]): Graph {
  assertEditable(graph, 'removeNodes')
  const doomed = new Set(nodeIds.filter(id => id in graph.nodes))
  if (doomed.size === 0) return graph
  const nodes = { ...graph.nodes }
  for (const id of doomed) delete nodes[id]
  return {
    ...graph,
    nodes,
    wires: graph.wires.filter(w => !doomed.has(w.from) && !doomed.has(w.to)),
  }
}

/**
 * Delete a set of nodes and reconnect around them (the Houdini rule).
 *
 * When exactly ONE wire comes into the set from outside it, that wire's
 * source is connected to every outside consumer of the set, on the same
 * input port each consumer used. In every other case (no input from
 * outside, or two or more) the nodes and their wires are deleted with no
 * rewire, because there is no single obvious upstream to bridge from.
 *
 * A bridge is skipped when the port guard refuses it (no wire into a Ticker,
 * none out of Entry/Exit) or when it would close a cycle.
 */
export function removeNodesWithRewire(graph: Graph, nodeIds: readonly string[]): Graph {
  assertEditable(graph, 'removeNodesWithRewire')
  const doomed = new Set(nodeIds.filter(id => id in graph.nodes))
  if (doomed.size === 0) return graph

  const incoming = graph.wires.filter(w => doomed.has(w.to) && !doomed.has(w.from))
  const outgoing = graph.wires.filter(w => doomed.has(w.from) && !doomed.has(w.to))

  let working = removeNodes(graph, [...doomed])
  if (incoming.length !== 1) return working

  const wIn = incoming[0]
  const sourceType = working.nodes[wIn.from]?.type
  for (const wOut of outgoing) {
    const to = wOut.to
    if (to === wIn.from) continue // self-loop
    if (!canWire(sourceType, working.nodes[to]?.type)) continue
    if (wouldCreateCycle(working, wIn.from, to)) continue
    // The consumer's port is free again now that the old wire is gone.
    if (working.wires.some(w => w.to === to && w.to_port === wOut.to_port)) continue
    working = {
      ...working,
      wires: [
        ...working.wires,
        {
          id: newWireId(),
          from: wIn.from,
          to,
          from_port: 'out',
          to_port: wOut.to_port,
          attr: wIn.attr ?? null,
        },
      ],
    }
  }
  return working
}

/**
 * Single-node delete with rewire. Same rule as removeNodesWithRewire with a
 * set of one: one input bridges to every consumer, otherwise no rewire.
 */
export function removeNodeWithRewire(graph: Graph, nodeId: string): Graph {
  return removeNodesWithRewire(graph, [nodeId])
}

/**
 * Splice-on-Alt-drag: insert nodeId into wireId (which connects A → B).
 * Result: A → nodeId (lowest free port on nodeId), nodeId → B (on the port
 * the old wire used). Original wire removed. Wire attr is preserved on both
 * new wires.
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

  if (A === nodeId || B === nodeId) {
    // Splicing a node into its own incident wire would create a self-loop.
    throw new Error(
      `spliceNodeOntoWire: nodeId ${nodeId} is already an endpoint of wire ${wireId}`,
    )
  }

  // F7: reject splices that would form a cycle. If nodeId already has any
  // path back to A (via its other existing wires), inserting A→nodeId
  // closes A→nodeId→…→A. Likewise nodeId→B is safe only if B has no path
  // back to nodeId. Check against the graph with `wire` removed so the
  // existing A→B edge doesn't trivially block the second leg.
  const withoutWire = graph.wires.filter(w => w.id !== wireId)
  const graphMinusWire: Graph = { ...graph, wires: withoutWire }
  if (wouldCreateCycle(graphMinusWire, A, nodeId)) {
    throw new Error(
      `spliceNodeOntoWire: inserting ${nodeId} into ${wireId} (${A}→${B}) would create a cycle via ${A}→${nodeId}`,
    )
  }
  const firstLeg: GraphWire = {
    id: newWireId(),
    from: A,
    to: nodeId,
    from_port: 'out',
    to_port: lowestFreeInPort(graphMinusWire, nodeId),
    attr: wire.attr ?? null,
  }
  const graphAfterFirstLeg: Graph = {
    ...graphMinusWire,
    wires: [...graphMinusWire.wires, firstLeg],
  }
  if (wouldCreateCycle(graphAfterFirstLeg, nodeId, B)) {
    throw new Error(
      `spliceNodeOntoWire: inserting ${nodeId} into ${wireId} (${A}→${B}) would create a cycle via ${nodeId}→${B}`,
    )
  }

  const secondLeg: GraphWire = {
    id: newWireId(),
    from: nodeId,
    to: B,
    from_port: 'out',
    to_port: wire.to_port,
    attr: wire.attr ?? null,
  }

  return {
    ...graph,
    wires: [...withoutWire, firstLeg, secondLeg],
  }
}
