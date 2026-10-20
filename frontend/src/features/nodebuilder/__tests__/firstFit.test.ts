/**
 * The first view of a loaded graph (F453 item 4).
 *
 * - The fit waits until React Flow has measured every node. Before, React
 *   Flow's own queued fit ran after the first measure and framed only the
 *   Ticker.
 * - An automatic fit is never saved as the user's view; a pan or zoom by
 *   the user is, marked `user: true` in localStorage.
 *
 * jsdom never measures nodes, so `useNodesInitialized` is driven by the
 * test, and `fitView` is a stand-in that moves the view somewhere known.
 */

import { describe, it, expect, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { createElement } from 'react'
import type { Graph } from '../../../api/nodebuilder'

const ctl = vi.hoisted(() => ({
  init: false,
  listeners: new Set<() => void>(),
  fitCalls: 0,
}))

vi.mock('@xyflow/react', async importOriginal => {
  const mod = await importOriginal<typeof import('@xyflow/react')>()
  const react = await import('react')
  function useNodesInitialized() {
    return react.useSyncExternalStore(
      cb => { ctl.listeners.add(cb); return () => { ctl.listeners.delete(cb) } },
      () => ctl.init,
    )
  }
  function useReactFlow() {
    const r = mod.useReactFlow()
    return react.useMemo(() => ({
      ...r,
      fitView: async () => {
        ctl.fitCalls += 1
        await r.setViewport({ x: 5, y: 6, zoom: 0.9 })
        return true
      },
    }), [r])
  }
  return { ...mod, useNodesInitialized, useReactFlow }
})

import Canvas from '../Canvas'
import { getActiveCanvas, setActiveCanvas } from '../commands'
import { useNodeBuilderStore } from '../store'
import { viewportStorageKey } from '../store/view'

function setInitialized(v: boolean) {
  act(() => {
    ctl.init = v
    for (const l of ctl.listeners) l()
  })
}

function makeGraph(): Graph {
  const node = (id: string, type: string, y: number) => ({
    id, type, name: id, parent: null, params: {}, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 2,
    stream_schema: 1,
    meta: {},
    nodes: { t: node('t', 'ticker', 0), r: node('r', 'rsi', 150), e: node('e', 'entry', 300) },
    wires: [],
    annotations: { boxes: [], notes: [] },
  } as unknown as Graph
}

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

beforeEach(() => {
  localStorage.clear()
  ctl.init = false
  ctl.fitCalls = 0
})

afterEach(() => {
  cleanup()
  setActiveCanvas(null)
  useNodeBuilderStore.getState().discardEdits()
})

const s = () => useNodeBuilderStore.getState()
const nextFrame = () => act(() => new Promise<void>(r => requestAnimationFrame(() => r())))
// React Flow reports the end of a move from a timer (up to 150 ms later).
const settle = () => act(() => new Promise<void>(r => setTimeout(r, 200)))

function mountSaved(id = 'g_fit') {
  s().openGraph(makeGraph(), { id, rev: 1, name: 'fit' })
  return render(createElement('div', { style: { width: 800, height: 600 } }, createElement(Canvas, { graph: s().graph! })))
}

describe('the first fit (F453)', () => {
  it('waits until every node is measured, then fits once', async () => {
    mountSaved()
    await nextFrame()
    await nextFrame()
    expect(ctl.fitCalls).toBe(0)
    setInitialized(true)
    await nextFrame()
    expect(ctl.fitCalls).toBe(1)
    expect(getActiveCanvas()!.rf.getViewport()).toEqual({ x: 5, y: 6, zoom: 0.9 })
    // A node measured later (added, resized) does not fit again.
    setInitialized(false)
    setInitialized(true)
    await nextFrame()
    expect(ctl.fitCalls).toBe(1)
  })

  it('the automatic fit is not saved as the user view; a user move is', async () => {
    mountSaved()
    await nextFrame()
    setInitialized(true)
    await nextFrame()
    await settle()
    expect(s().rememberedViewport()).toBeNull()
    expect(localStorage.getItem(viewportStorageKey('g_fit'))).toBeNull()

    // A user move (the wheel and the frame keys move the view the same way).
    await act(async () => { await getActiveCanvas()!.rf.setViewport({ x: 40, y: 50, zoom: 1.2 }) })
    await settle()
    expect(s().rememberedViewport()).toEqual({ x: 40, y: 50, zoom: 1.2 })
    expect(JSON.parse(localStorage.getItem(viewportStorageKey('g_fit'))!)).toEqual({ '/': { x: 40, y: 50, zoom: 1.2, user: true } })
  })

  it('after a user move, moving back to the auto-fit view (Reset view) is saved (FL-4)', async () => {
    mountSaved()
    await nextFrame()
    setInitialized(true)
    await nextFrame()
    await settle()
    await act(async () => { await getActiveCanvas()!.rf.setViewport({ x: 40, y: 50, zoom: 1.2 }) })
    await settle()
    // H frames all again: the same view the automatic fit made.
    await act(async () => { await getActiveCanvas()!.rf.setViewport({ x: 5, y: 6, zoom: 0.9 }) })
    await settle()
    expect(s().rememberedViewport()).toEqual({ x: 5, y: 6, zoom: 0.9 })
    expect(JSON.parse(localStorage.getItem(viewportStorageKey('g_fit'))!)).toEqual({ '/': { x: 5, y: 6, zoom: 0.9, user: true } })
  })

  it('a stored user view comes back without a fit', async () => {
    localStorage.setItem(viewportStorageKey('g_fit'), JSON.stringify({ '/': { x: 7, y: 8, zoom: 0.5, user: true } }))
    mountSaved()
    await nextFrame()
    setInitialized(true)
    await nextFrame()
    expect(ctl.fitCalls).toBe(0)
    expect(getActiveCanvas()!.rf.getViewport()).toEqual({ x: 7, y: 8, zoom: 0.5 })
  })

  it('a stored view without the user mark is ignored and the graph is fitted', async () => {
    localStorage.setItem(viewportStorageKey('g_fit'), JSON.stringify({ '/': { x: 228.5, y: 344.5, zoom: 1 } }))
    mountSaved()
    await nextFrame()
    setInitialized(true)
    await nextFrame()
    expect(ctl.fitCalls).toBe(1)
    expect(getActiveCanvas()!.rf.getViewport()).toEqual({ x: 5, y: 6, zoom: 0.9 })
  })
})
