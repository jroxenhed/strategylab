/**
 * BaseNode — shared layout primitive for all custom node renderers.
 *
 * Renders: 3px left stripe, 16×16 icon chip, title, subtitle, attribute
 * chips, display-flag glow, bypass-flag dim+dot, and the ports.
 *
 * Diagnostics (spec S05): the node reads its own problems from the shared
 * diagnostics store and shows a DiagnosticBadge after the subtitle. Any error
 * turns the card border red. The node id comes from React Flow's node
 * context, or from the `nodeId` prop outside a flow (tests).
 *
 * Ports (spec S08, plan D4): the input handles come from the catalog
 * PortsSpec of the node's type (`portsOf` in streamLabels.ts), never from the
 * type name: `a` and `b` on a comparison, `in0, in1, ...` plus one dashed
 * spare on a dynamic node (logic, merge). Handle ids are `in<k>`, the same as
 * `GraphWire.to_port`, and the one output handle is `out`. `hasInput` /
 * `hasOutput` false still hide a side (a Ticker has no input, terminals no
 * output). Hover looks are CSS only (stream.css); the port's hover text is
 * filled in when the pointer arrives, without a React render.
 *
 * Chips (spec S10, foundation 4.5): a node type with `write` params shows
 * one renamable `+@name` chip per write param; other nodes show the fixed
 * writes the renderer passes. Read chips show what the node reads; when the
 * node's attribute pickers are on screen they already say it, so the read
 * chips are left out. Over 6 chips, the first 5 show and a `+N` chip opens
 * the full list.
 *
 * Flags (spec S16, foundation 4.6 and 4.9): in edit mode a column of two
 * dots hangs off the right edge: display (blue) on nodes with an output,
 * bypass (amber) on every node but Tickers and terminals. A click changes
 * that one node's flag as one undo step and never moves the selection.
 * The dots show when lit, on hover or while selected (CSS only). A display
 * node gets a blue inner outline; a bypassed node a second amber bar, a dim
 * body and the type label `bypassed`.
 *
 * Unsupported nodes (spec S13): a node the compiler cannot run draws the
 * UnsupportedNode card instead (see nodes/unsupported.ts for the rule).
 *
 * Port hover text (S08) shows after 400 ms through nodes/hoverTip.ts,
 * written straight to the page, so hovering never renders React.
 *
 * Sparkline slot (spec S26, W4 item 4.C): when the last `/preview` has data
 * for this node, the body reserves a 28px slot between the params and the
 * chips. The node reads only its own preview entry (`useNodePreview`), so a
 * new preview re-renders only the nodes whose data changed. The drawing is
 * done by the one SparklineLayer canvas; the slot registers its element
 * with `useSparklineSlot` and carries the hover text, the click (select the
 * node, open the Data Sheet) and a text summary for screen readers. No slot
 * on a bypassed node or below zoom 0.5.
 *
 * Code (W7, specs S45, S46, S48): a node with a code block shows the
 * `code · 3 lines` drawer under its rows (static text, never Monaco), its
 * spare params after the built-in rows (at most 4 rows in all, then
 * `+N more in Inspector`), and the code's writes as purple chips after its
 * own writes. Reads and writes come from `parse_code`, never from the text.
 *
 * All color references use CSS custom properties from tokens.css
 * scoped to .nodebuilder-root.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Handle,
  Position,
  useNodeConnections,
  useNodeId,
  useNodesData,
  useStore,
  useUpdateNodeInternals,
  type ReactFlowState,
} from '@xyflow/react'
import type { GraphNode } from '../../../api/nodebuilder'
import { CATS, type CatKey } from '../categories'
import { useNodeBuilderStore } from '../store'
import { focusDiagnosticWire, getStreams, useNodeDiagnostics } from '../useDiagnostics'
import {
  inputPortTitle,
  outputPortTitle,
  portLeft,
  portsOf,
  readParamsOf,
  readsOf,
  readsThroughWire,
  writeParamsOf,
  writesOf,
  type WriteSlot,
} from '../streamLabels'
import { Popover } from '../ui/Popover'
import { clickBypassFlag, clickDisplayFlag } from '../commands/flags'
import { flagProblemForType } from '../operations'
import { DiagnosticBadge } from './DiagnosticBadge'
import { endHoverTip, startHoverTip } from './hoverTip'
import { requestRename } from '../inspector/state'
import { ParamRows, rowsFor } from './ParamRow'
import { NodeCodeDrawer } from '../code/CodeDrawer'
import { SpareParamRows } from '../code/SpareParams'
import { CODE_SLOT, useCodeEnabled, useCodeStore, useLastOkParse } from '../code/codeStore'
import { visibleParams, WRANGLE_TYPE } from '../code/codeOps'
import { paramSpecsOf } from '../streamLabels'
import { UnsupportedNode } from './UnsupportedNode'
import { isUnsupportedNode } from './unsupported'
import { WriteChip } from './WriteChip'
import {
  SPARKLINE_HEIGHT,
  SPARKLINE_MIN_ZOOM,
  sparklineSummary,
  sparklineTooltip,
  useNodePreview,
  useSparklineSlot,
} from './Sparkline'
import { getCommand, runCommand } from '../commands'
import '../stream.css'
import './node.css'

// ---------------------------------------------------------------------------
// Attr pill (a plain read chip)
// ---------------------------------------------------------------------------
interface PillProps {
  label: string
  /** If true, renders with category-tinted background (write attr). */
  write?: boolean
  catColor?: string
}

export function AttrPill({ label, write = false, catColor }: PillProps) {
  if (write) {
    return (
      <span className="nb-chip nb-chip--write nb-chip--readonly" style={catColor ? { color: catColor } : undefined}>
        {label}
      </span>
    )
  }
  return <span className="nb-chip">{label}</span>
}

/**
 * Double-click on a node's header: select that node alone and start the
 * inline rename (the Inspector's name field, same as F2). Unsupported nodes
 * draw UnsupportedNode instead of this header, so they never rename (S13).
 */
function renameFromHeader(e: React.MouseEvent, nodeId: string): void {
  e.stopPropagation()
  const s = useNodeBuilderStore.getState()
  if (!s.graph || s.graph.readOnly || !(nodeId in s.graph.nodes)) return
  if (s.selectedNodeIds.length !== 1 || s.selectedNodeIds[0] !== nodeId) {
    s.setSelection({ nodeIds: [nodeId], primary: nodeId })
  }
  requestRename(nodeId)
}

// ---------------------------------------------------------------------------
// BaseNode props
// ---------------------------------------------------------------------------
export interface BaseNodeData extends Record<string, unknown> {
  backendType: string
  catalog: import('../catalog').NodeCatalogEntry | null
  params: Record<string, unknown>
  display: boolean
  bypass: boolean
  /** The node's name (graph `node.name`); shown as the title's hover text. */
  name?: string
  /** The node's path, e.g. `/rsi_fast`. */
  nodePath: string
  /** True when the canvas is in editable mode. */
  editable?: boolean
  /**
   * The whole graph node (EA-6). A renderer that needs a node field the
   * list above does not copy (parent W5, promoted/locked W6, code and
   * spare params W7) reads it here: in the read-only view the graph is not
   * in the store. The card data is rebuilt whenever the node object changes.
   */
  node?: import('../../../api/nodebuilder').GraphNode
}

interface BaseNodeProps {
  /** Category key — drives stripe + chip color + glyph. */
  cat: CatKey
  /** Node title (e.g. "RSI(14)", "AAPL", "AND"). */
  title: string
  /** Optional subtitle rendered right-aligned in the header. */
  subtitle?: string
  /** Read attributes (gray chips), for node types with no read params. */
  reads?: readonly string[]
  /** Fixed write attributes, for node types with no `write` params. */
  writes?: readonly string[]
  /** Is this node display-flagged? (blue inner outline, lit blue dot). */
  display?: boolean
  /** Is this node bypassed? (amber bar, dim body, lit amber dot). */
  bypass?: boolean
  /** Width of the node in px. */
  width?: number
  /** Optional extra body content (param rows, etc.). */
  children?: React.ReactNode
  /** True in edit mode: ports connect, chips rename, pickers open. */
  editable?: boolean
  /** Render the input ports. False for source nodes like Ticker. */
  hasInput?: boolean
  /** Render the output port. False for terminals like Entry/Exit. */
  hasOutput?: boolean
  /** Node id for diagnostics. Defaults to the React Flow node this renders in. */
  nodeId?: string
  /** Read chips drawn in the error color (a Wrangle's missing reads, S46). */
  missingReads?: ReadonlySet<string>
  /** Text shown in the chip row when there are no chips (a Wrangle's `writes nothing`). */
  emptyChipsText?: string
  /** The card's accessible name (a Wrangle names its writes, S46). */
  ariaLabel?: string
}

/** Card border when the node has an error (ui-ux-spec 4.8). */
const ERROR_BORDER = '1px solid rgba(248, 113, 113, 0.6)'

/** More chips than this and the row folds (foundation 4.5). */
const MAX_CHIPS = 6

const NO_PARAMS: Record<string, unknown> = {}

type Chip = { kind: 'read'; name: string } | { kind: 'write'; slot: WriteSlot } | { kind: 'code'; name: string }

/** Most rows a node card shows, built-in plus spare (S48). */
const MAX_NODE_ROWS = 4

/** True while the canvas is zoomed in enough to show sparklines (a boolean, so zooming re-renders only at the threshold). */
const selectSparkZoom = (s: ReactFlowState) => s.transform[2] >= SPARKLINE_MIN_ZOOM

/**
 * A click on the sparkline: select this node alone and open the Data Sheet
 * on it (S26, the `S` key's behavior). The sheet follows the selection, so
 * it is only toggled when it is closed.
 */
function openSheetOn(nodeId: string): void {
  const s = useNodeBuilderStore.getState()
  if (s.selectedNodeIds.length !== 1 || s.selectedNodeIds[0] !== nodeId) {
    s.setSelection({ nodeIds: [nodeId], primary: nodeId })
  }
  const toggle = getCommand('panels.toggleSheet')
  if (toggle && !toggle.checked?.(useNodeBuilderStore.getState())) runCommand('panels.toggleSheet')
}

export function BaseNode({
  cat,
  title,
  subtitle,
  reads = [],
  writes = [],
  display = false,
  bypass = false,
  width = 158,
  children,
  editable = false,
  hasInput = true,
  hasOutput = true,
  nodeId: nodeIdProp,
  missingReads,
  emptyChipsText,
  ariaLabel,
}: BaseNodeProps) {
  const flowNodeId = useNodeId()
  const nodeId = nodeIdProp ?? flowNodeId
  const diagnostics = useNodeDiagnostics(nodeId)
  const hasError = diagnostics.some(d => d.severity === 'error')
  const select = useNodeBuilderStore(s => s.select)
  const catEntry = CATS[cat] ?? CATS.indicator
  const catColor = catEntry.color
  const glyph = catEntry.glyph
  // Category color and tint for the ports and chips (stream.css reads them).
  const catVars = { '--cat': catColor, '--tint': `var(--nb-tint-${cat})` } as React.CSSProperties

  // The node's type and params, from React Flow's node data.
  const nodeData = useNodesData(nodeId ?? '')?.data as BaseNodeData | undefined
  const nodeType = nodeData?.backendType
  const params = nodeData?.params ?? NO_PARAMS
  const node = useMemo(
    () => ({ type: nodeType ?? '', params: params as GraphNode['params'] }),
    [nodeType, params],
  )

  // ── Ports ────────────────────────────────────────────────────────────────
  const connections = useNodeConnections({ id: nodeId ?? undefined, handleType: 'target' })
  // Port id -> the node wired into it. A string key keeps the memo stable.
  const wiredKey = connections
    .map(c => `${c.targetHandle ?? ''}<${c.source}`)
    .sort()
    .join('|')
  const sourceByPort = useMemo(() => {
    const m = new Map<string, string>()
    for (const part of wiredKey ? wiredKey.split('|') : []) {
      const [port, source] = part.split('<')
      if (port) m.set(port, source)
    }
    return m
  }, [wiredKey])
  const ports = useMemo(
    () => (hasInput ? portsOf(nodeType, sourceByPort.keys()) : []),
    [hasInput, nodeType, sourceByPort],
  )
  const portsKey = ports.map(p => p.id).join(',')
  const updateNodeInternals = useUpdateNodeInternals()
  // React Flow measures handles once; ports that come and go (the spare of
  // a dynamic node) need a fresh measure so wires attach to them.
  const firstPorts = useRef(true)
  useEffect(() => {
    if (firstPorts.current) { firstPorts.current = false; return }
    if (nodeId) updateNodeInternals(nodeId)
  }, [portsKey, nodeId, updateNodeInternals])

  const portErrors = useMemo(() => {
    const m = new Map<string, string>()
    for (const d of diagnostics) {
      if (d.code === 'missing_input' && d.port && !m.has(d.port)) m.set(d.port, d.message)
    }
    return m
  }, [diagnostics])

  // The hover text needs the latest streams, so it is worked out when the
  // pointer arrives and shown by hoverTip (no React render on hover).
  const showPortTip = (portId: string, label: string) => (e: React.PointerEvent<HTMLDivElement>) => {
    const error = portErrors.get(portId)
    if (error) { startHoverTip(e.currentTarget, error, 'above'); return }
    const from = sourceByPort.get(portId)
    let readsHere: string[] = []
    if (from && nodeId) {
      const graph = { nodes: { [nodeId]: { ...node, id: nodeId } as GraphNode }, wires: [] }
      readsHere = readsThroughWire({ from, to: nodeId }, graph, getStreams())
    }
    startHoverTip(e.currentTarget, inputPortTitle(label, readsHere), 'above')
  }

  // ── Unsupported node (S13) ───────────────────────────────────────────────
  // Every hook above runs either way, so the hook order never changes.
  const connectedPorts = useMemo(() => [...sourceByPort.keys()], [sourceByPort])
  const unsupported = !!nodeId && isUnsupportedNode(nodeType, connectedPorts.length > 0, diagnostics)

  // ── Chips ────────────────────────────────────────────────────────────────
  const hasWriteParams = writeParamsOf(nodeType).length > 0
  const readParams = readParamsOf(nodeType)
  const writeSlots: WriteSlot[] = hasWriteParams
    ? writesOf(node)
    : writes.map(name => ({ param: null, name }))
  // Editable nodes whose renderer draws no param rows still need their
  // pickers (a logic node's `terms`), so BaseNode draws them itself.
  const ownPickers = editable && !children && readParams.length > 0 && !!nodeId
  const pickersShown = editable && readParams.length > 0 && (!!children || ownPickers)
  const readChips = pickersShown ? [] : readParams.length > 0 ? readsOf(node) : [...reads]
  // ── Code (W7) ────────────────────────────────────────────────────────────
  const graphNode = nodeData?.node
  const nodeCode = graphNode?.code ?? null
  const isWrangle = nodeType === WRANGLE_TYPE
  const codeOn = useCodeEnabled()
  const codeParse = useLastOkParse(nodeCode && !isWrangle ? nodeId : null, CODE_SLOT)
  const codeWrites = codeParse && nodeCode
    ? codeParse.res.writes.map(w => w.name).filter(n => !writeSlots.some(s => s.name === n))
    : []
  const spareCount = editable ? graphNode?.spare_params?.length ?? 0 : 0
  const builtInRows = !editable ? 0
    : children ? rowsFor(
      visibleParams(params, new Set(paramSpecsOf(nodeType).map(s => s.name)), graphNode),
      paramSpecsOf(nodeType),
      false,
    ).length
      : readParams.length
  // The drawer row shows when the node has code, or the user added a block this session (S45).
  const codeAdded = useCodeStore(st => (nodeId ? !!st.addedCode[nodeId] : false))
  const showDrawer = !isWrangle && !!nodeId && (!!nodeCode || (editable && codeAdded))

  const chips: Chip[] = [
    ...readChips.map(name => ({ kind: 'read' as const, name })),
    ...writeSlots.map(slot => ({ kind: 'write' as const, slot })),
    ...codeWrites.map(name => ({ kind: 'code' as const, name })),
  ]
  const folded = chips.length > MAX_CHIPS
  const shownChips = folded ? chips.slice(0, MAX_CHIPS - 1) : chips
  // The `+N` button the full chip list hangs from, while it is open.
  const [moreAnchor, setMoreAnchor] = useState<HTMLButtonElement | null>(null)
  const moreOpen = moreAnchor != null

  const renderChip = (c: Chip, i: number) => c.kind === 'read'
    ? <span key={`r-${i}-${c.name}`} className={`nb-chip${missingReads?.has(c.name) ? ' nb-chip--missing' : ''}`}>{c.name}</span>
    : c.kind === 'code'
      ? <span key={`c-${i}-${c.name}`} className="nb-chip nb-chip--write nb-chip--readonly nb-chip--code" data-testid={nodeId ? `nb-code-write-${nodeId}-${c.name}` : undefined}>+{c.name}</span>
    : nodeId
      ? <WriteChip key={`w-${i}-${c.slot.param ?? c.slot.name}`} nodeId={nodeId} param={c.slot.param} name={c.slot.name} editable={editable} />
      : <AttrPill key={`w-${i}`} label={`+${c.slot.name}`} write />

  const hasChips = chips.length > 0
  const readParamValues = ownPickers
    ? Object.fromEntries(readParams.map(p => [p.name, params[p.name] ?? null]))
    : null

  const containerStyle: React.CSSProperties = {
    ...catVars,
    width,
    fontFamily: 'var(--nb-font-sans)',
    background: 'var(--nb-bg-node)',
    // One shorthand for both states; never mixed with borderColor (bug 22).
    border: hasError ? ERROR_BORDER : '1px solid var(--nb-border)',
    borderRadius: 'var(--nb-radius-node)',
    position: 'relative',
    overflow: 'hidden',
    // Display: a blue inner outline (foundation 4.9). With selection, the
    // selection ring shows outside it.
    outline: display ? '1px solid var(--nb-flag-display)' : undefined,
    outlineOffset: display ? -1 : undefined,
  }

  // Which flag dots this node offers (foundation 4.6). Read-only graphs draw none.
  const showDisplayDot = editable && !!nodeId && hasOutput && flagProblemForType(nodeType, 'display') == null
  // A stray bypass on a node that cannot have it still shows, so it can be cleared.
  const showBypassDot = editable && !!nodeId && (bypass || flagProblemForType(nodeType, 'bypass') == null)
  // A flag click must not start a drag or change the selection.
  const stopPointer = (e: React.SyntheticEvent) => { e.stopPropagation(); endHoverTip() }
  const flagTip = (text: string) => (e: React.PointerEvent<HTMLButtonElement>) =>
    startHoverTip(e.currentTarget, text, 'right')

  // A badge click goes where the node's first problem is: its wire when the
  // problem is about a wire (spec S05), else the node.
  const activateBadge = () => {
    if (!nodeId) return
    if (diagnostics[0] && focusDiagnosticWire(diagnostics[0])) return
    select(nodeId)
  }

  const showAllPortLabels = ports.length >= 2

  // ── Sparkline slot (S26) ─────────────────────────────────────────────────
  const preview = useNodePreview(nodeId)
  const sparkZoom = useStore(selectSparkZoom)
  const showSpark = !!preview && !!nodeId && !bypass && sparkZoom
  const sparkRef = useSparklineSlot(showSpark ? nodeId : null, catColor)

  if (unsupported && nodeId && nodeType) {
    return (
      <UnsupportedNode
        nodeId={nodeId}
        nodeType={nodeType}
        fallbackName={title}
        params={params}
        connectedPorts={connectedPorts}
        diagnostics={diagnostics}
        editable={editable}
      />
    )
  }

  return (
    <>
      {/* Input ports (top edge) */}
      {ports.map(p => {
        const left = portLeft(p.index, ports.length)
        let cls = 'nb-port nb-port--in'
        if (p.connected) cls += ' nb-port--connected'
        if (p.spare) cls += ' nb-port--spare'
        if (portErrors.has(p.id)) cls += ' nb-port--error'
        return (
          <Handle
            key={p.id}
            type="target"
            id={p.id}
            position={Position.Top}
            isConnectable={editable}
            className={cls}
            style={{ ...catVars, left }}
            data-testid={nodeId ? `nb-port-${nodeId}-${p.id}` : undefined}
            aria-label={`input ${p.label}`}
            onPointerEnter={showPortTip(p.id, p.label)}
            onPointerLeave={endHoverTip}
            onPointerDown={endHoverTip}
          />
        )
      })}
      {ports.map(p => (
        <span
          key={`label-${p.id}`}
          className={`nb-port-label${showAllPortLabels ? '' : ' nb-port-label--hover'}`}
          style={{ left: portLeft(p.index, ports.length) }}
          aria-hidden="true"
        >
          {p.label}
        </span>
      ))}

      {/* A named card is a group, so its aria-label is exposed (UX-10). */}
      <div className="nb-node-card" style={containerStyle} aria-label={ariaLabel} role={ariaLabel ? 'group' : undefined}>
        {/* 3px left stripe */}
        <div style={{
          position: 'absolute',
          left: 0,
          top: 0,
          bottom: 0,
          width: 3,
          background: catColor,
          borderRadius: '5px 0 0 5px',
        }} />
        {/* Bypass: a second 3px amber bar right of the stripe */}
        {bypass && <div className="nb-bypass-bar" data-testid={nodeId ? `nb-bypass-bar-${nodeId}` : undefined} />}

        {/* Header. Double-click renames inline (foundation 6.1, UX-07). */}
        <div
          data-testid={nodeId ? `nb-node-header-${nodeId}` : undefined}
          onDoubleClick={editable && nodeId ? e => renameFromHeader(e, nodeId) : undefined}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 10px 5px 11px',
            minHeight: 24,
          }}
        >
          {/* Icon chip */}
          <div style={{
            width: 16,
            height: 16,
            borderRadius: 3,
            background: catColor,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}>
            <span style={{
              fontFamily: 'var(--nb-font-mono)',
              fontWeight: 700,
              fontSize: 9,
              color: 'var(--nb-bg)',
              lineHeight: 1,
              userSelect: 'none',
            }}>
              {glyph}
            </span>
          </div>

          {/* Title */}
          <span style={{
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--nb-text)',
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            lineHeight: '16px',
          }}
            title={nodeData?.nodePath ? `${title} · ${nodeData.nodePath}` : undefined}
            data-testid={nodeId ? `nb-node-title-${nodeId}` : undefined}
          >
            {title}
          </span>

          {/* Subtitle (the type label). A bypassed node reads `bypassed`. */}
          {(subtitle || bypass) && (
            <span
              data-testid={nodeId ? `nb-node-type-${nodeId}` : undefined}
              style={{
                fontFamily: 'var(--nb-font-mono)',
                fontSize: 10,
                color: bypass ? 'var(--nb-flag-bypass)' : 'var(--nb-text-muted)',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                lineHeight: '16px',
              }}
            >
              {bypass ? 'bypassed' : subtitle}
            </span>
          )}

          {/* Diagnostics badge, only when this node has a problem */}
          {nodeId && diagnostics.length > 0 && (
            <DiagnosticBadge
              nodeId={nodeId}
              diagnostics={diagnostics}
              onActivate={activateBadge}
            />
          )}
        </div>

        {/* Body — param rows, then chips */}
        {(hasChips || children || readParamValues || showSpark || showDrawer || spareCount > 0 || emptyChipsText) && (
          <div
            className="nb-node-body"
            data-testid={nodeId ? `nb-node-body-${nodeId}` : undefined}
            style={{
              padding: '4px 10px 7px 12px',
              borderTop: '1px solid var(--nb-border-subtle)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
              // Bypassed: the body dims, the header keeps full opacity (4.9).
              opacity: bypass ? 0.45 : undefined,
            }}
          >
            {children}
            {readParamValues && nodeId && (
              <ParamRows nodeId={nodeId} params={readParamValues} specs={readParams} />
            )}
            {spareCount > 0 && nodeId && (
              <SpareParamRows nodeId={nodeId} max={Math.max(0, MAX_NODE_ROWS - builtInRows)} editable={editable} />
            )}
            {showDrawer && nodeId && <NodeCodeDrawer nodeId={nodeId} code={nodeCode} codeOff={!codeOn} />}
            {showSpark && preview && nodeId && (
              <div
                ref={sparkRef}
                className="nb-sparkline-slot"
                data-testid={`nb-sparkline-slot-${nodeId}`}
                data-kind={preview.kind}
                style={{ height: SPARKLINE_HEIGHT, flexShrink: 0, cursor: 'pointer' }}
                onPointerEnter={e => startHoverTip(e.currentTarget, sparklineTooltip(preview), 'below')}
                onPointerLeave={endHoverTip}
                onPointerDown={endHoverTip}
                onClick={() => openSheetOn(nodeId)}
              >
                <span className="nb-sr-only">{sparklineSummary(preview)}</span>
              </div>
            )}
            {!hasChips && emptyChipsText && (
              <span className="nb-chips-none" data-testid={nodeId ? `nb-chips-none-${nodeId}` : undefined}>{emptyChipsText}</span>
            )}
            {hasChips && (
              <div
                className="nb-chip-row"
                style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: children || readParamValues ? 4 : 0 }}
              >
                {shownChips.map(renderChip)}
                {folded && (
                  <button
                    type="button"
                    className="nb-chip nb-chip--more nodrag"
                    aria-haspopup="dialog"
                    aria-expanded={moreOpen}
                    aria-label={`${chips.length - shownChips.length} more attributes`}
                    onPointerDown={e => e.stopPropagation()}
                    onClick={e => { const el = e.currentTarget; setMoreAnchor(a => (a ? null : el)) }}
                  >
                    +{chips.length - shownChips.length}
                  </button>
                )}
                {/* A Wrangle with read chips and no writes still says so (S46, UX-5). */}
                {emptyChipsText && (
                  <span className="nb-chips-none" data-testid={nodeId ? `nb-chips-none-${nodeId}` : undefined}>{emptyChipsText}</span>
                )}
              </div>
            )}
          </div>
        )}

      </div>

      {/* Flag dots, outside the right edge (S16). Not tab stops on the canvas. */}
      {(showDisplayDot || showBypassDot) && nodeId && (
        <div className="nb-flags nodrag nopan">
          {showDisplayDot && (
            <button
              type="button"
              tabIndex={-1}
              className="nb-flag nb-flag--display nodrag nopan"
              data-testid={`nb-flag-display-${nodeId}`}
              aria-label="Display flag"
              aria-pressed={display}
              onPointerEnter={flagTip(display ? 'Display (D) · already shown' : 'Display (D)')}
              onPointerLeave={endHoverTip}
              onPointerDown={stopPointer}
              onMouseDown={stopPointer}
              onDoubleClick={stopPointer}
              onClick={e => { stopPointer(e); clickDisplayFlag(nodeId) }}
            />
          )}
          {showBypassDot && (
            <button
              type="button"
              tabIndex={-1}
              className="nb-flag nb-flag--bypass nodrag nopan"
              data-testid={`nb-flag-bypass-${nodeId}`}
              aria-label="Bypass flag"
              aria-pressed={bypass}
              onPointerEnter={flagTip('Bypass (B)')}
              onPointerLeave={endHoverTip}
              onPointerDown={stopPointer}
              onMouseDown={stopPointer}
              onDoubleClick={stopPointer}
              onClick={e => { stopPointer(e); clickBypassFlag(nodeId) }}
            />
          )}
        </div>
      )}

      {/* Output port (bottom edge) */}
      {hasOutput && (
        <Handle
          type="source"
          id="out"
          position={Position.Bottom}
          isConnectable={editable}
          className="nb-port nb-port--out"
          style={catVars}
          data-testid={nodeId ? `nb-port-${nodeId}-out` : undefined}
          aria-label="output"
          title={outputPortTitle(writeSlots)}
          onPointerEnter={e => startHoverTip(e.currentTarget, outputPortTitle(writeSlots), 'below')}
          onPointerLeave={endHoverTip}
          onPointerDown={endHoverTip}
        />
      )}

      {moreOpen && folded && (
        <Popover
          anchor={moreAnchor}
          onClose={() => setMoreAnchor(null)}
          role="dialog"
          ariaLabel="All attributes"
          width={240}
          style={catVars}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, padding: 4 }}>
            {chips.map(renderChip)}
          </div>
        </Popover>
      )}
    </>
  )
}
