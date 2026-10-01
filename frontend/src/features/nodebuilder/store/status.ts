/**
 * Status slice of the node builder store (W3 pre-step 3.0, W4 item 4.C):
 * what the status bar (S20) and the node sparklines (S26) show that no other
 * slice holds.
 *
 * - `flash`: a short message a command sets ("Select a node first"). The
 *   status bar shows it for 2 s; `seq` tells a repeat of the same text apart.
 * - `cooks`: one state per kind of cook (foundation amendment A4). The
 *   `backtest` cook is Run backtest / Cmd+Enter; the `preview` cook is auto
 *   cook (`useAutoCook`), which only refreshes node data.
 * - `cook`: the latest of the two, for the status bar ("the status bar shows
 *   the most recent cook of either kind"). It is worked out from `cooks` on
 *   every write, so readers can keep selecting `s.cook`.
 * - `preview`: the last `/preview` answer per node id (S26), plus `stale`
 *   when the graph changed since. Nodes select their own entry, and a new
 *   answer keeps the old object for a node whose data did not change, so a
 *   node only re-renders when its own preview changes.
 * - `autoCook`: the toolbar switch (S27), kept in `nb.autocook` (A5).
 *
 * Nothing here is part of undo.
 */

import type { StateCreator } from 'zustand'
import type { PreviewNode } from '../../../api/nodebuilderInspect'
import type { NodeBuilderState } from '../store'

/** The cook's lifecycle, in the status bar's words (S20). */
export type CookPhase = 'idle' | 'cooking' | 'cooked' | 'cancelled' | 'failed'

/** The two kinds of cook (A4). */
export type CookKind = 'backtest' | 'preview'

export interface CookStatus {
  phase: CookPhase
  /** `backtest` (Run backtest, Cmd+Enter) or `preview` (auto cook). */
  kind: CookKind
  /** Date.now() when the cook started, or null. */
  startedAt: number | null
  /** Date.now() when the cook ended (cooked, cancelled or failed), or null. */
  endedAt: number | null
  /** True when the shown result answers an older graph or request. */
  stale: boolean
  /** The node a failed cook names, if the server said which one. */
  failedNodeId: string | null
  /** The server's cook cache id (`ck_...`) of the last finished cook, or null. */
  cookId: string | null
  /** Why the cook is stale, when there is a reason to show (`fix errors to cook`). */
  staleNote: string | null
  /**
   * Date.now() when `cookId` was last produced (any write carrying a cookId).
   * A failed or cancelled cook stamps `endedAt` but not this, so "which cook
   * is newer" compares this (DV-5).
   */
  cookedAt: number | null
}

export const IDLE_COOK: CookStatus = {
  phase: 'idle',
  kind: 'backtest',
  startedAt: null,
  endedAt: null,
  stale: false,
  failedNodeId: null,
  cookId: null,
  staleNote: null,
  cookedAt: null,
}

export const IDLE_PREVIEW_COOK: CookStatus = { ...IDLE_COOK, kind: 'preview' }

export interface CookStatuses {
  backtest: CookStatus
  preview: CookStatus
}

const IDLE_COOKS: CookStatuses = { backtest: IDLE_COOK, preview: IDLE_PREVIEW_COOK }

/** The sparkline data from the last `/preview` (S26). */
export interface PreviewState {
  /** The cook the data came from, or null before the first preview. */
  cookId: string | null
  /** Node id -> its sparkline. A node missing here has no sparkline. */
  nodes: Record<string, PreviewNode>
  /** True when the graph changed since this data was cooked (drawn dim). */
  stale: boolean
}

export const EMPTY_PREVIEW: PreviewState = { cookId: null, nodes: {}, stale: false }

/** Storage key of the auto cook switch (A5). */
export const AUTOCOOK_KEY = 'nb.autocook'

/** Auto cook starts on unless this browser turned it off (S27). */
export function readAutoCook(): boolean {
  try {
    return globalThis.localStorage?.getItem(AUTOCOOK_KEY) !== 'off'
  } catch {
    return true
  }
}

function writeAutoCook(on: boolean): void {
  try {
    globalThis.localStorage?.setItem(AUTOCOOK_KEY, on ? 'on' : 'off')
  } catch {
    // No storage (private window, blocked): the switch still works this session.
  }
}

/**
 * The cook the status bar shows: a running backtest always (it is the one
 * the user asked for and can stop with Esc), else the kind that changed
 * phase last.
 */
export function latestCook(cooks: CookStatuses, lastKind: CookKind): CookStatus {
  if (cooks.backtest.phase === 'cooking') return cooks.backtest
  return cooks[lastKind]
}

function sameValues(a: readonly (number | null)[], b: readonly (number | null)[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

/** True when two sparklines would draw the same thing. */
export function samePreviewNode(a: PreviewNode, b: PreviewNode): boolean {
  return (
    a.attr === b.attr &&
    a.kind === b.kind &&
    a.min === b.min &&
    a.max === b.max &&
    a.nan_count === b.nan_count &&
    a.true_pct === b.true_pct &&
    sameValues(a.values, b.values)
  )
}

/**
 * The new node map, keeping the old object for every node whose data did
 * not change (and the old map itself when nothing changed), so a per-node
 * selector returns the same value and that node does not re-render.
 */
export function mergePreviewNodes(
  prev: Record<string, PreviewNode>,
  next: Record<string, PreviewNode>,
): Record<string, PreviewNode> {
  const out: Record<string, PreviewNode> = {}
  let changed = Object.keys(prev).length !== Object.keys(next).length
  for (const [id, node] of Object.entries(next)) {
    const old = prev[id]
    if (old && samePreviewNode(old, node)) {
      out[id] = old
    } else {
      out[id] = node
      changed = true
    }
  }
  return changed ? out : prev
}

export interface SetCookOptions {
  /**
   * Do not make this kind the latest cook: a refresh that answers from the
   * backtest's own cook leaves the status bar on the backtest (DV-6).
   */
  keepLatest?: boolean
}

export interface StatusSlice {
  /** The current flash message, or null. */
  flash: { text: string; seq: number } | null
  /** Show a short message in the status bar. */
  showFlash(text: string): void

  /** Each kind of cook's own state (A4). */
  cooks: CookStatuses
  /** The kind whose phase changed last (internal; read `cook`). */
  lastCookKind: CookKind
  /** The latest cook of either kind (what the status bar shows). Derived from `cooks`. */
  cook: CookStatus
  /**
   * Merge fields into one cook: `patch.kind` picks which (default
   * `backtest`, so the Wave 3 callers keep working). Writes nothing when
   * no field changes.
   */
  setCook(patch: Partial<CookStatus>, opts?: SetCookOptions): void
  /** Both cooks back to idle (a different graph was loaded). */
  resetCooks(): void

  /** Sparkline data from `/preview` (S26). */
  preview: PreviewState
  /** Store a `/preview` answer. Unchanged nodes keep their old objects. */
  setPreview(cookId: string, nodes: Record<string, PreviewNode>): void
  /** Mark the sparklines stale (the graph changed) or fresh again. */
  setPreviewStale(stale: boolean): void
  /** Drop all sparkline data (a different graph was loaded). */
  clearPreview(): void

  /** The auto cook switch (S27). Persisted in `nb.autocook`. */
  autoCook: boolean
  setAutoCook(on: boolean): void
}

export const createStatusSlice: StateCreator<NodeBuilderState, [], [], StatusSlice> = (set, get) => ({
  flash: null,
  showFlash(text) {
    const prev = get().flash
    set({ flash: { text, seq: (prev?.seq ?? 0) + 1 } })
  },

  cooks: IDLE_COOKS,
  lastCookKind: 'backtest',
  cook: IDLE_COOK,
  setCook(rawPatch, opts) {
    const s = get()
    const kind: CookKind = rawPatch.kind ?? 'backtest'
    const current = s.cooks[kind]
    // A write that carries a cook id produced that cook now (DV-5).
    const patch: Partial<CookStatus> = rawPatch.cookId != null && rawPatch.cookedAt === undefined
      ? { ...rawPatch, cookedAt: Date.now() }
      : rawPatch
    // "Nothing changed" is judged on the caller's fields; the stamp alone is no change.
    const keys = Object.keys(rawPatch) as Array<keyof CookStatus>
    const same = keys.every(k => current[k] === rawPatch[k])
    const next = same ? current : { ...current, ...patch, kind }
    const cooks = same ? s.cooks : { ...s.cooks, [kind]: next }
    // A new phase (or a new start) makes this kind the latest one.
    const moved = !same && !opts?.keepLatest && ((patch.phase !== undefined && patch.phase !== current.phase) || (patch.startedAt !== undefined && patch.startedAt !== current.startedAt))
    const lastCookKind = moved ? kind : s.lastCookKind
    const cook = latestCook(cooks, lastCookKind)
    // Also write when `cook` was set directly (tests) and is out of step.
    if (same && cook === s.cook && lastCookKind === s.lastCookKind) return
    set({ cooks, lastCookKind, cook })
  },
  resetCooks() {
    const s = get()
    if (s.cooks === IDLE_COOKS && s.cook === IDLE_COOK) return
    set({ cooks: IDLE_COOKS, lastCookKind: 'backtest', cook: IDLE_COOK })
  },

  preview: EMPTY_PREVIEW,
  setPreview(cookId, nodes) {
    const prev = get().preview
    const merged = mergePreviewNodes(prev.nodes, nodes)
    if (merged === prev.nodes && prev.cookId === cookId && !prev.stale) return
    set({ preview: { cookId, nodes: merged, stale: false } })
  },
  setPreviewStale(stale) {
    const prev = get().preview
    if (prev.stale === stale) return
    set({ preview: { ...prev, stale } })
  },
  clearPreview() {
    if (get().preview === EMPTY_PREVIEW) return
    set({ preview: EMPTY_PREVIEW })
  },

  autoCook: readAutoCook(),
  setAutoCook(on) {
    writeAutoCook(on)
    if (get().autoCook === on) return
    set({ autoCook: on })
  },
})
