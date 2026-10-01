/**
 * AttrEdge — a wire on the node canvas (spec S11, foundation 5).
 *
 * Path: a bezier straight down out of the output port and straight into the
 * input port (`wirePath`), no arrowheads. A wide invisible path on top makes
 * the wire easy to click.
 *
 * Label: SVG text with a dark halo (no boxes, no foreignObject). The text is
 * what the consumer reads through this wire (`@rsi`, `@a, @b +3`), or a dim
 * `stream` when it reads nothing yet. Canvas.tsx works out the text and the
 * placement (fan-out and overlap rules in streamLabels.ts) and passes them
 * in `data`. A label hidden at rest (a fan-out duplicate, no free spot, or
 * zoomed out) still shows on hover and on the selected wire.
 *
 * States are classes styled in stream.css: hover (CSS), `hot` (an end node
 * is hovered), selected, and the diagnostic stroke when a diagnostic is
 * about this wire.
 *
 * Wire moves (spec S23, plugins/wireOps.ts): `spliceHot` draws the wire hot
 * while a dragged node rests on it (label shown), `flash` draws it red for a
 * moment when a splice or a wire-end drop is refused.
 *
 * Long-wire fade (foundation 5.3): a wire longer than 600 flow units
 * (straight line) is stroked with its own gradient, full at both ends and
 * 35% in the middle, in the wire's colour. Hovered, selected and hot wires
 * keep the plain stroke (wireOps.css). The gradient lives in the wire's own
 * `<defs>`, keyed by wire id.
 *
 * Hovering the wire or its label for 300 ms shows the stream popover: every
 * attribute that flows on the wire, grouped by writer, with the ones the
 * consumer reads ringed. It follows the pointer and takes no pointer events.
 * Following is a direct style write on the card, once per animation frame,
 * never React state, so a mouse move does not re-render the wire.
 */

import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { Edge, EdgeProps } from '@xyflow/react'
import { pointOnWire, wirePath } from '../streamLabels'
import { StreamPopover, streamPopoverPosition } from './StreamPopover'
import '../stream.css'
import './wireOps.css'

/** What Canvas.tsx hands each wire. */
export interface AttrEdgeData extends Record<string, unknown> {
  from: string
  to: string
  /** Drawn label text (`stream` when nothing is read). */
  text: string
  placeholder: boolean
  /** What the consumer reads through this wire. */
  reads: string[]
  /** Label position along the path (0..1) and horizontal nudge (px). */
  t: number
  dx: number
  /** Why the label is hidden at rest, or null when it shows. */
  hidden: 'dup' | 'overlap' | null
  /** Below 60% zoom labels hide at rest. */
  lowZoom: boolean
  /** An end node is hovered. */
  hot: boolean
  /** The message of the diagnostic about this wire, or null. */
  diag: string | null
  fromName: string
  toName: string
  portLabel: string
  /** A dragged node rests on this wire: dropping it splices (plugins/wireOps.ts). */
  spliceHot?: boolean
  /** Briefly red: a refused splice or a wire end dropped on empty canvas. */
  flash?: boolean
}

export type AttrFlowEdge = Edge<AttrEdgeData, 'attr'>

/** How long the pointer rests on a wire before the stream popover shows. */
export const STREAM_POPOVER_DELAY_MS = 300

/** Wires longer than this (flow units, straight line) fade in the middle. */
export const LONG_WIRE = 600

/** The id of a long wire's gradient. */
function fadeId(wireId: string): string {
  return `nb-fade-${wireId}`
}

const PLACEHOLDER_TIP = 'This node does not read anything yet. Pick an attribute on the node.'

export default function AttrEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  label,
  selected,
  data,
}: EdgeProps<AttrFlowEdge>) {
  const path = wirePath(sourceX, sourceY, targetX, targetY)
  const [pop, setPop] = useState<{ x: number; y: number } | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pointRef = useRef({ x: 0, y: 0 })
  const cardRef = useRef<HTMLDivElement | null>(null)
  const frameRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
  }, [])

  // A wire with no data (an older caller) still draws, labelled from `label`.
  const text = data?.text ?? (typeof label === 'string' ? label : '')
  const placeholder = data?.placeholder ?? false
  const t = data?.t ?? 0.5
  const at = pointOnWire(sourceX, sourceY, targetX, targetY, t)
  const restHidden = !selected && !data?.spliceHot && !!data && (data.hidden !== null || data.lowZoom)

  const hot = !!data?.hot || !!data?.spliceHot
  // The fade: long wires at rest only.
  const fade = !selected && !hot && !data?.flash
    && Math.hypot(targetX - sourceX, targetY - sourceY) > LONG_WIRE
  const fadeUrl = `url(#${fadeId(id)})`
  const fadeColor = data?.diag ? 'var(--nb-wire-invalid)' : 'var(--nb-wire)'

  let cls = 'nb-edge'
  if (selected) cls += ' nb-edge--selected'
  if (hot) cls += ' nb-edge--hot'
  if (data?.diag) cls += ' nb-edge--diag'
  if (data?.flash) cls += ' nb-edge--flash'
  if (fade) cls += ' nb-edge--fade'

  let labelCls = 'nb-edge__label'
  if (placeholder) labelCls += ' nb-edge__label--placeholder'
  if (restHidden) labelCls += ' nb-edge__label--rest-hidden'

  const enter = (e: React.MouseEvent) => {
    pointRef.current = { x: e.clientX, y: e.clientY }
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setPop({ ...pointRef.current }), STREAM_POPOVER_DELAY_MS)
  }
  const move = (e: React.MouseEvent) => {
    pointRef.current = { x: e.clientX, y: e.clientY }
    if (!pop || frameRef.current !== null) return
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null
      const card = cardRef.current
      if (!card) return
      const { left, top } = streamPopoverPosition(pointRef.current)
      card.style.left = `${left}px`
      card.style.top = `${top}px`
    })
  }
  const leave = () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = null
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    frameRef.current = null
    setPop(null)
  }

  const carries = data && data.reads.length > 0 ? `, carries ${data.reads.join(' ')}` : ''
  const ariaLabel = data
    ? `wire from ${data.fromName} out to ${data.toName} ${data.portLabel}${carries}`
    : 'wire'

  return (
    <g
      className={cls}
      data-testid={`nb-edge-${id}`}
      aria-label={ariaLabel}
      onMouseEnter={enter}
      onMouseMove={move}
      onMouseLeave={leave}
      style={fade ? ({ '--nb-fade-stroke': fadeUrl } as CSSProperties) : undefined}
    >
      {fade && (
        <defs>
          <linearGradient
            id={fadeId(id)}
            gradientUnits="userSpaceOnUse"
            x1={sourceX}
            y1={sourceY}
            x2={targetX}
            y2={targetY}
          >
            <stop offset="0" style={{ stopColor: fadeColor }} />
            <stop offset="0.3" style={{ stopColor: fadeColor, stopOpacity: 0.35 }} />
            <stop offset="0.7" style={{ stopColor: fadeColor, stopOpacity: 0.35 }} />
            <stop offset="1" style={{ stopColor: fadeColor }} />
          </linearGradient>
        </defs>
      )}
      <path className="nb-edge__path" d={path} stroke={fade ? fadeUrl : undefined} />
      <path
        className="react-flow__edge-interaction"
        d={path}
        fill="none"
        strokeOpacity={0}
        strokeWidth={20}
      />
      {text && (
        <text
          className={labelCls}
          x={at.x + (data?.dx ?? 0)}
          y={at.y}
          data-testid={`nb-edge-label-${id}`}
        >
          {placeholder && <title>{PLACEHOLDER_TIP}</title>}
          {data?.diag && <title>{data.diag}</title>}
          {text}
        </text>
      )}
      {pop && data && (
        <StreamPopover
          from={data.from}
          fromName={data.fromName}
          toName={data.toName}
          reads={data.reads}
          point={pop}
          cardRef={cardRef}
        />
      )}
    </g>
  )
}
