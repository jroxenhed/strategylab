/**
 * Asset graph edits (specs S41, S42, S43; plan W6 item 6.D). Pure
 * functions: each returns a new graph or a value, and the caller commits.
 *
 * - `assetNetworkOf`: a subnet's contents as an asset `network` (its
 *   children at any depth and the wires among them). Top-level children get
 *   `parent: null` and positions relative to the subnet node, so the asset
 *   does not depend on where the subnet sat in its graph.
 * - `deriveInterface`: what the subnet reads from its inputs and what it
 *   writes, from the last `/validate` streams (with the node params as a
 *   fallback when streams are missing).
 * - `replaceWithLockedInstance`: after a save, the subnet becomes a locked
 *   instance of the saved version, drawn as a card: its children and inner
 *   wires go (they now come from the library), its outside wires stay.
 * - `assetInstanceNode`: a new locked instance (Asset Manager insert, Tab
 *   menu), with its promoted params at their defaults.
 */

import type { Graph, GraphNode, GraphPromotedParam, GraphWire, ParamValue, StreamSchema } from '../../../api/nodebuilder'
import type { AssetFile, AttrDecl, PromotedParam } from '../../../api/graphLibrary'
import { newNodeId, ReadOnlyGraphError, uniqueName } from '../operations'
import { descendantsOf } from '../networkOps'
import { friendlyName } from '../search'
import { readsOf, writesOf } from '../streamLabels'
import { promotedOf } from './promote'
import { computeFrameLayouts } from '../rfMapping'

const BOUNDARY = new Set(['subnet_input', 'subnet_output'])

/**
 * Where the subnet is drawn: its stored position as a card, the frame's
 * top-left corner in frame view (a frame is drawn around its children, so
 * its stored position can be stale). The asset's positions are relative to
 * this point, and a locked instance made from the subnet sits here (FE-05).
 */
export function assetOriginOf(graph: Pick<Graph, 'nodes'>, subnetId: string): [number, number] {
  const net = graph.nodes[subnetId]
  if (!net) return [0, 0]
  if (net.meta?.view === 'card') return [net.position[0], net.position[1]]
  const asFrame = { ...graph.nodes, [subnetId]: { ...net, meta: { ...(net.meta ?? {}), view: 'frame' as const } } }
  const l = computeFrameLayouts(asFrame, undefined, net.parent ?? null).get(subnetId)
  return l ? [l.x, l.y] : [net.position[0], net.position[1]]
}

/** The subnet's contents as an asset network (children at any depth and their wires). */
export function assetNetworkOf(graph: Graph, subnetId: string): AssetFile['network'] {
  const net = graph.nodes[subnetId]
  if (!net) return { nodes: {}, wires: [] }
  const ids = new Set(descendantsOf(graph.nodes, subnetId))
  const [ox, oy] = assetOriginOf(graph, subnetId)
  const nodes: Record<string, GraphNode> = {}
  for (const id of ids) {
    const n = graph.nodes[id]
    // Only the subnet carries asset_ref and locked; a child is stored plain
    // (a nested asset instance keeps its own ref, it is a different asset).
    nodes[id] = {
      ...n,
      parent: n.parent === subnetId ? null : n.parent,
      position: [n.position[0] - ox, n.position[1] - oy],
    }
  }
  const wires: GraphWire[] = graph.wires
    .filter(w => ids.has(w.from) && ids.has(w.to))
    .map(w => ({ ...w }))
  return { nodes, wires }
}

function declOf(name: string, stream: StreamSchema | undefined, fallbackDtype = 'float'): AttrDecl {
  const point = stream?.points.find(a => a.name === name)
  if (point) return { name, class: 'point', dtype: point.dtype }
  const detail = stream?.detail.find(a => a.name === name)
  if (detail) return { name, class: 'detail', dtype: detail.dtype }
  return { name, class: 'point', dtype: fallbackDtype }
}

/**
 * What the subnet reads from its inputs and what its output writes.
 *
 * - reads: attributes some inside node reads (its attr params) that no
 *   inside node writes, so they must arrive through an input; the types
 *   come from the streams arriving at the `subnet_input` nodes.
 * - writes: the attributes in the output stream that an inside node wrote
 *   (from `/validate` streams), else every attribute an inside node writes.
 */
export function deriveInterface(
  graph: Graph,
  subnetId: string,
  streams: Readonly<Record<string, StreamSchema>> = {},
): AssetFile['interface'] {
  const ids = descendantsOf(graph.nodes, subnetId)
  const inner = ids.map(id => graph.nodes[id]).filter(n => n && !BOUNDARY.has(n.type))
  const writtenInside = new Set<string>()
  for (const n of inner) for (const w of writesOf(n)) writtenInside.add(w.name)

  // Streams that arrive through the inputs.
  const inputs = ids.map(id => graph.nodes[id]).filter(n => n?.type === 'subnet_input')
  const arriving = (name: string): StreamSchema | undefined =>
    inputs.map(b => streams[b.id]).find(s => s && (s.points.some(a => a.name === name) || s.detail.some(a => a.name === name)))

  const readNames: string[] = []
  for (const n of inner) {
    for (const r of readsOf(n)) {
      if (!writtenInside.has(r) && !readNames.includes(r)) readNames.push(r)
    }
  }
  const reads = readNames.map(name => declOf(name, arriving(name)))

  const out = ids.map(id => graph.nodes[id]).find(n => n?.type === 'subnet_output')
  const outStream = (out && streams[out.id]) || streams[subnetId]
  let writes: AttrDecl[]
  if (outStream) {
    const inside = new Set(ids)
    const own = (a: { name: string; written_by: string | null }) =>
      a.written_by ? inside.has(a.written_by) || a.written_by.startsWith(`${subnetId}::`) : writtenInside.has(a.name)
    writes = [
      ...outStream.points.filter(own).map(a => ({ name: a.name, class: 'point' as const, dtype: a.dtype })),
      ...outStream.detail.filter(own).map(a => ({ name: a.name, class: 'detail' as const, dtype: a.dtype })),
    ]
  } else {
    writes = out ? [...writtenInside].map(name => declOf(name, undefined)) : []
  }
  return { reads, writes }
}

/** The promoted list as the asset stores it (the stored `default`, never the instance's value: S41). */
export function assetPromotedOf(net: GraphNode): PromotedParam[] {
  return promotedOf(net).map(p => ({
    name: p.name,
    label: p.label,
    target: p.target,
    type: p.type as PromotedParam['type'],
    default: p.default,
  }))
}

/** The default Tab-menu label for an asset name: "momentum_confirm" gives "Momentum Confirm". */
export function defaultPaletteLabel(name: string): string {
  return name
    .split('_')
    .filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ') || friendlyName(name)
}

/**
 * The subnet becomes a locked instance of `ref`: its children and the
 * wires among them are removed (the library holds them now), and the
 * wires into and out of the subnet stay. Boxes and notes inside it go too.
 */
export function replaceWithLockedInstance(graph: Graph, subnetId: string, ref: { name: string; version: number }): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('lock asset instance')
  const net = graph.nodes[subnetId]
  if (!net) return graph
  const gone = new Set(descendantsOf(graph.nodes, subnetId))
  const nodes: Record<string, GraphNode> = {}
  for (const [id, n] of Object.entries(graph.nodes)) if (!gone.has(id)) nodes[id] = n
  // A locked instance is a card (FA1): a frame would be an empty box at a
  // stale position. It takes the frame's drawn corner (FE-05).
  nodes[subnetId] = {
    ...net,
    position: assetOriginOf(graph, subnetId),
    meta: { ...(net.meta ?? {}), view: 'card' },
    asset_ref: { name: ref.name, version: ref.version },
    locked: true,
  }
  const inGone = (p: string | null) => p !== null && (p === subnetId || gone.has(p))
  return {
    ...graph,
    nodes,
    wires: graph.wires.filter(w => !gone.has(w.from) && !gone.has(w.to)),
    annotations: {
      boxes: graph.annotations.boxes.filter(b => !inGone(b.parent ?? null)),
      notes: graph.annotations.notes.filter(n => !inGone(n.parent ?? null)),
    },
  }
}

/** The fields of an asset a new instance needs. */
export interface InstanceSource {
  name: string
  version: number
  promoted?: readonly PromotedParam[]
}

/**
 * A new locked instance of an asset version, in `parent` at `position`.
 * Its promoted params start at their defaults. The promoted list is copied
 * so the card can draw its rows before the asset file is fetched.
 */
export function assetInstanceNode(
  graph: Graph,
  asset: InstanceSource,
  parent: string | null,
  position: [number, number],
): GraphNode {
  const promoted: GraphPromotedParam[] = (asset.promoted ?? []).map(p => ({ ...p }))
  const params: Record<string, ParamValue> = {}
  for (const p of promoted) params[p.name] = (p.default ?? null) as ParamValue
  return {
    id: newNodeId(),
    type: 'subnet',
    name: uniqueName(graph, asset.name, parent),
    parent,
    params,
    position,
    display: false,
    bypass: false,
    meta: { view: 'card' },
    promoted,
    asset_ref: { name: asset.name, version: asset.version },
    locked: true,
  }
}

/** Add a new locked instance (one commit for the caller). */
export function addAssetInstance(
  graph: Graph,
  asset: InstanceSource,
  parent: string | null,
  position: [number, number],
): { graph: Graph; nodeId: string } {
  if (graph.readOnly) throw new ReadOnlyGraphError('insert asset')
  const node = assetInstanceNode(graph, asset, parent, position)
  return { graph: { ...graph, nodes: { ...graph.nodes, [node.id]: node } }, nodeId: node.id }
}

/** The separator in the ids of an expanded asset's nodes and wires (backend `COMPOSITE_ID_SEP`). */
export const COMPOSITE_SEP = '::'

/**
 * Unlock a locked instance into a local copy (S38 "Unlock"). The asset's
 * nodes and wires become the instance's own, with the ids the backend's
 * `bake_assets` gives them: `<instance>::<child id>` and
 * `<instance>::<wire id>`, top-level children parented to the instance.
 * The instance keeps `asset_ref` (where the copy came from), gets
 * `locked: false` and the asset's promoted list; its values stay in
 * `params`. Positions are stored relative to the instance in the asset, so
 * they move to the instance's place. A nested locked instance stays
 * locked: it is a different asset. One commit for the caller.
 */
export function unlockInstance(
  graph: Graph,
  instanceId: string,
  asset: { network: AssetFile['network']; promoted?: readonly PromotedParam[] },
): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('unlock asset')
  const inst = graph.nodes[instanceId]
  if (!inst || inst.type !== 'subnet' || inst.locked !== true || !inst.asset_ref) {
    throw new Error('Not a locked asset instance')
  }
  const cid = (id: string) => `${instanceId}${COMPOSITE_SEP}${id}`
  // A locked instance stores no children; drop any stray ones (the backend ignores them too).
  const stray = new Set(descendantsOf(graph.nodes, instanceId))
  const nodes: Record<string, GraphNode> = {}
  for (const [id, n] of Object.entries(graph.nodes)) if (!stray.has(id)) nodes[id] = n
  const [ox, oy] = inst.position
  for (const n of Object.values(asset.network.nodes)) {
    const id = cid(n.id)
    if (nodes[id]) throw new Error(`Node id ${id} is already taken`)
    nodes[id] = {
      ...n,
      id,
      parent: n.parent ? cid(n.parent) : instanceId,
      position: [n.position[0] + ox, n.position[1] + oy],
    }
  }
  nodes[instanceId] = {
    ...inst,
    locked: false,
    promoted: (asset.promoted ?? inst.promoted ?? []).map(p => ({ ...p })) as GraphPromotedParam[],
  }
  const wires: GraphWire[] = [
    ...graph.wires.filter(w => !stray.has(w.from) && !stray.has(w.to)),
    ...asset.network.wires.map(w => ({ ...w, id: cid(w.id), from: cid(w.from), to: cid(w.to) })),
  ]
  return { ...graph, nodes, wires }
}

// ── Lifecycle (S38): update to a newer version, re-lock a local copy ───────

/**
 * Point a locked instance at another version of its asset ("Update to vN",
 * one commit for the caller). It takes that version's promoted list:
 * values for names the new version still promotes are kept, new names start
 * at their defaults, and values for names it no longer promotes go.
 */
export function updateInstanceVersion(
  graph: Graph,
  instanceId: string,
  file: Pick<AssetFile, 'name' | 'version' | 'promoted'>,
): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('update asset')
  const inst = graph.nodes[instanceId]
  if (!inst || inst.locked !== true || inst.asset_ref?.name !== file.name) throw new Error('Not a locked instance of this asset')
  const promoted: GraphPromotedParam[] = (file.promoted ?? []).map(p => ({ ...p }))
  const keep = new Set(promoted.map(p => p.name))
  const params: Record<string, ParamValue> = {}
  const old = new Set(promotedOf(inst).map(p => p.name))
  for (const [k, v] of Object.entries(inst.params ?? {})) if (keep.has(k) || !old.has(k)) params[k] = v
  for (const p of promoted) if (!(p.name in params)) params[p.name] = (p.default ?? null) as ParamValue
  return {
    ...graph,
    nodes: {
      ...graph.nodes,
      [instanceId]: { ...inst, params, promoted, asset_ref: { name: file.name, version: file.version } },
    },
  }
}

/** JSON with sorted keys, so two equal values give the same text. */
function stableJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stableJson).join(',')}]`
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).filter(k => o[k] !== undefined).sort().map(k => `${JSON.stringify(k)}:${stableJson(o[k])}`).join(',')}}`
  }
  return JSON.stringify(v ?? null)
}

/** What decides a node's behaviour (not where it is drawn). */
function nodeShape(n: GraphNode): string {
  return stableJson({
    type: n.type,
    name: n.name,
    parent: n.parent ?? null,
    params: n.params ?? {},
    bypass: !!n.bypass,
    promoted: n.promoted ?? [],
    asset_ref: n.asset_ref ?? null,
    locked: n.locked === true,
  })
}

/**
 * True when an unlocked copy still holds exactly what its asset version
 * holds (positions and view aside), so re-locking loses nothing (S38: the
 * Inspector offers `Re-lock to v3` only then). The copy's children carry
 * the ids `unlockInstance` gave them (`<instance>::<child>`).
 */
export function localCopyMatches(graph: Graph, instanceId: string, file: Pick<AssetFile, 'network' | 'promoted'>): boolean {
  const inst = graph.nodes[instanceId]
  if (!inst || inst.locked === true || !inst.asset_ref) return false
  const cid = (id: string) => `${instanceId}${COMPOSITE_SEP}${id}`
  const ids = descendantsOf(graph.nodes, instanceId)
  const assetNodes = Object.values(file.network.nodes)
  if (ids.length !== assetNodes.length) return false
  for (const n of assetNodes) {
    const mine = graph.nodes[cid(n.id)]
    if (!mine) return false
    const want = { ...n, id: cid(n.id), parent: n.parent ? cid(n.parent) : instanceId }
    if (nodeShape(mine) !== nodeShape(want)) return false
  }
  const inside = new Set(ids)
  const wireKey = (w: Pick<GraphWire, 'from' | 'to' | 'from_port' | 'to_port'>) => `${w.from}>${w.from_port}>${w.to}>${w.to_port}`
  const mineWires = graph.wires.filter(w => inside.has(w.from) && inside.has(w.to)).map(wireKey).sort()
  const wantWires = file.network.wires.map(w => wireKey({ ...w, from: cid(w.from), to: cid(w.to) })).sort()
  if (mineWires.join('|') !== wantWires.join('|')) return false
  return stableJson(promotedOf(inst)) === stableJson(file.promoted ?? [])
}

/**
 * Re-lock an unlocked copy to the version it came from (one commit for the
 * caller): its children go back to the library, its values stay. The
 * caller checks `localCopyMatches` first.
 */
export function relockInstance(graph: Graph, instanceId: string): Graph {
  const inst = graph.nodes[instanceId]
  if (!inst || inst.locked === true || !inst.asset_ref) throw new Error('Not an unlocked asset copy')
  return replaceWithLockedInstance(graph, instanceId, inst.asset_ref)
}
