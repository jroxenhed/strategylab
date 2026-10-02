/**
 * Undo and redo commands (foundation 6.2): Cmd+Z undoes, Cmd+Shift+Z and
 * Cmd+Y redo. They act on the graph history in the store, not on the
 * browser's own undo, and never fire while typing in a text field (the
 * dispatcher skips those), so a param field keeps its native Cmd+Z.
 *
 * Loaded by the commands/ auto-registry (index.ts), which registers the
 * exported `commands`.
 */

import type { Command } from './index'
import { useNodeBuilderStore } from '../store'

/** True when the store holds an editable graph. */
function editing(): boolean {
  const g = useNodeBuilderStore.getState().graph
  return g != null && !g.readOnly
}

export const commands: Command[] = [
  {
    id: 'history.undo',
    label: 'Undo',
    keys: ['mod+z'],
    scope: 'global',
    enabled: editing,
    run() {
      useNodeBuilderStore.getState().undo()
    },
  },
  {
    id: 'history.redo',
    label: 'Redo',
    keys: ['mod+shift+z', 'mod+y'],
    scope: 'global',
    enabled: editing,
    run() {
      useNodeBuilderStore.getState().redo()
    },
  },
]
