/**
 * What the Data Sheet shows (S25 "Target resolution").
 *
 * Follow mode, in order: the selected wire; else the selected (primary)
 * node; else the display-flag node of the network on screen; else nothing.
 * Pin mode keeps one target until it is unpinned or deleted.
 *
 * A network or boundary node (an Output Group, Subnet or Regime frame, or a
 * port) is never a target: flatten removes it before the cook, so it has no
 * stream and /inspect would answer 404 target_not_found.
 */

import type { Graph } from '../../../api/nodebuilder'
import type { InspectTarget } from '../../../api/nodebuilderInspect'
import { BOUNDARY_TYPES, NETWORK_TYPES } from '../rfMapping'

export type SheetTarget =
  | { kind: 'wire'; wireId: string; fromId: string; toId: string }
  | { kind: 'node'; nodeId: string }
  | { kind: 'display'; nodeId: string }

export interface SheetSelection {
  selectedNodeId: string | null
  selectedWireIds: readonly string[]
}

/** The target in follow mode, or null. `parentId` is the network on screen (null = root). */
export function resolveSheetTarget(graph: Graph | null, sel: SheetSelection, parentId: string | null): SheetTarget | null {
  if (!graph) return null
  for (const wid of sel.selectedWireIds) {
    const w = graph.wires.find(x => x.id === wid)
    if (w && graph.nodes[w.from] && graph.nodes[w.to]) return { kind: 'wire', wireId: w.id, fromId: w.from, toId: w.to }
  }
  const selected = sel.selectedNodeId ? graph.nodes[sel.selectedNodeId] : undefined
  if (selected && hasStream(selected.type)) return { kind: 'node', nodeId: selected.id }
  for (const n of Object.values(graph.nodes)) {
    if (n.display && hasStream(n.type) && (n.parent ?? null) === parentId) return { kind: 'display', nodeId: n.id }
  }
  return null
}

/** False for network and boundary nodes, which never cook. */
function hasStream(type: string): boolean {
  return !NETWORK_TYPES.has(type) && !BOUNDARY_TYPES.has(type)
}

/** True while the target's node or wire is still in the graph. */
export function targetExists(graph: Graph | null, t: SheetTarget | null): boolean {
  if (!graph || !t) return false
  if (t.kind === 'wire') return graph.wires.some(w => w.id === t.wireId)
  return t.nodeId in graph.nodes
}

/** The contract's `target` for a sheet target. */
export function inspectTargetOf(t: SheetTarget): InspectTarget {
  return t.kind === 'wire' ? { wire_id: t.wireId } : { node_id: t.nodeId }
}

/** A stable string for a target (request keys, pin comparisons). */
export function targetKey(t: SheetTarget | null): string {
  if (!t) return ''
  return t.kind === 'wire' ? `w:${t.wireId}` : `n:${t.nodeId}`
}

/** The node whose stream the sheet shows: the node itself, or the wire's source. */
export function streamNodeId(t: SheetTarget): string {
  return t.kind === 'wire' ? t.fromId : t.nodeId
}

/** The target chip's text: `wire a → b`, `node rsi`, `display rsi`. */
export function targetLabel(graph: Graph | null, t: SheetTarget): string {
  const name = (id: string) => graph?.nodes[id]?.name ?? id
  if (t.kind === 'wire') {
    const w = graph?.wires.find(x => x.id === t.wireId)
    return `wire ${name(w?.from ?? t.fromId)} → ${name(w?.to ?? t.toId)}`
  }
  return `${t.kind === 'display' ? 'display' : 'node'} ${name(t.nodeId)}`
}

/** True when the streamed node is bypassed (the server then sends its input stream). */
export function targetBypassed(graph: Graph | null, t: SheetTarget): boolean {
  return graph?.nodes[streamNodeId(t)]?.bypass === true
}
