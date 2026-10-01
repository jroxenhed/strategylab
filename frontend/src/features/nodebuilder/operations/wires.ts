/**
 * Pure wire operations for item 3.G (spec S23): move a wire's end
 * (reconnect), put a node into a wire (splice), and the texts the canvas
 * shows when one of them is refused.
 *
 * Every function takes a graph and returns a new one (or throws), so the
 * caller wraps it in one `commit` and it is one undo step. The rules are
 * the same as for drawing a new wire (`connectionProblem`, spec S08): no
 * self-loop, no cycle, no second wire into a full port, nothing into a
 * Ticker and nothing out of a terminal node.
 */

import type { Graph, GraphWire } from '../../../api/nodebuilder'
import { canWire, hasOutputPort } from '../catalog'
import { addWire as opAddWire, newWireId, removeNodesWithRewire, removeWires, wouldCreateCycle } from '../operations'
import {
  connectedPortsByNode,
  connectionProblem,
  connectWire,
  portIndex,
  portsOf,
  portsSpecOf,
  removeWiresWithTerms,
  type ConnectionLike,
} from '../streamLabels'

/** Why a wire move or a splice was refused. */
export type WireProblem = NonNullable<ReturnType<typeof connectionProblem>>

/** Thrown by `reconnectWire` and `spliceIntoWire`; `problem` says why. */
export class WireOpError extends Error {
  readonly problem: WireProblem
  constructor(problem: WireProblem, message: string) {
    super(message)
    this.problem = problem
    this.name = 'WireOpError'
  }
}

/** The status-bar text for a refused wire move or splice. */
export function wireProblemText(problem: WireProblem): string {
  switch (problem) {
    case 'cycle': return 'That would create a loop'
    case 'full': return 'That input already has a wire'
    case 'self': return 'A node cannot wire into itself'
    case 'no_port': return 'Those two nodes cannot be wired'
    case 'direction': return 'Wire an output into an input'
    case 'missing': return 'That wire is no longer there'
  }
}

/** The graph without one wire (for checks that must ignore the wire being moved). */
export function withoutWire<G extends Pick<Graph, 'wires'>>(graph: G, wireId: string | null | undefined): G {
  if (!wireId || !graph.wires.some(w => w.id === wireId)) return graph
  return { ...graph, wires: graph.wires.filter(w => w.id !== wireId) }
}

/**
 * `connectionProblem` as if the wire `ignoreWireId` were not there. While a
 * wire's end is being moved, its own old port must not count as full, and
 * it must not count in the cycle check.
 */
export function connectionProblemIgnoring(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  c: ConnectionLike,
  ignoreWireId: string | null | undefined,
): WireProblem | null {
  return connectionProblem(withoutWire(graph, ignoreWireId), c)
}

// ---------------------------------------------------------------------------
// Reconnect
// ---------------------------------------------------------------------------

/** Where a moved wire end landed (React Flow's `Connection`). */
export interface WireEnds {
  source: string | null
  target: string | null
  sourceHandle?: string | null
  targetHandle?: string | null
}

/**
 * Move one or both ends of a wire. The wire keeps its id and its place in
 * the wire list. The new consumer's empty read param takes the source's
 * first write, as for a new wire. The old consumer's single read params are
 * left as they are (like a deleted wire); a term the wire added to an
 * AND / OR / XOR list is taken back. Throws `WireOpError` when the new
 * place breaks a wire rule. Returns the same graph when nothing moved.
 */
export function reconnectWire(graph: Graph, wireId: string, to: WireEnds): Graph {
  const index = graph.wires.findIndex(w => w.id === wireId)
  if (index < 0) throw new WireOpError('missing', `reconnectWire: no wire ${wireId}`)
  const old = graph.wires[index]
  const toPort = to.targetHandle ?? (to.target === old.to ? old.to_port : null)
  const conn = { ...to, sourceHandle: to.sourceHandle ?? 'out', targetHandle: toPort }
  if (conn.source === old.from && conn.target === old.to && toPort === old.to_port) return graph
  const problem = connectionProblemIgnoring(graph, conn, wireId)
  if (problem) throw new WireOpError(problem, `reconnectWire: ${wireProblemText(problem)}`)

  const lifted = removeWiresWithTerms(graph, [wireId])
  const keepLabel = old.from === conn.source && old.attr != null ? { attr: old.attr } : {}
  const joined = connectWire(lifted, {
    id: wireId,
    from: conn.source as string,
    to: conn.target as string,
    to_port: toPort ?? undefined,
    ...keepLabel,
  })
  // connectWire adds the wire at the end; put it back where it was.
  const moved = joined.wires[joined.wires.length - 1]
  const rest = joined.wires.slice(0, -1)
  const at = Math.min(index, rest.length)
  return { ...joined, wires: [...rest.slice(0, at), moved, ...rest.slice(at)] }
}

// ---------------------------------------------------------------------------
// Splice
// ---------------------------------------------------------------------------

/**
 * True when a node can be put into a wire at all: it has an output and at
 * least one free input port. (Whether a given wire fits is `spliceProblem`.)
 */
export function canSpliceNode(graph: Pick<Graph, 'nodes' | 'wires'>, nodeId: string): boolean {
  const node = graph.nodes[nodeId]
  if (!node || !hasOutputPort(node.type)) return false
  const used = connectedPortsByNode(graph.wires).get(nodeId) ?? []
  return portsOf(node.type, used).some(p => !p.connected)
}

/** The problem with splicing `nodeId` into `wireId`, and the input port it would use. */
export function splicePlan(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  nodeId: string,
  wireId: string,
): { problem: WireProblem | null; port: string | null } {
  const wire = graph.wires.find(w => w.id === wireId)
  const node = graph.nodes[nodeId]
  if (!wire || !node) return { problem: 'missing', port: null }
  if (wire.from === nodeId || wire.to === nodeId) return { problem: 'self', port: null }
  const fromType = graph.nodes[wire.from]?.type
  const toType = graph.nodes[wire.to]?.type
  if (!canWire(fromType, node.type) || !canWire(node.type, toType)) return { problem: 'no_port', port: null }

  // The first free input that takes the wire's source (port-aware: a node
  // whose in0 is taken uses its next free port).
  const minus = withoutWire(graph, wireId)
  const used = connectedPortsByNode(minus.wires).get(nodeId) ?? []
  let sawCycle = false
  let port: string | null = null
  for (const p of portsOf(node.type, used)) {
    if (p.connected) continue
    const problem = connectionProblem(minus, { source: wire.from, target: nodeId, sourceHandle: 'out', targetHandle: p.id })
    if (problem === null) { port = p.id; break }
    if (problem === 'cycle') sawCycle = true
  }
  if (!port) return { problem: sawCycle ? 'cycle' : 'full', port: null }

  // The second leg goes into the old wire's port, free again once the old
  // wire is gone; it must not close a loop through the first leg.
  const firstLeg: GraphWire = { id: '_first', from: wire.from, to: nodeId, from_port: 'out', to_port: port }
  if (wouldCreateCycle({ ...minus, wires: [...minus.wires, firstLeg] } as Graph, nodeId, wire.to)) {
    return { problem: 'cycle', port: null }
  }
  return { problem: null, port }
}

/** Why `nodeId` cannot be spliced into `wireId`, or null when it can. */
export function spliceProblem(graph: Pick<Graph, 'nodes' | 'wires'>, nodeId: string, wireId: string): WireProblem | null {
  return splicePlan(graph, nodeId, wireId).problem
}

/**
 * Put a node into a wire (spec S23): the old wire `A -> B` goes, `A out ->
 * node <first free input>` and `node out -> B <the old port>` come in. The
 * node's empty read param takes A's first write. B's params are not
 * touched: its named read now comes through the node. Both new wires take
 * the old wire's place in the list. Throws `WireOpError` when refused.
 */
export function spliceIntoWire(graph: Graph, nodeId: string, wireId: string): Graph {
  const { problem, port } = splicePlan(graph, nodeId, wireId)
  if (problem || !port) {
    const p = problem ?? 'full'
    throw new WireOpError(p, `spliceIntoWire: ${wireProblemText(p)}`)
  }
  const index = graph.wires.findIndex(w => w.id === wireId)
  const old = graph.wires[index]
  const firstId = newWireId()
  const secondId = newWireId()
  let next = removeWires(graph, [wireId])
  next = connectWire(next, { id: firstId, from: old.from, to: nodeId, to_port: port })
  next = opAddWire(next, {
    id: secondId,
    from: nodeId,
    to: old.to,
    to_port: old.to_port,
    ...(old.attr != null ? { attr: old.attr } : {}),
  })
  // Both new wires sit where the old one was.
  const added = next.wires.filter(w => w.id === firstId || w.id === secondId)
  const rest = next.wires.filter(w => w.id !== firstId && w.id !== secondId)
  const at = Math.min(index, rest.length)
  return { ...next, wires: [...rest.slice(0, at), ...added, ...rest.slice(at)] }
}

// ---------------------------------------------------------------------------
// Delete and reconnect, full-port notice
// ---------------------------------------------------------------------------

/**
 * The node a plain Delete removes with a rewire: exactly one selected graph
 * node with one wire in and one or more wires out. Null otherwise.
 */
export function rewireCandidate(graph: Pick<Graph, 'nodes' | 'wires'>, nodeIds: readonly string[]): string | null {
  if (nodeIds.length !== 1) return null
  const id = nodeIds[0]
  if (!graph.nodes[id]) return null
  const ins = graph.wires.filter(w => w.to === id).length
  const outs = graph.wires.filter(w => w.from === id).length
  return ins === 1 && outs >= 1 ? id : null
}

/** How many wires a delete-with-rewire of these nodes would add back. */
export function rewiredCount(graph: Graph, nodeIds: readonly string[]): number {
  const before = new Set(graph.wires.map(w => w.id))
  try {
    return removeNodesWithRewire(graph, nodeIds).wires.filter(w => !before.has(w.id)).length
  } catch {
    return 0
  }
}

/** The status-bar text after a delete-with-rewire (spec S23). */
export function rewireFlash(name: string, count: number): string {
  return `Deleted ${name} and reconnected ${count} wire${count === 1 ? '' : 's'}`
}

/** A wire drag that started at a port (Canvas's pending wire). */
export interface PortDragStart {
  fromNodeId: string
  handleType: 'source' | 'target'
  handleId: string | null
}

/**
 * The short notice for a drag that started at an input port that already
 * has a wire and ended on empty canvas (deferred from Wave 2): such a drag
 * can never wire the new node into that port, so instead of silently adding
 * an unwired node the canvas shows this. Null when the drag is fine. The
 * wire being moved (a reconnect) does not count.
 */
export function fullPortNotice(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  drag: PortDragStart | null | undefined,
  ignoreWireId?: string | null,
): string | null {
  if (!drag || drag.handleType !== 'target' || !drag.handleId) return null
  const full = graph.wires.some(w => w.id !== ignoreWireId && w.to === drag.fromNodeId && w.to_port === drag.handleId)
  if (!full) return null
  const node = graph.nodes[drag.fromNodeId]
  const label = portsSpecOf(node?.type)?.ports[portIndex(drag.handleId)]?.label ?? drag.handleId
  return `Input ${label} of ${node?.name || drag.fromNodeId} already has a wire. Drag its wire end to move it.`
}
