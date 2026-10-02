/**
 * editNotices.ts — pure helpers behind the "Edit this graph" copy.
 *
 * prepareEditableCopy turns a read-only auto-render graph into an editable
 * one. findUnsupportedNodes lists the nodes the graph compiler cannot run
 * yet, so the editor can warn before Run.
 */

import type { Graph, GraphNode } from '../../api/nodebuilder'
import { NODE_CATALOG, type NodeCatalogEntry } from './catalog'

/**
 * Stop-gap for overlapping nodes (bug 8). The auto-render layout is sized for
 * the read-only view; edit mode adds parameter rows, so nodes grow taller and
 * cover each other's ports. Spreading rows apart keeps the ports reachable
 * until a real tidy-layout lands.
 */
export const EDIT_VERTICAL_SPACING = 1.8

export interface EditableCopy {
  graph: Graph
}

/**
 * Copy an auto-render graph into an editable graph. Since W5 the graph runs
 * regime (the `regime_net` network and the `regime` terminal), so nothing is
 * taken out: the copy keeps every node and wire, rows spread apart.
 */
export function prepareEditableCopy(graph: Graph): EditableCopy {
  const nodes: Record<string, GraphNode> = {}
  for (const [id, n] of Object.entries(graph.nodes)) {
    nodes[id] = {
      ...n,
      position: [n.position[0], n.position[1] * EDIT_VERTICAL_SPACING],
    }
  }
  return { graph: { ...graph, readOnly: false, nodes, wires: [...graph.wires] } }
}

export interface UnsupportedNode {
  id: string
  type: string
  /**
   * Why compile refuses it:
   *   "unknown"   — not in the catalog;
   *   "inactive"  — in the catalog but compile cannot run it (a slope
   *                 condition...);
   *   "direction" — a per-direction (long/short) setting;
   *   "rule"      — a comparison carrying a rule detail it cannot draw.
   */
  reason: 'unknown' | 'inactive' | 'direction' | 'rule'
  /** The extra detail for "direction" and "rule" (e.g. "long", "atr_pct"). */
  detail?: string
}

/**
 * List the nodes in `graph` that the compiler will refuse, mirroring
 * backend/nodebuilder/compile.py: node types missing from the frontend
 * catalog, catalog entries marked compileActive=false, per-direction Settings nodes,
 * and comparisons carrying a condition_extra. A bypassed Settings node or
 * comparison is skipped by compile before those checks, so it is not listed.
 * Order follows the graph's node order.
 */
export function findUnsupportedNodes(
  graph: Graph | null,
  catalog: readonly NodeCatalogEntry[] = NODE_CATALOG,
): UnsupportedNode[] {
  if (graph == null) return []
  const byName = new Map(catalog.map(e => [e.name, e]))
  const out: UnsupportedNode[] = []
  for (const [id, n] of Object.entries(graph.nodes)) {
    const entry = byName.get(n.type)
    const params = n.params ?? {}
    if (entry == null) {
      out.push({ id, type: n.type, reason: 'unknown' })
    } else if (!entry.compileActive) {
      out.push({ id, type: n.type, reason: 'inactive' })
    } else if (n.bypass) {
      continue
    } else if (entry.cat === 'settings' && params.direction != null) {
      out.push({ id, type: n.type, reason: 'direction', detail: String(params.direction) })
    } else if (entry.cat === 'comparison' && params.condition_extra != null) {
      out.push({ id, type: n.type, reason: 'rule', detail: String(params.condition_extra) })
    }
  }
  return out
}

/** S07 `unsupported_nodes` copy, around the list of types. */
export const UNSUPPORTED_PREFIX = 'Unsupported in graphs: '
export const UNSUPPORTED_SUFFIX = '. The graph cannot run until these are replaced.'

/** How one unsupported node is listed: its type, plus the detail that makes it unsupported. */
export function unsupportedLabel(u: UnsupportedNode): string {
  return `${u.type}${u.detail ? ` [${u.detail}]` : ''}`
}

/**
 * S07 banner text for the unsupported-node list, or null when there is
 * nothing to warn about. The banner itself renders each entry as a link that
 * selects the node; this is the plain form of the same sentence.
 */
export function describeUnsupportedNodes(list: UnsupportedNode[]): string | null {
  if (list.length === 0) return null
  return `${UNSUPPORTED_PREFIX}${list.map(unsupportedLabel).join(', ')}${UNSUPPORTED_SUFFIX}`
}
