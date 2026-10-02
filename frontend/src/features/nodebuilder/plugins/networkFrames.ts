/**
 * Network frames on the canvas (W5, spec S31).
 *
 * - Loads the frame renderer, so the `nbNetworkFrame` node type exists.
 * - Drag stop: a dragged frame moves with everything inside it (React Flow
 *   moves the children on screen; this commits their absolute positions).
 * - Drag stop: a node dropped over a frame joins that network; a node
 *   dragged fully outside its frame leaves it. Its wires are re-routed
 *   through the frame ports (FA2) or removed, with a toast.
 *
 * Work happens on drop only, never per drag frame (S31 "must not"). The
 * canvas wraps the drag stop in one store batch, so the move and the
 * reparent are ONE undo step. Graphs without networks are left to the
 * canvas's own move commit.
 */

import type { Node as RFNode } from '@xyflow/react'
import type { Graph } from '../../../api/nodebuilder'
import type { CanvasCtx, CanvasPlugin } from '../canvasPlugins'
import { DEFAULT_NODE_SIZE } from '../geometry'
import {
  BOUNDARY_TYPES,
  computeFrameLayouts,
  frameParentOf,
  measuredSizeOf,
  type FrameLayout,
  type NodeSizeOf,
} from '../rfMapping'
import { dragOutToast, frameMoveDeltas, isInside, reparentNodes } from '../networkOps'
// Load the renderer so 'nbNetworkFrame' is registered with the canvas.
import '../nodes/NetworkFrame'

interface Box { x: number; y: number; w: number; h: number }

function contains(f: Box, x: number, y: number): boolean {
  return x >= f.x && x <= f.x + f.w && y >= f.y && y <= f.y + f.h
}

function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

/**
 * Where a dropped node belongs: the smallest frame under its centre that is
 * not the node itself, not inside it, and not being dragged. Null: the root.
 */
export function dropTargetOf(
  nodes: Graph['nodes'],
  layouts: Map<string, FrameLayout>,
  nodeId: string,
  rect: Box,
  dragged: ReadonlySet<string>,
): string | null {
  const cx = rect.x + rect.w / 2
  const cy = rect.y + rect.h / 2
  let best: string | null = null
  let bestArea = Infinity
  for (const [id, l] of layouts) {
    if (dragged.has(id) || isInside(nodes, id, nodeId)) continue
    if (!contains(l, cx, cy)) continue
    const area = l.w * l.h
    if (area < bestArea) {
      best = id
      bestArea = area
    }
  }
  return best
}

/**
 * The parent a dropped node should get, or undefined to keep its parent.
 * Joining a frame needs the centre over it; leaving the current frame needs
 * the node fully outside it (S31 "drag out"), otherwise the frame grows.
 */
export function reparentTargetOf(
  nodes: Graph['nodes'],
  layouts: Map<string, FrameLayout>,
  nodeId: string,
  rect: Box,
  dragged: ReadonlySet<string>,
): string | null | undefined {
  const node = nodes[nodeId]
  if (!node || BOUNDARY_TYPES.has(node.type)) return undefined
  const current = frameParentOf(nodes, node)
  const target = dropTargetOf(nodes, layouts, nodeId, rect, dragged)
  if (target === current) return undefined
  // Leaving the current frame (to its parent level or further out).
  if (current && !(target && isInside(nodes, target, current))) {
    const l = layouts.get(current)
    if (l && overlaps(l, rect)) return undefined
  }
  return target
}

/** Card sizes as React Flow measured them (`node.measured`), for frame layout. */
export function rfMeasuredSizeOf(ctx: Pick<CanvasCtx, 'rf'>): NodeSizeOf {
  return measuredSizeOf(id => {
    try {
      const m = ctx.rf.getNode(id)?.measured
      return m && m.width && m.height ? { w: m.width, h: m.height } : null
    } catch {
      return null
    }
  })
}

function handleDrop(nodes: RFNode[], ctx: CanvasCtx): boolean {
  if (!ctx.editable()) return false
  const graph = ctx.graph()
  // The frames as drawn: measured card sizes, the default until measured (FE-05).
  const layouts = computeFrameLayouts(graph.nodes, rfMeasuredSizeOf(ctx))
  if (layouts.size === 0) return false
  const moved = nodes.filter(n => n.id in graph.nodes)
  if (moved.length === 0) return false
  const moves = moved.map(n => ({ id: n.id, position: ctx.graphPosition(n) }))
  const dragged = new Set(moved.map(n => n.id))

  // Work out reparents before the move commit changes the frames.
  const reparents: Array<{ id: string; target: string | null }> = []
  for (const m of moves) {
    if (layouts.has(m.id)) continue // frames keep their parent in W5
    const rf = moved.find(n => n.id === m.id)
    const w = rf?.measured?.width ?? DEFAULT_NODE_SIZE.w
    const h = rf?.measured?.height ?? DEFAULT_NODE_SIZE.h
    // A node carried along inside a dragged frame stays where it is.
    let carried = false
    for (let p = graph.nodes[m.id]?.parent ?? null, i = 0; p && i < 64; p = graph.nodes[p]?.parent ?? null, i++) {
      if (dragged.has(p)) { carried = true; break }
    }
    if (carried) continue
    const target = reparentTargetOf(graph.nodes, layouts, m.id, { x: m.position[0], y: m.position[1], w, h }, dragged)
    if (target !== undefined) reparents.push({ id: m.id, target })
  }

  const { ids, deltas } = frameMoveDeltas(graph.nodes, moves, layouts)
  const touchesFrame = moves.some(m => layouts.has(m.id))
  // Nothing network-specific happened: let the canvas commit the move.
  if (!touchesFrame && reparents.length === 0) return false

  const store = ctx.store.getState()
  if (ids.length > 0) store.moveNodes(ids, deltas)
  if (reparents.length > 0) {
    // Every reparent in ONE commit (FE-01): a wire between two nodes that
    // move together stays a plain wire, and undo takes the whole drop back.
    const before = ctx.store.getState().graph
    if (before) {
      let removed = 0
      const into = reparents.some(r => r.target !== null)
      ctx.store.getState().commit(into ? 'move into network' : 'move out of network', g => {
        const res = reparentNodes(g, reparents.map(r => ({
          id: r.id,
          parent: r.target,
          position: g.nodes[r.id]?.position ?? [0, 0],
        })).filter(m => m.id in g.nodes))
        removed = res.removedWires
        return res.graph
      })
      if (removed > 0) {
        const first = reparents[0]
        const node = before.nodes[first.id]
        const netId = first.target ?? node?.parent ?? null
        const netName = (netId && before.nodes[netId]?.name) || 'the network'
        const what = reparents.length === 1 ? (node?.name || first.id) : `${reparents.length} nodes`
        ctx.store.getState().showFlash(dragOutToast(what, netName, removed, first.target !== null))
      }
    }
  }
  return true
}

export const plugin: CanvasPlugin = {
  id: 'networkFrames',
  onNodeDragStop(_e, _node, nodes, ctx) {
    return handleDrop(nodes, ctx)
  },
  onSelectionDragStop(_e, nodes, ctx) {
    return handleDrop(nodes, ctx)
  },
}
