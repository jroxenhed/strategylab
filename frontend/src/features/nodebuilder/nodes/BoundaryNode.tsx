/**
 * Boundary cards (W6 item 6.C, spec S38): inside a dived network, its
 * `subnet_input` and `subnet_output` nodes draw as half-height cards (26px
 * by 160px, dashed border). Outside, they are the ports of the network's
 * frame or card instead, never both at once (S31 rule). Registered as the
 * React Flow node type `nbBoundary` (plugins/networkDive.ts loads it).
 *
 * - `subnet_input`: glyph ▽, its name (double-click to rename inline), the
 *   type slot `input · 5 attrs`, and one output port at the bottom.
 * - `subnet_output`: glyph △, its name, `output · 7 attrs`, and one input
 *   port at the top.
 * - No flags, no params. They can be moved like any node.
 */

import { useState, type CSSProperties } from 'react'
import { Handle, Position, type Node as RFNode, type NodeProps } from '@xyflow/react'
import { registerNodeType, type NodeComponent } from '../nodeTypes'
import { BOUNDARY_RF_TYPE, type BoundaryNodeData } from '../rfMapping'
import { useNodeBuilderStore } from '../store'
import { useNodeStream } from '../useDiagnostics'
import { commitRename } from '../operations/rename'
import { isValidName } from '../paths'
import { boundaryTypeText } from './subnetFormat'
import './subnetNode.css'

export type BoundaryRFNode = RFNode<BoundaryNodeData, typeof BOUNDARY_RF_TYPE>

const catVars = { '--cat': 'var(--nb-cat-network)', '--tint': 'var(--nb-tint-network)' } as CSSProperties

export default function BoundaryNode({ id, data }: NodeProps<BoundaryRFNode>) {
  const { boundary, editable } = data
  const isInput = boundary.kind === 'input'
  const stream = useNodeStream(id)
  const attrs = stream ? stream.points.length : null
  const [editing, setEditing] = useState(false)
  const name = data.name ?? id
  const typeText = boundaryTypeText(boundary.kind, attrs)
  const tip = stream ? stream.points.map(a => a.name).join(', ') : undefined

  return (
    <>
      {!isInput && (
        <Handle
          type="target"
          id="in0"
          position={Position.Top}
          isConnectable={editable === true}
          className="nb-port nb-port--in"
          style={{ ...catVars, left: '50%' }}
          aria-label="input"
          data-testid={`nb-port-${id}-in0`}
        />
      )}
      <div
        className="nb-boundary"
        role="group"
        aria-label={`Network ${boundary.kind} ${name}${attrs === null ? '' : `, ${attrs} ${attrs === 1 ? 'attribute' : 'attributes'}`}`}
        data-testid={`nb-boundary-${id}`}
        data-kind={boundary.kind}
        style={catVars}
      >
        <div className="nb-boundary__stripe" />
        <span className="nb-boundary__glyph" aria-hidden="true">{isInput ? '▽' : '△'}</span>
        {editing && isInput && editable ? (
          <NameField
            initial={name}
            onDone={v => {
              setEditing(false)
              if (v !== null && v !== name) commitRename(useNodeBuilderStore, id, v)
            }}
          />
        ) : (
          <span
            className="nb-boundary__name"
            data-testid={`nb-boundary-name-${id}`}
            // Only an input is renamed here (S38: the output is always `out`).
            onDoubleClick={isInput && editable ? e => { e.stopPropagation(); setEditing(true) } : undefined}
          >
            {name}
          </span>
        )}
        <span className="nb-boundary__type" title={tip} data-testid={`nb-boundary-type-${id}`}>{typeText}</span>
      </div>
      {isInput && (
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

/** The inline rename field: Enter or blur keeps a valid name, Esc cancels. */
function NameField({ initial, onDone }: { initial: string; onDone: (v: string | null) => void }) {
  const [text, setText] = useState(initial)
  const ok = isValidName(text)
  const finish = (keep: boolean) => onDone(keep && ok ? text : null)
  return (
    <input
      className="nodrag nopan"
      aria-label="Rename input"
      autoFocus
      value={text}
      onChange={e => setText(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={e => {
        e.stopPropagation()
        if (e.key === 'Enter') finish(true)
        else if (e.key === 'Escape') finish(false)
      }}
      onDoubleClick={e => e.stopPropagation()}
      style={{
        flex: 1,
        minWidth: 0,
        height: 18,
        background: 'var(--nb-bg-input, var(--nb-bg-elevated))',
        border: `1px solid ${ok ? 'var(--nb-border-focus)' : 'var(--nb-error)'}`,
        borderRadius: 3,
        color: 'var(--nb-text)',
        font: '600 12px/16px var(--nb-font-sans, inherit)',
        padding: '0 4px',
        outline: 'none',
      }}
    />
  )
}

registerNodeType(BOUNDARY_RF_TYPE, BoundaryNode as NodeComponent)
