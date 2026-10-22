/**
 * Pure wire operations for item 3.G (spec S23): move a wire's end
 * (reconnect), put a node into a wire (splice), and the texts the canvas
 * shows when one of them is refused.
 *
 * Every function takes a graph and returns a new one (or throws), so the
 * caller wraps it in one `commit` and it is one undo step. The rules are
 * the same as for drawing a new wire (`connectionProblem`, spec S08): no
 * self-loop, no cycle, nothing into a Ticker and nothing out of a terminal
 * node. A wire dropped on a port that has one replaces it (F457, Houdini).
 */

import type { Graph, GraphWire, ParamValue } from '../../../api/nodebuilder'
import { canWire, hasInputPort, hasOutputPort } from '../catalog'
import { addWire as opAddWire, newWireId, removeNodesWithRewire, removeWires, wouldCreateCycle } from '../operations'
import {
  attrListValue,
  attrValue,
  connectedPortsByNode,
  connectionProblem,
  connectWire,
  defaultReadPatch,
  portIndex,
  portsOf,
  portsSpecOf,
  primaryWriteOf,
  readParamForPort,
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
// Replace a wire on a full port (F457)
// ---------------------------------------------------------------------------

/**
 * The node whose stream a wire out of `nodeId` carries. A frame's
 * `subnet_input` hands on what its outside wire brings, so it stands for
 * that wire's source. Null when such a port has no outside wire.
 */
export function streamSourceOf(graph: Pick<Graph, 'nodes' | 'wires'>, nodeId: string): string | null {
  let id = nodeId
  for (let i = 0; i < 64; i++) {
    const n = graph.nodes[id]
    if (!n || n.type !== 'subnet_input' || !n.parent) return id
    const k = boundaryPortOf(n)
    const outer = k === null ? undefined : graph.wires.find(w => w.to === n.parent && w.to_port === `in${k}`)
    if (!outer) return null
    id = outer.from
  }
  return null
}

/** The first write of the stream a wire out of `nodeId` carries (see `streamSourceOf`). */
function streamWriteOf(graph: Pick<Graph, 'nodes' | 'wires'>, nodeId: string | null | undefined): string | null {
  const src = nodeId ? streamSourceOf(graph, nodeId) : null
  return src ? primaryWriteOf(graph.nodes[src]) : null
}

/**
 * Reads of `oldName` through the port `to.toPort` become `newName` (null:
 * empty, so the compiler's default read applies). A frame's top port
 * passes on to the nodes its boundary feeds, at any depth; there an AND /
 * OR list term follows too. A read the user picked is left as it is.
 */
function followReads(graph: Graph, to: string, toPort: string, oldName: string, newName: string | null, depth = 0): Graph {
  const consumer = graph.nodes[to]
  if (!consumer || depth > 32) return graph
  if (isNetworkType(consumer.type)) {
    const b = boundaryInputs(graph.nodes, to).find(x => `in${boundaryPortOf(x)}` === toPort)
    if (!b) return graph
    let g = graph
    for (const w of graph.wires) {
      if (w.from === b.id) g = followReads(g, w.to, w.to_port, oldName, newName, depth + 1)
    }
    return g
  }
  const spec = readParamForPort(consumer.type, toPort)
  if (!spec) return graph
  const current = consumer.params?.[spec.name]
  let value: ParamValue | undefined
  if (spec.type === 'attr' && attrValue(current) === oldName) value = newName
  if (spec.type === 'attr_list' && depth > 0) {
    const list = attrListValue(current)
    if (list.includes(oldName)) {
      value = list.flatMap(x => (x !== oldName ? [x] : newName && !list.includes(newName) ? [newName] : []))
    }
  }
  if (value === undefined) return graph
  return { ...graph, nodes: { ...graph.nodes, [to]: { ...consumer, params: { ...consumer.params, [spec.name]: value } } } }
}

/**
 * Take a wire off its port for a replace: as on Delete (an AND / OR / XOR
 * term it added is taken back), and a frame port the wire came from goes
 * too, with its outside wire, when it feeds nothing else (no orphan port).
 */
function takeOff(graph: Graph, wire: GraphWire): Graph {
  const g = removeWiresWithTerms(graph, [wire.id])
  const b = g.nodes[wire.from]
  if (!b || b.type !== 'subnet_input' || !b.parent || g.wires.some(w => w.from === b.id)) return g
  const port = `in${boundaryPortOf(b)}`
  const nodes = { ...g.nodes }
  delete nodes[b.id]
  return { ...g, nodes, wires: g.wires.filter(w => w.to !== b.id && !(w.to === b.parent && w.to_port === port)) }
}

/**
 * Take a wire off its input port before a new wire goes there (a drop on a
 * port that has a wire replaces it, Houdini). See `takeOff`. A single read
 * param that still holds the old stream's first write is emptied, so the
 * new wire's default read fills it with the new source's. A read the user
 * picked is left as it is. The old stream through a frame port is the
 * port's outside source (`streamSourceOf`).
 */
export function liftWire(graph: Graph, wire: GraphWire): Graph {
  const old = streamWriteOf(graph, wire.from)
  const lifted = takeOff(graph, wire)
  return old ? followReads(lifted, wire.to, wire.to_port, old, null) : lifted
}

/**
 * Replace `occupant` with the wire `make` adds to the graph it gets (the
 * occupant already taken off), then make the reads that followed the old
 * stream follow the new one: on the consumer, and through a frame's top
 * port on the nodes inside (F457).
 */
function replaceOnPort(graph: Graph, occupant: GraphWire, newFrom: string | null | undefined, make: (g: Graph) => Graph): Graph {
  const oldName = streamWriteOf(graph, occupant.from)
  const next = make(takeOff(graph, occupant))
  if (!oldName) return next
  const newName = streamWriteOf(next, newFrom)
  return newName === oldName ? next : followReads(next, occupant.to, occupant.to_port, oldName, newName)
}

/**
 * The wire already on the input port a plain (sibling) connection would
 * use, or null. The wire `ignoreWireId` (one being moved) never counts.
 */
export function occupantOf(
  graph: Pick<Graph, 'wires'>,
  c: ConnectionLike,
  ignoreWireId?: string | null,
): GraphWire | null {
  if (!c.target || c.targetHandle == null) return null
  return graph.wires.find(w => w.id !== ignoreWireId && w.to === c.target && w.to_port === c.targetHandle) ?? null
}

/**
 * `connectionProblemIgnoring` where a full port is no problem: the wire on
 * it would be replaced. The replaced wire does not count in the cycle check
 * either.
 */
export function replaceProblemIgnoring(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  c: ConnectionLike,
  ignoreWireId: string | null | undefined,
): WireProblem | null {
  const occupant = occupantOf(graph, c, ignoreWireId)
  return connectionProblem(withoutWire(withoutWire(graph, ignoreWireId), occupant?.id), c)
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
 * AND / OR / XOR list is taken back. A port that already has another wire
 * takes this one in its place: that wire goes (`liftWire`, F457). Throws
 * `WireOpError` when the new place breaks a wire rule. Returns the same
 * graph when nothing moved.
 */
export function reconnectWire(graph: Graph, wireId: string, to: WireEnds): Graph {
  const index = graph.wires.findIndex(w => w.id === wireId)
  if (index < 0) throw new WireOpError('missing', `reconnectWire: no wire ${wireId}`)
  const old = graph.wires[index]
  const conn = reconnectTarget(old, to)
  const toPort = conn.targetHandle
  if (conn.source === old.from && conn.target === old.to && toPort === old.to_port) return graph
  const problem = replaceProblemIgnoring(graph, conn, wireId)
  if (problem) throw new WireOpError(problem, `reconnectWire: ${wireProblemText(problem)}`)

  const keepLabel = old.from === conn.source && old.attr != null ? { attr: old.attr } : {}
  const move = (base: Graph): Graph => {
    const joined = connectWire(removeWiresWithTerms(base, [wireId]), {
      id: wireId,
      from: conn.source as string,
      to: conn.target as string,
      to_port: toPort ?? undefined,
      ...keepLabel,
    })
    // connectWire adds the wire at the end; put it back where it was.
    const moved = joined.wires[joined.wires.length - 1]
    const rest = joined.wires.slice(0, -1)
    const at = Math.min(base.wires.findIndex(w => w.id === wireId), rest.length)
    return { ...joined, wires: [...rest.slice(0, at), moved, ...rest.slice(at)] }
  }
  const occupant = occupantOf(graph, conn, wireId)
  return occupant ? replaceOnPort(graph, occupant, conn.source, move) : move(graph)
}

/**
 * `reconnectWire` with networks (F457, S31): a wire end dropped on a port
 * inside a frame goes the way a new wire does (`connectReplacing`), through
 * a frame port, and never straight across the frame edge. A move between
 * siblings keeps the wire id. A wire already on the port is replaced.
 * Throws when the move is refused; across a network edge with the S31 text.
 */
export function reconnectInNetworks(graph: Graph, wireId: string, to: WireEnds): Graph {
  const old = graph.wires.find(w => w.id === wireId)
  if (!old) throw new WireOpError('missing', `reconnectWire: no wire ${wireId}`)
  const conn = reconnectTarget(old, to)
  const problem = replaceNetworkProblem(graph, conn, wireId)
  if (problem === 'crosses') throw new Error(WIRE_CROSSES_NETWORK_TEXT)
  if (problem) throw new WireOpError(problem, `reconnectWire: ${wireProblemText(problem)}`)
  const plan = planConnection(withoutWire(graph, wireId), conn)
  if (plan.kind === 'plain') return reconnectWire(graph, wireId, plan.conn)
  return connectReplacing(removeWiresWithTerms(graph, [wireId]), conn)
}

/** The connection a moved wire end makes: an unnamed input on the same node keeps its port. */
export function reconnectTarget(old: GraphWire, to: WireEnds): ConnectionLike {
  const toPort = to.targetHandle ?? (to.target === old.to ? old.to_port : null)
  return { ...to, sourceHandle: to.sourceHandle ?? 'out', targetHandle: toPort }
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

/**
 * The status-bar text after a drop replaced `occupant` (F457). The flash
 * is a polite live region, so a screen reader hears it too.
 */
export function replacedText(graph: Pick<Graph, 'nodes'>, occupant: GraphWire): string {
  const node = graph.nodes[occupant.to]
  const label = portsSpecOf(node?.type)?.ports[portIndex(occupant.to_port)]?.label ?? occupant.to_port
  return `Replaced the wire into input ${label} of ${node?.name || occupant.to}`
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

/**
 * The wire already on the input port a drop would use, with networks taken
 * into account (F457), or null. A frame's inner handle (`bnd:<id>`) stands
 * for its boundary node's `in0`; a routed wire's port is the inner node's.
 * The wire `ignoreWireId` (one being moved) never counts.
 */
export function networkOccupant(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  c: ConnectionLike,
  ignoreWireId?: string | null,
): GraphWire | null {
  const g = withoutWire(graph, ignoreWireId)
  const plan = planConnection(g, c)
  if (plan.kind === 'crosses') return null
  if (plan.kind === 'route') return g.wires.find(w => w.to === plan.to && w.to_port === plan.toPort) ?? null
  return occupantOf(g, plan.conn)
}

/**
 * `networkConnectionProblem` where a full port is no problem: the wire on
 * it would be replaced (F457). Every other rule stays: no self-loop, no
 * cycle (the replaced wire does not count), no port past the last one, no
 * wire across a network edge.
 */
export function replaceNetworkProblem(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  c: ConnectionLike,
  ignoreWireId?: string | null,
): NetworkWireProblem | null {
  const occupant = networkOccupant(graph, c, ignoreWireId)
  return networkConnectionProblem(withoutWire(graph, occupant?.id), c, ignoreWireId)
}

/**
 * `connectInNetworks`, and a port that already has a wire takes the new
 * one in its place (F457): the old wire goes (`liftWire`) and the new one
 * comes in, in one graph change (one undo step). Dropping the same source
 * on the port again changes nothing. Throws when the connection is refused.
 */
export function connectReplacing(graph: Graph, c: ConnectionLike, wireId: string = newWireId()): Graph {
  const occupant = networkOccupant(graph, c)
  if (!occupant) return connectInNetworks(graph, c, wireId)
  const plan = planConnection(graph, c)
  if (plan.kind === 'plain' && occupant.from === plan.conn.source) return graph
  // A routed port already fed by this source through its frame port.
  if (plan.kind === 'route' && graph.nodes[occupant.from]?.parent === plan.networkId
    && streamSourceOf(graph, occupant.from) === plan.from) return graph
  const newFrom = plan.kind === 'route' ? plan.from : plan.kind === 'plain' ? plan.conn.source : null
  return replaceOnPort(graph, occupant, newFrom, g => connectInNetworks(g, c, wireId))
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
 * The wire a drop between these two ends would replace (F457), or null: a
 * free port, a drop that is refused, or no drop target. The wire
 * `ignoreWireId` (one being moved) never counts. The canvas marks it during
 * the drag (ReplacePreview.tsx).
 */
export function dropOccupant(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  state: DropEnds | null | undefined,
  ignoreWireId?: string | null,
): GraphWire | null {
  const a = state?.fromHandle
  const b = state?.toHandle
  if (!a || !b || a.type === b.type) return null
  const [src, dst] = a.type === 'source' ? [a, b] : [b, a]
  const c: ConnectionLike = { source: src.nodeId, sourceHandle: src.id ?? null, target: dst.nodeId, targetHandle: dst.id ?? null }
  if (replaceNetworkProblem(graph, c, ignoreWireId) !== null) return null
  return networkOccupant(graph, c, ignoreWireId)
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
