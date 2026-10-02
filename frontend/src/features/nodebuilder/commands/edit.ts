/**
 * The canvas's own editing commands (foundation 6.2): Tab opens the Tab
 * menu, Delete and Backspace delete the selection (reconnecting around a
 * deleted node, the Houdini rule), Shift+Delete deletes without
 * reconnecting.
 *
 * They act through the mounted canvas (`ctx.canvas`), which holds the Tab
 * menu and React Flow's selection. With no canvas, or no editable graph,
 * they do nothing and the key keeps its browser behavior.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { NodeBuilderState } from '../store'
import type { Command } from './index'

/** True when the store holds an editable graph. */
function editing(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly
}

export const commands: Command[] = [
  {
    id: 'edit.addNode',
    label: 'Add node…',
    keys: ['tab'],
    scope: 'canvas',
    menu: 'pane',
    when: editing,
    // Returns false (not handled) when focus is on a toolbar button, so Tab
    // keeps moving focus there.
    run: ({ canvas, event }) => (canvas ? canvas.openTabMenu({ keyEvent: event }) : false),
  },
  {
    id: 'edit.delete',
    label: 'Delete',
    keys: ['delete', 'backspace'],
    scope: 'canvas',
    menu: 'node',
    when: editing,
    run: ({ canvas }) => (canvas ? canvas.deleteSelection({ rewire: true }) : false),
  },
  {
    id: 'edit.deleteNoRewire',
    label: 'Delete without rewiring',
    keys: ['shift+delete', 'shift+backspace'],
    scope: 'canvas',
    menu: 'node',
    when: editing,
    run: ({ canvas }) => (canvas ? canvas.deleteSelection({ rewire: false }) : false),
  },
]
