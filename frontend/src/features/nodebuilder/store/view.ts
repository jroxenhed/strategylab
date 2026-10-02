/**
 * View slice of the node builder store (W3 pre-step 3.0, item 3.D; W6 item
 * 6.C): which network the canvas shows, pan and zoom, and snap-to-grid.
 *
 * The network on screen is kept by node id (`currentNetworkId`, W6 6.C), so
 * renaming a network, or one of its parents, keeps the view inside it.
 * `network` is its path ('/' for the root), kept for display and for older
 * callers; a reconciler rewrites it after every commit, undo and redo.
 * The root is `network === '/'`: loads reset `network` to '/', and the id is
 * then ignored.
 *
 * The viewport is kept per network (key: the network node id, '/' for the
 * root), so going into a network and back restores where the user was. The
 * canvas writes it when a pan or zoom ends and reads it back
 * (`rememberedViewport`) when it mounts again or the network changes, so
 * leaving the editor and coming back keeps the view instead of re-fitting.
 * A saved graph also keeps its viewports in
 * `localStorage['nb.viewports.<graphId>']` (S37), so reopening the graph
 * later brings them back.
 *
 * The in-memory copy belongs to one loaded graph: it is tagged with the
 * graph slice's `layoutEpoch`, which every load (New, Open, Edit this graph)
 * bumps. A viewport saved for the previous graph is never restored onto the
 * next one (localStorage is per graph id, so it cannot mix graphs either).
 *
 * View state is never part of undo and never marks the graph dirty.
 */

import type { StateCreator } from 'zustand'
import type { Graph } from '../../../api/nodebuilder'
import { findByPath, nodePath } from '../paths'
import { NETWORK_TYPES } from '../rfMapping'
import type { NodeBuilderState } from '../store'
import { registerGraphReconciler } from './reconcile'

export interface Viewport {
  x: number
  y: number
  zoom: number
}

/** Path of the root network (and the viewport key of the root). */
export const ROOT_NETWORK = '/'

/** The view a new, empty graph starts from. */
export const HOME_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }

/** The snap grid in flow units (token --nb-grid, 24px). */
export const SNAP_GRID: [number, number] = [24, 24]

/** localStorage key of a saved graph's viewports (S37). */
export function viewportStorageKey(graphId: string): string {
  return `nb.viewports.${graphId}`
}

export interface ViewSlice {
  /** The id of the network node the canvas shows; ignored while `network` is '/'. */
  currentNetworkId: string | null
  /** Path of the network the canvas shows ('/' is the root). Follows renames. */
  network: string
  /** Last pan and zoom per network (key: node id, '/' for the root). Written when a pan or zoom ends. */
  viewports: Record<string, Viewport>
  /** The layoutEpoch `viewports` were saved under (a load starts a new memory). */
  viewportsEpoch: number
  /** Pan and zoom of the current network (the same value as viewports[key]). */
  viewport: Viewport
  /**
   * A node the canvas should frame once the new network is drawn, when it
   * is off screen (S37: going up frames the network you left). The canvas
   * clears it.
   */
  revealNodeId: string | null
  /** True: frame `revealNodeId` even when it is on screen (crumb menu `Frame in parent`). */
  revealAlways: boolean
  /** Nodes snap to the 24px grid while dragged (G key, pane menu). Not saved. */
  snapToGrid: boolean
  /** Record the current network's viewport. */
  setViewport(v: Viewport): void
  /**
   * The saved viewport of a network for the graph on screen, or null.
   * `network`: a node id, a path ('/long_leg'), or nothing for the current one.
   */
  rememberedViewport(network?: string): Viewport | null
  /**
   * Show the network at this path ('/' for the root). Older callers; the id
   * is looked up once, so the view then follows the node, not the path.
   */
  setNetwork(network: string): void
  /**
   * Show a network by node id (null: the root). Clears the selection, or
   * selects `opts.select` (going up selects the network you left, S37), and
   * restores that network's saved view. `opts.graph`: the graph on screen
   * (the read-only view is not in the store; default: the store graph).
   * `opts.reveal`: frame the selected node once drawn, when it is off screen
   * ('offscreen') or always ('always').
   */
  enterNetwork(id: string | null, opts?: { graph?: Graph | null; select?: string | null; reveal?: 'offscreen' | 'always' }): void
  /** The canvas has framed (or skipped) `revealNodeId`. */
  clearReveal(): void
  setSnapToGrid(on: boolean): void
  toggleSnapToGrid(): void
}

/** True when `id` names a network node in `graph`. */
function isNetworkId(graph: Graph | null | undefined, id: string | null | undefined): id is string {
  return !!graph && !!id && !!graph.nodes[id] && NETWORK_TYPES.has(graph.nodes[id].type)
}

/**
 * The id of the network node the canvas shows, or null for the root (EA-4).
 * Every creator (Tab menu, paste, a new box or note) puts new things here.
 * The view keeps the node id (W6 6.C); a path set by an older caller is
 * resolved against `graph` (default: the store graph).
 */
export function currentParentId(
  s: Pick<NodeBuilderState, 'network' | 'graph'> & { currentNetworkId?: string | null },
  graph: Graph | null = s.graph,
): string | null {
  if (!graph || !s.network || s.network === ROOT_NETWORK) return null
  if (isNetworkId(graph, s.currentNetworkId)) return s.currentNetworkId
  return findByPath(graph, s.network)
}

/** The viewport key of the network on screen: its id, '/' at the root, or the path while it is not found. */
function currentViewKey(s: Pick<ViewSlice, 'network' | 'currentNetworkId'>): string {
  if (s.network === ROOT_NETWORK) return ROOT_NETWORK
  return s.currentNetworkId ?? s.network
}

/** The viewport key for `network` (an id or a path) in `graph`. */
function viewKeyOf(graph: Graph | null, network: string): string {
  if (network === ROOT_NETWORK) return ROOT_NETWORK
  if (network.startsWith('/')) return (graph && findByPath(graph, network)) ?? network
  return network
}

function safePath(graph: Graph | null | undefined, id: string): string | null {
  if (!graph) return null
  try {
    return nodePath(graph, id)
  } catch {
    return null
  }
}

// ── localStorage (S37) ──────────────────────────────────────────────────────
// Every access is guarded: storage can be missing or full (private mode).

function isViewport(v: unknown): v is Viewport {
  if (!v || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  return [o.x, o.y, o.zoom].every(n => typeof n === 'number' && Number.isFinite(n)) && (o.zoom as number) > 0
}

/** The viewports stored for a saved graph (bad entries are skipped). */
export function readStoredViewports(graphId: string): Record<string, Viewport> {
  try {
    const raw = localStorage.getItem(viewportStorageKey(graphId))
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return {}
    const out: Record<string, Viewport> = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (isViewport(v)) out[k] = { x: v.x, y: v.y, zoom: v.zoom }
    }
    return out
  } catch {
    return {}
  }
}

function writeStoredViewport(graphId: string, key: string, v: Viewport): void {
  try {
    const all = readStoredViewports(graphId)
    all[key] = v
    localStorage.setItem(viewportStorageKey(graphId), JSON.stringify(all))
  } catch {
    // Storage is full or blocked: the in-memory copy still works.
  }
}

// After a commit, undo or redo (EA-4/EA-5, W6 6.C):
// - the network on screen still exists: keep it, and refresh its path (a
//   rename of it or of a parent changes the path, never the view);
// - it is gone (deleted, undone): climb to the nearest network above it
//   that still exists, and say so (S37 toast);
// - a path set by an older caller that no longer resolves: the root.
// All in the same store write as the graph change.
registerGraphReconciler((s, next, cause) => {
  if (cause === 'load' || !next || s.network === ROOT_NETWORK) return null
  const id = currentParentId(s, s.graph) ?? (isNetworkId(next, s.currentNetworkId) ? s.currentNetworkId : null)
  if (id && isNetworkId(next, id)) {
    const path = safePath(next, id)
    if (!path) return null
    if (path === s.network && s.currentNetworkId === id) return null
    return { network: path, currentNetworkId: id }
  }
  // Gone: climb through the graph as it was.
  const old = s.graph
  let up: string | null = null
  if (id && old) {
    for (let p = old.nodes[id]?.parent ?? null, i = 0; p && i < 64; p = old.nodes[p]?.parent ?? null, i++) {
      if (isNetworkId(next, p)) { up = p; break }
    }
  }
  const upPath = up ? safePath(next, up) : null
  const target = up && upPath ? up : null
  const goneName = (id && old?.nodes[id]?.name) || s.network.split('/').pop() || 'The network'
  const where = target ? next.nodes[target].name : 'the root'
  const key = target ?? ROOT_NETWORK
  const remembered = s.viewportsEpoch === s.layoutEpoch ? s.viewports[key] : undefined
  return {
    network: target ? upPath! : ROOT_NETWORK,
    currentNetworkId: target,
    viewport: remembered ?? s.rememberedViewport(key) ?? HOME_VIEWPORT,
    // Only a real node that disappeared gets the toast (not a stale path).
    ...(id ? { flash: { text: `${goneName} no longer exists. Moved up to ${where}.`, seq: (s.flash?.seq ?? 0) + 1 } } : {}),
  }
})

/** View fields for a fresh, empty graph. */
export function homeView() {
  return {
    viewport: HOME_VIEWPORT,
    viewports: {} as Record<string, Viewport>,
    currentNetworkId: null as string | null,
    revealNodeId: null as string | null,
    revealAlways: false,
  }
}

function sameViewport(a: Viewport | undefined, b: Viewport): boolean {
  return !!a && a.x === b.x && a.y === b.y && a.zoom === b.zoom
}

export const createViewSlice: StateCreator<NodeBuilderState, [], [], ViewSlice> = (set, get) => ({
  network: ROOT_NETWORK,
  ...homeView(),
  viewportsEpoch: 0,
  snapToGrid: false,

  setViewport(v) {
    const s = get()
    const { viewports, viewportsEpoch, layoutEpoch } = s
    const key = currentViewKey(s)
    // Saved under an older load: start a fresh memory for this graph.
    const current = viewportsEpoch === layoutEpoch ? viewports : {}
    // A pan that ends where it started writes nothing (no re-render).
    if (current === viewports && sameViewport(current[key], v)) return
    const next = { x: v.x, y: v.y, zoom: v.zoom }
    set({ viewport: next, viewports: { ...current, [key]: next }, viewportsEpoch: layoutEpoch })
    const graphId = s.graphMeta?.id
    if (graphId) writeStoredViewport(graphId, key, next)
  },

  rememberedViewport(network) {
    const s = get()
    const key = network === undefined ? currentViewKey(s) : viewKeyOf(s.graph, network)
    if (s.viewportsEpoch === s.layoutEpoch && s.viewports[key]) return s.viewports[key]
    // Not seen in this session: the saved graph's stored view (S37).
    const graphId = s.graphMeta?.id
    return graphId ? readStoredViewports(graphId)[key] ?? null : null
  },

  setNetwork(network) {
    const s = get()
    const id = network === ROOT_NETWORK ? null : (s.graph && findByPath(s.graph, network)) ?? null
    if (network === s.network && id === (network === ROOT_NETWORK ? null : s.currentNetworkId)) return
    const key = id ?? network
    set({ network, currentNetworkId: id, viewport: s.rememberedViewport(key) ?? HOME_VIEWPORT })
  },

  enterNetwork(id, opts = {}) {
    const s = get()
    const graph = opts.graph ?? s.graph
    const target = isNetworkId(graph, id) ? id : null
    const path = target ? safePath(graph, target) ?? ROOT_NETWORK : ROOT_NETWORK
    const select = opts.select && graph?.nodes[opts.select] ? opts.select : null
    set({
      network: target ? path : ROOT_NETWORK,
      currentNetworkId: target,
      viewport: s.rememberedViewport(target ?? ROOT_NETWORK) ?? HOME_VIEWPORT,
      revealNodeId: opts.reveal && select ? select : null,
      revealAlways: opts.reveal === 'always',
    })
    // Selection lives in its own slice: clear it on dive, or select the
    // network we came out of (S37).
    if (select) get().setSelection({ nodeIds: [select], wireIds: [], annotationIds: [], primary: select })
    else get().setSelection({ nodeIds: [], wireIds: [], annotationIds: [], primary: null })
  },

  clearReveal() {
    if (get().revealNodeId !== null || get().revealAlways) set({ revealNodeId: null, revealAlways: false })
  },

  setSnapToGrid(on) {
    if (get().snapToGrid !== on) set({ snapToGrid: on })
  },

  toggleSnapToGrid() {
    set(s => ({ snapToGrid: !s.snapToGrid }))
  },
})
