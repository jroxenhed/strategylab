/**
 * View slice of the node builder store (W3 pre-step 3.0, item 3.D): pan,
 * zoom and snap-to-grid.
 *
 * The viewport is kept per network (key: the network path, '/' for the
 * root), so going into a network and back restores where the user was. The
 * canvas writes it when a pan or zoom ends and reads it back
 * (`rememberedViewport`) when it mounts again or the network changes, so
 * leaving the editor and coming back keeps the view instead of re-fitting.
 *
 * The memory belongs to one loaded graph: it is tagged with the graph
 * slice's `layoutEpoch`, which every load (New, Open, Edit this graph)
 * bumps. A viewport saved for the previous graph is never restored onto the
 * next one.
 *
 * View state is never part of undo and never marks the graph dirty.
 */

import type { StateCreator } from 'zustand'
import type { Graph } from '../../../api/nodebuilder'
import { findByPath } from '../paths'
import type { NodeBuilderState } from '../store'
import { registerGraphReconciler } from './reconcile'

export interface Viewport {
  x: number
  y: number
  zoom: number
}

/** Path of the root network. */
export const ROOT_NETWORK = '/'

/** The view a new, empty graph starts from. */
export const HOME_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }

/** The snap grid in flow units (token --nb-grid, 24px). */
export const SNAP_GRID: [number, number] = [24, 24]

export interface ViewSlice {
  /** The network the canvas shows ('/' is the root). W5 and W6 change it. */
  network: string
  /** Last pan and zoom per network path. Written when a pan or zoom ends. */
  viewports: Record<string, Viewport>
  /** The layoutEpoch `viewports` were saved under (a load starts a new memory). */
  viewportsEpoch: number
  /** Pan and zoom of the current network (the same value as viewports[network]). */
  viewport: Viewport
  /** Nodes snap to the 24px grid while dragged (G key, pane menu). Not saved. */
  snapToGrid: boolean
  /** Record the current network's viewport. */
  setViewport(v: Viewport): void
  /** The saved viewport of a network (default: the current one) for the graph on screen, or null. */
  rememberedViewport(network?: string): Viewport | null
  /** Show another network. The canvas restores that network's saved view, or frames it. */
  setNetwork(network: string): void
  setSnapToGrid(on: boolean): void
  toggleSnapToGrid(): void
}

/**
 * The id of the network node the canvas shows, or null for the root (EA-4).
 * Every creator (Tab menu, paste, a new box or note) puts new things here.
 * The view keeps the path (W6 6.C turns it into a stable id); this resolves
 * it against `graph` (default: the store graph).
 */
export function currentParentId(s: Pick<NodeBuilderState, 'network' | 'graph'>, graph: Graph | null = s.graph): string | null {
  if (!graph || !s.network || s.network === ROOT_NETWORK) return null
  return findByPath(graph, s.network)
}

// A network that no longer exists after a commit, undo or redo (deleted,
// renamed, undone) is left for the root, in the same store write (EA-4/EA-5).
registerGraphReconciler((s, next) => {
  if (!next || s.network === ROOT_NETWORK || findByPath(next, s.network)) return null
  return { network: ROOT_NETWORK, viewport: s.rememberedViewport(ROOT_NETWORK) ?? HOME_VIEWPORT }
})

/** View fields for a fresh, empty graph. */
export function homeView() {
  return { viewport: HOME_VIEWPORT, viewports: {} as Record<string, Viewport> }
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
    const { network, viewports, viewportsEpoch, layoutEpoch } = get()
    // Saved under an older load: start a fresh memory for this graph.
    const current = viewportsEpoch === layoutEpoch ? viewports : {}
    // A pan that ends where it started writes nothing (no re-render).
    if (current === viewports && sameViewport(current[network], v)) return
    const next = { x: v.x, y: v.y, zoom: v.zoom }
    set({ viewport: next, viewports: { ...current, [network]: next }, viewportsEpoch: layoutEpoch })
  },

  rememberedViewport(network) {
    const s = get()
    if (s.viewportsEpoch !== s.layoutEpoch) return null
    return s.viewports[network ?? s.network] ?? null
  },

  setNetwork(network) {
    if (network === get().network) return
    set({ network, viewport: get().rememberedViewport(network) ?? HOME_VIEWPORT })
  },

  setSnapToGrid(on) {
    if (get().snapToGrid !== on) set({ snapToGrid: on })
  },

  toggleSnapToGrid() {
    set(s => ({ snapToGrid: !s.snapToGrid }))
  },
})
