/**
 * Inspector keys (foundation 6.2, S14): `P` opens and closes the Inspector
 * (Houdini: P for parameters); `F2` renames the selected node in the
 * Inspector header (also the node menu's Rename row, S19).
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { NodeBuilderState } from '../store'
import { isInspectorShown, requestRename, toggleInspector } from '../inspector/state'
import type { Command } from './index'
import { nodeIsUnsupported } from './unsupported'

/** True when exactly one node of an editable store graph is selected, and it is not unsupported (S13: no rename, UX-14). */
function oneEditableNode(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly && s.selectedNodeIds.length === 1
    && !nodeIsUnsupported(s.graph, s.selectedNodeIds[0])
}

export const commands: Command[] = [
  {
    id: 'panels.inspector',
    label: 'Inspector',
    keys: ['p'],
    scope: 'canvas',
    checked: () => isInspectorShown(),
    run: () => { toggleInspector() },
  },
  {
    id: 'edit.rename',
    label: 'Rename',
    keys: ['f2'],
    scope: 'canvas',
    menu: 'node',
    when: oneEditableNode,
    disabledReason: s => (oneEditableNode(s) ? null : 'Select one node to rename'),
    run: ({ store }) => {
      const id = store.getState().selectedNodeIds[0]
      if (!id) return false
      requestRename(id)
    },
  },
]
