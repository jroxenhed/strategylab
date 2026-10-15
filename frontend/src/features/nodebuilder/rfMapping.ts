/**
 * Graph to React Flow mapping (W3 pre-step 3.0, moved out of Canvas.tsx).
 *
 * - Graph nodes become React Flow nodes, each drawn by the renderer for its
 *   catalog category (nodeTypes.ts).
 * - Graph wires become 'attr' edges carrying their label, placement and
 *   diagnostic (edges/AttrEdge.tsx).
 * - Network nodes (`subnet`, `output_group`, `regime`, W5 spec S31) become
 *   frames: their children are React Flow children (`parentId`), drawn
 *   inside the frame, and their boundary nodes (`subnet_input`,
 *   `subnet_output`) are not drawn as cards but as ports on the frame edge.
 * - Other canvas items (network boxes and sticky notes, 3.E) come from
 *   registered node sources (`registerRfNodeSource`).
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
import type { Graph, GraphNode, GraphWire } from '../../api/nodebuilder'
import { NODE_CATALOG, type NodeCatalogEntry } from './catalog'
import { nodePath } from './paths'
import { resolveGroupTicker } from './graphGroups'
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
 * The terminal types (plan W5 terminal table, S32b). They always draw as
 * terminal cards (OutputNode), whatever category an older catalog gives
 * them: `trailing_stop` was a settings node before W5.
 */
export const TERMINAL_TYPES: ReadonlySet<string> = new Set([
  'entry', 'exit', 'size', 'stop', 'trailing_stop', 'time_stop', 'regime',
])

/**
 * Resolve the React Flow node type for a given backend node type string.
 * Falls back to 'indicator' for types not in the catalog.
 */
export function rfTypeFor(backendType: string): string {
  if (NETWORK_TYPES.has(backendType)) return FRAME_RF_TYPE
  // W7 (S46): the Wrangle draws its code block as the node body.
  if (backendType === 'wrangle') return 'nbWrangle'
  if (TERMINAL_TYPES.has(backendType)) return CATEGORY_TO_RF_TYPE.output
  const entry = CATALOG_BY_NAME.get(backendType)
  if (!entry) return 'indicator'  // generic fallback
  return CATEGORY_TO_RF_TYPE[entry.cat] ?? 'indicator'
}

// ---------------------------------------------------------------------------
// Networks and their frames (W5, spec S31)
// ---------------------------------------------------------------------------
//
// A network is a graph node whose children point at it through `parent`
// (plan D7). A network is drawn expanded, as a frame around its children
// ("one visual model"), unless its `meta.view` is "card" (W6, FA1): then it
// is one card and its insides are drawn only when the user dives in (see
// "Card or frame" below).
//
// The frame is not stored. Its rectangle is the bounding box of its
// children plus padding, worked out here when the graph changes (a commit:
// drag end, add, remove), never per render or per drag frame. An empty
// network uses its own stored position and the minimum size.

/**
 * Graph node types that are networks (plan D7). The regime network is
 * `regime_net`; `regime` is the terminal (a card, never a frame).
 */
export const NETWORK_TYPES: ReadonlySet<string> = new Set(['subnet', 'output_group', 'regime_net'])
/** Boundary node types: the ports of a network, inside it. */
export const BOUNDARY_TYPES: ReadonlySet<string> = new Set(['subnet_input', 'subnet_output'])

/** The React Flow node type of a network frame (nodes/NetworkFrame.tsx). */
export const FRAME_RF_TYPE = 'nbNetworkFrame'

/** Padding inside a frame, on every side (S31). */
export const FRAME_PAD = 16
/** Extra room at the top of a frame for its tab (S31). */
export const FRAME_TAB_ROOM = 32
/** The smallest frame (S31). */
export const FRAME_MIN = { w: 320, h: 120 } as const

/** Handle id prefix for a boundary node's port on the frame edge. */
export const BOUNDARY_HANDLE_PREFIX = 'bnd:'

/** True for a network node type. */
export function isNetworkType(type: string | undefined): boolean {
  return !!type && NETWORK_TYPES.has(type)
}

// ---------------------------------------------------------------------------
// Card or frame, and the network on screen (W6 6.C, FA1, S37, S38)
// ---------------------------------------------------------------------------
//
// A network draws either as a frame around its children (S31) or as one
// card (S38), by `Node.meta.view` ("frame" | "card"; missing = "frame",
// decisions-pre). A card's children are not drawn; diving into it (or into
// a frame) shows that network's children only, with its boundary nodes as
// half-height cards.
//
// The "layout scope" is the network the canvas shows (null: the root). The
// canvas sets it (`setLayoutScope`) so code that lays out frames without
// knowing about the dive (the frame drag plugin calls
// `computeFrameLayouts(nodes, sizeOf)`) sees the dived view: the network on
// screen counts as a frame that covers the whole canvas (a child dragged
// anywhere stays inside it), and frames outside it, or hidden inside a
// card, are not there. Callers that know the scope pass it explicitly.

/** The React Flow node type of a network drawn as a card (nodes/SubnetNode.tsx). */
export const CARD_RF_TYPE = 'nbSubnet'
/** The React Flow node type of a boundary node inside a dived network (nodes/BoundaryNode.tsx). */
export const BOUNDARY_RF_TYPE = 'nbBoundary'

/** A rectangle that holds every point the canvas can show (the dived network, as a frame). */
const SCOPE_RECT = { x: -1e9, y: -1e9, w: 2e9, h: 2e9 } as const

let layoutScope: string | null = null

/** Canvas only: the network on screen (null: the root). See the note above. */
export function setLayoutScope(id: string | null): void {
  layoutScope = id
}

/** The network on screen as the canvas last set it (null: the root). */
export function getLayoutScope(): string | null {
  return layoutScope
}

type NetworkLike = { type: string; id?: string; meta?: GraphNode['meta'] } | undefined

/** True for a network node drawn as a card (`meta.view === "card"`, FA1). */
export function isCardNetwork(node: NetworkLike): boolean {
  return !!node && NETWORK_TYPES.has(node.type) && node.meta?.view === 'card'
}

/** How a network node draws: "frame" (default) or "card" (FA1). */
export function networkViewOf(node: NetworkLike): 'frame' | 'card' {
  return isCardNetwork(node) ? 'card' : 'frame'
}

/**
 * True when this node is a network drawn as a frame: a network not in card
 * view. The network on screen (`scope`, default: the layout scope) always
 * counts as a frame: its children are inside it.
 */
export function isFrameNetwork(node: NetworkLike, scope: string | null = layoutScope): boolean {
  if (!node || !NETWORK_TYPES.has(node.type)) return false
  if (scope !== null && node.id !== undefined && node.id === scope) return true
  return node.meta?.view !== 'card'
}

/**
 * Child ids per parent id ('' for the root), built once per `nodes` object
 * (S37 "must not": no scan of every node per render). Commits share
 * unchanged objects, so a new index is built once per commit at most.
 */
const childIndexCache = new WeakMap<Graph['nodes'], Map<string, string[]>>()
export function childrenByParent(nodes: Graph['nodes']): Map<string, string[]> {
  const hit = childIndexCache.get(nodes)
  if (hit) return hit
  const out = new Map<string, string[]>()
  for (const n of Object.values(nodes)) {
    const key = n.parent ?? ''
    const list = out.get(key)
    if (list) list.push(n.id)
    else out.set(key, [n.id])
  }
  childIndexCache.set(nodes, out)
  return out
}

/** How many nodes a network holds directly, not counting its boundary nodes. */
export function networkChildCount(nodes: Graph['nodes'], id: string): number {
  let count = 0
  for (const c of childrenByParent(nodes).get(id) ?? []) {
    if (nodes[c] && !BOUNDARY_TYPES.has(nodes[c].type)) count += 1
  }
  return count
}

/** How a node shows with `scope` on screen. */
export type DrawKind = 'node' | 'frame' | 'card' | 'boundary'

/**
 * Every node drawn with `scope` on screen, and how: the scope's children
 * ('boundary' for its boundary nodes when dived), and inside each frame
 * among them, that frame's children (its boundary nodes are its ports, not
 * drawn). A card's insides, the scope itself, and everything outside it are
 * not drawn. One pass, cached per `nodes` object and scope.
 */
const drawnCache = new WeakMap<Graph['nodes'], Map<string, Map<string, DrawKind>>>()
export function drawnNodes(nodes: Graph['nodes'], scope: string | null): Map<string, DrawKind> {
  let byScope = drawnCache.get(nodes)
  const key = scope ?? ''
  const hit = byScope?.get(key)
  if (hit) return hit
  const out = new Map<string, DrawKind>()
  const kids = childrenByParent(nodes)
  const kindOf = (n: GraphNode): DrawKind =>
    NETWORK_TYPES.has(n.type) ? (isCardNetwork(n) ? 'card' : 'frame') : 'node'
  // Breadth first from the scope; a frame opens its children, a card does not.
  const queue: string[] = []
  const top = [...(kids.get(key) ?? [])]
  // At the root, a node whose parent is missing or is not a network is
  // drawn at the top level, as before W6 (the backend reports it).
  if (scope === null) {
    for (const n of Object.values(nodes)) {
      if (n.parent && !NETWORK_TYPES.has(nodes[n.parent]?.type ?? '')) top.push(n.id)
    }
  }
  for (const id of top) {
    const n = nodes[id]
    if (!n) continue
    // Boundary nodes at the root are drawn as plain cards (they are invalid there).
    out.set(id, scope !== null && BOUNDARY_TYPES.has(n.type) ? 'boundary' : kindOf(n))
    if (out.get(id) === 'frame') queue.push(id)
  }
  for (let i = 0; i < queue.length && i < 100000; i++) {
    const fid = queue[i]
    for (const id of kids.get(fid) ?? []) {
      const n = nodes[id]
      if (!n || out.has(id) || id === scope) continue
      if (BOUNDARY_TYPES.has(n.type)) continue // a frame port
      const k = kindOf(n)
      out.set(id, k)
      if (k === 'frame') queue.push(id)
    }
  }
  if (!byScope) {
    byScope = new Map()
    drawnCache.set(nodes, byScope)
  }
  byScope.set(key, out)
  return out
}

/**
 * The frame this node sits in, or null at the root (or when its parent is
 * missing or is not a network: such a node is drawn unparented). With a
 * network on screen, its direct children sit in it.
 */
export function frameParentOf(nodes: Graph['nodes'], node: Pick<GraphNode, 'parent'>, scope: string | null = layoutScope): string | null {
  const pid = node.parent
  if (!pid) return null
  return isFrameNetwork(nodes[pid], scope) ? pid : null
}

/** True for a boundary node that is drawn as a frame port (not as a card). */
export function isHiddenBoundary(nodes: Graph['nodes'], node: Pick<GraphNode, 'type' | 'parent'>, scope: string | null = layoutScope): boolean {
  if (!BOUNDARY_TYPES.has(node.type)) return false
  const pid = frameParentOf(nodes, node, scope)
  return pid !== null && pid !== scope
}

/**
 * The port a `subnet_input` stands for: its `port` param (a whole number,
 * default 0), as the backend reads it (kernel/flatten.py). Null when the
 * param is not a whole number 0 or above (the backend reports it).
 */
export function boundaryPortOf(n: Pick<GraphNode, 'params'>): number | null {
  const v = n.params?.port ?? 0
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null
}

/**
 * A network's `subnet_input` children in port order. Network input port
 * `in<k>` is the boundary node whose `port` param is k (plan D7; backend
 * flatten). Nodes with a bad port, or a port another node already took, are
 * left out (validate reports them).
 */
export function boundaryInputs(nodes: Graph['nodes'], networkId: string): GraphNode[] {
  const byPort = new Map<number, GraphNode>()
  for (const id of childrenByParent(nodes).get(networkId) ?? []) {
    const n = nodes[id]
    if (!n || n.type !== 'subnet_input') continue
    const k = boundaryPortOf(n)
    if (k === null || byPort.has(k)) continue
    byPort.set(k, n)
  }
  return [...byPort.entries()].sort((a, b) => a[0] - b[0]).map(e => e[1])
}

/** The network's `subnet_output` child, or null. */
export function boundaryOutput(nodes: Graph['nodes'], networkId: string): GraphNode | null {
  for (const id of childrenByParent(nodes).get(networkId) ?? []) {
    if (nodes[id]?.type === 'subnet_output') return nodes[id]
  }
  return null
}

/** A ghost card (S32a): the place where a required terminal is missing. */
export const GHOST = { w: 118, h: 46, gap: 12 } as const

/** A required terminal a group does not have yet. */
export interface MissingTerminal {
  /** Terminal node type: `entry`, `exit` or `regime`. */
  type: 'entry' | 'exit' | 'regime'
  /** The side, in a `regime_switch` group only. */
  side: 'long' | 'short' | null
  /** Ghost label: `+ exit`, `+ entry (long)`, `+ regime`. */
  label: string
}

/**
 * The required terminals an Output Group is missing, in the fixed terminal
 * order (S32a): `entry` and `exit`; in a `regime_switch` group one of each
 * per side plus `regime`. The server's `missing_terminal` diagnostic is the
 * authority; this only places the ghost cards.
 */
export function missingTerminals(nodes: Graph['nodes'], groupId: string): MissingTerminal[] {
  const group = nodes[groupId]
  if (!group || group.type !== 'output_group') return []
  const kids = Object.values(nodes).filter(n => n.parent === groupId)
  // A terminal with no stored side counts as long, as the server reads it
  // (nodes_groups._side, UX-14).
  const sideOf = (k: GraphNode) => (k.params?.side === 'short' ? 'short' : 'long')
  const has = (type: string, side: string | null) =>
    kids.some(k => k.type === type && (side === null || sideOf(k) === side))
  const out: MissingTerminal[] = []
  if (group.params?.direction === 'regime_switch') {
    for (const type of ['entry', 'exit'] as const) {
      for (const side of ['long', 'short'] as const) {
        if (!has(type, side)) out.push({ type, side, label: `+ ${type} (${side})` })
      }
    }
    if (!has('regime', null)) out.push({ type: 'regime', side: null, label: '+ regime' })
  } else {
    for (const type of ['entry', 'exit'] as const) {
      if (!has(type, null)) out.push({ type, side: null, label: `+ ${type}` })
    }
  }
  return out
}

/** One input port on a frame's top edge. */
export interface FramePort {
  /** The `subnet_input` node behind the port. */
  boundaryId: string
  /** Target handle id for the wire from outside: `in<k>`, k = the boundary's `port` param. */
  handle: string
  /** Port label: the boundary node's name (`in0`, or a renamed `signal`). */
  label: string
  /** Port centre, from the frame's left edge, in flow units. */
  x: number
}

/** Where a frame is and what is on its edge. Positions are absolute flow units. */
export interface FrameLayout {
  id: string
  x: number
  y: number
  w: number
  h: number
  inputs: FramePort[]
  /** The `subnet_output` node behind the bottom port (subnets only), or null. */
  outputId: string | null
  /** Children drawn as cards or frames (boundary nodes are not counted). */
  childCount: number
  /** Output Groups: required terminals that are missing (ghost cards in the bottom row). */
  missing: MissingTerminal[]
}

/** A node's box size for frame layout. */
export type NodeSizeOf = (n: GraphNode) => { w: number; h: number }

/**
 * A `sizeOf` for `computeFrameLayouts` from measured sizes (React Flow's
 * `node.measured`, collected by the canvas): the measured box when there is
 * one, `DEFAULT_NODE_SIZE` until then (FE-05).
 */
export function measuredSizeOf(
  sizes: ReadonlyMap<string, { w: number; h: number }> | ((id: string) => { w: number; h: number } | null | undefined) | null | undefined,
): NodeSizeOf {
  if (!sizes) return () => DEFAULT_NODE_SIZE
  const get = typeof sizes === 'function' ? sizes : (id: string) => sizes.get(id)
  return n => {
    const m = get(n.id)
    return m && m.w > 0 && m.h > 0 ? m : DEFAULT_NODE_SIZE
  }
}

/**
 * The rectangle and ports of every frame in the graph. A frame holds its
 * children's boxes (`DEFAULT_NODE_SIZE` until measured; nested frames by
 * their own rectangle) plus padding, and never gets smaller than
 * `FRAME_MIN`. Runs in one pass over the nodes; call it when the graph
 * changes, not per render.
 */
export function computeFrameLayouts(
  nodes: Graph['nodes'],
  sizeOf: NodeSizeOf = () => DEFAULT_NODE_SIZE,
  scope: string | null = layoutScope,
): Map<string, FrameLayout> {
  const childrenOf = new Map<string, GraphNode[]>()
  for (const n of Object.values(nodes)) {
    const pid = frameParentOf(nodes, n, scope)
    if (!pid || BOUNDARY_TYPES.has(n.type)) continue
    const list = childrenOf.get(pid)
    if (list) list.push(n)
    else childrenOf.set(pid, [n])
  }
  const out = new Map<string, FrameLayout>()
  const visiting = new Set<string>()
  const layoutOf = (net: GraphNode): FrameLayout => {
    const done = out.get(net.id)
    if (done) return done
    visiting.add(net.id)
    const kids = childrenOf.get(net.id) ?? []
    let x: number
    let y: number
    let w: number
    let h: number
    if (kids.length === 0) {
      ;[x, y] = net.position
      w = FRAME_MIN.w
      h = FRAME_MIN.h
    } else {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const k of kids) {
        // A child frame counts by its own rectangle (a parent loop is
        // refused by the backend; the guard only stops endless recursion).
        const r = isFrameNetwork(k, scope) && !visiting.has(k.id)
          ? layoutOf(k)
          : { x: k.position[0], y: k.position[1], ...sizeOf(k) }
        minX = Math.min(minX, r.x)
        minY = Math.min(minY, r.y)
        maxX = Math.max(maxX, r.x + r.w)
        maxY = Math.max(maxY, r.y + r.h)
      }
      x = minX - FRAME_PAD
      y = minY - FRAME_PAD - FRAME_TAB_ROOM
      w = Math.max(FRAME_MIN.w, maxX - minX + 2 * FRAME_PAD)
      h = Math.max(FRAME_MIN.h, maxY - minY + 2 * FRAME_PAD + FRAME_TAB_ROOM)
    }
    // Ghost cards for missing terminals get their own row at the bottom.
    const missing = missingTerminals(nodes, net.id)
    if (missing.length > 0) {
      h += GHOST.h + GHOST.gap
      w = Math.max(w, 2 * FRAME_PAD + missing.length * (GHOST.w + GHOST.gap) - GHOST.gap)
    }
    const ins = boundaryInputs(nodes, net.id)
    const inputs: FramePort[] = ins.map((b, i) => {
      const k = boundaryPortOf(b) ?? i
      return {
        boundaryId: b.id,
        handle: `in${k}`,
        label: b.name || `in${k}`,
        x: Math.round((w * (i + 1)) / (ins.length + 1)),
      }
    })
    // Only a subnet (and a regime network) has an output; a group has none.
    const outNode = net.type === 'output_group' ? null : boundaryOutput(nodes, net.id)
    const layout: FrameLayout = {
      id: net.id, x, y, w, h, inputs, outputId: outNode?.id ?? null, childCount: kids.length, missing,
    }
    visiting.delete(net.id)
    out.set(net.id, layout)
    return layout
  }
  // Only frames that are drawn: not the ones inside a card, or outside the
  // network on screen.
  for (const [id, kind] of drawnNodes(nodes, scope)) if (kind === 'frame') layoutOf(nodes[id])
  // The network on screen is the whole canvas (see setLayoutScope).
  if (scope !== null && nodes[scope]) {
    out.set(scope, {
      id: scope, ...SCOPE_RECT, inputs: [], outputId: null, childCount: networkChildCount(nodes, scope), missing: [],
    })
  }
  return out
}

/**
 * The absolute top-left a node is drawn at: a frame's computed corner, or
 * the node's stored position.
 */
export function drawnPositionOf(n: GraphNode, layouts: Map<string, FrameLayout>): FlowXY {
  const l = layouts.get(n.id)
  return l ? { x: l.x, y: l.y } : { x: n.position[0], y: n.position[1] }
}

/** How many drawn frames this node sits inside (0 at the top of the network on screen). */
function frameDepth(nodes: Graph['nodes'], n: GraphNode, drawn: Map<string, DrawKind>, scope: string | null): number {
  let d = 0
  let cur: GraphNode | undefined = n
  while (cur && d < 64) {
    const pid: string | null = cur.parent
    if (!pid || pid === scope || drawn.get(pid) !== 'frame') break
    d += 1
    cur = nodes[pid]
  }
  return d
}

/** An Output Group's primary Ticker, as its header shows it (S32a). */
export interface GroupTicker {
  id: string
  name: string
  symbol: string
  interval: string
}

/**
 * The primary Ticker of an Output Group: the node at its `params.ticker`
 * path, and only that (S32a: never wire order or file order). Null when the
 * path is unset or does not name a Ticker.
 */
export function groupTickerOf(nodes: Graph['nodes'], group: GraphNode): GroupTicker | null {
  // The shared resolver (group, parent, root), as the server (FE-10).
  const t = resolveGroupTicker({ nodes }, group)
  if (!t) return null
  const symbol = typeof t.params?.symbol === 'string' ? t.params.symbol.toUpperCase() : ''
  const interval = typeof t.params?.interval === 'string' ? t.params.interval : ''
  return { id: t.id, name: t.name, symbol, interval }
}

/** What a frame's React Flow node carries (nodes/NetworkFrame.tsx reads it). */
export interface FrameNodeData extends BaseNodeData {
  frame: FrameLayout
  /** Output Groups: the primary Ticker named by `params.ticker`, or null. */
  groupTicker: GroupTicker | null
}

/** One input port on a network card: the boundary node behind it. */
export interface CardPort {
  boundaryId: string
  /** Target handle id: `in<k>`, k = the boundary's `port` param. */
  handle: string
  /** The boundary node's name (`in0`, or a renamed `signal`). */
  label: string
}

/** What a network card shows besides the node (S38). */
export interface CardInfo {
  /** One per `subnet_input` child, in port order. */
  inputs: CardPort[]
  /** The `subnet_output` child, or null (no output port is drawn). */
  outputId: string | null
  /** Children, not counting boundary nodes. */
  childCount: number
  /** Set on an asset instance (W6): which asset and version, and whether it is locked. */
  asset: { name: string; version: number; locked: boolean } | null
}

/** What a card's React Flow node carries (nodes/SubnetNode.tsx reads it). */
export interface CardNodeData extends BaseNodeData {
  card: CardInfo
}

/** What a boundary card's React Flow node carries (nodes/BoundaryNode.tsx reads it). */
export interface BoundaryNodeData extends BaseNodeData {
  boundary: { kind: 'input' | 'output'; port: number | null }
}

/**
 * What the card mapper needs for a locked asset instance (FE-02): the
 * graph's wires (their ports stand in until the asset file loads) and the
 * asset network source (networkNav.assetNetworkOf). A locked instance
 * stores no children, so its ports come from these, not from `nodes`.
 */
export interface CardContext {
  wires?: readonly GraphWire[]
  assetNetwork?: (net: GraphNode) => { nodes: Graph['nodes']; wires: readonly GraphWire[] } | null
}

/**
 * A locked instance's ports: the asset network's top-level boundary nodes
 * (parent null in the file), plus every port the instance's own wires use,
 * so a wire never loses its handle while the file loads or after the asset
 * changed. Boundary ids are composite (`<inst>::<child>`), the ids the
 * backend gives an expanded instance's nodes.
 */
function lockedCardPorts(net: GraphNode, ctx: CardContext | undefined): Pick<CardInfo, 'inputs' | 'outputId' | 'childCount'> {
  const byPort = new Map<number, CardPort>()
  let outputId: string | null = null
  let childCount = 0
  const hasOutput = net.type !== 'output_group'
  const an = ctx?.assetNetwork?.(net) ?? null
  if (an) {
    boundaryInputs(an.nodes, '').forEach((b, i) => {
      const k = boundaryPortOf(b) ?? i
      byPort.set(k, { boundaryId: `${net.id}::${b.id}`, handle: `in${k}`, label: b.name || `in${k}` })
    })
    const o = hasOutput ? boundaryOutput(an.nodes, '') : null
    if (o) outputId = `${net.id}::${o.id}`
    for (const n of Object.values(an.nodes)) if (!n.parent && !BOUNDARY_TYPES.has(n.type)) childCount += 1
  }
  for (const w of ctx?.wires ?? []) {
    if (w.to === net.id) {
      const m = /^in(\d+)$/.exec(w.to_port)
      const k = m ? Number(m[1]) : -1
      if (k >= 0 && !byPort.has(k)) byPort.set(k, { boundaryId: `${net.id}::in${k}`, handle: `in${k}`, label: `in${k}` })
    }
    if (w.from === net.id && hasOutput && outputId === null) outputId = `${net.id}::out`
  }
  const inputs = [...byPort.entries()].sort((a, b) => a[0] - b[0]).map(e => e[1])
  return { inputs, outputId, childCount }
}

/** The card facts of a network node (S38): its ports, child count and asset. */
export function cardInfoOf(nodes: Graph['nodes'], net: GraphNode, ctx?: CardContext): CardInfo {
  const ref = net.asset_ref
  const asset = ref && typeof ref.name === 'string' && typeof ref.version === 'number'
    ? { name: ref.name, version: ref.version, locked: net.locked === true }
    : null
  if (asset?.locked) return { ...lockedCardPorts(net, ctx), asset }
  const inputs = boundaryInputs(nodes, net.id).map((b, i) => {
    const k = boundaryPortOf(b) ?? i
    return { boundaryId: b.id, handle: `in${k}`, label: b.name || `in${k}` }
  })
  // A group has no output port, as in its frame.
  const out = net.type === 'output_group' ? null : boundaryOutput(nodes, net.id)
  return { inputs, outputId: out?.id ?? null, childCount: networkChildCount(nodes, net.id), asset }
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

export function createNodeMapper(): (
  nodes: Graph['nodes'],
  editable: boolean,
  sizes?: ReadonlyMap<string, { w: number; h: number }>,
  scope?: string | null,
  cardCtx?: CardContext,
) => RFNode[] {
  // Keyed on the node object itself (EA-6): commits share unchanged nodes,
  // so a node whose object did not change keeps its React Flow node and
  // data. The renderer gets the whole node (`data.node`), so a later wave's
  // field needs no change here. Its path is in the key too: a rename of the
  // node (or of a parent) changes it. So is everything the node takes from
  // its frame (W5): the frame's corner (its children are drawn relative to
  // it) and, for a terminal, the group's direction. A card's ports and
  // child count (W6) come from its children, so they are in the key too.
  const cache = new Map<string, { node: GraphNode; sig: string; rfNode: RFNode }>()
  let prev: RFNode[] | null = null
  return (nodes, editable, sizes, scope = null, cardCtx) => {
    const seen = new Set<string>()
    // Only the network on screen is drawn (S37): its children, the inside
    // of every frame among them, and its boundary nodes as cards.
    const drawn = drawnNodes(nodes, scope)
    // Measured card sizes (FE-05): a tall card stretches its frame. The
    // frame's rectangle is in the cache signature, so a size change redraws
    // the frame (and moves its ghost row) without touching other nodes.
    const layouts = computeFrameLayouts(nodes, measuredSizeOf(sizes), scope)
    const entries: Array<{ depth: number; i: number; rfNode: RFNode }> = []
    let nested = false
    let i = 0
    for (const n of Object.values(nodes)) {
      const kind = drawn.get(n.id)
      if (!kind) continue
      seen.add(n.id)
      const path = safeNodePath(nodes, n.id)
      // The frame it is drawn in; the network on screen is the canvas itself.
      const pid = n.parent ?? null
      const parentId = pid && pid !== scope && drawn.get(pid) === 'frame' ? pid : null
      const parentLayout = parentId ? layouts.get(parentId) ?? null : null
      const parentAbs = parentLayout ? { x: parentLayout.x, y: parentLayout.y } : null
      const parentNode = pid ? nodes[pid] : undefined
      const groupDirection = parentNode?.type === 'output_group' && typeof parentNode.params?.direction === 'string'
        ? parentNode.params.direction
        : null
      const layout = kind === 'frame' ? layouts.get(n.id) ?? null : null
      const groupTicker = n.type === 'output_group' && kind === 'frame' ? groupTickerOf(nodes, n) : null
      // `card` is in the signature below, so a locked card redraws when its
      // asset file lands or a wire changes its ports (FE-02).
      const card = kind === 'card' ? cardInfoOf(nodes, n, cardCtx) : null
      const boundary = kind === 'boundary'
        ? { kind: n.type === 'subnet_output' ? 'output' as const : 'input' as const, port: n.type === 'subnet_input' ? boundaryPortOf(n) : null }
        : null
      const depth = parentId ? frameDepth(nodes, n, drawn, scope) : 0
      if (depth > 0) nested = true
      const sig = JSON.stringify([editable, kind, path, parentId, parentAbs, groupDirection, layout, groupTicker, card, boundary])
      const cached = cache.get(n.id)
      if (cached && cached.node === n && cached.sig === sig) {
        entries.push({ depth, i: i++, rfNode: cached.rfNode })
        continue
      }
      const base: BaseNodeData = {
        backendType: n.type,
        catalog: CATALOG_BY_NAME.get(n.type) ?? null,
        params: n.params,
        display: n.display,
        bypass: n.bypass,
        name: n.name,
        nodePath: path,
        editable,
        node: n,
        groupDirection,
      }
      const abs = drawnPositionOf(n, layouts)
      let data: BaseNodeData = base
      if (layout) data = { ...base, frame: layout, groupTicker } as FrameNodeData
      else if (card) data = { ...base, card } as CardNodeData
      else if (boundary) data = { ...base, boundary } as BoundaryNodeData
      const rfNode: RFNode = {
        id: n.id,
        type: kind === 'card' ? CARD_RF_TYPE : kind === 'boundary' ? BOUNDARY_RF_TYPE : rfTypeFor(n.type),
        position: rfPositionOf([abs.x, abs.y], parentAbs),
        data,
        draggable: editable,
        selectable: true,
      }
      if (parentId) rfNode.parentId = parentId
      if (layout) {
        // Frames sit under wires and cards, with the network boxes (S31).
        rfNode.width = layout.w
        rfNode.height = layout.h
        rfNode.zIndex = -1
        // Empty frame space belongs to the canvas (marquee, pane clicks):
        // only the tab, border and ports take the pointer (NetworkFrame.tsx).
        rfNode.style = { pointerEvents: 'none' }
      }
      cache.set(n.id, { node: n, sig, rfNode })
      entries.push({ depth, i: i++, rfNode })
    }
    // Evict removed nodes so the cache doesn't grow unbounded.
    for (const id of Array.from(cache.keys())) {
      if (!seen.has(id)) cache.delete(id)
    }
    // React Flow needs a parent before its children: shallow nodes first,
    // file order within a depth (a graph with no networks keeps its order).
    if (nested) entries.sort((a, b) => a.depth - b.depth || a.i - b.i)
    prev = stableArray(prev, entries.map(e => e.rfNode))
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
  scope: string | null = null,
): Record<string, LabelPlacement> {
  const rects: Record<string, Rect> = {}
  const portCounts: Record<string, number> = {}
  const dynamicNodes = new Set<string>()
  // A frame's box is its computed rectangle, not its stored position (S31).
  const layouts = computeFrameLayouts(graph.nodes, measuredSizeOf(sizes), scope)
  // Only what is drawn takes room (S37: the network on screen).
  const drawn = drawnNodes(graph.nodes, scope)
  for (const n of Object.values(graph.nodes)) {
    if (!drawn.has(n.id)) continue
    const size = sizes.get(n.id) ?? DEFAULT_NODE_SIZE
    const frame = layouts.get(n.id)
    rects[n.id] = frame
      ? { x: frame.x, y: frame.y, w: frame.w, h: frame.h }
      : { x: n.position[0], y: n.position[1], w: size.w, h: size.h }
    portCounts[n.id] = portsOf(n.type, connectedPorts.get(n.id) ?? []).length
    if (portsSpecOf(n.type)?.dynamic) dynamicNodes.add(n.id)
  }
  const items = graph.wires.filter(w => edgeEndsOf(graph.nodes, w, scope) !== null).map(w => ({
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
  /** The network on screen (null: the root). Only its wires are drawn (S37). */
  scope?: string | null
}

/** Maps graph wires to 'attr' edges, with the same per-item cache as nodes. */
export function createEdgeMapper(): (input: EdgeMapInput) => RFEdge[] {
  const cache = new Map<string, { sig: string; rfEdge: RFEdge }>()
  let prev: RFEdge[] | null = null
  return ({ graph, selectedWireId, labels, placements, lowZoom, diagByWire, scope = null }) => {
    const seen = new Set<string>()
    const result: RFEdge[] = []
    for (const w of graph.wires) {
      // A wire is drawn only when both of its ends are (S37, S38).
      const ends = edgeEndsOf(graph.nodes, w, scope)
      if (!ends) continue
      seen.add(w.id)
      const selected = w.id === selectedWireId
      const label = labels[w.id]
      const place = placements[w.id] ?? { t: 0.5, dx: 0, hidden: null }
      const consumer = graph.nodes[w.to]
      const portLabel = portsSpecOf(consumer?.type)?.ports[Number(w.to_port.slice(2))]?.label ?? w.to_port
      // A wire from or to a frame's boundary node is drawn from or to that
      // port on the frame edge (S31): the boundary node has no card.
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
      const sig = `${ends.source}|${ends.sourceHandle}|${ends.target}|${ends.targetHandle}|${selected ? 1 : 0}|${JSON.stringify(data)}`
      const cached = cache.get(w.id)
      if (cached && cached.sig === sig) {
        result.push(cached.rfEdge)
        continue
      }
      const rfEdge: RFEdge = {
        id: w.id,
        // Handle ids: the one output is 'out'; inputs are 'in0', 'in1', ...
        // A frame's boundary ports are 'bnd:<boundary node id>'.
        source: ends.source,
        target: ends.target,
        sourceHandle: ends.sourceHandle,
        targetHandle: ends.targetHandle,
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

/**
 * The React Flow ends of a wire, or null when the wire is not drawn with
 * `scope` on screen. A wire out of a frame's `subnet_input` starts at that
 * port on the frame's top edge; a wire into a frame's `subnet_output` ends at
 * the port on its bottom edge. Inside a dived network its own boundary
 * nodes are cards, so their wires run node to node. Every other wire runs
 * node to node (a wire into a network node, frame or card, ends at its
 * `in<k>` port). A wire with an end inside a card, or outside the network on
 * screen, is not drawn.
 */
export function edgeEndsOf(
  nodes: Graph['nodes'],
  w: Pick<Graph['wires'][number], 'from' | 'to' | 'to_port'>,
  scope: string | null = null,
): { source: string; sourceHandle: string; target: string; targetHandle: string } | null {
  const drawn = drawnNodes(nodes, scope)
  const from = nodes[w.from]
  const to = nodes[w.to]
  // A boundary node of a drawn frame (not the network on screen) is a port on it.
  const portFrame = (n: GraphNode | undefined, type: string): string | null => {
    if (!n || n.type !== type || !n.parent || n.parent === scope) return null
    return drawn.get(n.parent) === 'frame' ? n.parent : null
  }
  const fromFrame = portFrame(from, 'subnet_input')
  const toFrame = portFrame(to, 'subnet_output')
  if (!fromFrame && !drawn.has(w.from)) return null
  if (!toFrame && !drawn.has(w.to)) return null
  return {
    source: fromFrame ?? w.from,
    sourceHandle: fromFrame ? `${BOUNDARY_HANDLE_PREFIX}${w.from}` : 'out',
    target: toFrame ?? w.to,
    targetHandle: toFrame ? `${BOUNDARY_HANDLE_PREFIX}${w.to}` : w.to_port,
  }
}
