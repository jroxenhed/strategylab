/**
 * store.ts — Zustand store for the Node Strategy Builder.
 *
 * One store, built from slices (W3 pre-step 3.0). Each slice lives in its
 * own file under store/ and owns its fields and actions:
 *
 * - store/graph.ts        the graph, `commit`, undo and redo, loading
 * - store/selection.ts    selected nodes, wires and boxes/notes
 * - store/view.ts         pan and zoom per network
 * - store/status.ts       status-bar flash and cook state
 * - store/annotations.ts  network boxes and sticky notes (3.E)
 * - store/clipboard.ts    copy and paste (3.C)
 *
 * Every graph edit, in any slice, goes through `commit(label, recipe)` on
 * the graph slice, so it is one undo step. Boxes, notes and the flags live
 * inside the graph, so undo covers them too.
 *
 * `loadFromAutoRender` stays in this file: item 3.F owns it in Wave 3.
 *
 * Viewing auto-render results uses TanStack Query, NOT this store.
 * The store is only populated when the user explicitly enters edit mode.
 */

import { create } from 'zustand'
import type { Graph } from '../../api/nodebuilder'
import { prepareEditableCopy } from './editNotices'
import { tidyGraph } from './layout'
import { createGraphSlice, loadedGraphState, type GraphSlice } from './store/graph'
import { createSelectionSlice, type SelectionSlice } from './store/selection'
import { createViewSlice, type ViewSlice } from './store/view'
import { createStatusSlice, type StatusSlice } from './store/status'
import { createAnnotationsSlice, type AnnotationsSlice } from './store/annotations'
import { createClipboardSlice, type ClipboardSlice } from './store/clipboard'

export { HISTORY_CAP, hasEdits } from './store/graph'
export type { GraphMeta, GraphRecipe, HistoryEntry } from './store/graph'
export type { SelectionInput } from './store/selection'
export type { Viewport } from './store/view'
export type { CookKind, CookPhase, CookStatus, CookStatuses, PreviewState } from './store/status'

/** Actions defined in this file rather than in a slice. */
export interface LoadActions {
  /** Copy an auto-render graph into the store as editable (readOnly=false). */
  loadFromAutoRender(graph: Graph): void
}

export type NodeBuilderState =
  GraphSlice & SelectionSlice & ViewSlice & StatusSlice & AnnotationsSlice & ClipboardSlice & LoadActions

export const useNodeBuilderStore = create<NodeBuilderState>()((set, get, api) => ({
  ...createGraphSlice(set, get, api),
  ...createSelectionSlice(set, get, api),
  ...createViewSlice(set, get, api),
  ...createStatusSlice(set, get, api),
  ...createAnnotationsSlice(set, get, api),
  ...createClipboardSlice(set, get, api),

  loadFromAutoRender(graph) {
    // The copy keeps every node, regime included: since W5 the graph runs
    // regime (the regime_net network and the regime terminal).
    const { graph: editable } = prepareEditableCopy(graph)
    set({ ...loadedGraphState(get(), editable, { graphMeta: null }), layoutPending: true })

    // Edit mode draws param rows, so nodes grow and the read-only layout
    // overlaps (bug 8). Tidy the copy with elk (3.F). The copy above already
    // has its rows spread apart, which holds until elk is done (it loads on
    // first use) or if it fails. The tidy is part of the load: not an undo
    // step and not an edit. It only lands while the copy is untouched, so it
    // never moves nodes under the user or replaces a graph loaded since.
    const epoch = get().layoutEpoch
    // The canvas waits for this tidy before its first fit; any end of it
    // (landed, dropped, failed) lets it fit. A later load resets the flag.
    const settle = () => {
      const s = get()
      if (s.layoutEpoch === epoch && s.layoutPending) set({ layoutPending: false })
    }
    tidyGraph(editable)
      .then(tidy => {
        const s = get()
        const untouched =
          s.graph === editable && s.savedGraph === editable && s.past.length === 0 && s.future.length === 0
        if (!untouched || tidy === editable) { settle(); return }
        // layoutEpoch makes the canvas fit the view to the new positions.
        set({ graph: tidy, savedGraph: tidy, commitSeq: s.commitSeq + 1, layoutEpoch: s.layoutEpoch + 1, layoutPending: false })
      })
      .catch(err => {
        settle()
        console.error('nodebuilder: tidy layout of the edit copy failed', err)
      })
  },
}))

/** The store hook, which is also the store API (getState, setState, subscribe). */
export type NodeBuilderStoreApi = typeof useNodeBuilderStore
