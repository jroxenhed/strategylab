/**
 * Rename a written attribute and every reader of it (spec S10).
 *
 * `renameAttr(graph, nodeId, param, newName)` sets the node's `write` param
 * to `newName`, then walks downstream along the wires and rewrites every
 * `attr` / `attr_list` value equal to the old name. The walk stops past a
 * node that writes the old name again: readers below it read that node's
 * attribute, not ours. Never a string replace over the graph JSON.
 *
 * Pure: returns a new graph (the same graph when nothing changes). The
 * caller wraps it in one store `commit`, so the whole rename is one undo
 * step.
 */

import type { Graph, GraphNode, ParamValue } from '../../api/nodebuilder'
import { ReadOnlyGraphError } from './operations'
import { attrListValue, readParamsOf, writeParamsOf, writesOf } from './streamLabels'

/** Rewrite one node's read params from `oldName` to `newName`; null when none matched. */
function rewriteReads(node: GraphNode, oldName: string, newName: string): GraphNode | null {
  let params: Record<string, ParamValue> | null = null
  for (const spec of readParamsOf(node.type)) {
    const v = node.params?.[spec.name]
    if (spec.type === 'attr_list') {
      const list = attrListValue(v)
      if (!list.includes(oldName)) continue
      params ??= { ...node.params }
      params[spec.name] = list.map(n => (n === oldName ? newName : n))
    } else if (v === oldName) {
      params ??= { ...node.params }
      params[spec.name] = newName
    }
  }
  return params ? { ...node, params } : null
}

export function renameAttr(graph: Graph, nodeId: string, param: string, newName: string): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('renameAttr')
  const node = graph.nodes[nodeId]
  if (!node) return graph
  const spec = writeParamsOf(node.type).find(p => p.name === param)
  const current = node.params?.[param] ?? spec?.default
  if (typeof current !== 'string' || current === newName) return graph
  const oldName = current

  const nodes: Record<string, GraphNode> = {
    ...graph.nodes,
    [nodeId]: { ...node, params: { ...node.params, [param]: newName } },
  }

  // Nodes whose output still carries our attribute under the old name.
  const carriers = new Set<string>([nodeId])
  const queue = [nodeId]
  const visited = new Set<string>([nodeId])
  while (queue.length > 0) {
    const from = queue.shift()!
    for (const w of graph.wires) {
      if (w.from !== from || visited.has(w.to)) continue
      const down = nodes[w.to]
      if (!down) continue
      visited.add(w.to)
      const rewritten = rewriteReads(down, oldName, newName)
      if (rewritten) nodes[w.to] = rewritten
      // A node that writes the old name itself replaces ours below it.
      if (writesOf(down).some(s => s.name === oldName)) continue
      carriers.add(w.to)
      queue.push(w.to)
    }
  }

  // v2 wires may still carry the name as a label.
  let wiresChanged = false
  const wires = graph.wires.map(w => {
    if (w.attr !== oldName || !carriers.has(w.from)) return w
    wiresChanged = true
    return { ...w, attr: newName }
  })

  return { ...graph, nodes, wires: wiresChanged ? wires : graph.wires }
}
