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
import { canWire, hasInputPort, hasOutputPort } from '../catalog'
import { addWire as opAddWire, newWireId, removeNodesWithRewire, removeWires, wouldCreateCycle } from '../operations'
import {
  connectedPortsByNode,
  connectionProblem,
  connectWire,
  defaultReadPatch,
  portIndex,
  portsOf,
  portsSpecOf,
  removeWiresWithTerms,
  type ConnectionLike,
} from '../streamLabels'
import { BOUNDARY_HANDLE_PREFIX, boundaryInputs, boundaryOutput, boundaryPortOf, isNetworkType } from '../rfMapping'
import { routeInto } from '../networkOps'

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

// ---------------------------------------------------------------------------
// Wires and networks (W5, FA2)
// ---------------------------------------------------------------------------

/** The S31 copy for a wire that would cross a network edge (`wire_crosses_network`). */
export const WIRE_CROSSES_NETWORK_TEXT = 'Wires connect nodes in the same network. Route through a network port.'

/**
 * What a dropped wire does once networks are taken into account (FA2):
 * - `plain`: both ends are siblings (or a frame port and a sibling of the
 *   frame); the normal wire rules apply to `conn`;
 * - `route`: an outside node feeds a node directly inside a network one
 *   level down, so the wire goes through a frame port (`routeInto`);
 * - `crosses`: anything else (two levels, out of a network, between two
 *   groups or sibling subnets) is refused.
 */
export type NetworkConnection =
  | { kind: 'plain'; conn: ConnectionLike }
  | { kind: 'route'; from: string; networkId: string; to: string; toPort: string }
  | { kind: 'crosses' }

/** A frame's inner boundary handle (`bnd:<id>`) stands for the boundary node itself. */
function resolveFrameHandles(c: ConnectionLike): ConnectionLike {
  let { source, target, sourceHandle, targetHandle } = c
  if (sourceHandle != null && sourceHandle.startsWith(BOUNDARY_HANDLE_PREFIX)) {
    source = sourceHandle.slice(BOUNDARY_HANDLE_PREFIX.length)
    sourceHandle = 'out'
  }
  if (targetHandle != null && targetHandle.startsWith(BOUNDARY_HANDLE_PREFIX)) {
    target = targetHandle.slice(BOUNDARY_HANDLE_PREFIX.length)
    targetHandle = 'in0'
  }
  return { source, target, sourceHandle, targetHandle }
}

/** The first input port of `nodeId` with no wire, or `in0`. */
function freeInputPort(graph: Pick<Graph, 'nodes' | 'wires'>, nodeId: string): string {
  const connected = connectedPortsByNode(graph.wires).get(nodeId) ?? []
  return portsOf(graph.nodes[nodeId]?.type, connected).find(p => !p.connected)?.id ?? 'in0'
}

/** How a connection is made in a graph with networks (FA2). */
export function planConnection(graph: Pick<Graph, 'nodes' | 'wires'>, c: ConnectionLike): NetworkConnection {
  const conn = resolveFrameHandles(c)
  if (!conn.source || !conn.target) return { kind: 'plain', conn }
  const from = graph.nodes[conn.source]
  const to = graph.nodes[conn.target]
  if (!from || !to) return { kind: 'plain', conn }
  const pf = from.parent ?? null
  const pt = to.parent ?? null
  if (pf === pt) return { kind: 'plain', conn }
  const net = pt ? graph.nodes[pt] : undefined
  if (net && isNetworkType(net.type) && (net.parent ?? null) === pf && to.type !== 'subnet_input') {
    const toPort = conn.targetHandle ?? freeInputPort(graph, conn.target)
    return { kind: 'route', from: conn.source, networkId: net.id, to: conn.target, toPort }
  }
  return { kind: 'crosses' }
}

/** Why a wire may not be dropped here in a graph with networks, or null. */
export type NetworkWireProblem = WireProblem | 'crosses'

/**
 * `connectionProblemIgnoring` with networks (FA2): a wire between siblings
 * follows the S08 rules; a frame port takes a wire from a sibling of the
 * frame (the frame's output feeds one); an outside node may feed a node one
 * level inside through a port; anything else is `crosses`.
 */
export function networkConnectionProblem(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  c: ConnectionLike,
  ignoreWireId?: string | null,
): NetworkWireProblem | null {
  const g = withoutWire(graph, ignoreWireId)
  const plan = planConnection(g, c)
  if (plan.kind === 'crosses') return 'crosses'
  if (plan.kind === 'route') {
    if (g.wires.some(w => w.to === plan.to && w.to_port === plan.toPort)) return 'full'
    const src = g.nodes[plan.from]
    // A network source feeds out only through its output port.
    if (src && isNetworkType(src.type) && !boundaryOutput(g.nodes, src.id)) return 'no_port'
    if (!canWire(src?.type, g.nodes[plan.to]?.type)) return 'no_port'
    // The wire actually added at the outer level is source -> network
    // (FE-02): refuse it when the network already feeds the source.
    if (wouldCreateCycle(g as Graph, plan.from, plan.networkId)) return 'cycle'
    return null
  }
  const { source, target, targetHandle } = plan.conn
  const to = target ? g.nodes[target] : undefined
  const from = source ? g.nodes[source] : undefined
  // A frame's top port (`in<k>`) or bottom output: the network types may not
  // be in an older catalog, so the frame decides which ports exist.
  if (from && to && source !== target && (isNetworkType(to.type) || isNetworkType(from.type))) {
    if (isNetworkType(to.type)) {
      const k = targetHandle != null ? portIndex(targetHandle) : -1
      if (k < 0 || !boundaryInputs(g.nodes, to.id).some(b => boundaryPortOf(b) === k)) return 'no_port'
      if (g.wires.some(w => w.to === to.id && w.to_port === targetHandle)) return 'full'
    } else {
      if (!hasInputPort(to.type)) return 'no_port'
      if (targetHandle != null && g.wires.some(w => w.to === to.id && w.to_port === targetHandle)) return 'full'
    }
    if (isNetworkType(from.type) && !boundaryOutput(g.nodes, from.id)) return 'no_port'
    if (wouldCreateCycle(g as Graph, from.id, to.id)) return 'cycle'
    return null
  }
  return connectionProblem(g, plan.conn)
}

/** The two handle ends a finished wire drag reports (React Flow's FinalConnectionState). */
export interface DropEnds {
  fromHandle?: { nodeId: string; id?: string | null; type: 'source' | 'target' } | null
  toHandle?: { nodeId: string; id?: string | null; type: 'source' | 'target' } | null
}

/**
 * True when a wire drag ended ON a port that refuses it because it is
 * across a network edge (FE-09). A drop on a node body or anywhere that is
 * not a port is never a crossing, whatever the pointer passed over.
 */
export function dropCrossesNetwork(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  state: DropEnds | null | undefined,
  ignoreWireId?: string | null,
): boolean {
  const a = state?.fromHandle
  const b = state?.toHandle
  if (!a || !b) return false
  const [src, dst] = a.type === 'source' ? [a, b] : [b, a]
  const c: ConnectionLike = { source: src.nodeId, sourceHandle: src.id ?? null, target: dst.nodeId, targetHandle: dst.id ?? null }
  return networkConnectionProblem(graph, c, ignoreWireId) === 'crosses'
}

/**
 * Make the connection `c` in one graph change (one undo step). A routed
 * wire goes through a frame port, and the inside consumer's empty read
 * takes what the outside source writes (as a direct wire would). Throws
 * when the connection is refused.
 */
export function connectInNetworks(graph: Graph, c: ConnectionLike, wireId: string = newWireId()): Graph {
  const problem = networkConnectionProblem(graph, c)
  if (problem === 'crosses') throw new Error(WIRE_CROSSES_NETWORK_TEXT)
  if (problem) throw new WireOpError(problem, wireProblemText(problem))
  const plan = planConnection(graph, c)
  if (plan.kind === 'route') {
    const routed = routeInto(graph, plan.from, plan.networkId, plan.to, plan.toPort)
    if (!routed) throw new Error(WIRE_CROSSES_NETWORK_TEXT)
    const patch = defaultReadPatch(graph, { from: plan.from, to: plan.to, to_port: plan.toPort })
    if (!patch) return routed
    const node = routed.nodes[plan.to]
    return { ...routed, nodes: { ...routed.nodes, [plan.to]: { ...node, params: { ...node.params, ...patch } } } }
  }
  if (plan.kind !== 'plain' || !plan.conn.source || !plan.conn.target) throw new Error('connectInNetworks: missing end')
  return connectWire(graph, {
    id: wireId,
    from: plan.conn.source,
    to: plan.conn.target,
    to_port: plan.conn.targetHandle ?? undefined,
  })
}
