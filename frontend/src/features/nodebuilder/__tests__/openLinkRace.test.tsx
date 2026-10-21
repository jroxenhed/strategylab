/**
 * A bot-card or Asset Manager open loads exactly one graph (F453 item 2).
 *
 * Showing the graph view for the first time makes NodeBuilder call
 * `openLastGraph`. Before the fix, a link open showed the view and then
 * loaded its graph, and the "reopen last graph" load started right after
 * won (it bumps the load token), so the wrong graph opened.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { useLayoutEffect } from 'react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

vi.mock('../../../api/graphs', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/graphs')>()
  return {
    ...orig,
    listGraphs: vi.fn(),
    getGraph: vi.fn(),
    createGraph: vi.fn(),
    saveGraph: vi.fn(),
    deleteGraph: vi.fn(),
    seedLegacyGraphs: vi.fn(),
  }
})

import { getGraph, type GraphEnvelope } from '../../../api/graphs'
import { useNodeBuilderStore } from '../store'
import { useGraphSession, type GraphSession } from '../useGraphSession'
import { clearNotices } from '../notices'
import { setLastGraphId } from '../persistence'
import { requestOpenGraph, SHOW_GRAPH_VIEW_EVENT } from '../graphLinks'

function node(id: string): GraphNode {
  return { id, type: 'rsi', name: id, parent: null, params: { period: 14 }, position: [0, 0], display: false, bypass: false }
}

function graphWith(id: string): Graph {
  return { ...emptyGraph(), nodes: { [id]: node(id) } }
}

function envelope(id: string): GraphEnvelope {
  return { id, rev: 1, name: id, description: '', created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z', graph: graphWith(`n_${id}`) }
}

const holder: { current: GraphSession | null } = { current: null }
function Harness() {
  const s = useGraphSession()
  useLayoutEffect(() => {
    holder.current = s
  })
  return <>{s.element}</>
}

const st = () => useNodeBuilderStore.getState()

/** getGraph answers only when the test says so. */
function deferredLoads() {
  const pending = new Map<string, (env: GraphEnvelope) => void>()
  vi.mocked(getGraph).mockImplementation(id => new Promise<GraphEnvelope>(resolve => { pending.set(id, resolve) }))
  return {
    resolve: async (id: string) => {
      await act(async () => {
        pending.get(id)?.(envelope(id))
        await Promise.resolve()
      })
    },
  }
}

let offShow: (() => void) | null = null
/** Do what NodeBuilder does: shown graph view, then openLastGraph in an effect (after the render). */
function reopenLastOnShow() {
  const on = () => queueMicrotask(() => holder.current?.openLastGraph())
  window.addEventListener(SHOW_GRAPH_VIEW_EVENT, on)
  offShow = () => window.removeEventListener(SHOW_GRAPH_VIEW_EVENT, on)
}

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  vi.mocked(getGraph).mockReset()
  st().discardEdits()
})

afterEach(() => {
  offShow?.()
  offShow = null
  cleanup()
})

describe('a link open loads one graph (F453)', () => {
  it('the link graph opens, not the last graph, when the view is shown for the first time', async () => {
    setLastGraphId('g_last')
    const loads = deferredLoads()
    render(<Harness />)
    reopenLastOnShow()
    await act(async () => {
      requestOpenGraph({ graphId: 'g_bot', group: null, spawn: false })
      await Promise.resolve()
      await Promise.resolve()
    })
    await loads.resolve('g_bot')
    await loads.resolve('g_last')
    expect(vi.mocked(getGraph).mock.calls.map(c => c[0])).toEqual(['g_bot'])
    expect(st().graphMeta?.id).toBe('g_bot')
  })

  it('a link while openLastGraph is still loading wins, and the late last graph is dropped', async () => {
    setLastGraphId('g_last')
    const loads = deferredLoads()
    render(<Harness />)
    await act(async () => { holder.current!.openLastGraph() })
    expect(vi.mocked(getGraph)).toHaveBeenCalledWith('g_last')
    await act(async () => {
      requestOpenGraph({ graphId: 'g_bot', group: null, spawn: false })
      await Promise.resolve()
    })
    await loads.resolve('g_bot')
    await loads.resolve('g_last')
    expect(st().graphMeta?.id).toBe('g_bot')
  })
})
