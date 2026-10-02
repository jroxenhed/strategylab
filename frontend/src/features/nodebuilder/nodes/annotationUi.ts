/**
 * Small UI helpers shared by the network box and sticky note renderers and
 * the box drag plugin (item 3.E). Kept apart from the components so those
 * files only export components (fast refresh).
 */

import { useEffect, useRef, useSyncExternalStore } from 'react'
import { useStore, type ReactFlowState } from '@xyflow/react'
import { useNodeBuilderStore } from '../store'

// ---------------------------------------------------------------------------
// "Will join" signal: the box a dragged node would join on drop (S17).
// Kept outside the store so a drag never writes to it per frame.
// ---------------------------------------------------------------------------

let hotBoxId: string | null = null
const hotListeners = new Set<() => void>()

/** Set the box that shows the "will join" border (null: none). */
export function setHotBox(id: string | null): void {
  if (hotBoxId === id) return
  hotBoxId = id
  for (const l of [...hotListeners]) l()
}

/** The box that shows the "will join" border now. */
export function getHotBox(): string | null {
  return hotBoxId
}

function subscribeHot(l: () => void): () => void {
  hotListeners.add(l)
  return () => { hotListeners.delete(l) }
}

/** True while this box shows the "will join" border. */
export function useIsHotBox(id: string): boolean {
  return useSyncExternalStore(subscribeHot, () => hotBoxId === id)
}

/**
 * Give the canvas root keyboard focus again after an edit (spec 0.8), so
 * the keys work at once. Only when the editor still has focus: a blur to
 * somewhere else keeps that focus.
 */
export function focusCanvasRoot(el: HTMLElement | null): void {
  const root = el?.closest<HTMLElement>('.nodebuilder-root')
  if (root && document.activeElement === el) root.focus()
}

// ---------------------------------------------------------------------------
// Marquee: boxes and notes only when fully inside (S17, S18)
// ---------------------------------------------------------------------------

/** A marquee rect in pane pixels, as React Flow keeps it. */
export interface PaneRect { x: number; y: number; width: number; height: number }

/**
 * True when the item's flow rect lies fully inside the marquee. The marquee
 * is in pane pixels; `transform` is React Flow's [x, y, zoom].
 */
export function fullyInsideMarquee(
  rect: readonly [number, number, number, number],
  marquee: PaneRect,
  transform: readonly [number, number, number],
): boolean {
  const [tx, ty, zoom] = transform
  const left = (marquee.x - tx) / zoom
  const top = (marquee.y - ty) / zoom
  const right = left + marquee.width / zoom
  const bottom = top + marquee.height / zoom
  return rect[0] >= left && rect[1] >= top && rect[0] + rect[2] <= right && rect[1] + rect[3] <= bottom
}

/**
 * The canvas marquee selects anything it touches (partial mode, good for
 * nodes). A box or note should join only when fully inside. While a marquee
 * is open this follows whether the item is fully inside; when it closes, an
 * item that the marquee selected while only touching it is dropped from the
 * selection. An item that was already selected before the marquee (a
 * Shift-marquee adding to it) stays.
 */
export function useFullMarqueeOnly(id: string, rect: readonly [number, number, number, number], selected: boolean): void {
  const inside = useStore((s: ReactFlowState) =>
    s.userSelectionActive && s.userSelectionRect ? fullyInsideMarquee(rect, s.userSelectionRect, s.transform) : null,
  )
  const active = inside !== null
  const lastInside = useRef(false)
  const selectedAtStart = useRef(false)
  const wasActive = useRef(false)
  const selectedRef = useRef(selected)
  useEffect(() => {
    selectedRef.current = selected
  }, [selected])
  useEffect(() => {
    if (inside !== null) lastInside.current = inside
  }, [inside])
  useEffect(() => {
    if (active && !wasActive.current) selectedAtStart.current = selectedRef.current
    if (!active && wasActive.current && !lastInside.current && !selectedAtStart.current) {
      const s = useNodeBuilderStore.getState()
      if (s.selectedAnnotationIds.includes(id)) {
        s.setSelection({
          nodeIds: s.selectedNodeIds,
          wireIds: s.selectedWireIds,
          annotationIds: s.selectedAnnotationIds.filter(x => x !== id),
          primary: s.selectedNodeId,
        })
      }
    }
    wasActive.current = active
  }, [active, id])
}
