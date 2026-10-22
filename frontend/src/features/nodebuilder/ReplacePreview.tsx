/**
 * Replace preview (F457): while a wire drag is over an input port that
 * already has a wire, that wire is drawn dim and dashed (edge data
 * `replacing`), so the user sees which wire the drop takes out. The mark
 * goes when the drag leaves the port or ends.
 *
 * Rendered inside React Flow (CanvasChrome.tsx), which holds the drag state.
 */

import { useEffect } from 'react'
import { useConnection, useReactFlow } from '@xyflow/react'
import { dropOccupant } from './operations/wires'
import { reconnectingWireId } from './plugins/wireOps'
import { useNodeBuilderStore } from './store'

export default function ReplacePreview() {
  const rf = useReactFlow()
  // A string (or null), so the canvas re-renders only when the target changes.
  const occupant = useConnection(c => {
    if (!c.inProgress || c.isValid !== true) return null
    const graph = useNodeBuilderStore.getState().graph
    if (!graph || graph.readOnly) return null
    return dropOccupant(graph, { fromHandle: c.fromHandle, toHandle: c.toHandle }, reconnectingWireId())?.id ?? null
  })

  useEffect(() => {
    if (!occupant) return
    const mark = (on: boolean) => {
      try {
        rf.updateEdgeData(occupant, { replacing: on })
      } catch {
        // The wire is gone (the drop replaced it): nothing to clear.
      }
    }
    mark(true)
    return () => mark(false)
  }, [occupant, rf])

  return null
}
