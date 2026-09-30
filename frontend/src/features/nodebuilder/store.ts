/**
 * store.ts — Zustand store for the Node Strategy Builder (Unit 5, Wave 1).
 *
 * Manages the editable graph state, selection, viewport and edit history.
 * All mutation operations delegate to pure functions in operations.ts and
 * throw ReadOnlyGraphError when graph.readOnly is true.
 *
 * Every graph edit goes through `commit(label, recipe)`: it runs the recipe
 * on the current graph and records the old graph in `past`, so the edit can
 * be undone. Graphs are immutable, so a history entry is just a reference
 * to the old graph (unchanged nodes are shared, nothing is copied).
 *
 * `beginBatch()` / `endBatch()` fold every commit between them into ONE
 * undo step (a drag, or a create-and-wire from the Tab menu).
 *
 * Viewing auto-render results uses TanStack Query, NOT this store.
 * The store is only populated when the user explicitly enters edit mode.
 */

import { create } from 'zustand'
import { emptyGraph, type Graph, type GraphNode } from '../../api/nodebuilder'
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
} from './operations'
import { prepareEditableCopy } from './editNotices'

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

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

export interface NodeBuilderState {
  // Current editable graph (null = no graph loaded; view auto-render via TanStack Query)
  graph: Graph | null

  // Selection / display per-node UI state
  selectedNodeId: string | null
  displayNodeId: string | null

  // Regime node ids that "Edit this graph" took out of the copy (empty when
  // none). NodeBuilder shows a banner while this is non-empty.
  regimeRemoved: string[]

  // Pan / zoom
  viewport: { x: number; y: number; zoom: number }

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

  /** Open batch: nesting depth, and whether it already pushed its undo step. */
  batch: { depth: number; pushed: boolean; label: string } | null

  /** The graph as last saved or loaded; `dirty` compares against it. */
  savedGraph: Graph | null

  // ── Setters ──────────────────────────────────────────────────────────────

  select(id: string | null): void
  setDisplay(id: string | null): void
  setViewport(v: { x: number; y: number; zoom: number }): void

  // ── History operations ───────────────────────────────────────────────────

  /**
   * Apply one edit as one undo step. The recipe runs on the current graph;
   * if it throws, nothing changes and the error reaches the caller. A recipe
   * that returns the same graph records nothing.
   */
  commit(label: string, recipe: GraphRecipe): void
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

  /** Copy an auto-render graph into the store as editable (readOnly=false). */
  loadFromAutoRender(graph: Graph): void

  /** Throw away the edit copy and go back to the read-only auto-render view. */
  discardEdits(): void
}

/** True when the editable graph has edits that are not saved. */
export function hasEdits(s: Pick<NodeBuilderState, 'graph' | 'dirty'>): boolean {
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

/** Selection ids that still point at a node in `graph` (else null). */
function keepIfPresent(id: string | null, graph: Graph | null): string | null {
  return id && graph && id in graph.nodes ? id : null
}

/** Fields reset whenever a different graph is loaded (or none). */
function freshHistory() {
  return {
    past: [] as HistoryEntry[],
    future: [] as HistoryEntry[],
    canUndo: false,
    canRedo: false,
    batch: null,
    dirty: false,
    selectedNodeId: null,
    displayNodeId: null,
  }
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useNodeBuilderStore = create<NodeBuilderState>()((set, get) => ({
  graph: null,
  selectedNodeId: null,
  displayNodeId: null,
  regimeRemoved: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  past: [],
  future: [],
  canUndo: false,
  canRedo: false,
  commitSeq: 0,
  dirty: false,
  graphMeta: null,
  layoutEpoch: 0,
  batch: null,
  savedGraph: null,

  // ── Setters ───────────────────────────────────────────────────────────────

  select(id) {
    // An id from a late answer (a failed Run, an old diagnostics row) may name
    // a node that is gone; never leave the selection pointing at it. With no
    // store graph (the read-only view) the id belongs to that view's graph.
    const { graph } = get()
    set({ selectedNodeId: graph ? keepIfPresent(id, graph) : id })
  },

  setDisplay(id) {
    set({ displayNodeId: id })
  },

  setViewport(v) {
    set({ viewport: v })
  },

  // ── History ───────────────────────────────────────────────────────────────

  commit(label, recipe) {
    const s = get()
    const { graph } = s
    if (!graph) return
    const next = recipe(graph)
    if (next === graph) return
    const batch = s.batch
    // Inside a batch only the first commit records the "before" graph.
    const record = !batch || !batch.pushed
    const past = record
      ? pushCapped(s.past, { label: batch ? batch.label || label : label, graph })
      : s.past
    set({
      graph: next,
      past,
      future: [],
      canUndo: past.length > 0,
      canRedo: false,
      batch: batch ? { ...batch, pushed: true } : null,
      commitSeq: s.commitSeq + 1,
      dirty: next !== s.savedGraph,
      selectedNodeId: keepIfPresent(s.selectedNodeId, next),
      displayNodeId: keepIfPresent(s.displayNodeId, next),
    })
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
      selectedNodeId: keepIfPresent(s.selectedNodeId, entry.graph),
      displayNodeId: keepIfPresent(s.displayNodeId, entry.graph),
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
      selectedNodeId: keepIfPresent(s.selectedNodeId, entry.graph),
      displayNodeId: keepIfPresent(s.displayNodeId, entry.graph),
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
    const s = get()
    set({
      ...freshHistory(),
      graph: editable,
      savedGraph: editable,
      graphMeta: meta,
      regimeRemoved: [],
      commitSeq: s.commitSeq + 1,
      layoutEpoch: s.layoutEpoch + 1,
    })
  },

  newGraph() {
    const empty = emptyGraph()
    const s = get()
    set({
      ...freshHistory(),
      graph: empty,
      savedGraph: empty,
      graphMeta: null,
      regimeRemoved: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      commitSeq: s.commitSeq + 1,
      layoutEpoch: s.layoutEpoch + 1,
    })
  },

  markSaved(meta) {
    set(s => ({ graphMeta: meta, savedGraph: s.graph, dirty: false }))
  },

  loadFromAutoRender(graph) {
    // Strip /regime/* nodes + incident wires. The T2 graph evaluator returns
    // 400 on regime nodes ("Regime is not supported"), and WFA already strips
    // them at its boundary (see CLAUDE.md WFA §"Regime is unconditionally
    // stripped"). T1 read-only view still shows them; the editable copy must
    // not, otherwise Run Backtest 400s every time. The removed ids are kept
    // so NodeBuilder can tell the user. Rows are also spread apart (bug 8).
    const { graph: editable, regimeRemoved } = prepareEditableCopy(graph)
    const s = get()
    set({
      ...freshHistory(),
      graph: editable,
      savedGraph: editable,
      graphMeta: null,
      regimeRemoved,
      commitSeq: s.commitSeq + 1,
      layoutEpoch: s.layoutEpoch + 1,
    })
  },

  discardEdits() {
    const s = get()
    set({
      ...freshHistory(),
      graph: null,
      savedGraph: null,
      graphMeta: null,
      regimeRemoved: [],
      commitSeq: s.commitSeq + 1,
    })
  },
}))
