/**
 * BaseNode — shared layout primitive for all custom node renderers.
 *
 * Renders: 3px left stripe, 16×16 icon chip, title, subtitle, attribute pills,
 * display-flag glow, bypass-flag dim+dot, invisible handles.
 *
 * Diagnostics (spec S05): the node reads its own problems from the shared
 * diagnostics store and shows a DiagnosticBadge after the subtitle. Any error
 * turns the card border red. The node id comes from React Flow's node
 * context, or from the `nodeId` prop outside a flow (tests).
 *
 * Handles: a node gets a target (input) handle unless `hasInput` is false,
 * and a source (output) handle unless `hasOutput` is false. Terminals (Entry,
 * Exit, ...) have no output and the Ticker has no input, so a user can never
 * drag a wire from or to a port that means nothing.
 *
 * All color references use CSS custom properties from tokens.css
 * scoped to .nodebuilder-root.
 */

import { Handle, Position, useNodeId } from '@xyflow/react'
import { CATS, type CatKey } from '../categories'
import { useNodeBuilderStore } from '../store'
import { useNodeDiagnostics } from '../useDiagnostics'
import { DiagnosticBadge } from './DiagnosticBadge'

// ---------------------------------------------------------------------------
// Attr pill
// ---------------------------------------------------------------------------
interface PillProps {
  label: string
  /** If true, renders with category-tinted background (write attr). */
  write?: boolean
  catColor?: string
}

export function AttrPill({ label, write = false, catColor }: PillProps) {
  const bg = write && catColor
    ? `color-mix(in oklch, ${catColor} 22%, var(--nb-bg-elevated, oklch(0.14 0.012 250)))`
    : 'oklch(0.22 0.012 250)'
  const text = write ? (catColor ?? 'var(--nb-text-secondary)') : 'var(--nb-text-muted)'
  return (
    <span style={{
      fontFamily: 'var(--nb-font-mono)',
      fontSize: 10,
      fontWeight: 500,
      padding: '2px 5px',
      borderRadius: 'var(--nb-radius-pill)',
      background: bg,
      color: text,
      lineHeight: '14px',
      whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
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
  /** True when the canvas is in editable mode; handles become visible dots. */
  editable?: boolean
}

interface BaseNodeProps {
  /** Category key — drives stripe + chip color + glyph. */
  cat: CatKey
  /** Node title (e.g. "RSI(14)", "AAPL", "AND"). */
  title: string
  /** Optional subtitle rendered right-aligned in the header. */
  subtitle?: string
  /** Read attributes (gray pills). */
  reads?: readonly string[]
  /** Write attributes (category-tinted pills). */
  writes?: readonly string[]
  /** Is this node display-flagged? (blue halo). */
  display?: boolean
  /** Is this node bypassed? (dim + amber dot). */
  bypass?: boolean
  /** Width of the node in px. */
  width?: number
  /** Optional extra body content (param rows, etc.). */
  children?: React.ReactNode
  /** When true, handles render as visible colored dots (editable mode). */
  editable?: boolean
  /** Render the target (input) handle. False for source nodes like Ticker. */
  hasInput?: boolean
  /** Render the source (output) handle. False for terminals like Entry/Exit. */
  hasOutput?: boolean
  /** Node id for diagnostics. Defaults to the React Flow node this renders in. */
  nodeId?: string
}

/** Card border when the node has an error (ui-ux-spec 4.8). */
const ERROR_BORDER = '1px solid rgba(248, 113, 113, 0.6)'

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

  // For output category, chip needs a dark icon color since the cat color is near-white
  const chipTextColor = cat === 'output' ? 'var(--nb-bg)' : 'var(--nb-bg)'

  const hasPills = reads.length > 0 || writes.length > 0

  const containerStyle: React.CSSProperties = {
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

  // Visible handle style (editable mode) — 8px dot, category color
  const handleVisibleStyle: React.CSSProperties = editable ? {
    width: 8,
    height: 8,
    background: catColor,
    border: '1px solid oklch(0.10 0.008 250)',
    borderRadius: '50%',
  } : {
    width: 0,
    height: 0,
    border: 0,
    background: 'transparent',
  }

  return (
    <>
      {/* Target handle (top) */}
      {hasInput && (
        <Handle
          type="target"
          position={Position.Top}
          style={{ ...handleVisibleStyle, top: editable ? -4 : 0 }}
        />
      )}

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
              color: chipTextColor,
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
              onActivate={() => select(nodeId)}
            />
          )}
        </div>

        {/* Body — pills + optional children */}
        {(hasPills || children) && (
          <div style={{
            padding: '4px 10px 7px 12px',
            borderTop: '1px solid var(--nb-border-subtle)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}>
            {children}
            {hasPills && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: children ? 4 : 0 }}>
                {reads.map((attr, i) => (
                  <AttrPill key={`r-${i}`} label={attr} />
                ))}
                {writes.map((attr, i) => (
                  <AttrPill key={`w-${i}`} label={attr} write catColor={catColor} />
                ))}
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

      {/* Source handle (bottom) */}
      {hasOutput && (
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ ...handleVisibleStyle, bottom: editable ? -4 : 0 }}
        />
      )}
    </>
  )
}
