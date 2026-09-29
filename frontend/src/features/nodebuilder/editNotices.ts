/**
 * editNotices.ts — pure helpers behind the "Edit this graph" copy.
 *
 * prepareEditableCopy turns a read-only auto-render graph into an editable one
 * and reports which regime nodes it removed. findUnsupportedNodes lists the
 * nodes the graph compiler cannot run yet, so the editor can warn before Run.
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
  /** Ids of the regime nodes that were taken out of the copy. */
  regimeRemoved: string[]
}

function isRegimePath(id: string): boolean {
  return id.startsWith('/regime/')
}

/**
 * Copy an auto-render graph into an editable graph.
 *
 * Regime nodes (and their wires) are removed, because the graph backtest
 * refuses them. The read-only view keeps showing them; only the copy drops
 * them, and the caller shows a banner so the user knows.
 */
export function prepareEditableCopy(graph: Graph): EditableCopy {
  const nodes: Record<string, GraphNode> = {}
  const regimeRemoved: string[] = []
  for (const [id, n] of Object.entries(graph.nodes)) {
    if (isRegimePath(id)) {
      regimeRemoved.push(id)
      continue
    }
    nodes[id] = {
      ...n,
      position: [n.position[0], n.position[1] * EDIT_VERTICAL_SPACING],
    }
  }
  const wires = graph.wires.filter(w => !isRegimePath(w.from) && !isRegimePath(w.to))
  return {
    graph: { ...graph, readOnly: false, nodes, wires },
    regimeRemoved,
  }
}

export interface UnsupportedNode {
  id: string
  type: string
  /**
   * Why compile refuses it:
   *   "unknown"   — not in the catalog;
   *   "inactive"  — in the catalog but compile cannot run it (a wired Size or
   *                 Stop terminal, a slope condition...);
   *   "direction" — a per-direction (long/short) setting;
   *   "rule"      — a comparison carrying a rule detail it cannot draw.
   */
  reason: 'unknown' | 'inactive' | 'direction' | 'rule'
  /** The extra detail for "direction" and "rule" (e.g. "long", "atr_pct"). */
  detail?: string
}

// Terminals compile ignores while nothing is wired into them.
const IGNORED_WHEN_UNWIRED: ReadonlySet<string> = new Set(['size', 'stop'])

/**
 * List the nodes in `graph` that the compiler will refuse, mirroring
 * backend/nodebuilder/compile.py: node types missing from the frontend
 * catalog, catalog entries marked compileActive=false (Size/Stop only when
 * wired, since compile ignores them otherwise), per-direction Settings nodes,
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
  const wiredInto = new Set(graph.wires.map(w => w.to))
  const out: UnsupportedNode[] = []
  for (const [id, n] of Object.entries(graph.nodes)) {
    const entry = byName.get(n.type)
    const params = n.params ?? {}
    if (entry == null) {
      out.push({ id, type: n.type, reason: 'unknown' })
    } else if (!entry.compileActive) {
      if (IGNORED_WHEN_UNWIRED.has(n.type) && !wiredInto.has(id)) continue
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

/** Banner text for the unsupported-node list, or null when there is nothing to warn about. */
export function describeUnsupportedNodes(list: UnsupportedNode[]): string | null {
  if (list.length === 0) return null
  const items = list
    .map(u => `${u.type}${u.detail ? ` [${u.detail}]` : ''} (${u.id})`)
    .join(', ')
  const noun = list.length === 1 ? 'This node cannot' : 'These nodes cannot'
  return `${noun} run yet, so Run Backtest will fail until you remove ${list.length === 1 ? 'it' : 'them'}: ${items}`
}

export const REGIME_REMOVED_TEXT = 'Regime removed: this copy trades without the regime filter'
