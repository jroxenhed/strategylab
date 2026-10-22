/**
 * A click on an input port that has a wire selects that wire (F457), so
 * Delete removes it. A short wire between two close cards sits under the
 * ports' grab areas and the node bodies, so it is otherwise hard to hit.
 *
 * Only a click lands here: a press that turns into a drag still starts a
 * wire drag (React Flow ends that drag elsewhere, so no click reaches the
 * port). A click that finishes a click-to-connect is left to React Flow.
 * Frame ports count too: a frame's top port (`in<k>`) and its inner output
 * handle (`bnd:<id>`) select the wire into them.
 *
 * Loaded by the plugins/ auto-loader (canvasPlugins.ts); the listener lives
 * in an `overlays` slot component, like WireCut.
 */

import { useEffect } from 'react'
import type { Graph } from '../../../api/nodebuilder'
import type { CanvasPlugin } from '../canvasPlugins'
import { networkOccupant } from '../operations/wires'
import { getActiveCanvas } from '../screen'
import { registerSlot } from '../slots'

/** The id of the wire into the input port `handle` (a React Flow handle element), or null. */
export function portWire(graph: Pick<Graph, 'nodes' | 'wires'>, handle: Element): string | null {
  if (!handle.classList.contains('target')) return null
  const nodeId = handle.getAttribute('data-nodeid')
  if (!nodeId) return null
  const c = { source: null, target: nodeId, sourceHandle: null, targetHandle: handle.getAttribute('data-handleid') }
  return networkOccupant(graph, c)?.id ?? null
}

/** Listens for clicks on input ports of the active canvas. Renders nothing. */
export function PortWireSelect(): null {
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.button !== 0 || e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return
      const ctx = getActiveCanvas()
      const root = ctx?.container()
      const t = e.target
      if (!ctx || !root || !(t instanceof Element) || !root.contains(t)) return
      const handle = t.closest('.react-flow__handle')
      if (!handle) return
      // A click-to-connect is running: this click finishes it.
      if (root.querySelector('.react-flow__handle.clickconnecting')) return
      const id = portWire(ctx.graph(), handle)
      if (!id) return
      // React Flow would start a click-to-connect from this port, or
      // select the node: the wire is what was meant.
      e.preventDefault()
      e.stopPropagation()
      ctx.store.getState().setSelection({ wireIds: [id] })
      // Delete is heard on the canvas.
      ctx.focus()
    }
    window.addEventListener('click', onClick, true)
    return () => window.removeEventListener('click', onClick, true)
  }, [])
  return null
}

registerSlot('overlays', 'portWireSelect', PortWireSelect, 25)

export const plugin: CanvasPlugin = { id: 'portWireSelect' }
