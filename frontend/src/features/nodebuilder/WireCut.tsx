/**
 * Y + drag cuts wires (F457, Houdini).
 *
 * Hold Y with the pointer over the canvas and drag with the left button: a
 * dashed amber line runs from the press point to the pointer, and the wires
 * it crosses are marked. On release every drawn wire the line crosses is
 * deleted, in one commit (one undo step), the same way Delete removes a
 * selected wire (`removeWiresWithTerms`, so an AND / OR / XOR term the wire
 * added goes too). The status bar says how many went (or that none were
 * crossed); a miss makes no undo step. A stroke shorter than `MIN_CUT_PX`
 * is a click and does nothing. Releasing Y before the mouse, Esc, the
 * window losing focus, or focus moving into a text field cancels without a
 * change. Y pressed before the pointer reaches the canvas arms the cut once
 * the pointer is there.
 *
 * While Y is held a press on the canvas belongs to the cut: it is stopped
 * before React Flow sees it, so no node drag, box select or pan starts, and
 * its click (or a dblclick from two quick strokes) never reaches the canvas.
 * Nothing happens in a read-only graph, while a popover is open, or when
 * focus is in a text field or the code editor.
 *
 * The wires are read from the canvas DOM: each React Flow edge carries the
 * wire id and the path AttrEdge drew, in flow units (wireCutGeometry.ts
 * tests the crossing). Mounted in the `overlays` slot by plugins/wireCut.ts.
 */

import { useEffect, useState } from 'react'
import type { CanvasCtx, XY } from './canvasPlugins'
import { isTypingTarget } from './canvasHelpers'
import { getActiveCanvas } from './screen'
import { removeWiresWithTerms } from './streamLabels'
import { isPopoverOpen } from './ui/Popover'
import { isCutKey, wiresCutBy, type DrawnWire } from './wireCutGeometry'
import './edges/wireOps.css'

/** Class on the canvas root while Y is held (the cut cursor). */
export const CUTTING_CLASS = 'nb-cutting'

/** The wires drawn in this canvas, with their paths. */
function drawnWires(root: HTMLElement): DrawnWire[] {
  const out: DrawnWire[] = []
  for (const el of root.querySelectorAll('.react-flow__edge')) {
    const id = el.getAttribute('data-id')
    if (id) out.push({ id, d: el.querySelector('.nb-edge__path')?.getAttribute('d') })
  }
  return out
}

/** A stroke shorter than this (screen pixels) is a click: it cuts nothing. */
export const MIN_CUT_PX = 6

/** A dblclick this soon after a cut press belongs to the cut (no Tab menu). */
const DBLCLICK_AFTER_CUT_MS = 500

/**
 * The drawn wires the screen segment a→b crosses. The ends are not snapped
 * to the grid: the line tested is the line drawn.
 */
export function wiresUnderCut(ctx: CanvasCtx, a: XY, b: XY): string[] {
  const root = ctx.container()
  const graph = ctx.store.getState().graph
  if (!root || !graph) return []
  const fa = ctx.rf.screenToFlowPosition(a, { snapToGrid: false })
  const fb = ctx.rf.screenToFlowPosition(b, { snapToGrid: false })
  const known = new Set(graph.wires.map(w => w.id))
  return wiresCutBy(drawnWires(root), fa, fb).filter(id => known.has(id))
}

/** The status-bar text after a cut (a polite live region). */
export function cutText(count: number): string {
  return count === 0 ? 'No wires crossed' : `Cut ${count} wire${count === 1 ? '' : 's'}`
}

/**
 * Delete every wire the screen segment a→b crosses, as one commit, and say
 * how many. A stroke shorter than `MIN_CUT_PX` does nothing. Returns how
 * many wires went.
 */
export function cutWires(ctx: CanvasCtx, a: XY, b: XY): number {
  const s = ctx.store.getState()
  if (!ctx.editable() || !s.graph || s.graph.readOnly) return 0
  if (Math.hypot(b.x - a.x, b.y - a.y) < MIN_CUT_PX) return 0
  const ids = wiresUnderCut(ctx, a, b)
  if (ids.length > 0) s.commit(ids.length === 1 ? 'cut wire' : 'cut wires', g => removeWiresWithTerms(g, ids))
  s.showFlash(cutText(ids.length))
  return ids.length
}

interface Cut {
  ctx: CanvasCtx
  a: XY
  b: XY
  /** The wires marked as about to go (edge data `cutting`). */
  marked: string[]
}

function markWires(ctx: CanvasCtx, ids: readonly string[], on: boolean): void {
  for (const id of ids) {
    try {
      ctx.rf.updateEdgeData(id, { cutting: on })
    } catch {
      // The wire is gone: nothing to mark.
    }
  }
}

export default function WireCut() {
  const [line, setLine] = useState<{ a: XY; b: XY } | null>(null)

  useEffect(() => {
    // Y is down (outside a text field), and the canvas it armed.
    let yDown = false
    let armed: CanvasCtx | null = null
    let cut: Cut | null = null
    // A press the cut took: its pointerup and click must not reach the
    // canvas, even when the cut was cancelled (a pane click would clear the
    // selection).
    let pressOwned = false
    let swallowClick = false
    let lastCutUp = -Infinity

    const disarm = () => {
      armed?.container()?.classList.remove(CUTTING_CLASS)
      armed = null
    }
    const cancel = () => {
      if (cut) markWires(cut.ctx, cut.marked, false)
      cut = null
      setLine(null)
    }
    const insideCanvas = (ctx: CanvasCtx, t: EventTarget | null) => {
      const root = ctx.container()
      return !!root && t instanceof Node && root.contains(t)
    }
    // Arms the cut on the active canvas when the pointer is over it.
    const tryArm = () => {
      if (armed || !yDown) return
      const ctx = getActiveCanvas()
      if (!ctx || !ctx.editable() || !ctx.pointerOnCanvas()) return
      if (isTypingTarget(document.activeElement) || isPopoverOpen()) return
      const root = ctx.container()
      // display:none (another app tab is showing) gives no client rects.
      if (!root || root.getClientRects().length === 0) return
      armed = ctx
      root.classList.add(CUTTING_CLASS)
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && cut) {
        e.preventDefault()
        e.stopPropagation()
        cancel()
        return
      }
      if (!isCutKey(e) || isTypingTarget(e.target)) return
      yDown = true
      // A held Y repeats: the pointer may have reached the canvas since.
      tryArm()
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== 'y' && e.key !== 'Y') return
      yDown = false
      disarm()
      if (cut) cancel()
    }
    const onBlur = () => {
      yDown = false
      disarm()
      cancel()
    }
    // Focus moving into a text field or the code editor ends the cut.
    const onFocusIn = (e: FocusEvent) => {
      if (!isTypingTarget(e.target)) return
      yDown = false
      disarm()
      cancel()
    }

    const onPointerDown = (e: PointerEvent) => {
      // Y held since before the pointer came onto the canvas.
      if (!armed && yDown && getActiveCanvas() && insideCanvas(getActiveCanvas()!, e.target)) tryArm()
      const ctx = armed
      if (!ctx || cut || e.button !== 0 || !insideCanvas(ctx, e.target)) return
      e.preventDefault()
      e.stopPropagation()
      if (!ctx.editable()) return
      const a = { x: e.clientX, y: e.clientY }
      cut = { ctx, a, b: a, marked: [] }
      pressOwned = true
      setLine({ a, b: a })
    }
    // React Flow's node drag and pan listen to mousedown (d3).
    const onMouseDown = (e: MouseEvent) => {
      if (cut || (armed && insideCanvas(armed, e.target))) {
        e.preventDefault()
        e.stopPropagation()
      }
    }
    const onPointerMove = (e: PointerEvent) => {
      if (!cut) return
      cut.b = { x: e.clientX, y: e.clientY }
      setLine({ a: cut.a, b: cut.b })
      // Mark what the line crosses now; only the changes touch the wires.
      const long = Math.hypot(cut.b.x - cut.a.x, cut.b.y - cut.a.y) >= MIN_CUT_PX
      const now = long ? wiresUnderCut(cut.ctx, cut.a, cut.b) : []
      const keep = new Set(now)
      markWires(cut.ctx, cut.marked.filter(id => !keep.has(id)), false)
      const had = new Set(cut.marked)
      markWires(cut.ctx, now.filter(id => !had.has(id)), true)
      cut.marked = now
    }
    const onPointerUp = (e: PointerEvent) => {
      if (!pressOwned) return
      pressOwned = false
      e.preventDefault()
      e.stopPropagation()
      swallowClick = true
      setTimeout(() => { swallowClick = false }, 0)
      lastCutUp = Date.now()
      if (!cut) return
      const done = { ...cut, b: { x: e.clientX, y: e.clientY } }
      cancel()
      cutWires(done.ctx, done.a, done.b)
    }
    const onClick = (e: MouseEvent) => {
      if (!swallowClick) return
      swallowClick = false
      e.preventDefault()
      e.stopPropagation()
    }
    // Two quick strokes make a dblclick, which would open the Tab menu.
    const onDblClick = (e: MouseEvent) => {
      if (!armed && Date.now() - lastCutUp > DBLCLICK_AFTER_CUT_MS) return
      e.preventDefault()
      e.stopPropagation()
    }

    window.addEventListener('keydown', onKeyDown, true)
    window.addEventListener('keyup', onKeyUp, true)
    window.addEventListener('blur', onBlur)
    window.addEventListener('focusin', onFocusIn, true)
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('mousedown', onMouseDown, true)
    window.addEventListener('pointermove', onPointerMove, true)
    window.addEventListener('pointerup', onPointerUp, true)
    window.addEventListener('click', onClick, true)
    window.addEventListener('dblclick', onDblClick, true)
    return () => {
      disarm()
      cancel()
      window.removeEventListener('keydown', onKeyDown, true)
      window.removeEventListener('keyup', onKeyUp, true)
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('focusin', onFocusIn, true)
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('mousedown', onMouseDown, true)
      window.removeEventListener('pointermove', onPointerMove, true)
      window.removeEventListener('pointerup', onPointerUp, true)
      window.removeEventListener('click', onClick, true)
      window.removeEventListener('dblclick', onDblClick, true)
    }
  }, [])

  if (!line) return null
  return (
    <svg className="nb-wire-cut" data-testid="nb-wire-cut" aria-hidden="true">
      <line className="nb-wire-cut__halo" x1={line.a.x} y1={line.a.y} x2={line.b.x} y2={line.b.y} />
      <line className="nb-wire-cut__line" x1={line.a.x} y1={line.a.y} x2={line.b.x} y2={line.b.y} />
    </svg>
  )
}
