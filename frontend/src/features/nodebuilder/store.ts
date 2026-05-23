/**
 * store.ts — Zustand store for the Node Strategy Builder (Unit 5).
 *
 * Manages the editable graph state, selection, viewport, and persistence.
 * All mutation operations delegate to pure functions in operations.ts and
 * throw ReadOnlyGraphError when graph.readOnly is true.
 *
 * Viewing auto-render results uses TanStack Query, NOT this store.
 * The store is only populated when the user explicitly enters edit mode.
 */

import { create } from 'zustand'
import type { Graph, GraphNode, GraphWire } from '../../api/nodebuilder'
import {
  addNode as opAddNode,
  removeNodeWithRewire as opRemoveNodeWithRewire,
  addWire as opAddWire,
  removeWire as opRemoveWire,
  moveNode as opMoveNode,
  spliceNodeOntoWire as opSpliceNodeOntoWire,
  MIN_SUPPORTED_VERSION,
  IncompatibleGraphVersionError,
} from './operations'

// ---------------------------------------------------------------------------
// Persistence key
// ---------------------------------------------------------------------------

const SAVED_GRAPHS_KEY = 'strategylab-saved-graphs'

function loadSavedGraphs(): Record<string, Graph> {
  try {
    const raw = localStorage.getItem(SAVED_GRAPHS_KEY)
    if (!raw) return {}
    return JSON.parse(raw) as Record<string, Graph>
  } catch {
    return {}
  }
}

function saveSavedGraphs(graphs: Record<string, Graph>): void {
  localStorage.setItem(SAVED_GRAPHS_KEY, JSON.stringify(graphs))
}

// ---------------------------------------------------------------------------
// Simple hash for change detection (djb2 over JSON string)
// ---------------------------------------------------------------------------

function hashGraph(g: Graph | null): string | null {
  if (g === null) return null
  const s = JSON.stringify(g)
  let h = 5381
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) ^ s.charCodeAt(i)
  }
  return (h >>> 0).toString(16)
}

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

export interface NodeBuilderState {
  // Current editable graph (null = no graph loaded; view auto-render via TanStack Query)
  graph: Graph | null

  // Selection / display / bypass per-node UI state
  selectedNodeId: string | null
  displayNodeId: string | null
  bypassedNodeIds: Set<string>

  // Cached hash for change detection
  graphHash: string | null

  // Pan / zoom
  viewport: { x: number; y: number; zoom: number }

  // ── Setters ──────────────────────────────────────────────────────────────

  setGraph(g: Graph | null): void
  select(id: string | null): void
  setDisplay(id: string | null): void
  toggleBypass(id: string): void
  setViewport(v: { x: number; y: number; zoom: number }): void

  // ── Mutation operations (reject when graph.readOnly is true) ─────────────

  addNode(node: GraphNode): void
  removeNodeWithRewire(nodeId: string): void
  addWire(wire: GraphWire): void
  removeWire(wireId: string): void
  moveNode(nodeId: string, position: [number, number]): void
  spliceNodeOntoWire(nodeId: string, wireId: string): void

  // ── Persistence ──────────────────────────────────────────────────────────

  /** Write current graph to localStorage under its name (graph._version ensured). */
  saveCurrentGraph(): void

  /** Load a named graph from localStorage. Throws IncompatibleGraphVersionError if too old. */
  loadGraph(name: string): void

  /** Copy an auto-render graph into the store as editable (readOnly=false). */
  loadFromAutoRender(graph: Graph): void

  /** Create a new, empty editable graph and enter edit mode. */
  newEmptyGraph(): void
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useNodeBuilderStore = create<NodeBuilderState>()((set, get) => ({
  graph: null,
  selectedNodeId: null,
  displayNodeId: null,
  bypassedNodeIds: new Set(),
  graphHash: null,
  viewport: { x: 0, y: 0, zoom: 1 },

  // ── Setters ───────────────────────────────────────────────────────────────

  setGraph(g) {
    set({ graph: g, graphHash: hashGraph(g) })
  },

  select(id) {
    set({ selectedNodeId: id })
  },

  setDisplay(id) {
    set({ displayNodeId: id })
  },

  toggleBypass(id) {
    set(state => {
      const next = new Set(state.bypassedNodeIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { bypassedNodeIds: next }
    })
  },

  setViewport(v) {
    set({ viewport: v })
  },

  // ── Mutation operations ───────────────────────────────────────────────────

  addNode(node) {
    const { graph } = get()
    if (!graph) return
    const next = opAddNode(graph, node)
    set({ graph: next, graphHash: hashGraph(next) })
  },

  removeNodeWithRewire(nodeId) {
    const { graph } = get()
    if (!graph) return
    const next = opRemoveNodeWithRewire(graph, nodeId)
    set({ graph: next, graphHash: hashGraph(next) })
  },

  addWire(wire) {
    const { graph } = get()
    if (!graph) return
    const next = opAddWire(graph, wire)
    set({ graph: next, graphHash: hashGraph(next) })
  },

  removeWire(wireId) {
    const { graph } = get()
    if (!graph) return
    const next = opRemoveWire(graph, wireId)
    set({ graph: next, graphHash: hashGraph(next) })
  },

  moveNode(nodeId, position) {
    const { graph } = get()
    if (!graph) return
    const next = opMoveNode(graph, nodeId, position)
    set({ graph: next, graphHash: hashGraph(next) })
  },

  spliceNodeOntoWire(nodeId, wireId) {
    const { graph } = get()
    if (!graph) return
    const next = opSpliceNodeOntoWire(graph, nodeId, wireId)
    set({ graph: next, graphHash: hashGraph(next) })
  },

  // ── Persistence ───────────────────────────────────────────────────────────

  saveCurrentGraph() {
    const { graph } = get()
    if (!graph) return
    const graphName = (graph.nodes['output'] as GraphNode | undefined)?.params?.name as string
      ?? 'unnamed'
    const withVersion: Graph = { ...graph, _version: graph._version || 1 }
    const saved = loadSavedGraphs()
    saved[graphName] = withVersion
    saveSavedGraphs(saved)
  },

  loadGraph(name) {
    const saved = loadSavedGraphs()
    const g = saved[name]
    if (!g) throw new Error(`Graph "${name}" not found in saved graphs.`)
    const version = g._version ?? 0
    if (version < MIN_SUPPORTED_VERSION) {
      throw new IncompatibleGraphVersionError(version, MIN_SUPPORTED_VERSION)
    }
    set({ graph: g, graphHash: hashGraph(g), selectedNodeId: null, displayNodeId: null })
  },

  loadFromAutoRender(graph) {
    const editable: Graph = { ...graph, readOnly: false }
    set({
      graph: editable,
      graphHash: hashGraph(editable),
      selectedNodeId: null,
      displayNodeId: null,
      bypassedNodeIds: new Set(),
    })
  },

  newEmptyGraph() {
    const empty: Graph = {
      _version: 1,
      readOnly: false,
      nodes: {},
      wires: [],
    }
    set({
      graph: empty,
      graphHash: hashGraph(empty),
      selectedNodeId: null,
      displayNodeId: null,
      bypassedNodeIds: new Set(),
      viewport: { x: 0, y: 0, zoom: 1 },
    })
  },
}))
