/**
 * The Inspector with several nodes selected (S14): the count and, when they
 * all share a type, that type; a Bulk section with buttons for the bulk
 * commands; and Shared parameters, where one edit writes every selected node
 * in one commit (one undo step).
 *
 * Bulk buttons find their command by key (B bypass, X collapse, L tidy,
 * Shift+C collapse into subnet), so a command another item registers shows
 * up here on its own, and a key nobody has bound yet shows no button.
 */

import { useRef, useState } from 'react'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import type { ParamSpec } from '../catalog'
import { getCommand, isCommandEnabled, runCommand } from '../commands'
import { updateNodeParams as opUpdateNodeParams } from '../operations'
import { formatChord } from '../shortcutList'
import { useNodeBuilderStore } from '../store'
import { catalogEntry } from '../streamLabels'
import { Button } from '../ui/Button'
import { InspectorSectionShell } from './Section'
import { catVars, categoryOf, coerceParamNumber, commandForKeys, focusCanvas, glyphOf, sameValue, useInspectorGraph, valueText } from './util'
import { FromSidebarRow, sidebarTickerValue, useSidebarWindow } from '../sidebarWindow'

/**
 * Bulk buttons. The key cap is the first chord, formatted for the platform
 * (UX-15). `offLabel`: the label while the command's toggle is on for the
 * primary node (Bypass toggles, so with a bypassed primary it un-bypasses:
 * UX-23).
 */
const BULK: { label: string; offLabel?: string; keys: string[]; id?: string }[] = [
  { label: 'Bypass all', offLabel: 'Un-bypass all', keys: ['b'] },
  { label: 'Collapse all', keys: ['x'] },
  { label: 'Tidy', keys: ['l'], id: 'layout.tidy' },
  { label: 'Collapse into subnet', keys: ['shift+c'] },
]

/** Spec types a shared row can edit with a plain field. */
const SHARED_TYPES: ReadonlySet<string> = new Set(['number', 'int', 'string', 'select', 'bool'])

function SharedParamRow({ spec, nodes, editable }: { spec: ParamSpec; nodes: GraphNode[]; editable: boolean }) {
  const values = nodes.map(n => (spec.name in n.params ? n.params[spec.name] : spec.default))
  const mixed = values.some(v => !sameValue(v, values[0]))
  const shared = mixed ? '' : valueText(values[0])
  // While focused the typed text wins; otherwise the field shows the nodes.
  const [draft, setDraft] = useState<string | null>(null)
  // Set by Esc so the blur that follows does not save the typed text.
  const cancelRef = useRef(false)
  const isNumber = spec.type === 'number' || spec.type === 'int'

  const write = (value: unknown) => {
    const ids = nodes.map(n => n.id)
    useNodeBuilderStore.getState().commit(`edit ${spec.name} on ${ids.length} nodes`, (g: Graph) =>
      ids.reduce((acc, id) => opUpdateNodeParams(acc, id, { [spec.name]: value }), g))
  }
  const commitText = (text: string) => {
    const t = text.trim()
    if (t === shared || t === '') return
    if (isNumber) {
      const n = Number(t)
      if (!Number.isFinite(n)) return
      const v = coerceParamNumber(n, spec)
      if (!mixed && sameValue(v, values[0])) return
      write(v)
    } else {
      write(t)
    }
  }

  const testId = `nb-inspector-shared-${spec.name}`
  let control
  if (!editable) {
    control = <span className="nb-insp-prow__value">{mixed ? 'mixed' : shared}</span>
  } else if (spec.type === 'select' || spec.type === 'bool') {
    const options = spec.type === 'bool' ? ['false', 'true'] : [...(spec.options ?? [])]
    control = (
      <select
        className="nb-insp-field"
        data-testid={testId}
        value={mixed ? '' : shared}
        onChange={e => { if (e.target.value !== '') write(spec.type === 'bool' ? e.target.value === 'true' : e.target.value) }}
      >
        {mixed && <option value="">mixed</option>}
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  } else {
    control = (
      <input
        type="text"
        inputMode={isNumber ? 'decimal' : 'text'}
        className="nb-insp-field"
        data-testid={testId}
        value={draft ?? shared}
        placeholder={mixed ? 'mixed' : undefined}
        onFocus={() => { cancelRef.current = false; setDraft(shared) }}
        onChange={e => setDraft(e.target.value)}
        onBlur={() => {
          if (cancelRef.current || draft === null) { setDraft(null); return }
          const text = draft
          setDraft(null)
          commitText(text)
        }}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() }
          else if (e.key === 'Escape') { cancelRef.current = true; setDraft(null); (e.target as HTMLInputElement).blur(); focusCanvas() }
        }}
      />
    )
  }
  return (
    <label className="nb-insp-prow nb-insp-prow--grid">
      <span className="nb-insp-prow__label" title={spec.name}>{spec.label || spec.name}</span>
      {control}
    </label>
  )
}

export function MultiView({ nodeIds, editable }: { nodeIds: readonly string[]; editable: boolean }) {
  const { graph } = useInspectorGraph()
  const nodes = nodeIds.map(id => graph?.nodes[id]).filter((n): n is GraphNode => n != null)
  const type = nodes.length > 0 && nodes.every(n => n.type === nodes[0].type) ? nodes[0].type : null
  const sidebarWindow = useSidebarWindow()
  const cat = type ? categoryOf(type) : null
  const specs = type ? (catalogEntry(type)?.params ?? []).filter(s => SHARED_TYPES.has(s.type)) : []
  // Re-render when commands may have changed state (selection, graph).
  useNodeBuilderStore(s => s.commitSeq)

  return (
    <>
      <div className="nb-insp-head" style={catVars(cat)}>
        <span className={`nb-insp-head__glyph${type ? '' : ' nb-insp-head__glyph--plain'}`} aria-hidden="true">
          {type ? glyphOf(cat) : '≡'}
        </span>
        <div className="nb-insp-head__text">
          <div className="nb-insp-head__line1">
            <span className="nb-insp-head__title" data-testid="nb-inspector-count">{nodes.length} nodes</span>
            {type && <span className="nb-insp-head__type">{type}</span>}
          </div>
        </div>
      </div>
      {editable && (
        <InspectorSectionShell id="bulk" title="Bulk">
          <div className="nb-insp-bulk">
            {BULK.map(b => {
              const cmd = (b.id ? getCommand(b.id) : undefined) ?? commandForKeys(b.keys)
              if (!cmd) return null
              const enabled = isCommandEnabled(cmd)
              const on = b.offLabel != null && cmd.checked?.(useNodeBuilderStore.getState()) === true
              return (
                <Button
                  key={b.label}
                  keyCap={formatChord(b.keys[0])}
                  disabled={!enabled}
                  disabledReason={cmd.disabledReason?.(useNodeBuilderStore.getState()) ?? 'Not available now'}
                  onClick={() => runCommand(cmd.id)}
                >
                  {on ? b.offLabel : b.label}
                </Button>
              )
            })}
          </div>
        </InspectorSectionShell>
      )}
      {type && specs.length > 0 && (
        <InspectorSectionShell id="shared" title="Shared parameters">
          {specs.map(spec => {
            // D11 before W5: Ticker symbol and interval come from the sidebar (UX-01).
            const sb = sidebarTickerValue(sidebarWindow, type ?? undefined, spec.name)
            return sb != null
              ? <FromSidebarRow key={spec.name} label={spec.label || spec.name} value={sb} testId={`nb-inspector-shared-${spec.name}`} />
              : <SharedParamRow key={spec.name} spec={spec} nodes={nodes} editable={editable} />
          })}
        </InspectorSectionShell>
      )}
    </>
  )
}
