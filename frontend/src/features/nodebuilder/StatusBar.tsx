/**
 * Status bar (F435 W3 item 3.H, spec S20): one quiet line at the bottom of
 * the node builder with zoom, cursor position, selection, cook state,
 * problems, a flash message, save state and the graph's id.
 *
 * Registered in the `statusBar` slot by plugins/pointerTracker.ts.
 *
 * Speed rules (S20 "must not"):
 * - The cursor text is written straight into the DOM by the pointer
 *   tracker plugin (`writeCursor` in statusChannels.ts), once per
 *   animation frame. No React
 *   state changes on pointer move, so the bar never re-renders for it.
 * - The zoom text changes at most every 100 ms during a zoom (the plugin
 *   throttles `setLiveZoom`), and not at all during a plain pan.
 * - Each segment is its own component and subscribes only to the store
 *   fields it shows.
 */

import { memo, useEffect, useState, useSyncExternalStore } from 'react'
import { getActiveCanvas } from './commands'
import { useScreenGraph } from './screen'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import { useDiagnostics } from './useDiagnostics'
import { useBuilder } from './slots'
import { focusNode } from './viewOps'
import { useRelativeTime } from './ui/relativeTime'
import { toggleAutoCook } from './AutoCookToggle'
import type { Graph } from '../../api/nodebuilder'
import {
  bindCursorEl,
  clockText,
  getLiveZoom,
  lastSaveOf,
  plural,
  selectionText,
  setLiveZoom,
  shortGraphId,
  subscribeSave,
  subscribeZoom,
} from './statusChannels'
import './statusChrome.css'

// ── Helpers ─────────────────────────────────────────────────────────────────

const selectGraph = (s: NodeBuilderState) => s.graph

/**
 * The graph on screen: the store's, else the read-only canvas's. Reactive
 * (screen.ts), so a new read-only graph re-renders the bar (FC-4).
 */
function useGraphOnScreen(): Graph | null {
  const storeGraph = useNodeBuilderStore(selectGraph)
  const screen = useScreenGraph()
  return storeGraph ?? screen.graph
}

/**
 * True until `ms` after `since` (default: from when `key` first shows up).
 * A new `key` starts the time again. One re-render when it runs out.
 */
function useShownFor(key: number | null, ms: number, since: number | null = null): boolean {
  const [expiredKey, setExpiredKey] = useState<number | null>(null)
  useEffect(() => {
    if (key == null) return
    const left = since != null ? since + ms - Date.now() : ms
    const t = setTimeout(() => setExpiredKey(key), Math.max(0, left))
    return () => clearTimeout(t)
  }, [key, ms, since])
  return key != null && expiredKey !== key
}

function prefersReducedMotion(): boolean {
  try {
    return !!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
  } catch {
    return false
  }
}

// ── Segments ────────────────────────────────────────────────────────────────

const selectViewportZoom = (s: NodeBuilderState) => s.viewport.zoom

function ZoomSegment() {
  const live = useSyncExternalStore(subscribeZoom, getLiveZoom, getLiveZoom)
  const stored = useNodeBuilderStore(selectViewportZoom)
  // A pan or zoom end writes the store; show that value too.
  useEffect(() => { setLiveZoom(stored) }, [stored])
  const pct = live ?? Math.round(stored * 100)
  const toHundred = () => {
    const canvas = getActiveCanvas()
    if (!canvas) return
    void canvas.rf.zoomTo(1, { duration: prefersReducedMotion() ? 0 : 150 })
    canvas.focus()
  }
  return (
    <button
      type="button"
      className="nb-status__seg nb-status__btn nb-status__zoom"
      data-testid="nb-status-zoom"
      title="Zoom · click for 100%"
      onClick={toHundred}
    >
      {pct}%
    </button>
  )
}

function CursorSegment() {
  return (
    <span
      ref={bindCursorEl}
      className="nb-status__seg nb-status__cursor nb-status__wide"
      data-testid="nb-status-cursor"
      aria-hidden="true"
    />
  )
}

const selectNodeIds = (s: NodeBuilderState) => s.selectedNodeIds
const selectWireIds = (s: NodeBuilderState) => s.selectedWireIds
const selectAnnotationIds = (s: NodeBuilderState) => s.selectedAnnotationIds

function SelectionSegment() {
  const nodeIds = useNodeBuilderStore(selectNodeIds)
  const wireIds = useNodeBuilderStore(selectWireIds)
  const annotationIds = useNodeBuilderStore(selectAnnotationIds)
  // Names can change (rename), so the graph is read too; it only changes on a commit.
  const graph = useGraphOnScreen()
  const text = selectionText(graph, nodeIds, wireIds, annotationIds)
  const empty = text === 'no selection'
  return (
    <span
      className={`nb-status__seg nb-status__selection${empty ? ' nb-status--dim' : ''}`}
      data-testid="nb-status-selection"
      title={text}
    >
      {text}
    </span>
  )
}

const selectCook = (s: NodeBuilderState) => s.cook

function CookSegment() {
  const cook = useNodeBuilderStore(selectCook)
  const [now, setNow] = useState(() => Date.now())
  const cooking = cook.phase === 'cooking'
  // Elapsed time while cooking, every 100 ms.
  useEffect(() => {
    if (!cooking) return
    const t = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(t)
  }, [cooking])
  const ended = cook.phase === 'cooked' || cook.phase === 'cancelled' ? cook.endedAt : null
  const fresh = useShownFor(ended, 3000, ended)

  let text: string
  // Live detail that must not be announced: the elapsed ms ticks every
  // 100 ms, so it sits outside the live text (UX-11).
  let quiet = ''
  let tone = ''
  let clickable = false
  if (cooking) {
    const ms = cook.startedAt != null ? Math.max(0, now - cook.startedAt) : 0
    text = 'cooking…'
    quiet = ` ${ms} ms`
  } else if (cook.phase === 'failed') {
    text = 'cook failed'
    tone = 'nb-status--error'
    clickable = cook.failedNodeId != null
  } else if (cook.stale) {
    // S27: "stale · fix errors to cook" when errors block the auto cook.
    text = cook.staleNote ? `stale · ${cook.staleNote}` : 'stale'
    tone = 'nb-status--warn'
  } else if (cook.phase === 'cooked') {
    text = cook.endedAt != null ? `cooked ${clockText(cook.endedAt)}` : 'cooked'
    // The server answered from the last good cook (its fetch failed).
    if (cook.staleNote) text = `${text} · ${cook.staleNote}`
    tone = cook.staleNote ? 'nb-status--warn' : fresh ? 'nb-status--ok' : ''
  } else if (cook.phase === 'cancelled' && fresh) {
    text = 'cancelled'
  } else {
    text = 'idle'
    tone = 'nb-status--dim'
  }
  const preview = cook.kind === 'preview' && text !== 'idle'
  const body = (
    <>
      {cooking && <span className="nb-spinner nb-status__spinner" aria-hidden="true" />}
      {text}
      {quiet && <span aria-hidden="true" data-testid="nb-status-cook-ms">{quiet}</span>}
      {preview && <span className="nb-status--dim"> · preview</span>}
    </>
  )
  // S27: the segment announces `cooked` and `failed` only. The visible text
  // (cooking…, stale, idle) is not a live region; this hidden one changes
  // only when a cook ends as cooked or failed, so auto cook does not speak on
  // every edit (UX-11).
  const announce = cooking
    ? ''
    : cook.phase === 'failed'
      ? 'cook failed'
      : cook.phase === 'cooked' && !cook.stale
        ? text
        : ''
  const live = (
    <span role="status" aria-live="polite" className="nb-sr-only" data-testid="nb-status-cook-live">{announce}</span>
  )
  if (clickable) {
    // S20: clickable segments are buttons.
    return (
      <>
        <button
          type="button"
          className={`nb-status__seg nb-status__btn ${tone}`}
          data-testid="nb-status-cook"
          title="Show the node that failed"
          onClick={() => { if (cook.failedNodeId) focusNode(cook.failedNodeId) }}
        >
          {body}
        </button>
        {live}
      </>
    )
  }
  return (
    <>
      <span className={`nb-status__seg ${tone}`} data-testid="nb-status-cook">
        {body}
      </span>
      {live}
    </>
  )
}

const selectEditable = (s: NodeBuilderState) => s.graph != null

function DiagnosticsSegment() {
  const editable = useNodeBuilderStore(selectEditable)
  const { errorCount, warningCount } = useDiagnostics()
  const builder = useBuilder()
  if (!editable) {
    return <span className="nb-status__seg nb-status--dim" data-testid="nb-status-diag">—</span>
  }
  const none = errorCount === 0 && warningCount === 0
  return (
    <button
      type="button"
      className={`nb-status__seg nb-status__btn${none ? ' nb-status--dim' : ''}`}
      data-testid="nb-status-diag"
      title="Show the problem list"
      onClick={e => builder?.openDiagnostics(e.currentTarget)}
    >
      {none ? 'no problems' : (
        <>
          {errorCount > 0 && <span className="nb-status--error">{plural(errorCount, 'error')}</span>}
          {errorCount > 0 && warningCount > 0 && ' · '}
          {warningCount > 0 && <span className="nb-status--warn">{plural(warningCount, 'warning')}</span>}
        </>
      )}
    </button>
  )
}

const selectFlash = (s: NodeBuilderState) => s.flash

function FlashSegment() {
  const flash = useNodeBuilderStore(selectFlash)
  // A repeat of the same text has a new seq, which starts the 2 s again.
  const visible = useShownFor(flash?.seq ?? null, 2000)
  return (
    <span role="status" aria-live="polite" className="nb-status__seg nb-status__flash" data-testid="nb-status-flash">
      {visible && flash ? flash.text : ''}
    </span>
  )
}

const selectDirty = (s: NodeBuilderState) => s.dirty
const selectMetaId = (s: NodeBuilderState) => s.graphMeta?.id ?? null
const selectMetaRev = (s: NodeBuilderState) => s.graphMeta?.rev ?? 0

function SavedSegment() {
  const editable = useNodeBuilderStore(selectEditable)
  const dirty = useNodeBuilderStore(selectDirty)
  const id = useNodeBuilderStore(selectMetaId)
  const builder = useBuilder()
  // The read-only view has no store graph; screen.ts says when a canvas
  // draws one (reactive, FC-4).
  const onScreen = useScreenGraph().graph != null

  const savedAt = useSyncExternalStore(subscribeSave, () => lastSaveOf(id), () => lastSaveOf(id))
  const rel = useRelativeTime(savedAt)

  if (!editable) {
    return (
      <span className="nb-status__seg nb-status--dim" data-testid="nb-status-saved">{onScreen ? 'view' : '—'}</span>
    )
  }
  if (dirty) {
    return (
      <button
        type="button"
        className="nb-status__seg nb-status__btn nb-status--warn"
        data-testid="nb-status-saved"
        title="Save"
        onClick={() => builder?.session.save()}
      >
        unsaved
      </button>
    )
  }
  return (
    <span className={`nb-status__seg${id ? '' : ' nb-status--dim'}`} data-testid="nb-status-saved">
      {id ? (savedAt != null ? `saved ${rel}` : 'saved') : '—'}
    </span>
  )
}

const selectAutoCook = (s: NodeBuilderState) => s.autoCook
const selectCanAutoCook = (s: NodeBuilderState) => s.graph != null && !s.graph.readOnly

/** S20 segment 7: `auto cook on` / `auto cook off` (dim when off); click = the A command. */
function AutoCookSegment() {
  const editable = useNodeBuilderStore(selectCanAutoCook)
  const on = useNodeBuilderStore(selectAutoCook)
  if (!editable) {
    return <span className="nb-status__seg nb-status--dim" data-testid="nb-status-autocook">—</span>
  }
  return (
    <button
      type="button"
      className={`nb-status__seg nb-status__btn${on ? '' : ' nb-status--dim'}`}
      data-testid="nb-status-autocook"
      title="Toggle auto cook (A)"
      aria-pressed={on}
      onClick={toggleAutoCook}
    >
      {on ? 'auto cook on' : 'auto cook off'}
    </button>
  )
}

const selectNodeCount = (s: NodeBuilderState) => (s.graph ? Object.keys(s.graph.nodes).length : -1)

function GraphSegment() {
  const count = useNodeBuilderStore(selectNodeCount)
  const id = useNodeBuilderStore(selectMetaId)
  const rev = useNodeBuilderStore(selectMetaRev)
  let text = '—'
  let dim = true
  if (count === 0) text = 'empty graph'
  else if (count > 0 && !id) text = 'untitled'
  else if (count > 0 && id) {
    text = `graph ${shortGraphId(id)} @ rev ${rev}`
    dim = false
  }
  return (
    <span
      className={`nb-status__seg nb-status__wide${dim ? ' nb-status--dim' : ''}`}
      data-testid="nb-status-graph"
      title={id ?? undefined}
    >
      {text}
    </span>
  )
}

// ── The bar ─────────────────────────────────────────────────────────────────

function StatusBar() {
  return (
    <div
      className="nb-status"
      role="contentinfo"
      aria-label="Editor status"
      data-testid="nb-status"
      // Spec 0.8: a press on the bar keeps keys with the canvas. The
      // mousedown default would move focus to the pressed button instead.
      onPointerDown={() => { getActiveCanvas()?.focus() }}
      onMouseDown={e => e.preventDefault()}
    >
      <ZoomSegment />
      <CursorSegment />
      <SelectionSegment />
      <CookSegment />
      <DiagnosticsSegment />
      <FlashSegment />
      <div className="nb-status__right">
        <AutoCookSegment />
        <SavedSegment />
        <GraphSegment />
      </div>
    </div>
  )
}

export default memo(StatusBar)
