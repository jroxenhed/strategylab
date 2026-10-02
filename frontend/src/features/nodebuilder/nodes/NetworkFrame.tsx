/**
 * Network frame (W5, spec S31): every network (`subnet`, `regime`,
 * `output_group`) is drawn expanded, as a frame around its children, with
 * its ports on the frame edge. Registered as the React Flow node type
 * `nbNetworkFrame` (plugins/networkFrames.ts loads this module).
 *
 * - The frame rectangle and its ports come from `computeFrameLayouts`
 *   (rfMapping.ts), worked out when the graph changes, never here.
 * - Children are React Flow children (`parentId`), drawn by their own
 *   renderers. Boundary nodes are not drawn: they are the ports.
 * - Input ports sit on the top edge. Each port is two handles at the same
 *   spot: the target `in<k>` takes the wire from outside, and the source
 *   `bnd:<subnet_input id>` starts the wire inside. Together they read as
 *   one line with a dot on the frame edge. A subnet's output sits bottom
 *   centre the same way.
 * - Only the tab, the border and the ports take the pointer: a click or a
 *   drag in empty frame space goes to the canvas (marquee), so a network is
 *   selected by its tab (Houdini rule).
 * - An Output Group's tab carries the group header (OutputGroupHeader.tsx),
 *   and missing required terminals show as ghost cards in a bottom row.
 */

import { useCallback, type CSSProperties } from 'react'
import { Handle, Position, type Node as RFNode, type NodeProps } from '@xyflow/react'
import { registerNodeType, type NodeComponent } from '../nodeTypes'
import { useNodeBuilderStore } from '../store'
import { useNodeDiagnostics } from '../useDiagnostics'
import { DiagnosticBadge } from './DiagnosticBadge'
import { FRAME_PAD, FRAME_RF_TYPE, GHOST, BOUNDARY_HANDLE_PREFIX, type FrameNodeData, type MissingTerminal } from '../rfMapping'
import { addGroupTerminal } from '../networkOps'
import OutputGroupHeader from './OutputGroupHeader'
import { EMPTY_FRAME_TEXT, frameAriaLabel } from './networkFormat'
import './networkFrame.css'

export type FrameRFNode = RFNode<FrameNodeData, typeof FRAME_RF_TYPE>

const GLYPH: Record<string, { glyph: string; color: string }> = {
  output_group: { glyph: 'O', color: 'var(--nb-cat-output)' },
  subnet: { glyph: 'N', color: 'var(--nb-cat-network)' },
  regime_net: { glyph: 'R', color: 'var(--nb-cat-network)' },
}

// Handle styles: React Flow places a Top handle on the top edge and a
// Bottom handle on the bottom edge; the inside half of a port is moved onto
// the same spot as the outside half.
const onTop = (x: number): CSSProperties => ({ left: x, top: 0, bottom: 'auto', transform: 'translate(-50%, -50%)' })
const onBottom = (x: number | string): CSSProperties => ({ left: x, top: 'auto', bottom: 0, transform: 'translate(-50%, 50%)' })

/** The pointer-catching strips along the border (drag the border = drag the frame). */
const EDGE = 6
const edgeStrips: CSSProperties[] = [
  { left: 0, right: 0, top: 0, height: EDGE },
  { left: 0, right: 0, bottom: 0, height: EDGE },
  { top: 0, bottom: 0, left: 0, width: EDGE },
  { top: 0, bottom: 0, right: 0, width: EDGE },
]

export default function NetworkFrame({ id, data, selected }: NodeProps<FrameRFNode>) {
  const { frame, backendType, editable, bypass } = data
  const isGroup = backendType === 'output_group'
  const diagnostics = useNodeDiagnostics(id)
  const hasError = diagnostics.some(d => d.severity === 'error')
  const select = useNodeBuilderStore(s => s.select)
  const glyph = GLYPH[backendType] ?? GLYPH.subnet

  const addGhost = useCallback((m: MissingTerminal, i: number) => {
    const s = useNodeBuilderStore.getState()
    if (!s.graph || s.graph.readOnly) return
    const pos: [number, number] = [
      frame.x + FRAME_PAD + i * (GHOST.w + GHOST.gap),
      frame.y + frame.h - FRAME_PAD - GHOST.h,
    ]
    let added = null as string | null
    s.commit(`add ${m.type}`, g => {
      const res = addGroupTerminal(g, id, m, pos)
      added = res.nodeId
      return res.graph
    })
    if (added) s.select(added)
  }, [frame.x, frame.y, frame.h, id])

  const className = [
    'nb-frame',
    isGroup && 'nb-frame--group',
    selected && 'nb-frame--selected',
    hasError && 'nb-frame--error',
  ].filter(Boolean).join(' ')

  return (
    <div
      className={className}
      role="group"
      aria-label={frameAriaLabel(data)}
      data-testid={`nb-frame-${id}`}
      data-frame-type={backendType}
      style={{ pointerEvents: 'none' }}
    >
      {edgeStrips.map((st, i) => (
        <div key={i} aria-hidden="true" style={{ position: 'absolute', pointerEvents: 'auto', cursor: 'grab', ...st }} />
      ))}

      <div
        role="button"
        tabIndex={0}
        className="nb-frame__tab"
        aria-pressed={!!selected}
        data-testid={`nb-frame-tab-${id}`}
        style={{ pointerEvents: 'auto' }}
        onKeyDown={e => {
          if (e.key === 'Enter' && e.target === e.currentTarget) select(id)
        }}
      >
        <span className="nb-frame__glyph" aria-hidden="true" style={{ background: glyph.color }}>{glyph.glyph}</span>
        {isGroup ? (
          <OutputGroupHeader nodeId={id} name={data.name ?? ''} params={data.params} ticker={data.groupTicker} editable={editable === true} />
        ) : (
          <>
            <span className="nb-frame__name" title={data.nodePath}>{data.name}</span>
            <span className="nb-frame__meta">
              {backendType === 'regime_net' ? 'regime' : `subnet · ${frame.childCount} ${frame.childCount === 1 ? 'node' : 'nodes'}`}
            </span>
          </>
        )}
        {diagnostics.length > 0 && <DiagnosticBadge nodeId={id} diagnostics={diagnostics} />}
        {!isGroup && bypass && <span className="nb-frame__bypass-dot" title="Bypassed" aria-label="bypassed" />}
      </div>

      {frame.inputs.map(p => (
        <div key={p.boundaryId}>
          <span className="nb-frame__port-label" style={{ left: p.x }}>{p.label}</span>
          <Handle
            type="target"
            id={p.handle}
            position={Position.Top}
            className="nb-frame__port"
            style={{ ...onTop(p.x), pointerEvents: 'auto' }}
            isConnectable={editable === true}
            aria-label={`input ${p.label}`}
            data-testid={`nb-frame-port-${id}-${p.handle}`}
          />
          <Handle
            type="source"
            id={`${BOUNDARY_HANDLE_PREFIX}${p.boundaryId}`}
            position={Position.Bottom}
            className="nb-frame__port nb-frame__port--inner"
            style={onTop(p.x)}
            isConnectable={false}
          />
        </div>
      ))}

      {frame.outputId && (
        <>
          <Handle
            type="target"
            id={`${BOUNDARY_HANDLE_PREFIX}${frame.outputId}`}
            position={Position.Top}
            className="nb-frame__port nb-frame__port--inner"
            style={onBottom('50%')}
            isConnectable={false}
          />
          <Handle
            type="source"
            id="out"
            position={Position.Bottom}
            className="nb-frame__port nb-frame__port--out"
            style={{ ...onBottom('50%'), pointerEvents: 'auto' }}
            isConnectable={editable === true}
            aria-label="output"
            data-testid={`nb-frame-out-${id}`}
          />
        </>
      )}

      {frame.missing.map((m, i) => (
        <button
          key={`${m.type}:${m.side ?? ''}`}
          type="button"
          className="nb-frame__ghost nodrag"
          data-testid={`nb-frame-ghost-${id}-${m.type}${m.side ? `-${m.side}` : ''}`}
          style={{
            left: FRAME_PAD + i * (GHOST.w + GHOST.gap),
            top: frame.h - FRAME_PAD - GHOST.h,
            pointerEvents: 'auto',
          }}
          disabled={editable !== true}
          title={editable === true ? `Add the ${m.type} terminal` : undefined}
          onClick={() => addGhost(m, i)}
        >
          {m.label}
        </button>
      ))}

      {frame.childCount === 0 && frame.missing.length === 0 && (
        <div className="nb-frame__empty">{EMPTY_FRAME_TEXT}</div>
      )}
    </div>
  )
}


registerNodeType(FRAME_RF_TYPE, NetworkFrame as NodeComponent)
