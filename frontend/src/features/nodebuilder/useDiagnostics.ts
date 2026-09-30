/**
 * Diagnostics for the node builder (spec S05, plan 4.2, F435 W1 item 1.G).
 *
 * One shared zustand store holds the latest problems for the graph. Two
 * sources feed it:
 * - the server: `useDiagnosticsController()` (mounted once, in NodeBuilder)
 *   calls POST /api/nodebuilder/validate 300 ms after each store commit, and
 *   `setServerDiagnostics()` puts a failed Run's list in place until the next
 *   commit;
 * - the page: a param field whose text is not a number reports itself with
 *   `setLocalParamInvalid()`, so the badge, the counts and Run agree with the
 *   red field before anything reaches the server.
 *
 * Rules (S05 "Must not"):
 * - Only a commit starts a validate. Pointer moves, selection and viewport
 *   changes never do, because they never bump `commitSeq`.
 * - A new commit cancels the request in flight; its late answer is dropped.
 * - The last result stays on screen while the next one is pending.
 * - A failed request keeps the last result and sets `offline`.
 */

import { useEffect } from 'react'
import { create } from 'zustand'
import { useNodeBuilderStore } from './store'
import {
  validateGraph,
  isAbortError,
  diagnosticsFromError,
  describeValidateError,
  type Diagnostic,
} from '../../api/nodebuilderValidate'

export type { Diagnostic, Severity, DiagnosticCode } from '../../api/nodebuilderValidate'

/** Wait this long after the last commit before calling /validate. */
export const VALIDATE_DEBOUNCE_MS = 300

/** Message on a number field whose text does not parse. */
export const LOCAL_NUMBER_MESSAGE = 'Enter a number'

export interface DiagnosticsState {
  /** Server problems first, then local field problems. */
  diagnostics: Diagnostic[]
  /** The same list by node id (graph-level problems are left out). */
  byNode: Record<string, Diagnostic[]>
  /** Severity 'error' count. Run is disabled while it is above 0. */
  errorCount: number
  /** Severity 'warning' count. Info is shown in the popover but not counted. */
  warningCount: number
  /** A validate is scheduled or in flight. */
  pending: boolean
  /** The last validate failed; the counts are from the result before it. */
  offline: boolean
  /** Why the last validate failed (for the offline notice), else null. */
  offlineDetail: string | null
  /** False until the first result arrives (the chip shows `…` meanwhile). */
  hasResult: boolean
}

interface DiagStore {
  view: DiagnosticsState
  server: Diagnostic[]
  /** Local field problems, keyed by `localKey(nodeId, param)`. */
  local: Record<string, Diagnostic>
  /** The store commitSeq the current server list belongs to (-1 = none). */
  validatedSeq: number
}

const EMPTY_VIEW: DiagnosticsState = {
  diagnostics: [],
  byNode: {},
  errorCount: 0,
  warningCount: 0,
  pending: false,
  offline: false,
  offlineDetail: null,
  hasResult: false,
}

const useDiagStore = create<DiagStore>()(() => ({
  view: EMPTY_VIEW,
  server: [],
  local: {},
  validatedSeq: -1,
}))

function localKey(nodeId: string, param: string): string {
  return `${nodeId}\u0000${param}`
}

const DIAG_FIELDS = [
  'node_id', 'path', 'severity', 'code', 'message', 'param', 'port', 'line', 'col', 'end_line', 'end_col',
] as const

function sameDiagnostics(a: readonly Diagnostic[], b: readonly Diagnostic[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    for (const f of DIAG_FIELDS) if (a[i][f] !== b[i][f]) return false
  }
  return true
}

/**
 * Rebuild the combined list, the per-node map and the counts. A node whose
 * problems read the same as before keeps its old list, so its card does not
 * re-render on every /validate answer.
 */
function derive(server: Diagnostic[], local: Record<string, Diagnostic>, prevByNode: Record<string, Diagnostic[]>) {
  const diagnostics = [...server, ...Object.values(local)]
  const byNode: Record<string, Diagnostic[]> = {}
  let errorCount = 0
  let warningCount = 0
  for (const d of diagnostics) {
    if (d.severity === 'error') errorCount++
    else if (d.severity === 'warning') warningCount++
    if (d.node_id) (byNode[d.node_id] ??= []).push(d)
  }
  for (const id of Object.keys(byNode)) {
    const prev = prevByNode[id]
    if (prev && sameDiagnostics(prev, byNode[id])) byNode[id] = prev
  }
  return { diagnostics, byNode, errorCount, warningCount }
}

type ViewFlags = Pick<DiagnosticsState, 'pending' | 'offline' | 'offlineDetail' | 'hasResult'>

/** Update the store; the derived lists are rebuilt only when a list changed. */
function update(patch: Partial<Omit<DiagStore, 'view'>> & { flags?: Partial<ViewFlags> }) {
  useDiagStore.setState(s => {
    const flags = patch.flags ?? {}
    const flagsChange = (Object.keys(flags) as (keyof ViewFlags)[]).some(k => s.view[k] !== flags[k])
    // Nothing would change: keep the same state, so no subscriber re-renders.
    if (!patch.server && !patch.local && !flagsChange && (patch.validatedSeq ?? s.validatedSeq) === s.validatedSeq) {
      return s
    }
    const server = patch.server ?? s.server
    const local = patch.local ?? s.local
    const lists = patch.server || patch.local ? derive(server, local, s.view.byNode) : null
    const view: DiagnosticsState = { ...s.view, ...(lists ?? {}), ...flags }
    return {
      server,
      local,
      validatedSeq: patch.validatedSeq ?? s.validatedSeq,
      view,
    }
  })
}

// ---------------------------------------------------------------------------
// Request state. One controller runs at a time, so module scope is enough.
// ---------------------------------------------------------------------------

let timer: ReturnType<typeof setTimeout> | null = null
let inFlight: AbortController | null = null
/** Bumped by every request and every override; a late answer checks it. */
let requestId = 0

function cancelTimer() {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
}

function cancelInFlight() {
  requestId++
  if (inFlight) {
    inFlight.abort()
    inFlight = null
  }
}

/** Call /validate for the graph in the store right now. */
async function runValidate(): Promise<void> {
  cancelTimer()
  cancelInFlight()
  const { graph, commitSeq } = useNodeBuilderStore.getState()
  // Nothing to check: no graph, or the read-only auto-render view (the rule
  // backtester runs that one, and its gaps are shown by the notices).
  if (!graph || graph.readOnly) {
    update({ server: [], validatedSeq: commitSeq, flags: { pending: false, offline: false, offlineDetail: null } })
    return
  }
  const id = requestId
  const ctrl = new AbortController()
  inFlight = ctrl
  update({ flags: { pending: true } })
  try {
    const res = await validateGraph(graph, ctrl.signal)
    if (id !== requestId) return
    inFlight = null
    update({
      server: res.diagnostics,
      validatedSeq: commitSeq,
      flags: { pending: false, offline: false, offlineDetail: null, hasResult: true },
    })
  } catch (e) {
    if (id !== requestId || isAbortError(e)) return
    inFlight = null
    // A graph the server cannot even parse comes back as a 400 that still
    // carries diagnostics (plan 4.4): that is a result, not an outage.
    const fromError = diagnosticsFromError(e)
    if (fromError) {
      update({
        server: fromError,
        validatedSeq: commitSeq,
        flags: { pending: false, offline: false, offlineDetail: null, hasResult: true },
      })
      return
    }
    update({ flags: { pending: false, offline: true, offlineDetail: describeValidateError(e) } })
  }
}

/**
 * Start (or restart) the 300 ms wait before the next validate. A request
 * still in flight is for an older graph, so it is cancelled now.
 */
function scheduleValidate() {
  cancelTimer()
  cancelInFlight()
  timer = setTimeout(() => {
    timer = null
    void runValidate()
  }, VALIDATE_DEBOUNCE_MS)
  if (!useDiagStore.getState().view.pending) update({ flags: { pending: true } })
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * The current diagnostics. Any component may call it: they all read the one
 * shared store, so many callers never mean many requests.
 */
export function useDiagnostics(): DiagnosticsState {
  return useDiagStore(s => s.view)
}

/** The current diagnostics, read outside React (a key command runs before a re-render). */
export function getDiagnosticsView(): DiagnosticsState {
  return useDiagStore.getState().view
}

const NO_DIAGNOSTICS: Diagnostic[] = []

/** Problems on one node (a stable empty list when there are none). */
export function useNodeDiagnostics(nodeId: string | null | undefined): Diagnostic[] {
  return useDiagStore(s => (nodeId ? s.view.byNode[nodeId] : undefined) ?? NO_DIAGNOSTICS)
}

/**
 * The server problem for one param row, or null. Local problems are left
 * out: the row already knows its own text does not parse.
 */
export function useParamDiagnostic(nodeId: string, param: string): Diagnostic | null {
  return useDiagStore(s => s.server.find(d => d.node_id === nodeId && d.param === param) ?? null)
}

/**
 * Mount ONCE (NodeBuilder). Calls /validate 300 ms after each store commit
 * (it watches `commitSeq`), and once on mount when the graph on screen has
 * not been checked yet.
 */
export function useDiagnosticsController(): void {
  useEffect(() => {
    const unsubscribe = useNodeBuilderStore.subscribe((state, prev) => {
      if (state.commitSeq !== prev.commitSeq) scheduleValidate()
    })
    const { graph, commitSeq } = useNodeBuilderStore.getState()
    if (graph && useDiagStore.getState().validatedSeq !== commitSeq) scheduleValidate()
    return () => {
      unsubscribe()
      cancelTimer()
      cancelInFlight()
      if (useDiagStore.getState().view.pending) update({ flags: { pending: false } })
    }
  }, [])
}

/**
 * Show the diagnostics a failed Run returned (400, plan 4.4). They replace
 * the current set until the next commit; a validate already scheduled or in
 * flight for the same graph is cancelled so it cannot overwrite them.
 */
export function setServerDiagnostics(d: Diagnostic[]): void {
  cancelTimer()
  cancelInFlight()
  update({
    server: d,
    validatedSeq: useNodeBuilderStore.getState().commitSeq,
    flags: { pending: false, offline: false, offlineDetail: null, hasResult: true },
  })
}

/** Validate now (the Retry link on the offline notice). */
export function retryValidation(): Promise<void> {
  return runValidate()
}

/**
 * Report (message) or clear (null) a param field whose text does not parse.
 * ParamRow calls it; it never starts a request.
 */
export function setLocalParamInvalid(nodeId: string, param: string, message: string | null): void {
  const key = localKey(nodeId, param)
  const { local } = useDiagStore.getState()
  if (message === null) {
    if (!(key in local)) return
    const next = { ...local }
    delete next[key]
    update({ local: next })
    return
  }
  if (local[key]?.message === message) return
  const path = useNodeBuilderStore.getState().graph?.nodes[nodeId]?.name
  update({
    local: {
      ...local,
      [key]: {
        node_id: nodeId,
        path: path ? `/${path}` : null,
        severity: 'error',
        code: 'param_invalid',
        message,
        param,
        port: null,
        line: null,
        col: null,
        end_line: null,
        end_col: null,
      },
    },
  })
}

/** Tests only: forget everything and cancel any request. */
export function resetDiagnostics(): void {
  cancelTimer()
  cancelInFlight()
  useDiagStore.setState({ view: EMPTY_VIEW, server: [], local: {}, validatedSeq: -1 })
}
