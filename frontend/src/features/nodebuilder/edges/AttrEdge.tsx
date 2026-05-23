/**
 * AttrEdge — custom wire renderer with midpoint attribute label chip.
 *
 * Uses React Flow's BaseEdge + getBezierPath so we get a proper cubic
 * Bézier that matches the node handle positions. The label chip floats
 * at the geometric midpoint of the path.
 *
 * Stroke: var(--nb-wire) resting, var(--nb-wire-selected) when selected.
 * Label chip: small rounded rect with mono text (the wire's attr label).
 */

import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from '@xyflow/react'

export default function AttrEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
  selected,
  style: _style,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const strokeColor = selected ? 'var(--nb-wire-selected)' : 'var(--nb-wire)'
  const strokeWidth = selected ? 2.5 : 1.5

  const hasLabel = typeof label === 'string' && label.length > 0

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: strokeColor,
          strokeWidth,
        }}
      />
      {hasLabel && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'none',
              // chip styles
              background: 'var(--nb-bg-panel)',
              border: '1px solid var(--nb-border)',
              borderRadius: 4,
              height: 14,
              padding: '0 4px',
              display: 'flex',
              alignItems: 'center',
              fontFamily: 'var(--nb-font-mono)',
              fontSize: 10,
              color: 'var(--nb-text-secondary)',
              whiteSpace: 'nowrap',
              lineHeight: 1,
            }}
            className="nodrag nopan"
          >
            {label as string}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
