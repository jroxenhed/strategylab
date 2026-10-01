/**
 * CanvasChrome — the React Flow extras drawn with the canvas: the dot grid,
 * the minimap (W3 pre-step 3.0; minimap colors by item 3.H, spec S22) and
 * the W4 sparkline layer (S26).
 * Rendered inside <ReactFlow>.
 *
 * React Flow's zoom Controls are not drawn (foundation amendment A2, audit
 * bug B10): zoom is the mouse wheel, the F and H keys, the toolbar
 * "Reset view" button and the status-bar zoom value.
 *
 * The minimap draws plain rects in each node's category color (boxes in
 * their tint, notes in the note border color, unsupported nodes dim). It is
 * 160x100 (120x76 when the canvas is under 1000px wide) and hidden under
 * 600px. Its mask and node opacity are styled in statusChrome.css.
 */

import { useMemo } from 'react'
import { Background, MiniMap, useStore, type ReactFlowState } from '@xyflow/react'
import type { Graph } from '../../api/nodebuilder'
import { minimapNodeColorWith, tokenColor } from './minimapColors'
import { isUnsupportedNode } from './nodes/unsupported'
import { SparklineLayer } from './nodes/Sparkline'
import { useScreenGraph } from './screen'
import { useDiagnostics, type Diagnostic } from './useDiagnostics'
import './statusChrome.css'

const NONE: readonly Diagnostic[] = []

/**
 * Ids of the nodes drawn as the S13 unsupported card, as one string so the
 * minimap's color function keeps its identity while the set is the same.
 */
function unsupportedKey(graph: Graph | null, byNode: Record<string, Diagnostic[]>): string {
  if (!graph) return ''
  const wiredIn = new Set<string>()
  for (const w of graph.wires) wiredIn.add(w.to)
  const ids: string[] = []
  for (const [id, n] of Object.entries(graph.nodes)) {
    if (isUnsupportedNode(n.type, wiredIn.has(id), byNode[id] ?? NONE)) ids.push(id)
  }
  return ids.join('\u0000')
}

// Props passed as objects are hoisted so they keep one identity: React Flow
// compares them by reference and would write its store on every render.
const MINIMAP_BASE = {
  background: 'rgba(17,21,29,0.92)',
  border: '1px solid var(--nb-border)',
  borderRadius: 4,
  margin: 8,
}
const MINIMAP_LARGE = { ...MINIMAP_BASE, width: 160, height: 100 }
const MINIMAP_SMALL = { ...MINIMAP_BASE, width: 120, height: 76 }

/** Below this canvas width the minimap shrinks; below the other it hides. */
const SMALL_BELOW_PX = 1000
const HIDDEN_BELOW_PX = 600

// The canvas size in pixels. React Flow stores 500x500 when it could not
// measure the canvas (hidden tab, jsdom), so that pair counts as unknown.
const selectWidth = (s: ReactFlowState) => (s.width === 500 && s.height === 500 ? 0 : s.width)

// No props: the graph on screen comes from screen.ts (the read-only view
// has no store graph).
export default function CanvasChrome() {
  const width = useStore(selectWidth)
  // Width 0: not measured; draw the full-size map.
  const hidden = width > 0 && width < HIDDEN_BELOW_PX
  const small = width > 0 && width < SMALL_BELOW_PX
  // Unsupported nodes are dim in the minimap (S22, UX-22): the same rule
  // as the card, on the graph on screen (store or read-only view).
  const { graph } = useScreenGraph()
  const { byNode } = useDiagnostics()
  const key = useMemo(() => unsupportedKey(graph, byNode), [graph, byNode])
  const nodeColor = useMemo(() => minimapNodeColorWith(new Set(key ? key.split('\u0000') : [])), [key])
  return (
    <>
      <Background gap={20} color="oklch(0.26 0.018 250)" />
      {/* W4 (S26): one canvas for every node's sparkline. It needs the
          ReactFlow context (ViewportPortal, useStoreApi), hence lives here. */}
      <SparklineLayer />
      {!hidden && (
        <MiniMap
          position="bottom-left"
          style={small ? MINIMAP_SMALL : MINIMAP_LARGE}
          nodeColor={nodeColor}
          nodeStrokeWidth={0}
          nodeBorderRadius={1}
          maskColor="rgba(11,14,20,0.6)"
          maskStrokeColor={tokenColor('--nb-selection')}
          maskStrokeWidth={1}
          ariaLabel="Minimap"
          pannable
          zoomable
        />
      )}
    </>
  )
}
