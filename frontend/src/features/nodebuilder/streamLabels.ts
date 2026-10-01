/**
 * Ports, streams and wire labels for the node builder (F435 W2 item 2.E,
 * specs S08, S09, S11; plan D4 and section 3).
 *
 * Pure functions, no React. Everything here works from the catalog's
 * PortsSpec and ParamSpec and never from a node type name, so node types
 * that arrive later through a regenerated catalog work without changes.
 *
 * - Ports: `portsOf` says which input handles a node draws (`in0`, `in1`,
 *   ... plus a dashed spare on dynamic nodes). `connectionProblem` is the
 *   check React Flow runs while a wire is dragged.
 * - Reads and writes: params of type `attr` / `attr_list` read attributes,
 *   params of type `write` name what a node adds to the stream.
 * - Streams: `/validate` returns each node's OUTPUT stream. A node's input
 *   stream is the union of the streams wired into it (`inputStreamOf`).
 * - Wire labels: a wire is labelled with what its consumer reads through it
 *   (`readsThroughWire`, `labelText`), placed by `placeLabels`.
 */

import type { AttrInfo, Graph, GraphNode, GraphWire, ParamValue, StreamSchema } from '../../api/nodebuilder'
import type { Diagnostic } from '../../api/nodebuilderValidate'
import { NODE_CATALOG, canWire, type NodeCatalogEntry, type ParamSpec, type PortsSpec } from './catalog'
import { primaryAttrFor } from './canvasHelpers'
import { addWire as opAddWire, removeWires as opRemoveWires, wouldCreateCycle, type NewWire } from './operations'

// ---------------------------------------------------------------------------
// Catalog lookups
// ---------------------------------------------------------------------------

const BY_NAME = new Map<string, NodeCatalogEntry>(NODE_CATALOG.map(e => [e.name, e]))

/** The catalog entry for a node type, or undefined for an unknown type. */
export function catalogEntry(nodeType: string | undefined): NodeCatalogEntry | undefined {
  return nodeType ? BY_NAME.get(nodeType) : undefined
}

const NO_SPECS: readonly ParamSpec[] = []

/** The node type's param specs in display order (empty when unknown). */
export function paramSpecsOf(nodeType: string | undefined): readonly ParamSpec[] {
  return catalogEntry(nodeType)?.params ?? NO_SPECS
}

/** True for a param that reads attributes (`attr` or `attr_list`). */
export function isReadParam(spec: Pick<ParamSpec, 'type'>): boolean {
  return spec.type === 'attr' || spec.type === 'attr_list'
}

/** The params of a node type that read attributes, in order. */
export function readParamsOf(nodeType: string | undefined): ParamSpec[] {
  return paramSpecsOf(nodeType).filter(isReadParam)
}

/** The params of a node type that name a written attribute, in order. */
export function writeParamsOf(nodeType: string | undefined): ParamSpec[] {
  return paramSpecsOf(nodeType).filter(p => p.type === 'write')
}

// ---------------------------------------------------------------------------
// Attribute names and values
// ---------------------------------------------------------------------------

/** A full attribute name with its sigil (plan 3.2). */
export const ATTR_NAME_RE = /^@[a-z_][a-z0-9_]{0,63}$/
/** The part of a name after the `@`, as typed in a write chip. */
export const ATTR_BODY_RE = /^[a-z_][a-z0-9_]{0,63}$/
/** What the attribute picker accepts as free text (the `@` is optional). */
export const ATTR_QUERY_RE = /^@?[a-z_][a-z0-9_]{0,63}$/

/** `'clo'` or `'@clo'` becomes `'@clo'`. */
export function withSigil(name: string): string {
  return name.startsWith('@') ? name : `@${name}`
}

/**
 * The names held by an `attr_list` value. The graph stores a list; a
 * comma or space separated string (older files, hand edits) is read too.
 */
export function attrListValue(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string' && v !== '')
  if (typeof value === 'string') return value.split(/[\s,]+/).filter(Boolean)
  return []
}

/** The single name held by an `attr` value, or null when unset. */
export function attrValue(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

/** Every attribute a node reads, in param order (unset params skipped). */
export function readsOf(node: Pick<GraphNode, 'type' | 'params'>): string[] {
  const out: string[] = []
  for (const spec of readParamsOf(node.type)) {
    const v = node.params?.[spec.name]
    if (spec.type === 'attr_list') out.push(...attrListValue(v))
    else {
      const one = attrValue(v)
      if (one) out.push(one)
    }
  }
  return out
}

/** One attribute a node writes. `param` is null for a fixed write (a Ticker's @close). */
export interface WriteSlot {
  param: string | null
  name: string
}

/**
 * What a node writes: one slot per `write` param (its current value, or
 * the catalog default when unset), else the catalog's fixed writes.
 */
export function writesOf(node: Pick<GraphNode, 'type' | 'params'>): WriteSlot[] {
  const specs = writeParamsOf(node.type)
  if (specs.length > 0) {
    return specs.flatMap(spec => {
      const v = node.params?.[spec.name] ?? spec.default
      return typeof v === 'string' && v !== '' ? [{ param: spec.name, name: v }] : []
    })
  }
  return (catalogEntry(node.type)?.writes ?? []).map(name => ({ param: null, name }))
}

/**
 * The attribute a fresh wire out of this node is read as by default (plan
 * D4 "default reads"): the node's first `write` param. A node without
 * `write` params falls back to the catalog rule (`@close` for a Ticker).
 */
export function primaryWriteOf(node: Pick<GraphNode, 'type' | 'params'> | undefined): string | null {
  if (!node) return null
  if (writeParamsOf(node.type).length > 0) return writesOf(node)[0]?.name ?? null
  return primaryAttrFor(node.type)
}

// ---------------------------------------------------------------------------
// Ports (spec S08)
// ---------------------------------------------------------------------------

/** One drawn input port. */
export interface PortView {
  /** Handle id, `in<k>`; it equals `GraphWire.to_port`. */
  id: string
  index: number
  label: string
  optional: boolean
  connected: boolean
  /** The dashed extra port of a dynamic node. */
  spare: boolean
}

/** `'in3'` gives 3; anything else gives -1. */
export function portIndex(portId: string | null | undefined): number {
  const m = portId ? /^in(\d+)$/.exec(portId) : null
  return m ? Number(m[1]) : -1
}

/** The ports spec of a node type, or null when the type is unknown. */
export function portsSpecOf(nodeType: string | undefined): PortsSpec | null {
  const entry = catalogEntry(nodeType)
  if (!entry) return null
  if (entry.inputs) return entry.inputs
  // A small hand-built entry with no spec: one plain input unless it takes none.
  const ins = entry.defaults?.ins ?? 1
  return {
    ports: Array.from({ length: ins }, (_, k) => ({ label: `in${k}` })),
    dynamic: false,
    min: Math.min(ins, 1),
    max: ins,
  }
}

/**
 * The input ports a node draws, left to right.
 *
 * - Fixed nodes draw every port in their spec (`a` and `b` of a comparison).
 * - Dynamic nodes (logic, merge) draw the ports in use, at least `min`,
 *   plus one dashed spare until `max` is reached. Disconnecting the last
 *   wire shrinks them back to `min`.
 * - An unknown type draws the ports its wires use (at least one).
 * - A port a wire uses is always drawn, even past the spec, so the wire
 *   stays visible and can be removed.
 */
export function portsOf(nodeType: string | undefined, connectedPorts: Iterable<string>): PortView[] {
  const connected = new Set<string>()
  let highest = -1
  for (const p of connectedPorts) {
    const k = portIndex(p)
    if (k < 0) continue
    connected.add(`in${k}`)
    highest = Math.max(highest, k)
  }
  const spec = catalogEntry(nodeType) ? portsSpecOf(nodeType) : null
  let count: number
  let spare = -1
  if (!spec) {
    count = Math.max(highest + 1, 1)
  } else if (spec.dynamic) {
    count = Math.max(spec.min, highest + 1)
    if (count < spec.max) spare = count
  } else {
    count = Math.max(spec.ports.length, spec.min, highest + 1)
  }
  const out: PortView[] = []
  const total = spare >= 0 ? count + 1 : count
  for (let k = 0; k < total; k++) {
    const p = spec?.ports[k]
    const id = `in${k}`
    out.push({
      id,
      index: k,
      label: p?.label ?? id,
      optional: p?.optional ?? (spec ? k >= spec.min : false),
      connected: connected.has(id),
      spare: k === spare,
    })
  }
  return out
}

/** Where a port sits along the top edge, as a fraction of the node width. */
export function portFraction(index: number, count: number): number {
  return Math.round(((index + 1) / (count + 1)) * 2000) / 2000
}

/** The CSS `left` of a port: `portFraction` as a percent, without float noise. */
export function portLeft(index: number, count: number): string {
  return `${Math.round(portFraction(index, count) * 100000) / 1000}%`
}

/** What React Flow hands to `isValidConnection` and `onConnect`. */
export interface ConnectionLike {
  source: string | null
  target: string | null
  sourceHandle?: string | null
  targetHandle?: string | null
}

/**
 * Why a wire may not be dropped here, or null when it may (spec S08).
 * Refused: a missing end, the same node at both ends, two outputs or two
 * inputs, a node with no port on that side, a port that already has a
 * wire, a port past a node's last allowed port, and a wire that would
 * close a cycle.
 */
export function connectionProblem(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  c: ConnectionLike,
): 'missing' | 'self' | 'direction' | 'no_port' | 'full' | 'cycle' | null {
  if (!c.source || !c.target) return 'missing'
  if (c.source === c.target) return 'self'
  const from = graph.nodes[c.source]
  const to = graph.nodes[c.target]
  if (!from || !to) return 'missing'
  if (c.sourceHandle != null && c.sourceHandle !== 'out') return 'direction'
  if (c.targetHandle != null && portIndex(c.targetHandle) < 0) return 'direction'
  if (!canWire(from.type, to.type)) return 'no_port'
  if (c.targetHandle != null) {
    const k = portIndex(c.targetHandle)
    const spec = portsSpecOf(to.type)
    if (spec) {
      const limit = spec.dynamic ? spec.max : Math.max(spec.ports.length, spec.max)
      if (k >= limit) return 'no_port'
    }
    if (graph.wires.some(w => w.to === c.target && w.to_port === c.targetHandle)) return 'full'
  }
  if (wouldCreateCycle(graph as Graph, c.source, c.target)) return 'cycle'
  return null
}

// ---------------------------------------------------------------------------
// Default reads and connecting (spec S09 "Default reads", plan D4)
// ---------------------------------------------------------------------------

/**
 * The read param a wire on `portId` feeds: the param named like the port
 * label (`a`, `b`, `source`), else the list param of a dynamic node, else
 * the k-th single read param. Null when the node reads nothing by param.
 */
export function readParamForPort(nodeType: string | undefined, portId: string): ParamSpec | null {
  const reads = readParamsOf(nodeType)
  if (reads.length === 0) return null
  const k = portIndex(portId)
  const spec = portsSpecOf(nodeType)
  const label = spec?.ports[k]?.label
  const byLabel = label ? reads.find(p => p.name === label) : undefined
  if (byLabel) return byLabel
  const list = reads.find(p => p.type === 'attr_list')
  if (spec?.dynamic && list) return list
  const singles = reads.filter(p => p.type === 'attr')
  return (k >= 0 ? singles[k] : undefined) ?? list ?? null
}

/**
 * The param change a new wire makes on its consumer: an empty `attr`
 * param on that port takes the source's primary write; a list param gets
 * it added. Null when nothing changes.
 */
export function defaultReadPatch(
  graph: Pick<Graph, 'nodes'>,
  wire: Pick<GraphWire, 'from' | 'to' | 'to_port'>,
): Record<string, ParamValue> | null {
  const consumer = graph.nodes[wire.to]
  if (!consumer) return null
  const spec = readParamForPort(consumer.type, wire.to_port)
  if (!spec) return null
  const name = primaryWriteOf(graph.nodes[wire.from])
  if (!name) return null
  const current = consumer.params?.[spec.name]
  if (spec.type === 'attr_list') {
    const list = attrListValue(current)
    return list.includes(name) ? null : { [spec.name]: [...list, name] }
  }
  return attrValue(current) ? null : { [spec.name]: name }
}

/**
 * True when the consumer's catalog entry has no param specs at all (a small
 * hand-built or older catalog entry). Only such a consumer, in a v2 graph,
 * still uses the v2 wire `attr` label. A node that has params but reads none
 * (time_of_day, constant, merge) reads nothing through its wires.
 */
function usesLegacyWireAttr(nodeType: string | undefined): boolean {
  return catalogEntry(nodeType)?.params === undefined
}

/**
 * Add a wire and fill the consumer's default read in one graph change (so
 * one undo step). When the consumer reads by param, the wire carries no
 * `attr` (the v3 shape: the param is the operand). A v3 graph never gets a
 * wire `attr` (plan 4.1: v1/v2 only). In an older (v2) graph only a consumer
 * with no param specs keeps the v2 wire label.
 * Throws like `addWire` (cycle, no port), and when `to_port` is given and
 * that port already has a wire (spec S08: one wire per input port).
 */
export function connectWire(graph: Graph, wire: NewWire): Graph {
  if (wire.to_port != null && graph.wires.some(w => w.to === wire.to && w.to_port === wire.to_port)) {
    throw new Error(`Cannot add wire: port ${wire.to_port} of "${wire.to}" already has a wire.`)
  }
  const consumerType = graph.nodes[wire.to]?.type
  const readsByParam = readParamsOf(consumerType).length > 0
  const { attr, ...rest } = wire
  const toAdd: NewWire = readsByParam || graph._version >= 3 || !usesLegacyWireAttr(consumerType)
    ? rest
    : { ...rest, attr: attr ?? primaryAttrFor(graph.nodes[wire.from]?.type) }
  const next = opAddWire(graph, toAdd)
  if (!readsByParam) return next
  const added = next.wires[next.wires.length - 1]
  const patch = defaultReadPatch(next, added)
  if (!patch) return next
  const node = next.nodes[wire.to]
  return {
    ...next,
    nodes: { ...next.nodes, [wire.to]: { ...node, params: { ...node.params, ...patch } } },
  }
}

/**
 * Remove wires and undo what `connectWire` added for them, in one graph
 * change. A wire into an `attr_list` param (the terms of AND / OR / XOR)
 * appended its source's primary write to the list; when that name is no
 * longer provided by any wire still coming into the consumer, it is taken
 * out again, so the node does not turn into an `attr_missing` error.
 * Single `attr` params are left as they are (a missing read then shows as
 * missing on its chip, as after delete-and-rewire, spec S23).
 */
export function removeWiresWithTerms(graph: Graph, wireIds: readonly string[]): Graph {
  if (wireIds.length === 0) return graph
  const doomed = new Set(wireIds)
  const removed = graph.wires.filter(w => doomed.has(w.id))
  const next = opRemoveWires(graph, wireIds)
  const memo = new Map<string, Set<string>>()
  let nodes = next.nodes
  for (const w of removed) {
    const consumer = nodes[w.to]
    if (!consumer) continue
    const spec = readParamForPort(consumer.type, w.to_port)
    if (!spec || spec.type !== 'attr_list') continue
    const name = primaryWriteOf(graph.nodes[w.from])
    if (!name) continue
    const list = attrListValue(consumer.params?.[spec.name])
    if (!list.includes(name)) continue
    const stillProvided = next.wires.some(r => r.to === w.to && staticOutputNames(next, r.from, memo).has(name))
    if (stillProvided) continue
    nodes = {
      ...nodes,
      [w.to]: { ...consumer, params: { ...consumer.params, [spec.name]: list.filter(n => n !== name) } },
    }
  }
  return nodes === next.nodes ? next : { ...next, nodes }
}

// ---------------------------------------------------------------------------
// Unique write names for a new node (plan D4 "Writing")
// ---------------------------------------------------------------------------

/** Every attribute name some node in the graph writes. */
export function writtenNames(graph: Pick<Graph, 'nodes'>): Set<string> {
  const out = new Set<string>()
  for (const n of Object.values(graph.nodes)) for (const w of writesOf(n)) out.add(w.name)
  return out
}

/** `@rsi` when free, else `@rsi_2`, `@rsi_3`, ... (kept within 64 characters). */
export function uniqueAttrName(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) return base
  const body = base.replace(/^@/, '').replace(/_\d+$/, '')
  for (let k = 2; ; k++) {
    const suffix = `_${k}`
    const name = `@${body.slice(0, 64 - suffix.length)}${suffix}`
    if (!taken.has(name)) return name
  }
}

/**
 * The params for a new node of `nodeType`, with every `write` param made
 * unique in the graph, so two RSI nodes write `@rsi` and `@rsi_2`.
 */
export function withUniqueWrites(
  graph: Pick<Graph, 'nodes'>,
  nodeType: string,
  params: Record<string, ParamValue>,
): Record<string, ParamValue> {
  const specs = writeParamsOf(nodeType)
  if (specs.length === 0) return params
  const taken = writtenNames(graph)
  const out = { ...params }
  for (const spec of specs) {
    const v = out[spec.name] ?? spec.default
    if (typeof v !== 'string' || !ATTR_NAME_RE.test(v)) continue
    const name = uniqueAttrName(v, taken)
    out[spec.name] = name
    taken.add(name)
  }
  return out
}

// ---------------------------------------------------------------------------
// Streams (spec S09 data, S11 popover)
// ---------------------------------------------------------------------------

/** An attribute on a node's input, with the port it arrives on. */
export interface InputAttr extends AttrInfo {
  /** True for a detail (one value per cook) attribute. */
  detail: boolean
  port: string
}

export interface InputStream {
  attrs: InputAttr[]
  /** At least one wire comes in. */
  wired: boolean
  /** Every upstream node has a stream from /validate (so a missing name is really missing). */
  known: boolean
}

/** The wires into a node, in port order. */
export function wiresInto(wires: readonly GraphWire[], nodeId: string): GraphWire[] {
  return wires
    .filter(w => w.to === nodeId)
    .sort((a, b) => portIndex(a.to_port) - portIndex(b.to_port))
}

/**
 * A node's input stream: the union of the output streams wired into it, in
 * port order (plan D4). The same name from the same writer on two ports is
 * listed once (a diamond from one Ticker is fine); the same name from two
 * writers is listed twice, so the clash is visible.
 */
export function inputStreamOf(
  nodeId: string,
  streams: Record<string, StreamSchema>,
  wires: readonly GraphWire[],
): InputStream {
  const into = wiresInto(wires, nodeId)
  const seen = new Set<string>()
  const attrs: InputAttr[] = []
  let known = true
  for (const w of into) {
    const s = streams[w.from]
    if (!s) { known = false; continue }
    const add = (a: AttrInfo, detail: boolean) => {
      const key = `${a.name}\u0000${a.written_by ?? ''}`
      if (seen.has(key)) return
      seen.add(key)
      attrs.push({ ...a, detail, port: w.to_port })
    }
    for (const a of s.points) add(a, false)
    for (const a of s.detail) add(a, true)
  }
  return { attrs, wired: into.length > 0, known: into.length > 0 && known }
}

/** Every name on a stream (points and detail). */
export function streamNames(s: StreamSchema): Set<string> {
  const out = new Set<string>()
  for (const a of s.points) out.add(a.name)
  for (const a of s.detail) out.add(a.name)
  return out
}

/**
 * A best guess at a node's output names before /validate has answered (or
 * while its answer is for an older graph): what it writes plus everything
 * that flows into it. Wire labels and the picker's missing check fall back
 * on it; the picker list and the popover use the server's streams.
 */
export function staticOutputNames(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  nodeId: string,
  memo: Map<string, Set<string>> = new Map(),
  visiting: Set<string> = new Set(),
): Set<string> {
  const hit = memo.get(nodeId)
  if (hit) return hit
  const out = new Set<string>()
  if (visiting.has(nodeId)) return out
  visiting.add(nodeId)
  for (const w of graph.wires) {
    if (w.to === nodeId && w.from in graph.nodes) {
      for (const n of staticOutputNames(graph, w.from, memo, visiting)) out.add(n)
    }
  }
  const node = graph.nodes[nodeId]
  if (node) for (const s of writesOf(node)) out.add(s.name)
  visiting.delete(nodeId)
  memo.set(nodeId, out)
  return out
}

/** The static guess at a node's INPUT names: the union of `staticOutputNames` of every node wired into it. */
export function staticInputNames(graph: Pick<Graph, 'nodes' | 'wires'>, nodeId: string): Set<string> {
  const memo = new Map<string, Set<string>>()
  const out = new Set<string>()
  for (const w of graph.wires) {
    if (w.to !== nodeId || !(w.from in graph.nodes)) continue
    for (const n of staticOutputNames(graph, w.from, memo)) out.add(n)
  }
  return out
}

// ---------------------------------------------------------------------------
// Wire labels (spec S11, foundation 5.2)
// ---------------------------------------------------------------------------

/** Label shown on a wire whose consumer reads nothing yet. */
export const PLACEHOLDER_LABEL = 'stream'

/**
 * What the consumer of `wire` reads through it, in the consumer's param
 * order: its read values that are present on the source's output stream.
 * A consumer whose catalog entry has no param specs (an older catalog) falls
 * back to the v2 `wire.attr`; one with params but no reads reads nothing.
 *
 * `fresh` says the streams belong to the graph on screen. While they are
 * from an older graph (a rename or edit since the last /validate), the
 * static guess is added to them, so a renamed write still labels its wire
 * on the same render instead of flashing the `stream` placeholder.
 */
export function readsThroughWire(
  wire: Pick<GraphWire, 'from' | 'to' | 'attr'>,
  graph: Pick<Graph, 'nodes' | 'wires'>,
  streams: Record<string, StreamSchema>,
  memo?: Map<string, Set<string>>,
  fresh = true,
): string[] {
  const consumer = graph.nodes[wire.to]
  if (!consumer) return []
  if (readParamsOf(consumer.type).length === 0) {
    return usesLegacyWireAttr(consumer.type) && wire.attr ? [wire.attr] : []
  }
  const s = streams[wire.from]
  let available: Set<string>
  if (!s) available = staticOutputNames(graph, wire.from, memo)
  else if (fresh) available = streamNames(s)
  else available = new Set([...streamNames(s), ...staticOutputNames(graph, wire.from, memo)])
  const out: string[] = []
  for (const r of readsOf(consumer)) if (available.has(r) && !out.includes(r)) out.push(r)
  return out
}

/** `@rsi`; `@a, @b`; `@a, @b +3`; '' when nothing is read. */
export function labelText(reads: readonly string[]): string {
  if (reads.length <= 2) return reads.join(', ')
  return `${reads[0]}, ${reads[1]} +${reads.length - 2}`
}

/** What a wire's label shows: its reads, or the dim `stream` placeholder. */
export interface WireLabel {
  reads: string[]
  /** The drawn text (`stream` when nothing is read). */
  text: string
  placeholder: boolean
}

/** The label of every wire in the graph, keyed by wire id (`fresh`: see `readsThroughWire`). */
export function wireLabels(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  streams: Record<string, StreamSchema>,
  fresh = true,
): Record<string, WireLabel> {
  const memo = new Map<string, Set<string>>()
  const out: Record<string, WireLabel> = {}
  for (const w of graph.wires) {
    const reads = readsThroughWire(w, graph, streams, memo, fresh)
    const text = labelText(reads)
    out[w.id] = text ? { reads, text, placeholder: false } : { reads, text: PLACEHOLDER_LABEL, placeholder: true }
  }
  return out
}

/** The ids of the input ports that have a wire, per node. */
export function connectedPortsByNode(wires: readonly GraphWire[]): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const w of wires) {
    const list = out.get(w.to)
    if (list) list.push(w.to_port)
    else out.set(w.to, [w.to_port])
  }
  return out
}

/**
 * The hover text of an input port (spec S08): `in1 · @spread_z` (the port
 * label and the first attribute the node reads through it), or the plain
 * label when nothing flows in on it.
 */
export function inputPortTitle(label: string, reads: readonly string[]): string {
  return reads.length > 0 ? `${label} · ${reads[0]}` : label
}

/** The hover text of the output port: `out · +@rsi +@rsi_slope`. */
export function outputPortTitle(writes: readonly WriteSlot[]): string {
  return writes.length > 0 ? `out · ${writes.map(w => `+${w.name}`).join(' ')}` : 'out'
}

// ---------------------------------------------------------------------------
// Wire geometry (foundation 5)
// ---------------------------------------------------------------------------

/** Control distance: max(40, 0.45 |dy|), or 120 for a wire that runs upward. */
export function wireControl(sy: number, ty: number): number {
  return ty < sy ? 120 : Math.max(40, 0.45 * Math.abs(ty - sy))
}

/** SVG path of a wire: straight down out of the output, straight into the input. */
export function wirePath(sx: number, sy: number, tx: number, ty: number): string {
  const c = wireControl(sy, ty)
  return `M${sx},${sy} C${sx},${sy + c} ${tx},${ty - c} ${tx},${ty}`
}

/** The point at `t` (0..1) along the wire path. */
export function pointOnWire(sx: number, sy: number, tx: number, ty: number, t: number): { x: number; y: number } {
  const c = wireControl(sy, ty)
  const u = 1 - t
  const a = u * u * u
  const b = 3 * u * u * t
  const d = 3 * u * t * t
  const e = t * t * t
  return {
    x: a * sx + b * sx + d * tx + e * tx,
    y: a * sy + b * (sy + c) + d * (ty - c) + e * ty,
  }
}

// ---------------------------------------------------------------------------
// Label placement (foundation 5.2)
// ---------------------------------------------------------------------------

export interface Rect { x: number; y: number; w: number; h: number }

/** One wire to place a label for. */
export interface LabelWire {
  id: string
  from: string
  to: string
  toPort: string
  text: string
}

export interface LabelPlacement {
  /** Position along the path, 0..1. */
  t: number
  /** Horizontal nudge in px. */
  dx: number
  /**
   * Why the label is not drawn at rest: 'dup' (a fan-out sibling shows the
   * same text) or 'overlap' (no free spot). Hover and selection still show it.
   */
  hidden: 'dup' | 'overlap' | null
}

const DEFAULT_RECT: Rect = { x: 0, y: 0, w: 176, h: 60 }
/** Rough label box: mono 10px is about 6px per character. */
const CHAR_W = 6.1
const LABEL_H = 12

function boxesOverlap(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

/**
 * Where each wire's label goes.
 *
 * - Fan-out (several wires leave one output), sorted by target x: when every
 *   text is the same, only the leftmost wire shows it (at t = 0.5). When they
 *   differ, label i of n sits at t = 0.38 + 0.24 i / max(1, n - 1), nudged
 *   by (i - (n - 1) / 2) * 10 px.
 * - Fan-in (3 or more wires into a dynamic node): t alternates 0.45 / 0.6.
 * - Then one overlap pass: a label that hits a label already placed, or a
 *   node card, moves along by +0.08 at most twice, then hides.
 *
 * `rects` are node boxes in graph units; `portCounts` the number of input
 * ports each node draws (for the target point). Missing values fall back
 * to defaults, which only make the overlap pass rougher.
 */
export function placeLabels(
  wires: readonly LabelWire[],
  rects: Record<string, Rect>,
  portCounts: Record<string, number> = {},
  dynamicNodes: ReadonlySet<string> = new Set(),
): Record<string, LabelPlacement> {
  const out: Record<string, LabelPlacement> = {}
  const rectOf = (id: string) => rects[id] ?? DEFAULT_RECT
  const ends = (w: LabelWire) => {
    const s = rectOf(w.from)
    const t = rectOf(w.to)
    const n = Math.max(1, portCounts[w.to] ?? 1)
    const k = Math.max(0, portIndex(w.toPort))
    return {
      sx: s.x + s.w / 2,
      sy: s.y + s.h,
      tx: t.x + t.w * ((k + 1) / (n + 1)),
      ty: t.y,
    }
  }

  // Fan-out groups.
  const byFrom = new Map<string, LabelWire[]>()
  for (const w of wires) {
    const list = byFrom.get(w.from)
    if (list) list.push(w)
    else byFrom.set(w.from, [w])
  }
  for (const group of byFrom.values()) {
    if (group.length < 2) continue
    const sorted = [...group].sort((a, b) => ends(a).tx - ends(b).tx || a.id.localeCompare(b.id))
    const same = sorted.every(w => w.text === sorted[0].text)
    const n = sorted.length
    sorted.forEach((w, i) => {
      if (same) {
        out[w.id] = { t: 0.5, dx: 0, hidden: i === 0 ? null : 'dup' }
      } else {
        out[w.id] = {
          t: Math.round((0.38 + (0.24 * i) / Math.max(1, n - 1)) * 1000) / 1000,
          dx: (i - (n - 1) / 2) * 10,
          hidden: null,
        }
      }
    })
  }

  // Fan-in at dynamic nodes with 3 or more inputs, for wires not already
  // spread out by a fan-out.
  const byTo = new Map<string, LabelWire[]>()
  for (const w of wires) {
    if (!dynamicNodes.has(w.to)) continue
    const list = byTo.get(w.to)
    if (list) list.push(w)
    else byTo.set(w.to, [w])
  }
  for (const group of byTo.values()) {
    if (group.length < 3) continue
    const sorted = [...group].sort((a, b) => portIndex(a.toPort) - portIndex(b.toPort))
    sorted.forEach((w, i) => {
      const prev = out[w.id]
      if (prev && (prev.hidden || prev.t !== 0.5 || prev.dx !== 0)) return
      out[w.id] = { t: i % 2 === 0 ? 0.45 : 0.6, dx: 0, hidden: null }
    })
  }

  for (const w of wires) out[w.id] ??= { t: 0.5, dx: 0, hidden: null }

  // Overlap pass.
  const placed: Rect[] = []
  const nodeBoxes = Object.values(rects)
  for (const w of wires) {
    const p = out[w.id]
    if (p.hidden || !w.text) continue
    const e = ends(w)
    const width = w.text.length * CHAR_W + 6
    let t = p.t
    let ok = false
    for (let attempt = 0; attempt < 3; attempt++) {
      const pt = pointOnWire(e.sx, e.sy, e.tx, e.ty, t)
      const box = { x: pt.x + p.dx - width / 2, y: pt.y - LABEL_H / 2, w: width, h: LABEL_H }
      const hit = placed.some(b => boxesOverlap(b, box)) || nodeBoxes.some(b => boxesOverlap(b, box))
      if (!hit) {
        placed.push(box)
        ok = true
        break
      }
      if (attempt < 2) t = Math.min(0.95, Math.round((t + 0.08) * 1000) / 1000)
    }
    out[w.id] = ok ? { ...p, t } : { ...p, hidden: 'overlap' }
  }
  return out
}

// ---------------------------------------------------------------------------
// Wire diagnostics (spec S05 / S11)
// ---------------------------------------------------------------------------

/** Codes that are about what flows on a wire. */
const WIRE_STREAM_CODES: ReadonlySet<string> = new Set(['attr_missing', 'attr_clash', 'attr_type'])

/**
 * The wire a diagnostic is about, or null. A diagnostic with a `port`
 * names the wire into that port of its node. A `dangling_wire` names the
 * wire at its known end whose other end is gone.
 */
export function wireIdForDiagnostic(
  d: Pick<Diagnostic, 'node_id' | 'port' | 'code'>,
  graph: Pick<Graph, 'nodes' | 'wires'> | null | undefined,
): string | null {
  if (!graph || !d.node_id) return null
  if (d.port) {
    const w = graph.wires.find(x => x.to === d.node_id && x.to_port === d.port)
    if (w) return w.id
  }
  if (d.code === 'dangling_wire') {
    const w = graph.wires.find(x =>
      (x.from === d.node_id && !(x.to in graph.nodes)) || (x.to === d.node_id && !(x.from in graph.nodes)))
    if (w) return w.id
  }
  return null
}

/**
 * The diagnostic that marks each wire (the S11 red stroke), first one wins.
 * Besides wires named by `wireIdForDiagnostic`, a stream problem
 * (`attr_missing`, `attr_clash`, `attr_type`) with no port marks a wire into
 * its node: the one wire whose port feeds the diagnostic's `param` (an
 * explicitly set read param comes with `param` but no `port`), else the only
 * wire into the node, when there is exactly one.
 */
export function wireDiagnostics(
  diagnostics: readonly Diagnostic[],
  graph: Pick<Graph, 'nodes' | 'wires'> | null | undefined,
): Map<string, Diagnostic> {
  const out = new Map<string, Diagnostic>()
  if (!graph) return out
  for (const d of diagnostics) {
    let id = wireIdForDiagnostic(d, graph)
    if (!id && !d.port && d.node_id && WIRE_STREAM_CODES.has(d.code)) {
      const into = graph.wires.filter(w => w.to === d.node_id)
      const type = graph.nodes[d.node_id]?.type
      const byParam = d.param ? into.filter(w => readParamForPort(type, w.to_port)?.name === d.param) : []
      if (byParam.length === 1) id = byParam[0].id
      else if (into.length === 1) id = into[0].id
    }
    if (id && !out.has(id)) out.set(id, d)
  }
  return out
}

/**
 * The `attr_clash` a write takes part in, or null (spec S10 clash state).
 * The server reports a clash on the READING node, against its read param.
 * The write `name` of `writerId` is in that clash when the reader's param
 * holds `name` and `writerId` is upstream of the reader.
 */
export function writeClashFor(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  clashes: readonly Diagnostic[],
  writerId: string,
  name: string,
): Diagnostic | null {
  for (const d of clashes) {
    if (d.code !== 'attr_clash' || !d.node_id || !d.param || d.node_id === writerId) continue
    const reader = graph.nodes[d.node_id]
    if (!reader) continue
    const spec = readParamsOf(reader.type).find(p => p.name === d.param)
    const raw = reader.params?.[d.param]
    const held = spec?.type === 'attr_list' ? attrListValue(raw) : [attrValue(raw)]
    if (!held.includes(name)) continue
    if (isUpstream(graph, writerId, d.node_id)) return d
  }
  return null
}

/** True when `from` reaches `to` by following wires downstream. */
function isUpstream(graph: Pick<Graph, 'wires'>, from: string, to: string): boolean {
  const seen = new Set<string>()
  const stack = [to]
  while (stack.length > 0) {
    const cur = stack.pop()!
    if (seen.has(cur)) continue
    seen.add(cur)
    for (const w of graph.wires) {
      if (w.to !== cur) continue
      if (w.from === from) return true
      stack.push(w.from)
    }
  }
  return false
}
