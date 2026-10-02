/**
 * Graph edits for network frames (W5, spec S31 and S32a). Pure functions:
 * each takes a graph and returns a new one; the caller commits.
 *
 * - `frameMoveDeltas`: dragging a frame moves the frame and everything in it.
 * - `routeInto`: a wire from a node outside a network to a node inside it
 *   goes through a boundary port (FA2): outside node -> network port `in<k>`,
 *   `subnet_input` -> inside node.
 * - `reparentNodes`: nodes dropped into a frame (or dragged out of one)
 *   change their `parent`, all in one step. Their wires are re-routed
 *   through the frame port when one step does it, and removed otherwise
 *   (the caller shows a toast).
 * - `addGroupTerminal`: a click on a ghost card adds the missing terminal.
 *
 * Positions stay absolute (EA-1). Wires connect only siblings (plan D7).
 */

import type { Graph, GraphNode, GraphWire } from '../../api/nodebuilder'
import { newNodeId, newWireId, uniqueName } from './operations'
import { nodePath, rewritePathRefs } from './paths'
import { boundaryInputs, boundaryPortOf, computeFrameLayouts, type FrameLayout, type MissingTerminal } from './rfMapping'

/** Every node inside `networkId`, at any depth. */
export function descendantsOf(nodes: Graph['nodes'], networkId: string): string[] {
  const kids = new Map<string, string[]>()
  for (const n of Object.values(nodes)) {
    if (!n.parent) continue
    const list = kids.get(n.parent)
    if (list) list.push(n.id)
    else kids.set(n.parent, [n.id])
  }
  const out: string[] = []
  const seen = new Set<string>([networkId])
  const stack = [...(kids.get(networkId) ?? [])]
  while (stack.length > 0) {
    const id = stack.pop()!
    if (seen.has(id)) continue
    seen.add(id)
    out.push(id)
    stack.push(...(kids.get(id) ?? []))
  }
  return out
}

/** True when `id` is `ancestorId` or sits inside it. */
export function isInside(nodes: Graph['nodes'], id: string, ancestorId: string): boolean {
  let cur: string | null = id
  for (let i = 0; cur && i < 64; i++) {
    if (cur === ancestorId) return true
    cur = nodes[cur]?.parent ?? null
  }
  return false
}

/**
 * The moves for a drag stop that may include frames. `moves` holds each
 * dragged node's new absolute top-left (for a frame, the corner it is drawn
 * at). A frame's stored position and every node inside it move by the
 * frame's delta, so the frame is redrawn where it was dropped. A node that
 * is inside a dragged frame takes the frame's delta, not its own.
 */
export function frameMoveDeltas(
  nodes: Graph['nodes'],
  moves: ReadonlyArray<{ id: string; position: [number, number] }>,
  layouts: Map<string, FrameLayout> = computeFrameLayouts(nodes),
): { ids: string[]; deltas: Array<[number, number]> } {
  const delta = new Map<string, [number, number]>()
  const dragged = new Set(moves.map(m => m.id))
  // Frames first, so their delta wins for the nodes inside them.
  const ordered = [...moves].sort((a, b) => Number(layouts.has(b.id)) - Number(layouts.has(a.id)))
  for (const m of ordered) {
    const n = nodes[m.id]
    if (!n || delta.has(m.id)) continue
    // A node inside another dragged frame moves with that frame.
    let p = n.parent
    let carried = false
    for (let i = 0; p && i < 64; i++) {
      if (dragged.has(p) && layouts.has(p)) { carried = true; break }
      p = nodes[p]?.parent ?? null
    }
    if (carried) continue
    const l = layouts.get(m.id)
    const from: [number, number] = l ? [l.x, l.y] : n.position
    const d: [number, number] = [m.position[0] - from[0], m.position[1] - from[1]]
    delta.set(m.id, d)
    if (l) for (const c of descendantsOf(nodes, m.id)) delta.set(c, d)
  }
  const ids: string[] = []
  const deltas: Array<[number, number]> = []
  for (const [id, d] of delta) {
    if (d[0] === 0 && d[1] === 0) continue
    ids.push(id)
    deltas.push(d)
  }
  return { ids, deltas }
}

/**
 * The innermost frame that holds `point` (absolute flow units), among the
 * frames inside `within` (the network on screen; null: every frame). Null
 * when the point is on no frame. The Tab menu places a new node there
 * (S31, UX-04).
 */
export function frameAtPoint(
  nodes: Graph['nodes'],
  layouts: Map<string, FrameLayout>,
  point: { x: number; y: number },
  within: string | null,
): string | null {
  let best: string | null = null
  let bestArea = Infinity
  for (const [id, l] of layouts) {
    if (within && (id === within || !isInside(nodes, id, within))) continue
    if (point.x < l.x || point.x > l.x + l.w || point.y < l.y || point.y > l.y + l.h) continue
    const area = l.w * l.h
    if (area < bestArea) {
      best = id
      bestArea = area
    }
  }
  return best
}

function withNode(graph: Graph, node: GraphNode): Graph {
  return { ...graph, nodes: { ...graph.nodes, [node.id]: node } }
}

/** The lowest port number no `subnet_input` of `networkId` uses yet. */
function freeBoundaryPort(graph: Graph, networkId: string): number {
  const used = new Set<number>()
  for (const n of Object.values(graph.nodes)) {
    if (n.type !== 'subnet_input' || n.parent !== networkId) continue
    const k = boundaryPortOf(n)
    if (k !== null) used.add(k)
  }
  let k = 0
  while (used.has(k)) k += 1
  return k
}

/** A new `subnet_input` in `networkId` on a free port. Returns the graph and the port handle. */
function addBoundaryInput(graph: Graph, networkId: string, near: [number, number]): { graph: Graph; boundaryId: string; port: string } {
  const k = freeBoundaryPort(graph, networkId)
  const id = newNodeId()
  const node: GraphNode = {
    id,
    type: 'subnet_input',
    name: uniqueName(graph, `in${k}`, networkId),
    parent: networkId,
    // The backend maps network input in<k> to the boundary whose port is k.
    params: { port: k },
    position: [near[0], near[1] - 80],
    display: false,
    bypass: false,
  }
  return { graph: withNode(graph, node), boundaryId: id, port: `in${k}` }
}

/**
 * Route a wire from `fromId` (outside the network) to `toId`'s port
 * `toPort` (inside it) through a boundary port (FA2). `fromId` must sit at
 * the network's level and `toId` directly inside it; otherwise null. A
 * source already fed into the network reuses its port, so one source gets
 * one boundary (S39 rule).
 */
export function routeInto(graph: Graph, fromId: string, networkId: string, toId: string, toPort: string): Graph | null {
  const net = graph.nodes[networkId]
  const from = graph.nodes[fromId]
  const to = graph.nodes[toId]
  if (!net || !from || !to) return null
  if ((from.parent ?? null) !== (net.parent ?? null) || to.parent !== networkId) return null
  let boundaryId: string | null = null
  for (const b of boundaryInputs(graph.nodes, networkId)) {
    const port = `in${boundaryPortOf(b)}`
    if (graph.wires.some(w => w.from === fromId && w.to === networkId && w.to_port === port)) {
      boundaryId = b.id
      break
    }
  }
  let g = graph
  if (!boundaryId) {
    const added = addBoundaryInput(g, networkId, to.position)
    g = added.graph
    boundaryId = added.boundaryId
    g = { ...g, wires: [...g.wires, { id: newWireId(), from: fromId, to: networkId, from_port: 'out', to_port: added.port }] }
  }
  const inner: GraphWire = { id: newWireId(), from: boundaryId, to: toId, from_port: 'out', to_port: toPort }
  return { ...g, wires: [...g.wires, inner] }
}

/** What `reparentNode` did. */
export interface ReparentResult {
  graph: Graph
  /** Wires that had no route and were removed (the toast counts them). */
  removedWires: number
}

/** One node of a reparent: its new parent (null: the root) and absolute top-left. */
export interface ReparentMove {
  id: string
  parent: string | null
  position: [number, number]
}

/**
 * Move `nodeId` into `newParent` (a network id, or null for the root) at
 * the absolute `position`. See `reparentNodes`.
 */
export function reparentNode(
  graph: Graph,
  nodeId: string,
  newParent: string | null,
  position: [number, number],
): ReparentResult {
  return reparentNodes(graph, [{ id: nodeId, parent: newParent, position }])
}

/**
 * Move a set of nodes to new parents in ONE step (FE-01): every node moves
 * first, then each wire that touches a moved node is planned once, so a
 * wire between two nodes that move together stays a plain wire. Wires
 * follow FA2:
 * - both ends now siblings: the wire is kept as it is;
 * - a source now inside the network it fed through port `in<k>`: the port
 *   is collapsed, the source feeds that port's consumers directly, and the
 *   boundary node goes when nothing else uses it;
 * - into a network one level down: an outside source is routed through a
 *   frame port (`routeInto`);
 * - out to the parent level: a wire that came in through a frame port now
 *   runs straight from that port's outside source, and an inside consumer
 *   is fed through a new frame port;
 * - anything else (two levels, a group's output) is removed and counted.
 * Each node takes a free name among its new siblings, and stored paths to
 * it (an Output Group's `ticker`) follow it (FE-03).
 */
export function reparentNodes(graph: Graph, moves: ReadonlyArray<ReparentMove>): ReparentResult {
  let g: Graph = graph
  const oldParentOf = new Map<string, string | null>()
  for (const m of moves) {
    const node = g.nodes[m.id]
    if (!node || oldParentOf.has(m.id)) continue
    const oldParent = node.parent ?? null
    if (oldParent === m.parent) continue
    const name = uniqueName(g, node.name || node.type, m.parent, m.id)
    g = withNode(g, { ...node, parent: m.parent, name, position: m.position })
    oldParentOf.set(m.id, oldParent)
  }
  if (oldParentOf.size === 0) return { graph, removedWires: 0 }
  const moved = (id: string) => oldParentOf.has(id)
  const touching = graph.wires.filter(w => moved(w.from) || moved(w.to))
  const touchingIds = new Set(touching.map(w => w.id))
  g = { ...g, wires: g.wires.filter(w => !touchingIds.has(w.id)) }
  let removed = 0
  const parentOf = (id: string) => g.nodes[id]?.parent ?? null
  const collapsedBoundaries = new Set<string>()
  for (const w of touching) {
    const from = g.nodes[w.from]
    const to = g.nodes[w.to]
    if (!from || !to) continue
    // Still (or now) siblings: keep the wire as it was.
    if ((from.parent ?? null) === (to.parent ?? null)) {
      g = { ...g, wires: [...g.wires, w] }
      continue
    }
    // The source now sits inside the network it fed: collapse the port.
    if (moved(w.from) && from.parent === to.id) {
      const b = boundaryInputs(g.nodes, to.id).find(x => `in${boundaryPortOf(x)}` === w.to_port)
      if (b) {
        g = {
          ...g,
          wires: g.wires.map(x => (x.from === b.id && parentOf(x.to) === to.id ? { ...x, id: newWireId(), from: from.id } : x)),
        }
        collapsedBoundaries.add(b.id)
        continue
      }
      removed += 1
      continue
    }
    let next: Graph | null = null
    if (moved(w.to)) {
      const nodeId = w.to
      const newParent = to.parent ?? null
      const oldParent = oldParentOf.get(nodeId) ?? null
      if (newParent && (from.parent ?? null) === parentOf(newParent)) {
        // Dropped into a network next to its source: route in.
        next = routeInto(g, from.id, newParent, nodeId, w.to_port)
      } else if (from.type === 'subnet_input' && from.parent === oldParent && oldParent && parentOf(oldParent) === newParent) {
        // Dragged out: take the stream from the port's outside source.
        const k = boundaryPortOf(from)
        const outer = k === null ? undefined : g.wires.find(x => x.to === oldParent && x.to_port === `in${k}`)
        if (outer) next = { ...g, wires: [...g.wires, { ...w, id: newWireId(), from: outer.from }] }
      }
    }
    if (!next && moved(w.from)) {
      const oldParent = oldParentOf.get(w.from) ?? null
      if (oldParent && (to.parent ?? null) === oldParent && parentOf(oldParent) === (from.parent ?? null)) {
        // Outgoing to a node left inside the network: feed it through a port.
        next = routeInto(g, from.id, oldParent, to.id, w.to_port)
      }
    }
    if (next) g = next
    else removed += 1
  }
  // A collapsed port with nothing left on it goes (its outer wire went above).
  for (const bId of collapsedBoundaries) {
    const b = g.nodes[bId]
    if (!b) continue
    const port = `in${boundaryPortOf(b)}`
    const used = g.wires.some(x => x.from === bId || x.to === bId || (x.to === b.parent && x.to_port === port))
    if (used) continue
    const nodes = { ...g.nodes }
    delete nodes[bId]
    g = { ...g, nodes }
  }
  // Stored paths follow each moved node (FE-03).
  for (const id of oldParentOf.keys()) {
    try {
      g = rewritePathRefs(g, nodePath(graph, id), nodePath(g, id))
    } catch {
      // A broken parent chain: nothing to rewrite.
    }
  }
  return { graph: g, removedWires: removed }
}

/** Toast text after a move that removed wires (S31 copy). */
export function dragOutToast(nodeName: string, networkName: string, removed: number, into = false): string {
  const verb = into ? `Moved ${nodeName} into ${networkName}.` : `Moved ${nodeName} out of ${networkName}.`
  const wires = removed === 1 ? '1 wire had no route and was removed.' : `${removed} wires had no route and were removed.`
  return `${verb} ${wires} Undo (⌘Z)`
}

/** Add the terminal a ghost card stands for (S32a) at the absolute `position`. Returns the graph and the new id. */
export function addGroupTerminal(
  graph: Graph,
  groupId: string,
  missing: Pick<MissingTerminal, 'type' | 'side'>,
  position: [number, number],
): { graph: Graph; nodeId: string } {
  const id = newNodeId()
  const params: GraphNode['params'] = missing.type === 'regime'
    ? { signal: null, on_flip: 'hold' }
    : { signal: null }
  if (missing.side) params.side = missing.side
  const base = missing.side ? `${missing.type}_${missing.side}` : missing.type
  const node: GraphNode = {
    id,
    type: missing.type,
    name: uniqueName(graph, base, groupId),
    parent: groupId,
    params,
    position,
    display: false,
    bypass: false,
  }
  return { graph: withNode(graph, node), nodeId: id }
}

/**
 * The graph with a group's direction set. Switching to `regime_switch` also
 * stores `side: long` on the group's entry and exit that have no side yet
 * (the server reads a missing side as long), so the ghost row asks only for
 * the short side (S32a, UX-14). One recipe, so the caller commits it as one
 * undo step.
 */
export function withGroupDirection(graph: Graph, groupId: string, direction: string): Graph {
  const group = graph.nodes[groupId]
  if (!group) return graph
  const nodes = { ...graph.nodes, [groupId]: { ...group, params: { ...group.params, direction } } }
  if (direction === 'regime_switch') {
    for (const n of Object.values(graph.nodes)) {
      if (n.parent !== groupId || (n.type !== 'entry' && n.type !== 'exit')) continue
      if (n.params?.side === 'long' || n.params?.side === 'short') continue
      nodes[n.id] = { ...n, params: { ...n.params, side: 'long' } }
    }
  }
  return { ...graph, nodes }
}
