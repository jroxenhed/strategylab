/**
 * Rename a node (spec S14, plan D3).
 *
 * The Inspector's name field and the F2 command rename through here. The
 * real work is `renameNode` in paths.ts (the TypeScript copy of the
 * backend's rename_node): it checks the name, keeps names unique among
 * siblings and rewrites stored path strings. This file adds the read-only
 * guard, a plain-English check for the name field, and the store commit,
 * so a rename is always one undo step.
 */

import type { Graph } from '../../../api/nodebuilder'
import { ReadOnlyGraphError } from '../operations'
import { isValidName, renameNode, siblingNames } from '../paths'
import type { NodeBuilderStoreApi } from '../store'

/** Helper text under an invalid name field (S14 "rename invalid"). */
export const RENAME_HELP = 'Names use a-z, 0-9 and _ and must be unique among siblings'

/** Undo label for a rename. */
export const RENAME_LABEL = 'rename node'

/**
 * Why `name` cannot be this node's new name, or null when it can. The
 * node's own current name is fine (the rename then does nothing).
 */
export function renameProblem(graph: Graph, nodeId: string, name: string): string | null {
  const node = graph.nodes[nodeId]
  if (!node) return 'This node no longer exists'
  if (!isValidName(name)) return RENAME_HELP
  if (name !== node.name && siblingNames(graph, node.parent, nodeId).includes(name)) return RENAME_HELP
  return null
}

/**
 * A copy of `graph` with the node renamed (the same graph when the name is
 * unchanged). Throws ReadOnlyGraphError on a read-only graph, and the
 * PathError of `renameNode` for a bad or taken name.
 */
export function renameNodeOp(graph: Graph, nodeId: string, newName: string): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('renameNode')
  return renameNode(graph, nodeId, newName)
}

/**
 * Rename through the store as one undo step. Returns false (and changes
 * nothing) when there is no editable graph or the name is not allowed.
 */
export function commitRename(store: NodeBuilderStoreApi, nodeId: string, newName: string): boolean {
  const { graph, commit } = store.getState()
  if (!graph || graph.readOnly) return false
  if (renameProblem(graph, nodeId, newName) !== null) return false
  if (graph.nodes[nodeId].name === newName) return true
  commit(RENAME_LABEL, g => renameNodeOp(g, nodeId, newName))
  return true
}
