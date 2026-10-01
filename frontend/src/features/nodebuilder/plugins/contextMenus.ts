/**
 * Right-click menus on the canvas (item 3.G, spec S19).
 *
 * A right-click on a node, a wire, a network box, a sticky note or the
 * empty pane opens ContextMenu.tsx at the pointer with that menu's rows.
 * Before it opens, the selection follows the Houdini rules:
 * - a node (or box, note) that is not selected becomes the only selection;
 *   a selected one keeps the selection, so bulk rows act on all of it;
 * - a wire becomes the only selection;
 * - the pane keeps the selection (Paste goes to the pointer).
 *
 * The browser's own menu never shows on the canvas (each hook calls
 * preventDefault), in the read-only view too; there the menus keep only
 * the rows that do not edit.
 *
 * The menu itself is drawn by `ContextMenuHost` in the builder's
 * `overlays` slot, registered here.
 *
 * Loaded by the plugins/ auto-loader (canvasPlugins.ts).
 */

import type { Node as RFNode } from '@xyflow/react'
import type { CanvasCtx, CanvasPlugin } from '../canvasPlugins'
import { isTypingTarget } from '../canvasHelpers'
import type { CommandMenu } from '../commands'
import { ContextMenuHost } from '../ContextMenu'
import { openContextMenu } from '../contextMenuModel'
import { registerSlot } from '../slots'

registerSlot('overlays', 'contextMenu', ContextMenuHost, 50)

/** Which menu a right-clicked canvas node gets, or null for none. */
export function menuKindOf(node: Pick<RFNode, 'id' | 'type'>, ctx: Pick<CanvasCtx, 'graph'>): CommandMenu | null {
  if (node.id in ctx.graph().nodes) return 'node'
  if (node.type === 'nbBox') return 'box'
  if (node.type === 'nbNote') return 'note'
  return null
}

function open(kind: CommandMenu, e: { clientX: number; clientY: number }, ctx: CanvasCtx): void {
  const screen = { x: e.clientX, y: e.clientY }
  openContextMenu({ kind, screen, flow: ctx.rf.screenToFlowPosition(screen), canvas: ctx })
}

export const plugin: CanvasPlugin = {
  id: 'contextMenus',

  onNodeContextMenu(e, node, ctx) {
    // A text field on the card (a param value, a note being edited) keeps
    // the browser's own menu: copy, paste, spelling (S19).
    if (isTypingTarget(e.target)) return
    e.preventDefault()
    const kind = menuKindOf(node, ctx)
    if (!kind) return
    const store = ctx.store.getState()
    if (kind === 'node') {
      // The clicked node is the menu's subject (UX-04): a selected node keeps
      // the selection (bulk rows act on all of it) but becomes the primary,
      // so Display and the Bypass checkmark are about this node.
      if (!store.selectedNodeIds.includes(node.id)) store.setSelection({ nodeIds: [node.id], primary: node.id })
      else if (store.selectedNodeId !== node.id) {
        store.setSelection({
          nodeIds: store.selectedNodeIds,
          wireIds: store.selectedWireIds,
          annotationIds: store.selectedAnnotationIds,
          primary: node.id,
        })
      }
    } else if (!store.selectedAnnotationIds.includes(node.id)) {
      store.setSelection({ annotationIds: [node.id] })
    }
    open(kind, e, ctx)
  },

  onEdgeContextMenu(e, edge, ctx) {
    e.preventDefault()
    ctx.store.getState().setSelection({ wireIds: [edge.id] })
    open('wire', e, ctx)
  },

  onPaneContextMenu(e, ctx) {
    e.preventDefault()
    open('pane', e, ctx)
  },
}
