/**
 * useAutoCook: the preview cook (spec S27, foundation amendment A4).
 *
 * Houdini cooks on change. Here, after every commit that changes what the
 * graph computes, the hook waits 500 ms for the edits to settle and then
 * asks `POST /api/nodebuilder/preview` for each node's sparkline data. It
 * never runs the backtest: that is the Run button's backtest cook.
 *
 * Rules (S27 "Behavior of the hook" and "Must not"):
 * - Only commits trigger it. Selection, pan and zoom never commit, and a
 *   commit that only moves nodes or edits boxes and notes leaves the
 *   graph's eval key (`graphEvalKey`) the same, so it does not cook either.
 * - A new commit aborts the request in flight; a late answer is dropped.
 * - With errors on the graph (S05) nothing is sent: the preview cook turns
 *   `stale` with the note `fix errors to cook`.
 * - Three failures in a row turn auto cook off and say so in a banner.
 * - When a backtest cook finishes with a `cook_id`, the hook asks
 *   `/preview` once with that id (a cache hit), even with auto cook off,
 *   so the sparklines match the run.
 * - Unmounting, or `active: false` (the tab is hidden), aborts everything.
 *
 * The hook only subscribes to the store and schedules; nothing awaits a
 * preview inside the commit path. Item 4.D mounts it once in NodeBuilder.
 */

import { useEffect, useRef } from 'react'
import { preview, type InspectWindow, type PreviewRequest, type PreviewResponse } from '../../api/nodebuilderInspect'
import type { Graph } from '../../api/nodebuilder'
import { isAbortError } from '../../api/nodebuilderValidate'
import { apiErrorDetail } from '../../shared/utils/errors'
import { pushNotice, resolveNotice } from './notices'
import { errorNodeId, graphEvalKey } from './resultsStrip'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import { getDiagnosticsView } from './useDiagnostics'

/** Wait this long after the last commit before cooking (S27). */
export const AUTO_COOK_DEBOUNCE_MS = 500
/** Points per sparkline (S26). */
export const PREVIEW_POINTS = 96
/** Failures in a row before auto cook turns itself off (S27). */
export const AUTO_COOK_MAX_FAILURES = 3
/** While /validate is still answering the last commit, check again after this long. */
export const VALIDATE_WAIT_MS = 150
/** Give up waiting for /validate after this many re-checks and cook anyway. */
const VALIDATE_WAIT_TRIES = 20

/** Banner keys (S07). */
export const AUTOCOOK_ERROR_NOTICE = 'autocook_error'
export const AUTOCOOK_OFF_NOTICE = 'autocook_off'
export const AUTOCOOK_OFF_TEXT = 'Auto cook turned off after 3 failures. Turn it on again from the toolbar.'
/** The status-bar note while errors block the cook. */
export const FIX_ERRORS_NOTE = 'fix errors to cook'
/** The status-bar note when /preview answered from the last good cook (the fetch failed). */
export const STALE_DATA_NOTE = 'showing the last cook: fresh data could not be loaded'

/** The sidebar's data window (plan D11): what to cook the graph on. */
export type CookWindow = InspectWindow

export interface UseAutoCookOptions {
  /** The sidebar's ticker, dates, interval and source; null = cannot cook yet. */
  window: CookWindow | null
  /** False while the node builder is hidden: nothing runs, a request in flight is aborted. */
  active?: boolean
}

function windowKey(w: CookWindow | null): string {
  return w ? `${w.ticker}|${w.start}|${w.end}|${w.interval}|${w.source}` : ''
}

function hasTicker(graph: Graph): boolean {
  return Object.values(graph.nodes).some(n => n.type === 'ticker')
}

/** The server's message for a failed preview (never the bare axios text when it sent one). */
function describePreviewError(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    for (const key of ['detail', 'message', 'msg'] as const) {
      const v = (detail as Record<string, unknown>)[key]
      if (typeof v === 'string' && v) return `Auto cook failed: ${v}`
    }
  }
  return `Auto cook failed: ${apiErrorDetail(e, 'the server did not answer')}`
}

function focusNodeLater(id: string): void {
  // viewOps pulls in the canvas helpers; load it only when a link is clicked.
  void import('./viewOps').then(m => m.focusNode(id))
}

/**
 * The hook's engine, outside React so the rules read top to bottom. One
 * instance per mounted hook.
 */
function loadKeyOf(state: NodeBuilderState): string {
  return `${state.graphMeta?.id ?? ''}#${graphEvalKey(state.graph)}`
}

class AutoCook {
  private timer: ReturnType<typeof setTimeout> | null = null
  private ctrl: AbortController | null = null
  private reqId = 0
  private failures = 0
  private waits = 0
  /** The eval key of the graph the sparklines describe (or are about to). */
  private lastKey: string
  /** The cook id that belongs to the current graph and window, if known. */
  private knownCook: { cookId: string; key: string } | null = null
  window: CookWindow | null = null
  /** Subscribed and shown. */
  private active = false
  /** A cook came due while hidden. */
  private pending = false

  /**
   * Graph id + eval key at the last load. The elk tidy that lands a moment
   * after "Edit this graph" (3.F) bumps layoutEpoch but only moves nodes:
   * the same load key means the sparklines still belong (NodeBuilder keeps
   * its run by the same rule).
   */
  private loadKey: string
  /** The layoutEpoch last seen (a load while hidden is caught on activate, DV-13). */
  private epoch: number
  /** A preview came due while a backtest cook was running (DV-12). */
  private waitingForBacktest = false
  /** The eval key when the running backtest started (DV-12). */
  private backtestKey: string | null = null

  constructor(state: NodeBuilderState, window: CookWindow | null) {
    this.lastKey = graphEvalKey(state.graph)
    this.loadKey = loadKeyOf(state)
    this.epoch = state.layoutEpoch
    this.window = window
  }

  private get store() {
    return useNodeBuilderStore.getState()
  }

  private fullKey(graph: Graph | null): string {
    return `${graphEvalKey(graph)}#${windowKey(this.window)}`
  }

  /**
   * Stop the timer and abort the request in flight. `stopped` is a real stop
   * (hidden, unmounted, auto cook off): the preview cook shows `cancelled`.
   * Without it a newer cook supersedes this one, which is no cancel: the
   * cook goes back to idle without taking over the status bar (DV-6).
   */
  cancel(stopped = false): void {
    if (this.timer != null) clearTimeout(this.timer)
    this.timer = null
    this.waits = 0
    if (this.ctrl) {
      this.ctrl.abort()
      this.ctrl = null
      this.reqId += 1
      const s = this.store
      if (s.cooks.preview.phase === 'cooking') {
        if (stopped) s.setCook({ kind: 'preview', phase: 'cancelled', endedAt: Date.now() })
        else s.setCook({ kind: 'preview', phase: 'idle', startedAt: null }, { keepLatest: true })
      }
    }
  }

  /** Cook after the debounce (restarts it). While hidden, cook when shown again. */
  schedule(): void {
    this.cancel()
    if (!this.active) { this.pending = true; return }
    this.timer = setTimeout(() => { this.timer = null; this.fire(null) }, AUTO_COOK_DEBOUNCE_MS)
  }

  /** The node builder is shown (or mounted): catch up on what changed meanwhile. */
  activate(): void {
    this.active = true
    const s = this.store
    // A different graph was loaded while hidden ("Edit as graph" from the
    // Strategy tab): the same reset as a load seen live (DV-13).
    if (s.layoutEpoch !== this.epoch) {
      this.epoch = s.layoutEpoch
      const loadKey = loadKeyOf(s)
      if (loadKey !== this.loadKey) {
        this.loadKey = loadKey
        this.pending = false
        this.resetForNewGraph(s)
        return
      }
    }
    const key = graphEvalKey(s.graph)
    if (key !== this.lastKey) {
      this.lastKey = key
      this.changed()
      return
    }
    const due = this.pending || s.preview.cookId == null || s.preview.stale
    this.pending = false
    if (due && s.autoCook && s.graph && !s.graph.readOnly) this.schedule()
  }

  /** The node builder is hidden or unmounted: nothing may stay in flight. */
  deactivate(): void {
    this.active = false
    this.cancel(true)
  }

  /** A different graph: nothing cooked so far belongs to it. */
  private resetForNewGraph(state: NodeBuilderState): void {
    this.cancel()
    this.failures = 0
    this.knownCook = null
    this.waitingForBacktest = false
    this.lastKey = graphEvalKey(state.graph)
    state.clearPreview()
    state.setCook({ kind: 'preview', phase: 'idle', startedAt: null, endedAt: null, stale: false, failedNodeId: null, cookId: null, staleNote: null, cookedAt: null })
    if (state.autoCook && state.graph && !state.graph.readOnly) this.schedule()
  }

  /** The graph's data changed: dim the sparklines and cook if auto cook is on. */
  private changed(): void {
    const s = this.store
    this.cancel()
    s.setPreviewStale(Object.keys(s.preview.nodes).length > 0)
    if (s.preview.cookId != null) s.setCook({ kind: 'preview', stale: true })
    if (s.autoCook) this.schedule()
  }

  /** Store change handler (zustand subscribe). */
  onStore(state: NodeBuilderState, prev: NodeBuilderState): void {
    this.epoch = state.layoutEpoch
    const loadKey = state.layoutEpoch !== prev.layoutEpoch ? loadKeyOf(state) : null
    const sameLoad = loadKey != null && loadKey === this.loadKey
    if (loadKey != null) this.loadKey = loadKey
    if (loadKey != null && !sameLoad) {
      this.resetForNewGraph(state)
      return
    }
    if (state.commitSeq !== prev.commitSeq) {
      const key = graphEvalKey(state.graph)
      if (key !== this.lastKey) {
        this.lastKey = key
        this.changed()
      }
    }
    if (state.autoCook !== prev.autoCook) {
      if (!state.autoCook) { this.cancel(true); this.waitingForBacktest = false }
      else if (state.preview.cookId == null || state.preview.stale) this.schedule()
    }
    const bt = state.cooks.backtest
    const prevBt = prev.cooks.backtest
    if (bt.phase === 'cooking' && prevBt.phase !== 'cooking') this.backtestKey = this.lastKey
    let refreshed = false
    if (bt.cookId && bt.cookId !== prevBt.cookId && bt.phase === 'cooked') {
      // The backtest's cook is in the server cache: refresh the sparklines from it.
      this.cancel()
      this.fire(bt.cookId)
      refreshed = true
    }
    if (prevBt.phase === 'cooking' && bt.phase !== 'cooking' && this.waitingForBacktest) {
      // A preview waited for this backtest (DV-12). Its refresh covers the
      // graph it ran on; an edit made during the run still needs its own cook.
      this.waitingForBacktest = false
      const editedDuringRun = this.backtestKey != null && this.backtestKey !== this.lastKey
      if ((!refreshed || editedDuringRun) && state.autoCook) this.schedule()
    }
  }

  /** The sidebar window changed: the cooked data no longer matches. */
  setWindow(w: CookWindow | null): void {
    const changed = windowKey(w) !== windowKey(this.window)
    this.window = w
    if (changed) this.changed()
  }

  /**
   * Send the preview now. `backtestCookId` is the backtest-refresh case
   * (runs even with auto cook off).
   */
  fire(backtestCookId: string | null): void {
    const s = this.store
    const graph = s.graph
    if (!graph || graph.readOnly || this.window == null) return
    if (backtestCookId == null && !s.autoCook) return
    if (backtestCookId == null && s.cooks.backtest.phase === 'cooking') {
      // The backtest is cooking this graph already; its cook refreshes the
      // sparklines when it ends, so do not cook it twice on the server (DV-12).
      this.waitingForBacktest = true
      return
    }
    const diag = getDiagnosticsView()
    if (backtestCookId == null && diag.pending && this.waits < VALIDATE_WAIT_TRIES) {
      // /validate has not answered this commit yet: wait for its error count.
      this.waits += 1
      this.timer = setTimeout(() => { this.timer = null; this.fire(null) }, VALIDATE_WAIT_MS)
      return
    }
    this.waits = 0
    if (diag.errorCount > 0) {
      s.setPreviewStale(Object.keys(s.preview.nodes).length > 0)
      s.setCook({ kind: 'preview', phase: 'idle', stale: true, staleNote: FIX_ERRORS_NOTE, endedAt: null, failedNodeId: null })
      return
    }
    if (!hasTicker(graph)) return

    const key = this.fullKey(graph)
    const cookId = backtestCookId ?? (this.knownCook?.key === key ? this.knownCook.cookId : null)
    if (this.ctrl) this.ctrl.abort()
    const ctrl = new AbortController()
    this.ctrl = ctrl
    const id = ++this.reqId
    // A refresh from the backtest's own cook keeps the status bar on the
    // backtest's `cooked` (DV-6).
    const refresh = backtestCookId != null ? { keepLatest: true } : undefined
    s.setCook({ kind: 'preview', phase: 'cooking', startedAt: Date.now(), endedAt: null, stale: false, staleNote: null, failedNodeId: null }, refresh)
    const req: PreviewRequest = { cook_id: cookId, graph, window: this.window, node_ids: null, points: PREVIEW_POINTS }
    let call: Promise<PreviewResponse>
    try {
      call = preview(req, ctrl.signal)
    } catch (e) {
      call = Promise.reject(e)
    }
    call.then(
      res => {
        if (id !== this.reqId) return
        this.ctrl = null
        this.failures = 0
        // A cook the server did not keep cannot be asked for by id again.
        this.knownCook = res.kept === false ? null : { cookId: res.cook_id, key }
        const st = this.store
        st.setPreview(res.cook_id, res.nodes ?? {})
        st.setCook({
          kind: 'preview', phase: 'cooked', endedAt: Date.now(), cookId: res.cook_id, stale: false,
          staleNote: res.stale_data === true ? STALE_DATA_NOTE : null,
        }, refresh)
        resolveNotice(AUTOCOOK_ERROR_NOTICE)
      },
      (e: unknown) => {
        if (id !== this.reqId || isAbortError(e)) return
        this.ctrl = null
        this.failures += 1
        const st = this.store
        const nodeId = errorNodeId(e)
        st.setCook({ kind: 'preview', phase: 'failed', endedAt: Date.now(), failedNodeId: nodeId })
        const node = nodeId ? st.graph?.nodes[nodeId] : undefined
        pushNotice({
          key: AUTOCOOK_ERROR_NOTICE,
          severity: 'error',
          text: describePreviewError(e),
          actions: node ? [{ label: node.name || node.id, run: () => focusNodeLater(node.id) }] : undefined,
        })
        if (this.failures >= AUTO_COOK_MAX_FAILURES && st.autoCook) {
          this.failures = 0
          st.setAutoCook(false)
          pushNotice({ key: AUTOCOOK_OFF_NOTICE, severity: 'warn', text: AUTOCOOK_OFF_TEXT })
        }
      },
    )
  }
}

/**
 * Keep node data fresh after each edit (S27). Mount once, in NodeBuilder.
 */
export function useAutoCook({ window, active = true }: UseAutoCookOptions): void {
  const engineRef = useRef<AutoCook | null>(null)
  if (engineRef.current == null) engineRef.current = new AutoCook(useNodeBuilderStore.getState(), window)
  const engine = engineRef.current

  // Subscribe while shown; abort everything when hidden or unmounted.
  useEffect(() => {
    if (!active) return
    const unsubscribe = useNodeBuilderStore.subscribe((state, prev) => engine.onStore(state, prev))
    engine.activate()
    return () => {
      unsubscribe()
      engine.deactivate()
    }
  }, [active, engine])

  // Only a different window counts (a new object with the same values does not).
  const key = windowKey(window)
  useEffect(() => {
    engine.setWindow(window)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, engine])
}
