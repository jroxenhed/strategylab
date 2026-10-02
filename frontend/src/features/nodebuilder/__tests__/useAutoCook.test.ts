/**
 * Auto cook (F435 W4 item 4.C, spec S27, amendment A4): a debounced
 * `/preview` after each commit that changes the graph's data, never the
 * backtest.
 *
 * `preview()` is mocked; fake timers drive the 500 ms debounce.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, render, act, cleanup, fireEvent, screen } from '@testing-library/react'
import { createElement } from 'react'

vi.mock('../../../api/nodebuilderInspect', () => ({ preview: vi.fn() }))

import { preview, type PreviewResponse } from '../../../api/nodebuilderInspect'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { useNodeBuilderStore } from '../store'
import { AUTOCOOK_KEY, EMPTY_PREVIEW, IDLE_COOK, IDLE_PREVIEW_COOK } from '../store/status'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import { clearNotices, useNoticeStore } from '../notices'
import {
  AUTO_COOK_DEBOUNCE_MS,
  AUTOCOOK_OFF_NOTICE,
  FIX_ERRORS_NOTE,
  PREVIEW_POINTS,
  STALE_DATA_NOTE,
  useAutoCook,
  type CookWindow,
} from '../useAutoCook'
import { AutoCookToggle, autoCookCommands } from '../AutoCookToggle'

const previewMock = vi.mocked(preview)

const W: CookWindow = { ticker: 'AAPL', start: '2023-01-01', end: '2024-01-01', interval: '1d', source: 'yahoo' }

function node(id: string, type: string, params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false }
}

function makeGraph(): Graph {
  const nodes = [node('t', 'ticker', { symbol: 'AAPL', interval: '1d' }), node('r', 'rsi', { period: 14 })]
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires: [{ id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' }],
    annotations: { boxes: [], notes: [] },
  }
}

let seq = 0
function answer(): PreviewResponse {
  seq += 1
  return {
    cook_id: `ck_${seq}`,
    nodes: { r: { attr: '@rsi', kind: 'line', min: 10, max: 90, nan_count: 14, values: [null, 20, 40] } },
  }
}

const st = () => useNodeBuilderStore.getState()

/** Let resolved promises run (fake timers do not run microtasks by themselves). */
async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

async function advance(ms: number) {
  await act(async () => { vi.advanceTimersByTime(ms) })
  await flush()
}

function setPeriod(p: number) {
  act(() => { st().updateNodeParams('r', { period: p }) })
}

/** Open the graph, mount the hook, let the first (load) cook finish, then clear the mock. */
async function mountCooked() {
  previewMock.mockImplementation(() => Promise.resolve(answer()))
  act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
  const hook = renderHook(() => useAutoCook({ window: W }))
  await advance(AUTO_COOK_DEBOUNCE_MS)
  expect(previewMock).toHaveBeenCalledTimes(1)
  previewMock.mockClear()
  return hook
}

beforeEach(() => {
  vi.useFakeTimers()
  seq = 0
  previewMock.mockReset()
  try { localStorage.removeItem(AUTOCOOK_KEY) } catch { /* no storage */ }
  useNodeBuilderStore.setState({
    preview: EMPTY_PREVIEW,
    cooks: { backtest: IDLE_COOK, preview: IDLE_PREVIEW_COOK },
    lastCookKind: 'backtest',
    cook: IDLE_COOK,
    autoCook: true,
  })
  resetDiagnostics()
  clearNotices()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  st().discardEdits()
})

describe('useAutoCook', () => {
  it('one commit sends one /preview, 500 ms later', async () => {
    await mountCooked()
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS - 1)
    expect(previewMock).not.toHaveBeenCalled()
    await advance(1)
    expect(previewMock).toHaveBeenCalledTimes(1)
    const [req, signal] = previewMock.mock.calls[0]
    expect(req).toMatchObject({ cook_id: null, window: W, node_ids: null, points: PREVIEW_POINTS })
    expect(req.graph?.nodes.r.params.period).toBe(20)
    expect(signal).toBeInstanceOf(AbortSignal)
    // The answer lands in the store and the preview cook is cooked.
    expect(st().preview.cookId).toBe('ck_2')
    expect(st().preview.nodes.r.attr).toBe('@rsi')
    expect(st().cooks.preview).toMatchObject({ phase: 'cooked', cookId: 'ck_2', kind: 'preview' })
    expect(st().cook.kind).toBe('preview')
  })

  it('two commits 200 ms apart send one /preview, 500 ms after the second', async () => {
    await mountCooked()
    setPeriod(20)
    await advance(200)
    setPeriod(21)
    await advance(AUTO_COOK_DEBOUNCE_MS - 1)
    expect(previewMock).not.toHaveBeenCalled()
    await advance(1)
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(previewMock.mock.calls[0][0].graph?.nodes.r.params.period).toBe(21)
  })

  it('a second commit aborts the request in flight', async () => {
    await mountCooked()
    const signals: AbortSignal[] = []
    previewMock.mockImplementation((_req, signal) => {
      if (signal) signals.push(signal)
      return new Promise<PreviewResponse>(() => {})  // never answers
    })
    const abortSpy = vi.spyOn(AbortController.prototype, 'abort')
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(st().cooks.preview.phase).toBe('cooking')
    expect(signals[0].aborted).toBe(false)

    setPeriod(21)
    expect(signals[0].aborted).toBe(true)
    expect(abortSpy).toHaveBeenCalled()
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(2)
    expect(signals[1].aborted).toBe(false)
    abortSpy.mockRestore()
  })

  it('drops a late answer to a superseded request', async () => {
    await mountCooked()
    let resolveFirst: (r: PreviewResponse) => void = () => {}
    previewMock.mockImplementationOnce(() => new Promise<PreviewResponse>(r => { resolveFirst = r }))
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    setPeriod(21)
    resolveFirst({ cook_id: 'ck_old', nodes: {} })
    await flush()
    expect(st().preview.cookId).not.toBe('ck_old')
  })

  it('does not cook on selection, a node move or a viewport change', async () => {
    await mountCooked()
    act(() => { st().select('r') })
    act(() => { st().moveNode('r', [100, 50]) })
    act(() => { st().setViewport({ x: 10, y: 10, zoom: 0.8 }) })
    await advance(AUTO_COOK_DEBOUNCE_MS * 2)
    expect(previewMock).not.toHaveBeenCalled()
    expect(st().preview.stale).toBe(false)
  })

  it('marks the sparklines stale on a commit, until the answer', async () => {
    await mountCooked()
    setPeriod(20)
    expect(st().preview.stale).toBe(true)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(st().preview.stale).toBe(false)
  })

  it('with errors on the graph it sends nothing and the preview cook is stale', async () => {
    await mountCooked()
    setPeriod(20)
    const err: Diagnostic = {
      node_id: 'r', path: '/r', severity: 'error', code: 'bad_param', message: 'bad',
      param: 'period', port: null, line: null, col: null, end_line: null, end_col: null,
    }
    act(() => { setServerDiagnostics([err]) })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).not.toHaveBeenCalled()
    expect(st().cooks.preview).toMatchObject({ stale: true, staleNote: FIX_ERRORS_NOTE })
    expect(st().cook.stale).toBe(true)
  })

  it('three failures in a row turn auto cook off and show a banner', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.reject(Object.assign(new Error('boom'), { response: { status: 500, data: { detail: 'kaput' } } })))
    for (let i = 0; i < 3; i++) {
      setPeriod(20 + i)
      await advance(AUTO_COOK_DEBOUNCE_MS)
      if (i < 2) expect(st().autoCook).toBe(true)
    }
    expect(previewMock).toHaveBeenCalledTimes(3)
    expect(st().cooks.preview.phase).toBe('failed')
    expect(st().autoCook).toBe(false)
    expect(useNoticeStore.getState().notices.some(n => n.key === AUTOCOOK_OFF_NOTICE)).toBe(true)
    // Off: further commits send nothing.
    setPeriod(40)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(3)
  })

  it('after a backtest with cook_id ck_bt, asks /preview once with that id, even with auto cook off', async () => {
    await mountCooked()
    act(() => { st().setAutoCook(false) })
    act(() => { st().setCook({ phase: 'cooking', startedAt: Date.now() }) })
    act(() => { st().setCook({ phase: 'cooked', endedAt: Date.now(), cookId: 'ck_bt' }) })
    await flush()
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(previewMock.mock.calls[0][0].cook_id).toBe('ck_bt')
    await advance(AUTO_COOK_DEBOUNCE_MS * 2)
    expect(previewMock).toHaveBeenCalledTimes(1)
  })

  it('a different sidebar window cooks again; the same values do not', async () => {
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
    const { rerender } = renderHook(({ w }) => useAutoCook({ window: w }), { initialProps: { w: W } })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    previewMock.mockClear()
    rerender({ w: { ...W } })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).not.toHaveBeenCalled()
    const w2 = { ...W, start: '2022-01-01' }
    rerender({ w: w2 })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(previewMock.mock.calls[0][0].window).toEqual(w2)
  })

  it('aborts the request in flight on unmount and when hidden', async () => {
    const hook = await mountCooked()
    const signals: AbortSignal[] = []
    previewMock.mockImplementation((_req, signal) => { if (signal) signals.push(signal); return new Promise<PreviewResponse>(() => {}) })
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    hook.unmount()
    expect(signals[0].aborted).toBe(true)
    expect(st().cooks.preview.phase).toBe('cancelled')
  })

  it('a newly loaded graph drops the old sparklines', async () => {
    await mountCooked()
    expect(Object.keys(st().preview.nodes)).toEqual(['r'])
    act(() => { st().openGraph(makeGraph(), { id: 'g_other', rev: 1, name: 'other' }) })
    expect(st().preview.nodes).toEqual({})
    expect(st().cooks.preview.phase).toBe('idle')
  })

  it('the post-load tidy (same graph id and eval key, new epoch) keeps the sparklines and does not re-cook', async () => {
    await mountCooked()
    const before = st().preview
    // The elk tidy after "Edit this graph" (3.F): new positions, new epoch.
    act(() => {
      useNodeBuilderStore.setState(s => {
        const g = s.graph!
        const moved = { ...g, nodes: Object.fromEntries(Object.entries(g.nodes).map(([id, n]) => [id, { ...n, position: [40, 80] as [number, number] }])) }
        return { graph: moved, savedGraph: moved, layoutEpoch: s.layoutEpoch + 1 }
      })
    })
    await advance(AUTO_COOK_DEBOUNCE_MS * 2)
    expect(st().preview).toBe(before)
    expect(previewMock).not.toHaveBeenCalled()
  })
})

describe('useAutoCook review fixes', () => {
  it('a superseded preview goes back to idle, never "cancelled" (DV-6)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => new Promise<PreviewResponse>(() => {}))
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(st().cooks.preview.phase).toBe('cooking')
    const phases: string[] = []
    const unsub = useNodeBuilderStore.subscribe(s => { phases.push(s.cooks.preview.phase) })
    setPeriod(21)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    unsub()
    expect(phases).not.toContain('cancelled')
    expect(st().cooks.preview.phase).toBe('cooking')
  })

  it('the refresh after a backtest leaves the status bar on the backtest (DV-6)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.resolve({ ...answer(), cook_id: 'ck_bt' }))
    act(() => { st().setCook({ phase: 'cooking', startedAt: Date.now() }) })
    act(() => { st().setCook({ phase: 'cooked', endedAt: Date.now(), cookId: 'ck_bt' }) })
    await flush()
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(st().cooks.preview).toMatchObject({ phase: 'cooked', cookId: 'ck_bt' })
    expect(st().lastCookKind).toBe('backtest')
    expect(st().cook.kind).toBe('backtest')
    expect(st().cook.phase).toBe('cooked')
  })

  it('does not cook a preview while a backtest cooks; the backtest refresh covers it (DV-12)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    setPeriod(20)
    act(() => { st().setCook({ phase: 'cooking', startedAt: Date.now() }) })
    await advance(AUTO_COOK_DEBOUNCE_MS * 2)
    expect(previewMock).not.toHaveBeenCalled()
    act(() => { st().setCook({ phase: 'cooked', endedAt: Date.now(), cookId: 'ck_bt' }) })
    await flush()
    await advance(AUTO_COOK_DEBOUNCE_MS * 2)
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(previewMock.mock.calls[0][0].cook_id).toBe('ck_bt')
  })

  it('an edit made during the backtest still gets its own preview (DV-12)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    act(() => { st().setCook({ phase: 'cooking', startedAt: Date.now() }) })
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS * 2)
    expect(previewMock).not.toHaveBeenCalled()
    act(() => { st().setCook({ phase: 'cooked', endedAt: Date.now(), cookId: 'ck_bt' }) })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    const last = previewMock.mock.calls.at(-1)![0]
    expect(last.cook_id).toBeNull()
    expect(last.graph?.nodes.r.params.period).toBe(20)
  })

  it('a failed backtest lets the waiting preview run (DV-12)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    setPeriod(20)
    act(() => { st().setCook({ phase: 'cooking', startedAt: Date.now() }) })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).not.toHaveBeenCalled()
    act(() => { st().setCook({ phase: 'failed', endedAt: Date.now() }) })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(1)
  })

  it('a graph loaded while hidden drops the old sparklines when shown (DV-13)', async () => {
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    act(() => { st().openGraph(makeGraph(), { id: 'g_a', rev: 0, name: 'a' }) })
    const { rerender } = renderHook(({ active }) => useAutoCook({ window: W, active }), { initialProps: { active: true } })
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(Object.keys(st().preview.nodes)).toEqual(['r'])
    rerender({ active: false })
    act(() => { st().openGraph(makeGraph(), { id: 'g_b', rev: 1, name: 'b' }) })
    // openGraph's own reset may already clear; force old data back to prove the hook resets it.
    act(() => { st().setPreview('ck_old', answer().nodes) })
    previewMock.mockClear()
    rerender({ active: true })
    expect(st().preview.nodes).toEqual({})
    expect(st().cooks.preview.phase).toBe('idle')
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(1)
  })

  it('does not reuse a cook id the server did not keep (BE-2)', async () => {
    previewMock.mockImplementation(() => Promise.resolve({ ...answer(), kept: false }))
    act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
    renderHook(() => useAutoCook({ window: W }))
    await advance(AUTO_COOK_DEBOUNCE_MS)
    previewMock.mockClear()
    // Back to the cooked graph: a kept cook would be asked for by id.
    setPeriod(20)
    setPeriod(14)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock).toHaveBeenCalledTimes(1)
    expect(previewMock.mock.calls[0][0].cook_id).toBeNull()
  })

  it('reuses a kept cook id for the same graph (BE-2 control)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    setPeriod(20)
    setPeriod(14)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(previewMock.mock.calls[0][0].cook_id).toBe('ck_1')
  })

  it('notes a preview served from the last good cook (BE-3)', async () => {
    await mountCooked()
    previewMock.mockImplementation(() => Promise.resolve({ ...answer(), stale_data: true }))
    setPeriod(20)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(st().cooks.preview).toMatchObject({ phase: 'cooked', stale: false, staleNote: STALE_DATA_NOTE })
    previewMock.mockImplementation(() => Promise.resolve(answer()))
    setPeriod(21)
    await advance(AUTO_COOK_DEBOUNCE_MS)
    expect(st().cooks.preview.staleNote).toBeNull()
  })
})

describe('AutoCookToggle', () => {
  it('toggles aria-checked, persists nb.autocook and survives a remount', async () => {
    act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
    const first = render(createElement(AutoCookToggle))
    const sw = screen.getByTestId('nb-autocook')
    expect(sw.getAttribute('role')).toBe('switch')
    expect(sw.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(sw)
    expect(sw.getAttribute('aria-checked')).toBe('false')
    expect(localStorage.getItem(AUTOCOOK_KEY)).toBe('off')
    first.unmount()
    render(createElement(AutoCookToggle))
    expect(screen.getByTestId('nb-autocook').getAttribute('aria-checked')).toBe('false')
  })

  it('the A command toggles it and flashes the new state', () => {
    act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
    const cmd = autoCookCommands[0]
    expect(cmd.id).toBe('cook.toggleAuto')
    expect(cmd.keys).toEqual(['a'])
    act(() => { cmd.run({ canvas: null, store: useNodeBuilderStore, event: null }) })
    expect(st().autoCook).toBe(false)
    expect(st().flash?.text).toBe('auto cook off')
  })

  it('is not shown for a read-only graph', () => {
    act(() => { useNodeBuilderStore.setState({ graph: { ...makeGraph(), readOnly: true } }) })
    render(createElement(AutoCookToggle))
    expect(screen.queryByTestId('nb-autocook')).toBeNull()
  })
})
