// Node names and paths (plan D3). TypeScript copy of the helpers in
// backend/nodebuilder/migrate.py. Both run the same vectors in
// backend/tests/nodebuilder/vectors/paths.json; change them together.
//
// A node's path is its parent's path + "/" + its name. Root is "/".
// Wires point at ids, so a rename never breaks a wire.

import { rewriteChPaths } from './code/chRewrite'
//
// Relative paths follow Houdini: they start at the node given as
// relativeTo. ".." goes up one level (so "../rsi" is a sibling called rsi),
// "." stays put, and a bare name goes down into a child.

// Only the fields these helpers read, so any Graph shape fits.
export interface PathNode {
  name: string
  parent: string | null
}
export interface PathGraph<N extends PathNode = PathNode> {
  nodes: Record<string, N>
}

export const NAME_RE = /^[a-z_][a-z0-9_]{0,63}$/
const NAME_MAX = 64
const UUID_RE = /^\/?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/
const NEW_ID_RE = /^n_[a-z0-9]{8}$/
const TRAILING_DIGITS_RE = /^(.*?)(\d+)$/

export type PathErrorCode = 'name_invalid' | 'name_duplicate' | 'node_not_found'

export class PathError extends Error {
  readonly code: PathErrorCode
  readonly nodeId: string
  constructor(code: PathErrorCode, nodeId: string, message: string) {
    super(message)
    this.name = 'PathError'
    this.code = code
    this.nodeId = nodeId
  }
}

// ---------------------------------------------------------------------------
// Names
// ---------------------------------------------------------------------------

export function isValidName(name: unknown): boolean {
  return typeof name === 'string' && NAME_RE.test(name)
}

// Lower-case, every character outside [a-z0-9_] becomes "_", a leading digit
// gets "n_" in front, cut to 64 characters.
export function sanitizeName(raw: string): string {
  let s = String(raw).toLowerCase().replace(/[^a-z0-9_]/gu, '_')
  if (!s) s = 'node'
  if (/^[0-9]/.test(s)) s = 'n_' + s
  return s.slice(0, NAME_MAX)
}

// True for ids that say nothing about the node (UUIDs, n_xxxxxxxx).
// Such nodes are named after their type instead (rsi1, rsi2...).
export function isOpaqueId(nodeId: string): boolean {
  return UUID_RE.test(nodeId) || NEW_ID_RE.test(nodeId)
}

// base, or the next free numbered form of it: rsi -> rsi1, rsi1 -> rsi2,
// cmp_buy_0 -> cmp_buy_1. base must already be a valid name. The result is
// never longer than 64 characters.
export function uniqueName(base: string, taken: Iterable<string>): string {
  const takenSet = new Set(taken)
  if (!takenSet.has(base)) return base
  const m = TRAILING_DIGITS_RE.exec(base)
  let stem = base
  let n = 1
  if (m && m[1]) {
    stem = m[1]
    n = parseInt(m[2], 10) + 1
  }
  for (;;) {
    const suffix = String(n)
    const candidate = stem.slice(0, NAME_MAX - suffix.length) + suffix
    if (!takenSet.has(candidate)) return candidate
    n += 1
  }
}

// The name a node gets when it has none.
export function defaultName(nodeId: string, nodeType: string): string {
  if (isOpaqueId(nodeId)) return sanitizeName(nodeType || 'node').slice(0, NAME_MAX - 1) + '1'
  return sanitizeName(nodeId.replace(/^\/+/, ''))
}

// Names already used by the children of parent (null = root).
export function siblingNames(graph: PathGraph, parent: string | null, exceptId?: string): string[] {
  const out: string[] = []
  for (const [id, node] of Object.entries(graph.nodes)) {
    if (id !== exceptId && node.parent === parent) out.push(node.name)
  }
  return out
}

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

// Absolute path of a node, e.g. /regime/spy_sma. Throws when the node is missing.
export function nodePath(graph: PathGraph, nodeId: string): string {
  const names: string[] = []
  const seen = new Set<string>()
  let current: string | null = nodeId
  while (current !== null) {
    const node: PathNode | undefined = graph.nodes[current]
    if (!node) throw new PathError('node_not_found', current, `No node with id ${current}.`)
    if (seen.has(current)) break // parent loop: the backend rejects these, stop anyway
    seen.add(current)
    names.push(node.name)
    current = node.parent
  }
  return '/' + names.reverse().join('/')
}

function childByName(graph: PathGraph, parent: string | null, name: string): string | null {
  for (const [id, node] of Object.entries(graph.nodes)) {
    if (node.parent === parent && node.name === name) return id
  }
  return null
}

// Id of the node at path, or null when nothing is there. An absolute path
// starts with "/"; anything else is relative to the node relativeTo (or to
// the root when that is null). Going above the root, or a path that ends at
// the root itself, finds nothing.
export function findByPath(graph: PathGraph, path: string, relativeTo: string | null = null): string | null {
  if (typeof path !== 'string' || path === '') return null
  let current: string | null
  if (path.startsWith('/') || relativeTo === null) {
    current = null
  } else {
    if (!(relativeTo in graph.nodes)) return null
    current = relativeTo
  }
  for (const part of path.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (current === null) return null
      current = graph.nodes[current].parent
      continue
    }
    current = childByName(graph, current, part)
    if (current === null) return null
  }
  return current
}

// A copy of graph with the node renamed; the input is not changed. Wires use
// ids, so they need nothing. Stored path strings that pointed at the old path
// get rewritten (see rewritePathRefs). Throws PathError with code
// name_invalid, name_duplicate or node_not_found. It does not check
// readOnly; the caller decides whether the graph may change.
export function renameNode<G extends PathGraph>(graph: G, nodeId: string, newName: string): G {
  const node = graph.nodes[nodeId]
  if (!node) throw new PathError('node_not_found', nodeId, `No node with id ${nodeId}.`)
  if (!isValidName(newName)) {
    throw new PathError(
      'name_invalid',
      nodeId,
      `Name ${JSON.stringify(newName)} is not valid. Use lower-case letters, digits and _, starting with a letter or _ (at most 64).`,
    )
  }
  if (newName === node.name) return graph
  if (childByName(graph, node.parent, newName) !== null) {
    throw new PathError('name_duplicate', nodeId, `Another node here is already called ${newName}.`)
  }
  const oldPath = nodePath(graph, nodeId)
  const next = { ...graph, nodes: { ...graph.nodes, [nodeId]: { ...node, name: newName } } }
  return rewritePathRefs(next, oldPath, nodePath(next, nodeId))
}

// Points stored path strings at a renamed (or moved) node's new path,
// including paths under oldPath. Three kinds of ref store a path: an Output
// Group's `ticker` (W5), each network's promoted-param targets (W6, see
// rewritePromoted) and the ch() strings in a node's code and expression
// params (W7, see rewriteCodeRefs). `onlyNodes` limits the rewrite to
// refs stored on those nodes: a paste renames the pasted copies, and only
// refs inside the pasted set follow (FC-9); refs elsewhere still mean the
// originals.
//
// A relative ref is resolved the way the server resolves a group's ticker
// (against the group, then its parent, then the root). It is rewritten only
// when it pointed into oldPath and no longer lands at the same node; it then
// becomes the absolute new path.
export function rewritePathRefs<G extends PathGraph>(
  graph: G,
  oldPath: string,
  newPath: string,
  onlyNodes?: ReadonlySet<string>,
): G {
  if (oldPath === newPath || oldPath === '/' || oldPath === '') return graph
  const under = (p: string) => p === oldPath || p.startsWith(oldPath + '/')
  const toNew = (p: string) => (under(p) ? newPath + p.slice(oldPath.length) : p)
  const toOld = (p: string) => (p === newPath || p.startsWith(newPath + '/') ? oldPath + p.slice(newPath.length) : p)
  let nodes: Record<string, PathNode> | null = null
  for (const [id, raw] of Object.entries(graph.nodes)) {
    if (onlyNodes && !onlyNodes.has(id)) continue
    let node = raw as PathNode & { type?: string; params?: Record<string, unknown>; promoted?: unknown; code?: unknown }
    const promoted = rewritePromoted(graph, id, node, under, toNew, toOld)
    if (promoted !== null) {
      nodes ??= { ...graph.nodes }
      node = { ...node, promoted }
      nodes[id] = node as PathNode
    }
    const codeUpdate = rewriteCodeRefs(graph, id, node, under, toNew, toOld)
    if (codeUpdate !== null) {
      nodes ??= { ...graph.nodes }
      node = { ...node, ...codeUpdate }
      nodes[id] = node as PathNode
    }
    if (node.type !== 'output_group') continue
    const ref = node.params?.ticker
    if (typeof ref !== 'string' || ref === '') continue
    let next = ref
    if (ref.startsWith('/')) {
      next = toNew(ref)
    } else {
      const bases: Array<string | null> = [id, node.parent, null]
      for (const base of bases) {
        let newBase = '/'
        try {
          newBase = base === null ? '/' : nodePath(graph, base)
        } catch {
          continue
        }
        const oldAbs = joinPath(toOld(newBase), ref)
        if (oldAbs === null || !under(oldAbs)) continue
        const want = toNew(oldAbs)
        next = joinPath(newBase, ref) === want ? ref : want
        break
      }
    }
    if (next === ref) continue
    nodes ??= { ...graph.nodes }
    nodes[id] = { ...node, params: { ...node.params, ticker: next } } as PathNode
  }
  return nodes ? ({ ...graph, nodes } as G) : graph
}

// The promoted-param targets of network `id` after a rename, or null when
// none change (twin of migrate._rewrite_promoted). A target is
// `<node path>/<param>`, relative to the network (W6). It is rewritten when
// the node it named lies on the renamed path and the same string no longer
// reaches it; the new target stays relative to the network. Renaming the
// network itself (or anything above it) moves base and target together.
function rewritePromoted(
  graph: PathGraph,
  id: string,
  node: { promoted?: unknown },
  under: (p: string) => boolean,
  toNew: (p: string) => string,
  toOld: (p: string) => string,
): unknown[] | null {
  const list = node.promoted
  if (!Array.isArray(list) || list.length === 0) return null
  let newBase: string
  try {
    newBase = nodePath(graph, id)
  } catch {
    return null
  }
  const oldBase = toOld(newBase)
  let changed = false
  const next = list.map((p: unknown) => {
    const target = (p as { target?: unknown } | null)?.target
    if (typeof target !== 'string' || target.startsWith('/')) return p
    const i = target.lastIndexOf('/')
    if (i <= 0) return p
    const nodePart = target.slice(0, i)
    const oldAbs = joinPath(oldBase, nodePart)
    if (oldAbs === null || !under(oldAbs)) return p
    const want = toNew(oldAbs)
    if (joinPath(newBase, nodePart) === want) return p
    changed = true
    return { ...(p as object), target: `${relativePath(newBase, want)}/${target.slice(i + 1)}` }
  })
  return changed ? next : null
}

// The ch() paths in node `id`'s code and expression params after a rename
// (twin of migrate._rewrite_code_refs), as the fields that change, or null
// when none do. A ch() path starts at the node that holds the code
// (`../vol/p` is the sibling vol). A path is rewritten when the node it
// named lies on the renamed path and the same string no longer reaches it:
// an absolute path stays absolute, a relative one stays relative to the
// node. Only the string inside the quotes changes (code/chRewrite.ts).
function rewriteCodeRefs(
  graph: PathGraph,
  id: string,
  node: { code?: unknown; params?: Record<string, unknown> },
  under: (p: string) => boolean,
  toNew: (p: string) => string,
  toOld: (p: string) => string,
): { code?: string; params?: Record<string, unknown> } | null {
  const code = typeof node.code === 'string' ? node.code : null
  const params = node.params ?? {}
  const exprOf = (v: unknown): string | null =>
    v && typeof v === 'object' && !Array.isArray(v) && typeof (v as { expr?: unknown }).expr === 'string'
      ? (v as { expr: string }).expr
      : null
  if (!(code !== null && code.includes('/')) && !Object.values(params).some(v => (exprOf(v) ?? '').includes('/'))) {
    return null
  }
  let newBase: string
  try {
    newBase = nodePath(graph, id)
  } catch {
    return null
  }
  const oldBase = toOld(newBase)
  const fix = (path: string): string | null => {
    const i = path.lastIndexOf('/')
    const nodePart = path.slice(0, i)
    const target = path.slice(i + 1)
    if (i < 0 || !target || !nodePart) return null // a bare name, or "/x" (the root has no params)
    const oldAbs = joinPath(oldBase, nodePart)
    if (oldAbs === null || !under(oldAbs)) return null
    const want = toNew(oldAbs)
    if (path.startsWith('/')) return `${want}/${target}`
    if (joinPath(newBase, nodePart) === want) return null
    return `${relativePath(newBase, want)}/${target}`
  }
  const update: { code?: string; params?: Record<string, unknown> } = {}
  if (code !== null) {
    const next = rewriteChPaths(code, fix)
    if (next !== code) update.code = next
  }
  let nextParams: Record<string, unknown> | null = null
  for (const [name, value] of Object.entries(params)) {
    const text = exprOf(value)
    if (text === null) continue
    const next = rewriteChPaths(text, fix)
    if (next === text) continue
    nextParams ??= { ...params }
    // The backend stores {expr} only; keep any other keys the editor holds (meta).
    nextParams[name] = { ...(value as object), expr: next }
  }
  if (nextParams !== null) update.params = nextParams
  return update.code !== undefined || update.params !== undefined ? update : null
}

// The path from the node at absolute `base` to the node at `target`, using
// `..` only when `target` is not inside `base` (twin of migrate._relative_path).
export function relativePath(base: string, target: string): string {
  const b = base.split('/').filter(p => p !== '')
  const t = target.split('/').filter(p => p !== '')
  let common = 0
  while (common < Math.min(b.length, t.length) && b[common] === t[common]) common += 1
  const parts = [...Array<string>(b.length - common).fill('..'), ...t.slice(common)]
  return parts.length > 0 ? parts.join('/') : '.'
}

// The absolute path `rel` names from the node at absolute path `base`
// (Houdini rules, as findByPath). Null when it climbs above the root.
export function joinPath(base: string, rel: string): string | null {
  const parts = rel.startsWith('/') ? [] : base.split('/').filter(p => p !== '')
  for (const part of rel.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (parts.length === 0) return null
      parts.pop()
      continue
    }
    parts.push(part)
  }
  return parts.length === 0 ? null : '/' + parts.join('/')
}
