/**
 * Graph helpers for code at three levels (F435 W7, specs S44 to S49).
 *
 * Pure functions over the graph: no React, no store, no Monaco. The store
 * calls them inside `commit`, so every change here is one undo step.
 *
 * - Level 1, a param expression: `params[name] = { expr }`. The literal it
 *   replaced is kept in `meta.literal_backup[name]` so "Use literal value"
 *   can bring it back (S44 must-not).
 * - Level 2, a node code block: `Node.code`.
 * - Level 3, a Wrangle node: its whole body is `Node.code`.
 * - Spare params (S48): `Node.spare_params` comes from the last good
 *   `parse_code`; values live in `Node.params` and are never deleted when a
 *   row goes away.
 */

import { isExprValue, type Graph, type GraphNode, type ParamValue, type SpareParamSpec } from '../../../api/nodebuilder'
import type { ParamSpec } from '../catalog'

/** The Wrangle node type (plan W7, S46). */
export const WRANGLE_TYPE = 'wrangle'

/** Ticker params that never take code (D1, `ticker_param_not_codeable`). */
const TICKER_FIXED: ReadonlySet<string> = new Set(['symbol', 'interval', 'prefix'])

/** Param types that can switch to an expression (S44 placement). */
const CODEABLE_TYPES: ReadonlySet<string> = new Set(['number', 'int', 'bool', 'string', 'select'])

/**
 * True when this param row may show the `=` toggle: its spec allows code,
 * its type is a plain value, and it is not a Ticker symbol/interval/prefix.
 */
export function isCodeableParam(nodeType: string | undefined, spec: Pick<ParamSpec, 'type' | 'code_able' | 'name'> | undefined, paramKey: string): boolean {
  if (!spec) return false
  if (spec.code_able === false) return false
  if (!CODEABLE_TYPES.has(spec.type)) return false
  if (nodeType === 'ticker' && TICKER_FIXED.has(paramKey)) return false
  return true
}

/** The param type `parse_code` expects for an expression (`expected.type`). */
export function expectedTypeOf(spec: Pick<ParamSpec, 'type'> | undefined): 'int' | 'float' | 'bool' | 'string' {
  switch (spec?.type) {
    case 'int': return 'int'
    case 'number': return 'float'
    case 'bool': return 'bool'
    default: return 'string'
  }
}

/** True when this node carries any code: an `{expr}` param, a code block, or it is a Wrangle. */
export function nodeHasCode(node: Pick<GraphNode, 'type' | 'params' | 'code'> | undefined | null): boolean {
  if (!node) return false
  if (node.type === WRANGLE_TYPE) return true
  if (typeof node.code === 'string' && node.code.trim() !== '') return true
  return Object.values(node.params ?? {}).some(isExprValue)
}

/** True when any node of the graph carries code (S49 `hasCode(graph)`). */
export function hasCode(graph: Pick<Graph, 'nodes'> | null | undefined): boolean {
  if (!graph) return false
  return Object.values(graph.nodes).some(nodeHasCode)
}

/** Ids of the nodes that carry code, in graph order. */
export function codeNodeIds(graph: Pick<Graph, 'nodes'> | null | undefined): string[] {
  if (!graph) return []
  return Object.values(graph.nodes).filter(nodeHasCode).map(n => n.id)
}

/** The node with a patch, or the same graph when the node is gone. */
function patchNode(graph: Graph, nodeId: string, patch: (n: GraphNode) => GraphNode): Graph {
  const node = graph.nodes[nodeId]
  if (!node) return graph
  const next = patch(node)
  if (next === node) return graph
  return { ...graph, nodes: { ...graph.nodes, [nodeId]: next } }
}

function backupOf(node: GraphNode): Record<string, unknown> {
  const b = node.meta?.literal_backup
  return b && typeof b === 'object' && !Array.isArray(b) ? (b as Record<string, unknown>) : {}
}

/**
 * The literal to show when a row leaves code mode: the backup, else the
 * catalog default.
 */
export function literalFor(node: GraphNode, param: string, fallback: unknown): unknown {
  const b = backupOf(node)
  return param in b ? b[param] : fallback
}

/**
 * Put an expression on a param. The literal it replaces (when it is not
 * already an expression) goes into `meta.literal_backup[param]`.
 */
export function setParamExpr(graph: Graph, nodeId: string, param: string, expr: string): Graph {
  return patchNode(graph, nodeId, node => {
    const old = node.params[param]
    if (isExprValue(old) && old.expr === expr) return node
    const meta = isExprValue(old) || old === undefined
      ? node.meta
      : { ...(node.meta ?? {}), literal_backup: { ...backupOf(node), [param]: old } }
    return { ...node, meta, params: { ...node.params, [param]: { expr } } }
  })
}

/** Leave code mode: write a literal value (the expression stays in undo history). */
export function setParamLiteral(graph: Graph, nodeId: string, param: string, value: unknown): Graph {
  return patchNode(graph, nodeId, node => ({
    ...node,
    params: { ...node.params, [param]: value as ParamValue },
  }))
}

/** The text an expression field starts with when a row enters code mode (`14`, `"sma"`, `True`). */
export function initialExprText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') {
    // A bool stored as text by the select editor reads as Python's bool.
    if (value === 'true') return 'True'
    if (value === 'false') return 'False'
    return JSON.stringify(value)
  }
  if (typeof value === 'boolean') return value ? 'True' : 'False'
  if (Array.isArray(value)) return JSON.stringify(value)
  return String(value)
}

/** Set (or clear, with null) a node's code block. */
export function setNodeCode(graph: Graph, nodeId: string, code: string | null): Graph {
  return patchNode(graph, nodeId, node => {
    const next = code === '' ? null : code
    if ((node.code ?? null) === next) return node
    return { ...node, code: next }
  })
}

function sameSpec(a: SpareParamSpec, b: SpareParamSpec): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Apply the spare params of a good parse (S48):
 * - the list becomes `Node.spare_params`;
 * - a new name gets its default in `params` (unless a value is already
 *   there from before, which is kept);
 * - a name whose type changed gets the new default, and is reported back
 *   so the caller can show the toast;
 * - a name that left the list keeps its value in `params`.
 * Returns the same graph when nothing changed.
 */
export function applySpareParams(
  graph: Graph,
  nodeId: string,
  specs: readonly SpareParamSpec[],
): { graph: Graph; typeChanged: SpareParamSpec[] } {
  const typeChanged: SpareParamSpec[] = []
  const next = patchNode(graph, nodeId, node => {
    const prev = node.spare_params ?? []
    const prevByName = new Map(prev.map(p => [p.name, p]))
    const params = { ...node.params }
    let paramsChanged = false
    for (const spec of specs) {
      const old = prevByName.get(spec.name)
      if (old && old.type !== spec.type) {
        params[spec.name] = spec.default as ParamValue
        paramsChanged = true
        typeChanged.push(spec)
      } else if (!(spec.name in params)) {
        params[spec.name] = spec.default as ParamValue
        paramsChanged = true
      }
    }
    const listSame = prev.length === specs.length && prev.every((p, i) => sameSpec(p, specs[i]))
    if (listSame && !paramsChanged) return node
    return { ...node, spare_params: [...specs], params: paramsChanged ? params : node.params }
  })
  return { graph: next, typeChanged }
}

/**
 * The params a row list shows: on a node with code (or spare params), the
 * params the catalog does not know are spare values, current or stale
 * (S48), and the spare list draws the current ones. So they are left out.
 */
export function visibleParams<T extends Record<string, unknown>>(
  params: T,
  specNames: ReadonlySet<string>,
  node: Pick<GraphNode, 'type' | 'code' | 'spare_params'> | null | undefined,
): T {
  if (!node || !(node.spare_params?.length || (node.code && node.code.trim()) || node.type === WRANGLE_TYPE)) return params
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(params)) if (specNames.has(k)) out[k] = v
  return out as T
}

/** The default code of a new Wrangle, with its write name made unique (S46). */
export function defaultWrangleCode(takenAttrs: ReadonlySet<string>): string {
  let name = 'out'
  for (let k = 2; takenAttrs.has(`@${name}`); k++) name = `out_${k}`
  return `# write attributes with @name = expr\n@${name} = @close`
}

/** The empty-editor placeholder of a node code block (S45). */
export const NODE_CODE_PLACEHOLDER = "# runs after this node's own compute\n# @rsi = sl.ema(@rsi, chi(\"smooth\", default=3))"

/** Lines in a piece of code, for `code · 3 lines`. */
export function lineCount(code: string | null | undefined): number {
  if (!code) return 0
  return code.replace(/\n+$/, '').split('\n').length
}

/** Source size in UTF-8 bytes (S45 footer `4 of 8 KB`). */
export function byteLength(code: string): number {
  return new TextEncoder().encode(code).length
}
