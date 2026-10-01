/**
 * ParamRow / ParamRows — inline editor for node params.
 *
 * Rendered as <BaseNode> children when `editable` is true. One labelled input
 * per param. Type inferred from `paramTypes` (catalog override) when provided,
 * otherwise from `typeof value` (number → number input, anything else → text).
 *
 * Commit semantics: blur reads `e.target.value` directly (not React state) so a
 * fast type-then-tab can't lose the value to batching. Enter blurs, ESC reverts.
 * Container has `.nodrag .nopan` + onPointerDown stopPropagation to keep React
 * Flow from grabbing the cursor mid-edit. Select inputs commit immediately
 * onChange (no blur step — there's nothing to type).
 *
 * Store sync (Cmd+Z guard, bug 7): the browser's own undo can rewrite a field
 * behind our back. So the field follows three rules:
 * - While the field is not focused it shows the store value. Input events that
 *   arrive while it is not focused are ignored, and React puts the store value
 *   back on screen.
 * - While it is focused the user's text wins, even if the store changes.
 * - On blur, typed text (including text a native undo put back) is committed.
 *   If nothing was typed, the field re-syncs to the store instead of pushing
 *   its old text over a newer store value.
 * A catalog unit (ParamTypeSpec.unit) shows after the input; a "fraction" also
 * shows its percent.
 * Numeric inputs stay type="text" inputMode="decimal" (F278). Never switch them
 * to type="number".
 *
 * Invalid state (spec S05): the field turns red with `aria-invalid="true"`
 * when its text is not a number (checked on every keystroke, no request),
 * or when the server's diagnostics name this param. The message goes in
 * `title` and in a hidden element the field points at. A local problem is
 * also reported to the diagnostics store, so the node badge, the error count
 * and Run agree with the red field.
 *
 * Stream params (F435 W2, specs S09, S10, S12) are chosen by the catalog
 * ParamSpec type, never by node type, so node types added later work too:
 * - `attr` / `attr_list` show the attribute picker (AttrPicker);
 * - `time_range` shows the time-of-day range (TimeRangeInput);
 * - a `select` whose value or default is a list shows toggle pills
 *   (DayOfWeekInput, the weekday picker);
 * - `write` shows a renamable write chip. On a node the chip row draws the
 *   write chips, so ParamRows leaves `write` params out unless asked.
 * The specs come from the `specs` prop, else from the catalog entry of the
 * node's type in the store. Without either, rows fall back to `paramTypes`.
 */

import { useState, useEffect, useRef } from 'react'
import { useNodeBuilderStore } from '../store'
import { useParamDiagnostic, setLocalParamInvalid, LOCAL_NUMBER_MESSAGE } from '../useDiagnostics'
import type { ParamSpec, ParamTypeSpec } from '../catalog'
import { paramSpecsOf } from '../streamLabels'
import { unitLabel } from './paramFormat'
import { AttrPicker } from './AttrPicker'
import { TimeRangeInput } from './TimeRangeInput'
import { DayOfWeekInput } from './DayOfWeekInput'
import { WriteChip } from './WriteChip'

/** Which special widget a param gets, from its ParamSpec; null for a plain field. */
export type StreamWidget = 'attr' | 'time_range' | 'days' | 'write'

export function streamWidgetFor(spec: Pick<ParamSpec, 'type' | 'default' | 'name'> | undefined, value: unknown): StreamWidget | null {
  if (!spec) return null
  if (spec.type === 'attr' || spec.type === 'attr_list') return 'attr'
  if (spec.type === 'time_range') return 'time_range'
  if (spec.type === 'write') return 'write'
  if (spec.type === 'select' && (Array.isArray(spec.default) || Array.isArray(value))) return 'days'
  return null
}

/** Spec types that show a row even when the param has no stored value yet. */
const ALWAYS_SHOWN: ReadonlySet<string> = new Set(['attr', 'attr_list', 'time_range'])

/**
 * The rows to draw, in catalog order: every spec param that is stored (or
 * is a stream param that should be picked), then stored params the catalog
 * does not know.
 */
export function rowsFor(
  params: Record<string, unknown>,
  specs: readonly ParamSpec[],
  showWrites: boolean,
): { key: string; value: unknown; spec?: ParamSpec }[] {
  const out: { key: string; value: unknown; spec?: ParamSpec }[] = []
  const seen = new Set<string>()
  for (const spec of specs) {
    seen.add(spec.name)
    if (spec.type === 'write' && !showWrites) continue
    if (!(spec.name in params) && !ALWAYS_SHOWN.has(spec.type)) continue
    out.push({ key: spec.name, value: params[spec.name] ?? null, spec })
  }
  for (const [key, value] of Object.entries(params)) {
    if (!seen.has(key)) out.push({ key, value })
  }
  return out
}

export function ParamRows({
  nodeId,
  params,
  paramTypes,
  specs,
  showWrites = false,
}: {
  nodeId: string
  params: Record<string, unknown>
  /** Optional per-key type overrides from `NodeCatalogEntry.paramTypes`. */
  paramTypes?: Record<string, ParamTypeSpec>
  /** The full catalog specs; looked up from the node's type when left out. */
  specs?: readonly ParamSpec[]
  /** Also draw `write` params (the Inspector); the node shows them as chips. */
  showWrites?: boolean
}) {
  const nodeType = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.type)
  const allSpecs = specs ?? paramSpecsOf(nodeType)
  return (
    <div className="nodrag nopan" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {rowsFor(params, allSpecs, showWrites).map(({ key, value, spec }) => (
        <ParamRow
          key={key}
          nodeId={nodeId}
          paramKey={key}
          value={value}
          typeSpec={paramTypes?.[key]}
          spec={spec}
        />
      ))}
    </div>
  )
}

const labelStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  fontFamily: 'var(--nb-font-mono)',
  fontSize: 10,
  color: 'var(--nb-text-muted)',
  lineHeight: '14px',
}

// Both states set `border` as one shorthand and never touch borderColor.
// Mixing the shorthand with `borderColor` makes React warn on re-render
// (bug 22, spec S05).
const fieldStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  background: 'var(--nb-bg-elevated)',
  border: '1px solid var(--nb-border)',
  borderRadius: 'var(--nb-radius-pill)',
  color: 'var(--nb-text)',
  fontFamily: 'var(--nb-font-mono)',
  fontSize: 10,
  padding: '2px 5px',
  outline: 'none',
}

const invalidFieldStyle: React.CSSProperties = {
  ...fieldStyle,
  border: '1px solid var(--nb-error)',
  borderRadius: 3,
  color: 'var(--nb-error)',
}

// Visually hidden, still read by screen readers (aria-describedby target).
const srOnlyStyle: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
  border: 0,
}

/** True when a number field's text would not parse. Empty is not invalid
 * (it reverts on blur, F275). */
export function isUnparseableNumber(text: string): boolean {
  const t = text.trim()
  return t !== '' && !Number.isFinite(Number(t))
}

const unitStyle: React.CSSProperties = {
  flexShrink: 0,
  color: 'var(--nb-text-muted)',
}

export interface ParamRowProps {
  nodeId: string
  paramKey: string
  value: unknown
  typeSpec?: ParamTypeSpec
  /** The catalog spec; it picks the stream widgets. */
  spec?: ParamSpec
}

export function ParamRow(props: ParamRowProps) {
  const widget = streamWidgetFor(props.spec, props.value)
  if (widget && props.spec) return <StreamParamRow {...props} spec={props.spec} widget={widget} />
  return <ValueParamRow {...props} />
}

const streamRowStyle: React.CSSProperties = {
  ...labelStyle,
  justifyContent: 'space-between',
  minHeight: 16,
}

/** A row whose value is a picker, a chip or a widget instead of a text field. */
function StreamParamRow({
  nodeId,
  paramKey,
  value,
  spec,
  widget,
}: ParamRowProps & { spec: ParamSpec; widget: StreamWidget }) {
  const readOnly = useNodeBuilderStore(s => s.graph?.readOnly ?? false)
  let cell: React.ReactNode
  if (widget === 'attr') {
    cell = <AttrPicker nodeId={nodeId} spec={spec} value={value} disabled={readOnly} />
  } else if (widget === 'time_range') {
    cell = <TimeRangeInput nodeId={nodeId} param={paramKey} value={value} disabled={readOnly} />
  } else if (widget === 'days') {
    cell = <DayOfWeekInput nodeId={nodeId} param={paramKey} value={value} options={spec.options} disabled={readOnly} />
  } else {
    const name = typeof value === 'string' && value !== '' ? value : typeof spec.default === 'string' ? spec.default : ''
    cell = name ? <WriteChip nodeId={nodeId} param={paramKey} name={name} editable={!readOnly} /> : null
  }
  // A div, not a label: a label would forward a click on its text to the
  // first button inside it and open the picker by surprise.
  return (
    <div style={streamRowStyle} data-testid={`nb-param-${nodeId}-${paramKey}`} data-param-kind={widget}>
      <span style={{ flexShrink: 0 }}>{spec.label || paramKey}</span>
      {cell}
    </div>
  )
}

function ValueParamRow({
  nodeId,
  paramKey,
  value,
  typeSpec,
}: ParamRowProps) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const resolvedType = typeSpec?.type ?? (typeof value === 'number' ? 'number' : 'string')
  const isNumber = resolvedType === 'number'
  const isSelect = resolvedType === 'select'
  const initial = value === null || value === undefined ? '' : String(value)
  const [draft, setDraft] = useState(initial)
  // Refs, not state: these are read inside event handlers and the sync effect
  // and must never trigger a render on their own.
  const focusedRef = useRef(false)
  // True when the user has typed since the field last matched the store.
  const dirtyRef = useRef(false)

  // Re-sync from the store whenever the store value changes, but never while
  // the user is typing in the field.
  useEffect(() => {
    if (focusedRef.current) return
    setDraft(initial)
    dirtyRef.current = false
  }, [initial])

  const resync = () => {
    setDraft(initial)
    dirtyRef.current = false
  }

  // Invalid state: local parse first (it is newer than any server answer),
  // then the server's diagnostic for this param.
  const serverDiag = useParamDiagnostic(nodeId, paramKey)
  const localInvalid = isNumber && !isSelect && isUnparseableNumber(draft)
  const invalidMessage = localInvalid ? LOCAL_NUMBER_MESSAGE : serverDiag?.message ?? null
  const testId = `nb-param-${nodeId}-${paramKey}`
  const messageId = `${testId}-msg`

  // Report the local problem to the shared store (no request), and take it
  // back when the field is fixed or goes away.
  useEffect(() => {
    if (!localInvalid) return
    setLocalParamInvalid(nodeId, paramKey, LOCAL_NUMBER_MESSAGE)
    return () => setLocalParamInvalid(nodeId, paramKey, null)
  }, [localInvalid, nodeId, paramKey])

  const message = invalidMessage
    ? <span id={messageId} style={srOnlyStyle}>{invalidMessage}</span>
    : null

  const commit = (raw: string) => {
    if (raw === initial) { resync(); return }
    if (isNumber) {
      const trimmed = raw.trim()
      // Empty input → silent revert (F275). Distinct from "abc" which is
      // unparseable: we keep the bad value visible + red so the user can fix it.
      if (trimmed === '') {
        resync()
        return
      }
      const n = Number(trimmed)
      if (Number.isFinite(n)) {
        // Show the value the store will hold ("20.0" becomes "20") so the
        // field and the store agree even if the number did not change.
        setDraft(String(n))
        dirtyRef.current = false
        updateNodeParams(nodeId, { [paramKey]: n })
      }
      // Otherwise the text stays on screen, red, until the user fixes it.
    } else {
      dirtyRef.current = false
      updateNodeParams(nodeId, { [paramKey]: raw })
    }
  }

  if (isSelect) {
    const options = typeSpec?.options ?? []
    // Include current value as a fallback option if it's not in the list, so a
    // strategy with a legacy/unknown value still displays correctly.
    const allOptions = options.includes(initial as never) ? options : [initial, ...options]
    // A select has no typing to protect, so it always shows the store value.
    return (
      <>
      <label style={labelStyle}>
        <span style={{ flexShrink: 0 }}>{paramKey}</span>
        <select
          value={initial}
          data-testid={testId}
          aria-invalid={invalidMessage ? true : undefined}
          aria-describedby={invalidMessage ? messageId : undefined}
          title={invalidMessage ?? undefined}
          onChange={e => { if (e.target.value !== initial) updateNodeParams(nodeId, { [paramKey]: e.target.value }) }}
          onPointerDown={e => e.stopPropagation()}
          style={invalidMessage ? invalidFieldStyle : fieldStyle}
        >
          {allOptions.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </label>
      {message}
      </>
    )
  }

  const unitText = unitLabel(typeSpec?.unit, initial)

  return (
    <>
    <label style={labelStyle}>
      <span style={{ flexShrink: 0 }}>{paramKey}</span>
      <input
        type="text"
        inputMode={isNumber ? 'decimal' : 'text'}
        value={draft}
        data-testid={testId}
        aria-invalid={invalidMessage ? true : undefined}
        aria-describedby={invalidMessage ? messageId : undefined}
        title={invalidMessage ?? undefined}
        onFocus={() => { focusedRef.current = true }}
        onChange={e => {
          // A native undo (Cmd+Z) can edit a field that does not have focus.
          // Ignore that: not updating the draft makes React restore the store
          // value on screen.
          if (!focusedRef.current || document.activeElement !== e.currentTarget) return
          dirtyRef.current = true
          setDraft(e.target.value)
        }}
        onBlur={e => {
          focusedRef.current = false
          if (dirtyRef.current) commit(e.target.value)
          else resync()
        }}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur()
          } else if (e.key === 'Escape') {
            // Revert first, then blur: with dirty cleared the blur re-syncs
            // to the store instead of committing the typed text.
            resync();
            (e.target as HTMLInputElement).blur()
          }
        }}
        onPointerDown={e => e.stopPropagation()}
        style={invalidMessage ? invalidFieldStyle : fieldStyle}
      />
      {unitText && <span data-testid="param-unit" style={unitStyle}>{unitText}</span>}
    </label>
    {message}
    </>
  )
}
