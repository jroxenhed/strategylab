/**
 * The Inspector's Parameters section for a network node (spec S40): a
 * "Promoted" list on top of the usual rows.
 *
 * Each promoted param is a 24px row: a drag grip (reorder; `Alt+Up` /
 * `Alt+Down` with the row focused is the keyboard path), the label, the
 * control (the same ParamRow the subnet card uses), a reset dot when the
 * value differs from the default, and an overflow menu (Rename label…,
 * Rename name…, Go to target, Unpromote; Remove when the target is gone).
 * Under the list, "+ Promote a parameter…" picks a child param.
 *
 * A locked asset instance shows the same rows with editable values, but
 * no grip, no overflow and no "+ Promote" link, and a muted line "Defined
 * by regime_filter v3".
 *
 * plugins/assets.ts registers it under the id `parameters`, which
 * overrides the built-in section; any other node gets the built-in one.
 */

import { useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import type { GraphNode, GraphPromotedParam } from '../../api/nodebuilder'
import { openPromote, currentNetworkOf } from './assetUi'
import { ParametersCount, ParametersSection } from './inspector/NodeSections'
import type { InspectorSectionProps } from './inspector/sections'
import { PromotedParamRow } from './nodes/ParamRow'
import {
  isNetworkNode,
  movePromoted,
  promotedNameProblem,
  promotedOf,
  promotedValue,
  promoteProblem,
  relabelPromoted,
  renamePromoted,
  resolveTarget,
  unpromoteParam,
} from './operations/promote'
import { insideLockedAsset } from './operations/collapse'
import { useNodeBuilderStore } from './store'
import { paramSpecsOf } from './streamLabels'
import { Dialog } from './ui/Dialog'
import { ActionMenu } from './ui/ActionMenu'
import './assets.css'

function sameValue(a: unknown, b: unknown): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b)
}

/** The node without its promoted values in `params` (the built-in rows show the rest). */
function withoutPromoted(node: GraphNode): GraphNode {
  const list = promotedOf(node)
  if (list.length === 0) return node
  const params = { ...node.params }
  for (const p of list) delete params[p.name]
  return { ...node, params }
}

/** The Parameters section, with the Promoted list for a network node. */
export function ParametersWithPromoted(props: InspectorSectionProps) {
  const { node } = props
  const base = useMemo(() => withoutPromoted(node), [node])
  if (!isNetworkNode(node)) return <ParametersSection {...props} />
  const hasOwn = Object.keys(base.params).length > 0 || paramSpecsOf(node.type).length > 0
  return (
    <>
      <PromotedList networkId={props.nodeId} node={node} editable={props.editable} />
      {hasOwn && <ParametersSection {...props} node={base} />}
    </>
  )
}

export function ParametersWithPromotedCount(props: InspectorSectionProps) {
  const base = useMemo(() => withoutPromoted(props.node), [props.node])
  if (!isNetworkNode(props.node)) return <ParametersCount {...props} />
  const n = promotedOf(props.node).length
  return (
    <>
      {n > 0 ? `${n}` : ''}
      {n > 0 && (Object.keys(base.params).length > 0 || paramSpecsOf(props.node.type).length > 0) ? ' + ' : ''}
      {(Object.keys(base.params).length > 0 || paramSpecsOf(props.node.type).length > 0) && <ParametersCount {...props} node={base} />}
    </>
  )
}

type Ask = { kind: 'label' | 'name'; entry: GraphPromotedParam } | { kind: 'pick' } | null

function PromotedList({ networkId, node, editable }: { networkId: string; node: GraphNode; editable: boolean }) {
  const list = promotedOf(node)
  const locked = !!node.locked
  const canArrange = editable && !locked
  // The row whose ⋯ menu is open, and its button (UX-06: a real menu).
  const [menu, setMenu] = useState<{ name: string; anchor: HTMLElement } | null>(null)
  const [ask, setAsk] = useState<Ask>(null)
  const [dragName, setDragName] = useState<string | null>(null)
  const commit = useNodeBuilderStore(s => s.commit)
  // Targets that no longer resolve (a child deleted or renamed outside the
  // rename path), as one string so other edits do not re-render the list.
  const missingKey = useNodeBuilderStore(s => (locked || !s.graph
    ? ''
    : list.filter(e => !resolveTarget(s.graph!, networkId, e.target)).map(e => e.name).join('\u0000')))
  const missingSet = new Set(missingKey ? missingKey.split('\u0000') : [])
  const inLockedParent = useNodeBuilderStore(s => insideLockedAsset(s.graph?.nodes ?? {}, networkId))

  const move = (name: string, delta: number) => {
    commit('reorder promoted params', g => movePromoted(g, networkId, name, delta))
  }

  const onRowKey = (e: ReactKeyboardEvent, name: string) => {
    if (!canArrange || !e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return
    e.preventDefault()
    move(name, e.key === 'ArrowUp' ? -1 : 1)
  }

  const goToTarget = (entry: GraphPromotedParam) => {
    const s = useNodeBuilderStore.getState()
    const g = s.graph
    if (!g) return
    const t = resolveTarget(g, networkId, entry.target)
    if (!t) return
    // Dive into the network and select the child.
    if (currentNetworkOf(s) !== networkId) s.enterNetwork(networkId, { select: t.nodeId, reveal: 'offscreen' })
    else s.setSelection({ nodeIds: [t.nodeId], primary: t.nodeId })
  }

  return (
    <div className="nb-promoted-list" data-testid="nb-inspector-promoted">
      <div className="nb-promoted-list__title">
        <span>Promoted</span>
        <span>{list.length}</span>
      </div>
      {locked && node.asset_ref && (
        <div className="nb-promoted-list__defined">{`Defined by ${node.asset_ref.name} v${node.asset_ref.version}`}</div>
      )}
      {list.map(entry => {
        const missing = missingSet.has(entry.name)
        const value = promotedValue(node, entry)
        const changed = !sameValue(value, entry.default)
        return (
          <div
            key={entry.name}
            className="nb-promoted-item"
            data-testid={`nb-inspector-promoted-${entry.name}`}
            onKeyDown={e => onRowKey(e, entry.name)}
            onDragOver={canArrange && dragName ? e => e.preventDefault() : undefined}
            onDrop={canArrange && dragName ? e => {
              e.preventDefault()
              const from = list.findIndex(p => p.name === dragName)
              const to = list.findIndex(p => p.name === entry.name)
              if (from >= 0 && to >= 0 && from !== to) move(dragName, to - from)
              setDragName(null)
            } : undefined}
          >
            {canArrange && (
              <span
                className="nb-promoted-item__grip"
                aria-roledescription="drag handle"
                aria-label={`Reorder ${entry.label}`}
                draggable
                onDragStart={() => setDragName(entry.name)}
                onDragEnd={() => setDragName(null)}
              >
                ⋮⋮
              </span>
            )}
            <div className="nb-promoted-item__row">
              {editable ? (
                <PromotedParamRow networkId={networkId} entry={entry} variant="inspector" />
              ) : (
                <div className="nb-insp-prow__ro">
                  <span className="nb-insp-prow__label">{entry.label}</span>
                  <span className="nb-insp-prow__value">{value === null || value === undefined ? '' : String(value)}</span>
                </div>
              )}
            </div>
            {editable && changed && (
              <button
                type="button"
                className="nb-insp-prow__changed"
                aria-label={`Reset ${entry.label} to ${String(entry.default)}`}
                title={`Changed from default ${String(entry.default)}. Click to reset`}
                style={{ border: 'none', padding: 0, cursor: 'pointer' }}
                onClick={() => useNodeBuilderStore.getState().updateNodeParams(networkId, { [entry.name]: entry.default as never })}
              />
            )}
            {canArrange && (
              <span style={{ position: 'relative' }}>
                <button
                  type="button"
                  className="nb-promoted-item__more"
                  aria-label={`More for ${entry.label}`}
                  aria-haspopup="menu"
                  aria-expanded={menu?.name === entry.name}
                  onClick={e => {
                    const el = e.currentTarget
                    setMenu(m => (m?.name === entry.name ? null : { name: entry.name, anchor: el }))
                  }}
                >
                  ⋯
                </button>
                {menu?.name === entry.name && (
                  <ActionMenu
                    anchor={menu.anchor}
                    onClose={() => setMenu(null)}
                    ariaLabel={`More for ${entry.label}`}
                    width={160}
                    items={missing
                      ? [{ id: 'remove', label: 'Remove', onSelect: () => commit('remove promoted param', g => unpromoteParam(g, networkId, entry.name)) }]
                      : [
                          { id: 'label', label: 'Rename label…', onSelect: () => setAsk({ kind: 'label', entry }) },
                          { id: 'name', label: 'Rename name…', onSelect: () => setAsk({ kind: 'name', entry }) },
                          { id: 'target', label: 'Go to target', onSelect: () => goToTarget(entry) },
                          { id: 'unpromote', label: 'Unpromote', onSelect: () => commit('unpromote param', g => unpromoteParam(g, networkId, entry.name)) },
                        ]}
                  />
                )}
              </span>
            )}
          </div>
        )
      })}
      {canArrange && !inLockedParent && (
        <button type="button" className="nb-promoted-link" onClick={() => setAsk({ kind: 'pick' })}>
          + Promote a parameter…
        </button>
      )}
      {ask && ask.kind !== 'pick' && (
        <RenameEntryDialog networkId={networkId} node={node} kind={ask.kind} entry={ask.entry} onClose={() => setAsk(null)} />
      )}
      {ask?.kind === 'pick' && <PickParamDialog networkId={networkId} onClose={() => setAsk(null)} />}
    </div>
  )
}

/** Rename a promoted param's label or name. */
function RenameEntryDialog({
  networkId,
  node,
  kind,
  entry,
  onClose,
}: {
  networkId: string
  node: GraphNode
  kind: 'label' | 'name'
  entry: GraphPromotedParam
  onClose: () => void
}) {
  const [text, setText] = useState(kind === 'label' ? entry.label : entry.name)
  const problem = kind === 'name'
    ? (text === entry.name ? null : promotedNameProblem(node, text, entry.name))
    : (text.trim() ? null : 'Enter a label')
  const apply = () => {
    if (problem) return
    const s = useNodeBuilderStore.getState()
    if (kind === 'label') s.commit('rename promoted label', g => relabelPromoted(g, networkId, entry.name, text.trim()))
    else s.commit('rename promoted name', g => renamePromoted(g, networkId, entry.name, text))
    onClose()
  }
  return (
    <Dialog
      title={kind === 'label' ? 'Rename label' : 'Rename name'}
      onCancel={onClose}
      width={300}
      primaryLabel="Rename"
      onPrimary={apply}
      primaryDisabled={!!problem}
      primaryDisabledReason={problem ?? undefined}
    >
      <div className="nb-asset-field">
        <input
          className={`nb-asset-input${kind === 'name' ? ' nb-asset-input--mono' : ''}`}
          aria-label={kind === 'label' ? 'Label' : 'Name'}
          aria-invalid={problem ? true : undefined}
          value={text}
          onChange={e => setText(e.target.value)}
        />
        {problem && <span className="nb-asset-line nb-asset-line--error">{problem}</span>}
      </div>
    </Dialog>
  )
}

/** "+ Promote a parameter…": the network's children and their params. */
function PickParamDialog({ networkId, onClose }: { networkId: string; onClose: () => void }) {
  const graph = useNodeBuilderStore(s => s.graph)
  const children = useMemo(
    () => Object.values(graph?.nodes ?? {}).filter(n => n.parent === networkId && n.type !== 'subnet_input' && n.type !== 'subnet_output'),
    [graph, networkId],
  )
  return (
    <Dialog title="Promote a parameter" onCancel={onClose} width={320} data-testid="nb-promote-picker">
      {children.length === 0 && <div className="nb-asset-help">This network has no nodes yet.</div>}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {children.map(child => {
          const params = [
            ...paramSpecsOf(child.type).map(p => ({ name: p.name, label: p.label || p.name })),
            ...promotedOf(child).map(p => ({ name: p.name, label: p.label })),
            // Same rules as the param menu: not a network's own params, not a port's, not twice.
          ].filter(p => promoteProblem(graph, child.id, p.name) === null)
          if (params.length === 0) return null
          return (
            <li key={child.id} style={{ marginBottom: 6 }}>
              <div className="nb-asset-section__title" style={{ fontFamily: 'var(--nb-font-mono)' }}>{child.name}</div>
              <div className="nb-promoted-menu" role="menu" aria-label={child.name}>
                {params.map(p => (
                  <button
                    key={p.name}
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      onClose()
                      openPromote({ nodeId: child.id, param: p.name })
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </li>
          )
        })}
      </ul>
    </Dialog>
  )
}
