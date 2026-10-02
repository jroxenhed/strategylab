/**
 * Alt-drag duplicate (F435 W3 item 3.C, foundation 6.1): Alt+drag a node
 * duplicates the selection and drags the copy, internal wires kept.
 *
 * On drag start with Alt held, the store turns the grabbed nodes into the
 * copy and moves the originals to new ids where they stand
 * (`duplicateForDrag`, see clipboard.ts swapIdsForDrag). React Flow goes on
 * dragging the same ids, which now hold the copy, so the canvas's normal
 * drop (move, splice onto a wire, box membership) acts on the copy.
 *
 * The duplicate and the drop are ONE undo step: a store batch opens on drag
 * start and closes on drag stop. If the drag stop never reaches the plugins
 * (the canvas unmounted mid-drag), the batch is closed right after the
 * mouse is released, so later edits never fold into it.
 *
 * Loaded by the plugins/ auto-registry (canvasPlugins.ts).
 */

import type { Node as RFNode } from '@xyflow/react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import type { CanvasCtx, CanvasPlugin } from '../canvasPlugins'

/** The batch this plugin opened and has not closed yet, or null. */
let open: { ctx: CanvasCtx; stopListening: () => void } | null = null

/** Close the open batch, once. */
function close(): void {
  if (!open) return
  const { ctx, stopListening } = open
  open = null
  stopListening()
  ctx.store.getState().endBatch()
}

/** Close the batch shortly after the mouse is released, if the drag stop did not. */
function closeAfterRelease(): () => void {
  if (typeof window === 'undefined') return () => {}
  const events = ['mouseup', 'pointerup', 'touchend', 'pointercancel'] as const
  let timer: ReturnType<typeof setTimeout> | null = null
  const onUp = () => {
    if (timer === null) timer = setTimeout(close, 0)
  }
  for (const e of events) window.addEventListener(e, onUp, true)
  return () => {
    for (const e of events) window.removeEventListener(e, onUp, true)
    if (timer !== null) clearTimeout(timer)
  }
}

/** Start an Alt-drag duplicate of the grabbed graph nodes. */
function start(e: ReactMouseEvent | MouseEvent, nodes: RFNode[], ctx: CanvasCtx): void {
  if (!e.altKey || !ctx.editable()) return
  const s = ctx.store.getState()
  if (!s.graph || s.graph.readOnly) return
  close() // a batch left open by an earlier drag
  // Boxes and notes in the drag are not graph nodes: they just move.
  const grabbed = nodes.filter(n => n.id in s.graph!.nodes)
  if (grabbed.length === 0) return
  // Graph (absolute) positions (EA-1).
  const positions = new Map<string, [number, number]>(
    grabbed.map(n => [n.id, ctx.graphPosition ? ctx.graphPosition(n) : [n.position.x, n.position.y]]),
  )
  s.beginBatch('duplicate')
  open = { ctx, stopListening: closeAfterRelease() }
  const copies = s.duplicateForDrag(grabbed.map(n => n.id), positions)
  if (!copies) close()
}

export const plugin: CanvasPlugin = {
  id: 'altDragDuplicate',
  onNodeDragStart(e, _node, nodes, ctx) {
    start(e, nodes, ctx)
  },
  onSelectionDragStart(e, nodes, ctx) {
    start(e, nodes, ctx)
  },
  // The canvas's own batch for the drop is open around these, so the move
  // still joins the duplicate's undo step after this close.
  onNodeDragStop() {
    close()
  },
  onSelectionDragStop() {
    close()
  },
}

/** Tests only: whether an Alt-drag batch is open. */
export function _altDragOpen(): boolean {
  return open !== null
}
