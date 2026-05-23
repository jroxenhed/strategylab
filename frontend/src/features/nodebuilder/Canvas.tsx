/**
 * Canvas — React Flow integration for the read-only Unit 4a graph viewer.
 *
 * Translates the Graph (from auto_render API) into React Flow nodes + edges.
 * Pan/zoom enabled. Nodes are not draggable and not connectable (read-only).
 * Unit 4b will swap in custom node renderers; here we use the default 'default' type.
 */

import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node as RFNode,
  type Edge as RFEdge,
} from '@xyflow/react'
import type { Graph } from '../../api/nodebuilder'

interface CanvasProps {
  graph: Graph
}

export default function Canvas({ graph }: CanvasProps) {
  // Translate Graph.nodes (dict[str, Node]) → RF nodes array
  const rfNodes: RFNode[] = Object.values(graph.nodes).map(n => ({
    id: n.id,
    type: 'default',
    position: { x: n.position[0], y: n.position[1] },
    data: { label: `${n.type}\n${n.id}` },
    draggable: false,
    selectable: true,
  }))

  // Translate Graph.wires → RF edges
  // Note: with response_model_by_alias=True on the endpoint, wires have `from`/`to`.
  const rfEdges: RFEdge[] = graph.wires.map(w => ({
    id: w.id,
    source: w.from,
    target: w.to,
    label: w.attr ?? undefined,
    type: 'default',
  }))

  return (
    <div style={{ width: '100%', height: '100%', background: 'var(--bg-main)' }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={4}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={20} color="#30363d" />
        <Controls position="bottom-right" />
        <MiniMap
          position="bottom-left"
          nodeColor="#30363d"
          maskColor="rgba(0,0,0,0.5)"
          style={{ background: '#161b22', border: '1px solid #30363d' }}
        />
      </ReactFlow>
    </div>
  )
}
