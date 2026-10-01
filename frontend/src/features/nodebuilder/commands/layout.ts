/**
 * Tidy layout command (W3 item 3.F, ui-ux-spec 6.2): L lays out the selected
 * nodes, or every node when none is selected, top-down with elkjs
 * (layout.ts). The move is one commit, so one undo step.
 *
 * The layout is async (elk loads on first use), so the command commits when
 * it is done, and only if the graph did not change in the meantime.
 *
 * Loaded by the commands/ auto-registry (index.ts). The pane context menu
 * and the Inspector's bulk "Tidy (L)" button run it by id: 'layout.tidy'.
 */

import type { GraphNode } from '../../../api/nodebuilder'
import type { CanvasCtx } from '../canvasPlugins'
import { tidyPositions, applyPositions, type SizeOf } from '../layout'
import type { NodeBuilderState } from '../store'
import { opRefitBoxesOf } from '../store/annotations'
import { rfSizeOf } from '../geometry'
import { runCommand, type Command } from './index'

/** True when the store holds an editable graph with at least one node. */
function canTidy(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly && Object.keys(s.graph.nodes).length > 0
}

/** Node sizes as React Flow measured them; unmeasured nodes get an estimate. */
function measuredSizes(canvas: CanvasCtx | null): SizeOf | undefined {
  if (!canvas) return undefined
  return (n: GraphNode) => {
    const m = canvas.rf.getNode(n.id)?.measured
    return m?.width && m?.height ? { width: m.width, height: m.height } : null
  }
}

export const commands: Command[] = [
  {
    id: 'layout.tidy',
    label: 'Tidy layout',
    keys: ['l'],
    scope: 'canvas',
    menu: 'pane',
    when: canTidy,
    disabledReason: s => (canTidy(s) ? null : 'Nothing to tidy'),
    run({ canvas, store }) {
      const s = store.getState()
      const graph = s.graph
      if (!graph || graph.readOnly) return false
      const ids = s.selectedNodeIds.filter(id => id in graph.nodes)
      if (ids.length === 1) {
        s.showFlash('Select two or more nodes to tidy, or none to tidy all')
        return true
      }
      const all = ids.length === 0
      tidyPositions(graph, ids, measuredSizes(canvas))
        .then(positions => {
          const now = store.getState()
          // The user edited (or loaded another graph) while elk ran: moving
          // nodes now would undo their edit, so leave it.
          if (now.graph !== graph) {
            now.showFlash('The graph changed while tidying. Press L again.')
            return
          }
          const label = all ? 'tidy layout' : `tidy ${ids.length} nodes`
          // Boxes around moved nodes wrap them again, in the same step (FC-5).
          const sizeOf = canvas ? rfSizeOf(canvas.rf) : () => null
          now.commit(label, g => opRefitBoxesOf(applyPositions(g, positions), positions.keys(), sizeOf))
          // A full tidy can grow the graph past the view: frame it.
          if (all && store.getState().graph !== graph && canvas) {
            requestAnimationFrame(() => {
              if (!runCommand('view.frameAll', { canvas })) void canvas.rf.fitView({ padding: 0.2, duration: 200 })
            })
          }
        })
        .catch(err => {
          console.error('nodebuilder: tidy layout failed', err)
          store.getState().showFlash('Tidy layout failed')
        })
      return true
    },
  },
]
