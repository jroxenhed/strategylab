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

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  applyNodeChanges,
  applyEdgeChanges,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeTypes,
  type EdgeTypes,
  type Connection,
  type NodeChange,
  type EdgeChange,
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
// Perf: wrap each renderer in React.memo so a single node move doesn't
// re-render every other node. The default `arePropsEqual` is fine because
// rfNodes (and its `data` payloads) are memoized in CanvasInner — a node's
// `data` reference only changes when that node's underlying state changes.
const nodeTypes: NodeTypes = {
  ticker: memo(TickerNode),
  indicator: memo(IndicatorNode),
  comparison: memo(ComparisonNode),
  logic: memo(LogicNode),
  settings: memo(SettingsNode),
  output: memo(OutputNode),
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

// Perf: NODE_CATALOG.find(...) per-node per-render was O(N×M); pre-build a
// Map once at module load. Catalog is static.
const CATALOG_BY_NAME: Map<string, NodeCatalogEntry> = new Map(
  NODE_CATALOG.map(e => [e.name, e]),
)

/**
 * Resolve the React Flow node type for a given backend node type string.
 * Falls back to 'indicator' for types not in Core 14 (e.g. turns_up, stochastic).
 */
function rfTypeFor(backendType: string): string {
  const entry = CATALOG_BY_NAME.get(backendType)
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

  // Per-node cache so that ONLY the node whose underlying state actually
  // changed produces a fresh RFNode (and therefore a fresh `data` reference).
  // Without this, any mutation (move, select, add) rebuilt every rfNode with a
  // new `data` ref → React.memo on the custom renderers invalidated for every
  // node → N renders per single-node change. Now: 1 render per single-node
  // change regardless of graph size.
  const rfNodeCacheRef = useRef<Map<string, { sig: string; rfNode: RFNode }>>(new Map())
  const rfNodes: RFNode[] = useMemo(() => {
    const cache = rfNodeCacheRef.current
    const seen = new Set<string>()
    const result: RFNode[] = []
    for (const n of Object.values(graph.nodes)) {
      seen.add(n.id)
      const selected = n.id === selectedNodeId
      const sig = `${n.type}|${n.position[0]},${n.position[1]}|${n.display ? 1 : 0}|${n.bypass ? 1 : 0}|${editable ? 1 : 0}|${selected ? 1 : 0}|${JSON.stringify(n.params)}`
      const cached = cache.get(n.id)
      if (cached && cached.sig === sig) {
        result.push(cached.rfNode)
        continue
      }
      const catalogEntry = CATALOG_BY_NAME.get(n.type) ?? null
      const data: BaseNodeData = {
        backendType: n.type,
        catalog: catalogEntry,
        params: n.params,
        display: n.display,
        bypass: n.bypass,
        nodePath: n.id,
        editable,
      }
      const rfNode: RFNode = {
        id: n.id,
        type: rfTypeFor(n.type),
        position: { x: n.position[0], y: n.position[1] },
        data,
        draggable: editable,
        selectable: true,
        selected,
      }
      cache.set(n.id, { sig, rfNode })
      result.push(rfNode)
    }
    // Evict removed nodes so the cache doesn't grow unbounded.
    for (const id of Array.from(cache.keys())) {
      if (!seen.has(id)) cache.delete(id)
    }
    // Stabilize the array reference itself: if every element matches the
    // previous result element-wise, return the previous array so consumers
    // (useEffect deps, child memo) don't see a new reference.
    const prev = prevRfNodesRef.current
    if (prev && prev.length === result.length && result.every((n, i) => n === prev[i])) {
      return prev
    }
    prevRfNodesRef.current = result
    return result
  }, [graph.nodes, editable, selectedNodeId])

  // Per-edge cache, same pattern.
  const rfEdgeCacheRef = useRef<Map<string, { sig: string; rfEdge: RFEdge }>>(new Map())
  const prevRfEdgesRef = useRef<RFEdge[] | null>(null)
  const rfEdges: RFEdge[] = useMemo(() => {
    const cache = rfEdgeCacheRef.current
    const seen = new Set<string>()
    const result: RFEdge[] = []
    for (const w of graph.wires) {
      seen.add(w.id)
      const selected = w.id === selectedWireId
      const sig = `${w.from}|${w.to}|${w.attr ?? ''}|${selected ? 1 : 0}`
      const cached = cache.get(w.id)
      if (cached && cached.sig === sig) {
        result.push(cached.rfEdge)
        continue
      }
      const rfEdge: RFEdge = {
        id: w.id,
        source: w.from,
        target: w.to,
        label: w.attr ?? undefined,
        type: 'attr',
        selected,
      }
      cache.set(w.id, { sig, rfEdge })
      result.push(rfEdge)
    }
    for (const id of Array.from(cache.keys())) {
      if (!seen.has(id)) cache.delete(id)
    }
    const prev = prevRfEdgesRef.current
    if (prev && prev.length === result.length && result.every((e, i) => e === prev[i])) {
      return prev
    }
    prevRfEdgesRef.current = result
    return result
  }, [graph.wires, selectedWireId])

  // Local mirror of nodes/edges so React Flow can update positions LIVE during
  // a drag (and selection during a click) without round-tripping through the
  // Zustand store. The store stays authoritative; we sync FROM store on
  // memo-array changes, and commit drag-end / connect / delete back TO store.
  // Without onNodesChange, React Flow's internal node state was being
  // continuously overwritten by the prop on every parent render → drag had
  // no visual update until release.
  const prevRfNodesRef = useRef<RFNode[] | null>(null)
  const [localNodes, setLocalNodes] = useState<RFNode[]>(rfNodes)
  const [localEdges, setLocalEdges] = useState<RFEdge[]>(rfEdges)
  useEffect(() => { setLocalNodes(rfNodes) }, [rfNodes])
  useEffect(() => { setLocalEdges(rfEdges) }, [rfEdges])

  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setLocalNodes(nds => applyNodeChanges(changes, nds))
  }, [])
  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    setLocalEdges(eds => applyEdgeChanges(changes, eds))
  }, [])

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
  // Perf: onMove fires on EVERY pan/zoom pixel; writing to Zustand at that
  // rate triggered a re-render storm. Use onMoveEnd instead — store-update
  // once when the gesture finishes. React Flow handles the in-flight viewport
  // itself via its internal state.
  const handleMoveEnd = useCallback(
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
        nodes={localNodes}
        edges={localEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={editable}
        nodesConnectable={editable}
        elementsSelectable={true}
        deleteKeyCode={null}  // We handle Delete ourselves to run rewire logic
        onNodeDragStop={editable ? handleNodeDragStop : undefined}
        onMoveEnd={editable ? handleMoveEnd : undefined}
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
