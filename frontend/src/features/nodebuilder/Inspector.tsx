/**
 * The Inspector (F272, specs S14 and S15): Houdini's Parameters pane.
 *
 * A resizable panel in the `rightPanel` slot (registered by
 * plugins/inspector.ts). It shows what is selected:
 * - one node: header (name, type, path, flags), the catalog description,
 *   then the registered sections (Parameters, Stream, Diagnostics; W7 adds
 *   Code through `registerInspectorSection`, see inspector/sections.ts);
 *   a subnet also gets "Save as asset…" and "Promote to palette…" under its
 *   header (W6, S41), and a network's Parameters section holds its
 *   Promoted list (PromotedSection.tsx);
 * - several nodes: the count, Bulk buttons and Shared parameters;
 * - one wire: its ends, ports, reads and the source stream;
 * - nothing: the legend, flags and keys (InspectorLegend.tsx).
 *
 * It reads the store (and, in the read-only view, the graph on screen from
 * screen.ts), never React Flow, so it renders while the canvas is hidden.
 * It subscribes to the selection and the selected node's own data (narrow
 * selectors, memoized sections), so a pan, a zoom, an edit to another node
 * or a drag of the resize handle never re-renders the node view. It never takes focus on a
 * selection change (that would steal the canvas keys); Esc in any of its
 * fields hands focus back to the canvas.
 *
 * Panel state (open, width, sections) lives in inspector/state.ts and
 * persists in `nb.inspector`, never in the graph file. Below 1100px the
 * panel floats over the canvas; that overlay's open state is per session
 * and starts closed (UX-08).
 */

import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import type { Graph } from '../../api/nodebuilder'
import { useNodeBuilderStore } from './store'
import { useScreenGraph } from './screen'
import { catalogEntry } from './streamLabels'
import { useNodeDiagnostics } from './useDiagnostics'
import { isUnsupportedNode } from './nodes/unsupported'
import InspectorLegend from './InspectorLegend'
import { openSaveAsset } from './assetUi'
import { Button } from './ui/Button'
import { NodeHeader } from './inspector/NodeHeader'
import { AssetLifecycleRows } from './inspector/AssetLifecycleRows'
import { MultiView } from './inspector/MultiView'
import { WireView } from './inspector/WireView'
import { InspectorSectionShell } from './inspector/Section'
import { useInspectorSections, type InspectorSectionProps } from './inspector/sections'
import {
  clampInspectorWidth,
  defaultInspectorWidth,
  INSPECTOR_OVERLAY_WIDTH,
  isOverlayMode,
  setInspectorWidth,
  toggleInspector,
  useAppWidth,
  useInspectorUi,
} from './inspector/state'
import {
  focusCanvas,
  InspectorSourceContext,
  useInspectorEditable,
  useInspectorGraph,
  useInspectorSelect,
  type InspectorSource,
} from './inspector/util'
// The built-in node sections register themselves on load.
import './inspector/NodeSections'
import './inspector/inspector.css'

/** Keyboard resize step on the handle (S14 accessibility). */
const KEY_RESIZE_STEP = 16

function isField(el: EventTarget | null): boolean {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement
}

/**
 * Presses that do not close the overlay Inspector (UX-08): on a node or a
 * wire (selecting one is the reason to look at the Inspector), on the
 * toolbar toggle (its own click decides), and inside a menu or dialog.
 */
const KEEP_OVERLAY_OPEN = [
  '[data-nb-inspector-toggle]',
  '.react-flow__node',
  '.react-flow__edge',
  '.react-flow__handle',
  '[role="menu"]',
  '[role="dialog"]',
].join(', ')

/** A section's body or count, memoized on its props (IP-2). */
const SectionPart = memo(function SectionPart({ C, ...props }: InspectorSectionProps & { C: ComponentType<InspectorSectionProps> }) {
  return <C {...props} />
})

/**
 * One node: header, description, then the registered sections. Subscribes
 * to this node and to whether it has a wire in, not to the whole graph.
 */
function NodeView({ nodeId, editable }: { nodeId: string; editable: boolean }) {
  const node = useInspectorSelect(g => g?.nodes[nodeId] ?? null)
  const wiredIn = useInspectorSelect(g => g?.wires.some(w => w.to === nodeId) ?? false)
  const diagnostics = useNodeDiagnostics(nodeId)
  const sections = useInspectorSections()
  if (!node) return null
  // S13: an unsupported node is inert: no rename, no param edits, no flags.
  const canEdit = editable && !isUnsupportedNode(node.type, wiredIn, diagnostics)
  const entry = catalogEntry(node.type)
  const props: InspectorSectionProps = { nodeId, node, editable: canEdit }
  return (
    <div data-testid="nb-inspector-node">
      <NodeHeader nodeId={nodeId} node={node} editable={canEdit} />
      {entry?.desc && (
        <div className="nb-insp-desc" title={entry.desc}>{entry.desc}</div>
      )}
      {node.type === 'subnet' && node.asset_ref && (
        <AssetLifecycleRows nodeId={nodeId} node={node} editable={canEdit} diagnostics={diagnostics} />
      )}
      {canEdit && node.type === 'subnet' && (
        <div className="nb-insp-desc" style={{ display: 'flex', gap: 6 }} data-testid="nb-inspector-asset-actions">
          <Button onClick={() => openSaveAsset({ subnetId: nodeId, palette: false })}>Save as asset…</Button>
          <Button onClick={() => openSaveAsset({ subnetId: nodeId, palette: true })}>Promote to palette…</Button>
        </div>
      )}
      {sections.filter(s => !s.when || s.when(props)).map(s => (
        <InspectorSectionShell
          key={s.id}
          id={s.id}
          title={s.title}
          count={s.Count ? <SectionPart C={s.Count} {...props} /> : undefined}
        >
          <SectionPart C={s.Component} {...props} />
        </InspectorSectionShell>
      ))}
    </div>
  )
}

/** The legend view reads the whole graph (counts per category). */
function LegendView() {
  const { graph, editable } = useInspectorGraph()
  return <InspectorLegend graph={graph} editable={editable} />
}

type View =
  | ['multi', string[]]
  | ['node', string]
  | ['wire', string]
  | ['wires', number]
  | ['legend']

/** Which view the selection asks for, as a string (so an unrelated edit re-renders nothing). */
function viewKey(
  graph: Graph | null,
  nodeIds: readonly string[],
  primary: string | null,
  wireIds: readonly string[],
): string {
  if (graph) {
    const present = nodeIds.filter(id => id in graph.nodes)
    if (present.length > 1) return JSON.stringify(['multi', present])
    const one = present[0] ?? (primary && primary in graph.nodes ? primary : null)
    if (one) return JSON.stringify(['node', one])
    if (wireIds.length === 1 && graph.wires.some(w => w.id === wireIds[0])) return JSON.stringify(['wire', wireIds[0]])
    if (wireIds.length > 1) return JSON.stringify(['wires', wireIds.length])
  }
  return '["legend"]'
}

/**
 * Picks the view for the current selection. Memoized with no props: it
 * re-renders from its own subscriptions only (the selection and the view
 * key), never from the panel's width or open state (IP-3).
 */
const InspectorBody = memo(function InspectorBody() {
  const editable = useInspectorEditable()
  const primary = useNodeBuilderStore(s => s.selectedNodeId)
  const nodeIds = useNodeBuilderStore(s => s.selectedNodeIds)
  const wireIds = useNodeBuilderStore(s => s.selectedWireIds)
  const key = useInspectorSelect(g => viewKey(g, nodeIds, primary, wireIds))
  const view = useMemo(() => JSON.parse(key) as View, [key])

  switch (view[0]) {
    case 'multi':
      return <MultiView nodeIds={view[1]} editable={editable} />
    case 'node':
      return <NodeView nodeId={view[1]} editable={editable} />
    case 'wire':
      return <WireView wireId={view[1]} editable={editable} />
    case 'wires':
      return (
        <div className="nb-insp-head">
          <span className="nb-insp-head__glyph nb-insp-head__glyph--plain" aria-hidden="true">→</span>
          <span className="nb-insp-head__title" data-testid="nb-inspector-count">{view[1]} wires</span>
        </div>
      )
    default:
      return <LegendView />
  }
})

export default function Inspector() {
  const appWidth = useAppWidth()
  const overlay = isOverlayMode(appWidth)
  const open = useInspectorUi(s => (overlay ? s.overlayOpen : s.open))
  const storedWidth = useInspectorUi(s => s.width)
  // Width when a handle drag started; the drag itself writes the panel's
  // style directly (no render per pointer move, IP-3).
  const [dragStart, setDragStart] = useState<number | null>(null)
  const dragRef = useRef<{ x: number; w: number; last: number } | null>(null)
  const panelRef = useRef<HTMLElement>(null)

  // The graph source for everything inside: the store while it has a graph,
  // else the read-only graph on screen (screen.ts, reactive: FC-4, EA-9).
  // Inside a locked asset the canvas draws the asset's nodes under composite
  // ids that only the screen graph has: when the primary selection is one of
  // them, show the screen graph, read-only (FE-07).
  const storeHasGraph = useNodeBuilderStore(s => s.graph != null)
  const screenGraph = useScreenGraph().graph
  const primaryOffStore = useNodeBuilderStore(s => s.graph != null && !!s.selectedNodeId && !(s.selectedNodeId in s.graph.nodes))
  const fromScreen = !storeHasGraph || (primaryOffStore && !!screenGraph)
  const source = useMemo<InspectorSource | null>(
    () => (fromScreen ? { graph: screenGraph } : null),
    [fromScreen, screenGraph],
  )

  const width = overlay
    ? INSPECTOR_OVERLAY_WIDTH
    : dragStart ?? storedWidth ?? defaultInspectorWidth(appWidth)

  // Overlay mode closes on a pointer-down outside it, except on a node or a
  // wire (UX-08), and on Esc pressed outside a text field.
  useEffect(() => {
    if (!overlay || !open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null
      if (!t || panelRef.current?.contains(t)) return
      if (t.closest?.(KEEP_OVERLAY_OPEN)) return
      toggleInspector(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const t = e.target as Element | null
      // Inside the panel its own onKeyDown decides; a field elsewhere keeps its Esc.
      if (t && panelRef.current?.contains(t)) return
      if (isField(t)) return
      toggleInspector(false)
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [overlay, open])

  if (!open) return null

  const onHandleDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (overlay || e.button !== 0) return
    e.preventDefault()
    dragRef.current = { x: e.clientX, w: width, last: width }
    try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* jsdom */ }
    setDragStart(width)
  }
  const onHandleMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current
    if (!d) return
    // The handle is on the left edge: moving left makes the panel wider.
    const next = clampInspectorWidth(d.w + (d.x - e.clientX))
    if (next === d.last) return
    d.last = next
    const panel = panelRef.current
    if (panel) panel.style.width = `${next}px`
    e.currentTarget.setAttribute('aria-valuenow', String(next))
  }
  const onHandleUp = () => {
    const d = dragRef.current
    if (!d) return
    dragRef.current = null
    setInspectorWidth(d.last)
    setDragStart(null)
  }
  const onHandleKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    setInspectorWidth(width + (e.key === 'ArrowLeft' ? KEY_RESIZE_STEP : -KEY_RESIZE_STEP))
  }

  const onKeyDown = (e: ReactKeyboardEvent<HTMLElement>) => {
    if (e.key !== 'Escape') return
    if (isField(e.target)) {
      // The field already reverted its own text; hand the keys back to the canvas.
      ;(e.target as HTMLElement).blur()
      focusCanvas()
      return
    }
    if (overlay) {
      toggleInspector(false)
      focusCanvas()
    }
  }

  return (
    <aside
      ref={panelRef}
      role="complementary"
      aria-label="Inspector"
      data-testid="nb-inspector"
      className={`nb-insp${overlay ? ' nb-insp--overlay' : ''}${dragStart !== null ? ' nb-insp--resizing' : ''}`}
      style={{ width }}
      onKeyDown={onKeyDown}
    >
      {!overlay && (
        <div
          className="nb-insp__handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the Inspector"
          aria-valuenow={width}
          tabIndex={0}
          data-testid="nb-inspector-handle"
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
          onDoubleClick={() => setInspectorWidth(null)}
          onKeyDown={onHandleKey}
        />
      )}
      <div className="nb-insp__scroll">
        <InspectorSourceContext.Provider value={source}>
          <InspectorBody />
        </InspectorSourceContext.Provider>
      </div>
    </aside>
  )
}
