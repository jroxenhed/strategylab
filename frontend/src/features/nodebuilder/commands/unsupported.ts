/**
 * `Replace with…` for an unsupported node (spec S13).
 *
 * Opens the Tab menu at the node. Choosing a type creates the new node,
 * puts it where the old one was, moves every wire that fits (`in0..` by
 * index, `out`) and deletes the old node, all in one undo step (the Tab
 * menu's create batch wraps the `onCreate` below). The node context menu
 * (S19) lists it; it has no key.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import { getDiagnosticsView } from '../useDiagnostics'
import { isUnsupportedNode } from '../nodes/unsupported'
import { replaceNode } from '../operations'
import type { Graph } from '../../../api/nodebuilder'
import type { NodeBuilderState } from '../store'
import type { Command } from './index'

/** True when a node of `graph` draws the S13 unsupported card (no rename, no params, no bypass). */
export function nodeIsUnsupported(graph: Graph, id: string): boolean {
  const node = graph.nodes[id]
  if (!node) return false
  const wiredIn = graph.wires.some(w => w.to === id)
  return isUnsupportedNode(node.type, wiredIn, getDiagnosticsView().byNode[id] ?? [])
}

/** The primary selected node when it is an unsupported node of an editable graph, else null. */
export function replaceableNode(s: NodeBuilderState): string | null {
  const graph = s.graph
  const id = s.selectedNodeId
  if (!graph || graph.readOnly || !id) return null
  const node = graph.nodes[id]
  if (!node) return null
  const wiredIn = graph.wires.some(w => w.to === id)
  const diagnostics = getDiagnosticsView().byNode[id] ?? []
  return isUnsupportedNode(node.type, wiredIn, diagnostics) ? id : null
}

export const commands: Command[] = [
  {
    id: 'nodes.replace',
    label: 'Replace with…',
    scope: 'canvas',
    menu: 'node',
    when: s => replaceableNode(s) != null,
    disabledReason: s => (replaceableNode(s) ? null : 'Only unsupported nodes are replaced'),
    run({ canvas, store }) {
      const oldId = replaceableNode(store.getState())
      if (!canvas || !oldId) return false
      const old = store.getState().graph!.nodes[oldId]
      const flow = { x: old.position[0], y: old.position[1] }
      return canvas.openTabMenu({
        flow,
        screen: canvas.rf.flowToScreenPosition(flow),
        onCreate(newId) {
          store.getState().commit(`replace ${old.name || oldId}`, g => replaceNode(g, oldId, newId))
          store.getState().setSelection({ nodeIds: [newId], primary: newId })
        },
      })
    },
  },
]
