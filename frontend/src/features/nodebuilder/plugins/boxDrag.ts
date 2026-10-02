/**
 * Network boxes and sticky notes on the canvas (item 3.E, specs S17, S18).
 *
 * - Registers the node source that turns `graph.annotations` into React
 *   Flow nodes (`nbBox` at zIndex -2, `nbNote` at -1, both under wires).
 * - Dragging a box moves its members live and commits the box and member
 *   moves on drop. Dragging a note commits its new place.
 * - Dropping graph nodes works out box membership again for them. While a
 *   node is dragged, the box it would join shows a solid border.
 * - Delete removes selected boxes and notes (their nodes stay).
 *
 * The canvas wraps every drag stop and Delete in one store batch, so the
 * commits made here and the canvas's own node move are ONE undo step.
 * Membership is never worked out per drag frame, only on drop (S17).
 */

import type { MouseEvent as ReactMouseEvent } from 'react'
import type { Node as RFNode } from '@xyflow/react'
import type { Graph } from '../../../api/nodebuilder'
import type { CanvasCtx, CanvasPlugin } from '../canvasPlugins'
import { registerRfNodeSource } from '../rfMapping'
import {
  annotationsOf,
  nodeCenter,
  opMoveAnnotations,
  opRemoveAnnotations,
  recomputeMembership,
  rfSizeOf,
  smallestBoxAt,
  type XY,
} from '../store/annotations'
import { setHotBox } from '../nodes/annotationUi'
import type { BoxNodeData } from '../nodes/NetworkBox'
import type { NoteNodeData } from '../nodes/StickyNote'
// Load the renderers so 'nbBox' and 'nbNote' are registered with the canvas.
import '../nodes/NetworkBox'
import '../nodes/StickyNote'

// ---------------------------------------------------------------------------
// Node source: graph.annotations → React Flow nodes
// ---------------------------------------------------------------------------

/**
 * Builds the box and note nodes, reusing the same object for an annotation
 * whose stored object, member count and editability did not change (the
 * store keeps unchanged annotations as the same objects).
 */
export function createAnnotationNodeMapper(): (graph: Graph, editable: boolean, networkId?: string | null) => RFNode[] {
  const cache = new Map<string, { src: unknown; sig: string; node: RFNode }>()
  return (graph, editable, networkId = null) => {
    // Only the network on screen (EA-4): a box or note inside another
    // network is drawn when the user goes there.
    const all = annotationsOf(graph)
    const here = <T extends { parent?: string | null }>(a: T) => (a.parent ?? null) === networkId
    const boxes = all.boxes.filter(here)
    const notes = all.notes.filter(here)
    const out: RFNode[] = []
    const seen = new Set<string>()
    for (const box of boxes) {
      const memberCount = box.members.filter(m => m in graph.nodes).length
      const sig = `${memberCount}|${editable ? 1 : 0}`
      seen.add(box.id)
      const hit = cache.get(box.id)
      if (hit && hit.src === box && hit.sig === sig) { out.push(hit.node); continue }
      const data: BoxNodeData = { box, memberCount, editable }
      const node: RFNode = {
        id: box.id,
        type: 'nbBox',
        position: { x: box.rect[0], y: box.rect[1] },
        width: box.rect[2],
        height: box.rect[3],
        zIndex: -2,
        draggable: editable,
        selectable: true,
        connectable: false,
        data,
      }
      cache.set(box.id, { src: box, sig, node })
      out.push(node)
    }
    for (const note of notes) {
      const sig = editable ? '1' : '0'
      seen.add(note.id)
      const hit = cache.get(note.id)
      if (hit && hit.src === note && hit.sig === sig) { out.push(hit.node); continue }
      const data: NoteNodeData = { note, editable }
      const node: RFNode = {
        id: note.id,
        type: 'nbNote',
        position: { x: note.rect[0], y: note.rect[1] },
        width: note.rect[2],
        height: note.rect[3],
        zIndex: -1,
        draggable: editable,
        selectable: true,
        connectable: false,
        data,
      }
      cache.set(note.id, { src: note, sig, node })
      out.push(node)
    }
    for (const id of Array.from(cache.keys())) if (!seen.has(id)) cache.delete(id)
    return out
  }
}

const mapAnnotations = createAnnotationNodeMapper()
registerRfNodeSource({
  id: 'annotations',
  order: 'before',
  nodes: (graph, { editable, networkId }) => mapAnnotations(graph, editable, networkId ?? null),
})

// ---------------------------------------------------------------------------
// Drag
// ---------------------------------------------------------------------------

interface DragState {
  /** Start position of each dragged box. */
  boxStart: Map<string, XY>
  /** Members that follow a dragged box (not dragged themselves): start position and the box they follow. */
  followers: Map<string, { start: XY; box: string }>
}

let drag: DragState | null = null

/** A React Flow node's graph (absolute) position (EA-1). */
function graphPos(ctx: CanvasCtx, n: RFNode): [number, number] {
  return ctx.graphPosition ? ctx.graphPosition(n) : [n.position.x, n.position.y]
}

// One live update per animation frame while a box is dragged (IP-6): the
// latest drag event wins, the rest of the frame's events only replace it.
let pending: { node: RFNode | null; nodes: readonly RFNode[]; ctx: CanvasCtx } | null = null
let frame = 0

function scheduleLive(node: RFNode | null, nodes: readonly RFNode[], ctx: CanvasCtx): void {
  pending = { node, nodes, ctx }
  if (frame) return
  frame = requestAnimationFrame(flushLive)
}

/** Apply the pending live update now (the next frame, or a test). */
export function flushLive(): void {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
  const p = pending
  pending = null
  if (!p) return
  moveFollowers(p.nodes, p.ctx)
  markHotBox(p.node, p.nodes, p.ctx)
}

function cancelLive(): void {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
  pending = null
}

function isBox(graph: Graph, id: string): boolean {
  return annotationsOf(graph).boxes.some(b => b.id === id)
}

function startDrag(nodes: readonly RFNode[], ctx: CanvasCtx): void {
  cancelLive()
  // ctx.graph() is the store graph while editing (EA-10), so an Alt-drag
  // that swapped ids earlier in this same drag start is already in it (FC-3).
  const graph = ctx.graph()
  const dragged = new Set(nodes.map(n => n.id))
  const boxStart = new Map<string, XY>()
  const followers = new Map<string, { start: XY; box: string }>()
  for (const b of annotationsOf(graph).boxes) {
    if (!dragged.has(b.id)) continue
    boxStart.set(b.id, { x: b.rect[0], y: b.rect[1] })
    for (const m of b.members) {
      const n = graph.nodes[m]
      if (!n || dragged.has(m) || followers.has(m)) continue
      // The follower's own React Flow position (relative to its parent for a
      // W5 child): a box's delta applies to it the same way.
      const rf = ctx.rf.getNode?.(m)
      const start = rf ? { x: rf.position.x, y: rf.position.y } : { x: n.position[0], y: n.position[1] }
      followers.set(m, { start, box: b.id })
    }
  }
  drag = boxStart.size > 0 ? { boxStart, followers } : null
}

/** Move a dragged box's members on screen by the box's delta (no commit). */
function moveFollowers(nodes: readonly RFNode[], ctx: CanvasCtx): void {
  if (!drag || drag.followers.size === 0) return
  const delta = new Map<string, XY>()
  for (const n of nodes) {
    const s = drag.boxStart.get(n.id)
    if (s) delta.set(n.id, { x: n.position.x - s.x, y: n.position.y - s.y })
  }
  const { followers } = drag
  ctx.rf.setNodes(curr => curr.map(n => {
    const f = followers.get(n.id)
    const d = f && delta.get(f.box)
    if (!f || !d) return n
    const x = f.start.x + d.x
    const y = f.start.y + d.y
    return n.position.x === x && n.position.y === y ? n : { ...n, position: { x, y } }
  }))
}

/** Show the "will join" border on the box under the dragged node's center. */
function markHotBox(node: RFNode | null, nodes: readonly RFNode[], ctx: CanvasCtx): void {
  const graph = ctx.graph()
  const boxes = annotationsOf(graph).boxes
  const primary = node && node.id in graph.nodes ? node : nodes.find(n => n.id in graph.nodes) ?? null
  if (!primary || boxes.length === 0) { setHotBox(null); return }
  const dragged = new Set(nodes.map(n => n.id))
  const size = rfSizeOf(ctx.rf)(primary.id)
  const center = nodeCenter(graphPos(ctx, primary), size)
  const parent = graph.nodes[primary.id].parent ?? null
  const inNetwork = boxes.filter(b => (b.parent ?? null) === parent)
  setHotBox(smallestBoxAt(inNetwork, center, dragged)?.id ?? null)
}

/**
 * Commit on drop: dragged boxes and notes take their new places (a box
 * brings its members), then dropped graph nodes get their membership worked
 * out again. Returns nothing, so the canvas still saves the graph nodes'
 * own moves in the same batch.
 */
function stopDrag(node: RFNode | null, nodes: readonly RFNode[], ctx: CanvasCtx): void {
  // The commit below puts everything in place; a frame still pending would
  // only repeat an older position.
  cancelLive()
  setHotBox(null)
  drag = null
  if (!ctx.editable()) return
  const store = ctx.store.getState()
  const graph = store.graph
  if (!graph) return
  const all = node && !nodes.some(n => n.id === node.id) ? [...nodes, node] : nodes
  const { boxes, notes } = annotationsOf(graph)
  if (boxes.length === 0 && notes.length === 0) return
  const annIds = new Set([...boxes.map(b => b.id), ...notes.map(n => n.id)])
  const moves = new Map<string, XY>()
  const graphMoves = new Map<string, [number, number]>()
  for (const n of all) {
    const [x, y] = graphPos(ctx, n)
    if (annIds.has(n.id)) moves.set(n.id, { x, y })
    else if (n.id in graph.nodes) graphMoves.set(n.id, [x, y])
  }
  const label = [...moves.keys()].some(id => isBox(graph, id))
    ? 'move network box'
    : moves.size > 0 ? 'move note' : 'move node'
  const sizeOf = rfSizeOf(ctx.rf)
  store.commit(label, g => {
    let next = opMoveAnnotations(g, moves, new Set(graphMoves.keys()))
    if (graphMoves.size > 0) {
      next = recomputeMembership(next, [...graphMoves.keys()], sizeOf, id => graphMoves.get(id) ?? null)
    }
    return next
  })
}

export const plugin: CanvasPlugin = {
  id: 'boxDrag',

  onNodeDragStart(_e: ReactMouseEvent, node, nodes, ctx) {
    startDrag(nodes.length ? nodes : [node], ctx)
  },
  onNodeDrag(_e, node, nodes, ctx) {
    scheduleLive(node, nodes.length ? nodes : [node], ctx)
  },
  onNodeDragStop(_e, node, nodes, ctx) {
    stopDrag(node, nodes, ctx)
  },

  onSelectionDragStart(_e, nodes, ctx) {
    startDrag(nodes, ctx)
  },
  onSelectionDrag(_e, nodes, ctx) {
    scheduleLive(null, nodes, ctx)
  },
  onSelectionDragStop(_e, nodes, ctx) {
    stopDrag(null, nodes, ctx)
  },

  onDeleteSelection(req, ctx) {
    if (!ctx.editable() || req.otherIds.length === 0) return
    const s = ctx.store.getState()
    const graph = s.graph
    if (!graph) return
    const { boxes, notes } = annotationsOf(graph)
    const known = new Set([...boxes.map(b => b.id), ...notes.map(n => n.id)])
    const ids = req.otherIds.filter(id => known.has(id))
    if (ids.length === 0) return
    s.commit(ids.length === 1 ? 'delete annotation' : 'delete annotations', g => opRemoveAnnotations(g, ids))
    return true
  },
}
