/**
 * Promoted params (spec S40, plan W6 item 6.D).
 *
 * A network node can expose one setting of a direct child as its own
 * param, as a Houdini promoted parameter. The network keeps
 * `promoted: [{name, label, target, type, default}]` and the value in
 * `params[name]`; `target` is the child's path relative to the network
 * plus the param, e.g. "rsi/period". The backend puts the value into the
 * child's param in `flatten`, so the frontend stores nothing on the child:
 * the promoted list on the parent is the one source of truth.
 *
 * Every function here is pure and returns a new graph; each is committed
 * as one undo step by its caller.
 */

import type { Graph, GraphNode, GraphPromotedParam, ParamValue } from '../../../api/nodebuilder'
import type { ParamSpec, ParamTypeSpec } from '../catalog'
import { ReadOnlyGraphError } from '../operations'
import { findByPath, sanitizeName, uniqueName } from '../paths'
import { BOUNDARY_TYPES, NETWORK_TYPES } from '../rfMapping'
import { paramSpecsOf } from '../streamLabels'
import { insideLockedAsset } from './collapse'

/** Promoted-param names: the node-name rule (S40 `Name` field). */
export const PROMOTED_NAME_RE = /^[a-z_][a-z0-9_]{0,63}$/

/** S40 copy. */
export const PROMOTE_TEXT = {
  menu: 'Promote to parent…',
  unpromote: 'Unpromote',
  goToPromoted: 'Go to promoted parameter',
  nameInvalid: 'Use a-z, 0-9 and _, starting with a letter or _ (at most 64).',
  nameTaken: 'This network already has a param with that name.',
} as const

/** The tooltip on a promoted child row. */
export function promotedTooltip(name: string): string {
  return `Promoted to ../${name}. Edit it on the subnet.`
}

/** The tooltip on a promoted row whose target is gone. */
export function targetMissingTooltip(target: string): string {
  return `Target ${target} no longer exists.`
}

/** The popover title (S40). */
export function promoteTitle(param: string, network: string): string {
  return `Promote ${param} to ${network}`
}

/** The hint under the Name field (S40). */
export function promoteHint(name: string): string {
  return `Used in ch("../${name}") and in the file.`
}

/** True when this node type is a network (it can hold promoted params). */
export function isNetworkNode(node: Pick<GraphNode, 'type'> | undefined): boolean {
  return !!node && NETWORK_TYPES.has(node.type)
}

const NO_PROMOTED: GraphPromotedParam[] = []

/** The promoted list of a node (one shared empty list when it has none, so selectors stay stable). */
export function promotedOf(node: Pick<GraphNode, 'promoted'> | undefined | null): GraphPromotedParam[] {
  return Array.isArray(node?.promoted) ? node!.promoted! : NO_PROMOTED
}

/** Split a target "rsi/period" into the child path and the param name. */
export function splitTarget(target: string): { path: string; param: string } | null {
  const i = target.lastIndexOf('/')
  if (i <= 0 || i === target.length - 1) return null
  return { path: target.slice(0, i), param: target.slice(i + 1) }
}

/** The child id and param a promoted target points at, or null when it no longer resolves. */
export function resolveTarget(graph: Pick<Graph, 'nodes'>, networkId: string, target: string): { nodeId: string; param: string } | null {
  const parts = splitTarget(target)
  if (!parts) return null
  const nodeId = findByPath(graph, parts.path, networkId)
  if (!nodeId) return null
  return { nodeId, param: parts.param }
}

/**
 * The promotion that drives a child's param, or null. The child's parent
 * must be a network whose promoted list targets `<child name>/<param>`.
 */
export function promotionOf(
  graph: Pick<Graph, 'nodes'> | null | undefined,
  nodeId: string,
  param: string,
): { networkId: string; entry: GraphPromotedParam } | null {
  const node = graph?.nodes[nodeId]
  if (!graph || !node?.parent) return null
  const net = graph.nodes[node.parent]
  const list = promotedOf(net)
  if (list.length === 0) return null
  const want = `${node.name}/${param}`
  const entry = list.find(p => p.target === want)
  return entry ? { networkId: net.id, entry } : null
}

/** The value a promoted param has on its network (stored value, else its default). */
export function promotedValue(net: Pick<GraphNode, 'params'>, entry: GraphPromotedParam): unknown {
  return entry.name in net.params ? net.params[entry.name] : entry.default
}

/** A default promoted name: `<node>_<param>`, made valid and unique on the network. */
export function defaultPromotedName(graph: Graph, nodeId: string, param: string): string {
  const node = graph.nodes[nodeId]
  const net = node?.parent ? graph.nodes[node.parent] : undefined
  const base = sanitizeName(`${node?.name ?? 'node'}_${param}`)
  return uniqueName(base, takenNames(net))
}

/** The fields of a network the name checks read (`type` for its own catalog params). */
type NamedNet = Pick<GraphNode, 'params' | 'promoted'> & { type?: string }

/**
 * Names a new promoted param may not use on this network: its promoted
 * names, its stored params, and its own catalog params even when not
 * stored yet (a group's `direction`, `ticker`: FE-11).
 */
export function takenNames(net: NamedNet | undefined, except?: string): Set<string> {
  const out = new Set<string>()
  if (!net) return out
  for (const p of promotedOf(net)) if (p.name !== except) out.add(p.name)
  for (const k of Object.keys(net.params ?? {})) if (k !== except) out.add(k)
  for (const spec of paramSpecsOf(net.type)) if (spec.name !== except) out.add(spec.name)
  return out
}

/** Why `name` cannot name a promoted param on this network, or null. */
export function promotedNameProblem(net: NamedNet | undefined, name: string, except?: string): string | null {
  if (!PROMOTED_NAME_RE.test(name)) return PROMOTE_TEXT.nameInvalid
  if (takenNames(net, except).has(name)) return PROMOTE_TEXT.nameTaken
  return null
}

/** The catalog spec of a child param (null when the catalog does not know it). */
export function childParamSpec(graph: Pick<Graph, 'nodes'>, nodeId: string, param: string): ParamSpec | null {
  const node = graph.nodes[nodeId]
  return node ? paramSpecOfNode(node, param) : null
}

/** The spec of one param of a node: its catalog spec, or its promoted entry when it is a network's promoted param. */
export function paramSpecOfNode(node: GraphNode, param: string): ParamSpec | null {
  // A promoted param of a nested network: its entry is the spec.
  const nested = promotedOf(node).find(p => p.name === param)
  if (nested) {
    return { name: nested.name, type: nested.type as ParamSpec['type'], label: nested.label, default: nested.default }
  }
  return paramSpecsOf(node.type).find(p => p.name === param) ?? null
}

/** Why this child param cannot be promoted, or null. */
export function promoteProblem(graph: Graph | null, nodeId: string, param: string): string | null {
  if (!graph) return 'No graph'
  if (graph.readOnly) return 'Read-only graph'
  const node = graph.nodes[nodeId]
  if (!node) return 'No node'
  const net = node.parent ? graph.nodes[node.parent] : undefined
  if (!net || !isNetworkNode(net)) return 'Only a node inside a network can promote a param'
  if (insideLockedAsset(graph.nodes, net.id)) return 'This asset is locked. Unlock it first.'
  // The backend's rules (kernel/flatten.py _check_promoted): a network port
  // node's params never promote, and of a network node only its own
  // promoted params can be promoted again (not direction, ticker, ...).
  if (BOUNDARY_TYPES.has(node.type)) return 'A network port param cannot be promoted'
  if (isNetworkNode(node) && !promotedOf(node).some(p => p.name === param)) {
    return "Only a network's promoted params can be promoted again"
  }
  if (promotionOf(graph, nodeId, param)) return 'Already promoted'
  return null
}

export interface PromoteInput {
  name: string
  label: string
}

/**
 * Promote `nodeId`'s `param` to its parent network. The network gains the
 * entry and `params[name]` = the child's current value (also the entry's
 * default). The child is left unchanged.
 */
export function promoteParam(graph: Graph, nodeId: string, param: string, input: PromoteInput): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('promote param')
  const problem = promoteProblem(graph, nodeId, param)
  if (problem) throw new Error(problem)
  const node = graph.nodes[nodeId]
  const net = graph.nodes[node.parent!]
  const nameProblem = promotedNameProblem(net, input.name)
  if (nameProblem) throw new Error(nameProblem)
  const spec = childParamSpec(graph, nodeId, param)
  const value = param in node.params ? node.params[param] : spec?.default ?? null
  const type = spec?.type ?? (typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'bool' : 'string')
  const entry: GraphPromotedParam = {
    name: input.name,
    label: input.label.trim() || spec?.label || param,
    target: `${node.name}/${param}`,
    type,
    default: value,
  }
  const nextNet: GraphNode = {
    ...net,
    promoted: [...promotedOf(net), entry],
    params: { ...net.params, [input.name]: value as ParamValue },
  }
  return { ...graph, nodes: { ...graph.nodes, [net.id]: nextNet } }
}

/**
 * Remove the promoted param `name` from `networkId`. Its current value is
 * copied back into the child's param, so the child keeps working with the
 * last value the user set on the network.
 */
export function unpromoteParam(graph: Graph, networkId: string, name: string): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('unpromote param')
  const net = graph.nodes[networkId]
  if (!net) return graph
  const list = promotedOf(net)
  const entry = list.find(p => p.name === name)
  if (!entry) return graph
  const value = promotedValue(net, entry)
  const params = { ...net.params }
  delete params[name]
  const nodes = { ...graph.nodes, [networkId]: { ...net, promoted: list.filter(p => p !== entry), params } }
  const target = resolveTarget(graph, networkId, entry.target)
  if (target && nodes[target.nodeId]) {
    const child = nodes[target.nodeId]
    nodes[target.nodeId] = { ...child, params: { ...child.params, [target.param]: value as ParamValue } }
  }
  return { ...graph, nodes }
}

/** Change a promoted param's label. */
export function relabelPromoted(graph: Graph, networkId: string, name: string, label: string): Graph {
  return editEntry(graph, networkId, name, e => ({ ...e, label }))
}

/** Change a promoted param's name; its stored value moves with it. */
export function renamePromoted(graph: Graph, networkId: string, name: string, newName: string): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('rename promoted param')
  const net = graph.nodes[networkId]
  if (!net || name === newName) return graph
  const problem = promotedNameProblem(net, newName, name)
  if (problem) throw new Error(problem)
  const next = editEntry(graph, networkId, name, e => ({ ...e, name: newName }))
  const n = next.nodes[networkId]
  if (!(name in n.params)) return next
  const params: Record<string, ParamValue> = {}
  for (const [k, v] of Object.entries(n.params)) params[k === name ? newName : k] = v
  return { ...next, nodes: { ...next.nodes, [networkId]: { ...n, params } } }
}

/** Move a promoted param up (-1) or down (+1) in the list. */
export function movePromoted(graph: Graph, networkId: string, name: string, delta: number): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('reorder promoted params')
  const net = graph.nodes[networkId]
  const list = promotedOf(net)
  const i = list.findIndex(p => p.name === name)
  const j = i + delta
  if (i < 0 || j < 0 || j >= list.length) return graph
  // Move the entry to j (a drag over several rows), not a swap (FE-08).
  const next = [...list]
  const [moved] = next.splice(i, 1)
  next.splice(j, 0, moved)
  return { ...graph, nodes: { ...graph.nodes, [networkId]: { ...net, promoted: next } } }
}

function editEntry(graph: Graph, networkId: string, name: string, fn: (e: GraphPromotedParam) => GraphPromotedParam): Graph {
  if (graph.readOnly) throw new ReadOnlyGraphError('edit promoted param')
  const net = graph.nodes[networkId]
  if (!net) return graph
  const list = promotedOf(net)
  const i = list.findIndex(p => p.name === name)
  if (i < 0) return graph
  const next = [...list]
  next[i] = fn(list[i])
  return { ...graph, nodes: { ...graph.nodes, [networkId]: { ...net, promoted: next } } }
}

/** The row editor type of a promoted param (as the catalog builds it for a node). */
export function promotedTypeSpec(entry: Pick<GraphPromotedParam, 'type'>, spec: ParamSpec | null): ParamTypeSpec {
  const t = entry.type
  if (t === 'number' || t === 'int') return { type: 'number' }
  if (t === 'select') return { type: 'select', options: spec?.options ?? [] }
  if (t === 'bool') return { type: 'select', options: ['false', 'true'] }
  return { type: 'string' }
}

/** "+3 more in Inspector". */
export function moreInInspectorText(n: number): string {
  return `+${n} more in Inspector`
}
