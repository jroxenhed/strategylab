/**
 * Wire commands (item 3.G, specs S19, S23).
 *
 * - `wires.insertNode` (Tab with one wire selected, wire menu): the Tab menu
 *   opens at the wire's middle and the picked node is spliced into it.
 * - `wires.delete` (Delete / Backspace, wire menu): deletes the selected
 *   wires, through the canvas's own delete (one undo step).
 * - `wires.deleteRewire` (Delete / Backspace on ONE node with one wire in
 *   and one or more out; the node menu's Delete row): deletes the node and
 *   connects its source to every old target (the Houdini rule), then says
 *   so in the status bar: `Deleted rsi and reconnected 2 wires`.
 * - `edit.contextMenu` (Shift+F10, the ContextMenu key): opens the
 *   right-click menu for the primary selected node at its center, the
 *   selected wire at its middle, or the pane at the pointer.
 *
 * These are newer than edit.ts's Delete and Tab (file order), so they get
 * the key first; while their `when` is false the key falls to edit.ts.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { NodeBuilderState } from '../store'
import type { Command } from './index'
import { openContextMenu } from '../contextMenuModel'
import { DEFAULT_NODE_SIZE, openInsertMenu, wireMidpoint } from '../plugins/wireOps'
import { rewireCandidate, rewiredCount, rewireFlash } from '../operations/wires'

function editing(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly
}

/** Exactly one wire selected and nothing else. */
function oneWire(s: NodeBuilderState): boolean {
  return editing(s) && s.selectedWireIds.length === 1 && s.selectedNodeIds.length === 0
    && s.selectedAnnotationIds.length === 0
}

/** The one selected node a plain Delete removes with a rewire, or null. */
function rewireNode(s: NodeBuilderState): string | null {
  if (!editing(s) || s.selectedWireIds.length > 0 || s.selectedAnnotationIds.length > 0) return null
  return rewireCandidate(s.graph!, s.selectedNodeIds)
}

export const commands: Command[] = [
  {
    id: 'wires.insertNode',
    label: 'Insert node…',
    keys: ['tab'],
    scope: 'canvas',
    menu: 'wire',
    when: oneWire,
    disabledReason: s => (oneWire(s) ? null : 'Select one wire first'),
    run: ({ canvas, store }) => {
      const id = store.getState().selectedWireIds[0]
      return canvas && id ? openInsertMenu(canvas, id) : false
    },
  },
  {
    id: 'wires.delete',
    label: 'Delete wire',
    keys: ['delete', 'backspace'],
    scope: 'canvas',
    menu: 'wire',
    when: s => editing(s) && s.selectedWireIds.length > 0,
    disabledReason: s => (s.selectedWireIds.length > 0 ? null : 'Select a wire first'),
    run: ({ canvas }) => (canvas ? canvas.deleteSelection({ rewire: true }) : false),
  },
  {
    id: 'wires.deleteRewire',
    label: 'Delete and reconnect',
    keys: ['delete', 'backspace'],
    scope: 'canvas',
    menu: 'node',
    when: s => rewireNode(s) != null,
    disabledReason: s => (rewireNode(s) ? null : 'No wires to reconnect'),
    run: ({ canvas, store }) => {
      const s = store.getState()
      const id = rewireNode(s)
      if (!canvas || !id || !s.graph) return false
      const name = s.graph.nodes[id]?.name || id
      const count = rewiredCount(s.graph, [id])
      if (!canvas.deleteSelection({ rewire: true })) return false
      if (count > 0) store.getState().showFlash(rewireFlash(name, count))
      return true
    },
  },
  {
    id: 'edit.contextMenu',
    label: 'Open the right-click menu',
    keys: ['shift+f10', 'contextmenu'],
    scope: 'canvas',
    run: ({ canvas, store }) => {
      if (!canvas) return false
      const s = store.getState()
      const graph = canvas.graph()
      const primary = s.selectedNodeId && graph.nodes[s.selectedNodeId] ? s.selectedNodeId : null
      if (primary) {
        const n = canvas.rf.getInternalNode(primary)
        const at = n?.internals.positionAbsolute ?? { x: graph.nodes[primary].position[0], y: graph.nodes[primary].position[1] }
        const flow = {
          x: at.x + (n?.measured?.width ?? DEFAULT_NODE_SIZE.w) / 2,
          y: at.y + (n?.measured?.height ?? DEFAULT_NODE_SIZE.h) / 2,
        }
        openContextMenu({ kind: 'node', flow, screen: canvas.rf.flowToScreenPosition(flow), canvas })
        return true
      }
      const wire = s.selectedWireIds.length === 1 ? graph.wires.find(w => w.id === s.selectedWireIds[0]) : undefined
      const mid = wire ? wireMidpoint(canvas, wire, graph) : null
      if (wire && mid) {
        openContextMenu({ kind: 'wire', flow: mid, screen: canvas.rf.flowToScreenPosition(mid), canvas })
        return true
      }
      const flow = canvas.pointer()
      openContextMenu({ kind: 'pane', flow, screen: canvas.rf.flowToScreenPosition(flow), canvas })
      return true
    },
  },
]
