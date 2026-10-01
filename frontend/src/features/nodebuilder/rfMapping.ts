/**
 * Graph to React Flow mapping (W3 pre-step 3.0, moved out of Canvas.tsx).
 *
 * - Graph nodes become React Flow nodes, each drawn by the renderer for its
 *   catalog category (nodeTypes.ts).
 * - Graph wires become 'attr' edges carrying their label, placement and
 *   diagnostic (edges/AttrEdge.tsx).
 * - Other canvas items (network boxes and sticky notes, 3.E; network frames,
 *   W5) come from registered node sources (`registerRfNodeSource`).
 *
 * Both mappers keep a per-item cache, so ONLY the item whose underlying
 * state changed gets a fresh object (and a fresh `data` reference). Without
 * this, any change rebuilt every node with a new `data`, and React.memo on
 * the renderers re-rendered all N nodes for a one-node change. When nothing
 * changed at all the previous array comes back, so effect deps and child
 * memos do not see a new reference.
 */

import { useSyncExternalStore } from 'react'
import type { Node as RFNode, Edge as RFEdge } from '@xyflow/react'
import type { Graph, GraphNode } from '../../api/nodebuilder'
import { NODE_CATALOG, type NodeCatalogEntry } from './catalog'
import { nodePath } from './paths'
import { KeyedStack } from './keyedStack'
import { DEFAULT_NODE_SIZE } from './geometry'
import type { BaseNodeData } from './nodes/BaseNode'
import type { AttrEdgeData } from './edges/AttrEdge'
import type { Diagnostic } from '../../api/nodebuilderValidate'
import {
  placeLabels,
  portsOf,
  portsSpecOf,
  type LabelPlacement,
  type Rect,
  type WireLabel,
} from './streamLabels'

// ---------------------------------------------------------------------------
// Category → RF node type
// ---------------------------------------------------------------------------

const CATEGORY_TO_RF_TYPE: Record<string, string> = {
  ticker:     'ticker',
  indicator:  'indicator',
  comparison: 'comparison',
  logic:      'logic',
  settings:   'settings',
  output:     'nbOutput',
}

// Perf: NODE_CATALOG.find(...) per-node per-render was O(N×M); pre-build a
// Map once at module load. Catalog is static.
export const CATALOG_BY_NAME: Map<string, NodeCatalogEntry> = new Map(
  NODE_CATALOG.map(e => [e.name, e]),
)

/**
 * Resolve the React Flow node type for a given backend node type string.
 * Falls back to 'indicator' for types not in the catalog.
 */
export function rfTypeFor(backendType: string): string {
  const entry = CATALOG_BY_NAME.get(backendType)
  if (!entry) return 'indicator'  // generic fallback
  return CATEGORY_TO_RF_TYPE[entry.cat] ?? 'indicator'
}

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Coordinate spaces (EA-1)
// ---------------------------------------------------------------------------
//
// `GraphNode.position` and annotation rects are ABSOLUTE flow coordinates,
// at every level: a node inside a network frame stores where it is on the
// canvas, not where it is inside the frame. Everything that writes a
// position (drag stop, box drag, Alt-drag, the Tab menu, paste at the
// cursor, tidy, frame) works in these coordinates.
//
// React Flow is different for child nodes: a node with `parentId` (W5 maps
// a network frame's children that way) takes a position RELATIVE to its
// parent. The two helpers below are the only conversion between the two:
// the node mapper uses `rfPositionOf` to draw, and every consumer of a
// React Flow node's position (drag callbacks hand those out) uses
// `graphPositionOf` (CanvasCtx.graphPosition does it for plugins). A node
// without `parentId` has the same position in both, so today both are
// identities.
//
// React Flow also needs a parent before its children in the nodes array.
// Graph nodes come in one mapped list and annotation sources draw
// 'before' them, so a W5 source that draws frames as parents must order
// them first (`order: 'before'`), and a box inside a frame must not be a
// React Flow child of it (boxes stay absolute, unparented).

/** A point in flow units. */
export interface FlowXY {
  x: number
  y: number
}

/** The React Flow position for a graph (absolute) position under a parent at `parentAbs` (null: no parent). */
export function rfPositionOf(graphPos: readonly [number, number], parentAbs: FlowXY | null): FlowXY {
  return parentAbs
    ? { x: graphPos[0] - parentAbs.x, y: graphPos[1] - parentAbs.y }
    : { x: graphPos[0], y: graphPos[1] }
}

/**
 * The graph (absolute) position of a React Flow node. `absOf(parentId)`
 * gives the parent's absolute position (see `absoluteLookup`).
 */
export function graphPositionOf(
  n: { position: FlowXY; parentId?: string },
  absOf: (id: string) => FlowXY | null,
): [number, number] {
  if (!n.parentId) return [n.position.x, n.position.y]
  const p = absOf(n.parentId) ?? { x: 0, y: 0 }
  return [n.position.x + p.x, n.position.y + p.y]
}

/**
 * `absOf` for `graphPositionOf` over a list of React Flow nodes (whose own
 * positions may be relative to their parents too). Built lazily: a lookup
 * over nodes with no parents never builds the map.
 */
export function absoluteLookup(
  nodes: readonly { id: string; position: FlowXY; parentId?: string }[],
): (id: string) => FlowXY | null {
  let byId: Map<string, { id: string; position: FlowXY; parentId?: string }> | null = null
  const memo = new Map<string, FlowXY | null>()
  const abs = (id: string, depth = 0): FlowXY | null => {
    if (memo.has(id)) return memo.get(id)!
    byId ??= new Map(nodes.map(n => [n.id, n]))
    const n = byId.get(id)
    let out: FlowXY | null = null
    if (n) {
      // Depth guard: a parent cycle never loops forever.
      const p = n.parentId && depth < 64 ? abs(n.parentId, depth + 1) : null
      out = p ? { x: n.position.x + p.x, y: n.position.y + p.y } : { x: n.position.x, y: n.position.y }
    }
    memo.set(id, out)
    return out
  }
  return id => abs(id)
}

/** Return `next`, or `prev` when both hold the same objects in the same order. */
export function stableArray<T>(prev: T[] | null, next: T[]): T[] {
  if (prev && prev.length === next.length && next.every((x, i) => x === prev[i])) return prev
  return next
}

/**
 * Maps graph nodes to React Flow nodes, with the per-node cache described
 * above. Selection is not part of the cache key: React Flow owns it locally
 * (see Canvas). A move only changes the position, so the `data` object is
 * reused when nothing inside it changed.
 */
/** The node's path (`/rsi1`), or its id when the path cannot be built. */
function safeNodePath(nodes: Graph['nodes'], id: string): string {
  try {
    return nodePath({ nodes }, id)
  } catch {
    return id
  }
}

export function createNodeMapper(): (nodes: Graph['nodes'], editable: boolean) => RFNode[] {
  // Keyed on the node object itself (EA-6): commits share unchanged nodes,
  // so a node whose object did not change keeps its React Flow node and
  // data. The renderer gets the whole node (`data.node`), so a later wave's
  // field needs no change here. Its path is in the key too: a rename of the
  // node (or, W5, of a parent) changes it.
  const cache = new Map<string, { node: GraphNode; editable: boolean; path: string; rfNode: RFNode }>()
  let prev: RFNode[] | null = null
  return (nodes, editable) => {
    const seen = new Set<string>()
    const result: RFNode[] = []
    for (const n of Object.values(nodes)) {
      seen.add(n.id)
      const path = safeNodePath(nodes, n.id)
      const cached = cache.get(n.id)
      if (cached && cached.node === n && cached.editable === editable && cached.path === path) {
        result.push(cached.rfNode)
        continue
      }
      const data: BaseNodeData = {
        backendType: n.type,
        catalog: CATALOG_BY_NAME.get(n.type) ?? null,
        params: n.params,
        display: n.display,
        bypass: n.bypass,
        name: n.name,
        nodePath: path,
        editable,
        node: n,
      }
      const rfNode: RFNode = {
        id: n.id,
        type: rfTypeFor(n.type),
        // No `parentId` yet (W5 sets it for network frame children).
        position: rfPositionOf(n.position, null),
        data,
        draggable: editable,
        selectable: true,
      }
      cache.set(n.id, { node: n, editable, path, rfNode })
      result.push(rfNode)
    }
    // Evict removed nodes so the cache doesn't grow unbounded.
    for (const id of Array.from(cache.keys())) {
      if (!seen.has(id)) cache.delete(id)
    }
    prev = stableArray(prev, result)
    return prev
  }
}

// ---------------------------------------------------------------------------
// Extra node sources (boxes, notes, network frames)
// ---------------------------------------------------------------------------

/** What a node source gets besides the graph. */
export interface RfNodeSourceOpts {
  editable: boolean
  /**
   * The id of the network node the canvas shows, or null for the root
   * (EA-4). A source draws only that network's items. The canvas re-runs
   * sources when it changes.
   */
  networkId: string | null
}

export interface RfNodeSource {
  id: string
  /**
   * React Flow nodes for this graph that are not graph nodes. Their ids must
   * not clash with graph node ids. Return the same objects when nothing
   * changed, so React Flow does not re-render them. Called when the graph
   * changes (any field, including `annotations`).
   */
  nodes(graph: Graph, opts: RfNodeSourceOpts): RFNode[]
  /**
   * 'before' (default): drawn before the graph nodes in the DOM, for things
   * that sit under them (boxes, notes; they also set a negative zIndex).
   * 'after': drawn after them.
   */
  order?: 'before' | 'after'
}

// Per id a stack (keyedStack.ts, EA-12): replacing a source by id and then
// removing the replacement brings the old one back.
const sourceStack = new KeyedStack<RfNodeSource>()
let sources: RfNodeSource[] = []
let sourcesVersion = 0
const sourceListeners = new Set<() => void>()

/** Add (or replace by id) a node source. Returns a function that removes it again. */
export function registerRfNodeSource(src: RfNodeSource): () => void {
  const remove = sourceStack.push(src.id, src)
  sources = sourceStack.values()
  sourcesVersion += 1
  for (const l of [...sourceListeners]) l()
  return () => {
    if (!remove()) return
    sources = sourceStack.values()
    sourcesVersion += 1
    for (const l of [...sourceListeners]) l()
  }
}

/** Every registered node source. */
export function listRfNodeSources(): readonly RfNodeSource[] {
  return sources
}

function subscribeSources(listener: () => void): () => void {
  sourceListeners.add(listener)
  return () => { sourceListeners.delete(listener) }
}

/** A number that changes when a node source is added or removed. */
export function useRfNodeSourcesVersion(): number {
  return useSyncExternalStore(subscribeSources, () => sourcesVersion)
}

/** The nodes every source gives for this graph, split into before and after. */
export function sourceNodes(graph: Graph, editable: boolean, networkId: string | null = null): { before: RFNode[]; after: RFNode[] } {
  const before: RFNode[] = []
  const after: RFNode[] = []
  for (const src of sources) {
    let list: RFNode[] = []
    try {
      list = src.nodes(graph, { editable, networkId })
    } catch (err) {
      console.error(`nodebuilder: node source "${src.id}" failed`, err)
    }
    if (src.order === 'after') after.push(...list)
    else before.push(...list)
  }
  return { before, after }
}

// ---------------------------------------------------------------------------
// Wires
// ---------------------------------------------------------------------------

/** Node box used for label placement until React Flow has measured a node (geometry.ts). */
export { DEFAULT_NODE_SIZE }

/**
 * Where each wire's label goes (spec S11). Placement uses the node boxes
 * (measured sizes when known), the number of input ports each node draws,
 * and which nodes have dynamic inputs.
 */
export function wirePlacements(
  graph: Pick<Graph, 'nodes' | 'wires'>,
  labels: Record<string, WireLabel>,
  connectedPorts: Map<string, string[]>,
  sizes: Map<string, { w: number; h: number }>,
): Record<string, LabelPlacement> {
  const rects: Record<string, Rect> = {}
  const portCounts: Record<string, number> = {}
  const dynamicNodes = new Set<string>()
  for (const n of Object.values(graph.nodes)) {
    const size = sizes.get(n.id) ?? DEFAULT_NODE_SIZE
    rects[n.id] = { x: n.position[0], y: n.position[1], w: size.w, h: size.h }
    portCounts[n.id] = portsOf(n.type, connectedPorts.get(n.id) ?? []).length
    if (portsSpecOf(n.type)?.dynamic) dynamicNodes.add(n.id)
  }
  const items = graph.wires.map(w => ({
    id: w.id, from: w.from, to: w.to, toPort: w.to_port, text: labels[w.id]?.text ?? '',
  }))
  return placeLabels(items, rects, portCounts, dynamicNodes)
}

/** Everything a wire's React Flow edge shows. */
export interface EdgeMapInput {
  graph: Pick<Graph, 'nodes' | 'wires'>
  /** The wire the canvas has selected (store-driven), or null. */
  selectedWireId: string | null
  labels: Record<string, WireLabel>
  placements: Record<string, LabelPlacement>
  /** Below 60% zoom labels hide at rest. */
  lowZoom: boolean
  diagByWire: Map<string, Diagnostic>
}

/** Maps graph wires to 'attr' edges, with the same per-item cache as nodes. */
export function createEdgeMapper(): (input: EdgeMapInput) => RFEdge[] {
  const cache = new Map<string, { sig: string; rfEdge: RFEdge }>()
  let prev: RFEdge[] | null = null
  return ({ graph, selectedWireId, labels, placements, lowZoom, diagByWire }) => {
    const seen = new Set<string>()
    const result: RFEdge[] = []
    for (const w of graph.wires) {
      seen.add(w.id)
      const selected = w.id === selectedWireId
      const label = labels[w.id]
      const place = placements[w.id] ?? { t: 0.5, dx: 0, hidden: null }
      const consumer = graph.nodes[w.to]
      const portLabel = portsSpecOf(consumer?.type)?.ports[Number(w.to_port.slice(2))]?.label ?? w.to_port
      const data: AttrEdgeData = {
        from: w.from,
        to: w.to,
        text: label?.text ?? '',
        placeholder: label?.placeholder ?? false,
        reads: label?.reads ?? [],
        t: place.t,
        dx: place.dx,
        hidden: place.hidden,
        lowZoom,
        // Set on the local mirror by markHotEdges (node hover).
        hot: false,
        diag: diagByWire.get(w.id)?.message ?? null,
        fromName: graph.nodes[w.from]?.name ?? w.from,
        toName: consumer?.name ?? w.to,
        portLabel,
      }
      const sig = `${w.from}|${w.to}|${w.to_port}|${selected ? 1 : 0}|${JSON.stringify(data)}`
      const cached = cache.get(w.id)
      if (cached && cached.sig === sig) {
        result.push(cached.rfEdge)
        continue
      }
      const rfEdge: RFEdge = {
        id: w.id,
        source: w.from,
        target: w.to,
        // Handle ids: the one output is 'out'; inputs are 'in0', 'in1', ...
        sourceHandle: 'out',
        targetHandle: w.to_port,
        type: 'attr',
        selected,
        data,
      }
      cache.set(w.id, { sig, rfEdge })
      result.push(rfEdge)
    }
    for (const id of Array.from(cache.keys())) {
      if (!seen.has(id)) cache.delete(id)
    }
    prev = stableArray(prev, result)
    return prev
  }
}
