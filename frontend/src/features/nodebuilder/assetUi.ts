/**
 * Shared state for asset authoring (W6 item 6.D, specs S39 to S43).
 *
 * - Which asset dialog is open: the Promote popover (S40), Save as asset
 *   (S41) or the Asset Manager (S42). The dialogs mount in the `dialogs`
 *   slot (plugins/assets.ts) and read this store; commands and menus open
 *   them with `openPromote`, `openSaveAsset`, `openAssetManager`.
 * - The asset toast (S39 collapse, S41 save): one line at the bottom of the
 *   canvas column, with at most one action link.
 * - The library list (`GET /api/graph_library`), kept for the Tab menu and
 *   the Asset Manager. It never blocks a menu: callers draw their built-in
 *   rows at once and add asset rows when the list arrives.
 * - `currentNetworkOf`: the network on screen, by id.
 */

import { create } from 'zustand'
import type { Graph } from '../../api/nodebuilder'
import { assetErrorText, forgetMissingAssets, listAssets, type AssetListItem } from '../../api/graphLibrary'
import { currentParentId } from './store/view'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import { getActiveCanvas } from './screen'
import { insideLockedAsset } from './operations/collapse'
import { addAssetInstance, type InstanceSource } from './operations/assets'

// ── Dialogs ───────────────────────────────────────────────────────────────

export interface PromoteRequest {
  nodeId: string
  param: string
}

export interface SaveAssetRequest {
  subnetId: string
  /** Open with the palette section on ("Promote to palette"). */
  palette: boolean
}

export interface ManagerRequest {
  /** Where `Insert into graph` places the instance (flow units); null: the canvas centre. */
  insertAt: { x: number; y: number } | null
  /** Asset to select when it opens. */
  select?: string
}

interface AssetUiState {
  promote: PromoteRequest | null
  saveAsset: SaveAssetRequest | null
  manager: ManagerRequest | null
  toast: { text: string; action?: { label: string; run: () => void }; seq: number } | null
}

export const useAssetUi = create<AssetUiState>()(() => ({
  promote: null,
  saveAsset: null,
  manager: null,
  toast: null,
}))

export function openPromote(req: PromoteRequest): void {
  useAssetUi.setState({ promote: req })
}

export function closePromote(): void {
  useAssetUi.setState({ promote: null })
}

export function openSaveAsset(req: SaveAssetRequest): void {
  useAssetUi.setState({ saveAsset: req })
}

export function closeSaveAsset(): void {
  useAssetUi.setState({ saveAsset: null })
}

export function openAssetManager(req: Partial<ManagerRequest> = {}): void {
  useAssetUi.setState({ manager: { insertAt: req.insertAt ?? null, select: req.select } })
}

export function closeAssetManager(): void {
  useAssetUi.setState({ manager: null })
}

let toastSeq = 0

/** Show the asset toast (replaces the one on screen). */
export function showAssetToast(text: string, action?: { label: string; run: () => void }): void {
  toastSeq += 1
  useAssetUi.setState({ toast: { text, action, seq: toastSeq } })
}

export function hideAssetToast(seq?: number): void {
  const t = useAssetUi.getState().toast
  if (!t || (seq !== undefined && t.seq !== seq)) return
  useAssetUi.setState({ toast: null })
}

/** Tests: everything closed. */
export function resetAssetUi(): void {
  useAssetUi.setState({ promote: null, saveAsset: null, manager: null, toast: null })
}

// ── Library list ──────────────────────────────────────────────────────────

export type LibraryStatus = 'idle' | 'loading' | 'ok' | 'error'

interface LibraryState {
  items: AssetListItem[]
  status: LibraryStatus
  error: string | null
  /** When the list last arrived (ms), 0 for never. */
  loadedAt: number
}

export const useAssetLibrary = create<LibraryState>()(() => ({
  items: [],
  status: 'idle',
  error: null,
  loadedAt: 0,
}))

/** A list newer than this is reused by `ensureLibrary`. */
const FRESH_MS = 30_000

let pending: Promise<void> | null = null
// Bumped by resetAssetLibrary: an answer to an older request is dropped.
let listGen = 0

/** Fetch the list again (one request at a time). */
export function refreshLibrary(): Promise<void> {
  if (pending) return pending
  useAssetLibrary.setState({ status: 'loading', error: null })
  forgetMissingAssets()
  const gen = listGen
  const p: Promise<void> = listAssets()
    .then(items => {
      if (gen === listGen) useAssetLibrary.setState({ items, status: 'ok', error: null, loadedAt: Date.now() })
    })
    .catch((e: unknown) => {
      // The server's sentence (detail.message, LD-05), never the axios text.
      if (gen === listGen) useAssetLibrary.setState({ status: 'error', error: assetErrorText(e) })
    })
    .finally(() => { if (pending === p) pending = null })
  pending = p
  return p
}

/** Fetch the list unless a fresh one is already here. */
export function ensureLibrary(): Promise<void> {
  const s = useAssetLibrary.getState()
  if (s.status === 'ok' && Date.now() - s.loadedAt < FRESH_MS) return Promise.resolve()
  return refreshLibrary()
}

/** Tests: forget the list. */
export function resetAssetLibrary(): void {
  pending = null
  listGen += 1
  useAssetLibrary.setState({ items: [], status: 'idle', error: null, loadedAt: 0 })
}

// ── Where things go ───────────────────────────────────────────────────────

/**
 * The id of the network on screen, or null for the root (the store keeps
 * it by id, `currentNetworkId`; a network that is not in the graph counts
 * as the root).
 */
export function currentNetworkOf(s: NodeBuilderState, graph: Graph | null = s.graph): string | null {
  return currentParentId(s, graph)
}

/** Show the network `networkId` sits in and select it (S40 "Go to subnet"). */
export function goUpAndSelect(s: NodeBuilderState, networkId: string): void {
  const g = s.graph
  if (!g?.nodes[networkId]) return
  const parent = g.nodes[networkId].parent ?? null
  if (currentNetworkOf(s) !== parent) s.enterNetwork(parent, { select: networkId, reveal: 'offscreen' })
  else s.setSelection({ nodeIds: [networkId], primary: networkId })
}

// ── Insert an asset instance ──────────────────────────────────────────────

/** The middle of the canvas on screen, in flow units, or null without a canvas. */
export function canvasCentre(): { x: number; y: number } | null {
  const c = getActiveCanvas()
  const el = c?.container()
  if (!c || !el) return null
  try {
    const r = el.getBoundingClientRect()
    return c.rf.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 })
  } catch {
    return null
  }
}

/** Why an asset cannot be inserted into the network on screen now, or null. */
export function insertProblem(s: NodeBuilderState = useNodeBuilderStore.getState()): string | null {
  const g = s.graph
  if (!g || g.readOnly) return 'Read-only graph'
  if (insideLockedAsset(g.nodes, currentNetworkOf(s, g))) return 'You are inside a locked asset'
  return null
}

/**
 * Place a locked instance of an asset version in the network on screen,
 * at `at` (flow units) or the canvas middle, selected. One undo step.
 * Returns the new node id, or null when nothing was placed.
 */
export function insertAssetInstance(asset: InstanceSource, at: { x: number; y: number } | null = null): string | null {
  const s = useNodeBuilderStore.getState()
  const problem = insertProblem(s)
  if (problem) {
    s.showFlash(problem)
    return null
  }
  const parent = currentNetworkOf(s)
  const pos = at ?? canvasCentre() ?? { x: 0, y: 0 }
  let id: string | null = null
  s.commit(`insert ${asset.name}`, g => {
    const r = addAssetInstance(g, asset, parent, [Math.round(pos.x), Math.round(pos.y)])
    id = r.nodeId
    return r.graph
  })
  if (id) useNodeBuilderStore.getState().setSelection({ nodeIds: [id], primary: id })
  return id
}
