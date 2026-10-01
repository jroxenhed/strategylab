/**
 * Clipboard slice of the node builder store: copy, paste and duplicate
 * (F435 W3 item 3.C).
 *
 * What was copied is held here, not in the graph, so it is not part of
 * undo and survives opening another graph (copy in one graph, paste in
 * another). A paste is a graph edit: it goes through `get().commit(...)` and
 * is one undo step. The pure work (id remap, name uniquing, read remap) is
 * in ../clipboard.ts.
 *
 * Nothing here pastes into a read-only graph or with no graph loaded.
 */

import type { StateCreator } from 'zustand'
import type { Graph } from '../../../api/nodebuilder'
import type { NodeBuilderState } from '../store'
import {
  copyFromGraph,
  isEmptyPayload,
  pastePayload,
  swapIdsForDrag,
  type ClipboardPayload,
  type PasteOptions,
} from '../clipboard'
import { currentParentId } from './view'

/** How far Cmd+D moves the copy (foundation 6.2). */
export const DUPLICATE_OFFSET: [number, number] = [24, 24]

export interface ClipboardSlice {
  /** The last copy, or null. Not history; kept across graph loads. */
  clipboard: ClipboardPayload | null

  /**
   * Copy the current selection. `source` is the graph on screen (the
   * read-only view keeps its graph outside the store); default: the store
   * graph. Returns the payload, or null when nothing was selected (the
   * clipboard is then left as it was).
   */
  copySelection(source?: Graph | null): ClipboardPayload | null
  /**
   * Paste the clipboard, its top-left corner at `at` (flow units; default:
   * where it was copied from). One undo step; the pasted items become the
   * selection. Returns the new node ids, or null when nothing was pasted.
   */
  pasteClipboard(at?: { x: number; y: number } | null): string[] | null
  /** Copy the selection and paste it 24px down and right, leaving the clipboard alone. */
  duplicateSelection(): string[] | null
  /**
   * Alt-drag: turn the grabbed nodes into a copy while the originals stay
   * where they stand (see swapIdsForDrag). `positions` are where the canvas
   * shows the grabbed nodes now. Returns the copy's ids, or null.
   */
  duplicateForDrag(ids: readonly string[], positions?: ReadonlyMap<string, [number, number]>): string[] | null
}

/** True when the store holds a graph that can be edited. */
export function canEdit(s: Pick<NodeBuilderState, 'graph'>): boolean {
  return s.graph != null && !s.graph.readOnly
}

/** True when there is something to paste. */
export function canPaste(s: Pick<NodeBuilderState, 'graph' | 'clipboard'>): boolean {
  return canEdit(s) && !isEmptyPayload(s.clipboard)
}

/** The node id of the network the canvas shows (null = root). */
function currentParent(s: NodeBuilderState, graph: Graph): string | null {
  return currentParentId(s, graph)
}

export const createClipboardSlice: StateCreator<NodeBuilderState, [], [], ClipboardSlice> = (set, get) => {
  /** Paste `payload` with `opts` as one commit, then select what was pasted. */
  function paste(label: string, payload: ClipboardPayload, opts: (g: Graph) => PasteOptions): string[] | null {
    const s = get()
    if (!canEdit(s) || isEmptyPayload(payload)) return null
    // The recipe runs inside commit; keep what it made in a holder.
    const out: { made: { nodeIds: string[]; annotationIds: string[] } | null } = { made: null }
    s.commit(label, g => {
      const r = pastePayload(g, payload, opts(g))
      out.made = r
      return r.graph
    })
    if (!out.made) return null
    const { nodeIds, annotationIds } = out.made
    get().setSelection({ nodeIds, annotationIds, primary: nodeIds[0] ?? null })
    return nodeIds
  }

  return {
    clipboard: null,

    copySelection(source) {
      const s = get()
      const graph = source ?? s.graph
      if (!graph) return null
      const payload = copyFromGraph(graph, { nodeIds: s.selectedNodeIds, annotationIds: s.selectedAnnotationIds })
      if (!payload) return null
      set({ clipboard: payload })
      return payload
    },

    pasteClipboard(at) {
      const s = get()
      const payload = s.clipboard
      if (!payload) return null
      return paste('paste', payload, g => ({
        at: at ?? { x: payload.origin[0], y: payload.origin[1] },
        parent: currentParent(get(), g),
      }))
    },

    duplicateSelection() {
      const s = get()
      if (!canEdit(s)) return null
      const payload = copyFromGraph(s.graph!, { nodeIds: s.selectedNodeIds, annotationIds: s.selectedAnnotationIds })
      if (!payload) return null
      return paste('duplicate', payload, () => ({ offset: DUPLICATE_OFFSET }))
    },

    duplicateForDrag(ids, positions) {
      const s = get()
      if (!canEdit(s)) return null
      const out: { copyIds: string[] | null } = { copyIds: null }
      s.commit('duplicate', g => {
        const r = swapIdsForDrag(g, ids, positions)
        if (!r) return g
        out.copyIds = r.copyIds
        return r.graph
      })
      return out.copyIds
    },
  }
}
