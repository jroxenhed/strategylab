/**
 * Collapse a selection into a subnet (spec S39, plan W6 item 6.D).
 *
 * `collapseIntoSubnet(graph, ids)` is a pure function: it returns the next
 * graph and a summary, and the command commits it as ONE undo step, so
 * Cmd+Z gives back the exact graph from before.
 *
 * What it does:
 * 1. Adds a `subnet` node (name subnet1, subnet2, ... among the siblings)
 *    in the selection's network, at the centre of the selection, drawn as
 *    a card (`meta.view = "card"`).
 * 2. Moves the selected nodes into it (`parent` = the subnet). Positions
 *    stay absolute flow coordinates (W3 contract), so nothing jumps.
 *    Boxes and notes that sit inside the selection move with it.
 * 3. Each outside node that fed a selected node gets one `subnet_input`
 *    (in0, in1, ... by the source's x position): one wire from the source
 *    into the subnet's port `in<k>`, and one wire from the boundary to each
 *    former consumer port.
 * 4. Selected nodes that fed outside nodes: one feeds the `subnet_output`
 *    directly; two or more feed a `merge` node (`merge_out`) first. Every
 *    outside consumer then reads from the subnet. A consumer read that
 *    names an attribute resolves by name through the stream and is left
 *    alone. A read left empty (it took the wired node's primary write) is
 *    filled in with the name it read before when a merge is added, since a
 *    merge has no primary write (`keepReads`).
 * 5. Wires between selected nodes stay exactly as they were.
 */

import type { Graph, GraphNode, GraphWire, NetworkBox, ParamValue, StickyNote } from '../../../api/nodebuilder'
import { newNodeId, newWireId, ReadOnlyGraphError, uniqueName } from '../operations'
import { uniqueName as pathsUniqueName } from '../paths'
import { boundaryOutput, boundaryPortOf, computeFrameLayouts, isFrameNetwork, isNetworkType, TERMINAL_TYPES } from '../rfMapping'
import {
  ATTR_NAME_RE, attrListValue, attrValue, portIndex, portsSpecOf, primaryWriteOf,
  readParamForPort, readParamsOf, writesOf,
} from '../streamLabels'

/** Copy (S39 "States and feedback"). */
export const COLLAPSE_TEXT = {
  nothing: 'Select nodes to collapse',
  mixedParents: 'Select nodes in one network to collapse them.',
  terminal: 'Terminals and groups cannot go inside a subnet.',
  locked: 'This asset is locked. Unlock it first.',
  tooManyOutputs: `More than ${mergeMax()} selected nodes feed nodes outside. Collapse fewer at a time.`,
} as const

export type CollapseProblem = keyof typeof COLLAPSE_TEXT

/** How many inputs one merge node takes (catalog: 16). */
function mergeMax(): number {
  return portsSpecOf('merge')?.max ?? 16
}

/** Distinct selected nodes that feed a node outside the selection. */
function outgoingProducers(graph: Graph, selected: ReadonlySet<string>): string[] {
  const out: string[] = []
  for (const w of graph.wires) {
    if (selected.has(w.from) && !selected.has(w.to) && !out.includes(w.from)) out.push(w.from)
  }
  return out
}

/** Default names (S39 "Copy"). */
export const SUBNET_BASE_NAME = 'subnet1'
export const MERGE_OUT_NAME = 'merge_out'
export const OUTPUT_BOUNDARY_NAME = 'out'

/** The card size used to place things before React Flow has measured them. */
const CARD_W = 176
const CARD_H = 60
/** Room between the selection and the boundary nodes. */
const BOUNDARY_GAP = 80
const BOUNDARY_STEP = 180

export interface CollapseResult {
  graph: Graph
  subnetId: string
  /** Nodes moved in (the selection, without boxes and notes). */
  count: number
  inputs: number
  outputs: number
  /** True when a merge node joins two or more outgoing streams. */
  merged: boolean
  /** How many inside nodes the merge joins (0 without a merge). */
  mergedFrom: number
}

/** Types a selection may never take inside a subnet. */
function isForbidden(node: GraphNode): boolean {
  return TERMINAL_TYPES.has(node.type)
    || node.type === 'output_group'
    || node.type === 'subnet_input'
    || node.type === 'subnet_output'
}

/** True when `id` or any network above it is a locked asset instance. */
export function insideLockedAsset(nodes: Graph['nodes'], id: string | null): boolean {
  let cur = id
  for (let i = 0; cur && i < 64; i++) {
    const n: GraphNode | undefined = nodes[cur]
    if (!n) return false
    if (n.locked) return true
    cur = n.parent
  }
  return false
}

/**
 * Why these nodes cannot be collapsed, or null when they can. Checked
 * before anything changes. Unknown ids are ignored.
 */
export function collapseProblem(graph: Graph, ids: readonly string[]): CollapseProblem | null {
  const nodes = ids.map(id => graph.nodes[id]).filter((n): n is GraphNode => !!n)
  if (nodes.length === 0) return 'nothing'
  const parent = nodes[0].parent ?? null
  if (nodes.some(n => (n.parent ?? null) !== parent)) return 'mixedParents'
  if (insideLockedAsset(graph.nodes, parent)) return 'locked'
  if (nodes.some(isForbidden)) return 'terminal'
  // One merge joins the outgoing streams; past its input limit the backend
  // would refuse the extra ports (FE-12).
  if (outgoingProducers(graph, new Set(nodes.map(n => n.id))).length > mergeMax()) return 'tooManyOutputs'
  return null
}

/** The S39 toast after a collapse. */
export function collapseToast(r: Pick<CollapseResult, 'count' | 'inputs' | 'outputs' | 'merged' | 'mergedFrom'>, name: string): string {
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
  let text = `Collapsed ${plural(r.count, 'node', 'nodes')} into ${name} · ${plural(r.inputs, 'input', 'inputs')} · ${plural(r.outputs, 'output', 'outputs')}`
  if (r.merged) text += ` (merged from ${r.mergedFrom} nodes)`
  return text
}

/**
 * The node whose output stream the output of `id` really is, as the
 * backend flatten works it out (`_resolve`): through a network to what
 * feeds its `subnet_output`, through a `subnet_input` to what feeds its
 * network's port. Null when nothing arrives (or for a locked instance,
 * whose children are not in the graph).
 */
function realSource(graph: Graph, id: string | null | undefined, seen: ReadonlySet<string> = new Set()): string | null {
  if (!id || seen.has(id)) return null
  const n = graph.nodes[id]
  if (!n) return null
  const next = new Set(seen).add(id)
  const fedBy = (to: string, port: string) => graph.wires.find(w => w.to === to && w.to_port === port)?.from ?? null
  if (isNetworkType(n.type)) {
    if (n.bypass) return realSource(graph, fedBy(id, 'in0'), next)
    const out = boundaryOutput(graph.nodes, id)
    return out ? realSource(graph, fedBy(out.id, 'in0'), next) : null
  }
  if (n.type === 'subnet_input') {
    const k = boundaryPortOf(n)
    return n.parent != null && k !== null ? realSource(graph, fedBy(n.parent, `in${k}`), next) : null
  }
  return n.type === 'subnet_output' ? null : id
}

/**
 * The name the backend reads through `wire` for a consumer read left empty
 * (schema `_default_name`): the wire's own v2 label when the real source
 * writes it, else the real source's primary write.
 */
function defaultReadThrough(graph: Graph, wire: GraphWire): string | null {
  const srcId = realSource(graph, wire.from)
  const src = srcId ? graph.nodes[srcId] : undefined
  if (!src) return null
  if (wire.attr && ATTR_NAME_RE.test(wire.attr) && writesOf(src).some(s => s.name === wire.attr)) return wire.attr
  return primaryWriteOf(src)
}

/**
 * The param changes that keep an outside consumer reading what it read
 * before its wires in `rerouted` started to come out of a merge. A read
 * that already names an attribute (or has a catalog default) resolves by
 * name and stays. An empty `attr` read fed by a re-routed wire gets the
 * name read through that wire before. An empty `attr_list` read fed by a
 * re-routed wire gets one name per wire in port order, as the backend v3
 * migration writes it. Null when nothing changes.
 */
function keepReads(graph: Graph, consumerId: string, rerouted: ReadonlySet<string>): Record<string, ParamValue> | null {
  const consumer = graph.nodes[consumerId]
  if (!consumer) return null
  const into = graph.wires
    .filter(w => w.to === consumerId && portIndex(w.to_port) >= 0)
    .sort((a, b) => portIndex(a.to_port) - portIndex(b.to_port))
  const patch: Record<string, ParamValue> = {}
  for (const spec of readParamsOf(consumer.type)) {
    const fed = into.filter(w => readParamForPort(consumer.type, w.to_port)?.name === spec.name)
    if (!fed.some(w => rerouted.has(w.id))) continue
    const current = consumer.params?.[spec.name]
    if (spec.type === 'attr_list') {
      if (attrListValue(current).length > 0) continue
      const names = fed.map(w => defaultReadThrough(graph, w)).filter((n): n is string => !!n)
      if (names.length > 0) patch[spec.name] = names
    } else {
      if (attrValue(current) || (typeof spec.default === 'string' && spec.default !== '')) continue
      // The backend reads a single param through its first wired port.
      if (!rerouted.has(fed[0].id)) continue
      const name = defaultReadThrough(graph, fed[0])
      if (name) patch[spec.name] = name
    }
  }
  return Object.keys(patch).length > 0 ? patch : null
}

function inside(rect: [number, number, number, number], box: { x0: number; y0: number; x1: number; y1: number }): boolean {
  const [x, y, w, h] = rect
  return x >= box.x0 && y >= box.y0 && x + w <= box.x1 && y + h <= box.y1
}

/**
 * Collapse `ids` into a new subnet. Throws ReadOnlyGraphError on a
 * read-only graph and an Error carrying the S39 text when the selection
 * cannot be collapsed (see `collapseProblem`).
 */
export function collapseIntoSubnet(graph: Graph, ids: readonly string[]): CollapseResult {
  if (graph.readOnly) throw new ReadOnlyGraphError('collapse into subnet')
  const problem = collapseProblem(graph, ids)
  if (problem) throw new Error(COLLAPSE_TEXT[problem])

  const selected = new Set(ids.filter(id => id in graph.nodes))
  const members = [...selected].map(id => graph.nodes[id])
  const parent = members[0].parent ?? null

  // The selection's bounding box (absolute flow units). A member drawn as a
  // frame counts by its drawn rectangle, not its stored position (FE-12).
  const frames = members.some(n => isFrameNetwork(n, parent))
    ? computeFrameLayouts(graph.nodes, undefined, parent)
    : null
  const rectOf = (n: GraphNode) => {
    const l = frames?.get(n.id)
    return l ? { x: l.x, y: l.y, w: l.w, h: l.h } : { x: n.position[0], y: n.position[1], w: CARD_W, h: CARD_H }
  }
  const rects = members.map(rectOf)
  const x0 = Math.min(...rects.map(r => r.x))
  const y0 = Math.min(...rects.map(r => r.y))
  const x1 = Math.max(...rects.map(r => r.x + r.w))
  const y1 = Math.max(...rects.map(r => r.y + r.h))
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2

  const subnetId = newNodeId()
  const subnet: GraphNode = {
    id: subnetId,
    type: 'subnet',
    name: uniqueName(graph, SUBNET_BASE_NAME, parent),
    parent,
    params: {},
    position: [Math.round(cx - CARD_W / 2), Math.round(cy - CARD_H / 2)],
    display: false,
    bypass: false,
    meta: { view: 'card' },
  }

  const nodes: Record<string, GraphNode> = { ...graph.nodes, [subnetId]: subnet }
  for (const n of members) nodes[n.id] = { ...n, parent: subnetId }

  // Names inside the subnet, so boundary and merge names stay unique.
  const takenInside = new Set(members.map(n => n.name))
  const insideName = (base: string) => {
    const name = pathsUniqueName(base, takenInside)
    takenInside.add(name)
    return name
  }

  const incoming: GraphWire[] = []
  const outgoing: GraphWire[] = []
  for (const w of graph.wires) {
    const toIn = selected.has(w.to)
    const fromIn = selected.has(w.from)
    if (toIn && !fromIn) incoming.push(w)
    else if (fromIn && !toIn) outgoing.push(w)
  }
  // A crossing wire is replaced at its own place in the list (wire order
  // can matter to a reader), and new wires go after the old ones.
  const replaced = new Map<string, GraphWire>()
  const added: GraphWire[] = []

  // ── Inputs: one boundary per distinct outside source ───────────────────
  const sources: string[] = []
  for (const w of incoming) if (!sources.includes(w.from)) sources.push(w.from)
  sources.sort((a, b) => (graph.nodes[a]?.position[0] ?? 0) - (graph.nodes[b]?.position[0] ?? 0))
  const boundaryOf = new Map<string, string>()
  sources.forEach((src, k) => {
    const id = newNodeId()
    boundaryOf.set(src, id)
    nodes[id] = {
      id,
      type: 'subnet_input',
      name: insideName(`in${k}`),
      parent: subnetId,
      // The backend maps network input in<k> to the boundary whose port is k.
      params: { port: k },
      position: [Math.round(x0 + k * BOUNDARY_STEP), Math.round(y0 - BOUNDARY_GAP)],
      display: false,
      bypass: false,
    }
    added.push({ id: newWireId(), from: src, to: subnetId, from_port: 'out', to_port: `in${k}` })
  })
  for (const w of incoming) {
    // Same wire id and consumer port; only its source moves to the boundary.
    replaced.set(w.id, { ...w, from: boundaryOf.get(w.from)!, from_port: 'out' })
  }

  // ── Output: one subnet_output, after a merge when 2+ nodes feed out ────
  const producers: string[] = []
  for (const w of outgoing) if (!producers.includes(w.from)) producers.push(w.from)
  producers.sort((a, b) => (graph.nodes[a]?.position[0] ?? 0) - (graph.nodes[b]?.position[0] ?? 0))
  let merged = false
  if (producers.length > 0) {
    const outId = newNodeId()
    nodes[outId] = {
      id: outId,
      type: 'subnet_output',
      name: insideName(OUTPUT_BOUNDARY_NAME),
      parent: subnetId,
      params: {},
      position: [Math.round(cx - CARD_W / 2), Math.round(y1 + BOUNDARY_GAP + (producers.length > 1 ? BOUNDARY_GAP : 0))],
      display: false,
      bypass: false,
    }
    let feed = producers[0]
    if (producers.length > 1) {
      merged = true
      const mergeId = newNodeId()
      nodes[mergeId] = {
        id: mergeId,
        type: 'merge',
        name: insideName(MERGE_OUT_NAME),
        parent: subnetId,
        params: {},
        position: [Math.round(cx - CARD_W / 2), Math.round(y1 + BOUNDARY_GAP / 2)],
        display: false,
        bypass: false,
      }
      producers.forEach((p, k) => {
        added.push({ id: newWireId(), from: p, to: mergeId, from_port: 'out', to_port: `in${k}` })
      })
      feed = mergeId
      // A merge has no primary write, so a consumer read left empty would
      // read nothing through the subnet. Name what it read before.
      const rerouted = new Set(outgoing.map(w => w.id))
      for (const cid of new Set(outgoing.map(w => w.to))) {
        const patch = keepReads(graph, cid, rerouted)
        if (patch) nodes[cid] = { ...nodes[cid], params: { ...nodes[cid].params, ...patch } }
      }
    }
    // Numbered port, as every backend writer uses: 'in' is not a port the
    // backend knows, so the subnet would output nothing (FE-01).
    added.push({ id: newWireId(), from: feed, to: outId, from_port: 'out', to_port: 'in0' })
    for (const w of outgoing) {
      // Same wire id and consumer port; the consumer now reads the subnet.
      replaced.set(w.id, { ...w, from: subnetId, from_port: 'out' })
    }
  }
  const wires: GraphWire[] = [...graph.wires.map(w => replaced.get(w.id) ?? w), ...added]

  // ── Boxes and notes fully inside the selection move with it ────────────
  const area = { x0, y0, x1, y1 }
  const boxes: NetworkBox[] = graph.annotations.boxes.map(b => {
    if ((b.parent ?? null) !== parent) return b
    const byMembers = b.members.length > 0 && b.members.every(m => selected.has(m))
    return byMembers || inside(b.rect, area) ? { ...b, parent: subnetId } : b
  })
  const notes: StickyNote[] = graph.annotations.notes.map(n =>
    (n.parent ?? null) === parent && inside(n.rect, area) ? { ...n, parent: subnetId } : n,
  )
  const annotationsChanged = boxes.some((b, i) => b !== graph.annotations.boxes[i])
    || notes.some((n, i) => n !== graph.annotations.notes[i])

  const next: Graph = {
    ...graph,
    nodes,
    wires,
    annotations: annotationsChanged ? { boxes, notes } : graph.annotations,
  }
  return {
    graph: next,
    subnetId,
    count: members.length,
    inputs: sources.length,
    outputs: producers.length > 0 ? 1 : 0,
    merged,
    mergedFrom: merged ? producers.length : 0,
  }
}
