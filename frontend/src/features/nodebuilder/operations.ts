/**
 * operations.ts — Pure graph operations for the Node Strategy Builder (Unit 5).
 *
 * All functions return a NEW Graph; they never mutate the input.
 * All mutation operations throw ReadOnlyGraphError when graph.readOnly is true.
 * The store wraps each of them in one `commit`, so each is one undo step.
 */

import type { Graph, GraphNode, GraphWire, ParamValue } from '../../api/nodebuilder'
import { NODE_CATALOG, canWire, hasOutputPort, type NodeCatalogEntry } from './catalog'
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
 * The given node ids that exist, plus every node inside them at any depth
 * (W5): a network's children and boundary nodes go with it, so no node is
 * left with a dangling `parent` (the backend refuses such a graph).
 */
export function withDescendants(graph: Pick<Graph, 'nodes'>, nodeIds: readonly string[]): Set<string> {
  const doomed = new Set(nodeIds.filter(id => id in graph.nodes))
  if (doomed.size === 0) return doomed
  let grew = true
  while (grew) {
    grew = false
    for (const n of Object.values(graph.nodes)) {
      if (n.parent && doomed.has(n.parent) && !doomed.has(n.id)) {
        doomed.add(n.id)
        grew = true
      }
    }
  }
  return doomed
}

/**
 * Remove nodes and every wire that touches them. No rewiring. A network
 * takes everything inside it along (`withDescendants`).
 */
export function removeNodes(graph: Graph, nodeIds: readonly string[]): Graph {
  assertEditable(graph, 'removeNodes')
  const doomed = withDescendants(graph, nodeIds)
  if (doomed.size === 0) return graph
  const nodes = { ...graph.nodes }
  for (const id of doomed) delete nodes[id]
  return mapBoxMembers({
    ...graph,
    nodes,
    wires: graph.wires.filter(w => !doomed.has(w.from) && !doomed.has(w.to)),
  }, id => (doomed.has(id) ? null : id))
}

/**
 * Rewrite every network box's member list (FC-2): `fn` maps a member id to
 * its new id, or null to drop it. Boxes whose members do not change stay
 * the same objects, and the graph is returned as is when none changes, so
 * box membership never points at a node that is gone.
 */
export function mapBoxMembers(graph: Graph, fn: (id: string) => string | null): Graph {
  const boxes = graph.annotations?.boxes
  if (!boxes || boxes.length === 0) return graph
  let changed = false
  const next = boxes.map(b => {
    let out: string[] | null = null
    b.members.forEach((m, i) => {
      const to = fn(m)
      if (to === m && !out) return
      out ??= b.members.slice(0, i)
      if (to !== null && !out.includes(to)) out.push(to)
    })
    if (!out) return b
    changed = true
    return { ...b, members: out }
  })
  return changed ? { ...graph, annotations: { ...graph.annotations!, boxes: next } } : graph
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
  const doomed = withDescendants(graph, nodeIds)
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

// ---------------------------------------------------------------------------
// Flags (spec S16): display and bypass
// ---------------------------------------------------------------------------

/** The two Houdini flags a node carries. */
export type NodeFlag = 'display' | 'bypass'

// Looked up per call by the flag checks; the catalog is static.
const ENTRY_BY_NAME: ReadonlyMap<string, NodeCatalogEntry> = new Map(NODE_CATALOG.map(e => [e.name, e]))

/**
 * Why `flag` cannot be turned on for a node of this type, or null when it
 * can (foundation 4.6). Only nodes with an output have a display flag
 * (not terminals, not Settings nodes). Every node but terminals and
 * Tickers can be bypassed. A type missing from the catalog gets neither:
 * the compiler does not know it, bypassed or not (S13).
 */
export function flagProblemForType(nodeType: string | undefined, flag: NodeFlag): string | null {
  const entry = nodeType ? ENTRY_BY_NAME.get(nodeType) : undefined
  if (!entry) {
    return flag === 'display' ? 'Unsupported nodes have no display flag' : 'Unsupported nodes cannot be bypassed'
  }
  if (flag === 'display') {
    if (entry.cat === 'output') return 'Terminals have no display flag'
    if (!hasOutputPort(nodeType)) return 'Settings nodes have no display flag'
    return null
  }
  if (entry.cat === 'output') return 'Terminals cannot be bypassed'
  // Same rule as the backend's `bypassable=False` (nodes_data.py, nodes_terminals.py).
  if (entry.cat === 'ticker') return 'Tickers cannot be bypassed'
  return null
}

/** Same as flagProblemForType, for a node of the graph. A missing node: "Select a node first". */
export function flagProblem(graph: Graph, nodeId: string, flag: NodeFlag): string | null {
  const node = graph.nodes[nodeId]
  if (!node) return 'Select a node first'
  return flagProblemForType(node.type, flag)
}

/**
 * Set one flag on one node (S16). Turning display on clears it on every
 * other node of the same network (same `parent`) in the same step: there
 * is one display node per network. Turning a flag on for a node that may
 * not have it (flagProblem) changes nothing; turning a flag off always
 * works. Returns the same graph when nothing changes, so the store records
 * no undo step (clicking the lit display dot again is a no-op).
 */
export function setFlag(graph: Graph, nodeId: string, flag: NodeFlag, value: boolean): Graph {
  return setFlags(graph, [nodeId], flag, value)
}

/**
 * Set one flag on several nodes in one step (B on a selection). Nodes that
 * may not carry the flag are skipped. For display only the last allowed id
 * of each network keeps the flag.
 */
export function setFlags(graph: Graph, nodeIds: readonly string[], flag: NodeFlag, value: boolean): Graph {
  assertEditable(graph, 'setFlag')
  // Copied on the first change only, so "no change" returns the same graph.
  let nodes = graph.nodes
  let changed = false
  const put = (n: GraphNode, v: boolean) => {
    if (n[flag] === v) return
    if (!changed) { nodes = { ...graph.nodes }; changed = true }
    nodes[n.id] = { ...n, [flag]: v }
  }
  for (const id of nodeIds) {
    const node = nodes[id]
    if (!node) continue
    if (value && flagProblemForType(node.type, flag) != null) continue
    if (flag === 'display' && value) {
      const parent = node.parent ?? null
      for (const other of Object.values(nodes)) {
        if (other.id !== id && other.display && (other.parent ?? null) === parent) put(other, false)
      }
    }
    put(node, value)
  }
  return changed ? { ...graph, nodes } : graph
}

// ---------------------------------------------------------------------------
// Replace a node (spec S13 "Replace with…")
// ---------------------------------------------------------------------------

/**
 * Put node `newId` (already in the graph) where `oldId` was, then delete
 * `oldId`: the new node takes the old one's position and network, and
 * every wire of the old node that fits moves over. Inputs keep their port
 * (`in0`, `in1`, ...) while the new type has that port; the output moves
 * when the new type has one. Wires that do not fit, or would close a
 * cycle, are dropped. The display flag moves over when the new type can
 * carry it. One step, so the store records one undo entry.
 */
export function replaceNode(graph: Graph, oldId: string, newId: string): Graph {
  assertEditable(graph, 'replaceNode')
  const old = graph.nodes[oldId]
  const fresh = graph.nodes[newId]
  if (!old || !fresh || oldId === newId) return graph
  const entry = ENTRY_BY_NAME.get(fresh.type)
  const maxIn = entry?.inputs ? (entry.inputs.dynamic ? Infinity : Math.max(entry.inputs.max, entry.inputs.ports.length)) : (entry?.defaults.ins ?? 1)
  const placed: GraphNode = {
    ...fresh,
    position: old.position,
    parent: old.parent ?? null,
    display: old.display && flagProblemForType(fresh.type, 'display') == null,
  }
  // The replacement takes the old node's place in its network boxes too (FC-2).
  const withMembers = mapBoxMembers({ ...graph, nodes: { ...graph.nodes, [newId]: placed } }, m => (m === oldId ? newId : m))
  let working: Graph = removeNodes(withMembers, [oldId])
  for (const w of graph.wires) {
    if (w.from === newId || w.to === newId) continue
    let moved: GraphWire | null = null
    if (w.to === oldId) {
      const k = /^in(\d+)$/.exec(w.to_port)
      if (!k || Number(k[1]) >= maxIn) continue
      if (!canWire(working.nodes[w.from]?.type, fresh.type)) continue
      if (working.wires.some(x => x.to === newId && x.to_port === w.to_port)) continue
      moved = { ...w, to: newId }
    } else if (w.from === oldId) {
      if (!canWire(fresh.type, working.nodes[w.to]?.type)) continue
      moved = { ...w, from: newId }
    }
    if (!moved || wouldCreateCycle(working, moved.from, moved.to)) continue
    working = { ...working, wires: [...working.wires, moved] }
  }
  return working
}
