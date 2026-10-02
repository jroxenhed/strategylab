/**
 * Wires: label, states and the stream popover (F435 W2 item 2.E, spec S11).
 *
 * AttrEdge is drawn on its own inside an <svg> (React Flow only draws edges
 * once nodes are measured, which jsdom cannot do). Canvas.tsx builds the
 * `data` it gets; the pure parts of that are in streamLabels.test.ts.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { Position } from '@xyflow/react'
import { emptyGraph, type AttrInfo, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics, setStreams } from '../useDiagnostics'
import AttrEdge, { STREAM_POPOVER_DELAY_MS, type AttrEdgeData } from '../edges/AttrEdge'
import { groupByWriter } from '../edges/StreamPopover'

function node(id: string, type: string): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [0, 0], display: false, bypass: false }
}
function attr(name: string, written_by: string, dtype: AttrInfo['dtype'] = 'float'): AttrInfo {
  return { name, dtype, written_by }
}

function data(over: Partial<AttrEdgeData> = {}): AttrEdgeData {
  return {
    from: 'rsi', to: 'xb', text: '@rsi', placeholder: false, reads: ['@rsi'],
    t: 0.5, dx: 0, hidden: null, lowZoom: false, hot: false, diag: null,
    fromName: 'rsi', toName: 'xb', portLabel: 'a', ...over,
  }
}

function drawEdge(d: AttrEdgeData, selected = false) {
  const props = {
    id: 'w1', source: 'rsi', target: 'xb', sourceX: 0, sourceY: 0, targetX: 0, targetY: 200,
    sourcePosition: Position.Bottom, targetPosition: Position.Top, selected, data: d,
  } as unknown as Parameters<typeof AttrEdge>[0]
  return render(<svg><AttrEdge {...props} /></svg>)
}

beforeEach(() => {
  resetDiagnostics()
  const g: Graph = { ...emptyGraph(), nodes: { aapl: node('aapl', 'ticker'), rsi: node('rsi', 'rsi'), xb: node('xb', 'crosses_below') } }
  act(() => { useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' }) })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('AttrEdge', () => {
  it('draws the label as SVG text at the middle, with an aria label', () => {
    drawEdge(data())
    const label = screen.getByTestId('nb-edge-label-w1')
    expect(label.tagName.toLowerCase()).toBe('text')
    expect(label.textContent).toBe('@rsi')
    expect(label.getAttribute('y')).toBe('100')
    expect(screen.getByTestId('nb-edge-w1').getAttribute('aria-label')).toBe('wire from rsi out to xb a, carries @rsi')
    // No arrowheads, no label boxes.
    expect(document.querySelector('marker, rect, foreignObject')).toBeNull()
  })

  it('the placeholder is dim with a hint', () => {
    drawEdge(data({ text: 'stream', placeholder: true, reads: [] }))
    const label = screen.getByTestId('nb-edge-label-w1')
    expect(label.getAttribute('class')).toContain('nb-edge__label--placeholder')
    expect(label.querySelector('title')?.textContent).toContain('does not read anything yet')
  })

  it('hides a duplicate or zoomed-out label at rest, but not on the selected wire', () => {
    drawEdge(data({ hidden: 'dup' }))
    expect(screen.getByTestId('nb-edge-label-w1').getAttribute('class')).toContain('rest-hidden')
    cleanup()
    drawEdge(data({ lowZoom: true }), true)
    expect(screen.getByTestId('nb-edge-label-w1').getAttribute('class')).not.toContain('rest-hidden')
    expect(screen.getByTestId('nb-edge-w1').getAttribute('class')).toContain('nb-edge--selected')
  })

  it('a diagnostic on the wire draws the diagnostic stroke', () => {
    drawEdge(data({ diag: '@rsi is not present on the input' }))
    expect(screen.getByTestId('nb-edge-w1').getAttribute('class')).toContain('nb-edge--diag')
  })

  it('hovering 300 ms shows the stream, grouped by writer, with the reads ringed', () => {
    vi.useFakeTimers()
    act(() => setStreams({
      rsi: {
        stream_schema: 1,
        points: [attr('@open', 'aapl'), attr('@close', 'aapl'), attr('@rsi', 'rsi')],
        detail: [attr('@stop_pct', 'sl')],
        prims: [],
      },
    }))
    drawEdge(data())
    const label = screen.getByTestId('nb-edge-label-w1')
    act(() => { fireEvent.mouseEnter(label, { clientX: 10, clientY: 10 }) })
    expect(screen.queryByTestId('nb-stream-popover')).toBeNull()
    act(() => { vi.advanceTimersByTime(STREAM_POPOVER_DELAY_MS) })
    const pop = screen.getByTestId('nb-stream-popover')
    expect(pop.textContent).toContain('stream · 4 attrs')
    expect(pop.textContent).toContain('wire rsi → xb')
    const ringed = [...pop.querySelectorAll('.nb-chip--read-here')].map(e => e.textContent)
    expect(ringed).toEqual(['@rsi'])
    act(() => { fireEvent.mouseLeave(screen.getByTestId('nb-edge-w1')) })
    expect(screen.queryByTestId('nb-stream-popover')).toBeNull()
  })

  it('the open popover follows the pointer by a style write once per frame, not by state (FP-12)', () => {
    vi.useFakeTimers()
    const frames: FrameRequestCallback[] = []
    const raf = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation(cb => { frames.push(cb); return frames.length })
    try {
      drawEdge(data())
      const g = screen.getByTestId('nb-edge-w1')
      act(() => { fireEvent.mouseEnter(g, { clientX: 10, clientY: 10 }) })
      act(() => { vi.advanceTimersByTime(STREAM_POPOVER_DELAY_MS) })
      const pop = screen.getByTestId('nb-stream-popover')
      expect([pop.style.left, pop.style.top]).toEqual(['22px', '22px'])
      // Two moves in one frame: one frame request, nothing moves until it runs.
      fireEvent.mouseMove(g, { clientX: 100, clientY: 50 })
      fireEvent.mouseMove(g, { clientX: 120, clientY: 60 })
      expect(raf).toHaveBeenCalledTimes(1)
      expect(screen.getByTestId('nb-stream-popover')).toBe(pop)
      expect(pop.style.left).toBe('22px')
      frames[0](0)
      expect([pop.style.left, pop.style.top]).toEqual(['132px', '72px'])
    } finally {
      raf.mockRestore()
    }
  })
})

describe('groupByWriter', () => {
  it('keeps stream order', () => {
    const groups = groupByWriter([attr('@a', 'p'), attr('@b', 'q'), attr('@c', 'p')])
    expect(groups.map(g => [g.writer, g.attrs.map(a => a.name)])).toEqual([['p', ['@a', '@c']], ['q', ['@b']]])
  })
})
