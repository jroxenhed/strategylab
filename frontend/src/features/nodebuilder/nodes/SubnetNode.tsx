/**
 * Network card (W6 item 6.C, spec S38): a network shown as one node
 * (`Node.meta.view === "card"`, FA1). Registered as the React Flow node type
 * `nbSubnet` (plugins/networkDive.ts loads this module).
 *
 * - Header: the network glyph, the name, and the type slot: `subnet · 7
 *   nodes`, `subnet · empty`, or for an asset instance `regime_filter @ v3`
 *   with a lock glyph when locked (`regime_filter v3 · local copy` when
 *   unlocked, `· missing` when the library no longer has it).
 * - Ports: one input per `subnet_input` child (in port order, labelled with
 *   the boundary node's name), one output when a `subnet_output` exists.
 * - Body: promoted params as ordinary rows (S40, item 6.D's `PromotedRows`:
 *   at most 4, the rest in the Inspector). Then the attributes the network
 *   writes.
 * - Double-click on the header dives into the network (S37). X / Shift+X
 *   switch it back to the frame view (commands/network.ts).
 *
 * The card facts (ports, child count, asset) come from rfMapping's
 * `cardInfoOf`, worked out when the graph changes, never here.
 */

import { useMemo, type CSSProperties } from 'react'
import { Handle, Position, type Node as RFNode, type NodeProps } from '@xyflow/react'
import { registerNodeType, type NodeComponent } from '../nodeTypes'
import { CARD_RF_TYPE, type CardNodeData } from '../rfMapping'
import { portLeft } from '../streamLabels'
import { useNodeDiagnostics, useStreams } from '../useDiagnostics'
import { diveInto } from '../networkNav'
import { DiagnosticBadge } from './DiagnosticBadge'
import { PromotedRows } from './ParamRow'
import { cardAriaLabel, cardTypeText, CARD_GLYPH } from './subnetFormat'
import { useAssetLibrary } from '../assetUi'
import { hasRulesPalette, LIFECYCLE_TEXT, newerVersionOf } from '../assetLifecycle'
import './subnetNode.css'

export type SubnetRFNode = RFNode<CardNodeData, typeof CARD_RF_TYPE>

const catVars = { '--cat': 'var(--nb-cat-network)', '--tint': 'var(--nb-tint-network)' } as CSSProperties

export default function SubnetNode({ id, data }: NodeProps<SubnetRFNode>) {
  const { card, editable, bypass, node, backendType } = data
  const diagnostics = useNodeDiagnostics(id)
  const hasError = diagnostics.some(d => d.severity === 'error')
  const missing = diagnostics.some(d => d.code === 'asset_missing')
  const promotedCount = node?.promoted?.length ?? 0
  const glyph = CARD_GLYPH[backendType] ?? CARD_GLYPH.subnet
  const type = cardTypeText({ type: backendType, card, missing })
  // S38 (UX-04): a dot after the version when the library has a newer one,
  // and the Rules colour on the type slot when the asset has a Rules entry.
  const ref = card.asset ? { asset_ref: { name: card.asset.name, version: card.asset.version } } : null
  const newer = useAssetLibrary(s => newerVersionOf(ref, s.items))
  const rules = useAssetLibrary(s => hasRulesPalette(ref, s.items))

  // What the network writes: attributes on its output stream that none of
  // its inputs bring in (from the last /validate streams).
  const streams = useStreams()
  const writes = useMemo(() => {
    const out = streams[id]
    if (!out) return []
    const incoming = new Set<string>()
    for (const p of card.inputs) for (const a of streams[p.boundaryId]?.points ?? []) incoming.add(a.name)
    return out.points.map(a => a.name).filter(n => !incoming.has(n))
  }, [streams, id, card.inputs])

  const className = [
    'nb-subnet',
    card.asset?.locked && 'nb-subnet--locked',
    (hasError || missing) && 'nb-subnet--error',
    bypass && 'nb-subnet--bypass',
  ].filter(Boolean).join(' ')

  return (
    <>
      {card.inputs.map((p, i) => {
        const left = portLeft(i, card.inputs.length)
        return (
          <div key={p.boundaryId}>
            <span className="nb-subnet__port-label" style={{ left }} aria-hidden="true">{p.label}</span>
            <Handle
              type="target"
              id={p.handle}
              position={Position.Top}
              isConnectable={editable === true}
              className="nb-port nb-port--in"
              style={{ ...catVars, left }}
              aria-label={`input ${p.label}`}
              data-testid={`nb-port-${id}-${p.handle}`}
            />
          </div>
        )
      })}

      <div
        className={className}
        role="group"
        aria-label={cardAriaLabel({ name: data.name ?? id, card })}
        data-testid={`nb-subnet-${id}`}
        style={catVars}
      >
        <div className="nb-subnet__stripe" />
        <div className="nb-subnet__stripe2" data-testid={`nb-subnet-stripe2-${id}`} />
        <div
          className="nb-subnet__header"
          data-testid={`nb-subnet-header-${id}`}
          onDoubleClick={e => {
            // Dive (S37). Stop here so the canvas does not open the Tab menu.
            e.stopPropagation()
            diveInto(id)
          }}
        >
          <span className="nb-subnet__glyph" aria-hidden="true" style={{ background: glyph.color }}>{glyph.glyph}</span>
          <span className="nb-subnet__name" title={data.nodePath}>{data.name}</span>
          <span
            className={`nb-subnet__type${missing ? ' nb-subnet__type--missing' : rules && card.asset?.locked ? ' nb-subnet__type--rules' : ''}`}
            data-testid={`nb-subnet-type-${id}`}
          >
            {type.text}
            {newer !== null && !missing && (
              <span className="nb-subnet__newer" role="img" aria-label={LIFECYCLE_TEXT.available(newer)} title={LIFECYCLE_TEXT.available(newer)} data-testid={`nb-subnet-newer-${id}`} />
            )}
            {card.asset?.locked && <span className="nb-subnet__lock" aria-label="locked" role="img">🔒</span>}
            {type.noOutput && <span className="nb-subnet__warn">· no output</span>}
          </span>
          {diagnostics.length > 0 && <DiagnosticBadge nodeId={id} diagnostics={diagnostics} />}
          {bypass && <span className="nb-subnet__bypass-dot" title="Bypassed" aria-label="bypassed" />}
        </div>

        {(promotedCount > 0 || writes.length > 0 || card.childCount === 0) && (
          <div className="nb-subnet__body" data-testid={`nb-subnet-body-${id}`}>
            {/* Promoted params (S40): editable here, the value lives on this node. */}
            {promotedCount > 0 && <PromotedRows nodeId={id} />}
            {card.childCount === 0 && !card.asset?.locked && <div className="nb-subnet__hint">Dive in (I) to add nodes</div>}
            {writes.length > 0 && (
              <div className="nb-subnet__chips">
                {writes.map(w => <span key={w} className="nb-chip nb-subnet__chip--write">{w}</span>)}
              </div>
            )}
          </div>
        )}
      </div>

      {card.outputId && (
        <Handle
          type="source"
          id="out"
          position={Position.Bottom}
          isConnectable={editable === true}
          className="nb-port nb-port--out"
          style={catVars}
          aria-label="output"
          data-testid={`nb-port-${id}-out`}
        />
      )}
    </>
  )
}

registerNodeType(CARD_RF_TYPE, SubnetNode as NodeComponent)
