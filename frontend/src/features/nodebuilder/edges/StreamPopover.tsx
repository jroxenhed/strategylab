/**
 * StreamPopover — the hover card of a wire (spec S11).
 *
 * Shows everything that flows on the wire: the output stream of the wire's
 * source node from the last `/validate` answer, grouped by the node that
 * wrote each attribute, in stream order. Chips take the writer's category
 * colors; the attributes the consumer reads through this wire get a ring.
 * Detail attributes (one value per cook, like @stop_pct) come last.
 *
 * Portaled to the page body at the pointer (12px offset), clamped inside
 * the window. It takes no pointer events, so it never steals the hover.
 */

import { createPortal } from 'react-dom'
import type { AttrInfo } from '../../../api/nodebuilder'
import { CATS, type CatKey } from '../categories'
import { useNodeBuilderStore } from '../store'
import { useNodeStream } from '../useDiagnostics'
import { catalogEntry } from '../streamLabels'
import '../stream.css'

/** At most this many writer groups; the rest is a count. */
export const MAX_WRITER_GROUPS = 8

const WIDTH = 280
const OFFSET = 12

export interface WriterGroup {
  writer: string
  attrs: AttrInfo[]
}

/** Attributes grouped by writer, in stream order (pure, tested). */
export function groupByWriter(attrs: readonly AttrInfo[]): WriterGroup[] {
  const out: WriterGroup[] = []
  const index = new Map<string, WriterGroup>()
  for (const a of attrs) {
    const writer = a.written_by ?? ''
    let g = index.get(writer)
    if (!g) {
      g = { writer, attrs: [] }
      index.set(writer, g)
      out.push(g)
    }
    g.attrs.push(a)
  }
  return out
}

export interface StreamPopoverProps {
  /** The wire's source node: its output stream is what flows. */
  from: string
  fromName: string
  toName: string
  /** What the consumer reads through the wire (ringed). */
  reads: readonly string[]
  /** Pointer position in screen pixels (where it opens). */
  point: { x: number; y: number }
  /** The card element, so the wire can move it with the pointer without a re-render. */
  cardRef?: React.Ref<HTMLDivElement>
}

/** Where the card goes for a pointer position: 12px off, flipped left / up near the window edges. */
export function streamPopoverPosition(point: { x: number; y: number }): { left: number; top: number } {
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1200
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800
  const left = point.x + OFFSET + WIDTH > vw ? Math.max(8, point.x - OFFSET - WIDTH) : point.x + OFFSET
  const top = Math.min(point.y + OFFSET, Math.max(8, vh - 200))
  return { left, top }
}

export function StreamPopover({ from, fromName, toName, reads, point, cardRef }: StreamPopoverProps) {
  const stream = useNodeStream(from)
  const nodes = useNodeBuilderStore(s => s.graph?.nodes)

  const writerName = (id: string) => nodes?.[id]?.name ?? (id || 'unknown')
  const catVars = (id: string) => {
    const cat = (catalogEntry(nodes?.[id]?.type)?.cat ?? 'settings') as CatKey
    return {
      '--cat': (CATS[cat] ?? CATS.settings).color,
      '--tint': `var(--nb-tint-${cat})`,
    } as React.CSSProperties
  }

  const points = stream?.points ?? []
  const detail = stream?.detail ?? []
  const count = points.length + detail.length
  const groups = groupByWriter(points)
  const shown = groups.slice(0, MAX_WRITER_GROUPS)
  const moreWriters = groups.length - shown.length

  // Keep the card on screen: flip to the left / above near the edges.
  const { left, top } = streamPopoverPosition(point)

  const chip = (a: AttrInfo) => (
    <span
      key={`${a.name}-${a.written_by ?? ''}`}
      className={`nb-chip${reads.includes(a.name) ? ' nb-chip--read-here' : ''}`}
      style={catVars(a.written_by ?? '')}
    >
      {a.name}
    </span>
  )

  const card = (
    <div
      ref={cardRef}
      className="nodebuilder-root nb-stream-pop"
      role="tooltip"
      data-testid="nb-stream-popover"
      style={{ left, top }}
    >
      <div className="nb-stream-pop__title">
        <span>{stream ? `stream · ${count} attrs` : 'stream'}</span>
        <span className="nb-stream-pop__wire">wire {fromName} → {toName}</span>
      </div>
      {!stream && (
        <div className="nb-stream-pop__writer">Not checked yet. It shows after the next check.</div>
      )}
      {shown.map(g => (
        <div key={g.writer || '_'}>
          <div className="nb-stream-pop__writer">{writerName(g.writer)}</div>
          <div className="nb-stream-pop__chips">{g.attrs.map(chip)}</div>
        </div>
      ))}
      {moreWriters > 0 && <div className="nb-stream-pop__writer">+{moreWriters} more writers</div>}
      {detail.length > 0 && (
        <div>
          <div className="nb-stream-pop__writer">detail</div>
          <div className="nb-stream-pop__chips">{detail.map(chip)}</div>
        </div>
      )}
      <div className="nb-stream-pop__footer">click to select</div>
    </div>
  )

  return typeof document !== 'undefined' ? createPortal(card, document.body) : null
}

export default StreamPopover
