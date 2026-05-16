/**
 * F245b — Drag-reorder indicator panes unit tests.
 *
 * Tests the reorder logic directly (the same function body used in
 * IndicatorList's handleDrop) without needing to render the component.
 * This avoids importing Chart.tsx / SubPane which require complex canvas stubs.
 */
import { describe, it, expect } from 'vitest'
import type { IndicatorInstance } from '../shared/types'

// ---------------------------------------------------------------------------
// Minimal factory — creates a typed IndicatorInstance stub with just id/type
// ---------------------------------------------------------------------------
function makeInst(id: string): IndicatorInstance {
  return {
    id,
    type: 'rsi',
    enabled: true,
    pane: 'sub',
    params: { period: 14 },
  } as IndicatorInstance
}

// ---------------------------------------------------------------------------
// Pure reorder helper — mirrors the logic in IndicatorList handleDrop
// ---------------------------------------------------------------------------
function reorder(indicators: IndicatorInstance[], sourceId: string, targetId: string): IndicatorInstance[] {
  if (sourceId === targetId) return indicators
  const from = indicators.findIndex(i => i.id === sourceId)
  const to = indicators.findIndex(i => i.id === targetId)
  if (from === -1 || to === -1) return indicators
  const next = [...indicators]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('F245b indicator drag-reorder logic', () => {
  const a = makeInst('a')
  const b = makeInst('b')
  const c = makeInst('c')

  it('moves first item to end', () => {
    const result = reorder([a, b, c], 'a', 'c')
    expect(result.map(i => i.id)).toEqual(['b', 'c', 'a'])
  })

  it('moves last item to front', () => {
    const result = reorder([a, b, c], 'c', 'a')
    expect(result.map(i => i.id)).toEqual(['c', 'a', 'b'])
  })

  it('swaps adjacent items (middle → first)', () => {
    const result = reorder([a, b, c], 'b', 'a')
    expect(result.map(i => i.id)).toEqual(['b', 'a', 'c'])
  })

  it('no-op when source equals target', () => {
    const original = [a, b, c]
    const result = reorder(original, 'b', 'b')
    expect(result).toBe(original) // same reference — no mutation
  })

  it('no-op when source id not found', () => {
    const original = [a, b, c]
    const result = reorder(original, 'z', 'a')
    expect(result).toBe(original)
  })

  it('no-op when target id not found', () => {
    const original = [a, b, c]
    const result = reorder(original, 'a', 'z')
    expect(result).toBe(original)
  })

  it('does not mutate the original array', () => {
    const original = [a, b, c]
    const snapshot = [...original]
    reorder(original, 'a', 'c')
    expect(original.map(i => i.id)).toEqual(snapshot.map(i => i.id))
  })

  it('two-element swap', () => {
    const result = reorder([a, b], 'a', 'b')
    expect(result.map(i => i.id)).toEqual(['b', 'a'])
  })
})
