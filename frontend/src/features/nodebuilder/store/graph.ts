/**
 * Graph slice of the node builder store: the editable graph, its edit
 * history and every graph edit (W1 item 1.E, split out in W3 pre-step 3.0).
 *
 * All mutation operations delegate to pure functions in operations.ts and
 * throw ReadOnlyGraphError when graph.readOnly is true.
 *
 * Every graph edit goes through `commit(label, recipe)`: it runs the recipe
 * on the current graph and records the old graph in `past`, so the edit can
 * be undone. Graphs are immutable, so a history entry is just a reference
 * to the old graph (unchanged nodes are shared, nothing is copied).
 *
 * Anything stored inside the graph is covered by undo for free: nodes,
 * wires, params, the display and bypass flags, and `graph.annotations`
 * (boxes and notes). Other slices (selection, view, status, clipboard) are
 * not history; a commit only drops selected ids that no longer exist.
 *
 * `beginBatch()` / `endBatch()` fold every commit between them into ONE
 * undo step (a drag, or a create-and-wire from the Tab menu).
 *
 * Viewing auto-render results uses TanStack Query, NOT this store.
 * The store is only populated when the user explicitly enters edit mode.
 */

import type { StateCreator } from 'zustand'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import {
  addNode as opAddNode,
  removeNodes as opRemoveNodes,
  removeNodesWithRewire as opRemoveNodesWithRewire,
  addWire as opAddWire,
  removeWire as opRemoveWire,
  moveNode as opMoveNode,
  moveNodes as opMoveNodes,
  updateNodeParams as opUpdateNodeParams,
  spliceNodeOntoWire as opSpliceNodeOntoWire,
  type NewWire,
} from '../operations'
import type { NodeBuilderState } from '../store'
import { clearedSelection, pruneSelection } from './selection'
import { homeView, ROOT_NETWORK } from './view'
import { runReconcilers } from './reconcile'

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

/** Most undo steps kept. The oldest step is dropped past this. */
export const HISTORY_CAP = 100

/** One undo (or redo) step: the graph to go back to, and what the edit was. */
export interface HistoryEntry {
  label: string
  graph: Graph
}

/** The saved graph on the server this edit copy belongs to. */
export interface GraphMeta {
  /** Server id (g_...), or null for a graph not saved yet. */
  id: string | null
  rev: number
  name: string
}

/** Recipe for a commit: takes the current graph, returns the next one. */
export type GraphRecipe = (graph: Graph) => Graph

/**
 * Folding several commits into one undo step without a batch (UX-13,
 * UX-18). A commit with `coalesce: key` right after another commit with the
 * same key (nothing else committed, undone or loaded in between, and
 * within `windowMs`) records no new undo step: one undo goes back to before
 * the first of them. Arrow-key nudges use it per key-press burst; a new
 * sticky note and its first text edit are one step.
 */
export interface CommitOptions {
  coalesce?: string
  /** How long after the previous commit of the run a commit still joins it. Default 1000 ms. */
  windowMs?: number
  /** This commit ends the run: the next commit with the key starts a new step. */
  last?: boolean
}

/** Default join window for coalesced commits. */
export const COALESCE_MS = 1000

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

export interface GraphSlice {
  // Current editable graph (null = no graph loaded; view auto-render via TanStack Query)
  graph: Graph | null

  // Regime node ids that "Edit this graph" took out of the copy (empty when
  // none). NodeBuilder shows a banner while this is non-empty.
  regimeRemoved: string[]

  // ── History ──────────────────────────────────────────────────────────────

  /** Undo steps, oldest first. */
  past: HistoryEntry[]
  /** Redo steps, the next one to redo last. */
  future: HistoryEntry[]
  canUndo: boolean
  canRedo: boolean

  /**
   * Goes up by one on every commit, undo, redo and graph load. Watchers
   * (diagnostics, draft autosave) key off it instead of diffing graphs.
   */
  commitSeq: number

  /** True when the graph differs from the last saved or loaded version. */
  dirty: boolean

  /** The server graph being edited, or null for a new or auto-render copy. */
  graphMeta: GraphMeta | null

  /** Goes up when a different graph is loaded, so the canvas refits the view. */
  layoutEpoch: number

  /**
   * True while "Edit this graph" waits for its tidy layout (IP-4). The canvas
   * holds its first fit until the tidy lands (or a short timeout), so the
   * user does not see a fit, then every node jump, then a second fit.
   */
  layoutPending: boolean

  /** Open batch: nesting depth, and whether it already pushed its undo step. */
  batch: { depth: number; pushed: boolean; label: string } | null

  /** The graph as last saved or loaded; `dirty` compares against it. */
  savedGraph: Graph | null

  /** The open coalesce run (CommitOptions): its key, the commitSeq it left, and when. Internal. */
  coalesceRun: { key: string; seq: number; at: number } | null

  // ── History operations ───────────────────────────────────────────────────

  /**
   * Apply one edit as one undo step. The recipe runs on the current graph;
   * if it throws, nothing changes and the error reaches the caller. A recipe
   * that returns the same graph records nothing.
   */
  commit(label: string, recipe: GraphRecipe, opts?: CommitOptions): void
  /**
   * Take back the whole open coalesce run `key` as if it never happened (no
   * redo step), when nothing else was committed since. Returns true when it
   * did. A new sticky note left empty on Esc goes away this way (UX-18).
   */
  dropCoalesced(key: string): boolean
  /** Fold every commit until the matching endBatch into one undo step. */
  beginBatch(label?: string): void
  endBatch(): void
  undo(): void
  redo(): void

  // ── Mutation operations (reject when graph.readOnly is true) ─────────────

  addNode(node: GraphNode): void
  /** Delete nodes and reconnect around them (Houdini rule, see operations.ts). */
  removeNodesWithRewire(nodeIds: string[]): void
  /** Delete nodes and their wires with no rewire. */
  removeNodes(nodeIds: string[]): void
  addWire(wire: NewWire): void
  removeWire(wireId: string): void
  moveNode(nodeId: string, position: [number, number]): void
  /** Move several nodes in one step: one [dx, dy] for all, or one per id. */
  moveNodes(ids: string[], deltas: [number, number] | Array<[number, number]>): void
  updateNodeParams(nodeId: string, partial: Record<string, unknown>): void
  spliceNodeOntoWire(nodeId: string, wireId: string): void

  // ── Loading ──────────────────────────────────────────────────────────────

  /** Load a server graph for editing. Clears history; not dirty. */
  openGraph(graph: Graph, meta: GraphMeta): void

  /** Start a new, empty, unsaved graph. Clears history; graphMeta is null. */
  newGraph(): void

  /** Record a successful save: graphMeta updates and the graph is clean. */
  markSaved(meta: GraphMeta): void

  /** Throw away the edit copy and go back to the read-only auto-render view. */
  discardEdits(): void
}

/** True when the editable graph has edits that are not saved. */
export function hasEdits(s: Pick<GraphSlice, 'graph' | 'dirty'>): boolean {
  return s.graph != null && !s.graph.readOnly && s.dirty
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Push onto a history stack, dropping the oldest step past the cap. */
function pushCapped(stack: HistoryEntry[], entry: HistoryEntry): HistoryEntry[] {
  const next = [...stack, entry]
  return next.length > HISTORY_CAP ? next.slice(next.length - HISTORY_CAP) : next
}

/** Fields reset whenever a different graph is loaded (or none). */
export function freshHistory() {
  return {
    past: [] as HistoryEntry[],
    future: [] as HistoryEntry[],
    canUndo: false,
    canRedo: false,
    batch: null,
    dirty: false,
    coalesceRun: null,
    ...clearedSelection(),
  }
}

/**
 * State outside history that names graph ids, reset by every load (FC-10):
 * the network on screen goes back to the root, and no box or note stays in
 * edit (its id, `note1`, would name the next graph's note1).
 */
export function loadResetFields() {
  return { network: ROOT_NETWORK, editingAnnotationId: null as string | null }
}

/**
 * What else changes when the graph becomes `next` (EA-5): selection pruning
 * first, then every registered reconciler (store/reconcile.ts), merged into
 * the caller's one store write.
 */
export function reconcileFields(s: NodeBuilderState, next: Graph, cause: 'commit' | 'undo' | 'redo') {
  return { ...pruneSelection(s, next), ...runReconcilers(s, next, cause) }
}

/**
 * Everything a load writes: fresh history, an empty selection, the graph as
 * both current and saved, and the next commit and layout counters. Shared by
 * the load actions here and `loadFromAutoRender` in store.ts.
 */
export function loadedGraphState(
  s: Pick<GraphSlice, 'commitSeq' | 'layoutEpoch'>,
  graph: Graph,
  extra: { graphMeta: GraphMeta | null; regimeRemoved: string[] },
) {
  // Module stores (the Inspector's flash) clear themselves; store fields
  // are reset below.
  runReconcilers(s as NodeBuilderState, graph, 'load')
  return {
    ...freshHistory(),
    ...loadResetFields(),
    graph,
    savedGraph: graph,
    graphMeta: extra.graphMeta,
    regimeRemoved: extra.regimeRemoved,
    commitSeq: s.commitSeq + 1,
    layoutEpoch: s.layoutEpoch + 1,
    layoutPending: false,
  }
}

// ---------------------------------------------------------------------------
// Slice
// ---------------------------------------------------------------------------

export const createGraphSlice: StateCreator<NodeBuilderState, [], [], GraphSlice> = (set, get) => ({
  graph: null,
  regimeRemoved: [],
  past: [],
  future: [],
  canUndo: false,
  canRedo: false,
  commitSeq: 0,
  dirty: false,
  graphMeta: null,
  layoutEpoch: 0,
  layoutPending: false,
  batch: null,
  savedGraph: null,
  coalesceRun: null,

  // ── History ───────────────────────────────────────────────────────────────

  commit(label, recipe, opts) {
    const s = get()
    const { graph } = s
    if (!graph) return
    const next = recipe(graph)
    if (next === graph) return
    const batch = s.batch
    const now = Date.now()
    const key = !batch ? opts?.coalesce : undefined
    const run = s.coalesceRun
    // Joins the open run: same key, nothing committed since, in time.
    const joins = !!key && !!run && run.key === key && run.seq === s.commitSeq && s.past.length > 0
      && now - run.at <= (opts?.windowMs ?? COALESCE_MS)
    // Inside a batch only the first commit records the "before" graph.
    const record = joins ? false : !batch || !batch.pushed
    const past = record
      ? pushCapped(s.past, { label: batch ? batch.label || label : label, graph })
      : s.past
    // One store write per commit (a group drag must be one write).
    set({
      graph: next,
      past,
      future: [],
      canUndo: past.length > 0,
      canRedo: false,
      batch: batch ? { ...batch, pushed: true } : null,
      commitSeq: s.commitSeq + 1,
      dirty: next !== s.savedGraph,
      coalesceRun: key && !opts?.last ? { key, seq: s.commitSeq + 1, at: now } : null,
      ...reconcileFields(s, next, 'commit'),
    })
  },

  dropCoalesced(key) {
    const s = get()
    const run = s.coalesceRun
    if (!s.graph || !run || run.key !== key || run.seq !== s.commitSeq || s.past.length === 0) return false
    const entry = s.past[s.past.length - 1]
    const past = s.past.slice(0, -1)
    set({
      graph: entry.graph,
      past,
      canUndo: past.length > 0,
      batch: null,
      commitSeq: s.commitSeq + 1,
      dirty: entry.graph !== s.savedGraph,
      coalesceRun: null,
      ...reconcileFields(s, entry.graph, 'undo'),
    })
    return true
  },

  beginBatch(label = '') {
    const { batch } = get()
    set({ batch: batch ? { ...batch, depth: batch.depth + 1 } : { depth: 1, pushed: false, label } })
  },

  endBatch() {
    const { batch } = get()
    if (!batch) return
    set({ batch: batch.depth > 1 ? { ...batch, depth: batch.depth - 1 } : null })
  },

  undo() {
    const s = get()
    if (!s.graph || s.past.length === 0) return
    const entry = s.past[s.past.length - 1]
    const past = s.past.slice(0, -1)
    const future = pushCapped(s.future, { label: entry.label, graph: s.graph })
    set({
      graph: entry.graph,
      past,
      future,
      canUndo: past.length > 0,
      canRedo: true,
      // An undo in the middle of a batch closes it: later commits are a new step.
      batch: null,
      commitSeq: s.commitSeq + 1,
      dirty: entry.graph !== s.savedGraph,
      ...reconcileFields(s, entry.graph, 'undo'),
    })
  },

  redo() {
    const s = get()
    if (!s.graph || s.future.length === 0) return
    const entry = s.future[s.future.length - 1]
    const future = s.future.slice(0, -1)
    const past = pushCapped(s.past, { label: entry.label, graph: s.graph })
    set({
      graph: entry.graph,
      past,
      future,
      canUndo: true,
      canRedo: future.length > 0,
      batch: null,
      commitSeq: s.commitSeq + 1,
      dirty: entry.graph !== s.savedGraph,
      ...reconcileFields(s, entry.graph, 'redo'),
    })
  },

  // ── Mutation operations ───────────────────────────────────────────────────

  addNode(node) {
    get().commit(`add ${node.name || node.type}`, g => opAddNode(g, node))
  },

  removeNodesWithRewire(nodeIds) {
    const label = nodeIds.length === 1 ? 'delete node' : `delete ${nodeIds.length} nodes`
    get().commit(label, g => opRemoveNodesWithRewire(g, nodeIds))
  },

  removeNodes(nodeIds) {
    const label = nodeIds.length === 1 ? 'delete node' : `delete ${nodeIds.length} nodes`
    get().commit(label, g => opRemoveNodes(g, nodeIds))
  },

  addWire(wire) {
    get().commit('add wire', g => opAddWire(g, wire))
  },

  removeWire(wireId) {
    get().commit('delete wire', g => opRemoveWire(g, wireId))
  },

  moveNode(nodeId, position) {
    get().commit('move node', g => opMoveNode(g, nodeId, position))
  },

  moveNodes(ids, deltas) {
    const label = ids.length === 1 ? 'move node' : `move ${ids.length} nodes`
    get().commit(label, g => opMoveNodes(g, ids, deltas))
  },

  // Kept as a named action: browser verification looks it up by name.
  updateNodeParams(nodeId, partial) {
    const keys = Object.keys(partial)
    const label = keys.length === 1 ? `edit ${keys[0]}` : 'edit params'
    get().commit(label, g => opUpdateNodeParams(g, nodeId, partial))
  },

  spliceNodeOntoWire(nodeId, wireId) {
    get().commit('splice node', g => opSpliceNodeOntoWire(g, nodeId, wireId))
  },

  // ── Loading ───────────────────────────────────────────────────────────────

  openGraph(graph, meta) {
    const editable = graph.readOnly ? { ...graph, readOnly: false } : graph
    set(loadedGraphState(get(), editable, { graphMeta: meta, regimeRemoved: [] }))
  },

  newGraph() {
    // A new graph also starts from the home view, so it does not keep the
    // old graph's pan and zoom.
    set({ ...loadedGraphState(get(), emptyGraph(), { graphMeta: null, regimeRemoved: [] }), ...homeView() })
  },

  markSaved(meta) {
    set(s => ({ graphMeta: meta, savedGraph: s.graph, dirty: false }))
  },

  discardEdits() {
    const s = get()
    runReconcilers(s, null, 'load')
    set({
      ...freshHistory(),
      ...loadResetFields(),
      layoutPending: false,
      graph: null,
      savedGraph: null,
      graphMeta: null,
      regimeRemoved: [],
      commitSeq: s.commitSeq + 1,
    })
  },
})
