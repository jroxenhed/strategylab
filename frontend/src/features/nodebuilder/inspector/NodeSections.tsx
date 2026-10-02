/**
 * The built-in sections of the Inspector's node view (spec S14):
 * Parameters (10), Stream (30) and Diagnostics (40). They register in the
 * section registry (sections.ts) when this module loads, the same way W7
 * adds Code (20) and a later item adds Notes (50).
 *
 * Parameters reuses ParamRow, so the on-node row and the Inspector row are
 * the same control on the same store value: editing either updates both,
 * and every edit goes through `updateNodeParams` (one undo step).
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { Graph, GraphWire } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import type { ParamSpec } from '../catalog'
import { getActiveCanvas } from '../commands'
import { openParamMenu } from '../contextMenuModel'
import { ParamRow, rowsFor } from '../nodes/ParamRow'
import { unitLabel } from '../nodes/paramFormat'
import { WriteChip } from '../nodes/WriteChip'
import { newWireId } from '../operations'
import { wireProblemText } from '../operations/wires'
import { useNodeBuilderStore } from '../store'
import {
  catalogEntry,
  connectionProblem,
  connectWire,
  inputStreamOf,
  portsOf,
  readsOf,
  writesOf,
} from '../streamLabels'
import { focusDiagnosticWire, useNodeDiagnostics, useStreams } from '../useDiagnostics'
import { registerInspectorSection, type InspectorSectionProps } from './sections'
import { FLASH_MS, flashParam, setSectionOpen, useInspectorUi } from './state'
import { catVars, categoryOf, diagnosticsCountText, sameValue, stepNumberField, useInspectorSelect, valueText } from './util'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

/** Spec defaults, for params the node has not stored yet. */
function defaultsOf(specs: readonly ParamSpec[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const s of specs) if (s.default !== undefined) out[s.name] = s.default
  return out
}

/** A slider under a number row whose spec has min and max. Commits on release. */
export function ParamSlider({
  nodeId,
  paramKey,
  spec,
  value,
  editable,
}: {
  nodeId: string
  paramKey: string
  spec: ParamSpec
  value: unknown
  editable: boolean
}) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const [draft, setDraft] = useState<number | null>(null)
  const stored = typeof value === 'number' ? value : Number(value)
  const shown = draft ?? (Number.isFinite(stored) ? stored : Number(spec.min))
  const unit = unitLabel(spec.unit ?? undefined, String(shown))
  const commit = () => {
    if (draft === null) return
    setDraft(null)
    if (draft !== stored) updateNodeParams(nodeId, { [paramKey]: draft })
  }
  return (
    <div className="nb-insp-slider">
      <input
        type="range"
        min={spec.min}
        max={spec.max}
        step={spec.step ?? (spec.type === 'int' ? 1 : 'any')}
        value={shown}
        disabled={!editable}
        aria-label={`${spec.label || paramKey} slider`}
        aria-valuetext={unit ? `${shown} ${unit}` : String(shown)}
        data-testid={`nb-inspector-slider-${paramKey}`}
        onChange={e => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
    </div>
  )
}

export function ParametersSection({ nodeId, node, editable }: InspectorSectionProps) {
  const entry = catalogEntry(node.type)
  const specs = useMemo(() => entry?.params ?? [], [entry])
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const merged = useMemo(() => ({ ...defaultsOf(specs), ...node.params }), [specs, node.params])
  const rows = rowsFor(merged, specs, true)
  const rootRef = useRef<HTMLDivElement>(null)

  // A diagnostics click flashes the row (S05) and may focus its field.
  const flash = useInspectorUi(s => s.flash)
  // The flash seq whose 800 ms are over; the row is lit until then.
  const [doneSeq, setDoneSeq] = useState(0)
  const lit = flash && flash.nodeId === nodeId && flash.seq !== doneSeq ? flash.param : null
  useEffect(() => {
    if (!flash || flash.nodeId !== nodeId || !flash.param) return
    const row = rootRef.current?.querySelector<HTMLElement>(`[data-param="${flash.param}"]`)
    try { row?.scrollIntoView?.({ block: 'nearest' }) } catch { /* jsdom */ }
    if (flash.focus) row?.querySelector<HTMLElement>('input, select, button')?.focus()
    const t = setTimeout(() => setDoneSeq(flash.seq), FLASH_MS)
    return () => clearTimeout(t)
  }, [flash, nodeId])

  if (rows.length === 0) return <div className="nb-insp-empty">No parameters.</div>

  return (
    <div ref={rootRef} className="nb-insp-params" style={catVars(categoryOf(node.type))}>
      {rows.map(({ key, value, spec }) => {
        const stored = key in node.params
        const changed = spec != null && stored && !sameValue(node.params[key], spec.default)
        const slider = spec && (spec.type === 'number' || spec.type === 'int')
          && typeof spec.min === 'number' && typeof spec.max === 'number'
        const unit = spec?.unit ? ` (${spec.unit})` : ''
        return (
          <div key={key}>
            <div
              className={`nb-insp-prow${lit === key ? ' nb-insp-prow--flash' : ''}`}
              data-testid={`nb-inspector-param-${key}`}
              data-param={key}
              title={`${key}${unit}`}
              onKeyDown={editable ? e => stepNumberField(e, spec) : undefined}
            >
              {changed && (
                <span
                  className="nb-insp-prow__changed"
                  role="img"
                  aria-label="changed from default"
                  title={`Changed from default ${valueText(spec!.default)}. Right-click to reset`}
                  onContextMenu={e => {
                    if (!editable) return
                    e.preventDefault()
                    updateNodeParams(nodeId, { [key]: spec!.default })
                  }}
                />
              )}
              {editable ? (
                <ParamRow
                  nodeId={nodeId}
                  paramKey={key}
                  value={value}
                  typeSpec={entry?.paramTypes?.[key]}
                  spec={spec}
                  variant="inspector"
                />
              ) : (
                <div className="nb-insp-prow__ro" onContextMenu={e => openParamMenu(e, nodeId, key)}>
                  <span className="nb-insp-prow__label">{spec?.label || key}</span>
                  <span className="nb-insp-prow__value">{valueText(value)}</span>
                </div>
              )}
            </div>
            {slider && (
              <ParamSlider nodeId={nodeId} paramKey={key} spec={spec!} value={value} editable={editable} />
            )}
          </div>
        )
      })}
    </div>
  )
}

export function ParametersCount({ node }: InspectorSectionProps) {
  const specs = catalogEntry(node.type)?.params ?? []
  const n = rowsFor({ ...defaultsOf(specs), ...node.params }, specs, true).length
  return <>{n || ''}</>
}

// ---------------------------------------------------------------------------
// Stream
// ---------------------------------------------------------------------------

const NO_WIRES: readonly GraphWire[] = []

/** `name` and `type` of some nodes, as two flat maps (shallow-comparable). */
function namesOf(graph: Graph | null, ids: readonly string[]): { names: Record<string, string>; types: Record<string, string> } {
  const names: Record<string, string> = {}
  const types: Record<string, string> = {}
  for (const id of ids) {
    const n = graph?.nodes[id]
    if (n) { names[id] = n.name; types[id] = n.type }
  }
  return { names, types }
}

export function StreamSection({ nodeId, node, editable }: InspectorSectionProps) {
  const streams = useStreams()
  const select = useNodeBuilderStore(s => s.setSelection)
  // Only the wires into this node: an edit elsewhere does not re-render it.
  const wiresIn = useInspectorSelect(g => g?.wires.filter(w => w.to === nodeId) ?? NO_WIRES)
  const reads = readsOf(node)
  const writes = writesOf(node)
  const cat = categoryOf(node.type)
  const input = inputStreamOf(nodeId, streams, wiresIn)
  const connected = wiresIn.map(w => w.to_port)
  const ports = portsOf(node.type, connected).filter(p => !p.spare)

  // Input attributes grouped by the node that wrote them, in stream order.
  const groups: { writer: string | null; names: string[] }[] = []
  for (const a of input.attrs) {
    const g = groups.find(x => x.writer === a.written_by)
    if (g) g.names.push(a.name)
    else groups.push({ writer: a.written_by, names: [a.name] })
  }
  const otherIds = [...groups.map(g => g.writer).filter((w): w is string => w != null), ...wiresIn.map(w => w.from)]
  const names = useInspectorSelect(g => namesOf(g, otherIds).names)
  const types = useInspectorSelect(g => namesOf(g, otherIds).types)

  const connect = (port: string) => {
    const canvas = getActiveCanvas()
    if (!canvas) return
    canvas.openTabMenu({
      onCreate(newId, ctx) {
        const s = ctx.store.getState()
        const g = s.graph
        if (!g) return
        // The Tab menu is not filtered here, so the picked node may have no
        // output (Entry, Exit, Settings): say why instead of throwing (FC-1).
        const problem = connectionProblem(g, { source: newId, target: nodeId, sourceHandle: 'out', targetHandle: port })
        if (problem) {
          s.showFlash(wireProblemText(problem))
          return
        }
        s.commit('add wire', gr => connectWire(gr, { id: newWireId(), from: newId, to: nodeId, to_port: port }))
      },
    })
  }

  return (
    <div className="nb-insp-stream">
      <div className="nb-insp-lbl">reads</div>
      <div className="nb-insp-chips">
        {reads.length === 0 ? <span className="nb-insp-dim">none</span> : reads.map(r => (
          <span key={r} className="nb-chip">{r}</span>
        ))}
      </div>
      <div className="nb-insp-lbl">writes</div>
      <div className="nb-insp-chips" style={catVars(cat)}>
        {writes.length === 0 ? <span className="nb-insp-dim">none</span> : writes.map(w => (
          w.param
            ? <WriteChip key={`${w.param}:${w.name}`} nodeId={nodeId} param={w.param} name={w.name} editable={editable} />
            : <span key={w.name} className="nb-chip nb-chip--write nb-chip--readonly">+{w.name}</span>
        ))}
      </div>
      {input.wired && (
        <>
          <div className="nb-insp-lbl">input stream · {input.attrs.length} attrs</div>
          {groups.map(g => {
            const writerName = g.writer ? names[g.writer] : undefined
            return (
              <div key={g.writer ?? '?'} className="nb-insp-srow">
                <span>{g.names.join(' ')}</span>
                {writerName !== undefined && (
                  <span className="nb-insp-by" style={catVars(categoryOf(types[g.writer!]))}>{writerName}</span>
                )}
              </div>
            )
          })}
        </>
      )}
      {ports.length > 0 && (
        <div className="nb-insp-ports">
          {ports.map(p => {
            const wire = wiresIn.find(w => w.to_port === p.id)
            if (wire) {
              return (
                <div key={p.id} className="nb-insp-port" data-testid={`nb-inspector-port-${p.id}`}>
                  <span>{p.label} ← {names[wire.from] ?? wire.from} out</span>
                  <button type="button" className="nb-btn nb-btn--text" onClick={() => select({ wireIds: [wire.id] })}>
                    Select wire
                  </button>
                </div>
              )
            }
            if (p.optional) return null
            return (
              <div key={p.id} className="nb-insp-port nb-insp-port--missing" data-testid={`nb-inspector-port-${p.id}`}>
                <span>{p.label} · not connected</span>
                {editable && (
                  <button type="button" className="nb-btn nb-btn--text" onClick={() => connect(p.id)}>
                    Connect…
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

export function DiagnosticsSection({ nodeId }: InspectorSectionProps) {
  const list = useNodeDiagnostics(nodeId)
  if (list.length === 0) {
    return <div className="nb-insp-ok" data-testid="nb-inspector-diag-clean">✓ no issues on this node</div>
  }
  const activate = (d: Diagnostic) => {
    if (focusDiagnosticWire(d)) return
    if (d.param) {
      setSectionOpen('parameters', true)
      flashParam(nodeId, d.param, true)
    }
  }
  return (
    <div className="nb-insp-diags" role="list">
      {list.map((d, i) => (
        // The list item wraps the button, so the row keeps its button role (UX-12).
        <div key={i} role="listitem">
          <button
            type="button"
            className="nb-insp-diag"
            data-testid={`nb-inspector-diag-${i}`}
            onClick={() => activate(d)}
          >
            <span className={`nb-insp-diag__dot nb-insp-diag__dot--${d.severity}`} aria-hidden="true" />
            <span className="nb-insp-diag__msg">{d.message}</span>
            <span className="nb-insp-diag__code">{d.code}</span>
          </button>
        </div>
      ))}
    </div>
  )
}

export function DiagnosticsCount({ nodeId }: InspectorSectionProps) {
  const { text, kind } = diagnosticsCountText(useNodeDiagnostics(nodeId))
  if (!text) return null
  return <span className={`nb-insp-sec__count--${kind}`}>{text}</span>
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

registerInspectorSection({ id: 'parameters', title: 'Parameters', order: 10, Component: ParametersSection, Count: ParametersCount })
registerInspectorSection({ id: 'stream', title: 'Stream', order: 30, Component: StreamSection })
registerInspectorSection({ id: 'diagnostics', title: 'Diagnostics', order: 40, Component: DiagnosticsSection, Count: DiagnosticsCount })
