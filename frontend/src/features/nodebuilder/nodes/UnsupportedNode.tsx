/**
 * UnsupportedNode: the card for a node the compiler cannot run (spec S13).
 *
 * BaseNode draws this instead of the normal card when `isUnsupportedNode`
 * says so (nodes/unsupported.ts). The node stays visible, honest and
 * inert: a dim stripe and a `?` glyph, the stored type in amber, a red
 * badge with the reason, every stored param as a plain read-only row, and
 * no chips or flags. It can still be selected, moved and deleted, and the
 * `Replace with…` command (commands/unsupported.ts) swaps it for a real
 * node. Ports come from the stored wires so the wires keep their shape;
 * they never take a new wire.
 */

import { useMemo } from 'react'
import { Handle, Position, useNodeConnections } from '@xyflow/react'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { hasOutputPort } from '../catalog'
import { useNodeBuilderStore } from '../store'
import { catalogEntry, portLeft, portsOf } from '../streamLabels'
import { DiagnosticBadge } from './DiagnosticBadge'
import { endHoverTip, startHoverTip } from './hoverTip'
import { UNSUPPORTED_CODES, formatStoredParam, localUnsupportedDiagnostic } from './unsupported'
import './node.css'

export interface UnsupportedNodeProps {
  nodeId: string
  /** The stored node type. */
  nodeType: string
  /** The name to show when the node is not in the store graph (read-only view). */
  fallbackName: string
  params: Record<string, unknown>
  /** Input ports that have a wire (`in0`, `in2`, ...). Keep the array stable between renders. */
  connectedPorts: readonly string[]
  diagnostics: readonly Diagnostic[]
  editable: boolean
}

export function UnsupportedNode({
  nodeId,
  nodeType,
  fallbackName,
  params,
  connectedPorts,
  diagnostics,
  editable,
}: UnsupportedNodeProps) {
  const storeName = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.name)
  const name = storeName || fallbackName || nodeType
  const select = useNodeBuilderStore(s => s.select)
  const outgoing = useNodeConnections({ id: nodeId, handleType: 'source' })

  const known = catalogEntry(nodeType) != null
  // A known type draws its own ports; an unknown one only the ports its
  // wires use, so nothing invites a new wire.
  const ports = useMemo(() => {
    const all = portsOf(nodeType, connectedPorts)
    return known ? all.filter(p => !p.spare) : all.filter(p => p.connected)
  }, [nodeType, connectedPorts, known])
  const drawOutput = known ? hasOutputPort(nodeType) : outgoing.length > 0

  // Until /validate answers, the card still says why it cannot run.
  const badgeList = useMemo(
    () => diagnostics.some(d => UNSUPPORTED_CODES.has(d.code))
      ? diagnostics
      : [localUnsupportedDiagnostic(nodeId, nodeType), ...diagnostics],
    [diagnostics, nodeId, nodeType],
  )

  const rows = Object.entries(params)
  const portVars = { '--cat': 'var(--nb-text-dim)' } as React.CSSProperties

  return (
    <>
      {ports.map(p => (
        <Handle
          key={p.id}
          type="target"
          id={p.id}
          position={Position.Top}
          isConnectable={false}
          className={`nb-port nb-port--in${p.connected ? ' nb-port--connected' : ''}`}
          style={{ ...portVars, left: portLeft(p.index, ports.length) }}
          data-testid={`nb-port-${nodeId}-${p.id}`}
          aria-label={`input ${p.label}`}
          onPointerEnter={e => startHoverTip(e.currentTarget, p.label, 'above')}
          onPointerLeave={endHoverTip}
          onPointerDown={endHoverTip}
        />
      ))}

      <div
        className="nb-unsupported"
        role="group"
        data-testid={`nb-node-unsupported-${nodeId}`}
        aria-label={`unsupported node ${name} of type ${nodeType}`}
      >
        <div className="nb-unsupported__stripe" />
        <div className="nb-unsupported__header">
          <span className="nb-unsupported__glyph" aria-hidden="true">?</span>
          <span className="nb-unsupported__name">{name}</span>
          <span className="nb-unsupported__type" title="Not supported yet">{nodeType}</span>
          <DiagnosticBadge nodeId={nodeId} diagnostics={badgeList} onActivate={() => select(nodeId)} />
        </div>
        {(rows.length > 0 || editable) && (
          <div className="nb-unsupported__body">
            {rows.map(([k, v]) => (
              <div key={k} className="nb-unsupported__row">
                <span className="nb-unsupported__label">{k}</span>
                <span className="nb-unsupported__value" title={formatStoredParam(v)}>{formatStoredParam(v)}</span>
              </div>
            ))}
            {editable && <div className="nb-unsupported__hint">unsupported · replace this node</div>}
          </div>
        )}
      </div>

      {drawOutput && (
        <Handle
          type="source"
          id="out"
          position={Position.Bottom}
          isConnectable={false}
          className="nb-port nb-port--out"
          style={portVars}
          data-testid={`nb-port-${nodeId}-out`}
          aria-label="output"
          onPointerEnter={e => startHoverTip(e.currentTarget, 'out', 'below')}
          onPointerLeave={endHoverTip}
          onPointerDown={endHoverTip}
        />
      )}
    </>
  )
}

export default UnsupportedNode
