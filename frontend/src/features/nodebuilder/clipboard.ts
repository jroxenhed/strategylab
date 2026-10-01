/**
 * Copy, paste and duplicate for the node builder (F435 W3 item 3.C,
 * foundation 6.1 and 6.2).
 *
 * Pure functions, no React and no store:
 *
 * - `copyFromGraph` takes the selected nodes, boxes and notes and returns a
 *   self-contained payload: deep copies, plus only the wires whose two ends
 *   were both copied (the "internal" wires). A box brings its member nodes,
 *   a network node brings the nodes inside it.
 * - `pastePayload` puts a payload into a graph: every node, wire, box and
 *   note gets a new id; node names are made unique among their new
 *   siblings (rsi -> rsi1, the paths.ts rule); every written attribute is
 *   made unique in the graph (@rsi -> @rsi_2), and the pasted nodes that
 *   read it through a pasted wire are pointed at the new name. Reads of
 *   attributes from outside the payload (@close) are left alone.
 * - `swapIdsForDrag` is the Alt-drag trick: React Flow keeps dragging the
 *   node ids it grabbed, so the originals move to new ids where they stand
 *   and the grabbed ids become the copy (see the function).
 *
 * The store (store/clipboard.ts) wraps each of these in one `commit`, so a
 * paste is one undo step.
 */

import type { Graph, GraphNode, GraphWire, NetworkBox, ParamValue, StickyNote } from '../../api/nodebuilder'
import { _genId, newNodeId, newWireId, ReadOnlyGraphError } from './operations'
import { isValidName, nodePath, rewritePathRefs, sanitizeName, siblingNames, uniqueName } from './paths'
import {
  ATTR_NAME_RE,
  attrListValue,
  readParamsOf,
  uniqueAttrName,
  writeParamsOf,
  writtenNames,
} from './streamLabels'

/** What Copy holds: everything needed to paste, with the ids it had when copied. */
export interface ClipboardPayload {
  /** Copied nodes, deep copies, in graph order. */
  nodes: GraphNode[]
  /** Wires whose two ends are both in `nodes`. */
  wires: GraphWire[]
  /** Copied boxes; their `members` are all in `nodes`. */
  boxes: NetworkBox[]
  /** Copied sticky notes. */
  notes: StickyNote[]
  /** Top-left corner of everything copied, in flow units. Paste puts it at the cursor. */
  origin: [number, number]
}

/** What to copy: graph node ids and annotation (box, note) ids. */
export interface CopyRequest {
  nodeIds?: readonly string[]
  annotationIds?: readonly string[]
}

/** How to place a paste. */
export interface PasteOptions {
  /** Put the payload's top-left corner here (paste at the cursor). */
  at?: { x: number; y: number }
  /** Or move every item by this much (duplicate). Ignored when `at` is given. */
  offset?: [number, number]
  /**
   * The network the pasted items go into (null = root). Leave it undefined
   * to keep each item's own parent (duplicate in place). A parent that is
   * itself pasted always becomes the pasted copy.
   */
  parent?: string | null
  /** The id a copied node gets; return undefined for a fresh one. Used by Alt-drag. */
  nodeId?(oldId: string): string | undefined
}

/** What a paste made, for selecting it afterwards. */
export interface PasteResult {
  graph: Graph
  /** New node ids, in payload order. */
  nodeIds: string[]
  /** New wire ids. */
  wireIds: string[]
  /** New box and note ids. */
  annotationIds: string[]
  /** Old node id to new node id. */
  idMap: Map<string, string>
}

/** A JSON deep copy (graphs are plain JSON). */
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

/** True when the payload holds nothing to paste. */
export function isEmptyPayload(p: ClipboardPayload | null | undefined): boolean {
  return !p || (p.nodes.length === 0 && p.boxes.length === 0 && p.notes.length === 0)
}

/** "3 nodes", "1 node and 1 note": for flashes and tooltips. */
export function describePayload(p: ClipboardPayload): string {
  const parts: string[] = []
  const add = (n: number, one: string, many: string) => { if (n > 0) parts.push(`${n} ${n === 1 ? one : many}`) }
  add(p.nodes.length, 'node', 'nodes')
  add(p.boxes.length, 'box', 'boxes')
  add(p.notes.length, 'note', 'notes')
  return parts.join(' and ') || 'nothing'
}

// ---------------------------------------------------------------------------
// Copy
// ---------------------------------------------------------------------------

/**
 * The payload for the selected items of `graph`, or null when nothing in the
 * request exists. A selected box brings its members; a copied network node
 * brings every node inside it, however deep.
 */
export function copyFromGraph(graph: Graph, req: CopyRequest): ClipboardPayload | null {
  const annotationIds = new Set(req.annotationIds ?? [])
  const boxes = (graph.annotations?.boxes ?? []).filter(b => annotationIds.has(b.id))
  const notes = (graph.annotations?.notes ?? []).filter(n => annotationIds.has(n.id))

  const wanted = new Set<string>()
  for (const id of req.nodeIds ?? []) if (id in graph.nodes) wanted.add(id)
  for (const b of boxes) for (const id of b.members) if (id in graph.nodes) wanted.add(id)

  // Nodes inside a copied network come along (W5 and W6 networks).
  const inside = (id: string): boolean => {
    const seen = new Set<string>()
    let p = graph.nodes[id]?.parent ?? null
    while (p !== null && !seen.has(p)) {
      if (wanted.has(p)) return true
      seen.add(p)
      p = graph.nodes[p]?.parent ?? null
    }
    return false
  }
  const ids = Object.keys(graph.nodes).filter(id => wanted.has(id) || inside(id))
  const idSet = new Set(ids)
  if (ids.length === 0 && boxes.length === 0 && notes.length === 0) return null

  const nodes = ids.map(id => clone(graph.nodes[id]))
  const wires = graph.wires.filter(w => idSet.has(w.from) && idSet.has(w.to)).map(w => clone(w))
  const copiedBoxes = boxes.map(b => ({ ...clone(b), members: b.members.filter(m => idSet.has(m)) }))
  const copiedNotes = notes.map(n => clone(n))

  // Only top-level items set the corner: nodes inside a copied network sit
  // inside its frame (positions are absolute, EA-1, but the network's own
  // spot is what the user sees as the group's corner).
  let x = Infinity
  let y = Infinity
  for (const n of nodes) {
    if (n.parent !== null && idSet.has(n.parent)) continue
    x = Math.min(x, n.position[0])
    y = Math.min(y, n.position[1])
  }
  for (const a of [...copiedBoxes, ...copiedNotes]) {
    x = Math.min(x, a.rect[0])
    y = Math.min(y, a.rect[1])
  }
  const origin: [number, number] = [Number.isFinite(x) ? x : 0, Number.isFinite(y) ? y : 0]

  return { nodes, wires, boxes: copiedBoxes, notes: copiedNotes, origin }
}

// ---------------------------------------------------------------------------
// Paste
// ---------------------------------------------------------------------------

/** Every id already used by a node, a box or a note (React Flow shares one id space). */
function usedIds(graph: Graph): Set<string> {
  return new Set([
    ...Object.keys(graph.nodes),
    ...(graph.annotations?.boxes ?? []).map(b => b.id),
    ...(graph.annotations?.notes ?? []).map(n => n.id),
  ])
}

/** A fresh id from `make` that is not in `used` (and is then marked used). */
function freshId(used: Set<string>, make: () => string): string {
  let id = make()
  while (used.has(id)) id = make()
  used.add(id)
  return id
}

/** `<prefix>_` plus 8 lower-case base-36 characters, like node and wire ids. */
function annotationId(prefix: string): string {
  const chars = _genId().toLowerCase().replace(/[^a-z0-9]/g, '')
  return `${prefix}_${chars.slice(-8).padStart(8, '0')}`
}

/** The written attribute names of a node, per write param (default when unset). */
function writesByParam(node: GraphNode): Array<{ param: string; name: string }> {
  const out: Array<{ param: string; name: string }> = []
  for (const spec of writeParamsOf(node.type)) {
    const v = node.params?.[spec.name] ?? spec.default
    if (typeof v === 'string' && ATTR_NAME_RE.test(v)) out.push({ param: spec.name, name: v })
  }
  return out
}

/**
 * For each payload node, the attribute renames its reads should follow:
 * a read of `@x` is renamed when the nearest payload node upstream of it
 * (along payload wires) that writes `@x` got a new name for it. That is the
 * same rule as renameAttr: readers below a node that writes the name again
 * read that node's attribute.
 */
function readRenames(
  nodes: readonly GraphNode[],
  wires: readonly GraphWire[],
  writeRenames: Map<string, Map<string, string>>,
): Map<string, Map<string, string>> {
  const byId = new Map(nodes.map(n => [n.id, n]))
  const writes = new Map(nodes.map(n => [n.id, new Set(writesByParam(n).map(w => w.name))]))
  const upstream = new Map<string, string[]>()
  for (const w of wires) {
    if (!upstream.has(w.to)) upstream.set(w.to, [])
    upstream.get(w.to)!.push(w.from)
  }
  const out = new Map<string, Map<string, string>>()
  for (const reader of nodes) {
    const names = new Set<string>()
    for (const spec of readParamsOf(reader.type)) {
      const v = reader.params?.[spec.name]
      for (const n of spec.type === 'attr_list' ? attrListValue(v) : typeof v === 'string' ? [v] : []) names.add(n)
    }
    if (names.size === 0) continue
    const map = new Map<string, string>()
    for (const name of names) {
      // Breadth-first up the payload wires: the nearest writer of `name` wins.
      const seen = new Set<string>([reader.id])
      let frontier = upstream.get(reader.id) ?? []
      let found: string | null = null
      while (frontier.length > 0 && found === null) {
        const next: string[] = []
        for (const id of frontier) {
          if (seen.has(id) || !byId.has(id)) continue
          seen.add(id)
          if (writes.get(id)?.has(name)) { found = id; break }
          next.push(...(upstream.get(id) ?? []))
        }
        frontier = next
      }
      const renamed = found ? writeRenames.get(found)?.get(name) : undefined
      if (renamed && renamed !== name) map.set(name, renamed)
    }
    if (map.size > 0) out.set(reader.id, map)
  }
  return out
}

/** `params` with every read param's names passed through `map`. */
function renameReads(type: string, params: Record<string, ParamValue>, map: Map<string, string>): Record<string, ParamValue> {
  const out = { ...params }
  for (const spec of readParamsOf(type)) {
    const v = out[spec.name]
    if (spec.type === 'attr_list') {
      if (v == null) continue
      const list = attrListValue(v)
      if (list.some(n => map.has(n))) out[spec.name] = list.map(n => map.get(n) ?? n)
    } else if (typeof v === 'string' && map.has(v)) {
      out[spec.name] = map.get(v)!
    }
  }
  return out
}

/**
 * Put `payload` into `graph`. Returns the new graph and the new ids. The
 * input graph is not changed. Throws ReadOnlyGraphError on a read-only
 * graph: the caller checks first, this is the last line of defence.
 */
export function pastePayload(graph: Graph, payload: ClipboardPayload, opts: PasteOptions = {}): PasteResult {
  if (graph.readOnly) throw new ReadOnlyGraphError('paste')
  const [dx, dy] = opts.at
    ? [opts.at.x - payload.origin[0], opts.at.y - payload.origin[1]]
    : (opts.offset ?? [0, 0])

  const used = usedIds(graph)
  const idMap = new Map<string, string>()
  for (const n of payload.nodes) {
    const wanted = opts.nodeId?.(n.id)
    if (wanted !== undefined && !used.has(wanted)) {
      used.add(wanted)
      idMap.set(n.id, wanted)
    } else {
      idMap.set(n.id, freshId(used, newNodeId))
    }
  }
  const parentOf = (p: string | null): string | null => {
    if (p !== null && idMap.has(p)) return idMap.get(p)!
    const target = opts.parent === undefined ? p : opts.parent
    return target !== null && target in graph.nodes ? target : null
  }

  // Written attributes: unique in the whole graph, pasted nodes included.
  const taken = writtenNames(graph)
  const writeRenames = new Map<string, Map<string, string>>()
  for (const n of payload.nodes) {
    const map = new Map<string, string>()
    for (const w of writesByParam(n)) {
      const name = uniqueAttrName(w.name, taken)
      taken.add(name)
      map.set(w.name, name)
    }
    writeRenames.set(n.id, map)
  }
  const reads = readRenames(payload.nodes, payload.wires, writeRenames)

  const nodes: Record<string, GraphNode> = { ...graph.nodes }
  const nodeIds: string[] = []
  for (const src of payload.nodes) {
    const id = idMap.get(src.id)!
    const parent = parentOf(src.parent)
    // Positions are absolute flow coordinates at every level (EA-1), so the
    // nodes inside a pasted network move with it, by the same offset.
    const base = isValidName(src.name) ? src.name : sanitizeName(src.name || src.type || 'node')
    const name = uniqueName(base, siblingNames({ nodes }, parent))
    let params: Record<string, ParamValue> = clone(src.params ?? {})
    for (const w of writesByParam(src)) {
      const renamed = writeRenames.get(src.id)?.get(w.name)
      if (renamed && renamed !== w.name) params[w.param] = renamed
    }
    const readMap = reads.get(src.id)
    if (readMap) params = renameReads(src.type, params, readMap)
    nodes[id] = {
      ...clone(src),
      id,
      name,
      parent,
      params,
      position: [src.position[0] + dx, src.position[1] + dy],
      // One display node per network (spec S16): the copy never takes the flag.
      display: false,
    }
    nodeIds.push(id)
  }

  const wireIds: string[] = []
  const wireIdsUsed = new Set(graph.wires.map(w => w.id))
  const wires: GraphWire[] = [...graph.wires]
  for (const src of payload.wires) {
    const from = idMap.get(src.from)
    const to = idMap.get(src.to)
    if (!from || !to) continue
    const id = freshId(wireIdsUsed, newWireId)
    const wire: GraphWire = { ...clone(src), id, from, to }
    // An old (v2) wire label names what flows through it: follow a rename.
    if (typeof src.attr === 'string') {
      const renamed = writeRenames.get(src.from)?.get(src.attr)
      if (renamed) wire.attr = renamed
    }
    wires.push(wire)
    wireIds.push(id)
  }

  const annotationIds: string[] = []
  const shift = (r: [number, number, number, number]): [number, number, number, number] => [r[0] + dx, r[1] + dy, r[2], r[3]]
  const boxes = payload.boxes.map(b => {
    const id = freshId(used, () => annotationId('b'))
    annotationIds.push(id)
    return {
      ...clone(b),
      id,
      rect: shift(b.rect),
      members: b.members.filter(m => idMap.has(m)).map(m => idMap.get(m)!),
      parent: parentOf(b.parent),
    }
  })
  const notes = payload.notes.map(n => {
    const id = freshId(used, () => annotationId('s'))
    annotationIds.push(id)
    return { ...clone(n), id, rect: shift(n.rect), parent: parentOf(n.parent) }
  })

  const annotations = boxes.length > 0 || notes.length > 0
    ? {
        boxes: [...(graph.annotations?.boxes ?? []), ...boxes],
        notes: [...(graph.annotations?.notes ?? []), ...notes],
      }
    : graph.annotations

  // Stored path strings inside the pasted nodes follow the paste's renames
  // (rsi -> rsi1), limited to the pasted nodes (FC-9; a no-op until W6/W7
  // store paths). Alt-drag pastes through here too.
  let out: Graph = { ...graph, nodes, wires, annotations }
  const scope = new Set(nodeIds)
  for (const [oldPath, newPath] of pastePathRenames(payload, nodes, idMap)) {
    out = rewritePathRefs(out, oldPath, newPath, scope)
  }

  return {
    graph: out,
    nodeIds,
    wireIds,
    annotationIds,
    idMap,
  }
}

/**
 * The [old, new] paths of pasted nodes whose path changed by the paste's
 * renames, in the payload's own tree (a parent outside the payload counts as
 * the top), so a ref between pasted siblings (`../rsi`) can follow
 * (`../rsi1`). Exported for tests.
 */
export function pastePathRenames(
  payload: Pick<ClipboardPayload, 'nodes'>,
  nodes: Record<string, GraphNode>,
  idMap: ReadonlyMap<string, string>,
): Array<[string, string]> {
  const before: Record<string, { id: string; name: string; parent: string | null }> = {}
  const after: Record<string, { id: string; name: string; parent: string | null }> = {}
  for (const src of payload.nodes) {
    const id = idMap.get(src.id)
    if (!id || !nodes[id]) continue
    const parent = src.parent !== null && idMap.has(src.parent) ? idMap.get(src.parent)! : null
    before[id] = { id, name: src.name, parent }
    after[id] = { id, name: nodes[id].name, parent }
  }
  const out: Array<[string, string]> = []
  for (const id of Object.keys(after)) {
    let oldPath: string
    let newPath: string
    try {
      oldPath = nodePath({ nodes: before }, id)
      newPath = nodePath({ nodes: after }, id)
    } catch {
      continue
    }
    if (oldPath !== newPath) out.push([oldPath, newPath])
  }
  return out
}

// ---------------------------------------------------------------------------
// Alt-drag
// ---------------------------------------------------------------------------

/**
 * Give each node in `map` (old id -> new id) its new id everywhere: the
 * node itself, wires, parents of nodes inside it, box members. Nothing else
 * changes. Node order is kept.
 */
export function remapNodeIds(graph: Graph, map: ReadonlyMap<string, string>): Graph {
  if (map.size === 0) return graph
  const re = (id: string) => map.get(id) ?? id
  const reP = (p: string | null) => (p === null ? null : re(p))
  const nodes: Record<string, GraphNode> = {}
  for (const [id, n] of Object.entries(graph.nodes)) {
    const nid = re(id)
    nodes[nid] = nid === id && reP(n.parent) === n.parent ? n : { ...n, id: nid, parent: reP(n.parent) }
  }
  const wires = graph.wires.map(w =>
    map.has(w.from) || map.has(w.to) ? { ...w, from: re(w.from), to: re(w.to) } : w)
  const boxes = (graph.annotations?.boxes ?? []).map(b =>
    b.members.some(m => map.has(m)) || (b.parent !== null && map.has(b.parent))
      ? { ...b, members: b.members.map(re), parent: reP(b.parent) }
      : b)
  const notes = (graph.annotations?.notes ?? []).map(n =>
    n.parent !== null && map.has(n.parent) ? { ...n, parent: reP(n.parent) } : n)
  return { ...graph, nodes, wires, annotations: { boxes, notes } }
}

/**
 * Alt-drag duplicate (foundation 6.1: "duplicates the selection and drags
 * the copy, internal wires kept").
 *
 * React Flow keeps dragging the node ids it grabbed, and it cannot be told
 * to drag other ones. So the grabbed ids become the copy: each original
 * moves to a new id where it stands (with its name, its outside wires, its
 * display flag and its box), and the grabbed ids are filled with a copy
 * (unique name, unique written attributes, internal wires only, no display
 * flag). To the user the original stays put and the copy follows the
 * pointer, and whatever runs on drop (splice onto a wire, box membership)
 * acts on the copy.
 *
 * `positions` are where React Flow shows the grabbed nodes now; the copies
 * are put there, so the canvas does not snap them back while the drag goes
 * on. Returns null when none of `ids` is a graph node.
 */
export function swapIdsForDrag(
  graph: Graph,
  ids: readonly string[],
  positions: ReadonlyMap<string, [number, number]> = new Map(),
): { graph: Graph; copyIds: string[]; originalIds: Map<string, string> } | null {
  const grabbed = ids.filter(id => id in graph.nodes)
  if (grabbed.length === 0) return null
  const payload = copyFromGraph(graph, { nodeIds: grabbed })
  if (!payload) return null
  const used = usedIds(graph)
  const originalIds = new Map<string, string>()
  for (const id of grabbed) originalIds.set(id, freshId(used, newNodeId))
  const moved = remapNodeIds(graph, originalIds)
  const grabbedSet = new Set(grabbed)
  const pasted = pastePayload(moved, payload, {
    offset: [0, 0],
    nodeId: old => (grabbedSet.has(old) ? old : undefined),
  })
  let out = pasted.graph
  if (positions.size > 0) {
    const nodes = { ...out.nodes }
    for (const id of grabbed) {
      const p = positions.get(id)
      if (p && nodes[id]) nodes[id] = { ...nodes[id], position: [p[0], p[1]] }
    }
    out = { ...out, nodes }
  }
  return { graph: out, copyIds: grabbed, originalIds }
}
