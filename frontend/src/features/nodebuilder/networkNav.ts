/**
 * Moving between networks and showing them as cards or frames (W6 item
 * 6.C, specs S37, S38, FA1).
 *
 * - Dive (`I`, Enter, a double-click on a frame tab or a card header) shows
 *   one network's children; up (`U`) goes back to its parent and selects the
 *   network you left; Shift+U goes to the root. The breadcrumb uses the same
 *   moves.
 * - X / Shift+X switch a network between its frame view and its card view
 *   (`Node.meta.view`), as one undoable commit.
 * - A locked asset instance stores no children. Its definition comes from
 *   the asset library through a registered source (`registerAssetNetworkSource`,
 *   item 6.D wires it to graphLibrary.ts); the dived view shows that copy
 *   read-only.
 *
 * Plain functions over the store and the graph on screen, so commands, the
 * breadcrumb and the canvas share them. Nothing here imports a command
 * module.
 */

import { useSyncExternalStore } from 'react'
import type { Graph, GraphNode, GraphWire } from '../../api/nodebuilder'
import { nodePath } from './paths'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import { currentParentId } from './store/view'
import {
  BOUNDARY_TYPES,
  computeFrameLayouts,
  measuredSizeOf,
  NETWORK_TYPES,
  networkChildCount,
  type NodeSizeOf,
} from './rfMapping'
import type { CanvasCtx } from './canvasPlugins'
import { getActiveCanvas } from './screen'

// ── Facts about networks ────────────────────────────────────────────────────

/** True for a network node (subnet, output_group, regime_net). */
export function isNetworkNode(n: Pick<GraphNode, 'type'> | undefined | null): boolean {
  return !!n && NETWORK_TYPES.has(n.type)
}

/** True for a locked asset instance (W6): its children come from the library. */
export function isLockedInstance(n: GraphNode | undefined | null): boolean {
  return !!n && n.type === 'subnet' && n.locked === true && !!n.asset_ref
}

/** The locked asset instance `id` sits in (itself included), or null. */
export function lockedAncestorOf(graph: Pick<Graph, 'nodes'>, id: string | null): string | null {
  for (let cur = id, i = 0; cur && i < 64; i++) {
    const n = graph.nodes[cur]
    if (!n) return null
    if (isLockedInstance(n)) return cur
    cur = n.parent ?? null
  }
  return null
}

/** The network a node sits in (its parent, when that is a network), or null at the root. */
export function parentNetworkOf(graph: Pick<Graph, 'nodes'>, id: string): string | null {
  const p = graph.nodes[id]?.parent ?? null
  return p && isNetworkNode(graph.nodes[p]) ? p : null
}

/** The network on screen in `graph` (null: the root). */
export function screenNetworkId(graph: Graph | null, s: NodeBuilderState = useNodeBuilderStore.getState()): string | null {
  if (!graph) return null
  const id = currentParentId(s, graph)
  return id && isNetworkNode(graph.nodes[id]) ? id : null
}

/** The absolute path of a network, '/' for the root. */
export function networkPath(graph: Pick<Graph, 'nodes'>, id: string | null): string {
  if (!id) return '/'
  try {
    return nodePath(graph, id)
  } catch {
    return '/'
  }
}

/** The status bar text inside a network (S37): `in /long_leg/regime`, or '' at the root. */
export function networkStatusText(graph: Pick<Graph, 'nodes'> | null, id: string | null): string {
  if (!graph || !id) return ''
  return `in ${networkPath(graph, id)}`
}

/** One breadcrumb (S37). */
export interface Crumb {
  /** The network node id, or null for the root. */
  id: string | null
  /** `/` for the root, else the network name. */
  label: string
  /** Full path in mono, for the tooltip. */
  path: string
  /** Node type (subnet, output_group, regime_net), or null for the root. */
  type: string | null
  /** A locked asset instance shows a lock glyph. */
  locked: boolean
}

/** The crumbs from the root to the network `id` (one crumb, the root, when null). */
export function crumbsOf(graph: Pick<Graph, 'nodes'> | null, id: string | null): Crumb[] {
  const chain: GraphNode[] = []
  if (graph) {
    for (let cur = id, i = 0; cur && i < 64; i++) {
      const n = graph.nodes[cur]
      if (!n) break
      chain.unshift(n)
      cur = n.parent ?? null
    }
  }
  const root: Crumb = { id: null, label: '/', path: '/', type: null, locked: false }
  return [
    root,
    ...chain.map(n => ({
      id: n.id,
      label: n.name,
      path: networkPath(graph!, n.id),
      type: n.type,
      locked: isLockedInstance(n),
    })),
  ]
}

// ── Moving ──────────────────────────────────────────────────────────────────

function graphOnScreen(canvas: CanvasCtx | null | undefined): Graph | null {
  const c = canvas ?? getActiveCanvas()
  try {
    return c?.graph() ?? useNodeBuilderStore.getState().graph
  } catch {
    return useNodeBuilderStore.getState().graph
  }
}

/** Dive into the network `id` (S37). False when it is not a network. */
export function diveInto(id: string, canvas?: CanvasCtx | null): boolean {
  const graph = graphOnScreen(canvas)
  if (!graph || !isNetworkNode(graph.nodes[id])) return false
  // A network inside a locked asset is a library copy, not part of the graph.
  if (id.includes('::')) {
    useNodeBuilderStore.getState().showFlash('Unlock the asset to open the networks inside it')
    return false
  }
  useNodeBuilderStore.getState().enterNetwork(id, { graph })
  return true
}

/**
 * Up one level (S37): the parent network shows, and the network you left
 * becomes the selected node, framed when it is off screen. False at the root.
 */
export function goUp(canvas?: CanvasCtx | null): boolean {
  const graph = graphOnScreen(canvas)
  const current = screenNetworkId(graph)
  if (!graph || !current) return false
  useNodeBuilderStore.getState().enterNetwork(parentNetworkOf(graph, current), { graph, select: current, reveal: 'offscreen' })
  return true
}

/** Shift+U: to the root, selecting the top-level network you were in. False at the root. */
export function goToRoot(canvas?: CanvasCtx | null): boolean {
  const graph = graphOnScreen(canvas)
  const current = screenNetworkId(graph)
  if (!graph || !current) return false
  let top = current
  for (let p = parentNetworkOf(graph, top), i = 0; p && i < 64; p = parentNetworkOf(graph, p), i++) top = p
  useNodeBuilderStore.getState().enterNetwork(null, { graph, select: top, reveal: 'offscreen' })
  return true
}

/**
 * A crumb click (S37): show that level. Going up selects the network on the
 * path you came down; a level that is not above you is a plain dive.
 */
export function goToCrumb(id: string | null, canvas?: CanvasCtx | null): boolean {
  const graph = graphOnScreen(canvas)
  if (!graph) return false
  const current = screenNetworkId(graph)
  if (id === current) return false
  // The child of `id` on the way down to where we are, if `id` is above us.
  let below: string | null = null
  for (let cur = current, i = 0; cur && i < 64; i++) {
    const p = parentNetworkOf(graph, cur)
    if (p === id) { below = cur; break }
    cur = p
  }
  if (id !== null && !isNetworkNode(graph.nodes[id])) return false
  useNodeBuilderStore.getState().enterNetwork(id, { graph, select: below, reveal: below ? 'offscreen' : undefined })
  return true
}

/** Crumb menu `Frame in parent` (S37): show the network's parent with the network selected and framed. */
export function frameInParent(id: string, canvas?: CanvasCtx | null): boolean {
  const graph = graphOnScreen(canvas)
  if (!graph || !isNetworkNode(graph.nodes[id])) return false
  useNodeBuilderStore.getState().enterNetwork(parentNetworkOf(graph, id), { graph, select: id, reveal: 'always' })
  return true
}

/** The selected network nodes on screen (primary first). */
export function selectedNetworks(s: NodeBuilderState, graph: Graph | null): string[] {
  if (!graph) return []
  const ids = s.selectedNodeId ? [s.selectedNodeId, ...s.selectedNodeIds.filter(i => i !== s.selectedNodeId)] : s.selectedNodeIds
  return ids.filter(id => isNetworkNode(graph.nodes[id]))
}

// ── Card or frame (FA1) ─────────────────────────────────────────────────────

/** Card sizes as the active canvas measured them (default size until measured). */
export function canvasSizeOf(canvas: CanvasCtx | null | undefined): NodeSizeOf {
  if (!canvas) return measuredSizeOf(null)
  return measuredSizeOf(id => {
    try {
      const m = canvas.rf.getNode(id)?.measured
      return m && m.width && m.height ? { w: m.width, h: m.height } : null
    } catch {
      return null
    }
  })
}

/** The frame rectangle a network has, or would have if it were shown as a frame. */
function frameRectOf(graph: Graph, id: string, sizeOf: NodeSizeOf): { x: number; y: number } | null {
  const n = graph.nodes[id]
  if (!n) return null
  const asFrame = { ...graph.nodes, [id]: { ...n, meta: { ...(n.meta ?? {}), view: 'frame' as const } } }
  const l = computeFrameLayouts(asFrame, sizeOf, n.parent ?? null).get(id)
  return l ? { x: l.x, y: l.y } : null
}

/** Every node inside `id` (children, their children, ...). */
function descendants(graph: Pick<Graph, 'nodes'>, id: string): string[] {
  const kids = new Map<string, string[]>()
  for (const n of Object.values(graph.nodes)) {
    if (!n.parent) continue
    const list = kids.get(n.parent)
    if (list) list.push(n.id)
    else kids.set(n.parent, [n.id])
  }
  const out: string[] = []
  const stack = [...(kids.get(id) ?? [])]
  while (stack.length > 0 && out.length < 100000) {
    const c = stack.pop()!
    out.push(c)
    stack.push(...(kids.get(c) ?? []))
  }
  return out
}

/**
 * Show these networks as cards or frames (FA1). A frame becomes a card at
 * the frame's top-left corner. A card becomes a frame whose corner lands on
 * the card: its children (stored in absolute coordinates) move by the
 * difference, keeping their places relative to each other (S31 "children
 * keep their relative positions"). Returns the same graph when nothing
 * changes.
 */
export function setNetworkView(graph: Graph, ids: readonly string[], view: 'frame' | 'card', sizeOf: NodeSizeOf = measuredSizeOf(null)): Graph {
  let g = graph
  for (const id of ids) {
    const n = g.nodes[id]
    if (!isNetworkNode(n)) continue
    // A locked instance has no children in the graph: as a frame it would be
    // an empty box with no ports, so it stays a card (FA1, FE-02).
    if (view === 'frame' && n.locked === true) continue
    const now = n.meta?.view === 'card' ? 'card' : 'frame'
    if (now === view) continue
    const rect = frameRectOf(g, id, sizeOf)
    const nodes = { ...g.nodes }
    if (view === 'card') {
      const pos: [number, number] = rect ? [rect.x, rect.y] : n.position
      nodes[id] = { ...n, position: pos, meta: { ...(n.meta ?? {}), view: 'card' } }
    } else {
      // Keep children where they are relative to each other; move them so the
      // frame's corner is where the card was. An empty network keeps its spot.
      if (rect && networkChildCount(g.nodes, id) + boundaryCount(g, id) > 0) {
        const dx = n.position[0] - rect.x
        const dy = n.position[1] - rect.y
        if (dx !== 0 || dy !== 0) {
          for (const c of descendants(g, id)) {
            const cn = nodes[c]
            nodes[c] = { ...cn, position: [cn.position[0] + dx, cn.position[1] + dy] }
          }
        }
      }
      nodes[id] = { ...n, meta: { ...(n.meta ?? {}), view: 'frame' } }
    }
    g = { ...g, nodes }
  }
  return g
}

function boundaryCount(graph: Pick<Graph, 'nodes'>, id: string): number {
  let k = 0
  for (const n of Object.values(graph.nodes)) if (n.parent === id && BOUNDARY_TYPES.has(n.type)) k += 1
  return k
}

/**
 * X / Shift+X: toggle the selected networks. When any of them is a frame,
 * all become cards; otherwise all become frames (one predictable result for
 * a mixed selection). One commit, one undo step. False when no network is
 * selected or the graph cannot be edited.
 */
export function toggleSelectedNetworkViews(canvas?: CanvasCtx | null): boolean {
  const s = useNodeBuilderStore.getState()
  const graph = s.graph
  if (!graph || graph.readOnly) return false
  const ids = selectedNetworks(s, graph)
  if (ids.length === 0) return false
  const toCard = ids.some(id => graph.nodes[id]?.meta?.view !== 'card')
  const view = toCard ? 'card' : 'frame'
  const sizeOf = canvasSizeOf(canvas ?? getActiveCanvas())
  s.commit(toCard ? 'show as card' : 'show as frame', g => setNetworkView(g, ids, view, sizeOf))
  return true
}

/** The X row label: `Show as card` while a selected network is a frame, else `Show as frame`. */
export function toggleViewLabel(s: NodeBuilderState = useNodeBuilderStore.getState()): string {
  const graph = s.graph
  const ids = selectedNetworks(s, graph)
  if (ids.length > 0 && ids.every(id => graph?.nodes[id]?.meta?.view === 'card')) return 'Show as frame'
  return 'Show as card'
}

// ── Locked asset instances (S38) ────────────────────────────────────────────

/** An asset's network as the library stores it. */
export interface AssetNetwork {
  nodes: Record<string, GraphNode>
  wires: GraphWire[]
}

/** Looks up an asset version's network (cached by the provider), or null/undefined when it is not loaded. */
export type AssetNetworkSource = (ref: { name: string; version: number }) => AssetNetwork | null | undefined

let assetSource: AssetNetworkSource | null = null
let assetVersion = 0
const assetListeners = new Set<() => void>()

/**
 * Register where locked instances get their children (item 6.D wires this
 * to graphLibrary.ts). Call `notifyAssetNetworks()` when a fetch lands so
 * the canvas redraws. Returns the function that removes it.
 */
export function registerAssetNetworkSource(fn: AssetNetworkSource): () => void {
  assetSource = fn
  notifyAssetNetworks()
  return () => {
    if (assetSource !== fn) return
    assetSource = null
    notifyAssetNetworks()
  }
}

/** Tell the canvas that an asset network finished loading. */
export function notifyAssetNetworks(): void {
  assetVersion += 1
  for (const l of [...assetListeners]) l()
}

function subscribeAssets(l: () => void): () => void {
  assetListeners.add(l)
  return () => { assetListeners.delete(l) }
}

/** A number that changes when the asset source or its data changes. */
export function useAssetNetworksVersion(): number {
  return useSyncExternalStore(subscribeAssets, () => assetVersion, () => assetVersion)
}

/** The library network of a locked instance, when the source has it. */
export function assetNetworkOf(n: GraphNode | undefined): AssetNetwork | null {
  if (!n?.asset_ref || !assetSource) return null
  try {
    return assetSource(n.asset_ref) ?? null
  } catch {
    return null
  }
}

/**
 * The graph to draw while inside the locked instance `id`: the graph plus
 * the asset's nodes and wires under composite ids `<id>::<child>` (the
 * backend's ids for an expanded instance), parented to the instance and
 * moved to its place (the asset keeps positions relative to it). Shown
 * read-only. The graph itself when the definition is not loaded.
 */
export function withLockedChildren(graph: Graph, id: string): Graph {
  const inst = graph.nodes[id]
  if (!isLockedInstance(inst)) return graph
  const net = assetNetworkOf(inst)
  if (!net) return graph
  const cid = (child: string) => `${id}::${child}`
  const nodes = { ...graph.nodes }
  // The asset stores positions relative to the subnet it was saved from.
  const [ox, oy] = inst!.position
  for (const n of Object.values(net.nodes)) {
    nodes[cid(n.id)] = {
      ...n,
      id: cid(n.id),
      parent: n.parent ? cid(n.parent) : id,
      position: [n.position[0] + ox, n.position[1] + oy],
    }
  }
  const wires = [
    ...graph.wires,
    ...net.wires.map(w => ({ ...w, id: cid(w.id), from: cid(w.from), to: cid(w.to) })),
  ]
  return { ...graph, nodes, wires, readOnly: true }
}
