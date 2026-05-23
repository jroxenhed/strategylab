/**
 * Canvas — React Flow integration for the graph viewer.
 *
 * Unit 4b: registers custom nodeTypes + edgeTypes.
 * Unit 5: wires to Zustand store when graph.readOnly === false.
 * Unit 6: Tab key opens TabMenu; Delete/Backspace deletes selected node or wire;
 *          onConnect creates wires via store.addWire; handles visible in edit mode.
 *
 * Translates the Graph into React Flow nodes + edges, dispatching each
 * backend node to the correct custom renderer by category.
 *
 * Read-only (auto-render): pan/zoom only; nodes are not draggable/connectable.
 * Editable (store-backed): nodesDraggable=true; drag-end calls store.moveNode.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeTypes,
  type EdgeTypes,
  type Connection,
} from '@xyflow/react'
import type { Graph, GraphNode } from '../../api/nodebuilder'
import { NODE_CATALOG } from './catalog'
import type { BaseNodeData } from './nodes/BaseNode'
import { useNodeBuilderStore } from './store'
import TabMenu from './TabMenu'
import type { NodeCatalogEntry } from './catalog'

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
// Canvas (inner) — needs useReactFlow() so it must be a child of ReactFlowProvider
// ---------------------------------------------------------------------------
interface CanvasInnerProps {
  graph: Graph
  editable: boolean
}

function CanvasInner({ graph, editable }: CanvasInnerProps) {
  const { screenToFlowPosition } = useReactFlow()
  const storeMoveNode = useNodeBuilderStore(s => s.moveNode)
  const storeSetViewport = useNodeBuilderStore(s => s.setViewport)
  const storeAddNode = useNodeBuilderStore(s => s.addNode)
  const storeAddWire = useNodeBuilderStore(s => s.addWire)
  const storeRemoveNodeWithRewire = useNodeBuilderStore(s => s.removeNodeWithRewire)
  const storeRemoveWire = useNodeBuilderStore(s => s.removeWire)
  const storeSelect = useNodeBuilderStore(s => s.select)
  const selectedNodeId = useNodeBuilderStore(s => s.selectedNodeId)

  // Tab menu state
  const [tabMenuOpen, setTabMenuOpen] = useState(false)
  const [tabMenuScreen, setTabMenuScreen] = useState({ x: 200, y: 200 })
  const [tabMenuGraph, setTabMenuGraph] = useState({ x: 0, y: 0 })
  const [tabAutoWire, setTabAutoWire] = useState(true)

  // Selected wire id (for delete)
  const [selectedWireId, setSelectedWireId] = useState<string | null>(null)

  const containerRef = useRef<HTMLDivElement>(null)

  // Translate Graph.nodes (dict[str, Node]) → RF nodes array
  const rfNodes: RFNode[] = Object.values(graph.nodes).map(n => {
    const catalogEntry = NODE_CATALOG.find(e => e.name === n.type) ?? null
    const data: BaseNodeData = {
      backendType: n.type,
      catalog: catalogEntry,
      params: n.params,
      display: n.display,
      bypass: n.bypass,
      nodePath: n.id,
      editable,
    }
    return {
      id: n.id,
      type: rfTypeFor(n.type),
      position: { x: n.position[0], y: n.position[1] },
      data,
      draggable: editable,
      selectable: true,
      selected: n.id === selectedNodeId,
    }
  })

  // Translate Graph.wires → RF edges
  const rfEdges: RFEdge[] = graph.wires.map(w => ({
    id: w.id,
    source: w.from,
    target: w.to,
    label: w.attr ?? undefined,
    type: 'attr',
    selected: w.id === selectedWireId,
  }))

  // ── Key handlers ──────────────────────────────────────────────────────────

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!editable) return
      // Don't interfere with the TabMenu's own keydown (it handles its own input)
      if (tabMenuOpen) return

      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      if (e.key === 'Tab') {
        e.preventDefault()
        // Open at center of canvas container
        const rect = containerRef.current?.getBoundingClientRect()
        const screenX = rect ? rect.left + rect.width / 2 : 300
        const screenY = rect ? rect.top + 80 : 200
        setTabMenuScreen({ x: screenX, y: screenY })
        setTabMenuGraph(screenToFlowPosition({ x: screenX, y: screenY }))
        setTabMenuOpen(true)
        return
      }

      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedNodeId) {
          e.preventDefault()
          storeRemoveNodeWithRewire(selectedNodeId)
          storeSelect(null)
          setSelectedWireId(null)
        } else if (selectedWireId) {
          e.preventDefault()
          storeRemoveWire(selectedWireId)
          setSelectedWireId(null)
        }
      }
    },
    [
      editable, tabMenuOpen, selectedNodeId, selectedWireId,
      storeRemoveNodeWithRewire, storeSelect, storeRemoveWire, screenToFlowPosition,
    ]
  )

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.addEventListener('keydown', handleKeyDown)
    return () => el.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  // ── Drag-end: persist positions ───────────────────────────────────────────

  const handleNodeDragStop = useCallback(
    (_event: React.MouseEvent, node: RFNode) => {
      if (!editable) return
      storeMoveNode(node.id, [node.position.x, node.position.y])
    },
    [editable, storeMoveNode],
  )

  // ── Viewport persist ──────────────────────────────────────────────────────

  const handleMove = useCallback(
    (_event: MouseEvent | TouchEvent | null, viewport: { x: number; y: number; zoom: number }) => {
      if (!editable) return
      storeSetViewport({ x: viewport.x, y: viewport.y, zoom: viewport.zoom })
    },
    [editable, storeSetViewport],
  )

  // ── onConnect: wire drag creates a wire ───────────────────────────────────

  const handleConnect = useCallback(
    (params: Connection) => {
      if (!params.source || !params.target) return
      // Derive attr from source node's catalog writes[0]
      const sourceNode = graph.nodes[params.source]
      let attr: string | null = null
      if (sourceNode) {
        const entry = NODE_CATALOG.find(e => e.name === sourceNode.type)
        attr = (entry?.writes[0] as string | undefined) ?? null
      }
      try {
        storeAddWire({
          id: crypto.randomUUID(),
          from: params.source,
          to: params.target,
          attr,
        })
      } catch {
        // Cycle detected — silently ignore (React Flow already shows visual feedback)
      }
    },
    [graph.nodes, storeAddWire],
  )

  // ── Node click → select ───────────────────────────────────────────────────

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: RFNode) => {
      storeSelect(node.id)
      setSelectedWireId(null)
    },
    [storeSelect],
  )

  // ── Edge (wire) click → select wire ──────────────────────────────────────

  const handleEdgeClick = useCallback(
    (_event: React.MouseEvent, edge: RFEdge) => {
      setSelectedWireId(edge.id)
      storeSelect(null)
    },
    [storeSelect],
  )

  // ── Canvas click (background) → deselect ─────────────────────────────────

  const handlePaneClick = useCallback(() => {
    storeSelect(null)
    setSelectedWireId(null)
  }, [storeSelect])

  // ── Tab menu: create node ─────────────────────────────────────────────────

  const handleTabMenuCreate = useCallback(
    (catalogEntry: NodeCatalogEntry, withWire: boolean) => {
      const id = crypto.randomUUID()
      const newNode: GraphNode = {
        id,
        type: catalogEntry.name,
        params: { ...catalogEntry.defaults.params },
        position: [tabMenuGraph.x, tabMenuGraph.y],
        display: false,
        bypass: false,
      }
      storeAddNode(newNode)

      // Auto-wire: source.out → new.in
      if (withWire && selectedNodeId && selectedNodeId !== id) {
        const srcNode = graph.nodes[selectedNodeId]
        let attr: string | null = null
        if (srcNode) {
          const entry = NODE_CATALOG.find(e => e.name === srcNode.type)
          attr = (entry?.writes[0] as string | undefined) ?? null
        }
        try {
          storeAddWire({
            id: crypto.randomUUID(),
            from: selectedNodeId,
            to: id,
            attr,
          })
        } catch {
          // Cycle — skip auto-wire silently
        }
      }

      storeSelect(id)
    },
    [tabMenuGraph, storeAddNode, storeAddWire, storeSelect, selectedNodeId, graph.nodes],
  )

  // ── RF built-in delete callbacks (also hook for robustness) ──────────────

  const handleNodesDelete = useCallback(
    (nodes: RFNode[]) => {
      for (const n of nodes) {
        storeRemoveNodeWithRewire(n.id)
      }
      storeSelect(null)
    },
    [storeRemoveNodeWithRewire, storeSelect],
  )

  const handleEdgesDelete = useCallback(
    (edges: RFEdge[]) => {
      for (const e of edges) {
        storeRemoveWire(e.id)
      }
      setSelectedWireId(null)
    },
    [storeRemoveWire],
  )

  return (
    <div
      ref={containerRef}
      className="nodebuilder-root"
      tabIndex={0}
      style={{ width: '100%', height: '100%', background: 'var(--nb-bg)', outline: 'none' }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={editable}
        nodesConnectable={editable}
        elementsSelectable={true}
        deleteKeyCode={null}  // We handle Delete ourselves to run rewire logic
        onNodeDragStop={editable ? handleNodeDragStop : undefined}
        onMove={editable ? handleMove : undefined}
        onConnect={editable ? handleConnect : undefined}
        onNodeClick={handleNodeClick}
        onEdgeClick={editable ? handleEdgeClick : undefined}
        onPaneClick={handlePaneClick}
        onNodesDelete={editable ? handleNodesDelete : undefined}
        onEdgesDelete={editable ? handleEdgesDelete : undefined}
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

      {editable && (
        <TabMenu
          open={tabMenuOpen}
          screenPosition={tabMenuScreen}
          graphPosition={tabMenuGraph}
          selectedNodeId={selectedNodeId}
          autoWire={tabAutoWire}
          onToggleAutoWire={() => setTabAutoWire(v => !v)}
          onCreate={handleTabMenuCreate}
          onClose={() => setTabMenuOpen(false)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Canvas — public component. Wraps CanvasInner inside ReactFlowProvider so
// useReactFlow() works inside CanvasInner.
// ---------------------------------------------------------------------------
interface CanvasProps {
  graph: Graph
}

export default function Canvas({ graph }: CanvasProps) {
  const editable = !graph.readOnly

  return (
    <ReactFlowProvider>
      <CanvasInner graph={graph} editable={editable} />
    </ReactFlowProvider>
  )
}
