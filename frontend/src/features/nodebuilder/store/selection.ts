/**
 * Selection slice of the node builder store (W3 pre-step 3.0).
 *
 * React Flow keeps the live selection on the canvas (click, Shift-click,
 * marquee). The canvas mirrors it here on every change (`mirrorSelection`),
 * so panels that never touch React Flow (the Inspector, the status bar,
 * commands) can read it.
 *
 * Code outside the canvas changes the selection with `select` (one node) or
 * `setSelection` (any set). `setSelection` bumps `selectionRequestSeq`; the
 * canvas watches it and applies the set to React Flow.
 *
 * Selection is not part of undo. After every commit, undo and redo the ids
 * that no longer exist are dropped (`pruneSelection`).
 */

import type { StateCreator } from 'zustand'
import type { Graph } from '../../../api/nodebuilder'
import type { NodeBuilderState } from '../store'
import { extraSelectableIds } from './reconcile'

/** A selection to set or mirror. Missing lists count as empty. */
export interface SelectionInput {
  /** Graph node ids. */
  nodeIds?: readonly string[]
  /** Wire ids. */
  wireIds?: readonly string[]
  /** Canvas items that are not graph nodes: network boxes and sticky notes. */
  annotationIds?: readonly string[]
  /** The primary node; defaults to the current one if still selected, else the first. */
  primary?: string | null
}

export interface SelectionSlice {
  /**
   * The primary selected node: the one the Tab menu wires from and `D` sets
   * the display flag on. It is also in `selectedNodeIds`.
   */
  selectedNodeId: string | null
  /** Every selected graph node, primary included. */
  selectedNodeIds: string[]
  /** Every selected wire. */
  selectedWireIds: string[]
  /** Selected boxes and notes (their ids, from graph.annotations). */
  selectedAnnotationIds: string[]
  /** Older per-node display state, kept as it was. The W3 flags live on graph nodes. */
  displayNodeId: string | null
  /** Goes up on every `setSelection`, so the canvas applies a selection made elsewhere. */
  selectionRequestSeq: number

  /**
   * Select one node, or nothing with null. An id that is not in the store
   * graph is ignored. With a store graph, a node already in a multi-selection
   * becomes the primary and the rest stay selected.
   */
  select(id: string | null): void
  setDisplay(id: string | null): void
  /** Replace the whole selection (legend click, paste, new box). The canvas follows. */
  setSelection(sel: SelectionInput): void
  /** Canvas only: record what React Flow has selected. Does not ask the canvas to re-apply it. */
  mirrorSelection(sel: SelectionInput): void
}

/** Selection fields when nothing is selected. */
export function clearedSelection() {
  return {
    selectedNodeId: null,
    selectedNodeIds: [] as string[],
    selectedWireIds: [] as string[],
    selectedAnnotationIds: [] as string[],
    displayNodeId: null,
  }
}

/** Selection ids that still point at a node in `graph` (else null). */
export function keepIfPresent(id: string | null, graph: Graph | null): string | null {
  return id && graph && id in graph.nodes ? id : null
}

/** `list` filtered by `keep`, or `list` itself when nothing is dropped. */
function filterSame(list: string[], keep: (id: string) => boolean): string[] {
  return list.every(keep) ? list : list.filter(keep)
}

/** True when two id lists hold the same ids in the same order. */
function sameIds(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i])
}

/**
 * Selection fields after the graph changed to `graph` (commit, undo, redo):
 * ids that are gone are dropped. Lists that lose nothing keep their identity,
 * so panels that subscribe to them do not re-render.
 */
export function pruneSelection(s: SelectionSlice, graph: Graph) {
  let wireIds: Set<string> | null = null
  let noteIds: Set<string> | null = null
  const hasWire = (id: string) => (wireIds ??= new Set(graph.wires.map(w => w.id))).has(id)
  const hasAnnotation = (id: string) =>
    (noteIds ??= new Set([
      ...(graph.annotations?.boxes ?? []).map(b => b.id),
      ...(graph.annotations?.notes ?? []).map(n => n.id),
      // Other selectable canvas nodes a source registered (EA-5).
      ...extraSelectableIds(graph),
    ])).has(id)
  return {
    selectedNodeId: keepIfPresent(s.selectedNodeId, graph),
    displayNodeId: keepIfPresent(s.displayNodeId, graph),
    selectedNodeIds: filterSame(s.selectedNodeIds, id => id in graph.nodes),
    selectedWireIds: filterSame(s.selectedWireIds, hasWire),
    selectedAnnotationIds: filterSame(s.selectedAnnotationIds, hasAnnotation),
  }
}

/** The primary node for a new node list: keep `current` if still in it, else the first. */
function pickPrimary(nodeIds: readonly string[], current: string | null, wanted?: string | null): string | null {
  if (wanted !== undefined) return wanted && nodeIds.includes(wanted) ? wanted : (nodeIds[0] ?? null)
  if (current && nodeIds.includes(current)) return current
  return nodeIds[0] ?? null
}

/** The fields to write for `sel`, reusing current lists that did not change. */
function selectionFields(s: SelectionSlice, sel: SelectionInput) {
  const nodeIds = [...(sel.nodeIds ?? [])]
  const wireIds = [...(sel.wireIds ?? [])]
  const annotationIds = [...(sel.annotationIds ?? [])]
  return {
    selectedNodeId: pickPrimary(nodeIds, s.selectedNodeId, sel.primary),
    selectedNodeIds: sameIds(nodeIds, s.selectedNodeIds) ? s.selectedNodeIds : nodeIds,
    selectedWireIds: sameIds(wireIds, s.selectedWireIds) ? s.selectedWireIds : wireIds,
    selectedAnnotationIds: sameIds(annotationIds, s.selectedAnnotationIds) ? s.selectedAnnotationIds : annotationIds,
  }
}

export const createSelectionSlice: StateCreator<NodeBuilderState, [], [], SelectionSlice> = (set, get) => ({
  ...clearedSelection(),
  selectionRequestSeq: 0,

  select(id) {
    // An id from a late answer (a failed Run, an old diagnostics row) may name
    // a node that is gone; never leave the selection pointing at it. With no
    // store graph (the read-only view) the id belongs to that view's graph.
    const s = get()
    const next = s.graph ? keepIfPresent(id, s.graph) : id
    // Empty lists that are already empty keep their identity, and a write
    // that changes nothing is skipped (IP-5: a pane click with nothing
    // selected must not re-render the panels).
    const emptyOf = (list: string[]) => (list.length === 0 ? list : [])
    if (next === null) {
      if (s.selectedNodeId === null && s.selectedNodeIds.length === 0
        && s.selectedWireIds.length === 0 && s.selectedAnnotationIds.length === 0) return
      set({
        selectedNodeId: null,
        selectedNodeIds: emptyOf(s.selectedNodeIds),
        selectedWireIds: emptyOf(s.selectedWireIds),
        selectedAnnotationIds: emptyOf(s.selectedAnnotationIds),
      })
      return
    }
    if (s.selectedNodeIds.includes(next)) {
      if (s.selectedNodeId !== next) set({ selectedNodeId: next })
      return
    }
    set({
      selectedNodeId: next,
      selectedNodeIds: [next],
      selectedWireIds: emptyOf(s.selectedWireIds),
      selectedAnnotationIds: emptyOf(s.selectedAnnotationIds),
    })
  },

  setDisplay(id) {
    set({ displayNodeId: id })
  },

  setSelection(sel) {
    const s = get()
    set({ ...selectionFields(s, sel), selectionRequestSeq: s.selectionRequestSeq + 1 })
  },

  mirrorSelection(sel) {
    const s = get()
    const next = selectionFields(s, sel)
    if (
      next.selectedNodeId === s.selectedNodeId
      && next.selectedNodeIds === s.selectedNodeIds
      && next.selectedWireIds === s.selectedWireIds
      && next.selectedAnnotationIds === s.selectedAnnotationIds
    ) return
    set(next)
  },
})
