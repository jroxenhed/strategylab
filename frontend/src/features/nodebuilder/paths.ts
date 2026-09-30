// Node names and paths (plan D3). TypeScript copy of the helpers in
// backend/nodebuilder/migrate.py. Both run the same vectors in
// backend/tests/nodebuilder/vectors/paths.json; change them together.
//
// A node's path is its parent's path + "/" + its name. Root is "/".
// Wires point at ids, so a rename never breaks a wire.
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

// Points stored path strings at a renamed node's new path. Nothing stores
// paths yet; W6 adds promoted-param targets and W7 adds ch() strings, and
// both get rewritten here (including paths under oldPath).
export function rewritePathRefs<G extends PathGraph>(graph: G, _oldPath: string, _newPath: string): G {
  return graph
}
