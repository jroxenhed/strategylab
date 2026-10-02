/**
 * Collapse into subnet (spec S39, plan W6 item 6.D): `Shift+C`, the node
 * menu row "Collapse into subnet" (S19) and the
 * multi-selection Inspector's bulk button (found by its key).
 *
 * The menu row is placed by its key (contextMenuModel MENU_LAYOUTS).
 * The graph edit is `operations/collapse.ts`, committed as ONE undo step.
 * Afterwards the new subnet is selected and its rename field opens, so
 * typing names it at once.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import { showAssetToast } from '../assetUi'
import { requestRename } from '../inspector/state'
import { COLLAPSE_TEXT, collapseIntoSubnet, collapseProblem, collapseToast, type CollapseResult } from '../operations/collapse'
import type { NodeBuilderState } from '../store'
import type { Command } from './index'

/** Undo label of a collapse. */
export const COLLAPSE_LABEL = 'collapse into subnet'

function editable(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly
}

export const commands: Command[] = [
  {
    id: 'subnet.collapse',
    label: 'Collapse into subnet',
    keys: ['shift+c'],
    scope: 'canvas',
    // The node menu row `{ key: 'shift+c', slot: 'node.network' }` finds it by its key.
    menu: 'node',
    // Enabled on any editable graph: an empty or wrong selection says why
    // (S39 feedback table) instead of doing nothing.
    when: editable,
    disabledReason: s => (editable(s) ? null : 'Read-only graph'),
    run: ({ store }) => {
      const s = store.getState()
      const graph = s.graph
      if (!graph || graph.readOnly) return false
      const ids = [...s.selectedNodeIds]
      const problem = collapseProblem(graph, ids)
      if (problem === 'nothing') {
        s.showFlash(COLLAPSE_TEXT.nothing)
        return
      }
      if (problem) {
        showAssetToast(COLLAPSE_TEXT[problem])
        return
      }
      let result: CollapseResult | null = null
      s.commit(COLLAPSE_LABEL, g => {
        result = collapseIntoSubnet(g, ids)
        return result.graph
      })
      const done = result as CollapseResult | null
      if (!done) return
      const id = done.subnetId
      store.getState().setSelection({ nodeIds: [id], primary: id })
      const name = store.getState().graph?.nodes[id]?.name ?? 'subnet1'
      showAssetToast(collapseToast(done, name))
      // Houdini: the new folder's name is ready to type over.
      requestRename(id)
    },
  },
]
