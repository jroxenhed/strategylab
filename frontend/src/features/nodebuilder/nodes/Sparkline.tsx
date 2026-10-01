/**
 * Sparklines on nodes (spec S26, W4 item 4.C).
 *
 * Each node shows the shape of what it writes: a thin line for numbers, a
 * strip of bars for booleans. The data is the last `/preview` answer in the
 * store (`store.preview`, filled by `useAutoCook`); nothing here sends a
 * request.
 *
 * How it is drawn (performance rule 8.3): there is ONE `<canvas>` for the
 * whole canvas, `SparklineLayer`, inside React Flow's viewport so it moves
 * with the flow. BaseNode only reserves a 28px slot and registers the slot
 * element with `useSparklineSlot` (a plain map, no React state). The layer
 * finds each slot on screen and draws into it. No per-node `<svg>` or
 * `<canvas>`.
 *
 * The layer redraws when the preview changes, when a pan or zoom ends, when
 * the graph changes (a drag ends, a node is added) and when the canvas
 * resizes; never per frame. During a drag it hides after 100 ms and shows
 * again on drop. Below zoom 0.5, or while the builder is hidden, it draws
 * nothing.
 *
 * The layer sits above the node cards (their background is opaque, so a
 * layer under them would be covered) and never takes pointer events: hover
 * and click are on the slot element in BaseNode.
 *
 * `useNodePreview(nodeId)` reads one node's entry, so a node re-renders only
 * when its own sparkline changes (the store keeps unchanged entries as the
 * same objects).
 */

import { useCallback, useEffect, useRef } from 'react'
import { useStoreApi, ViewportPortal, type ReactFlowState } from '@xyflow/react'
import type { PreviewNode } from '../../../api/nodebuilderInspect'
import { tokenColor } from '../minimapColors'
import { useNodeBuilderStore, type NodeBuilderState } from '../store'

/** Slot height in px (foundation 4.7). */
export const SPARKLINE_HEIGHT = 28
/** Room kept at the right end of a line for the last value. */
export const LINE_TEXT_ROOM = 30
/** Below this zoom the layer draws nothing (S26). */
export const SPARKLINE_MIN_ZOOM = 0.5
/** Hide the layer after a drag has run this long. */
export const DRAG_HIDE_MS = 100
/** Drawing budget for the whole layer (logged once in dev when exceeded). */
const BUDGET_MS = 4

const THIN_SPACE = ' '

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

/** The last value printed at the right end: 2 decimals; 1000 and up as whole numbers with thin spaces. */
export function formatLastValue(v: number): string {
  if (!Number.isFinite(v)) return ''
  if (Math.abs(v) >= 1000) {
    const sign = v < 0 ? '-' : ''
    const whole = Math.round(Math.abs(v)).toString()
    return sign + whole.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE)
  }
  return v.toFixed(2)
}

/** `2.5 % true` (one decimal). */
export function formatTruePct(pct: number | undefined): string {
  return `${(pct ?? 0).toFixed(1)} % true`
}

function fmt(v: number | undefined): string {
  return v == null || !Number.isFinite(v) ? '—' : formatLastValue(v)
}

function lastFinite(values: readonly (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i]
    if (v != null && Number.isFinite(v)) return v
  }
  return null
}

/** Hover text on the slot (S26). */
export function sparklineTooltip(p: PreviewNode): string {
  if (p.kind === 'bool') return `${p.attr} · ${formatTruePct(p.true_pct)}`
  const nan = p.nan_count ? ` · ${p.nan_count} nan` : ''
  return `${p.attr} · min ${fmt(p.min)} · max ${fmt(p.max)}${nan}`
}

/** The summary screen readers get instead of the drawing (S26). */
export function sparklineSummary(p: PreviewNode): string {
  if (p.kind === 'bool') return `sparkline: ${p.attr} ${formatTruePct(p.true_pct)}`
  const last = lastFinite(p.values)
  return `sparkline: ${p.attr} from ${fmt(p.min)} to ${fmt(p.max)}, last ${last == null ? '—' : formatLastValue(last)}`
}

// ---------------------------------------------------------------------------
// Drawing (pure: takes a 2D context and a box, so tests can spy on it)
// ---------------------------------------------------------------------------

/** The 2D context calls the drawing uses (a real context has them all). */
export type SparkCtx = Pick<
  CanvasRenderingContext2D,
  | 'beginPath' | 'moveTo' | 'lineTo' | 'stroke' | 'fillRect' | 'fillText' | 'setLineDash'
  | 'strokeStyle' | 'fillStyle' | 'lineWidth' | 'lineJoin' | 'globalAlpha' | 'font' | 'textAlign' | 'textBaseline'
>

export interface SparkOpts {
  /** Top-left of the slot and its size, in the context's units. */
  x: number
  y: number
  width: number
  height?: number
  /** Stroke and fill color (category color, or the dim token when stale). */
  color: string
  /** Color of the last-value text. */
  textColor?: string
  /** Color of the zero line. */
  midColor?: string
  /** Color of the warmup tick. */
  dimColor?: string
  /** Server min and max (else worked out from the values). */
  min?: number
  max?: number
  nanCount?: number
  truePct?: number
}

const MONO_9 = '9px ui-monospace, SFMono-Regular, Menlo, monospace'

/** The warmup tick: a 1px line along the top-left, as long as the leading nulls. */
function drawWarmup(ctx: SparkCtx, values: readonly (number | null)[], o: SparkOpts, plotW: number): void {
  if (!o.nanCount || o.nanCount <= 0 || values.length === 0) return
  let lead = 0
  while (lead < values.length && values[lead] == null) lead++
  const len = Math.max(2, (lead / values.length) * plotW)
  ctx.globalAlpha = 1
  ctx.fillStyle = o.dimColor ?? o.color
  ctx.fillRect(o.x, o.y, len, 1)
}

/**
 * A numeric sparkline: one polyline, broken at nulls (no interpolation).
 * 96 finite values give one moveTo and 95 lineTo calls.
 */
export function drawLine(ctx: SparkCtx, values: readonly (number | null)[], o: SparkOpts): void {
  const h = o.height ?? SPARKLINE_HEIGHT
  const plotW = Math.max(1, o.width - LINE_TEXT_ROOM)
  let lo = o.min
  let hi = o.max
  if (lo == null || hi == null || !Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = Infinity
    hi = -Infinity
    for (const v of values) {
      if (v == null || !Number.isFinite(v)) continue
      if (v < lo) lo = v
      if (v > hi) hi = v
    }
  }
  const pad = 3
  const top = o.y + pad
  const span = h - pad * 2
  const flat = !(hi > lo)
  const yOf = (v: number) => (flat ? top + span / 2 : top + (1 - (v - lo!) / (hi! - lo!)) * span)
  const step = values.length > 1 ? plotW / (values.length - 1) : 0

  // Zero line when the range crosses zero.
  if (!flat && lo! < 0 && hi! > 0) {
    ctx.globalAlpha = 1
    ctx.strokeStyle = o.midColor ?? o.color
    ctx.lineWidth = 1
    ctx.setLineDash([1, 2])
    ctx.beginPath()
    const y0 = Math.round(yOf(0)) + 0.5
    ctx.moveTo(o.x, y0)
    ctx.lineTo(o.x + plotW, y0)
    ctx.stroke()
    ctx.setLineDash([])
  }

  drawWarmup(ctx, values, o, plotW)

  ctx.globalAlpha = 0.85
  ctx.strokeStyle = o.color
  ctx.lineWidth = 1
  ctx.lineJoin = 'round'
  ctx.beginPath()
  let pen = false
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null || !Number.isFinite(v)) { pen = false; continue }
    const px = o.x + i * step
    const py = yOf(v)
    if (pen) ctx.lineTo(px, py)
    else { ctx.moveTo(px, py); pen = true }
  }
  ctx.stroke()

  const last = lastFinite(values)
  if (last != null) {
    ctx.globalAlpha = 1
    ctx.fillStyle = o.textColor ?? o.color
    ctx.font = MONO_9
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    ctx.fillText(formatLastValue(last), o.x + o.width, o.y + h / 2)
  }
  ctx.globalAlpha = 1
}

/** Room for `100.0 % true` at 9px mono. */
export const BOOL_TEXT_ROOM = 54

/**
 * A boolean strip: one bar per bucket with any true bars in it, its alpha
 * scaled by the bucket's share of true (at least 0.35), and `<pct> % true`
 * at the right end.
 */
export function drawBool(ctx: SparkCtx, values: readonly (number | null)[], o: SparkOpts): void {
  const h = o.height ?? SPARKLINE_HEIGHT
  const plotW = Math.max(1, o.width - BOOL_TEXT_ROOM)
  const n = Math.max(1, values.length)
  const step = plotW / n
  const barW = Math.max(1, Math.min(2, step * 0.8))
  const barH = 16
  const top = o.y + (h - barH) / 2
  drawWarmup(ctx, values, o, plotW)
  ctx.fillStyle = o.color
  for (let i = 0; i < values.length; i++) {
    const share = values[i]
    if (share == null || !(share > 0)) continue
    ctx.globalAlpha = 0.85 * Math.max(0.35, Math.min(1, share))
    ctx.fillRect(o.x + i * step, top, barW, barH)
  }
  ctx.globalAlpha = 1
  ctx.fillStyle = o.textColor ?? o.color
  ctx.font = MONO_9
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  ctx.fillText(formatTruePct(o.truePct), o.x + o.width, o.y + h / 2)
}

/** Draw one node's sparkline into its box. */
export function drawSparkline(ctx: SparkCtx, p: PreviewNode, o: Omit<SparkOpts, 'min' | 'max' | 'nanCount' | 'truePct'>): void {
  const full: SparkOpts = { ...o, min: p.min, max: p.max, nanCount: p.nan_count, truePct: p.true_pct }
  if (p.kind === 'bool') drawBool(ctx, p.values, full)
  else drawLine(ctx, p.values, full)
}

// ---------------------------------------------------------------------------
// Store reads
// ---------------------------------------------------------------------------

/** One node's sparkline, or undefined. Re-renders only when that node's entry changes. */
export function useNodePreview(nodeId: string | null | undefined): PreviewNode | undefined {
  return useNodeBuilderStore(s => (nodeId ? s.preview.nodes[nodeId] : undefined))
}

// ---------------------------------------------------------------------------
// Slot registry (BaseNode -> layer), no React state
// ---------------------------------------------------------------------------

interface SlotEntry {
  el: HTMLElement
  /** Category color, as a CSS value (`var(--nb-cat-indicator)`). */
  color: string
}

const slots = new Map<string, SlotEntry>()
const redrawListeners = new Set<() => void>()

/** Ask every mounted layer to redraw on the next frame. */
export function requestSparklineRedraw(): void {
  for (const l of redrawListeners) l()
}

/** The slots registered now (tests and the layer). */
export function sparklineSlots(): ReadonlyMap<string, SlotEntry> {
  return slots
}

/**
 * BaseNode's slot: returns a ref callback for the 28px slot element. It
 * records the element (and the node's category color) in a plain map and
 * asks the layer to redraw; it sets no React state.
 */
export function useSparklineSlot(nodeId: string | null | undefined, color: string): (el: HTMLElement | null) => void {
  // A new color gives a new callback, so React detaches and re-attaches it
  // and the map holds the new color (a category change is rare).
  return useCallback((el: HTMLElement | null) => {
    if (!nodeId) return
    if (el) slots.set(nodeId, { el, color })
    else slots.delete(nodeId)
    requestSparklineRedraw()
  }, [nodeId, color])
}

// ---------------------------------------------------------------------------
// The layer
// ---------------------------------------------------------------------------

function cssColor(v: string, fallback: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(v.trim())
  if (!m) return v
  const c = tokenColor(m[1])
  return c || fallback
}

/** A screen rectangle (getBoundingClientRect's fields the layer uses). */
export interface ScreenRect { left: number; top: number; right: number; bottom: number }

/** One node card in paint order, for the cover test. */
export interface PaintedNode { el: Element; rect: ScreenRect; z: number }

function overlaps(a: ScreenRect, b: ScreenRect): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
}

/**
 * True when a card painted above the slot's own card overlaps the slot: a
 * higher z-index, or the same z-index later in the DOM (DV-14). The layer
 * sits above every card (cards are opaque), so it skips such a slot rather
 * than draw one node's sparkline over another node.
 */
export function isSlotCovered(slot: ScreenRect, ownerIndex: number, nodes: readonly PaintedNode[]): boolean {
  const ownZ = ownerIndex >= 0 ? nodes[ownerIndex].z : 0
  for (let j = 0; j < nodes.length; j++) {
    if (j === ownerIndex) continue
    const n = nodes[j]
    const above = n.z > ownZ || (n.z === ownZ && j > ownerIndex)
    if (above && overlaps(slot, n.rect)) return true
  }
  return false
}

const selectPreview = (s: NodeBuilderState) => s.preview
const selectViewport = (s: NodeBuilderState) => s.viewport
const selectGraph = (s: NodeBuilderState) => s.graph
const anyDragging = (s: ReactFlowState) => s.nodes.some(n => n.dragging)

let budgetLogged = false

/**
 * The one canvas all sparklines are drawn on. CanvasChrome mounts it inside
 * <ReactFlow>.
 */
export function SparklineLayer() {
  const rf = useStoreApi()
  const anchorRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const draw = useCallback(() => {
    const anchor = anchorRef.current
    const canvas = canvasRef.current
    if (!anchor || !canvas) return
    const t0 = typeof performance !== 'undefined' ? performance.now() : 0
    const rfState = rf.getState()
    const zoom = rfState.transform[2] || 1
    const container = rfState.domNode
    const clear = () => { if (canvas.width !== 0) canvas.width = 0; if (canvas.height !== 0) canvas.height = 0 }
    // Hidden tab (display: none) or zoomed far out: draw nothing.
    if ((container && container.clientWidth === 0) || zoom < SPARKLINE_MIN_ZOOM) { clear(); return }

    const { preview } = useNodeBuilderStore.getState()
    const origin = anchor.getBoundingClientRect()
    // The visible part of the flow, in flow units: only slots on screen are
    // drawn, and the canvas never grows past the screen (DV-3). Unknown
    // (zero-size) container: no clipping.
    const view = container ? container.getBoundingClientRect() : null
    const clip = view && view.width > 0 && view.height > 0
      ? {
        x0: (view.left - origin.left) / zoom,
        y0: (view.top - origin.top) / zoom,
        x1: (view.right - origin.left) / zoom,
        y1: (view.bottom - origin.top) / zoom,
      }
      : null
    // Cards in paint order, for the cover test (DV-14).
    const painted: PaintedNode[] = []
    if (container) {
      container.querySelectorAll<HTMLElement>('.react-flow__node').forEach(el => {
        painted.push({ el, rect: el.getBoundingClientRect(), z: Number.parseInt(el.style.zIndex, 10) || 0 })
      })
    }
    type Item = { p: PreviewNode; x: number; y: number; w: number; color: string }
    const items: Item[] = []
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const [id, entry] of slots) {
      const p = preview.nodes[id]
      if (!p || !entry.el.isConnected) continue
      const r = entry.el.getBoundingClientRect()
      if (r.width === 0) continue
      if (painted.length > 1) {
        const owner = entry.el.closest('.react-flow__node')
        if (isSlotCovered(r, painted.findIndex(n => n.el === owner), painted)) continue
      }
      const x = (r.left - origin.left) / zoom
      const y = (r.top - origin.top) / zoom
      const w = r.width / zoom
      if (clip && (x + w < clip.x0 || x > clip.x1 || y + SPARKLINE_HEIGHT < clip.y0 || y > clip.y1)) continue
      items.push({ p, x, y, w, color: entry.color })
      minX = Math.min(minX, x); minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + w); maxY = Math.max(maxY, y + SPARKLINE_HEIGHT)
    }
    if (items.length === 0) { clear(); return }
    if (clip) {
      minX = Math.max(minX, clip.x0); minY = Math.max(minY, clip.y0)
      maxX = Math.min(maxX, clip.x1); maxY = Math.min(maxY, clip.y1)
    }

    // The canvas covers the visible slots' bounding box, in flow units, at
    // screen resolution (so at most about the screen size times dpr squared).
    const bw = maxX - minX
    const bh = maxY - minY
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1
    let scale = dpr * zoom
    const MAX_SIDE = 8192
    scale = Math.min(scale, MAX_SIDE / Math.max(1, bw), MAX_SIDE / Math.max(1, bh))
    canvas.style.left = `${minX}px`
    canvas.style.top = `${minY}px`
    canvas.style.width = `${bw}px`
    canvas.style.height = `${bh}px`
    // Setting width/height reallocates the backing store: only when the size
    // changes; otherwise clear what is there (DV-3).
    const pw = Math.max(1, Math.ceil(bw * scale))
    const ph = Math.max(1, Math.ceil(bh * scale))
    const resized = canvas.width !== pw || canvas.height !== ph
    if (canvas.width !== pw) canvas.width = pw
    if (canvas.height !== ph) canvas.height = ph
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    if (!resized) {
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, pw, ph)
    }
    ctx.setTransform(scale, 0, 0, scale, -minX * scale, -minY * scale)

    const dim = cssColor('var(--nb-text-dim)', '#7a8296')
    const muted = cssColor('var(--nb-text-muted)', '#98a1b3')
    const strong = cssColor('var(--nb-border-strong)', '#2f3848')
    for (const it of items) {
      const color = preview.stale ? dim : cssColor(it.color, muted)
      drawSparkline(ctx, it.p, {
        x: it.x, y: it.y, width: it.w, height: SPARKLINE_HEIGHT,
        color, textColor: preview.stale ? dim : muted, midColor: strong, dimColor: dim,
      })
    }
    if (import.meta.env?.DEV && t0) {
      const ms = performance.now() - t0
      if (ms > BUDGET_MS && !budgetLogged) {
        budgetLogged = true
        console.warn(`[nodebuilder] sparklines took ${ms.toFixed(1)} ms for ${items.length} nodes (budget ${BUDGET_MS} ms)`)
      }
    }
  }, [rf])

  // One draw per frame at most, however many triggers arrive.
  const frame = useRef<number | null>(null)
  const schedule = useCallback(() => {
    if (frame.current != null) return
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb: FrameRequestCallback) => setTimeout(() => cb(0), 16) as unknown as number
    frame.current = raf(() => { frame.current = null; draw() })
  }, [draw])

  useEffect(() => {
    redrawListeners.add(schedule)
    // Redraw on a new preview, the end of a pan or zoom, and any graph change
    // (a drag ends in a commit). Never per frame: none of these change per frame.
    const unsub = useNodeBuilderStore.subscribe((s, prev) => {
      if (selectPreview(s) !== selectPreview(prev) || selectViewport(s) !== selectViewport(prev) || selectGraph(s) !== selectGraph(prev)) schedule()
    })
    // Hide while dragging (after 100 ms), show on drop.
    let dragTimer: ReturnType<typeof setTimeout> | null = null
    let dragging = anyDragging(rf.getState())
    const unsubRf = rf.subscribe(s => {
      const now = anyDragging(s)
      if (now === dragging) return
      dragging = now
      const canvas = canvasRef.current
      if (now) {
        dragTimer = setTimeout(() => { if (canvasRef.current) canvasRef.current.style.visibility = 'hidden' }, DRAG_HIDE_MS)
      } else {
        if (dragTimer != null) clearTimeout(dragTimer)
        dragTimer = null
        if (canvas) canvas.style.visibility = ''
        schedule()
      }
    })
    // Canvas resize (panel resize end, window resize).
    let ro: ResizeObserver | null = null
    const dom = rf.getState().domNode
    if (dom && typeof ResizeObserver === 'function') {
      ro = new ResizeObserver(() => schedule())
      ro.observe(dom)
    }
    schedule()
    return () => {
      redrawListeners.delete(schedule)
      unsub()
      unsubRf()
      ro?.disconnect()
      if (dragTimer != null) clearTimeout(dragTimer)
      if (frame.current != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame.current)
      frame.current = null
    }
  }, [rf, schedule])

  return (
    <ViewportPortal>
      <div ref={anchorRef} style={{ position: 'absolute', left: 0, top: 0, width: 0, height: 0, zIndex: 1500, pointerEvents: 'none' }}>
        <canvas
          ref={canvasRef}
          data-testid="nb-sparkline-layer"
          aria-hidden="true"
          style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'none' }}
        />
      </div>
    </ViewportPortal>
  )
}

export default SparklineLayer
