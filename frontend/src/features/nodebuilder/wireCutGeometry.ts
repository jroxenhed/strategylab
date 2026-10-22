/**
 * Y + drag cuts wires (F457, Houdini): the pure part.
 *
 * A wire is the cubic bezier AttrEdge draws (`wirePath`). The cut is a
 * straight segment from the press point to the pointer. A wire is cut when
 * the segment crosses it; the bezier is tested as a polyline of
 * `CUT_SAMPLES` pieces, like the splice hit test in plugins/wireOps.ts.
 *
 * Everything here is in flow units. WireCut.tsx reads each drawn wire's
 * path from the canvas and commits the delete.
 */

import type { XY } from './geometry'

/** Pieces each wire is split into for the crossing test. */
export const CUT_SAMPLES = 24

/** The four points of a cubic bezier. */
export interface Cubic {
  p0: XY
  p1: XY
  p2: XY
  p3: XY
}

/**
 * The cubic in an SVG path `M x,y C x1,y1 x2,y2 x,y` (what `wirePath`
 * makes). Null for any other shape.
 */
export function parseWirePath(d: string | null | undefined): Cubic | null {
  if (!d || !/^\s*M/i.test(d) || !/C/i.test(d)) return null
  const n = (d.match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? []).map(Number)
  if (n.length !== 8 || n.some(v => !Number.isFinite(v))) return null
  return {
    p0: { x: n[0], y: n[1] },
    p1: { x: n[2], y: n[3] },
    p2: { x: n[4], y: n[5] },
    p3: { x: n[6], y: n[7] },
  }
}

/** The point at `t` (0..1) on a cubic. */
export function cubicPoint(c: Cubic, t: number): XY {
  const u = 1 - t
  const a = u * u * u
  const b = 3 * u * u * t
  const m = 3 * u * t * t
  const e = t * t * t
  return {
    x: a * c.p0.x + b * c.p1.x + m * c.p2.x + e * c.p3.x,
    y: a * c.p0.y + b * c.p1.y + m * c.p2.y + e * c.p3.y,
  }
}

/** Which side of the line a→b the point p is on (the sign of the cross product). */
function side(a: XY, b: XY, p: XY): number {
  return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
}

/** True when p, known to be on the line a→b, lies within the segment's box. */
function within(a: XY, b: XY, p: XY): boolean {
  return Math.min(a.x, b.x) <= p.x && p.x <= Math.max(a.x, b.x)
    && Math.min(a.y, b.y) <= p.y && p.y <= Math.max(a.y, b.y)
}

/** True when segment a→b and segment c→d touch or cross. */
export function segmentsCross(a: XY, b: XY, c: XY, d: XY): boolean {
  const d1 = side(c, d, a)
  const d2 = side(c, d, b)
  const d3 = side(a, b, c)
  const d4 = side(a, b, d)
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true
  // An end on the other segment (touching, or lying along it).
  if (d1 === 0 && within(c, d, a)) return true
  if (d2 === 0 && within(c, d, b)) return true
  if (d3 === 0 && within(a, b, c)) return true
  if (d4 === 0 && within(a, b, d)) return true
  return false
}

/** True when the cut a→b crosses the wire. A cut of zero length crosses nothing. */
export function cutCrossesWire(wire: Cubic, a: XY, b: XY, samples: number = CUT_SAMPLES): boolean {
  if (a.x === b.x && a.y === b.y) return false
  let prev = wire.p0
  for (let i = 1; i <= samples; i++) {
    const next = cubicPoint(wire, i / samples)
    if (segmentsCross(a, b, prev, next)) return true
    prev = next
  }
  return false
}

/** A drawn wire: its id and the `d` of its path. */
export interface DrawnWire {
  id: string
  d: string | null | undefined
}

/** The ids of the wires the cut a→b crosses, in the order given. Unreadable paths are skipped. */
export function wiresCutBy(wires: readonly DrawnWire[], a: XY, b: XY): string[] {
  const out: string[] = []
  for (const w of wires) {
    const cubic = parseWirePath(w.d)
    if (cubic && cutCrossesWire(cubic, a, b)) out.push(w.id)
  }
  return out
}

/** The key that arms the cut: Y with no Cmd, Ctrl or Alt (Shift is fine, as caps lock). */
export function isCutKey(e: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey'>): boolean {
  return (e.key === 'y' || e.key === 'Y') && !e.metaKey && !e.ctrlKey && !e.altKey
}
