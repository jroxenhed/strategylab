/**
 * Wire moves on the canvas (item 3.G, spec S23).
 *
 * Reconnect: pick a wire's end up (React Flow's reconnect anchors, 18px)
 * and drop it on another port. A valid drop is one `commit('reconnect
 * wire')` that keeps the wire id. A drop on an invalid port snaps back. A
 * drop on empty canvas flashes the wire red for 150 ms and deletes it
 * (Houdini). Esc during the drag cancels it.
 *
 * Splice: drag ONE node that has a free input and an output over a wire.
 * After 150 ms over the same wire the wire goes hot; releasing there (Cmd
 * not held) puts the node into the wire, in the same undo step as the move
 * (the canvas batches the drag stop). A splice that would close a loop
 * flashes the wire red and leaves a plain move. The hit test uses points
 * sampled along each wire once at drag start, never the DOM.
 *
 * Double-clicking a wire opens the Tab menu at its middle; the new node is
 * spliced in (`openInsertMenu`, also used by `wires.insertNode`).
 *
 * Visual states go on the wire through `rf.updateEdgeData` (`spliceHot`,
 * `flash`); the next store sync replaces them.
 *
 * Loaded by the plugins/ auto-loader (canvasPlugins.ts).
 */

import type { Connection, Edge as RFEdge, FinalConnectionState, Node as RFNode } from '@xyflow/react'
import type { Graph, GraphWire } from '../../../api/nodebuilder'
import type { CanvasCtx, CanvasPlugin, XY } from '../canvasPlugins'
import { isEmptyCanvasTarget } from '../canvasHelpers'
import { DEFAULT_NODE_SIZE } from '../geometry'
import { canWire } from '../catalog'
import {
  connectionProblemIgnoring,
  canSpliceNode,
  reconnectWire,
  spliceIntoWire,
  spliceProblem,
  wireProblemText,
  WireOpError,
} from '../operations/wires'
import { connectedPortsByNode, pointOnWire, portFraction, portIndex, portsOf, removeWiresWithTerms } from '../streamLabels'

/** How long a refused wire shows red. */
export const INVALID_FLASH_MS = 150
/** How long a dragged node rests over a wire before the wire goes hot. */
export const SPLICE_HOT_MS = 150
/** How close (flow units) a dragged node's center must be to a wire. */
export const SPLICE_DISTANCE = 12
/** Points sampled along each wire for the splice hit test. */
export const WIRE_SAMPLES = 24
/** Node size used before React Flow has measured a node. */
// One node-size constant for the store, mapping and plugins (geometry.ts, EA-13).
export { DEFAULT_NODE_SIZE }

// ── Geometry ────────────────────────────────────────────────────────────────

type InternalNodeLike = {
  measured?: { width?: number; height?: number }
  width?: number
  height?: number
  internals?: {
    positionAbsolute?: XY
    handleBounds?: {
      source?: Array<{ id?: string | null; x: number; y: number; width: number; height: number }> | null
      target?: Array<{ id?: string | null; x: number; y: number; width: number; height: number }> | null
    } | null
  }
}

function sizeOf(n: InternalNodeLike | undefined): { w: number; h: number } {
  return {
    w: n?.measured?.width ?? n?.width ?? DEFAULT_NODE_SIZE.w,
    h: n?.measured?.height ?? n?.height ?? DEFAULT_NODE_SIZE.h,
  }
}

function internalNode(ctx: Pick<CanvasCtx, 'rf'>, id: string): InternalNodeLike | undefined {
  try {
    return ctx.rf.getInternalNode(id) as InternalNodeLike | undefined
  } catch {
    return undefined
  }
}

/**
 * The two end points of a wire in flow units: the bottom middle of the
 * source's output port and the top middle of the target's input port. Uses
 * React Flow's measured handles; before they are measured, the same places
 * worked out from the node box and the port's slot along the top edge.
 */
export function wireEnds(ctx: Pick<CanvasCtx, 'rf'>, wire: GraphWire, graph: Pick<Graph, 'nodes' | 'wires'>): { s: XY; t: XY } | null {
  const from = graph.nodes[wire.from]
  const to = graph.nodes[wire.to]
  if (!from || !to) return null
  const fi = internalNode(ctx, wire.from)
  const ti = internalNode(ctx, wire.to)
  const fPos = fi?.internals?.positionAbsolute ?? { x: from.position[0], y: from.position[1] }
  const tPos = ti?.internals?.positionAbsolute ?? { x: to.position[0], y: to.position[1] }

  let s: XY
  const out = fi?.internals?.handleBounds?.source?.find(h => h.id === 'out') ?? fi?.internals?.handleBounds?.source?.[0]
  if (out) s = { x: fPos.x + out.x + out.width / 2, y: fPos.y + out.y + out.height }
  else {
    const { w, h } = sizeOf(fi)
    s = { x: fPos.x + w / 2, y: fPos.y + h }
  }

  let t: XY
  const inp = ti?.internals?.handleBounds?.target?.find(h => h.id === wire.to_port)
  if (inp) t = { x: tPos.x + inp.x + inp.width / 2, y: tPos.y + inp.y }
  else {
    const { w } = sizeOf(ti)
    const ports = portsOf(to.type, connectedPortsByNode(graph.wires).get(wire.to) ?? [])
    const k = Math.max(0, portIndex(wire.to_port))
    t = { x: tPos.x + w * portFraction(k, Math.max(ports.length, k + 1)), y: tPos.y }
  }
  return { s, t }
}

/** `WIRE_SAMPLES` points along the wire's curve, end to end. */
export function sampleWire(s: XY, t: XY, count = WIRE_SAMPLES): XY[] {
  const pts: XY[] = []
  for (let i = 0; i < count; i++) pts.push(pointOnWire(s.x, s.y, t.x, t.y, i / (count - 1)))
  return pts
}

/** Shortest distance from `p` to the line through the sample points. */
export function distanceToSamples(pts: readonly XY[], p: XY): number {
  let best = Infinity
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len2 = dx * dx + dy * dy
    const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2))
    const d = Math.hypot(a.x + u * dx - p.x, a.y + u * dy - p.y)
    if (d < best) best = d
  }
  return best
}

interface SampledWire {
  id: string
  pts: XY[]
  minX: number
  minY: number
  maxX: number
  maxY: number
}

/** The id of the wire nearest to `p` within `maxDist`, or null. */
export function nearestWire(wires: readonly SampledWire[], p: XY, maxDist = SPLICE_DISTANCE): string | null {
  let best: string | null = null
  let bestD = maxDist
  for (const w of wires) {
    if (p.x < w.minX - maxDist || p.x > w.maxX + maxDist || p.y < w.minY - maxDist || p.y > w.maxY + maxDist) continue
    const d = distanceToSamples(w.pts, p)
    if (d <= bestD) {
      bestD = d
      best = w.id
    }
  }
  return best
}

function sampled(id: string, pts: XY[]): SampledWire {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of pts) {
    if (p.x < minX) minX = p.x
    if (p.y < minY) minY = p.y
    if (p.x > maxX) maxX = p.x
    if (p.y > maxY) maxY = p.y
  }
  return { id, pts, minX, minY, maxX, maxY }
}

/** The visible part of the canvas in flow units, or null when it has no size (tests). */
function visibleFlowRect(ctx: CanvasCtx): { minX: number; minY: number; maxX: number; maxY: number } | null {
  const el = ctx.container()
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (r.width === 0 || r.height === 0) return null
  const a = ctx.rf.screenToFlowPosition({ x: r.left, y: r.top })
  const b = ctx.rf.screenToFlowPosition({ x: r.right, y: r.bottom })
  return { minX: a.x, minY: a.y, maxX: b.x, maxY: b.y }
}

function nodeCenter(node: RFNode): XY {
  const w = node.measured?.width ?? node.width ?? DEFAULT_NODE_SIZE.w
  const h = node.measured?.height ?? node.height ?? DEFAULT_NODE_SIZE.h
  return { x: node.position.x + w / 2, y: node.position.y + h / 2 }
}

/** The middle of a wire in flow units (falls back to the middle between the two nodes). */
export function wireMidpoint(ctx: Pick<CanvasCtx, 'rf'>, wire: GraphWire, graph: Pick<Graph, 'nodes' | 'wires'>): XY | null {
  const ends = wireEnds(ctx, wire, graph)
  if (!ends) return null
  return pointOnWire(ends.s.x, ends.s.y, ends.t.x, ends.t.y, 0.5)
}

// ── Wire visual states ──────────────────────────────────────────────────────

function setEdgeData(ctx: CanvasCtx, id: string, patch: Record<string, unknown>): void {
  try {
    ctx.rf.updateEdgeData(id, patch)
  } catch {
    // The wire is gone (deleted, or the canvas unmounted): nothing to show.
  }
}

/** Show a wire red for `INVALID_FLASH_MS`, then run `after` (if given). */
function flashWire(ctx: CanvasCtx, id: string, after?: () => void): void {
  setEdgeData(ctx, id, { flash: true })
  setTimeout(() => {
    setEdgeData(ctx, id, { flash: false })
    after?.()
  }, INVALID_FLASH_MS)
}

function flash(ctx: Pick<CanvasCtx, 'store'>, text: string): void {
  ctx.store.getState().showFlash(text)
}

// ── Reconnect ───────────────────────────────────────────────────────────────

interface ReconnectDrag {
  wireId: string
  /** Esc was pressed: ignore the drop. */
  cancelled: boolean
  /** A drop on a port already ran (React Flow calls onReconnect before onReconnectEnd). */
  dropped: boolean
  cleanup: () => void
}

let reconnect: ReconnectDrag | null = null

/**
 * The wire whose end is being dragged right now, or null. Connection checks
 * during that drag must ignore it (its own port is not "full"):
 * `connectionProblemIgnoring(graph, c, reconnectingWireId())`.
 */
export function reconnectingWireId(): string | null {
  return reconnect?.wireId ?? null
}

function endReconnect(): void {
  const r = reconnect
  reconnect = null
  r?.cleanup()
}

/**
 * Esc during a wire-end drag: React Flow has no cancel for it, so the drag
 * is ended by a release far off the canvas (no port is near it, so nothing
 * connects), and the drop is ignored.
 */
function cancelReconnectDrag(): void {
  const off = { clientX: -100000, clientY: -100000, bubbles: true }
  document.dispatchEvent(new MouseEvent('mousemove', off))
  document.dispatchEvent(new MouseEvent('mouseup', off))
}

function startReconnect(wireId: string): void {
  endReconnect()
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || !reconnect || reconnect.wireId !== wireId) return
    e.preventDefault()
    e.stopPropagation()
    reconnect.cancelled = true
    cancelReconnectDrag()
  }
  // A press that never became a drag gets no onReconnectEnd: forget the drag
  // on the release, after React Flow's own release handler has run.
  const onUp = () => {
    setTimeout(() => {
      if (reconnect?.wireId === wireId) endReconnect()
    }, 0)
  }
  window.addEventListener('keydown', onKey, true)
  window.addEventListener('mouseup', onUp)
  window.addEventListener('touchend', onUp)
  reconnect = {
    wireId,
    cancelled: false,
    dropped: false,
    cleanup: () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('touchend', onUp)
    },
  }
}

function isPane(target: EventTarget | null): boolean {
  // The empty inside of a box or note counts as empty canvas too (UX-02).
  return isEmptyCanvasTarget(target)
}

/** Delete a wire after its red flash (a wire end dropped on empty canvas). */
function deleteAfterFlash(ctx: CanvasCtx, wireId: string): void {
  // The delete belongs to the history state the drop happened in (FC-6): an
  // undo, redo or any other commit during the flash means the user moved on,
  // and committing now would clear the redo stack under them.
  const seq = ctx.store.getState().commitSeq
  flashWire(ctx, wireId, () => {
    const s = ctx.store.getState()
    if (s.commitSeq !== seq) return
    // The wire went meanwhile (another graph opened): nothing to do.
    if (!s.graph || s.graph.readOnly || !s.graph.wires.some(w => w.id === wireId)) return
    s.commit('delete wire', g => removeWiresWithTerms(g, [wireId]))
  })
}

// ── Splice ──────────────────────────────────────────────────────────────────

interface SpliceDrag {
  nodeId: string
  wires: SampledWire[]
  /** The wire under the node now, and since when (ms). */
  candidate: string | null
  since: number
  /** The wire drawn hot. */
  hot: string | null
  timer: ReturnType<typeof setTimeout> | null
  frame: number | null
  latest: XY | null
}

let splice: SpliceDrag | null = null

function setHot(ctx: CanvasCtx, s: SpliceDrag, id: string | null): void {
  if (s.hot === id) return
  if (s.hot) setEdgeData(ctx, s.hot, { spliceHot: false })
  s.hot = id
  if (id) setEdgeData(ctx, id, { spliceHot: true })
}

function endSplice(ctx: CanvasCtx): void {
  const s = splice
  splice = null
  if (!s) return
  if (s.timer) clearTimeout(s.timer)
  if (s.frame !== null) cancelAnimationFrame(s.frame)
  setHot(ctx, s, null)
}

/** Find the wire under the node's center; start the hot timer when it changes. */
function testSplice(ctx: CanvasCtx, s: SpliceDrag, center: XY): void {
  const id = nearestWire(s.wires, center)
  if (id === s.candidate) return
  s.candidate = id
  s.since = Date.now()
  if (s.timer) clearTimeout(s.timer)
  s.timer = null
  setHot(ctx, s, null)
  if (!id) return
  s.timer = setTimeout(() => {
    if (splice === s && s.candidate === id) setHot(ctx, s, id)
  }, SPLICE_HOT_MS)
}

/** Sample every wire the node could go into, once, at drag start. */
function startSplice(ctx: CanvasCtx, nodeId: string): void {
  const graph = ctx.graph()
  const node = graph.nodes[nodeId]
  if (!node || !canSpliceNode(graph, nodeId)) return
  const view = visibleFlowRect(ctx)
  const wires: SampledWire[] = []
  for (const w of graph.wires) {
    if (w.from === nodeId || w.to === nodeId) continue
    if (!canWire(graph.nodes[w.from]?.type, node.type) || !canWire(node.type, graph.nodes[w.to]?.type)) continue
    const ends = wireEnds(ctx, w, graph)
    if (!ends) continue
    const sw = sampled(w.id, sampleWire(ends.s, ends.t))
    if (view && (sw.maxX < view.minX || sw.minX > view.maxX || sw.maxY < view.minY || sw.minY > view.maxY)) continue
    wires.push(sw)
  }
  if (wires.length === 0) return
  splice = { nodeId, wires, candidate: null, since: 0, hot: null, timer: null, frame: null, latest: null }
}

/** Put the node into the wire it was dropped on. Returns true when it did. */
function finishSplice(e: { metaKey?: boolean; ctrlKey?: boolean }, node: RFNode, ctx: CanvasCtx): boolean {
  const s = splice
  if (!s || s.nodeId !== node.id) {
    endSplice(ctx)
    return false
  }
  // Test the drop point itself (the last drag frame may not have run).
  testSplice(ctx, s, nodeCenter(node))
  const wireId = s.candidate && Date.now() - s.since >= SPLICE_HOT_MS ? s.candidate : null
  endSplice(ctx)
  if (!wireId || e.metaKey || e.ctrlKey) return false
  const store = ctx.store.getState()
  const graph = store.graph
  if (!graph || graph.readOnly) return false
  const problem = spliceProblem(graph, node.id, wireId)
  if (problem) {
    flashWire(ctx, wireId)
    flash(ctx, wireProblemText(problem))
    return false
  }
  const name = graph.nodes[node.id]?.name || node.id
  try {
    store.commit(`splice ${name}`, g => spliceIntoWire(g, node.id, wireId))
  } catch (err) {
    flashWire(ctx, wireId)
    flash(ctx, err instanceof WireOpError ? wireProblemText(err.problem) : 'Could not splice the node')
    return false
  }
  return true
}

// ── Insert node into a wire (Tab menu) ──────────────────────────────────────

/**
 * Open the Tab menu at the middle of a wire; the node picked there is put
 * into the wire, in the same undo step as its creation. Returns false when
 * the menu did not open.
 */
export function openInsertMenu(canvas: CanvasCtx, wireId: string): boolean {
  if (!canvas.editable()) return false
  const graph = canvas.graph()
  const wire = graph.wires.find(w => w.id === wireId)
  if (!wire) return false
  const mid = wireMidpoint(canvas, wire, graph)
  if (!mid) return false
  return canvas.openTabMenu({
    screen: canvas.rf.flowToScreenPosition(mid),
    // The node's middle sits on the wire's middle.
    flow: { x: mid.x - DEFAULT_NODE_SIZE.w / 2, y: mid.y - DEFAULT_NODE_SIZE.h / 2 },
    onCreate(nodeId, ctx) {
      const s = ctx.store.getState()
      const g = s.graph
      if (!g) return
      const problem = spliceProblem(g, nodeId, wireId)
      const name = g.nodes[nodeId]?.name || nodeId
      if (problem) {
        s.showFlash(problem === 'no_port' || problem === 'full'
          ? `${name} has no input and output to put into the wire`
          : wireProblemText(problem))
        return
      }
      s.commit(`splice ${name}`, gr => spliceIntoWire(gr, nodeId, wireId))
    },
  })
}

// ── The plugin ──────────────────────────────────────────────────────────────

export const plugin: CanvasPlugin = {
  id: 'wireOps',

  onReconnectStart(_e, edge, _handleType, ctx) {
    if (!ctx.editable()) return
    startReconnect(edge.id)
  },

  onReconnect(oldEdge: RFEdge, conn: Connection, ctx) {
    const r = reconnect
    if (r && r.wireId === oldEdge.id) {
      r.dropped = true
      if (r.cancelled) return
    }
    if (!ctx.editable()) return
    const s = ctx.store.getState()
    const graph = s.graph
    if (!graph) return
    const problem = connectionProblemIgnoring(graph, conn, oldEdge.id)
    if (problem) {
      flash(ctx, wireProblemText(problem))
      return
    }
    try {
      s.commit('reconnect wire', g => reconnectWire(g, oldEdge.id, conn))
    } catch (err) {
      flash(ctx, err instanceof WireOpError ? wireProblemText(err.problem) : 'Could not move the wire')
    }
  },

  onReconnectEnd(e, edge, _handleType, state: FinalConnectionState, ctx) {
    const r = reconnect
    endReconnect()
    if (!r || r.wireId !== edge.id || r.cancelled || r.dropped) return
    if (!ctx.editable()) return
    // Released near a port (an invalid one, or React Flow would have called
    // onReconnect): the wire snaps back. Released on a node or outside the
    // canvas: snaps back too. Only empty canvas deletes.
    if (state.toHandle || state.isValid) return
    if (!isPane(e.target)) return
    deleteAfterFlash(ctx, edge.id)
  },

  onNodeDragStart(e, node, nodes, ctx) {
    endSplice(ctx)
    // Multi-node drags and Alt-drag duplicates never splice.
    if (!ctx.editable() || nodes.length > 1 || e.altKey) return
    startSplice(ctx, node.id)
  },

  onNodeDrag(_e, node, _nodes, ctx) {
    const s = splice
    if (!s || s.nodeId !== node.id) return
    s.latest = nodeCenter(node)
    // At most one test per animation frame: test now, then once more at the
    // next frame with the latest position.
    if (s.frame !== null) return
    testSplice(ctx, s, s.latest)
    const tested = s.latest
    s.frame = requestAnimationFrame(() => {
      if (splice !== s) return
      s.frame = null
      if (s.latest && s.latest !== tested) testSplice(ctx, s, s.latest)
    })
  },

  onNodeDragStop(e, node, _nodes, ctx) {
    // The move itself is still committed by the canvas, in the same batch.
    finishSplice(e, node, ctx)
  },

  onEdgeDoubleClick(e, edge, ctx) {
    if (!ctx.editable()) return
    e.preventDefault()
    ctx.store.getState().setSelection({ wireIds: [edge.id] })
    openInsertMenu(ctx, edge.id)
  },
}

/** Tests only: drop any drag state left over. */
export function _resetWireOps(): void {
  reconnect?.cleanup()
  reconnect = null
  if (splice?.timer) clearTimeout(splice.timer)
  if (splice?.frame != null) cancelAnimationFrame(splice.frame)
  splice = null
}
