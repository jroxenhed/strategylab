/**
 * Spare parameters (F435 W7, spec S48): params the node's code declares
 * with `chf("threshold", default=2.0)` and friends. They look and edit like
 * any other row.
 *
 * - The list is `Node.spare_params` from the last good `parse_code` (never
 *   worked out on the client). Values live in `Node.params[name]`.
 * - A `{}` glyph in the gutter marks a spare row; the label's tooltip names
 *   the call it came from.
 * - In the Inspector: a slider under a float row with min and max, and a
 *   `←` glyph when another node reads the row through `ch()` (from the
 *   last /validate `param_deps`).
 * - On a node: at most `max` rows, then `+N more in Inspector`.
 * - A `vector` spec draws 2 to 4 number cells labelled x y z w.
 */

import { useState, useEffect, useRef } from 'react'
import type { GraphNode, SpareParamSpec } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { ParamRow } from '../nodes/ParamRow'
import { readersOf, useCodeStore } from './codeStore'
import { moreSpareText, spareParamSpec, spareTooltip, spareTypeSpec, useSpareSpecs } from './codeUi'
import './code.css'


/**
 * The spare rows of a node. `max` limits the rows on a node (S48: the
 * on-node rows, built-in plus spare, are at most 4).
 */
export function SpareParamRows({
  nodeId,
  node,
  variant = 'node',
  max,
  editable = true,
}: {
  nodeId: string
  /** The node, when the caller has it (the Inspector, the read-only view). */
  node?: GraphNode | null
  variant?: 'node' | 'inspector'
  max?: number
  editable?: boolean
}) {
  const specs = useSpareSpecs(nodeId, node)
  const values = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.params) ?? node?.params ?? {}
  const deps = useCodeStore(s => s.paramDeps)
  const names = useNodeBuilderStore(s => s.graph?.nodes)
  // Names seen on the first render: a row added later fades in (S48 "appearing").
  const [firstNames] = useState(() => new Set(specs.map(s => s.name)))
  if (specs.length === 0) return null
  const limit = max ?? specs.length
  const shown = specs.slice(0, Math.max(0, limit))
  const more = specs.length - shown.length
  return (
    <div className="nodrag nopan" style={{ display: 'flex', flexDirection: 'column', gap: 3 }} data-testid={`nb-spare-rows-${nodeId}`}>
      {shown.map(spec => {
        const value = values[spec.name] ?? spec.default
        const readers = variant === 'inspector'
          ? readersOf(deps, nodeId, spec.name).map(id => names?.[id]?.name ?? id)
          : []
        const cls = `nb-spare-row${firstNames.has(spec.name) ? '' : ' nb-spare-row--appear'}`
        const pspec = spareParamSpec(spec)
        const slider = variant === 'inspector' && spec.type === 'float'
          && typeof spec.min === 'number' && typeof spec.max === 'number'
        return (
          <div key={spec.name} className={cls} data-testid={`nb-spare-${nodeId}-${spec.name}`} data-spare-type={spec.type} title={spareTooltip(spec)}>
            <span className="nb-spare-glyph" aria-hidden="true">{'{}'}</span>
            <div style={{ display: 'flex', alignItems: 'center' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                {spec.type === 'vector' ? (
                  <VectorRow nodeId={nodeId} spec={spec} value={value} editable={editable} />
                ) : spec.type === 'bool' && !isExpr(value) ? (
                  <BoolRow nodeId={nodeId} spec={spec} value={value} editable={editable} />
                ) : editable ? (
                  <ParamRow
                    nodeId={nodeId}
                    paramKey={spec.name}
                    value={value}
                    typeSpec={spareTypeSpec(spec)}
                    spec={pspec}
                    variant={variant}
                    label={spec.label || spec.name}
                  />
                ) : (
                  <div className="nb-insp-prow__ro">
                    <span className="nb-insp-prow__label">{spec.label || spec.name}</span>
                    <span className="nb-insp-prow__value">{Array.isArray(value) ? value.join(', ') : String(value ?? '')}</span>
                  </div>
                )}
              </div>
              {readers.length > 0 && (
                <span className="nb-spare-readby" title={`Read by ${readers.join(', ')}`} data-testid={`nb-spare-readby-${spec.name}`}>←</span>
              )}
            </div>
            {slider && <SpareSlider nodeId={nodeId} spec={spec} value={value} editable={editable} />}
          </div>
        )
      })}
      {more > 0 && <div className="nb-spare-more" data-testid={`nb-spare-more-${nodeId}`}>{moreSpareText(more)}</div>}
    </div>
  )
}

/** A slider under a float spare with min and max (Inspector). Commits on release. */
function SpareSlider({ nodeId, spec, value, editable }: { nodeId: string; spec: SpareParamSpec; value: unknown; editable: boolean }) {
  const update = useNodeBuilderStore(s => s.updateNodeParams)
  const [draft, setDraft] = useState<number | null>(null)
  const stored = typeof value === 'number' ? value : Number(value)
  const shown = draft ?? (Number.isFinite(stored) ? stored : Number(spec.min))
  const commit = () => {
    if (draft === null) return
    setDraft(null)
    if (draft !== stored) update(nodeId, { [spec.name]: draft })
  }
  return (
    <div className="nb-insp-slider">
      <input
        type="range"
        min={spec.min ?? undefined}
        max={spec.max ?? undefined}
        step="any"
        value={shown}
        disabled={!editable}
        aria-label={`${spec.label || spec.name} slider`}
        data-testid={`nb-inspector-slider-${spec.name}`}
        onChange={e => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
    </div>
  )
}

function isExpr(v: unknown): boolean {
  return !!v && typeof v === 'object' && !Array.isArray(v) && 'expr' in (v as object)
}

/** A checkbox row for a `chb()` spare (S48). */
function BoolRow({ nodeId, spec, value, editable }: { nodeId: string; spec: SpareParamSpec; value: unknown; editable: boolean }) {
  const update = useNodeBuilderStore(s => s.updateNodeParams)
  const checked = value === true || value === 'true'
  return (
    <label className="nb-spare-bool" style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--nb-font-mono)', fontSize: 10, color: 'var(--nb-text-muted)' }}>
      <span style={{ flexShrink: 0 }}>{spec.label || spec.name}</span>
      <input
        type="checkbox"
        checked={checked}
        disabled={!editable}
        data-testid={`nb-param-${nodeId}-${spec.name}`}
        onChange={e => update(nodeId, { [spec.name]: e.target.checked })}
        onPointerDown={e => e.stopPropagation()}
      />
    </label>
  )
}

const AXES = ['x', 'y', 'z', 'w'] as const

/** 2 to 4 number cells for a `chv()` spare; one commit per cell edit (S48). */
function VectorRow({ nodeId, spec, value, editable }: { nodeId: string; spec: SpareParamSpec; value: unknown; editable: boolean }) {
  const update = useNodeBuilderStore(s => s.updateNodeParams)
  const base = Array.isArray(value) ? value : Array.isArray(spec.default) ? spec.default : [0, 0]
  const cells = base.slice(0, 4).map(v => Number(v))
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--nb-font-mono)', fontSize: 10, color: 'var(--nb-text-muted)' }}>
      <span style={{ flexShrink: 0 }}>{spec.label || spec.name}</span>
      <div className="nb-spare-vector" role="group" aria-label={`${spec.label || spec.name} vector`}>
        {cells.map((c, i) => (
          <VectorCell
            key={i}
            axis={AXES[i]}
            value={c}
            disabled={!editable}
            testId={`nb-param-${nodeId}-${spec.name}-${AXES[i]}`}
            onCommit={n => {
              if (n === c) return
              const next = [...cells]
              next[i] = n
              update(nodeId, { [spec.name]: next })
            }}
          />
        ))}
      </div>
    </div>
  )
}

function VectorCell({ axis, value, disabled, testId, onCommit }: {
  axis: string
  value: number
  disabled: boolean
  testId: string
  onCommit(n: number): void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => { setDraft(String(value)) }, [value])
  // Esc reverts: the blur that follows must not commit the typed text (FE-5).
  const reverting = useRef(false)
  const commit = (text: string) => {
    if (reverting.current) { reverting.current = false; return }
    const n = Number(text.trim())
    if (text.trim() === '' || !Number.isFinite(n)) { setDraft(String(value)); return }
    onCommit(n)
  }
  return (
    <span className="nb-spare-vector__cell">
      <span className="nb-spare-vector__lbl" aria-hidden="true">{axis}</span>
      <input
        type="text"
        inputMode="decimal"
        aria-label={axis}
        value={draft}
        disabled={disabled}
        data-testid={testId}
        onChange={e => setDraft(e.target.value)}
        onBlur={e => commit(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() }
          else if (e.key === 'Escape') {
            e.stopPropagation()
            setDraft(String(value))
            reverting.current = true
            ;(e.target as HTMLInputElement).blur()
            reverting.current = false
          }
        }}
        onPointerDown={e => e.stopPropagation()}
        style={{
          background: 'var(--nb-bg-elevated)', border: '1px solid var(--nb-border)', borderRadius: 3,
          color: 'var(--nb-text)', fontFamily: 'var(--nb-font-mono)', fontSize: 11, padding: '1px 4px', outline: 'none',
        }}
      />
    </span>
  )
}
