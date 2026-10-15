/**
 * `ch()` paths on the client (F435 W7, plan "ch() scoping rules").
 *
 * The backend resolves references for real (kernel/params.py, against the
 * pre-flatten graph). The editor needs the same answer for jump-to and
 * "read by" text, and the shared vectors (backend/tests/nodebuilder/vectors/
 * ch_refs.json) keep the two in step. The editor's graph is the pre-flatten
 * graph by nature, so no flatten is involved here.
 *
 * Rules, from the calling node:
 * - a bare name (`threshold`, `@rsi`) is the calling node's own param or attribute;
 * - each `..` goes up one network level (the calling node first goes to its
 *   parent network), each other segment steps into the child of that name;
 * - so `../other/param` is a sibling's param, `../other/@attr` its output
 *   attribute, and `../name` a param of the enclosing network;
 * - a leading `/` starts at the root: `/shared/spread/@spread`.
 * The last segment is the target (a param name or an `@attr`).
 */

import type { Graph } from '../../../api/nodebuilder'

export interface ChTarget {
  /** The node that holds the target. */
  node_id: string
  /** A param name or an `@attr`. */
  target: string
}

/** Children of a network (null = the root) by name. */
function childNamed(graph: Pick<Graph, 'nodes'>, parent: string | null, name: string): string | null {
  for (const n of Object.values(graph.nodes)) {
    if ((n.parent ?? null) === parent && n.name === name) return n.id
  }
  return null
}

/** Where a `ch()` path points from `fromId`, or null when it leads nowhere. */
export function resolveChPath(graph: Pick<Graph, 'nodes'>, fromId: string, path: string): ChTarget | null {
  const from = graph.nodes[fromId]
  if (!from || !path) return null
  if (!path.includes('/')) return { node_id: fromId, target: path }
  const absolute = path.startsWith('/')
  const segments = (absolute ? path.slice(1) : path).split('/')
  const target = segments.pop()
  if (!target) return null
  // The cursor is a node id, or null for the root network.
  let cursor: string | null
  let atNode: boolean
  if (absolute) {
    cursor = null
    atNode = false
  } else {
    cursor = fromId
    atNode = true
  }
  for (const seg of segments) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') {
      if (atNode) {
        // From a node, `..` is the network it sits in.
        cursor = graph.nodes[cursor as string]?.parent ?? null
        atNode = cursor !== null
        if (cursor === null) atNode = false
      } else {
        return null
      }
      continue
    }
    // Step into a child of the network the cursor is (or of the root).
    const parent: string | null = cursor
    const child = childNamed(graph, parent, seg)
    if (!child) return null
    cursor = child
    atNode = true
  }
  if (cursor === null) return null
  return { node_id: cursor, target }
}
