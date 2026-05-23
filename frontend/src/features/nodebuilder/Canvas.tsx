/**
 * Canvas — React Flow integration for the read-only graph viewer.
 *
 * Unit 4b: registers custom nodeTypes + edgeTypes.
 * Translates the Graph (from auto_render API) into React Flow nodes + edges,
 * dispatching each backend node to the correct custom renderer by category.
 *
 * Pan/zoom enabled. Nodes are not draggable and not connectable (read-only).
 */

import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeTypes,
  type EdgeTypes,
} from '@xyflow/react'
import type { Graph } from '../../api/nodebuilder'
import { NODE_CATALOG } from './catalog'
import type { BaseNodeData } from './nodes/BaseNode'

// ── Custom node renderers ────────────────────────────────────────────────────
import TickerNode from './nodes/TickerNode'
import IndicatorNode from './nodes/IndicatorNode'
import ComparisonNode from './nodes/ComparisonNode'
import LogicNode from './nodes/LogicNode'
import SettingsNode from './nodes/SettingsNode'
import OutputNode from './nodes/OutputNode'

// ── Custom edge renderer ─────────────────────────────────────────────────────
import AttrEdge from './edges/AttrEdge'

// ---------------------------------------------------------------------------
// nodeTypes / edgeTypes — defined outside component to avoid re-registration
// on every render (React Flow warning if these change identity).
// ---------------------------------------------------------------------------
const nodeTypes: NodeTypes = {
  ticker: TickerNode,
  indicator: IndicatorNode,
  comparison: ComparisonNode,
  logic: LogicNode,
  settings: SettingsNode,
  output: OutputNode,
}

const edgeTypes: EdgeTypes = {
  attr: AttrEdge,
}

// ---------------------------------------------------------------------------
// Category → RF node type mapping
// ---------------------------------------------------------------------------
const CATEGORY_TO_RF_TYPE: Record<string, string> = {
  ticker:     'ticker',
  indicator:  'indicator',
  comparison: 'comparison',
  logic:      'logic',
  settings:   'settings',
  output:     'output',
}

/**
 * Resolve the React Flow node type for a given backend node type string.
 * Falls back to 'indicator' for types not in Core 14 (e.g. turns_up, stochastic).
 */
function rfTypeFor(backendType: string): string {
  const entry = NODE_CATALOG.find(e => e.name === backendType)
  if (!entry) return 'indicator'  // generic fallback
  return CATEGORY_TO_RF_TYPE[entry.cat] ?? 'indicator'
}

// ---------------------------------------------------------------------------
// Canvas component
// ---------------------------------------------------------------------------
interface CanvasProps {
  graph: Graph
}

export default function Canvas({ graph }: CanvasProps) {
  // Translate Graph.nodes (dict[str, Node]) → RF nodes array
  // BaseNodeData extends Record<string, unknown> so it satisfies the RF constraint.
  const rfNodes: RFNode[] = Object.values(graph.nodes).map(n => {
    const catalogEntry = NODE_CATALOG.find(e => e.name === n.type) ?? null
    const data: BaseNodeData = {
      backendType: n.type,
      catalog: catalogEntry,
      params: n.params,
      display: n.display,
      bypass: n.bypass,
      nodePath: n.id,
    }
    return {
      id: n.id,
      type: rfTypeFor(n.type),
      position: { x: n.position[0], y: n.position[1] },
      data,
      draggable: false,
      selectable: true,
    }
  })

  // Translate Graph.wires → RF edges
  // With response_model_by_alias=True on the endpoint, wires have `from`/`to`.
  const rfEdges: RFEdge[] = graph.wires.map(w => ({
    id: w.id,
    source: w.from,
    target: w.to,
    label: w.attr ?? undefined,
    type: 'attr',
  }))

  return (
    <div className="nodebuilder-root" style={{ width: '100%', height: '100%', background: 'var(--nb-bg)' }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={4}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={20} color="oklch(0.26 0.018 250)" />
        <Controls position="bottom-right" />
        <MiniMap
          position="bottom-left"
          nodeColor="oklch(0.30 0.018 250)"
          maskColor="rgba(0,0,0,0.5)"
          style={{ background: 'oklch(0.18 0.014 250)', border: '1px solid oklch(0.30 0.018 250)' }}
        />
      </ReactFlow>
    </div>
  )
}
