/**
 * View operations for the node builder canvas (F435 W3 item 3.D): framing
 * and the gentle wheel zoom.
 *
 * Framing follows Houdini: F fits the selection, H (and Home) fit every
 * node. Both pad by 80px, never zoom in past 100% or out past 15%, and ease
 * over 150 ms (instant with reduced motion). `frameNode(id)` is the public
 * entry point for other surfaces (the Inspector, diagnostics, banners):
 *
 *   import { focusNode, frameNode } from './viewOps'
 *   focusNode(id)                              // select it, frame it if off-screen
 *   frameNode(id)                              // always frame it
 *   frameNode(id, { onlyIfOffscreen: true })   // frame only when not fully on screen
 *
 * Import these from here, not from commands/view.ts: a module in commands/
 * must not be imported first by anything else (see commands/index.ts).
 */

import type { ReactFlowInstance } from '@xyflow/react'
import type { CanvasCtx } from './canvasPlugins'
import { getActiveCanvas } from './commands'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import type { Viewport } from './store/view'

/** Zoom range of the canvas (foundation 6.1). */
export const MIN_ZOOM = 0.15
export const MAX_ZOOM = 3

/** Framing never zooms in past this (S22: zoom clamped to [0.15, 1.0]). */
export const FRAME_MAX_ZOOM = 1
/** Space kept around framed nodes, in screen pixels. */
export const FRAME_PADDING_PX = 80
/** Framing ease (--nb-motion-base). */
export const FRAME_DURATION_MS = 150

/** Wheel zoom factor per 100px of wheel movement (foundation 6.1). */
export const WHEEL_ZOOM_STEP = 1.08

type FitViewOptions = NonNullable<Parameters<ReactFlowInstance['fitView']>[0]>

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
  } catch {
    return false
  }
}

/**
 * Fit options for framing. `animate: false` for the first fit after a load,
 * which should not slide in.
 */
export function frameOptions(animate = true): FitViewOptions {
  return {
    padding: `${FRAME_PADDING_PX}px`,
    minZoom: MIN_ZOOM,
    maxZoom: FRAME_MAX_ZOOM,
    duration: animate && !prefersReducedMotion() ? FRAME_DURATION_MS : 0,
  }
}

/** True when there is something on the canvas to frame (the frame commands' `when`). */
export function hasNodesToFrame(s: NodeBuilderState = useNodeBuilderStore.getState()): boolean {
  // The read-only view's graph is not in the store; ask the canvas.
  const canvas = getActiveCanvas()
  if (canvas) return canvas.rf.getNodes().length > 0
  return !!s.graph && Object.keys(s.graph.nodes).length > 0
}

/** Fit every node on the canvas (graph nodes, boxes, notes). False when there is none. */
export function frameAll(canvas: CanvasCtx, animate = true): boolean {
  if (canvas.rf.getNodes().length === 0) return false
  void canvas.rf.fitView(frameOptions(animate))
  return true
}

/** Fit these React Flow node ids. Unknown ids are skipped; false when none is left. */
export function frameNodes(canvas: CanvasCtx, ids: readonly string[], animate = true): boolean {
  const known = ids.filter(id => canvas.rf.getNode(id) != null)
  if (known.length === 0) return false
  void canvas.rf.fitView({ ...frameOptions(animate), nodes: known.map(id => ({ id })) })
  return true
}

/**
 * What F frames: the selected nodes, boxes and notes, plus both ends of
 * each selected wire. Read from the store, which updates at once (React
 * Flow's own selection follows one effect later).
 */
export function selectionToFrame(canvas: CanvasCtx): string[] {
  const s = useNodeBuilderStore.getState()
  const ids = new Set<string>([...s.selectedNodeIds, ...s.selectedAnnotationIds])
  if (s.selectedNodeId) ids.add(s.selectedNodeId)
  if (s.selectedWireIds.length > 0) {
    const wires = canvas.graph()?.wires ?? []
    for (const w of wires) {
      if (!s.selectedWireIds.includes(w.id)) continue
      ids.add(w.from)
      ids.add(w.to)
    }
  }
  return [...ids]
}

/** F: frame the selection, or everything when nothing is selected (Houdini). */
export function frameSelection(canvas: CanvasCtx): boolean {
  const ids = selectionToFrame(canvas)
  if (ids.length > 0 && frameNodes(canvas, ids)) return true
  return frameAll(canvas)
}

/** True when the node is wholly inside the visible canvas. */
export function isNodeOnScreen(canvas: CanvasCtx, id: string): boolean {
  const node = canvas.rf.getInternalNode(id)
  const el = canvas.container()
  if (!node || !el) return false
  const rect = el.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return false
  const { x, y, zoom } = canvas.rf.getViewport()
  const p = node.internals.positionAbsolute
  const w = node.measured?.width ?? node.width ?? 0
  const h = node.measured?.height ?? node.height ?? 0
  const left = p.x * zoom + x
  const top = p.y * zoom + y
  return left >= 0 && top >= 0 && left + w * zoom <= rect.width && top + h * zoom <= rect.height
}

export interface FrameNodeOptions {
  /** Leave the view alone when the node is already fully on screen. */
  onlyIfOffscreen?: boolean
  /** The canvas to frame in; default: the active canvas. */
  canvas?: CanvasCtx | null
}

/**
 * Frame one node (F behavior for a single node). Returns true when the view
 * moved; false with no canvas, an unknown id, or a node already on screen
 * (with `onlyIfOffscreen`).
 */
export function frameNode(id: string, opts: FrameNodeOptions = {}): boolean {
  const canvas = opts.canvas === undefined ? getActiveCanvas() : opts.canvas
  if (!canvas) return false
  if (opts.onlyIfOffscreen && isNodeOnScreen(canvas, id)) return false
  return frameNodes(canvas, [id])
}

/**
 * Select only this node and frame it when it is off-screen (S05: a
 * diagnostics row, a badge, a banner's node link).
 */
export function focusNode(id: string, opts: Omit<FrameNodeOptions, 'onlyIfOffscreen'> = {}): void {
  useNodeBuilderStore.getState().select(id)
  frameNode(id, { ...opts, onlyIfOffscreen: true })
}

// ── Wheel ────────────────────────────────────────────────────────────────────

export interface WheelInput {
  deltaX: number
  deltaY: number
  /** 0 pixels, 1 lines, 2 pages (WheelEvent.deltaMode). */
  deltaMode: number
  metaKey: boolean
  shiftKey: boolean
}

/** A wheel delta in pixels. */
export function wheelPixels(delta: number, deltaMode: number): number {
  if (deltaMode === 1) return delta * 25   // lines (Firefox mouse wheel)
  if (deltaMode === 2) return delta * 800  // pages
  return delta
}

/**
 * The viewport after one wheel event, or null when nothing changes.
 * `point` is the pointer position relative to the canvas, in screen pixels.
 *
 * Plain wheel zooms toward the pointer, 1.08x per 100px (foundation 6.1).
 * Cmd+wheel pans vertically, Shift+wheel pans horizontally, and a sideways
 * trackpad swipe (no vertical part) pans horizontally too. Ctrl+wheel is a
 * trackpad pinch and stays with React Flow.
 */
export function wheelViewport(vp: Viewport, e: WheelInput, point: { x: number; y: number }): Viewport | null {
  const dx = wheelPixels(e.deltaX, e.deltaMode)
  const dy = wheelPixels(e.deltaY, e.deltaMode)
  if (e.shiftKey) {
    // Some systems already turn Shift+wheel into a sideways delta.
    const d = dx !== 0 ? dx : dy
    return d === 0 ? null : { ...vp, x: vp.x - d }
  }
  if (e.metaKey) return dy === 0 ? null : { ...vp, y: vp.y - dy }
  if (dy === 0) return dx === 0 ? null : { ...vp, x: vp.x - dx }
  const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, vp.zoom * Math.pow(WHEEL_ZOOM_STEP, -dy / 100)))
  if (zoom === vp.zoom) return null
  // Keep the flow point under the pointer where it is.
  const fx = (point.x - vp.x) / vp.zoom
  const fy = (point.y - vp.y) / vp.zoom
  return { x: point.x - fx * zoom, y: point.y - fy * zoom, zoom }
}

/** Wheel events the canvas leaves alone: pinch, scrollable parts, the minimap. */
export function wheelIsForSomethingElse(e: WheelEvent): boolean {
  if (e.ctrlKey) return true  // trackpad pinch (and Ctrl+wheel): React Flow zooms
  const t = e.target
  if (!(t instanceof Element)) return false
  return t.closest('.nowheel, .react-flow__minimap') != null
}
