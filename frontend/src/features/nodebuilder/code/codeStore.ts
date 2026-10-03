/**
 * Code UI state (F435 W7, specs S44 to S49). Not part of the graph, so not
 * in undo history and never saved.
 *
 * - Capabilities: `getCodeCapabilities()` once per builder mount (S49: never
 *   polled). Until it answers, code UI behaves as enabled. A failed fetch
 *   counts as enabled; the first validate or cook that answers
 *   `code_disabled` flips the mode.
 * - Parse results: one per code snippet (`<nodeId>|expr:<param>` or
 *   `<nodeId>|code`). `scheduleParse` waits (300 ms for an expression, 400 ms
 *   for a code block), cancels the request in flight, tags every request
 *   with a sequence number and drops an answer older than the last sent
 *   (S47 must-not). A good answer's problems also go to the diagnostics
 *   store, so badges and Run agree with the editor.
 * - `param_deps` from the last /validate (S48 "read by" glyph).
 * - Session-only UI: the banner's Dismiss, open node drawers, the toast.
 */

import { useEffect, useMemo } from 'react'
import { create } from 'zustand'
import {
  getCodeCapabilities,
  parseCode,
  type CodeCapabilities,
  type CodeContext,
  type ParseCodeResponse,
} from '../../../api/nodebuilderCode'
import { isAbortError, type Diagnostic, type ParamDep } from '../../../api/nodebuilderValidate'
import { useNodeBuilderStore } from '../store'
import { getStreams, onValidateAnswer, setLocalCodeDiagnostics, useServerDiagnostics } from '../useDiagnostics'
import { setCodeWritesLookup } from '../streamLabels'
import { applySpareParams } from './codeOps'
import { isExprValue, type Graph } from '../../../api/nodebuilder'

/** Wait after the last keystroke before parsing an expression (S44). */
export const EXPR_PARSE_MS = 300
/** Wait after the last keystroke before parsing a code block (S45). */
export const CODE_PARSE_MS = 400

export interface ParseEntry {
  /** The code this answer is for. */
  code: string
  res: ParseCodeResponse
  /** Date.now() when it arrived (S45 footer `Checked 0.3 s ago`). */
  at: number
}

interface CodeState {
  caps: CodeCapabilities | null
  /** False once the server said code is off (capabilities or a `code_disabled` answer). */
  enabled: boolean
  /** The latest answer per snippet key. */
  parses: Record<string, ParseEntry>
  /** The last good answer with no error per snippet key (S48: rows stay on an error). */
  lastOk: Record<string, ParseEntry>
  /** Keys with a parse scheduled or in flight. */
  pending: Record<string, true>
  paramDeps: ParamDep[]
  /** The banner's Dismiss (per session; it returns on reload). */
  bannerDismissed: boolean
  /** A code affordance was used while code is off: show the banner even with no code in the graph. */
  bannerWanted: boolean
  /** Nodes whose code drawer is open (per session; `Node.meta.code_open` is not saved). */
  openDrawers: Record<string, true>
  /** Nodes whose Code section the user opened with `Add code block` this session. */
  addedCode: Record<string, true>
  /** A request to focus a node's code editor at a line; `seq` changes on every ask. */
  focusRequest: { nodeId: string; line: number; seq: number } | null
}

const initial: CodeState = {
  caps: null,
  enabled: true,
  parses: {},
  lastOk: {},
  pending: {},
  paramDeps: [],
  bannerDismissed: false,
  bannerWanted: false,
  openDrawers: {},
  addedCode: {},
  focusRequest: null,
}

export const useCodeStore = create<CodeState>()(() => ({ ...initial }))

/** The parse key of one snippet. */
export function parseKey(nodeId: string, slot: string): string {
  return `${nodeId}|${slot}`
}

/** The slot of a param expression. */
export function exprSlot(param: string): string {
  return `expr:${param}`
}

/** The slot of a node code block (and a Wrangle body). */
export const CODE_SLOT = 'code'

/**
 * What a node's code writes, for `primaryWriteOf` (a Wrangle with exactly
 * one write hands it to a reader that names none): the last good parse of
 * this very code, else what the last validate says the node writes. Null
 * when neither knows yet.
 */
export function codeWritesOf(node: { id: string; code?: string | null }): readonly string[] | null {
  const s = useCodeStore.getState()
  const key = parseKey(node.id, CODE_SLOT)
  const parsed = [s.parses[key], s.lastOk[key]].find(e => e && e.res.ok && e.code === (node.code ?? ''))
  if (parsed) return parsed.res.writes.map(w => w.name)
  const stream = getStreams()[node.id]
  if (!stream) return null
  const mine = [...stream.points, ...stream.detail].filter(a => a.written_by === node.id).map(a => a.name)
  return [...new Set(mine)]
}

setCodeWritesLookup(codeWritesOf)

// ---------------------------------------------------------------------------
// Capabilities
// ---------------------------------------------------------------------------

let capsRequest: Promise<void> | null = null

/** Fetch the capabilities (once; `force` re-fetches, e.g. on a cook Retry). */
export function loadCodeCapabilities(force = false): Promise<void> {
  if (capsRequest && !force) return capsRequest
  capsRequest = getCodeCapabilities()
    .then(caps => {
      useCodeStore.setState({ caps, enabled: caps.enabled })
    })
    .catch(() => {
      // S49: a failed fetch is treated as enabled.
    })
  return capsRequest
}

/** Code is off on this server (S49). */
export function useCodeEnabled(): boolean {
  return useCodeStore(s => s.enabled)
}

/** Mark code as off because a validate or cook answered `code_disabled`. */
export function noteCodeDisabled(diagnostics: readonly Diagnostic[]): void {
  if (diagnostics.some(d => d.code === 'code_disabled') && useCodeStore.getState().enabled) {
    useCodeStore.setState({ enabled: false })
  }
}

/** A code affordance was used while code is off: bring the banner back (S49). */
export function requestCodeBanner(): void {
  useCodeStore.setState({ bannerWanted: true, bannerDismissed: false })
}

/**
 * Follow every good /validate answer: keep its param_deps, and notice
 * code_disabled. Called once by the builder's code controller; returns the
 * function that stops following.
 */
export function followValidateAnswers(): () => void {
  return onValidateAnswer(res => {
    const prev = useCodeStore.getState().paramDeps
    const next = res.param_deps ?? []
    if (JSON.stringify(prev) !== JSON.stringify(next)) useCodeStore.setState({ paramDeps: next })
    noteCodeDisabled(res.diagnostics)
  })
}

/** The `param_deps` edges whose target is this node's param (S48 `←` glyph). */
export function readersOf(deps: readonly ParamDep[], nodeId: string, param: string): string[] {
  return deps.filter(d => d.target_id === nodeId && d.target === param).map(d => d.reader_id)
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

interface Slot {
  timer: ReturnType<typeof setTimeout> | null
  ctrl: AbortController | null
  seq: number
}
const slots = new Map<string, Slot>()
let seqCounter = 0

/**
 * Text a section or row is typing and has not committed yet, per snippet
 * key. A parse answer may write spare params only for the node's committed
 * code or this draft (FE-6), and a key with a draft is kept by
 * `pruneCodeState` although the graph has no code for it yet (FE-9).
 */
const liveDrafts = new Map<string, string>()

/** Record (or with null, forget) the uncommitted text of one snippet. */
export function setLiveDraft(nodeId: string, slot: string, text: string | null): void {
  const key = parseKey(nodeId, slot)
  if (text === null) liveDrafts.delete(key)
  else liveDrafts.set(key, text)
}

/** The parsed code is still the node's code, committed or being typed. */
function parsedCodeIsCurrent(req: ParseRequest): boolean {
  const node = useNodeBuilderStore.getState().graph?.nodes[req.nodeId]
  if (!node) return false
  if (req.slot !== CODE_SLOT) return true
  return (node.code ?? '') === req.code || liveDrafts.get(parseKey(req.nodeId, req.slot)) === req.code
}

function slotOf(key: string): Slot {
  let s = slots.get(key)
  if (!s) {
    s = { timer: null, ctrl: null, seq: 0 }
    slots.set(key, s)
  }
  return s
}

function setPending(key: string, on: boolean) {
  const { pending } = useCodeStore.getState()
  if (!!pending[key] === on) return
  const next = { ...pending }
  if (on) next[key] = true
  else delete next[key]
  useCodeStore.setState({ pending: next })
}

export interface ParseRequest {
  nodeId: string
  slot: string
  code: string
  context: CodeContext
  expected: { type: string } | null
  /** After a good answer, write its spare params to the node (S48). */
  applySpares?: boolean
}

/** Run one parse now, for the graph in the store. Late answers are dropped by sequence number. */
export async function runParse(req: ParseRequest): Promise<ParseCodeResponse | null> {
  const key = parseKey(req.nodeId, req.slot)
  const slot = slotOf(key)
  if (slot.timer) { clearTimeout(slot.timer); slot.timer = null }
  slot.ctrl?.abort()
  const ctrl = new AbortController()
  slot.ctrl = ctrl
  const seq = ++seqCounter
  slot.seq = seq
  setPending(key, true)
  const graph = useNodeBuilderStore.getState().graph
  try {
    const res = await parseCode({
      code: req.code,
      context: req.context,
      expected: req.expected,
      graph,
      node_id: req.nodeId,
    }, ctrl.signal)
    if (slot.seq !== seq) return null
    slot.ctrl = null
    setPending(key, false)
    const entry: ParseEntry = { code: req.code, res, at: Date.now() }
    const s = useCodeStore.getState()
    const hasError = res.diagnostics.some(d => d.severity === 'error')
    useCodeStore.setState({
      parses: { ...s.parses, [key]: entry },
      lastOk: hasError ? s.lastOk : { ...s.lastOk, [key]: entry },
    })
    setLocalCodeDiagnostics(req.nodeId, req.slot, res.diagnostics)
    noteCodeDisabled(res.diagnostics)
    // An undo, redo or load since the request may have put other code back:
    // its spare params must not land on that code (FE-6).
    if (req.applySpares && !hasError && parsedCodeIsCurrent(req)) applySpares(req.nodeId, res)
    return res
  } catch (e) {
    if (slot.seq !== seq || isAbortError(e)) return null
    slot.ctrl = null
    setPending(key, false)
    return null
  }
}

/** Parse after the wait (a newer call restarts it and cancels the request in flight). */
export function scheduleParse(req: ParseRequest, delayMs: number): void {
  const key = parseKey(req.nodeId, req.slot)
  const slot = slotOf(key)
  if (slot.timer) clearTimeout(slot.timer)
  slot.ctrl?.abort()
  slot.ctrl = null
  slot.seq = ++seqCounter
  setPending(key, true)
  slot.timer = setTimeout(() => {
    slot.timer = null
    void runParse(req)
  }, delayMs)
}

/** Forget a snippet's answer and problems (its code was removed). */
export function clearParse(nodeId: string, slot: string): void {
  const key = parseKey(nodeId, slot)
  const sl = slots.get(key)
  if (sl) {
    if (sl.timer) clearTimeout(sl.timer)
    sl.ctrl?.abort()
    sl.seq = ++seqCounter
  }
  liveDrafts.delete(key)
  const s = useCodeStore.getState()
  if (key in s.parses || key in s.pending || key in s.lastOk) {
    const parses = { ...s.parses }
    const lastOk = { ...s.lastOk }
    const pending = { ...s.pending }
    delete parses[key]
    delete lastOk[key]
    delete pending[key]
    useCodeStore.setState({ parses, lastOk, pending })
  }
  setLocalCodeDiagnostics(nodeId, slot, [])
}

/**
 * Forget the answers (and their problems) of snippets that no longer exist:
 * a deleted node, a param that left code mode, a code block that was
 * cleared. Otherwise a stale parse error would keep Run disabled. Undo that
 * brings a snippet back parses it again (the rows check on mount).
 */
export function pruneCodeState(graph: Graph | null): void {
  const s = useCodeStore.getState()
  const keys = new Set([...Object.keys(s.parses), ...Object.keys(s.pending)])
  for (const key of keys) {
    const bar = key.indexOf('|')
    const nodeId = key.slice(0, bar)
    const slot = key.slice(bar + 1)
    const node = graph?.nodes[nodeId]
    let alive = false
    if (node && liveDrafts.has(key)) {
      // Still being typed: a first code block or an entering expression (FE-9).
      alive = true
    } else if (node) {
      if (slot === CODE_SLOT) alive = node.type === 'wrangle' || !!(node.code && node.code.trim())
      else if (slot.startsWith('expr:')) alive = isExprValue(node.params[slot.slice(5)])
    }
    if (!alive) clearParse(nodeId, slot)
  }
}

/** Message of the S48 toast when a spare param changes type. */
export function typeChangedText(name: string, type: string): string {
  return `${name} changed type to ${type}; its value was reset.`
}

function applySpares(nodeId: string, res: ParseCodeResponse): void {
  const st = useNodeBuilderStore.getState()
  const g = st.graph
  if (!g || g.readOnly || !g.nodes[nodeId]) return
  const { graph: next, typeChanged } = applySpareParams(g, nodeId, res.params)
  if (next === g) return
  // Part of the typing burst: the coalesce key joins it to the code commit.
  st.commit('edit code', gr => applySpareParams(gr, nodeId, res.params).graph, { coalesce: `code:${nodeId}` })
  for (const spec of typeChanged) st.showFlash(typeChangedText(spec.name, spec.type))
}

// ---------------------------------------------------------------------------
// Server code problems on the editor (UX-4)
// ---------------------------------------------------------------------------

/** Codes about a snippet's text that /validate or a cook reports (S44, S46). */
const SERVER_CODE_CODES: ReadonlySet<string> = new Set([
  'ref_broken', 'ch_cycle', 'ch_dynamic', 'attr_dynamic', 'attr_missing',
])

function isServerCodeDiagnostic(d: Diagnostic): boolean {
  if (d.code === 'code_disabled') return false
  return d.code.startsWith('code_') || SERVER_CODE_CODES.has(d.code)
}

/**
 * The server's problems (/validate, or a failed Run's cook) for one snippet:
 * `expr:<param>` takes the ones on that param, `code` the ones on the node
 * with no param. A code block's `attr_missing` counts only with a line (a
 * read in the code, not a built-in param's read).
 */
export function serverCodeDiagnostics(server: readonly Diagnostic[], nodeId: string, slot: string): Diagnostic[] {
  const param = slot.startsWith('expr:') ? slot.slice(5) : null
  return server.filter(d => {
    if (d.node_id !== nodeId || !isServerCodeDiagnostic(d)) return false
    if (param !== null) return d.param === param
    if (d.param != null) return false
    return d.code !== 'attr_missing' || d.line != null
  })
}

/** Parse problems first, then the server's that say something new (same code, line, column and message once). */
export function mergeCodeDiagnostics(parse: readonly Diagnostic[], server: readonly Diagnostic[]): Diagnostic[] {
  if (server.length === 0) return parse as Diagnostic[]
  const key = (d: Diagnostic) => `${d.code}\u0000${d.line}\u0000${d.col}\u0000${d.message}`
  const seen = new Set(parse.map(key))
  const extra = server.filter(d => !seen.has(key(d)))
  return extra.length ? [...parse, ...extra] : parse as Diagnostic[]
}

/**
 * The problems an editor, block or row shows for one snippet: the parse
 * answer's, plus the server's (attr_missing, ch_cycle, cook errors) while
 * the text on screen is the committed text the server checked (`current`).
 */
export function useCodeDiagnostics(
  nodeId: string | null | undefined,
  slot: string,
  parse: readonly Diagnostic[] | null,
  current: boolean,
): Diagnostic[] | null {
  const server = useServerDiagnostics()
  return useMemo(() => {
    const own = nodeId && current ? serverCodeDiagnostics(server, nodeId, slot) : []
    if (own.length === 0) return parse as Diagnostic[] | null
    return mergeCodeDiagnostics(parse ?? [], own)
  }, [server, nodeId, slot, parse, current])
}

/** The latest answer for a snippet, re-rendering on change. */
export function useParse(nodeId: string | null | undefined, slot: string): ParseEntry | null {
  return useCodeStore(s => (nodeId ? s.parses[parseKey(nodeId, slot)] ?? null : null))
}

/** The last error-free answer for a snippet. */
export function useLastOkParse(nodeId: string | null | undefined, slot: string): ParseEntry | null {
  return useCodeStore(s => (nodeId ? s.lastOk[parseKey(nodeId, slot)] ?? null : null))
}

/** True while a parse for this snippet is waiting or in flight. */
export function useParsePending(nodeId: string | null | undefined, slot: string): boolean {
  return useCodeStore(s => (nodeId ? !!s.pending[parseKey(nodeId, slot)] : false))
}

/** Open or close a node's code drawer (session only). */
export function setDrawerOpen(nodeId: string, open: boolean): void {
  const { openDrawers } = useCodeStore.getState()
  if (!!openDrawers[nodeId] === open) return
  const next = { ...openDrawers }
  if (open) next[nodeId] = true
  else delete next[nodeId]
  useCodeStore.setState({ openDrawers: next })
}

/** Mark that the user asked for a code block on this node (session only). */
export function markCodeAdded(nodeId: string): void {
  const { addedCode } = useCodeStore.getState()
  if (addedCode[nodeId]) return
  useCodeStore.setState({ addedCode: { ...addedCode, [nodeId]: true } })
}

/** Ask the Code section of a node to focus its editor at a 1-based line. */
export function requestCodeFocus(nodeId: string, line: number): void {
  const prev = useCodeStore.getState().focusRequest
  useCodeStore.setState({ focusRequest: { nodeId, line, seq: (prev?.seq ?? 0) + 1 } })
}

/**
 * Parse a node's code once when nothing has checked it yet (a graph just
 * opened), so its chips, spare rows and badges show without opening the
 * Inspector. Does nothing while a parse for it is pending or done.
 */
export function useEnsureParsed(nodeId: string | null | undefined, code: string | null | undefined, context: CodeContext, applySpares: boolean): void {
  useEffect(() => {
    if (!nodeId || !code || !code.trim()) return
    const st = useCodeStore.getState()
    const key = parseKey(nodeId, CODE_SLOT)
    if (st.pending[key] || st.parses[key]?.code === code) return
    void runParse({ nodeId, slot: CODE_SLOT, code, context, expected: null, applySpares })
  }, [nodeId, code, context, applySpares])
}

/** Tests only: forget everything. */
export function resetCodeStore(): void {
  for (const s of slots.values()) {
    if (s.timer) clearTimeout(s.timer)
    s.ctrl?.abort()
  }
  slots.clear()
  liveDrafts.clear()
  capsRequest = null
  useCodeStore.setState({ ...initial })
}
