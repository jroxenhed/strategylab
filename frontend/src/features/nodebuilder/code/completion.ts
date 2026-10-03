/**
 * Completion and hover for the code editors (F435 W7, spec S47), as pure
 * functions. MonacoEditor.tsx turns the plain items into Monaco items; the
 * tests call these directly.
 *
 * - After `@`: the node's input attributes (detail only in an expression),
 *   label `@close`, detail `float · point · from aapl`. Attributes the code
 *   already reads come first, then writer order.
 * - After `sl.`: the helpers from `code_capabilities.functions`, inserted as
 *   a snippet built from the signature (`rsi(${1:x}, ${2:period})`).
 * - Inside `ch("`: paths from the graph (siblings, their params and
 *   attributes, the enclosing network's promoted names, `/`).
 * - Otherwise: `np`, `pd`, `sl` and the `ch` family snippets.
 * Members of `np` and `pd` are not listed (Monaco's word suggestions cover
 * names already in the file).
 */

import type { CodeFunctionInfo } from '../../../api/nodebuilderCode'
import type { Graph, StreamSchema } from '../../../api/nodebuilder'
import { childrenByParent } from '../rfMapping'
import { paramSpecsOf } from '../streamLabels'

export interface CompletionAttr {
  name: string
  dtype: string
  cls: 'point' | 'detail'
  /** Name of the node that wrote it, when known. */
  from: string | null
}

export type CompletionKind = 'Variable' | 'Function' | 'Module' | 'Snippet'

export interface PlainCompletion {
  label: string
  detail: string
  kind: CompletionKind
  insertText: string
  /** insertText is a snippet with `${1:x}` tab stops. */
  snippet: boolean
  documentation?: string
  sortText: string
  /** How many characters before the cursor the item replaces. */
  replace: number
}

export interface CompletionInput {
  /** The text of the current line up to the cursor. */
  textBefore: string
  /** The whole code (to find attributes it already reads). */
  code: string
  attrs: readonly CompletionAttr[]
  /** An expression reads detail attributes only (S44). */
  detailOnly: boolean
  functions: readonly CodeFunctionInfo[]
  /** `ch()` path items for the node (see `chPathItems`). */
  paths: readonly string[]
}

const CH_SNIPPETS: { label: string; insert: string }[] = [
  { label: 'chf', insert: 'chf("${1:name}", default=${2:0.0})' },
  { label: 'chi', insert: 'chi("${1:name}", default=${2:0})' },
  { label: 'chb', insert: 'chb("${1:name}", default=${2:False})' },
  { label: 'chs', insert: 'chs("${1:name}", default="${2}")' },
  { label: 'chv', insert: 'chv("${1:name}", default=${2:(0.0, 0.0)})' },
  { label: 'ch', insert: 'ch("../${1:node}/${2:param}")' },
]

const MODULES: { label: string; detail: string }[] = [
  { label: 'np', detail: 'numpy' },
  { label: 'pd', detail: 'pandas' },
  { label: 'sl', detail: 'StrategyLab helpers' },
]

/** The arguments of a signature such as `sl.rsi(x, period=14)`, as names. */
export function signatureArgs(signature: string): string[] {
  const open = signature.indexOf('(')
  const close = signature.lastIndexOf(')')
  if (open < 0 || close <= open) return []
  const inner = signature.slice(open + 1, close).trim()
  if (!inner) return []
  // Split on top-level commas only (defaults may hold brackets).
  const parts: string[] = []
  let depth = 0
  let cur = ''
  for (const ch of inner) {
    if (ch === '(' || ch === '[' || ch === '{') depth++
    else if (ch === ')' || ch === ']' || ch === '}') depth--
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue }
    cur += ch
  }
  if (cur.trim()) parts.push(cur)
  return parts
    .map(p => p.trim().split('=')[0].trim().replace(/^\*+/, ''))
    .filter(n => n && n !== '/' && n !== '*')
}

/** The snippet for one helper: `rsi(${1:x}, ${2:period})`. */
export function helperSnippet(fn: Pick<CodeFunctionInfo, 'name' | 'signature'>): string {
  const short = fn.name.replace(/^sl\./, '')
  const args = signatureArgs(fn.signature)
  return `${short}(${args.map((a, i) => `\${${i + 1}:${a}}`).join(', ')})`
}

/** `float · point · from aapl` */
export function attrDetail(a: CompletionAttr): string {
  return `${a.dtype} · ${a.cls}${a.from ? ` · from ${a.from}` : ''}`
}

/** Attribute names (`@close`) the code already reads, in order of appearance. */
function readsInCode(code: string): string[] {
  const out: string[] = []
  for (const m of code.matchAll(/\x40([a-z_][a-z0-9_]*)/g)) {
    const n = `@${m[1]}`
    if (!out.includes(n)) out.push(n)
  }
  return out
}

/** The completion items for the cursor position. */
export function completionsFor(input: CompletionInput): PlainCompletion[] {
  const { textBefore } = input

  // Inside ch("...: graph paths.
  const chPath = /\b(?:ch|chf|chi|chb|chs|chv)\(\s*(["'])([^"']*)$/.exec(textBefore)
  if (chPath) {
    const typed = chPath[2]
    return input.paths.map((p, i) => ({
      label: p,
      detail: 'path',
      kind: 'Variable' as const,
      insertText: p,
      snippet: false,
      sortText: String(i).padStart(4, '0'),
      replace: typed.length,
    }))
  }

  // After sl.: the helpers.
  const sl = /\bsl\.([a-z0-9_]*)$/.exec(textBefore)
  if (sl) {
    return input.functions.map((f, i) => ({
      label: f.name,
      detail: `${f.signature} → ${f.returns}`,
      kind: 'Function' as const,
      insertText: helperSnippet(f),
      snippet: true,
      documentation: f.doc,
      sortText: String(i).padStart(4, '0'),
      replace: sl[1].length,
    }))
  }

  // After @: the stream's attributes.
  const at = /\x40([a-z0-9_]*)$/.exec(textBefore)
  if (at) {
    const read = readsInCode(input.code)
    const list = input.attrs.filter(a => !input.detailOnly || a.cls === 'detail')
    const ranked = list
      .map((a, i) => ({ a, rank: read.includes(a.name) ? read.indexOf(a.name) : 1000 + i }))
      .sort((x, y) => x.rank - y.rank)
    return ranked.map(({ a }, i) => ({
      label: a.name,
      detail: attrDetail(a),
      kind: 'Variable' as const,
      insertText: a.name,
      snippet: false,
      sortText: String(i).padStart(4, '0'),
      replace: at[1].length + 1,
    }))
  }

  // An identifier prefix (or nothing): modules and the ch family.
  const word = /([A-Za-z_]\w*)$/.exec(textBefore)?.[1] ?? ''
  const items: PlainCompletion[] = []
  MODULES.forEach((m, i) => items.push({
    label: m.label,
    detail: m.detail,
    kind: 'Module',
    insertText: m.label,
    snippet: false,
    sortText: `1${i}`,
    replace: word.length,
  }))
  CH_SNIPPETS.forEach((c, i) => items.push({
    label: c.label,
    detail: 'channel',
    kind: 'Snippet',
    insertText: c.insert,
    snippet: true,
    sortText: `2${i}`,
    replace: word.length,
  }))
  return items
}

/** The attributes a node can read: its input stream from the last /validate. */
export function attrsForNode(
  graph: Pick<Graph, 'nodes' | 'wires'> | null,
  nodeId: string,
  streams: Record<string, StreamSchema>,
): CompletionAttr[] {
  if (!graph) return []
  const out: CompletionAttr[] = []
  const seen = new Set<string>()
  for (const w of graph.wires) {
    if (w.to !== nodeId) continue
    const s = streams[w.from]
    if (!s) continue
    const add = (list: StreamSchema['points'], cls: 'point' | 'detail') => {
      for (const a of list) {
        if (seen.has(a.name)) continue
        seen.add(a.name)
        const writer = a.written_by ? graph.nodes[a.written_by]?.name ?? null : null
        out.push({ name: a.name, dtype: a.dtype, cls, from: writer })
      }
    }
    add(s.points, 'point')
    add(s.detail, 'detail')
  }
  return out
}

/**
 * The paths `ch("` offers for a node (S47): sibling nodes (`../rsi/`), their
 * params (`../rsi/period`) and output attributes (`../rsi/@rsi`), the
 * enclosing network's promoted names (`../lookback`), and the root (`/`).
 * Built from the graph and the catalog; no server call.
 */
export function chPathItems(
  graph: Pick<Graph, 'nodes'> | null,
  nodeId: string,
  streams: Record<string, StreamSchema>,
): string[] {
  if (!graph) return ['/']
  const node = graph.nodes[nodeId]
  if (!node) return ['/']
  const out: string[] = []
  const siblings = childrenByParent(graph.nodes as Graph['nodes']).get(node.parent ?? '') ?? []
  for (const id of siblings) {
    if (id === nodeId) continue
    const sib = graph.nodes[id]
    if (!sib) continue
    out.push(`../${sib.name}/`)
    const names = new Set<string>(paramSpecsOf(sib.type).map(p => p.name))
    for (const sp of sib.spare_params ?? []) names.add(sp.name)
    for (const p of sib.promoted ?? []) names.add(p.name)
    for (const n of names) out.push(`../${sib.name}/${n}`)
    for (const a of streams[id]?.points ?? []) out.push(`../${sib.name}/${a.name}`)
  }
  const parent = node.parent ? graph.nodes[node.parent] : undefined
  for (const p of parent?.promoted ?? []) out.push(`../${p.name}`)
  out.push('/')
  return out
}

/** Hover text for a word under the pointer: a helper's signature and doc, or an attribute's type. */
export function hoverFor(
  word: string,
  functions: readonly CodeFunctionInfo[],
  attrs: readonly CompletionAttr[],
): string | null {
  const fn = functions.find(f => f.name === word)
  if (fn) return `${fn.signature} → ${fn.returns}\n\n${fn.doc}`
  const a = attrs.find(x => x.name === word)
  if (a) return `${a.dtype} · ${a.cls}${a.from ? ` · written by ${a.from}` : ''}`
  return null
}
