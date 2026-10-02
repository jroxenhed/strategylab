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
 *
 * Promoted params (W6, spec S40):
 * - A child param that its network promoted shows the network's value,
 *   read-only, with a `↑` glyph and the tooltip "Promoted to ../name. Edit
 *   it on the subnet." It is edited on the network, never on the child.
 * - `PromotedRows` draws a network's promoted params as ordinary rows (on
 *   the subnet card, max 4 plus a "+N more in Inspector" line). An edit
 *   commits the network's `params[name]`. A row whose target no longer
 *   exists has its label struck through in the error colour.
 */

import { useState, useEffect, useMemo, useRef } from 'react'
import { useNodeBuilderStore } from '../store'
import { useParamDiagnostic, setLocalParamInvalid, LOCAL_NUMBER_MESSAGE } from '../useDiagnostics'
import type { ParamSpec, ParamTypeSpec } from '../catalog'
import { paramSpecsOf } from '../streamLabels'
import { openParamMenu } from '../contextMenuModel'
import { unitLabel, viewText, viewValue, type ParamView } from './paramFormat'
import { AttrPicker } from './AttrPicker'
import { TimeRangeInput } from './TimeRangeInput'
import { DayOfWeekInput } from './DayOfWeekInput'
import { WriteChip } from './WriteChip'
import { FromSidebarRow, useSidebarParamValue } from '../sidebarWindow'
import type { GraphPromotedParam } from '../../../api/nodebuilder'
import {
  paramSpecOfNode,
  moreInInspectorText,
  promotedOf,
  promotedTooltip,
  promotedTypeSpec,
  promotedValue,
  promotionOf,
  resolveTarget,
  targetMissingTooltip,
} from '../operations/promote'
import { goUpAndSelect } from '../assetUi'
import '../assets.css'

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
  views,
}: {
  nodeId: string
  params: Record<string, unknown>
  /** Optional per-key type overrides from `NodeCatalogEntry.paramTypes`. */
  paramTypes?: Record<string, ParamTypeSpec>
  /** The full catalog specs; looked up from the node's type when left out. */
  specs?: readonly ParamSpec[]
  /** Also draw `write` params (the Inspector); the node shows them as chips. */
  showWrites?: boolean
  /** Per-key display views (terminal cards, S32b): see `ParamRowProps.view`. */
  views?: Readonly<Record<string, ParamView>>
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
          view={views?.[key]}
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
  /**
   * 'inspector' (S14): the same row drawn in the Inspector. It gets its own
   * test id and message id (`nb-param-inspector-<nodeId>-<param>`), so the
   * node row and the Inspector row never share one while both are on screen.
   */
  variant?: 'node' | 'inspector'
  /** A display view for a number row (see `ParamView`). */
  view?: ParamView
  /** The row's label; default the param key (a promoted row shows its own label). */
  label?: string
}

/** The test id of a param field; the Inspector variant has its own. */
function paramTestId(nodeId: string, paramKey: string, variant: ParamRowProps['variant']): string {
  return variant === 'inspector' ? `nb-param-inspector-${nodeId}-${paramKey}` : `nb-param-${nodeId}-${paramKey}`
}

export function ParamRow(props: ParamRowProps) {
  // D11 before W5: a Ticker's symbol and interval come from the sidebar (UX-01).
  const fromSidebar = useSidebarParamValue(props.nodeId, props.paramKey)
  // W6 (S40): a param promoted to the parent network is edited there.
  const promotion = usePromotion(props.nodeId, props.paramKey)
  if (promotion) {
    return <PromotedChildRow {...props} networkId={promotion.networkId} name={promotion.name} valueText={promotion.valueText} />
  }
  if (fromSidebar != null) {
    return (
      <FromSidebarRow
        label={props.spec?.label || props.paramKey}
        value={fromSidebar}
        testId={paramTestId(props.nodeId, props.paramKey, props.variant)}
      />
    )
  }
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
  variant,
  label,
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
    <div
      style={streamRowStyle}
      data-testid={paramTestId(nodeId, paramKey, variant)}
      data-param-kind={widget}
      onContextMenu={e => openParamMenu(e, nodeId, paramKey)}
    >
      <span style={{ flexShrink: 0 }}>{label ?? (spec.label || paramKey)}</span>
      {cell}
    </div>
  )
}

function ValueParamRow({
  nodeId,
  paramKey,
  value,
  typeSpec,
  variant,
  view: viewProp,
  label,
}: ParamRowProps) {
  const rowLabel = label ?? paramKey
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const resolvedType = typeSpec?.type ?? (typeof value === 'number' ? 'number' : 'string')
  const isNumber = resolvedType === 'number'
  const isSelect = resolvedType === 'select'
  const view = isNumber && !isSelect ? viewProp : undefined
  const initial = viewText(value, view)
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
  const testId = paramTestId(nodeId, paramKey, variant)
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
        const stored = viewValue(n, view)
        setDraft(viewText(stored, view))
        dirtyRef.current = false
        updateNodeParams(nodeId, { [paramKey]: stored })
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
      <label style={labelStyle} onContextMenu={e => openParamMenu(e, nodeId, paramKey)}>
        <span style={{ flexShrink: 0 }}>{rowLabel}</span>
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

  const unitText = view === 'percent' ? '%' : unitLabel(typeSpec?.unit, initial)

  return (
    <>
    <label style={labelStyle} onContextMenu={e => openParamMenu(e, nodeId, paramKey)}>
      <span style={{ flexShrink: 0 }}>{rowLabel}</span>
      <input
        type="text"
        inputMode={isNumber ? 'decimal' : 'text'}
        value={draft}
        placeholder={view === 'stop' ? 'none' : undefined}
        className={view === 'stop' ? 'nb-param-none' : undefined}
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

// ---------------------------------------------------------------------------
// Promoted params (W6, spec S40)
// ---------------------------------------------------------------------------

/** Separator inside the promotion key (never in an id or a name). */
const SEP = '\u0000'

/**
 * The promotion that drives this child param, with the network's value as
 * text, or null. Selectors return strings, so an edit elsewhere does not
 * re-render the row.
 */
function usePromotion(nodeId: string, paramKey: string): { networkId: string; name: string; valueText: string } | null {
  const key = useNodeBuilderStore(s => {
    const p = promotionOf(s.graph, nodeId, paramKey)
    if (!p) return null
    const net = s.graph!.nodes[p.networkId]
    const v = promotedValue(net, p.entry)
    return `${p.networkId}${SEP}${p.entry.name}${SEP}${v === null || v === undefined ? '' : Array.isArray(v) ? v.join(', ') : String(v)}`
  })
  if (!key) return null
  const [networkId, name, ...rest] = key.split(SEP)
  return { networkId, name, valueText: rest.join(SEP) }
}

const promotedGlyphStyle: React.CSSProperties = {
  color: 'var(--nb-cat-network)',
  fontSize: 10,
  flexShrink: 0,
}

/**
 * A child param that its network promoted: the network's value, read-only.
 * Click does nothing; the tooltip says where to edit it, and "Go to subnet"
 * selects the network. No `=` code toggle here (S40 must-not).
 */
function PromotedChildRow({
  nodeId,
  paramKey,
  spec,
  label,
  variant,
  networkId,
  name,
  valueText,
}: ParamRowProps & { networkId: string; name: string; valueText: string }) {
  const testId = paramTestId(nodeId, paramKey, variant)
  const tipId = `${testId}-promoted`
  const tip = promotedTooltip(name)
  return (
    <div
      className="nb-promoted-child"
      style={streamRowStyle}
      data-testid={testId}
      data-promoted-to={name}
      title={tip}
      onContextMenu={e => openParamMenu(e, nodeId, paramKey)}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
        <span style={promotedGlyphStyle} aria-hidden="true" data-testid={`${testId}-promoted-glyph`}>↑</span>
        <span>{label ?? (spec?.label || paramKey)}</span>
      </span>
      <span
        className="nb-promoted-child__value"
        role="textbox"
        aria-readonly="true"
        aria-label={label ?? (spec?.label || paramKey)}
        aria-describedby={tipId}
        tabIndex={0}
      >
        {valueText}
      </span>
      <span id={tipId} style={srOnlyStyle}>{tip}</span>
      <button
        type="button"
        className="nb-promoted-child__go"
        onPointerDown={e => e.stopPropagation()}
        onClick={() => goUpAndSelect(useNodeBuilderStore.getState(), networkId)}
      >
        Go to subnet
      </button>
    </div>
  )
}

/** The spec a promoted row draws with: the target's spec (min, max, options), renamed to the promoted name. */
function promotedSpec(entry: GraphPromotedParam, target: ParamSpec | null): ParamSpec {
  return {
    ...(target ?? {}),
    name: entry.name,
    type: entry.type as ParamSpec['type'],
    label: entry.label,
    default: entry.default,
  }
}

/** Most promoted rows a subnet card shows; the rest are in the Inspector (S38, S40). */
export const PROMOTED_ON_NODE = 4

/** One promoted row of a network (card or Inspector). */
export function PromotedParamRow({
  networkId,
  entry,
  variant = 'node',
}: {
  networkId: string
  entry: GraphPromotedParam
  variant?: 'node' | 'inspector'
}) {
  const value = useNodeBuilderStore(s => {
    const net = s.graph?.nodes[networkId]
    return net ? promotedValue(net, entry) : entry.default
  })
  // A locked instance stores no children, so its targets are checked by
  // the backend (promoted_invalid), not here.
  const target = useNodeBuilderStore(s => {
    const g = s.graph
    const net = g?.nodes[networkId]
    if (!g || !net || net.locked) return 'ok'
    const r = resolveTarget(g, networkId, entry.target)
    return r ? `${r.nodeId}${SEP}${r.param}` : 'missing'
  })
  const [targetId, targetParam] = target === 'ok' || target === 'missing' ? [null, null] : target.split(SEP)
  const targetNode = useNodeBuilderStore(s => (targetId ? s.graph?.nodes[targetId] ?? null : null))
  const targetSpec = useMemo(
    () => (targetNode && targetParam ? paramSpecOfNode(targetNode, targetParam) : null),
    [targetNode, targetParam],
  )
  const missing = target === 'missing'
  const spec = promotedSpec(entry, targetSpec)
  const row = (
    <ParamRow
      nodeId={networkId}
      paramKey={entry.name}
      value={value}
      typeSpec={promotedTypeSpec(entry, targetSpec)}
      spec={spec}
      variant={variant}
      label={entry.label}
    />
  )
  if (!missing) return row
  return (
    <div className="nb-promoted-row nb-promoted-row--missing" title={targetMissingTooltip(entry.target)} data-target-missing="true">
      {row}
    </div>
  )
}

/**
 * A network's promoted params as ordinary rows (S38 card body, S40). At
 * most `max` rows, then "+N more in Inspector".
 */
export function PromotedRows({ nodeId, max = PROMOTED_ON_NODE }: { nodeId: string; max?: number }) {
  const list = useNodeBuilderStore(s => promotedOf(s.graph?.nodes[nodeId]))
  if (list.length === 0) return null
  const shown = list.slice(0, max)
  const more = list.length - shown.length
  return (
    <div className="nodrag nopan" style={{ display: 'flex', flexDirection: 'column', gap: 3 }} data-testid={`nb-promoted-rows-${nodeId}`}>
      {shown.map(entry => (
        <PromotedParamRow key={entry.name} networkId={nodeId} entry={entry} />
      ))}
      {more > 0 && <div className="nb-promoted-more">{moreInInspectorText(more)}</div>}
    </div>
  )
}
