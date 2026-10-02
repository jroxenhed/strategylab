/**
 * Graph reconcilers (W3 review fix EA-5): state outside the graph that
 * names graph ids, kept in step when the graph changes.
 *
 * `commit`, `undo` and `redo` (store/graph.ts) replace the graph in ONE
 * store write. Before that write they ask every registered reconciler what
 * else must change for the new graph, and merge the answers into the same
 * write. Selection pruning runs first (store/selection.ts pruneSelection);
 * then, in registration order:
 * - store/view.ts: the current network, when it no longer exists;
 * - store/annotations.ts: the box or note being edited, when it is gone;
 * - anything else that holds graph ids (the Inspector's flash and rename
 *   request, a W4 Data Sheet target, a W6 breadcrumb).
 *
 * A reconciler returns the store fields to change (or nothing). It may also
 * update its own non-store state (a module store), but must never call the
 * node builder store's `set` itself: the merged write is the point.
 *
 * Loads (Open, New, Edit this graph, Discard) reset these fields outright in
 * `loadedGraphState`; reconcilers also run there with cause 'load', so a
 * module store can clear itself.
 *
 * `registerSelectableIds`: a React Flow node source (rfMapping.ts) whose
 * nodes can be selected but are not graph nodes, boxes or notes (a W5
 * group header, a W6 boundary marker) names its ids here, so a commit does
 * not drop them from `selectedAnnotationIds`.
 *
 * A leaf module (types only), so every slice can import it without a cycle.
 */

import type { Graph } from '../../../api/nodebuilder'
import type { NodeBuilderState } from '../store'

export type ReconcileCause = 'commit' | 'undo' | 'redo' | 'load'

/** What else must change when the graph becomes `next`. */
export type GraphReconciler = (
  s: NodeBuilderState,
  next: Graph | null,
  cause: ReconcileCause,
) => Partial<NodeBuilderState> | null | undefined | void

const reconcilers: GraphReconciler[] = []

/** Register a reconciler. Returns the function that removes it. */
export function registerGraphReconciler(r: GraphReconciler): () => void {
  reconcilers.push(r)
  return () => {
    const i = reconcilers.indexOf(r)
    if (i >= 0) reconcilers.splice(i, 1)
  }
}

/** Run every reconciler; their fields merged (a later one wins on a clash). A throwing one is logged and skipped. */
export function runReconcilers(s: NodeBuilderState, next: Graph | null, cause: ReconcileCause): Partial<NodeBuilderState> {
  let out: Partial<NodeBuilderState> = {}
  for (const r of [...reconcilers]) {
    try {
      const patch = r(s, next, cause)
      if (patch) out = { ...out, ...patch }
    } catch (err) {
      console.error('nodebuilder: graph reconciler failed', err)
    }
  }
  return out
}

/** Ids of selectable non-graph canvas nodes in `graph` (beyond boxes and notes). */
export type SelectableIds = (graph: Graph) => Iterable<string>

const selectableSources: SelectableIds[] = []

/** Register more selectable ids. Returns the function that removes them. */
export function registerSelectableIds(fn: SelectableIds): () => void {
  selectableSources.push(fn)
  return () => {
    const i = selectableSources.indexOf(fn)
    if (i >= 0) selectableSources.splice(i, 1)
  }
}

/** Every registered extra selectable id in `graph`. */
export function extraSelectableIds(graph: Graph): Set<string> {
  const out = new Set<string>()
  for (const fn of selectableSources) {
    try {
      for (const id of fn(graph)) out.add(id)
    } catch (err) {
      console.error('nodebuilder: selectable ids source failed', err)
    }
  }
  return out
}
