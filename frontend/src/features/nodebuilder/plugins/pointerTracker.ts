/**
 * Pointer tracker (F435 W3 item 3.H, spec S20): feeds the status bar's
 * cursor and zoom segments from the canvas, without React state.
 *
 * - Pointer move: the flow position is kept and written into the cursor
 *   segment's text once per animation frame (`writeCursor`).
 * - Pointer leave: the cursor reads `—, —`.
 * - Pan or zoom: the zoom is passed on at most every 100 ms, with a last
 *   write at the end of the gesture. A plain pan does not change the zoom,
 *   so the bar does not re-render for it.
 *
 * When the builder is hidden (another app tab), the canvas gets no pointer
 * or move events, so nothing runs.
 *
 * This module also puts 3.H's parts in their slots, because canvasPlugins.ts
 * loads every module in plugins/ before the builder first renders: the status
 * bar (`statusBar`), the `?` overlay (`overlays`) and Reset view
 * (`toolbarRight`, order 10: first in the right cluster, A1).
 */

import type { CanvasPlugin, XY } from '../canvasPlugins'
import { registerSlot } from '../slots'
import StatusBar from '../StatusBar'
import { NO_CURSOR, setLiveZoom, writeCursor } from '../statusChannels'
import ShortcutHelp from '../ShortcutHelp'
import { ResetViewButton } from '../HintBar'

registerSlot('statusBar', 'status', StatusBar, 10)
registerSlot('overlays', 'shortcutHelp', ShortcutHelp, 10)
registerSlot('toolbarRight', 'resetView', ResetViewButton, 10)

/** The zoom text changes at most this often during a zoom (S20). */
export const ZOOM_THROTTLE_MS = 100

/** `1173, 277`: flow units, rounded. */
export function cursorText(p: XY): string {
  return `${Math.round(p.x)}, ${Math.round(p.y)}`
}

// Cursor: the newest position, written on the next animation frame.
let pending: XY | null = null
let frame: number | null = null

function flushCursor() {
  frame = null
  if (pending) writeCursor(cursorText(pending))
  pending = null
}

// Zoom: leading write, then at most one per ZOOM_THROTTLE_MS with a trailing
// write so the final value always lands.
let lastZoomAt = 0
let zoomTimer: ReturnType<typeof setTimeout> | null = null
let nextZoom: number | null = null

function flushZoom() {
  zoomTimer = null
  lastZoomAt = Date.now()
  if (nextZoom != null) setLiveZoom(nextZoom)
  nextZoom = null
}

export const plugin: CanvasPlugin = {
  id: 'pointerTracker',

  onPointerMove(flowPos) {
    pending = flowPos
    if (frame != null) return
    if (typeof requestAnimationFrame !== 'function') return flushCursor()
    const id = requestAnimationFrame(flushCursor)
    // A frame that ran at once (a test stub) has already flushed.
    if (pending) frame = id
  },

  onPointerLeave() {
    pending = null
    if (frame != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame)
    frame = null
    writeCursor(NO_CURSOR)
  },

  onMove(viewport) {
    nextZoom = viewport.zoom
    if (zoomTimer != null) return
    const wait = ZOOM_THROTTLE_MS - (Date.now() - lastZoomAt)
    if (wait <= 0) flushZoom()
    else zoomTimer = setTimeout(flushZoom, wait)
  },
}
