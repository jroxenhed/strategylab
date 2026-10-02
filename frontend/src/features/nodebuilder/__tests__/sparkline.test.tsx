/**
 * Sparklines on nodes (F435 W4 item 4.C, spec S26).
 *
 * The drawing functions are tested on a recording 2D context. The slot and
 * the layer are tested in a small React Flow whose nodes are real BaseNode
 * cards, with a React Profiler around each card to count its renders.
 */

import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
import { render, cleanup, act, waitFor } from '@testing-library/react'
import { Profiler } from 'react'
import { ReactFlow, ReactFlowProvider, type Node as RFNode, type NodeProps } from '@xyflow/react'
import type { PreviewNode } from '../../../api/nodebuilderInspect'
import { BaseNode } from '../nodes/BaseNode'
import {
  drawBool,
  drawLine,
  formatLastValue,
  sparklineSummary,
  sparklineTooltip,
  SparklineLayer,
  isSlotCovered,
  useNodePreview,
  type SparkCtx,
} from '../nodes/Sparkline'
import { useNodeBuilderStore } from '../store'
import { EMPTY_PREVIEW, mergePreviewNodes } from '../store/status'
import { resetDiagnostics } from '../useDiagnostics'

beforeAll(() => {
  // React Flow reads this in jsdom; a no-op stand-in is enough here.
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

// ---------------------------------------------------------------------------
// A recording 2D context
// ---------------------------------------------------------------------------

interface Rec extends SparkCtx {
  calls: { fn: string; args: unknown[] }[]
  strokeStyles: string[]
  count(fn: string): number
}

function recCtx(): Rec {
  const calls: { fn: string; args: unknown[] }[] = []
  const strokeStyles: string[] = []
  const fn = (name: string) => (...args: unknown[]) => { calls.push({ fn: name, args }) }
  const ctx = {
    calls,
    strokeStyles,
    count: (name: string) => calls.filter(c => c.fn === name).length,
    beginPath: fn('beginPath'),
    moveTo: fn('moveTo'),
    lineTo: fn('lineTo'),
    stroke: fn('stroke'),
    fillRect: fn('fillRect'),
    fillText: fn('fillText'),
    setLineDash: fn('setLineDash'),
    setTransform: fn('setTransform'),
    clearRect: fn('clearRect'),
    fillStyle: '',
    lineWidth: 1,
    lineJoin: 'miter',
    globalAlpha: 1,
    font: '',
    textAlign: 'start',
    textBaseline: 'alphabetic',
  } as unknown as Rec
  let stroke = ''
  Object.defineProperty(ctx, 'strokeStyle', {
    get: () => stroke,
    set: (v: string) => { stroke = v; strokeStyles.push(v) },
  })
  return ctx
}

const BOX = { x: 0, y: 0, width: 139, height: 28, color: '#34d399', textColor: '#98a1b3' }

describe('drawLine', () => {
  it('draws 96 finite values with one moveTo and 95 lineTo', () => {
    const ctx = recCtx()
    const values = Array.from({ length: 96 }, (_, i) => 10 + i)
    drawLine(ctx, values, BOX)
    expect(ctx.count('moveTo')).toBe(1)
    expect(ctx.count('lineTo')).toBe(95)
  })

  it('breaks the path at a null (no interpolation)', () => {
    const ctx = recCtx()
    const values: (number | null)[] = Array.from({ length: 96 }, (_, i) => 10 + i)
    values[50] = null
    drawLine(ctx, values, BOX)
    expect(ctx.count('moveTo')).toBe(2)
    expect(ctx.count('lineTo')).toBe(93)
  })

  it('prints the last value with 2 decimals', () => {
    const ctx = recCtx()
    drawLine(ctx, [1, 2, 41.2345], BOX)
    const text = ctx.calls.filter(c => c.fn === 'fillText').map(c => c.args[0])
    expect(text).toEqual(['41.23'])
    expect(formatLastValue(41.2345)).toBe('41.23')
    expect(formatLastValue(12345.6)).toBe('12 346')
  })

  it('draws a dotted zero line when the range crosses zero', () => {
    const ctx = recCtx()
    drawLine(ctx, [-1, 0.5, 1], { ...BOX, min: -1, max: 1, midColor: '#2f3848' })
    expect(ctx.calls.some(c => c.fn === 'setLineDash' && JSON.stringify(c.args[0]) === '[1,2]')).toBe(true)
    expect(ctx.strokeStyles[0]).toBe('#2f3848')
  })

  it('strokes in the color it is given (the layer passes the dim token when stale)', () => {
    const ctx = recCtx()
    drawLine(ctx, [1, 2, 3], { ...BOX, color: '#7a8296' })
    expect(ctx.strokeStyles.at(-1)).toBe('#7a8296')
  })
})

describe('drawBool', () => {
  it('draws one bar per non-zero bucket and prints true_pct', () => {
    const ctx = recCtx()
    const values = Array.from({ length: 96 }, (_, i) => (i % 8 === 0 ? 0.5 : 0))
    drawBool(ctx, values, { ...BOX, truePct: 2.5 })
    expect(ctx.count('fillRect')).toBe(12)
    const text = ctx.calls.filter(c => c.fn === 'fillText').map(c => c.args[0])
    expect(text).toEqual(['2.5 % true'])
  })

  it('scales alpha by the share, at least 0.35', () => {
    const ctx = recCtx()
    const alphas: number[] = []
    const fillRect = ctx.fillRect
    ctx.fillRect = ((...a: Parameters<SparkCtx['fillRect']>) => { alphas.push(ctx.globalAlpha); fillRect(...a) }) as SparkCtx['fillRect']
    drawBool(ctx, [0.1, 1], { ...BOX, truePct: 50 })
    expect(alphas[0]).toBeCloseTo(0.85 * 0.35)
    expect(alphas[1]).toBeCloseTo(0.85)
  })
})

describe('text', () => {
  const line: PreviewNode = { attr: '@rsi', kind: 'line', min: 12.1, max: 88.4, nan_count: 14, values: [null, 30, 41.2] }
  const bool: PreviewNode = { attr: '@xb_rsi', kind: 'bool', true_pct: 2.5, values: [0, 1] }
  it('tooltip and screen-reader summary', () => {
    expect(sparklineTooltip(line)).toBe('@rsi · min 12.10 · max 88.40 · 14 nan')
    expect(sparklineTooltip(bool)).toBe('@xb_rsi · 2.5 % true')
    expect(sparklineSummary(line)).toBe('sparkline: @rsi from 12.10 to 88.40, last 41.20')
    expect(sparklineSummary(bool)).toBe('sparkline: @xb_rsi 2.5 % true')
  })
})

describe('mergePreviewNodes', () => {
  it('keeps the old object for an unchanged node, and the old map when nothing changed', () => {
    const a: PreviewNode = { attr: '@a', kind: 'line', min: 0, max: 1, values: [0, 1] }
    const b: PreviewNode = { attr: '@b', kind: 'bool', true_pct: 5, values: [0, 1] }
    const prev = { a, b }
    const same = mergePreviewNodes(prev, { a: { ...a, values: [0, 1] }, b: { ...b, values: [0, 1] } })
    expect(same).toBe(prev)
    const next = mergePreviewNodes(prev, { a: { ...a, values: [1, 0] }, b: { ...b, values: [0, 1] } })
    expect(next).not.toBe(prev)
    expect(next.b).toBe(b)
    expect(next.a).not.toBe(a)
  })
})

// ---------------------------------------------------------------------------
// The slot and the layer in a flow of BaseNode cards
// ---------------------------------------------------------------------------

const renders: Record<string, number> = {}

function CardNode({ id }: NodeProps) {
  return (
    <Profiler id={id} onRender={() => { renders[id] = (renders[id] ?? 0) + 1 }}>
      <BaseNode cat="indicator" title={id} nodeId={id} hasInput={false} />
    </Profiler>
  )
}

const nodeTypes = { card: CardNode }
const RF_NODES: RFNode[] = ['a', 'b', 'c', 'd'].map((id, i) => ({
  id, type: 'card', position: { x: 0, y: i * 120 }, data: { backendType: 'sma', params: {} },
}))

function Flow() {
  return (
    <div className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      <ReactFlowProvider>
        <ReactFlow nodes={RF_NODES} edges={[]} nodeTypes={nodeTypes}>
          <SparklineLayer />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  )
}

function lineNode(attr: string, last: number): PreviewNode {
  return { attr, kind: 'line', min: 0, max: 100, nan_count: 0, values: [10, 50, last] }
}

const st = () => useNodeBuilderStore.getState()

beforeEach(() => {
  for (const k of Object.keys(renders)) delete renders[k]
  useNodeBuilderStore.setState({ preview: EMPTY_PREVIEW })
  resetDiagnostics()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useNodeBuilderStore.setState({ preview: EMPTY_PREVIEW })
})

describe('sparkline slots on nodes', () => {
  it('one layer canvas; a 28px slot only on nodes with a preview', () => {
    act(() => {
      st().setPreview('ck_1', {
        a: lineNode('@a', 40),
        b: { attr: '@xb', kind: 'bool', true_pct: 2.5, values: [0, 0.5, 1] },
        c: lineNode('@c', 60),
      })
    })
    const { container } = render(<Flow />)
    expect(container.querySelectorAll('[data-testid="nb-sparkline-layer"]')).toHaveLength(1)
    for (const id of ['a', 'b', 'c']) {
      const slot = container.querySelector(`[data-testid="nb-sparkline-slot-${id}"]`) as HTMLElement
      expect(slot).not.toBeNull()
      expect(slot.style.height).toBe('28px')
    }
    expect(container.querySelector('[data-testid="nb-sparkline-slot-d"]')).toBeNull()
    // The bool node's screen-reader summary carries true_pct.
    expect(container.querySelector('[data-testid="nb-sparkline-slot-b"]')?.textContent).toBe('sparkline: @xb 2.5 % true')
  })

  it('a node re-renders only when its own preview changes', () => {
    act(() => { st().setPreview('ck_1', { a: lineNode('@a', 40), b: lineNode('@b', 40) }) })
    render(<Flow />)
    const before = { ...renders }
    // A new answer where only `a` changed (b is equal, but a new object).
    act(() => { st().setPreview('ck_2', { a: lineNode('@a', 41), b: lineNode('@b', 40) }) })
    expect(renders.a).toBeGreaterThan(before.a)
    expect(renders.b).toBe(before.b)
    expect(renders.c).toBe(before.c)
    expect(renders.d).toBe(before.d)
    // Marking the data stale re-renders no node (the layer draws it dim).
    const mid = { ...renders }
    act(() => { st().setPreviewStale(true) })
    expect(renders).toEqual(mid)
  })

  it('the layer strokes in the dim token when the preview is stale', async () => {
    const ctx = recCtx()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const slot = (this as HTMLElement).dataset?.testid?.startsWith('nb-sparkline-slot-')
      return (slot
        ? { left: 10, top: 20, right: 149, bottom: 48, width: 139, height: 28, x: 10, y: 20 }
        : { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }) as DOMRect
    })
    act(() => { st().setPreview('ck_1', { a: lineNode('@a', 40) }) })
    render(<Flow />)
    await waitFor(() => expect(ctx.count('lineTo')).toBeGreaterThan(0))
    expect(ctx.strokeStyles.at(-1)).not.toBe('#7a8296')
    ctx.strokeStyles.length = 0
    act(() => { st().setPreviewStale(true) })
    await waitFor(() => expect(ctx.strokeStyles.length).toBeGreaterThan(0))
    expect(ctx.strokeStyles.at(-1)).toBe('#7a8296')
  })
})

// ---------------------------------------------------------------------------
// W4 review fixes: visible-rect canvas (DV-3), covered slots (DV-14)
// ---------------------------------------------------------------------------

type R = { left: number; top: number; width: number; height: number }
function rect(r: R): DOMRect {
  return { ...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top, toJSON: () => r } as DOMRect
}
const ZERO = rect({ left: 0, top: 0, width: 0, height: 0 })

function mockLayout(slotRects: Record<string, R>, nodeRects: Record<string, R> = {}) {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const el = this as HTMLElement
    const tid = el.dataset?.testid ?? ''
    if (tid.startsWith('nb-sparkline-slot-')) {
      const r = slotRects[tid.slice('nb-sparkline-slot-'.length)]
      return r ? rect(r) : ZERO
    }
    if (el.classList?.contains('react-flow__node')) {
      const r = nodeRects[el.getAttribute('data-id') ?? '']
      return r ? rect(r) : ZERO
    }
    if (el.classList?.contains('react-flow')) return rect({ left: 0, top: 0, width: 800, height: 600 })
    return ZERO
  })
}

describe('sparkline layer canvas', () => {
  it('covers only the slots on screen and reallocates only on a size change (DV-3)', async () => {
    const ctx = recCtx()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx as unknown as CanvasRenderingContext2D)
    mockLayout({ a: { left: 10, top: 20, width: 139, height: 28 }, b: { left: 5000, top: 4000, width: 139, height: 28 } })
    act(() => { st().setPreview('ck_1', { a: lineNode('@a', 40), b: lineNode('@b', 50) }) })
    const { container } = render(<Flow />)
    const canvas = container.querySelector('[data-testid="nb-sparkline-layer"]') as HTMLCanvasElement
    await waitFor(() => expect(ctx.count('lineTo')).toBeGreaterThan(0))
    // Only slot a: the off-screen slot b does not stretch the canvas.
    expect(canvas.style.left).toBe('10px')
    expect(canvas.style.width).toBe('139px')
    const widthSet = vi.spyOn(HTMLCanvasElement.prototype, 'width', 'set')
    const drawsBefore = ctx.count('clearRect')
    act(() => { st().setPreviewStale(true) })
    await waitFor(() => expect(ctx.count('clearRect')).toBeGreaterThan(drawsBefore))
    expect(widthSet).not.toHaveBeenCalled()
  })

  it('skips a slot covered by a card painted above it (DV-14)', async () => {
    const ctx = recCtx()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx as unknown as CanvasRenderingContext2D)
    // Card b (later in the DOM, same z) sits over card a's slot.
    mockLayout(
      { a: { left: 10, top: 20, width: 139, height: 28 }, b: { left: 300, top: 200, width: 139, height: 28 } },
      { a: { left: 0, top: 0, width: 160, height: 60 }, b: { left: 0, top: 0, width: 460, height: 240 } },
    )
    act(() => { st().setPreview('ck_1', { a: lineNode('@a', 40), b: lineNode('@b', 50) }) })
    const { container } = render(<Flow />)
    const canvas = container.querySelector('[data-testid="nb-sparkline-layer"]') as HTMLCanvasElement
    await waitFor(() => expect(ctx.count('lineTo')).toBeGreaterThan(0))
    expect(canvas.style.left).toBe('300px')
    expect(canvas.style.width).toBe('139px')
  })

  it('isSlotCovered: higher z or later in the DOM, and overlapping', () => {
    const slot = { left: 10, top: 10, right: 100, bottom: 38 }
    const el = (n: number) => ({ id: n }) as unknown as Element
    const over = { left: 0, top: 0, right: 50, bottom: 50 }
    const away = { left: 500, top: 500, right: 600, bottom: 600 }
    expect(isSlotCovered(slot, 0, [{ el: el(0), rect: over, z: 0 }, { el: el(1), rect: over, z: 0 }])).toBe(true)
    expect(isSlotCovered(slot, 1, [{ el: el(0), rect: over, z: 0 }, { el: el(1), rect: over, z: 0 }])).toBe(false)
    expect(isSlotCovered(slot, 1, [{ el: el(0), rect: over, z: 5 }, { el: el(1), rect: over, z: 0 }])).toBe(true)
    expect(isSlotCovered(slot, 0, [{ el: el(0), rect: over, z: 0 }, { el: el(1), rect: away, z: 9 }])).toBe(false)
  })
})

describe('useNodePreview', () => {
  it('returns the node entry or undefined', () => {
    act(() => { st().setPreview('ck_1', { a: lineNode('@a', 40) }) })
    function Probe({ id }: { id: string }) { return <span data-testid={`probe-${id}`}>{useNodePreview(id)?.attr ?? 'none'}</span> }
    const { getByTestId } = render(<><Probe id="a" /><Probe id="z" /></>)
    expect(getByTestId('probe-a').textContent).toBe('@a')
    expect(getByTestId('probe-z').textContent).toBe('none')
  })
})
