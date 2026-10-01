/**
 * Canvas — React Flow integration for the graph viewer.
 *
 * Unit 4b: registers custom nodeTypes + edgeTypes.
 * Unit 5: wires to Zustand store when graph.readOnly === false.
 * Unit 6: Tab key opens TabMenu; Delete/Backspace deletes the selection;
 *          onConnect creates wires via store.addWire; handles visible in edit mode.
 * Wave 1: every key goes through the command registry (commands/index.ts);
 *          the canvas registers its own commands (Tab, Delete) while editing.
 *          A group drag or a box delete is one store commit, so one undo step.
 *
 * Translates the Graph into React Flow nodes + edges, dispatching each
 * backend node to the correct custom renderer by category.
 *
 * Read-only (auto-render): pan/zoom only; nodes are not draggable/connectable.
 * Editable (store-backed): nodesDraggable=true; drag-end calls store.moveNodes
 * once with every node that moved.
 *
 * Selection lives in React Flow's local node state (so box selection with
 * Shift-drag and Cmd/Ctrl-click to add or remove a node work). The store
 * keeps one "primary" selected node, which is what the Tab menu auto-wires
 * from.
 */

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  useStore,
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
import { NODE_CATALOG, canWire, hasOutputPort, type NodeCatalogEntry } from './catalog'
import type { BaseNodeData } from './nodes/BaseNode'
import { useNodeBuilderStore } from './store'
import {
  newNodeId,
  newWireId,
  removeNodes as opRemoveNodes,
  removeNodesWithRewire as opRemoveNodesWithRewire,
  uniqueName,
} from './operations'
import { useDiagnostics, useStreams, useStreamsFresh, useWireFocus } from './useDiagnostics'
import {
  connectedPortsByNode,
  connectionProblem,
  connectWire,
  placeLabels,
  portsOf,
  portsSpecOf,
  removeWiresWithTerms,
  wireDiagnostics,
  wireLabels,
  withUniqueWrites,
  type ConnectionLike,
  type Rect,
} from './streamLabels'
import { dispatchKey, registerCommands, type CommandScope } from './commands'
import { closeActivePopover, isPopoverOpen } from './ui/Popover'
import TabMenu from './TabMenu'
import {
  alignSelection,
  dragStopMoves,
  isTypingTarget,
  markHotEdges,
  menuScreenPoint,
  mergeLocalNodes,
  newNodePosition,
  nodeLabel,
  nudgeFree,
  outermostRoot,
  planDeletion,
  primarySelection,
  selectOnly,
  shouldHandleCanvasKey,
  suppressTextSelection,
  type XY,
} from './canvasHelpers'

// ── Custom node renderers ────────────────────────────────────────────────────
import TickerNode from './nodes/TickerNode'
import IndicatorNode from './nodes/IndicatorNode'
import ComparisonNode from './nodes/ComparisonNode'
import LogicNode from './nodes/LogicNode'
import SettingsNode from './nodes/SettingsNode'
import OutputNode from './nodes/OutputNode'

// ── Custom edge renderer ─────────────────────────────────────────────────────
import AttrEdge, { type AttrEdgeData } from './edges/AttrEdge'

// ---------------------------------------------------------------------------
// nodeTypes / edgeTypes — defined outside component to avoid re-registration
// on every render (React Flow warning if these change identity).
// ---------------------------------------------------------------------------
// Perf: wrap each renderer in React.memo so a single node move doesn't
// re-render every other node. The default `arePropsEqual` is fine because
// rfNodes (and its `data` payloads) are memoized in CanvasInner — a node's
// `data` reference only changes when that node's underlying state changes.
//
// The Entry/Exit renderer is registered as 'nbOutput', not 'output': React
// Flow has a built-in 'output' node type with a white default style that
// would otherwise be applied to it. This name is only the React Flow type;
// the graph node type is unchanged.
const nodeTypes: NodeTypes = {
  ticker: memo(TickerNode),
  indicator: memo(IndicatorNode),
  comparison: memo(ComparisonNode),
  logic: memo(LogicNode),
  settings: memo(SettingsNode),
  nbOutput: memo(OutputNode),
}

const edgeTypes: EdgeTypes = {
  attr: AttrEdge,
}

// Props passed as objects are hoisted so they keep one identity: React Flow
// compares them by reference and would write its store on every render.
const FIT_VIEW_OPTIONS = { padding: 0.2 }
const HOME_VIEWPORT = { x: 0, y: 0, zoom: 1 }
// While the canvas is the active area both scopes may act on a key.
// Global commands (Cmd+S, Cmd+Z...) are dispatched by NodeBuilder's
// useGlobalKeys, in every mode; the canvas only runs its own.
const CANVAS_SCOPES: ReadonlySet<CommandScope> = new Set<CommandScope>(['canvas'])
const MINIMAP_STYLE = { background: 'oklch(0.18 0.014 250)', border: '1px solid oklch(0.30 0.018 250)' }
// Wire labels hide at rest below this zoom (foundation 5.2).
const LABEL_ZOOM = 0.6
// Node box used for label placement until React Flow has measured a node.
const DEFAULT_NODE_SIZE = { w: 176, h: 60 }

// ---------------------------------------------------------------------------
// Category → RF node type mapping
// ---------------------------------------------------------------------------
const CATEGORY_TO_RF_TYPE: Record<string, string> = {
  ticker:     'ticker',
  indicator:  'indicator',
  comparison: 'comparison',
  logic:      'logic',
  settings:   'settings',
  output:     'nbOutput',
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
  const { screenToFlowPosition, fitView, setViewport: rfSetViewport } = useReactFlow()
  const storeMoveNodes = useNodeBuilderStore(s => s.moveNodes)
  const storeSetViewport = useNodeBuilderStore(s => s.setViewport)
  const storeAddNode = useNodeBuilderStore(s => s.addNode)
  const storeRemoveNodesWithRewire = useNodeBuilderStore(s => s.removeNodesWithRewire)
  const storeRemoveNodes = useNodeBuilderStore(s => s.removeNodes)
  const storeCommit = useNodeBuilderStore(s => s.commit)
  const storeBeginBatch = useNodeBuilderStore(s => s.beginBatch)
  const storeEndBatch = useNodeBuilderStore(s => s.endBatch)
  const storeSelect = useNodeBuilderStore(s => s.select)
  const selectedNodeId = useNodeBuilderStore(s => s.selectedNodeId)
  const layoutEpoch = useNodeBuilderStore(s => s.layoutEpoch)

  // Tab menu state
  const [tabMenuOpen, setTabMenuOpen] = useState(false)
  const [tabMenuScreen, setTabMenuScreen] = useState<XY>({ x: 200, y: 200 })
  const [tabMenuGraph, setTabMenuGraph] = useState<XY>({ x: 0, y: 0 })
  const [tabAutoWire, setTabAutoWire] = useState(true)
  // True when the menu was opened by dropping a wire on empty space: that
  // wire decides what the new node connects to, not the selection.
  const [tabMenuFromWire, setTabMenuFromWire] = useState(false)
  // Houdini-style: when a port-drag ends in empty space, remember which node
  // and port it came from so the next node we create from TabMenu auto-wires
  // to it. Cleared on TabMenu close (Esc, outside click or successful create).
  const pendingWireRef = useRef<{ fromNodeId: string; handleType: 'source' | 'target'; handleId: string | null } | null>(null)
  // Last pointer position over the canvas, in screen pixels. Tab opens here.
  const lastPointerRef = useRef<XY | null>(null)
  // Puts back the page's text selection setting after a wire drag.
  const restoreSelectRef = useRef<(() => void) | null>(null)

  // Selected wire id (for delete)
  const [selectedWireId, setSelectedWireId] = useState<string | null>(null)

  const containerRef = useRef<HTMLDivElement>(null)
  // True while the last pointer press landed inside the node builder. Keys
  // that reach the page body only act on the canvas while this is set.
  const canvasActiveRef = useRef(false)

  // Latest graph for callbacks, so they don't need graph.nodes as a
  // dependency (which changed on every edit and re-created them).
  const graphRef = useRef(graph)

  // Per-node cache so that ONLY the node whose underlying state actually
  // changed produces a fresh RFNode (and therefore a fresh `data` reference).
  // Without this, any mutation (move, select, add) rebuilt every rfNode with a
  // new `data` ref → React.memo on the custom renderers invalidated for every
  // node → N renders per single-node change. Now: 1 render per single-node
  // change regardless of graph size.
  //
  // Selection is not part of the signature: React Flow owns it locally (see
  // alignSelection below). A move only changes the position, so the `data`
  // object is reused when nothing inside it changed.
  const rfNodeCacheRef = useRef<Map<string, { dataSig: string; sig: string; rfNode: RFNode }>>(new Map())
  const prevRfNodesRef = useRef<RFNode[] | null>(null)
  const rfNodes: RFNode[] = useMemo(() => {
    const cache = rfNodeCacheRef.current
    const seen = new Set<string>()
    const result: RFNode[] = []
    for (const n of Object.values(graph.nodes)) {
      seen.add(n.id)
      const dataSig = `${n.type}|${n.display ? 1 : 0}|${n.bypass ? 1 : 0}|${editable ? 1 : 0}|${JSON.stringify(n.params)}`
      const sig = `${dataSig}|${n.position[0]},${n.position[1]}`
      const cached = cache.get(n.id)
      if (cached && cached.sig === sig) {
        result.push(cached.rfNode)
        continue
      }
      const data: BaseNodeData = cached && cached.dataSig === dataSig
        ? (cached.rfNode.data as BaseNodeData)
        : {
            backendType: n.type,
            catalog: CATALOG_BY_NAME.get(n.type) ?? null,
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
      }
      cache.set(n.id, { dataSig, sig, rfNode })
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
  }, [graph.nodes, editable])

  // ── Wires (spec S11) ──────────────────────────────────────────────────────
  // Each wire's label is what its consumer reads through it, from the last
  // /validate streams. Placement (fan-out, fan-in, overlap) runs on graph
  // changes, node drag end and node size changes only, never per frame.
  const streams = useStreams()
  const { diagnostics } = useDiagnostics()
  const wireFocus = useWireFocus()
  // A boolean selector, so panning and zooming re-render only at the band edge.
  const lowZoom = useStore(s => s.transform[2] < LABEL_ZOOM)
  // Streams answer an older graph between a commit and its validate:
  // labels then also trust the static guess (a rename shows at once).
  const streamsFresh = useStreamsFresh()
  // The node under the pointer: its wires go "hot". Kept in a ref and
  // patched onto the local edge mirror (markHotEdges), so hover never
  // re-runs the edge build below.
  const hoveredNodeRef = useRef<string | null>(null)
  // Measured node sizes, for label placement. `sizesVersion` bumps when one changes.
  const sizesRef = useRef<Map<string, { w: number; h: number }>>(new Map())
  const [sizesVersion, setSizesVersion] = useState(0)

  const labels = useMemo(() => wireLabels(graph, streams, streamsFresh), [graph, streams, streamsFresh])
  const connectedPorts = useMemo(() => connectedPortsByNode(graph.wires), [graph.wires])
  const placements = useMemo(() => {
    const rects: Record<string, Rect> = {}
    const portCounts: Record<string, number> = {}
    const dynamicNodes = new Set<string>()
    for (const n of Object.values(graph.nodes)) {
      const size = sizesRef.current.get(n.id) ?? DEFAULT_NODE_SIZE
      rects[n.id] = { x: n.position[0], y: n.position[1], w: size.w, h: size.h }
      portCounts[n.id] = portsOf(n.type, connectedPorts.get(n.id) ?? []).length
      if (portsSpecOf(n.type)?.dynamic) dynamicNodes.add(n.id)
    }
    const items = graph.wires.map(w => ({
      id: w.id, from: w.from, to: w.to, toPort: w.to_port, text: labels[w.id]?.text ?? '',
    }))
    return placeLabels(items, rects, portCounts, dynamicNodes)
    // sizesVersion: re-place when React Flow reports a new node size.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.nodes, graph.wires, labels, connectedPorts, sizesVersion])
  const diagByWire = useMemo(() => wireDiagnostics(diagnostics, graph), [diagnostics, graph])

  // Per-edge cache, same pattern as the nodes: an edge object (and its
  // `data`) only changes when something it shows changed.
  const rfEdgeCacheRef = useRef<Map<string, { sig: string; rfEdge: RFEdge }>>(new Map())
  const prevRfEdgesRef = useRef<RFEdge[] | null>(null)
  const rfEdges: RFEdge[] = useMemo(() => {
    const cache = rfEdgeCacheRef.current
    const seen = new Set<string>()
    const result: RFEdge[] = []
    for (const w of graph.wires) {
      seen.add(w.id)
      const selected = w.id === selectedWireId
      const label = labels[w.id]
      const place = placements[w.id] ?? { t: 0.5, dx: 0, hidden: null }
      const consumer = graph.nodes[w.to]
      const portLabel = portsSpecOf(consumer?.type)?.ports[Number(w.to_port.slice(2))]?.label ?? w.to_port
      const data: AttrEdgeData = {
        from: w.from,
        to: w.to,
        text: label?.text ?? '',
        placeholder: label?.placeholder ?? false,
        reads: label?.reads ?? [],
        t: place.t,
        dx: place.dx,
        hidden: place.hidden,
        lowZoom,
        // Set on the local mirror by markHotEdges (node hover).
        hot: false,
        diag: diagByWire.get(w.id)?.message ?? null,
        fromName: graph.nodes[w.from]?.name ?? w.from,
        toName: consumer?.name ?? w.to,
        portLabel,
      }
      const sig = `${w.from}|${w.to}|${w.to_port}|${selected ? 1 : 0}|${JSON.stringify(data)}`
      const cached = cache.get(w.id)
      if (cached && cached.sig === sig) {
        result.push(cached.rfEdge)
        continue
      }
      const rfEdge: RFEdge = {
        id: w.id,
        source: w.from,
        target: w.to,
        // Handle ids: the one output is 'out'; inputs are 'in0', 'in1', ...
        sourceHandle: 'out',
        targetHandle: w.to_port,
        type: 'attr',
        selected,
        data,
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
  }, [graph.wires, graph.nodes, selectedWireId, labels, placements, lowZoom, diagByWire])

  // Local mirror of nodes/edges so React Flow can update positions LIVE during
  // a drag (and selection during a click) without round-tripping through the
  // Zustand store. The store stays authoritative for graph content; we sync
  // FROM store on memo-array changes, and commit drag-end / connect / delete
  // back TO store. Without onNodesChange, React Flow's internal node state was
  // being continuously overwritten by the prop on every parent render → drag
  // had no visual update until release.
  const [localNodes, setLocalNodes] = useState<RFNode[]>(rfNodes)
  const [localEdges, setLocalEdges] = useState<RFEdge[]>(rfEdges)
  const localNodesRef = useRef(localNodes)
  const localEdgesRef = useRef(localEdges)

  // Sync store-derived rfNodes INTO the local mirror. mergeLocalNodes keeps
  // React Flow's measured sizes and selection, and returns the same array when
  // nothing really changed (e.g. right after a drag, when the store catches up
  // to positions React Flow already shows), so no second render happens.
  useEffect(() => {
    setLocalNodes(curr => mergeLocalNodes(curr, rfNodes))
  }, [rfNodes])
  // Declared after the merge so a node created and selected in one step is
  // already in the mirror when its selection is applied.
  useEffect(() => {
    setLocalNodes(curr => alignSelection(curr, selectedNodeId))
  }, [selectedNodeId])
  // React Flow owns wire selection locally (click, Shift-click), so an edge
  // that is already shown keeps its local `selected` flag; everything else
  // comes from the store. A diagnostic that selects a wire sets it below.
  useEffect(() => {
    setLocalEdges(curr => {
      const selectedById = new Map(curr.map(e => [e.id, e.selected]))
      const next = markHotEdges(rfEdges.map(r => {
        const sel = selectedById.get(r.id)
        return sel === undefined || !!sel === !!r.selected ? r : { ...r, selected: sel }
      }), hoveredNodeRef.current)
      const same = next.length === curr.length && next.every((e, i) => {
        const c = curr[i]
        return e === c || (e.id === c.id && e.data === c.data && e.selected === c.selected
          && e.source === c.source && e.target === c.target && e.targetHandle === c.targetHandle)
      })
      return same ? curr : next
    })
  }, [rfEdges])

  // A diagnostic about a wire (popover row, badge) selects that wire only.
  // A request made before this canvas mounted is not replayed.
  const seenFocusSeq = useRef(wireFocus?.seq ?? 0)
  useEffect(() => {
    if (!wireFocus || wireFocus.seq === seenFocusSeq.current) return
    seenFocusSeq.current = wireFocus.seq
    const { wireId } = wireFocus
    setSelectedWireId(wireId)
    storeSelect(null)
    setLocalNodes(curr => curr.some(n => n.selected) ? curr.map(n => (n.selected ? { ...n, selected: false } : n)) : curr)
    setLocalEdges(curr => curr.map(e => (!!e.selected === (e.id === wireId) ? e : { ...e, selected: e.id === wireId })))
    // Only a new request (seq) acts; the store function is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wireFocus?.seq])

  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setLocalNodes(nds => applyNodeChanges(changes, nds))
    // Keep measured sizes for wire label placement (not per drag frame:
    // dimension changes come on mount and on real size changes).
    let sizeChanged = false
    for (const c of changes) {
      if (c.type !== 'dimensions' || !c.dimensions) continue
      const prev = sizesRef.current.get(c.id)
      const w = Math.round(c.dimensions.width)
      const h = Math.round(c.dimensions.height)
      if (!prev || prev.w !== w || prev.h !== h) {
        sizesRef.current.set(c.id, { w, h })
        sizeChanged = true
      }
    }
    if (sizeChanged) setSizesVersion(v => v + 1)
  }, [])

  const handleNodeMouseEnter = useCallback((_e: React.MouseEvent, node: RFNode) => {
    hoveredNodeRef.current = node.id
    setLocalEdges(curr => markHotEdges(curr, node.id))
  }, [])
  const handleNodeMouseLeave = useCallback(() => {
    hoveredNodeRef.current = null
    setLocalEdges(curr => markHotEdges(curr, null))
  }, [])
  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    setLocalEdges(eds => applyEdgeChanges(changes, eds))
  }, [])

  // React Flow selection (click, Shift-click, box) → the store's primary node.
  const handleSelectionChange = useCallback(
    ({ nodes }: { nodes: RFNode[]; edges: RFEdge[] }) => {
      const current = useNodeBuilderStore.getState().selectedNodeId
      const next = primarySelection(nodes.map(n => n.id), current)
      if (next !== current) storeSelect(next)
    },
    [storeSelect],
  )

  // ── Focus ─────────────────────────────────────────────────────────────────

  const focusPane = useCallback(() => {
    containerRef.current?.focus({ preventScroll: true })
  }, [])

  // Focus the canvas when editing starts, so keys work straight away.
  useEffect(() => {
    if (editable) focusPane()
  }, [editable, focusPane])

  // Re-fit when editing starts, and whenever the store loads a different
  // graph (layoutEpoch: New, Open, Edit this graph). The editable copy spreads
  // the rows out, and the first fit (made for the read-only view) can leave
  // Entry/Exit below the fold. An empty graph goes back to the home view
  // instead, so New does not keep the old graph's pan and zoom. Waits a
  // frame so React Flow has the new nodes.
  useEffect(() => {
    if (!editable) return
    const frame = requestAnimationFrame(() => {
      if (Object.keys(graphRef.current.nodes).length === 0) void rfSetViewport(HOME_VIEWPORT)
      else void fitView(FIT_VIEW_OPTIONS)
    })
    return () => cancelAnimationFrame(frame)
  }, [editable, layoutEpoch, fitView, rfSetViewport])

  // Re-fit when a new read-only graph arrives (another chart request, or back
  // from editing): the fitView prop only fits the first graph React Flow
  // sees. Read-only graphs keep their identity between renders, so this runs
  // once per new graph, never while the user pans.
  useEffect(() => {
    if (editable) return
    const frame = requestAnimationFrame(() => { void fitView(FIT_VIEW_OPTIONS) })
    return () => cancelAnimationFrame(frame)
  }, [editable, graph, fitView])

  // Track whether the last press was inside the node builder (see
  // canvasActiveRef). Capture phase, so React Flow stopping a pointer event
  // cannot hide it.
  useEffect(() => {
    if (!editable) return
    canvasActiveRef.current = true  // editing just started, focus is on the canvas
    const onPointerDown = (e: PointerEvent) => {
      const container = containerRef.current
      const root = container ? outermostRoot(container) : null
      const t = e.target as Node | null
      // Presses in the builder's own portals (a dialog, a popover menu) count
      // as inside: after closing one, keys still belong to the canvas.
      canvasActiveRef.current =
        (!!root && root.contains(t)) || (t instanceof Element && t.closest('.nodebuilder-root') != null)
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => document.removeEventListener('pointerdown', onPointerDown, true)
  }, [editable])

  // ── Key handlers ──────────────────────────────────────────────────────────
  // Every key goes through the command registry. The canvas decides WHETHER
  // the press belongs to the node builder (shouldHandleCanvasKey); the
  // registry decides WHAT it does. Modifier chords (Cmd+Z) are matched by the
  // registry, so the modifier check is off here.

  const handleKeyDown = (e: KeyboardEvent) => {
    if (!editable) return
    // Don't interfere with the TabMenu's own keydown (it handles its own input)
    if (tabMenuOpen) return
    // An open popover (toolbar menu, diagnostics, browser row menu) owns plain
    // keys: Delete or arrows there must not reach the canvas selection.
    // (Global chords such as Cmd+S go through useGlobalKeys, not here.)
    if (isPopoverOpen() && !e.metaKey && !e.ctrlKey) return
    const container = containerRef.current
    if (!container) return
    const ok = shouldHandleCanvasKey({
      target: e.target,
      root: outermostRoot(container),
      // display:none (another app tab is showing) gives no client rects.
      inView: container.getClientRects().length > 0,
      modifier: false,
      defaultPrevented: e.defaultPrevented,
      bodyActive: canvasActiveRef.current,
    })
    if (!ok) return
    dispatchKey(e, { scopes: CANVAS_SCOPES })
  }

  // Tab: open the Tab menu at the pointer. Returns false (not handled) when
  // focus is on a toolbar button, so Tab keeps moving focus there.
  const openTabMenuFromKey = (e: KeyboardEvent): boolean => {
    const container = containerRef.current
    if (!container) return false
    const t = e.target as Node | null
    const onBody = !t || t === document.body || t === document.documentElement
    if (!onBody && !container.contains(t)) return false
    const rect = container.getBoundingClientRect()
    const screen = menuScreenPoint(lastPointerRef.current, rect)
    const primary = selectedNodeId
      ? localNodesRef.current.find(n => n.id === selectedNodeId) ?? null
      : null
    const topLeft = screenToFlowPosition({ x: rect.left, y: rect.top })
    const bottomRight = screenToFlowPosition({ x: rect.right, y: rect.bottom })
    setTabMenuScreen(screen)
    setTabMenuGraph(newNodePosition({
      openedBy: 'keyboard',
      pointFlow: screenToFlowPosition(screen),
      selected: primary,
      visible: { minX: topLeft.x, minY: topLeft.y, maxX: bottomRight.x, maxY: bottomRight.y },
    }))
    pendingWireRef.current = null
    setTabMenuFromWire(false)
    setTabMenuOpen(true)
    return true
  }

  // Delete / Backspace: remove the selection as ONE undo step. Nodes are
  // removed with the Houdini rewire rule unless `rewire` is false
  // (Shift+Delete).
  const deleteSelection = (rewire: boolean): boolean => {
    const plan = planDeletion(localNodesRef.current, localEdgesRef.current, selectedNodeId, selectedWireId)
    if (plan.nodeIds.length === 0 && plan.wireIds.length === 0) return false
    if (plan.wireIds.length === 0 && rewire) {
      storeRemoveNodesWithRewire(plan.nodeIds)
    } else if (plan.wireIds.length === 0) {
      storeRemoveNodes(plan.nodeIds)
    } else {
      const label = plan.nodeIds.length > 0 ? 'delete selection' : 'delete wire'
      storeCommit(label, g => {
        // A wire into AND / OR / XOR also takes back the term it added.
        const withoutWires = removeWiresWithTerms(g, plan.wireIds)
        if (plan.nodeIds.length === 0) return withoutWires
        return rewire
          ? opRemoveNodesWithRewire(withoutWires, plan.nodeIds)
          : opRemoveNodes(withoutWires, plan.nodeIds)
      })
    }
    storeSelect(null)
    setSelectedWireId(null)
    return true
  }

  // Listen on the document, not the canvas div: after a toolbar click or a
  // closed menu, focus is on a button or the page body and a listener on the
  // div never heard the key. shouldHandleCanvasKey limits it to the node
  // builder while it is on screen. The ref keeps one listener for the
  // component's life while always calling the latest handler.
  const handleKeyDownRef = useRef(handleKeyDown)
  const openTabMenuRef = useRef(openTabMenuFromKey)
  const deleteSelectionRef = useRef(deleteSelection)
  // Refresh the "latest value" refs after each render, before any event can
  // read them. Callbacks read these instead of taking the values as deps.
  useLayoutEffect(() => {
    graphRef.current = graph
    localNodesRef.current = localNodes
    localEdgesRef.current = localEdges
    handleKeyDownRef.current = handleKeyDown
    openTabMenuRef.current = openTabMenuFromKey
    deleteSelectionRef.current = deleteSelection
  })
  useEffect(() => {
    if (!editable) return
    const listener = (e: KeyboardEvent) => handleKeyDownRef.current(e)
    document.addEventListener('keydown', listener)
    return () => document.removeEventListener('keydown', listener)
  }, [editable])

  // The canvas's own commands, registered while editing. They read the
  // latest handlers through refs, so they are registered once.
  useEffect(() => {
    if (!editable) return
    return registerCommands([
      {
        id: 'canvas.tabMenu',
        label: 'Add node',
        keys: ['tab'],
        scope: 'canvas',
        run: e => openTabMenuRef.current(e),
      },
      {
        id: 'canvas.delete',
        label: 'Delete selection',
        keys: ['delete', 'backspace'],
        scope: 'canvas',
        run: () => deleteSelectionRef.current(true),
      },
      {
        id: 'canvas.deleteNoRewire',
        label: 'Delete without reconnecting',
        keys: ['shift+delete', 'shift+backspace'],
        scope: 'canvas',
        run: () => deleteSelectionRef.current(false),
      },
    ])
  }, [editable])

  // Spec 0.8: a press anywhere in the canvas (pane, node, wire, minimap)
  // gives the canvas root keyboard focus, so keys work after a wire click.
  // Text fields keep their focus; the portalled Tab menu is not inside the
  // container in the DOM, so it is skipped too.
  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    const container = containerRef.current
    const target = e.target as Node | null
    if (!container || !target || !container.contains(target)) return
    if (isTypingTarget(e.target)) return
    if (document.activeElement !== container) focusPane()
  }, [focusPane])

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    lastPointerRef.current = { x: e.clientX, y: e.clientY }
  }, [])
  // Forget the pointer when it leaves, so Tab doesn't open the menu at the
  // canvas edge where the pointer last crossed.
  const handlePointerLeave = useCallback(() => {
    lastPointerRef.current = null
  }, [])

  // ── Drag-end: persist positions ───────────────────────────────────────────
  // React Flow passes every node that moved with the grabbed one; save them
  // all, or the rest snap back on the next sync from the store. All of them
  // go in one moveNodes call: one store write and one undo step.

  const commitDrag = useCallback(
    (moves: Array<{ id: string; position: [number, number] }>) => {
      const nodes = graphRef.current.nodes
      const ids: string[] = []
      const deltas: Array<[number, number]> = []
      for (const m of moves) {
        const n = nodes[m.id]
        if (!n) continue
        ids.push(m.id)
        deltas.push([m.position[0] - n.position[0], m.position[1] - n.position[1]])
      }
      if (ids.length > 0) storeMoveNodes(ids, deltas)
    },
    [storeMoveNodes],
  )

  const handleNodeDragStop = useCallback(
    (_event: React.MouseEvent, node: RFNode, nodes: RFNode[]) => {
      if (!editable) return
      commitDrag(dragStopMoves(node, nodes))
    },
    [editable, commitDrag],
  )

  // Dragging the box around a selection (not a node) ends here instead.
  const handleSelectionDragStop = useCallback(
    (_event: React.MouseEvent, nodes: RFNode[]) => {
      if (!editable) return
      commitDrag(dragStopMoves(null, nodes))
    },
    [editable, commitDrag],
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

  // 0.4: a pan or zoom closes the open popover (menu, diagnostics, ...), in
  // view mode too. Only user gestures carry an event; a programmatic move
  // (fitView, framing a node from a diagnostics row) passes null and leaves
  // the popover open.
  const handleMoveStart = useCallback((event: MouseEvent | TouchEvent | null) => {
    if (event) closeActivePopover()
  }, [])

  // ── onConnect: wire drag creates a wire ───────────────────────────────────
  // The wire goes exactly port to port (spec S08): `sourceHandle` is 'out',
  // `targetHandle` the input port ('in1'). React Flow hands the ends over in
  // that order even when the drag started at the input. The new wire and the
  // consumer's default read (an empty `attr` param takes the source's
  // primary write) are one commit, so one undo step.

  const handleConnect = useCallback(
    (params: Connection) => {
      const g = graphRef.current
      if (connectionProblem(g, params) !== null) return
      try {
        storeCommit('add wire', graph => connectWire(graph, {
          id: newWireId(),
          from: params.source,
          to: params.target,
          to_port: params.targetHandle ?? undefined,
        }))
      } catch {
        // Refused (a cycle, or no port at one end). isValidConnection below
        // already showed the drag as invalid, so there is nothing to add.
      }
    },
    [storeCommit],
  )

  // Tells React Flow, during the drag, whether a wire may be dropped here:
  // no self-loop, no cycle, no second wire into a port that has one, no
  // port past the node's last one.
  const isValidConnection = useCallback(
    (c: ConnectionLike) => connectionProblem(graphRef.current, c) === null,
    [],
  )

  // ── Node click → select ───────────────────────────────────────────────────

  const handleNodeClick = useCallback(
    (event: React.MouseEvent, node: RFNode) => {
      setSelectedWireId(null)
      // With the multi-select key held React Flow adds or removes this node;
      // handleSelectionChange then picks the primary. Selecting it here too
      // would collapse the group to this node (even when it was removed).
      if (event.metaKey || event.ctrlKey || event.shiftKey) return
      // A plain click (React Flow only reports one below the drag threshold)
      // selects just this node, even inside a group, so Delete removes only it.
      setLocalNodes(curr => selectOnly(curr, node.id))
      storeSelect(node.id)
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
    focusPane()
  }, [storeSelect, focusPane])

  // ── Port-drag → empty space opens TabMenu (Houdini pattern) ───────────────
  // onConnectStart fires when the user starts dragging from a handle. We
  // capture which node + handle so the next node we create can be auto-wired
  // to it. onConnectEnd fires on release; if the drop landed on empty pane
  // (not on a handle), we open the TabMenu at the drop point.

  const handleConnectStart = useCallback(
    (_event: unknown, params: { nodeId: string | null; handleId: string | null; handleType: 'source' | 'target' | null }) => {
      // No text selection on the page while the wire follows the pointer.
      restoreSelectRef.current?.()
      restoreSelectRef.current = suppressTextSelection()
      if (!editable || !params.nodeId || !params.handleType) {
        pendingWireRef.current = null
        return
      }
      pendingWireRef.current = { fromNodeId: params.nodeId, handleType: params.handleType, handleId: params.handleId }
    },
    [editable],
  )

  const handleConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent) => {
      restoreSelectRef.current?.()
      restoreSelectRef.current = null
      if (!editable || !pendingWireRef.current) return
      const target = event.target as HTMLElement | null
      const onPane = !!target?.classList?.contains('react-flow__pane')
      if (!onPane) {
        // Dropped on a handle / something else — let handleConnect deal with it.
        pendingWireRef.current = null
        return
      }
      const point = 'touches' in event && event.touches.length > 0
        ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
        : 'changedTouches' in event && event.changedTouches.length > 0
        ? { x: event.changedTouches[0].clientX, y: event.changedTouches[0].clientY }
        : { x: (event as MouseEvent).clientX, y: (event as MouseEvent).clientY }
      setTabMenuScreen(point)
      setTabMenuGraph(newNodePosition({
        openedBy: 'wire',
        pointFlow: screenToFlowPosition(point),
        selected: null,
      }))
      setTabMenuFromWire(true)
      setTabMenuOpen(true)
      // pendingWireRef stays set; handleTabMenuCreate will consume it.
    },
    [editable, screenToFlowPosition],
  )

  // Put text selection back if the canvas unmounts in the middle of a wire drag.
  useEffect(() => () => { restoreSelectRef.current?.() }, [])

  // ── Tab menu: create node ─────────────────────────────────────────────────

  const handleTabMenuCreate = useCallback(
    (catalogEntry: NodeCatalogEntry, withWire: boolean) => {
      const nodes = graphRef.current.nodes
      const id = newNodeId()
      // Step down past any node already on this spot, so repeated creates
      // don't stack exactly on top of each other.
      const pos = nudgeFree(
        tabMenuGraph,
        Object.values(nodes).map(n => ({ x: n.position[0], y: n.position[1] })),
      )
      const newNode: GraphNode = {
        id,
        type: catalogEntry.name,
        name: uniqueName(graphRef.current, catalogEntry.name, null),
        parent: null,
        // Write names are made unique in the graph (@rsi, then @rsi_2).
        params: withUniqueWrites(
          graphRef.current,
          catalogEntry.name,
          { ...catalogEntry.defaults.params } as GraphNode['params'],
        ),
        position: [pos.x, pos.y],
        display: false,
        bypass: false,
      }

      // The node and its auto-wire are one undo step.
      storeBeginBatch(`add ${newNode.name}`)
      try {
        storeAddNode(newNode)

        // Priority 1: port-drag → empty-space wire takes precedence over
        // the autoWire-from-selection hint. Direction follows the dragged
        // handle: source-handle drag → new node is the target; target-handle
        // drag → new node is the source.
        const pending = pendingWireRef.current
        pendingWireRef.current = null
        if (pending && pending.fromNodeId !== id) {
          const isFromSource = pending.handleType === 'source'
          const fromId = isFromSource ? pending.fromNodeId : id
          const toId = isFromSource ? id : pending.fromNodeId
          const sourceType = fromId === id ? newNode.type : nodes[fromId]?.type
          const targetType = toId === id ? newNode.type : nodes[toId]?.type
          // Skip the wire when the new node has no port on that side (a Ticker
          // has no input, Entry/Exit and Settings nodes no output).
          if (canWire(sourceType, targetType)) {
            try {
              // A drag that started at an input wires into that very port.
              const toPort = !isFromSource ? pending.handleId ?? undefined : undefined
              storeCommit('add wire', g => connectWire(g, { id: newWireId(), from: fromId, to: toId, to_port: toPort }))
            } catch {
              // A cycle, or the dragged input port already has a wire
              // (connectWire refuses a full port): skip the auto-wire.
            }
          }
        } else if (
          // Auto-wire: source.out → new.in (from selected node), only when the
          // selected node has an output and the new node an input.
          withWire && selectedNodeId && selectedNodeId !== id
          && canWire(nodes[selectedNodeId]?.type, newNode.type)
        ) {
          try {
            storeCommit('add wire', g => connectWire(g, { id: newWireId(), from: selectedNodeId, to: id }))
          } catch {
            // Cycle — skip auto-wire silently
          }
        }
      } finally {
        storeEndBatch()
      }

      storeSelect(id)
    },
    [tabMenuGraph, storeAddNode, storeCommit, storeBeginBatch, storeEndBatch, storeSelect, selectedNodeId],
  )

  const handleTabMenuClose = useCallback(() => {
    // Closing without creating cancels the pending wire (Esc / outside click).
    pendingWireRef.current = null
    setTabMenuOpen(false)
    setTabMenuFromWire(false)
    // Give keys back to the canvas.
    focusPane()
  }, [focusPane])

  const handleToggleAutoWire = useCallback(() => setTabAutoWire(v => !v), [])

  // ── RF built-in delete callbacks (also hook for robustness) ──────────────

  const handleNodesDelete = useCallback(
    (nodes: RFNode[]) => {
      storeRemoveNodesWithRewire(nodes.map(n => n.id))
      storeSelect(null)
    },
    [storeRemoveNodesWithRewire, storeSelect],
  )

  const handleEdgesDelete = useCallback(
    (edges: RFEdge[]) => {
      if (edges.length > 0) {
        storeCommit('delete wire', g => removeWiresWithTerms(g, edges.map(e => e.id)))
      }
      setSelectedWireId(null)
    },
    [storeCommit],
  )

  // The node the Tab menu would auto-wire from: none for a wire-drop menu
  // (the dropped wire decides) or when the selected node has no output.
  const wireFromId = !tabMenuFromWire && selectedNodeId && hasOutputPort(graph.nodes[selectedNodeId]?.type)
    ? selectedNodeId
    : null

  return (
    <div
      ref={containerRef}
      className="nodebuilder-root"
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      style={{ width: '100%', height: '100%', background: 'var(--nb-bg)', outline: 'none' }}
    >
      <ReactFlow
        nodes={localNodes}
        edges={localEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onSelectionChange={handleSelectionChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={editable}
        nodesConnectable={editable}
        elementsSelectable={true}
        deleteKeyCode={null}  // We handle Delete ourselves to run rewire logic
        onNodeDragStop={editable ? handleNodeDragStop : undefined}
        onSelectionDragStop={editable ? handleSelectionDragStop : undefined}
        onMoveStart={handleMoveStart}
        onMoveEnd={editable ? handleMoveEnd : undefined}
        onConnect={editable ? handleConnect : undefined}
        onConnectStart={editable ? handleConnectStart : undefined}
        onConnectEnd={editable ? handleConnectEnd : undefined}
        onNodeClick={handleNodeClick}
        onNodeMouseEnter={handleNodeMouseEnter}
        onNodeMouseLeave={handleNodeMouseLeave}
        onEdgeClick={editable ? handleEdgeClick : undefined}
        onPaneClick={handlePaneClick}
        onNodesDelete={editable ? handleNodesDelete : undefined}
        onEdgesDelete={editable ? handleEdgesDelete : undefined}
        isValidConnection={editable ? isValidConnection : undefined}
        // Arrow keys would move a focused node in React Flow's copy only; the
        // store never heard of it and the node snapped back.
        disableKeyboardA11y
        fitView
        fitViewOptions={FIT_VIEW_OPTIONS}
        minZoom={0.1}
        maxZoom={4}
      >
        <Background gap={20} color="oklch(0.26 0.018 250)" />
        <Controls position="bottom-right" />
        <MiniMap
          position="bottom-left"
          nodeColor="oklch(0.30 0.018 250)"
          maskColor="rgba(0,0,0,0.5)"
          style={MINIMAP_STYLE}
        />
      </ReactFlow>

      {editable && (
        <TabMenu
          open={tabMenuOpen}
          screenPosition={tabMenuScreen}
          graphPosition={tabMenuGraph}
          selectedNodeId={wireFromId}
          wireFromLabel={wireFromId ? nodeLabel(graph.nodes[wireFromId]) : undefined}
          autoWire={tabAutoWire}
          onToggleAutoWire={handleToggleAutoWire}
          onCreate={handleTabMenuCreate}
          onClose={handleTabMenuClose}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Canvas — public component. Wraps CanvasInner inside ReactFlowProvider so
// useReactFlow() works inside CanvasInner. Memoized so a parent re-render
// with the same graph object doesn't re-render the whole flow.
// ---------------------------------------------------------------------------
interface CanvasProps {
  graph: Graph
}

function Canvas({ graph }: CanvasProps) {
  const editable = !graph.readOnly

  return (
    <ReactFlowProvider>
      <CanvasInner graph={graph} editable={editable} />
    </ReactFlowProvider>
  )
}

export default memo(Canvas)
