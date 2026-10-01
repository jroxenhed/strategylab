/**
 * Canvas — React Flow integration for the graph viewer.
 *
 * Unit 4b: registers custom nodeTypes + edgeTypes.
 * Unit 5: wires to Zustand store when graph.readOnly === false.
 * Unit 6: Tab key opens TabMenu; Delete/Backspace deletes the selection;
 *          onConnect creates wires via store.addWire; handles visible in edit mode.
 * Wave 1: every key goes through the command registry (commands/index.ts).
 *          A group drag or a box delete is one store commit, so one undo step.
 * Wave 3 (pre-step 3.0): the pieces other items extend live in registries:
 *          node and edge types (nodeTypes.ts, edgeTypes.ts), the graph to
 *          React Flow mapping (rfMapping.ts), plugins called from the React
 *          Flow handlers below (canvasPlugins.ts), commands (commands/), and
 *          the grid and minimap (CanvasChrome.tsx).
 *
 * Read-only (auto-render): pan/zoom only; nodes are not draggable/connectable.
 * Editable (store-backed): nodesDraggable=true; drag-end calls store.moveNodes
 * once with every node that moved.
 *
 * Selection lives in React Flow's local node state (so box selection with
 * Shift-drag and Cmd/Ctrl-click to add or remove a node work). Every change
 * is mirrored into the store's selection slice (all selected nodes, wires,
 * boxes and notes, plus one "primary" node the Tab menu auto-wires from). A
 * selection set from outside the canvas (`setSelection`) is applied back.
 */

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  applyNodeChanges,
  applyEdgeChanges,
  type Node as RFNode,
  type Edge as RFEdge,
  type Connection,
  type FinalConnectionState,
  type HandleType,
  type NodeChange,
  type EdgeChange,
  type OnSelectionChangeParams,
  type Viewport,
  SelectionMode,
} from '@xyflow/react'
import type { Graph, GraphNode } from '../../api/nodebuilder'
import { canWire, hasOutputPort, type NodeCatalogEntry } from './catalog'
import { useNodeBuilderStore } from './store'
import {
  newNodeId,
  newWireId,
  removeNodes as opRemoveNodes,
  removeNodesWithRewire as opRemoveNodesWithRewire,
  uniqueName,
} from './operations'
import { useDiagnostics, useStreams, useStreamsFresh, useWireFocus } from './useDiagnostics'
import { reconnectingWireId } from './plugins/wireOps'
import { connectionProblemIgnoring, fullPortNotice } from './operations/wires'
import {
  connectedPortsByNode,
  connectionProblem,
  connectWire,
  removeWiresWithTerms,
  wireDiagnostics,
  wireLabels,
  withUniqueWrites,
  type ConnectionLike,
} from './streamLabels'
import { dispatchKey, type CommandScope } from './commands'
import { activateCanvas, mountCanvas, publishScreenGraph } from './screen'
import { anyPluginHandled, hasPluginHook, type CanvasCtx, type TabMenuRequest } from './canvasPlugins'
import { useNodeTypes } from './nodeTypes'
import { useEdgeTypes } from './edgeTypes'
import {
  absoluteLookup,
  createEdgeMapper,
  createNodeMapper,
  graphPositionOf,
  sourceNodes,
  stableArray,
  useRfNodeSourcesVersion,
  wirePlacements,
} from './rfMapping'
import CanvasChrome from './CanvasChrome'
import { currentParentId, HOME_VIEWPORT, SNAP_GRID } from './store/view'
import { frameOptions, MAX_ZOOM, MIN_ZOOM, wheelIsForSomethingElse, wheelViewport } from './viewOps'
import { closeActivePopover, isPopoverOpen } from './ui/Popover'
import TabMenu from './TabMenu'
import {
  alignSelection,
  dragStopMoves,
  isEmptyCanvasTarget,
  isTypingTarget,
  markHotEdges,
  menuScreenPoint,
  mergeLocalNodes,
  newNodePosition,
  nodeLabel,
  nudgeFree,
  outermostRoot,
  planDeletion,
  selectOnly,
  shouldHandleCanvasKey,
  suppressTextSelection,
  type XY,
} from './canvasHelpers'

// Props passed as objects are hoisted so they keep one identity: React Flow
// compares them by reference and would write its store on every render.
// The first fit after a load: the same as H (frame all), without the ease.
const FIT_VIEW_OPTIONS = frameOptions(false)
/** Longest the first fit of "Edit this graph" waits for its tidy layout (IP-4). */
const LAYOUT_WAIT_MS = 400
// Mouse buttons that pan on drag: the middle one (spec 6.1). The left button
// draws a marquee; holding Space turns it into a pan (React Flow's
// panActivationKeyCode).
const PAN_BUTTONS = [1]
// Shift+click toggles a node in the selection; Cmd/Ctrl+click is an alias.
const MULTI_SELECT_KEYS = ['Shift', 'Meta', 'Control']
// While the canvas is the active area both scopes may act on a key.
// Global commands (Cmd+S, Cmd+Z...) are dispatched by NodeBuilder's
// useGlobalKeys, in every mode; the canvas only runs its own.
const CANVAS_SCOPES: ReadonlySet<CommandScope> = new Set<CommandScope>(['canvas'])
// Wire labels hide at rest below this zoom (foundation 5.2).
const LABEL_ZOOM = 0.6

/**
 * Bring React Flow's selection in line with the store's primary node
 * (alignSelection). Clearing the primary deselects graph nodes only: a box
 * or note selected on its own has no primary, and must stay selected.
 */
function alignPrimary(curr: RFNode[], selectedId: string | null, graphNodes: Graph['nodes']): RFNode[] {
  if (selectedId !== null) return alignSelection(curr, selectedId)
  if (!curr.some(n => n.selected && n.id in graphNodes)) return curr
  return curr.map(n => (n.selected && n.id in graphNodes ? { ...n, selected: false } : n))
}

/** `curr` with `selected` set exactly for the ids in `want` (same array when nothing changes). */
function withSelected<T extends { id: string; selected?: boolean }>(curr: T[], want: ReadonlySet<string>): T[] {
  if (curr.every(x => !!x.selected === want.has(x.id))) return curr
  return curr.map(x => (!!x.selected === want.has(x.id) ? x : { ...x, selected: want.has(x.id) }))
}

// ---------------------------------------------------------------------------
// Canvas (inner) — needs useReactFlow() so it must be a child of ReactFlowProvider
// ---------------------------------------------------------------------------
interface CanvasInnerProps {
  graph: Graph
  editable: boolean
}

function CanvasInner({ graph, editable }: CanvasInnerProps) {
  const rf = useReactFlow()
  const { screenToFlowPosition, fitView, setViewport: rfSetViewport } = rf
  const storeMoveNodes = useNodeBuilderStore(s => s.moveNodes)
  const storeSetViewport = useNodeBuilderStore(s => s.setViewport)
  const storeAddNode = useNodeBuilderStore(s => s.addNode)
  const storeRemoveNodesWithRewire = useNodeBuilderStore(s => s.removeNodesWithRewire)
  const storeRemoveNodes = useNodeBuilderStore(s => s.removeNodes)
  const storeCommit = useNodeBuilderStore(s => s.commit)
  const storeBeginBatch = useNodeBuilderStore(s => s.beginBatch)
  const storeEndBatch = useNodeBuilderStore(s => s.endBatch)
  const storeSelect = useNodeBuilderStore(s => s.select)
  const storeMirrorSelection = useNodeBuilderStore(s => s.mirrorSelection)
  const selectedNodeId = useNodeBuilderStore(s => s.selectedNodeId)
  const selectionRequestSeq = useNodeBuilderStore(s => s.selectionRequestSeq)
  const layoutEpoch = useNodeBuilderStore(s => s.layoutEpoch)
  const layoutPending = useNodeBuilderStore(s => s.layoutPending)
  const network = useNodeBuilderStore(s => s.network)
  const snapToGrid = useNodeBuilderStore(s => s.snapToGrid)
  const nodeTypes = useNodeTypes()
  const edgeTypes = useEdgeTypes()

  // Tab menu state
  const [tabMenuOpen, setTabMenuOpen] = useState(false)
  const [tabMenuScreen, setTabMenuScreen] = useState<XY>({ x: 200, y: 200 })
  const [tabMenuGraph, setTabMenuGraph] = useState<XY>({ x: 0, y: 0 })
  const [tabAutoWire, setTabAutoWire] = useState(true)
  // True when the menu was opened by dropping a wire on empty space (or by a
  // request with its own wiring): that decides what the new node connects
  // to, not the selection.
  const [tabMenuFromWire, setTabMenuFromWire] = useState(false)
  // Houdini-style: when a port-drag ends in empty space, remember which node
  // and port it came from so the next node we create from TabMenu auto-wires
  // to it. Cleared on TabMenu close (Esc, outside click or successful create).
  const pendingWireRef = useRef<{ fromNodeId: string; handleType: 'source' | 'target'; handleId: string | null } | null>(null)
  // The open request from code (CanvasCtx.openTabMenu) with its own onCreate.
  const tabRequestRef = useRef<TabMenuRequest | null>(null)
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
  const editableRef = useRef(editable)

  // ── Nodes ─────────────────────────────────────────────────────────────────
  // Graph nodes map through a per-node cache (rfMapping.ts), so a one-node
  // change gives one fresh node object and one render. Registered node
  // sources (boxes, notes) add their own nodes before or after them.
  const [nodeMapper] = useState(createNodeMapper)
  const graphRfNodes = useMemo(() => nodeMapper(graph.nodes, editable), [nodeMapper, graph.nodes, editable])
  const sourcesVersion = useRfNodeSourcesVersion()
  // The network on screen: sources draw only its boxes and notes (EA-4).
  const networkId = useMemo(() => currentParentId({ network, graph }, graph), [network, graph])
  const extraNodes = useMemo(
    () => sourceNodes(graph, editable, networkId),
    // sourcesVersion: a source registered after mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph, editable, networkId, sourcesVersion],
  )
  const prevAllNodesRef = useRef<RFNode[] | null>(null)
  const rfNodes: RFNode[] = useMemo(() => {
    if (extraNodes.before.length === 0 && extraNodes.after.length === 0) return graphRfNodes
    const all = stableArray(prevAllNodesRef.current, [...extraNodes.before, ...graphRfNodes, ...extraNodes.after])
    prevAllNodesRef.current = all
    return all
  }, [graphRfNodes, extraNodes])

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
  const placements = useMemo(
    () => wirePlacements(graph, labels, connectedPorts, sizesRef.current),
    // sizesVersion: re-place when React Flow reports a new node size.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph.nodes, graph.wires, labels, connectedPorts, sizesVersion],
  )
  const diagByWire = useMemo(() => wireDiagnostics(diagnostics, graph), [diagnostics, graph])

  // Per-edge cache, same pattern as the nodes: an edge object (and its
  // `data`) only changes when something it shows changed.
  const [edgeMapper] = useState(createEdgeMapper)
  const rfEdges: RFEdge[] = useMemo(
    () => edgeMapper({
      graph: { nodes: graph.nodes, wires: graph.wires },
      selectedWireId,
      labels,
      placements,
      lowZoom,
      diagByWire,
    }),
    [edgeMapper, graph.wires, graph.nodes, selectedWireId, labels, placements, lowZoom, diagByWire],
  )

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
    setLocalNodes(curr => alignPrimary(curr, selectedNodeId, graphRef.current.nodes))
  }, [selectedNodeId])
  // A selection set outside the canvas (setSelection: a legend click, a
  // paste) is applied to React Flow here. Declared after the two effects
  // above, so it wins over the single-node alignment.
  const seenSelectionRequest = useRef(selectionRequestSeq)
  useEffect(() => {
    if (selectionRequestSeq === seenSelectionRequest.current) return
    seenSelectionRequest.current = selectionRequestSeq
    const s = useNodeBuilderStore.getState()
    const nodes = new Set([...s.selectedNodeIds, ...s.selectedAnnotationIds])
    const wires = new Set(s.selectedWireIds)
    setLocalNodes(curr => withSelected(curr, nodes))
    setLocalEdges(curr => withSelected(curr, wires))
    setSelectedWireId(s.selectedWireIds[0] ?? null)
  }, [selectionRequestSeq])
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

  // React Flow selection (click, Shift-click, box) → the store's selection
  // slice. Ids that are not graph nodes are boxes and notes.
  const handleSelectionChange = useCallback(
    ({ nodes, edges }: OnSelectionChangeParams) => {
      const g = graphRef.current
      const nodeIds: string[] = []
      const annotationIds: string[] = []
      for (const n of nodes) (n.id in g.nodes ? nodeIds : annotationIds).push(n.id)
      storeMirrorSelection({ nodeIds, wireIds: edges.map(e => e.id), annotationIds })
    },
    [storeMirrorSelection],
  )

  // ── Focus ─────────────────────────────────────────────────────────────────

  const focusPane = useCallback(() => {
    containerRef.current?.focus({ preventScroll: true })
  }, [])

  // Focus the canvas when editing starts, so keys work straight away.
  useEffect(() => {
    if (editable) focusPane()
  }, [editable, focusPane])

  // Place the view when editing starts, when the store loads a different
  // graph (layoutEpoch: New, Open, Edit this graph) and when the network
  // changes. A view saved for this graph and network (the user's last pan
  // and zoom, store/view.ts) comes back as it was. Otherwise: fit, because
  // the editable copy spreads the rows out and the first fit (made for the
  // read-only view) can leave Entry/Exit below the fold; an empty graph goes
  // back to the home view, so New does not keep the old graph's pan and
  // zoom. Waits a frame so React Flow has the new nodes.
  //
  // While "Edit this graph" waits for its tidy (layoutPending, IP-4) the fit
  // is held, up to LAYOUT_WAIT_MS: when the tidy lands first there is one
  // fit, of the tidied graph, instead of a fit, a jump and a second fit.
  useEffect(() => {
    if (!editable) return
    let frame = 0
    const place = () => {
      frame = requestAnimationFrame(() => {
        const remembered = useNodeBuilderStore.getState().rememberedViewport()
        if (remembered) void rfSetViewport(remembered)
        else if (Object.keys(graphRef.current.nodes).length === 0) void rfSetViewport(HOME_VIEWPORT)
        else void fitView(FIT_VIEW_OPTIONS)
      })
    }
    const wait = layoutPending ? setTimeout(place, LAYOUT_WAIT_MS) : null
    if (!wait) place()
    return () => {
      if (wait) clearTimeout(wait)
      cancelAnimationFrame(frame)
    }
  }, [editable, layoutEpoch, layoutPending, network, fitView, rfSetViewport])

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
    // Editing just started, focus is on the canvas. The read-only view takes
    // keys (readOnlyOk commands) only after a press inside the builder.
    canvasActiveRef.current = editable
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

  // ── The canvas context (plugins and commands) ─────────────────────────────
  // One object for the canvas's life. Its methods read the latest values
  // through refs, so it never needs rebuilding.
  const openTabMenuRef = useRef<(req?: TabMenuRequest) => boolean>(() => false)
  const deleteSelectionRef = useRef<(rewire: boolean) => boolean>(() => false)
  const rfRef = useRef(rf)
  const [ctx] = useState<CanvasCtx>(() => ({
    get rf() { return rfRef.current },
    store: useNodeBuilderStore,
    pointer() {
      const inst = rfRef.current
      const p = lastPointerRef.current
      if (p) return inst.screenToFlowPosition(p)
      const el = containerRef.current
      if (!el) return { x: 0, y: 0 }
      const r = el.getBoundingClientRect()
      return inst.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 })
    },
    pointerOnCanvas: () => lastPointerRef.current != null,
    // While editing, the store graph (EA-10): a plugin running after another
    // plugin's commit in the same event (Alt-drag swaps ids at drag start,
    // then boxDrag reads the graph) sees that commit, not the graph of the
    // last render. The read-only graph is not in the store.
    graph: () => (editableRef.current ? useNodeBuilderStore.getState().graph ?? graphRef.current : graphRef.current),
    graphPosition: n => graphPositionOf(n, absoluteLookup(localNodesRef.current)),
    editable: () => editableRef.current,
    container: () => containerRef.current,
    focus: () => { containerRef.current?.focus({ preventScroll: true }) },
    openTabMenu: req => openTabMenuRef.current(req),
    deleteSelection: opts => deleteSelectionRef.current(opts?.rewire ?? true),
  }))

  // Commands run from a button or a panel reach React Flow through this
  // (screen.ts: a stack, so a second canvas unmounting gives this one back).
  // A layout effect, so the canvas is mounted before it first publishes the
  // graph on screen below.
  useLayoutEffect(() => mountCanvas(ctx), [ctx])

  // ── Key handlers ──────────────────────────────────────────────────────────
  // Every key goes through the command registry. The canvas decides WHETHER
  // the press belongs to the node builder (shouldHandleCanvasKey); the
  // registry decides WHAT it does. Modifier chords (Cmd+Z) are matched by the
  // registry, so the modifier check is off here.

  const handleKeyDown = (e: KeyboardEvent) => {
    // The read-only view still takes the keys of `readOnlyOk` commands
    // (Copy, Frame, Frame all, Snap to grid; S19).
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
    dispatchKey(e, { scopes: CANVAS_SCOPES, canvas: ctx, readOnly: !editable })
  }

  // Open the Tab menu (Tab key, a menu row, the Inspector). With a key
  // press, returns false (not handled) when focus is on a toolbar button,
  // so Tab keeps moving focus there.
  const openTabMenu = (req: TabMenuRequest = {}): boolean => {
    if (!editable) return false
    const container = containerRef.current
    if (!container) return false
    if (req.keyEvent) {
      const t = req.keyEvent.target as Node | null
      const onBody = !t || t === document.body || t === document.documentElement
      if (!onBody && !container.contains(t)) return false
    }
    const rect = container.getBoundingClientRect()
    const screen = req.screen ?? menuScreenPoint(lastPointerRef.current, rect)
    const primary = selectedNodeId
      ? localNodesRef.current.find(n => n.id === selectedNodeId) ?? null
      : null
    const topLeft = screenToFlowPosition({ x: rect.left, y: rect.top })
    const bottomRight = screenToFlowPosition({ x: rect.right, y: rect.bottom })
    setTabMenuScreen(screen)
    setTabMenuGraph(req.flow ?? newNodePosition({
      // A point given by the caller is where the node goes.
      openedBy: req.screen ? 'wire' : 'keyboard',
      pointFlow: screenToFlowPosition(screen),
      selected: primary,
      visible: { minX: topLeft.x, minY: topLeft.y, maxX: bottomRight.x, maxY: bottomRight.y },
    }))
    pendingWireRef.current = null
    tabRequestRef.current = req.onCreate ? req : null
    setTabMenuFromWire(req.onCreate != null)
    setTabMenuOpen(true)
    return true
  }

  // Delete / Backspace: remove the selection as ONE undo step. Nodes are
  // removed with the Houdini rewire rule unless `rewire` is false
  // (Shift+Delete). Plugins delete what they own (boxes, notes) in the same
  // step, through onDeleteSelection.
  const deleteSelection = (rewire: boolean): boolean => {
    const plan = planDeletion(localNodesRef.current, localEdgesRef.current, selectedNodeId, selectedWireId)
    const g = graphRef.current
    const nodeIds = plan.nodeIds.filter(id => id in g.nodes)
    const otherIds = plan.nodeIds.filter(id => !(id in g.nodes))
    const wireIds = plan.wireIds
    if (nodeIds.length === 0 && wireIds.length === 0 && otherIds.length === 0) return false
    let handled = false
    // A batch only when a plugin may add its own commit (one undo step).
    const batched = hasPluginHook('onDeleteSelection')
    if (batched) storeBeginBatch()
    try {
      if (batched) handled = anyPluginHandled(p => p.onDeleteSelection?.({ nodeIds, wireIds, otherIds, rewire }, ctx), 'onDeleteSelection')
      if (nodeIds.length > 0 || wireIds.length > 0) {
        handled = true
        if (wireIds.length === 0 && rewire) {
          storeRemoveNodesWithRewire(nodeIds)
        } else if (wireIds.length === 0) {
          storeRemoveNodes(nodeIds)
        } else {
          const label = nodeIds.length > 0 ? 'delete selection' : 'delete wire'
          storeCommit(label, g => {
            // A wire into AND / OR / XOR also takes back the term it added.
            const withoutWires = removeWiresWithTerms(g, wireIds)
            if (nodeIds.length === 0) return withoutWires
            return rewire
              ? opRemoveNodesWithRewire(withoutWires, nodeIds)
              : opRemoveNodes(withoutWires, nodeIds)
          })
        }
      }
    } finally {
      if (batched) storeEndBatch()
    }
    if (!handled) return false
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
  // Refresh the "latest value" refs after each render, before any event can
  // read them. Callbacks read these instead of taking the values as deps.
  useLayoutEffect(() => {
    graphRef.current = graph
    editableRef.current = editable
    rfRef.current = rf
    localNodesRef.current = localNodes
    localEdgesRef.current = localEdges
    // Panels (Inspector, status bar) read the graph on screen reactively.
    publishScreenGraph(ctx, graph, editable)
    handleKeyDownRef.current = handleKeyDown
    openTabMenuRef.current = openTabMenu
    deleteSelectionRef.current = deleteSelection
  })
  useEffect(() => {
    const listener = (e: KeyboardEvent) => handleKeyDownRef.current(e)
    document.addEventListener('keydown', listener)
    return () => document.removeEventListener('keydown', listener)
  }, [])

  // Spec 0.8: a press anywhere in the canvas (pane, node, wire, minimap)
  // gives the canvas root keyboard focus, so keys work after a wire click.
  // Text fields keep their focus; the portalled Tab menu is not inside the
  // container in the DOM, so it is skipped too.
  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    const container = containerRef.current
    const target = e.target as Node | null
    if (!container || !target || !container.contains(target)) return
    activateCanvas(ctx)
    if (isTypingTarget(e.target)) return
    if (document.activeElement !== container) focusPane()
  }, [focusPane, ctx])

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    lastPointerRef.current = { x: e.clientX, y: e.clientY }
    if (!hasPluginHook('onPointerMove')) return
    const flow = rfRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY })
    anyPluginHandled(p => p.onPointerMove?.(flow, ctx), 'onPointerMove')
  }, [ctx])
  // Forget the pointer when it leaves, so Tab doesn't open the menu at the
  // canvas edge where the pointer last crossed.
  const handlePointerLeave = useCallback(() => {
    lastPointerRef.current = null
    anyPluginHandled(p => p.onPointerLeave?.(ctx), 'onPointerLeave')
  }, [ctx])

  // ── Drag: plugins, then persist positions ─────────────────────────────────
  // React Flow passes every node that moved with the grabbed one; save them
  // all, or the rest snap back on the next sync from the store. All of them
  // go in one moveNodes call: one store write and one undo step. The whole
  // drag stop is one batch, so plugin commits made there (splice, box
  // membership) join the same undo step.

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

  const handleNodeDragStart = useCallback(
    (event: React.MouseEvent, node: RFNode, nodes: RFNode[]) => {
      anyPluginHandled(p => p.onNodeDragStart?.(event, node, nodes, ctx), 'onNodeDragStart')
    },
    [ctx],
  )

  const handleNodeDrag = useCallback(
    (event: React.MouseEvent, node: RFNode, nodes: RFNode[]) => {
      if (!hasPluginHook('onNodeDrag')) return
      anyPluginHandled(p => p.onNodeDrag?.(event, node, nodes, ctx), 'onNodeDrag')
    },
    [ctx],
  )

  const handleNodeDragStop = useCallback(
    (event: React.MouseEvent, node: RFNode, nodes: RFNode[]) => {
      if (!editable) return
      // No plugin listening: the plain move, one store write as before.
      if (!hasPluginHook('onNodeDragStop')) {
        commitDrag(dragStopMoves(node, nodes, n => ctx.graphPosition(n as RFNode)))
        return
      }
      storeBeginBatch()
      try {
        const handled = anyPluginHandled(p => p.onNodeDragStop?.(event, node, nodes, ctx), 'onNodeDragStop')
        if (!handled) commitDrag(dragStopMoves(node, nodes, n => ctx.graphPosition(n as RFNode)))
      } finally {
        storeEndBatch()
      }
    },
    [editable, commitDrag, ctx, storeBeginBatch, storeEndBatch],
  )

  // Dragging the box around a selection (not a node) ends here instead.
  const handleSelectionDragStart = useCallback(
    (event: React.MouseEvent, nodes: RFNode[]) => {
      anyPluginHandled(p => p.onSelectionDragStart?.(event, nodes, ctx), 'onSelectionDragStart')
    },
    [ctx],
  )

  const handleSelectionDrag = useCallback(
    (event: React.MouseEvent, nodes: RFNode[]) => {
      if (!hasPluginHook('onSelectionDrag')) return
      anyPluginHandled(p => p.onSelectionDrag?.(event, nodes, ctx), 'onSelectionDrag')
    },
    [ctx],
  )

  const handleSelectionDragStop = useCallback(
    (event: React.MouseEvent, nodes: RFNode[]) => {
      if (!editable) return
      if (!hasPluginHook('onSelectionDragStop')) {
        commitDrag(dragStopMoves(null, nodes, n => ctx.graphPosition(n as RFNode)))
        return
      }
      storeBeginBatch()
      try {
        const handled = anyPluginHandled(p => p.onSelectionDragStop?.(event, nodes, ctx), 'onSelectionDragStop')
        if (!handled) commitDrag(dragStopMoves(null, nodes, n => ctx.graphPosition(n as RFNode)))
      } finally {
        storeEndBatch()
      }
    },
    [editable, commitDrag, ctx, storeBeginBatch, storeEndBatch],
  )

  // ── Viewport persist ──────────────────────────────────────────────────────
  // Perf: onMove fires on EVERY pan/zoom pixel; writing to Zustand at that
  // rate triggered a re-render storm. Use onMoveEnd instead — store-update
  // once when the gesture finishes. React Flow handles the in-flight viewport
  // itself via its internal state. Plugins get every move (onMove) and must
  // throttle themselves.
  // The saved view belongs to the editable graph: a pan in the read-only
  // view must not overwrite it. Checked here through the ref, because React
  // Flow reports a move end from a timer and does not drop a handler that
  // the props set to undefined.
  const handleMoveEnd = useCallback(
    (_event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
      if (!editableRef.current) return
      storeSetViewport({ x: viewport.x, y: viewport.y, zoom: viewport.zoom })
    },
    [storeSetViewport],
  )

  const handleMove = useCallback(
    (_event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
      if (!hasPluginHook('onMove')) return
      anyPluginHandled(p => p.onMove?.(viewport, ctx), 'onMove')
    },
    [ctx],
  )

  // ── Wheel (foundation 6.1) ────────────────────────────────────────────────
  // React Flow's wheel zoom is far too fast (one 600px step went from 1.94 to
  // 0.37), and it has no setting for the step, so the canvas zooms itself:
  // 1.08x per 100px toward the pointer; Cmd+wheel and Shift+wheel pan.
  // Capture phase and not passive, so it runs before React Flow's own wheel
  // handler and can stop the page from scrolling. A pinch (Ctrl+wheel), a
  // scrollable part (.nowheel) and the minimap stay with React Flow.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (wheelIsForSomethingElse(e)) return
      const inst = rfRef.current
      const r = el.getBoundingClientRect()
      const next = wheelViewport(inst.getViewport(), e, { x: e.clientX - r.left, y: e.clientY - r.top })
      e.preventDefault()
      if (!next) return
      // A programmatic move does not close popovers by itself (handleMoveStart).
      closeActivePopover()
      void inst.setViewport(next)
    }
    el.addEventListener('wheel', onWheel, { capture: true, passive: false })
    return () => el.removeEventListener('wheel', onWheel, { capture: true })
  }, [])

  // 0.4: a pan or zoom closes the open popover (menu, diagnostics, ...), in
  // view mode too. Only user gestures carry an event; a programmatic move
  // (fitView, framing a node from a diagnostics row) passes null and leaves
  // the popover open.
  // A user pan or zoom (event set) closes popovers and the Tab menu (S24).
  const tabMenuCloseRef = useRef<(() => void) | null>(null)
  const handleMoveStart = useCallback((event: MouseEvent | TouchEvent | null) => {
    if (!event) return
    closeActivePopover()
    tabMenuCloseRef.current?.()
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
  // port past the node's last one. While a wire end is being moved
  // (reconnect, 3.G), that wire's own port does not count as full.
  const isValidConnection = useCallback(
    (c: ConnectionLike) => connectionProblemIgnoring(graphRef.current, c, reconnectingWireId()) === null,
    [],
  )

  // ── Wire reconnect (plugins, S23) ─────────────────────────────────────────
  // Wire ends can only be picked up while a plugin handles the drop.

  const handleReconnect = useCallback(
    (oldEdge: RFEdge, conn: Connection) => {
      anyPluginHandled(p => p.onReconnect?.(oldEdge, conn, ctx), 'onReconnect')
    },
    [ctx],
  )

  const handleReconnectStart = useCallback(
    (event: React.MouseEvent, edge: RFEdge, handleType: HandleType) => {
      anyPluginHandled(p => p.onReconnectStart?.(event, edge, handleType, ctx), 'onReconnectStart')
    },
    [ctx],
  )

  const handleReconnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, edge: RFEdge, handleType: HandleType, state: FinalConnectionState) => {
      anyPluginHandled(p => p.onReconnectEnd?.(event, edge, handleType, state, ctx), 'onReconnectEnd')
    },
    [ctx],
  )

  // ── Context menus and double-click (plugins, S19) ─────────────────────────
  // With no plugin handling them, the browser's own menu shows as before.

  const handleNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: RFNode) => {
      anyPluginHandled(p => p.onNodeContextMenu?.(event, node, ctx), 'onNodeContextMenu')
    },
    [ctx],
  )

  const handleEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: RFEdge) => {
      anyPluginHandled(p => p.onEdgeContextMenu?.(event, edge, ctx), 'onEdgeContextMenu')
    },
    [ctx],
  )

  const handlePaneContextMenu = useCallback(
    (event: React.MouseEvent | MouseEvent) => {
      anyPluginHandled(p => p.onPaneContextMenu?.(event, ctx), 'onPaneContextMenu')
    },
    [ctx],
  )

  const handleEdgeDoubleClick = useCallback(
    (event: React.MouseEvent, edge: RFEdge) => {
      anyPluginHandled(p => p.onEdgeDoubleClick?.(event, edge, ctx), 'onEdgeDoubleClick')
    },
    [ctx],
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
      // A box or note is not a graph node: it is selected with no primary.
      if (node.id in graphRef.current.nodes) storeSelect(node.id)
      else storeMirrorSelection({ annotationIds: [node.id] })
    },
    [storeSelect, storeMirrorSelection],
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
      // The pane, or the empty inside of a box or note (UX-02).
      const onPane = isEmptyCanvasTarget(event.target)
      if (!onPane) {
        // Dropped on a handle / something else — let handleConnect deal with it.
        pendingWireRef.current = null
        return
      }
      // A wire end being moved (reconnect) dropped on empty canvas: wireOps
      // deletes the wire; no Tab menu.
      if (reconnectingWireId()) { pendingWireRef.current = null; return }
      // A drag from an input that already has its wire: say so instead of
      // offering a node that could not connect (S08).
      const notice = fullPortNotice(graphRef.current, pendingWireRef.current)
      if (notice) {
        pendingWireRef.current = null
        useNodeBuilderStore.getState().showFlash(notice)
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
      tabRequestRef.current = null
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
        // The network on screen (EA-4).
        parent: currentParentId(useNodeBuilderStore.getState(), graphRef.current),
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
      const request = tabRequestRef.current
      tabRequestRef.current = null
      storeBeginBatch(`add ${newNode.name}`)
      try {
        storeAddNode(newNode)

        // Priority 0: a request from code (splice into a wire, connect into
        // a port) does its own wiring.
        // Priority 1: port-drag → empty-space wire takes precedence over
        // the autoWire-from-selection hint. Direction follows the dragged
        // handle: source-handle drag → new node is the target; target-handle
        // drag → new node is the source.
        const pending = pendingWireRef.current
        pendingWireRef.current = null
        if (request?.onCreate) {
          // A faulty onCreate (an extension, or a connect that cannot wire)
          // must never strand the menu open or skip selecting the new node
          // (FC-1): the node stays, the wiring is skipped, the user is told.
          try {
            request.onCreate(id, ctx)
          } catch (err) {
            console.error('nodebuilder: Tab menu onCreate failed', err)
            useNodeBuilderStore.getState().showFlash(err instanceof Error && err.message ? err.message : 'Could not wire the new node')
          }
        } else if (pending && pending.fromNodeId !== id) {
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
    [tabMenuGraph, storeAddNode, storeCommit, storeBeginBatch, storeEndBatch, storeSelect, selectedNodeId, ctx],
  )

  const handleTabMenuClose = useCallback(() => {
    // Closing without creating cancels the pending wire (Esc / outside click).
    pendingWireRef.current = null
    tabRequestRef.current = null
    setTabMenuOpen(false)
    setTabMenuFromWire(false)
    // Give keys back to the canvas.
    focusPane()
  }, [focusPane])
  useLayoutEffect(() => {
    tabMenuCloseRef.current = tabMenuOpen ? handleTabMenuClose : null
  }, [tabMenuOpen, handleTabMenuClose])

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
  // Wire ends can be picked up only while a plugin handles the drop.
  const reconnectable = editable && hasPluginHook('onReconnect')

  return (
    <div
      ref={containerRef}
      className="nodebuilder-root"
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      // The keyboard ContextMenu key fires on the focused root, not the pane:
      // never show the browser menu over the canvas (S19). Fields keep theirs.
      onContextMenu={e => { if (!isTypingTarget(e.target)) e.preventDefault() }}
      // Double-click on empty canvas opens the Tab menu there (foundation
      // 6.1, UX-07); zoomOnDoubleClick is off for this.
      onDoubleClick={e => {
        if (!editable || !(e.target instanceof Element) || !e.target.classList.contains('react-flow__pane')) return
        openTabMenuRef.current({ screen: { x: e.clientX, y: e.clientY } })
      }}
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
        onNodeDragStart={editable ? handleNodeDragStart : undefined}
        onNodeDrag={editable ? handleNodeDrag : undefined}
        onNodeDragStop={editable ? handleNodeDragStop : undefined}
        onSelectionDragStart={editable ? handleSelectionDragStart : undefined}
        onSelectionDrag={editable ? handleSelectionDrag : undefined}
        onSelectionDragStop={editable ? handleSelectionDragStop : undefined}
        onMoveStart={handleMoveStart}
        onMove={handleMove}
        onMoveEnd={handleMoveEnd}
        onConnect={editable ? handleConnect : undefined}
        onConnectStart={editable ? handleConnectStart : undefined}
        onConnectEnd={editable ? handleConnectEnd : undefined}
        onReconnect={reconnectable ? handleReconnect : undefined}
        onReconnectStart={reconnectable ? handleReconnectStart : undefined}
        onReconnectEnd={reconnectable ? handleReconnectEnd : undefined}
        reconnectRadius={18}
        onNodeClick={handleNodeClick}
        onNodeMouseEnter={handleNodeMouseEnter}
        onNodeMouseLeave={handleNodeMouseLeave}
        onNodeContextMenu={handleNodeContextMenu}
        onEdgeClick={editable ? handleEdgeClick : undefined}
        onEdgeContextMenu={handleEdgeContextMenu}
        onEdgeDoubleClick={handleEdgeDoubleClick}
        onPaneClick={handlePaneClick}
        onPaneContextMenu={handlePaneContextMenu}
        onNodesDelete={editable ? handleNodesDelete : undefined}
        onEdgesDelete={editable ? handleEdgesDelete : undefined}
        isValidConnection={editable ? isValidConnection : undefined}
        // Arrow keys would move a focused node in React Flow's copy only; the
        // store never heard of it and the node snapped back.
        disableKeyboardA11y
        // Navigation (foundation 6.1, item 3.D). Editing: a left drag on
        // empty canvas draws a marquee that takes nodes partly inside; the
        // middle button, or Space held with the left one, pans. The read-only
        // view has nothing to select in bulk, so a left drag pans there.
        selectionOnDrag={editable}
        selectionMode={SelectionMode.Partial}
        panOnDrag={editable ? PAN_BUTTONS : true}
        panActivationKeyCode="Space"
        multiSelectionKeyCode={MULTI_SELECT_KEYS}
        // A press that moves less than this is still a click (deselects).
        paneClickDistance={4}
        // The wheel is handled above (gentle zoom, Cmd/Shift pan). A pinch
        // still zooms through React Flow. No zoom-activation key: Cmd+wheel
        // pans instead.
        zoomOnScroll={false}
        panOnScroll={false}
        zoomOnPinch
        zoomActivationKeyCode={null}
        zoomOnDoubleClick={false}
        snapToGrid={editable && snapToGrid}
        snapGrid={SNAP_GRID}
        // Editing places the view itself (saved view or a fit, effect above);
        // the fitView prop would re-fit over a restored view.
        fitView={!editable}
        fitViewOptions={FIT_VIEW_OPTIONS}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
      >
        <CanvasChrome />
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
