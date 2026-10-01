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
  useUpdateNodeInternals,
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
import { DiagnosticBadge } from './DiagnosticBadge'
import { ParamRows } from './ParamRow'
import { WriteChip } from './WriteChip'
import '../stream.css'

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

// ---------------------------------------------------------------------------
// BaseNode props
// ---------------------------------------------------------------------------
export interface BaseNodeData extends Record<string, unknown> {
  backendType: string
  catalog: import('../catalog').NodeCatalogEntry | null
  params: Record<string, unknown>
  display: boolean
  bypass: boolean
  nodePath: string
  /** True when the canvas is in editable mode. */
  editable?: boolean
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
  /** Is this node display-flagged? (blue halo). */
  display?: boolean
  /** Is this node bypassed? (dim + amber dot). */
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
}

/** Card border when the node has an error (ui-ux-spec 4.8). */
const ERROR_BORDER = '1px solid rgba(248, 113, 113, 0.6)'

/** More chips than this and the row folds (foundation 4.5). */
const MAX_CHIPS = 6

const NO_PARAMS: Record<string, unknown> = {}

type Chip = { kind: 'read'; name: string } | { kind: 'write'; slot: WriteSlot }

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

  // The hover text needs the latest streams, so it is written straight to
  // the element when the pointer arrives (no React render on hover).
  const setPortTitle = (portId: string, label: string) => (e: React.PointerEvent<HTMLDivElement>) => {
    const error = portErrors.get(portId)
    if (error) { e.currentTarget.title = error; return }
    const from = sourceByPort.get(portId)
    let readsHere: string[] = []
    if (from && nodeId) {
      const graph = { nodes: { [nodeId]: { ...node, id: nodeId } as GraphNode }, wires: [] }
      readsHere = readsThroughWire({ from, to: nodeId }, graph, getStreams())
    }
    e.currentTarget.title = inputPortTitle(label, readsHere)
  }

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
  const chips: Chip[] = [
    ...readChips.map(name => ({ kind: 'read' as const, name })),
    ...writeSlots.map(slot => ({ kind: 'write' as const, slot })),
  ]
  const folded = chips.length > MAX_CHIPS
  const shownChips = folded ? chips.slice(0, MAX_CHIPS - 1) : chips
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLButtonElement>(null)

  const renderChip = (c: Chip, i: number) => c.kind === 'read'
    ? <span key={`r-${i}-${c.name}`} className="nb-chip">{c.name}</span>
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
    opacity: bypass ? 0.55 : 1,
    boxShadow: display
      ? `0 0 0 1px var(--nb-flag-display), 0 0 16px var(--nb-flag-display)`
      : 'none',
  }

  // A badge click goes where the node's first problem is: its wire when the
  // problem is about a wire (spec S05), else the node.
  const activateBadge = () => {
    if (!nodeId) return
    if (diagnostics[0] && focusDiagnosticWire(diagnostics[0])) return
    select(nodeId)
  }

  const showAllPortLabels = ports.length >= 2

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
            onPointerEnter={setPortTitle(p.id, p.label)}
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

      <div className="nb-node-card" style={containerStyle}>
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

        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '6px 10px 5px 11px',
          minHeight: 24,
        }}>
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
          }}>
            {title}
          </span>

          {/* Subtitle */}
          {subtitle && (
            <span style={{
              fontFamily: 'var(--nb-font-mono)',
              fontSize: 10,
              color: 'var(--nb-text-muted)',
              whiteSpace: 'nowrap',
              flexShrink: 0,
              lineHeight: '16px',
            }}>
              {subtitle}
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
        {(hasChips || children || readParamValues) && (
          <div style={{
            padding: '4px 10px 7px 12px',
            borderTop: '1px solid var(--nb-border-subtle)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}>
            {children}
            {readParamValues && nodeId && (
              <ParamRows nodeId={nodeId} params={readParamValues} specs={readParams} />
            )}
            {hasChips && (
              <div
                className="nb-chip-row"
                style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: children || readParamValues ? 4 : 0 }}
              >
                {shownChips.map(renderChip)}
                {folded && (
                  <button
                    ref={moreRef}
                    type="button"
                    className="nb-chip nb-chip--more nodrag"
                    aria-haspopup="dialog"
                    aria-expanded={moreOpen}
                    aria-label={`${chips.length - shownChips.length} more attributes`}
                    onPointerDown={e => e.stopPropagation()}
                    onClick={() => setMoreOpen(o => !o)}
                  >
                    +{chips.length - shownChips.length}
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* Bypass dot — amber, top-right corner */}
        {bypass && (
          <div style={{
            position: 'absolute',
            top: 5,
            right: 6,
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: 'var(--nb-flag-bypass)',
          }} />
        )}
      </div>

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
        />
      )}

      {moreOpen && folded && (
        <Popover
          anchor={moreRef.current}
          onClose={() => setMoreOpen(false)}
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
