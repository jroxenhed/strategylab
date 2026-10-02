/**
 * Data Sheet tests (F435 W4 item 4.B, spec S25).
 *
 * POST /inspect is mocked with a small fake server over 1 256 intraday bars
 * (5-minute bars from 2024-03-04 09:30 ET). Nothing reaches a real server.
 * Covers: ET wall-clock times, the row count, virtualized rows, paging
 * offsets, the filter syntax, bool cells and the `% true` header, a wire
 * target with the consumer's reads, the 410 re-cook, jump to trades, the
 * empty states, and the pure helpers.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, act, fireEvent, waitFor, within } from '@testing-library/react'
import { AxiosError } from 'axios'
import type { Graph, GraphNode, ParamValue } from '../../../api/nodebuilder'
import {
  inspect,
  type InspectRequest,
  type InspectResponse,
  type InspectWindow,
} from '../../../api/nodebuilderInspect'
import { useNodeBuilderStore } from '../store'
import { DataSheet, clampSheetHeight, noCookMessage, SHEET_SMALL_COLUMN, SHEET_STALE_DATA_TEXT } from '../DataSheet'
import { headerStatsText } from '../datasheet/HeaderCell'
import { entryTimesFromTrades } from '../datasheet/trades'
import { resetSheetUi, useSheetUi, SHEET_STORAGE_KEY } from '../datasheet/sheetUi'
import { formatNumber, formatSheetTime, formatTruePct } from '../datasheet/format'
import { filterSuggestions, parseSheetFilter } from '../datasheet/filter'
import { resolveSheetTarget } from '../datasheet/target'
import {
  evictRows, firstMissing, indexOfTime, mergePage, pageOffsetFor,
  EXPIRED_NO_FALLBACK_TEXT, type SheetData, type SheetRow,
} from '../datasheet/useSheetData'
import { SHEET_MIN_HEIGHT } from '../datasheet/sheetUi'
import { useNoticeStore, clearNotices } from '../notices'
import { setTimezone } from '../../../shared/utils/time'

vi.mock('../../../api/nodebuilderInspect', async importOriginal => {
  const real = await importOriginal<typeof import('../../../api/nodebuilderInspect')>()
  return { ...real, inspect: vi.fn() }
})

const inspectMock = vi.mocked(inspect)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const N = 1256
const T0 = 1709562600 // 2024-03-04 14:30 UTC = 09:30 ET
const STEP = 300

const WINDOW: InspectWindow = { ticker: 'AAPL', start: '2024-03-01', end: '2024-03-31', interval: '5m', source: 'yahoo' }

function node(id: string, type: string, params: Record<string, ParamValue> = {}, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false, ...extra }
}

function makeGraph(): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      aapl: node('aapl', 'ticker', { symbol: 'AAPL', interval: '5m' }),
      rsi: node('rsi', 'rsi', { period: 14 }),
      xb: node('xb', 'crosses_above', {}, { display: true }),
      sl: node('sl', 'stop_loss', { pct: 2.5 }),
    },
    wires: [
      { id: 'w1', from: 'aapl', to: 'rsi', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'rsi', to: 'xb', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

function barTime(i: number): number {
  return T0 + i * STEP
}

function rsiAt(i: number): number | null {
  return i < 14 ? null : 50 + 30 * Math.sin(i / 10)
}

function xbAt(i: number): boolean {
  return i % 41 === 0 // 31 true rows in 1 256
}

/** A fake /inspect over the fixture stream: filter, around_time and paging. */
function fakeInspect(req: InspectRequest, cookId = 'ck_1'): InspectResponse {
  let idx = Array.from({ length: N }, (_, i) => i)
  const f = req.filter
  if (f) {
    const val = (i: number) => (f.attr === '@xb_rsi' ? (xbAt(i) ? 1 : 0) : rsiAt(i))
    idx = idx.filter(i => {
      const v = val(i)
      switch (f.op) {
        case 'is_true': return v === 1
        case 'is_false': return v === 0
        case 'gt': return v != null && v > (f.value ?? 0)
        case 'lt': return v != null && v < (f.value ?? 0)
        case 'not_nan': return v != null
      }
      return true
    })
  }
  const total = idx.length
  let offset = req.offset
  if (req.around_time != null) {
    const at = idx.findIndex(i => barTime(i) === req.around_time)
    offset = Math.max(0, Math.min(at - Math.floor(req.limit / 2), total - req.limit))
  }
  offset = Math.max(0, Math.min(offset, Math.max(0, total - 1)))
  const page = idx.slice(offset, offset + req.limit)
  const isWire = 'wire_id' in req.target
  const counts = Array.from({ length: 20 }, (_, i) => 40 + i)
  return {
    cook_id: cookId,
    cache: 'hit',
    stream_schema: 1,
    columns: [
      { name: '@time', dtype: 'time', written_by: null },
      { name: '@rsi', dtype: 'float', written_by: 'rsi' },
      { name: '@xb_rsi', dtype: 'bool', written_by: 'xb' },
    ],
    detail: [{ name: '@stop_pct', dtype: 'float', value: 2.5, written_by: 'sl' }],
    prims: [],
    ...(isWire ? { read_by_consumer: ['@rsi'] } : {}),
    time: page.map(barTime),
    rows: page.map(i => [rsiAt(i), xbAt(i)]),
    total,
    offset,
    stats: {
      '@rsi': { min: 20, max: 80, nan_count: 14, hist: { edges: Array.from({ length: 21 }, (_, i) => 20 + i * 3), counts } },
      '@xb_rsi': { true_count: 31 },
    },
  }
}

function expired(): AxiosError {
  return new AxiosError('Gone', 'ERR_BAD_REQUEST', undefined, undefined, {
    status: 410,
    statusText: 'Gone',
    data: { detail: { code: 'cook_expired' } },
    headers: {},
    config: { headers: {} },
  } as never)
}

const norm = (s: string | null | undefined) => (s ?? '').replace(/\u2009/g, ' ')

function load(graph: Graph = makeGraph()) {
  act(() => { useNodeBuilderStore.getState().openGraph(graph, { id: 'g_1', rev: 1, name: 'sheet test' }) })
}

function selectNode(id: string) {
  act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: [id] }) })
}

function lastReq(): InspectRequest {
  const calls = inspectMock.mock.calls
  return calls[calls.length - 1][0]
}

async function renderSheet(props: Partial<Parameters<typeof DataSheet>[0]> = {}) {
  const utils = render(
    <div className="nodebuilder-root">
      <DataSheet open cookId="ck_1" window={WINDOW} {...props} />
    </div>,
  )
  return utils
}

async function waitForRows() {
  await waitFor(() => expect(screen.getByTestId('nb-sheet-row-0').getAttribute('data-time')).not.toBeNull())
}

beforeEach(() => {
  try { window.localStorage.removeItem(SHEET_STORAGE_KEY) } catch { /* none */ }
  setTimezone('ET')
  resetSheetUi()
  act(() => { useSheetUi.getState().setHeight(240) })
  clearNotices()
  inspectMock.mockReset()
  inspectMock.mockImplementation(async req => fakeInspect(req))
  act(() => { useNodeBuilderStore.getState().newGraph() })
})

afterEach(() => {
  cleanup()
})

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe('format', () => {
  it('shows intraday unix seconds as ET wall clock, daily strings as given', () => {
    expect(formatSheetTime(1709562600)).toBe('2024-03-04 09:30')
    // Summer time: 2024-07-01 13:30 UTC is 09:30 EDT.
    expect(formatSheetTime(1719840600)).toBe('2024-07-01 09:30')
    expect(formatSheetTime('2024-03-04')).toBe('2024-03-04')
  })

  it('formats numbers per shared part 0.7', () => {
    expect(formatNumber(41.2)).toBe('41.2000')
    expect(norm(formatNumber(48213400))).toBe('48 213 400')
    expect(formatNumber(null)).toBe('nan')
    expect(formatNumber(NaN)).toBe('nan')
    expect(formatNumber(-0.5)).toBe('-0.5000')
    expect(formatTruePct(31, 1256)).toBe('2.5 % true')
  })
})

describe('filter syntax', () => {
  it('parses the five forms and rejects anything else', () => {
    expect(parseSheetFilter('@xb_rsi')).toEqual({ ok: true, filter: { attr: '@xb_rsi', op: 'is_true', value: null } })
    expect(parseSheetFilter('!@xb_rsi')).toEqual({ ok: true, filter: { attr: '@xb_rsi', op: 'is_false', value: null } })
    expect(parseSheetFilter('@rsi > 30')).toEqual({ ok: true, filter: { attr: '@rsi', op: 'gt', value: 30 } })
    expect(parseSheetFilter('@rsi<-1.5')).toEqual({ ok: true, filter: { attr: '@rsi', op: 'lt', value: -1.5 } })
    expect(parseSheetFilter('@rsi is set')).toEqual({ ok: true, filter: { attr: '@rsi', op: 'not_nan', value: null } })
    expect(parseSheetFilter('  ')).toEqual({ ok: true, filter: null })
    expect(parseSheetFilter('rsi > 3')).toEqual({ ok: false })
    expect(parseSheetFilter('@rsi >')).toEqual({ ok: false })
    expect(parseSheetFilter('@rsi = 3')).toEqual({ ok: false })
  })

  it('suggests names, then operators', () => {
    const attrs = ['@rsi', '@xb_rsi', '@close']
    expect(filterSuggestions('@x', attrs).map(s => s.text)).toEqual(['@xb_rsi'])
    expect(filterSuggestions('@rsi', attrs).map(s => s.text)).toContain('@rsi > ')
    expect(filterSuggestions('@rsi ', attrs).map(s => s.label)).toEqual(['> …', '< …', 'is set'])
    expect(filterSuggestions('', attrs).length).toBeLessThanOrEqual(6)
  })
})

describe('target resolution', () => {
  it('prefers the selected wire, then the selected node, then the display node', () => {
    const g = makeGraph()
    expect(resolveSheetTarget(g, { selectedNodeId: 'rsi', selectedWireIds: ['w2'] }, null))
      .toEqual({ kind: 'wire', wireId: 'w2', fromId: 'rsi', toId: 'xb' })
    expect(resolveSheetTarget(g, { selectedNodeId: 'rsi', selectedWireIds: [] }, null)).toEqual({ kind: 'node', nodeId: 'rsi' })
    expect(resolveSheetTarget(g, { selectedNodeId: null, selectedWireIds: [] }, null)).toEqual({ kind: 'display', nodeId: 'xb' })
    // The display node of another network does not count.
    expect(resolveSheetTarget(g, { selectedNodeId: null, selectedWireIds: [] }, 'net1')).toBeNull()
    expect(resolveSheetTarget(null, { selectedNodeId: 'rsi', selectedWireIds: [] }, null)).toBeNull()
  })

  it('never targets a network or boundary node, which has no stream', () => {
    const g = makeGraph()
    for (const type of ['output_group', 'subnet', 'regime_net', 'subnet_input', 'subnet_output']) {
      const withNet = { ...g, nodes: { ...g.nodes, grp: { ...g.nodes.rsi, id: 'grp', name: 'grp', type, display: true } } }
      // A selected group frame falls through to the display node on screen.
      expect(resolveSheetTarget(withNet, { selectedNodeId: 'grp', selectedWireIds: [] }, null)).toEqual({ kind: 'display', nodeId: 'xb' })
      const onlyNet = { ...withNet, nodes: { ...withNet.nodes, xb: { ...g.nodes.xb, display: false } } }
      expect(resolveSheetTarget(onlyNet, { selectedNodeId: 'grp', selectedWireIds: [] }, null)).toBeNull()
    }
  })
})

describe('paging helpers', () => {
  it('asks 250 rows before the first missing row, clamped', () => {
    expect(pageOffsetFor(900, N)).toBe(650)
    expect(pageOffsetFor(100, N)).toBe(0)
  })

  it('keeps at most 2 000 rows, none further than 1 000 from the viewport', () => {
    const rows = new Map<number, SheetRow>()
    for (let i = 0; i < 3000; i++) rows.set(i, { time: i, values: [] })
    evictRows(rows, 1500)
    expect(rows.size).toBeLessThanOrEqual(2000)
    expect([...rows.keys()].every(i => Math.abs(i - 1500) <= 1000)).toBe(true)
    expect(firstMissing(rows, 400, 520, 3000)).toBe(400)
    expect(firstMissing(rows, 600, 700, 3000)).toBe(-1)
  })

  it('finds a bar time in a page, else the first later bar', () => {
    expect(indexOfTime([10, 20, 30], 20)).toBe(1)
    expect(indexOfTime([10, 20, 30], 25)).toBe(2)
    expect(indexOfTime(['2024-03-01', '2024-03-04'], '2024-03-02')).toBe(1)
  })

  it('picks entry times from trades, oldest first', () => {
    expect(entryTimesFromTrades([
      { type: 'sell', date: 500, price: 1, shares: 1 },
      { type: 'buy', date: 400, price: 1, shares: 1 },
      { type: 'short', date: 100, price: 1, shares: 1 },
    ])).toEqual([100, 400])
  })
})

// ---------------------------------------------------------------------------
// The drawer
// ---------------------------------------------------------------------------

describe('DataSheet', () => {
  it('renders intraday unix times as ET wall-clock times', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const first = screen.getByTestId('nb-sheet-row-0')
    expect(first.textContent).toContain('2024-03-04 09:30')
    expect(screen.getByTestId('nb-sheet-row-1').textContent).toContain('2024-03-04 09:35')
    expect(lastReq()).toMatchObject({ cook_id: 'ck_1', graph: null, window: null, target: { node_id: 'rsi' }, offset: 0, limit: 500 })
  })

  it('shows the count, virtualizes rows, and pages around the viewport', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    expect(norm(screen.getByTestId('nb-sheet-count').textContent)).toBe('1 256 rows')
    const rowCount = () => document.querySelectorAll('[data-testid^="nb-sheet-row-"]').length
    expect(rowCount()).toBeGreaterThan(0)
    expect(rowCount()).toBeLessThanOrEqual(Math.ceil(240 / 28 + 16))
    expect(screen.getByTestId('nb-sheet-grid').getAttribute('aria-rowcount')).toBe(String(N + 1))

    const grid = screen.getByTestId('nb-sheet-grid')
    inspectMock.mockClear()
    act(() => {
      grid.scrollTop = 900 * 28
      fireEvent.scroll(grid)
    })
    await waitFor(() => expect(inspectMock).toHaveBeenCalled())
    const off = lastReq().offset
    expect(Math.abs(off - 650)).toBeLessThanOrEqual(10)
    await waitFor(() => expect(screen.getByTestId('nb-sheet-row-900').getAttribute('data-time')).toBe(String(barTime(900))))
    // Rows far above are no longer drawn.
    expect(screen.queryByTestId('nb-sheet-row-0')).toBeNull()
    expect(rowCount()).toBeLessThanOrEqual(Math.ceil(240 / 28 + 16))
  })

  it('a missing page renders … cells without a request per row', async () => {
    let release: (() => void) | null = null
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    inspectMock.mockClear()
    inspectMock.mockImplementation(req => new Promise(res => { release = () => res(fakeInspect(req)) }))
    const grid = screen.getByTestId('nb-sheet-grid')
    // A thumb drag: several scroll events in a row send one request, once
    // the rows settle (DV-8), not one per page crossed.
    act(() => { grid.scrollTop = 700 * 28; fireEvent.scroll(grid) })
    act(() => { grid.scrollTop = 900 * 28; fireEvent.scroll(grid) })
    act(() => { grid.scrollTop = 1100 * 28; fireEvent.scroll(grid) })
    const row = await screen.findByTestId('nb-sheet-row-1100')
    expect(row.textContent).toContain('…')
    expect(inspectMock).not.toHaveBeenCalled()
    await waitFor(() => expect(inspectMock).toHaveBeenCalledTimes(1))
    expect(lastReq().offset).toBe(pageOffsetFor(1100, N))
    await act(async () => { release?.() })
    await waitFor(() => expect(screen.getByTestId('nb-sheet-row-1100').textContent).not.toContain('…'))
  })

  it('draws bool cells and the % true header, and a numeric histogram', async () => {
    load()
    selectNode('xb')
    await renderSheet()
    await waitForRows()
    const header = screen.getByTestId('nb-sheet-col-@xb_rsi')
    expect(header.textContent).toContain('2.5 % true')
    const row0 = screen.getByTestId('nb-sheet-row-0')
    const cells = within(row0).getAllByRole('gridcell')
    // time, @rsi, @xb_rsi
    expect(cells[2].className).toContain('nb-sheet__td--true')
    expect(cells[2].textContent).toBe('true')
    expect(cells[1].textContent).toBe('nan')
    const row1 = within(screen.getByTestId('nb-sheet-row-1')).getAllByRole('gridcell')
    expect(row1[2].className).not.toContain('nb-sheet__td--true')
    expect(row1[2].textContent).toBe('·')
    const rsiHeader = screen.getByTestId('nb-sheet-col-@rsi')
    // 20 bins plus the hatched nan bar.
    expect(rsiHeader.querySelectorAll('.nb-sheet__bar').length).toBe(21)
    expect(rsiHeader.querySelector('.nb-sheet__bar--nan')).not.toBeNull()
    expect(rsiHeader.querySelector('.nb-sheet__bar')?.getAttribute('title')).toBe('[20.0, 23.0) · 40 rows')
    // The written-by chip names the writer and selects it.
    const by = screen.getByTestId('nb-sheet-by-@rsi')
    expect(by.getAttribute('aria-label')).toBe('written by rsi; select node')
    // The detail strip shows scalars.
    expect(screen.getByTestId('nb-sheet-detail').textContent).toContain('@stop_pct = 2.5')
  })

  it('the written-by chip selects the writer node', async () => {
    load()
    selectNode('xb')
    await renderSheet()
    await waitForRows()
    fireEvent.click(screen.getByTestId('nb-sheet-by-@rsi'))
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('rsi')
  })

  it('sends the filter from the typed text and shows the filtered count', async () => {
    load()
    selectNode('xb')
    await renderSheet()
    await waitForRows()
    const input = screen.getByTestId('nb-sheet-filter') as HTMLInputElement
    inspectMock.mockClear()
    // Typing alone does not request.
    fireEvent.change(input, { target: { value: '@xb_rsi > 0.5' } })
    expect(inspectMock).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(inspectMock).toHaveBeenCalled())
    expect(lastReq().filter).toEqual({ attr: '@xb_rsi', op: 'gt', value: 0.5 })
    await waitFor(() => expect(norm(screen.getByTestId('nb-sheet-count').textContent)).toBe('31 of 1 256 rows'))

    fireEvent.change(input, { target: { value: '!@xb_rsi' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(lastReq().filter).toEqual({ attr: '@xb_rsi', op: 'is_false', value: null }))

    // Invalid text: marked, and the last valid filter stays.
    inspectMock.mockClear()
    fireEvent.change(input, { target: { value: '@xb_rsi >' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('title')).toContain('@attr is set')
    expect(inspectMock).not.toHaveBeenCalled()
    expect(useSheetUi.getState().filter).toBe('!@xb_rsi')
  })

  it('shows the empty state when nothing matches, and clears the filter', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const input = screen.getByTestId('nb-sheet-filter')
    fireEvent.change(input, { target: { value: '@rsi > 1000' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    const empty = await screen.findByTestId('nb-sheet-empty')
    expect(empty.textContent).toContain('No rows match the filter.')
    fireEvent.click(within(empty).getByText('Clear filter'))
    await waitFor(() => expect(lastReq().filter).toBeNull())
    await waitForRows()
  })

  it('a wire target shows the source stream with the consumer reads marked', async () => {
    load()
    act(() => { useNodeBuilderStore.getState().setSelection({ wireIds: ['w2'] }) })
    await renderSheet()
    await waitForRows()
    expect(screen.getByTestId('nb-sheet-target').textContent).toContain('wire rsi → xb')
    expect(lastReq().target).toEqual({ wire_id: 'w2' })
    const read = screen.getByTestId('nb-sheet-col-@rsi')
    expect(read.getAttribute('data-read')).toBe('true')
    expect(read.className).toContain('nb-sheet__th--read')
    expect(screen.getByTestId('nb-sheet-col-@xb_rsi').getAttribute('data-read')).toBeNull()
  })

  it('follows the selection, then the display node, and marks a bypassed node', async () => {
    const g = makeGraph()
    g.nodes.rsi.bypass = true
    load(g)
    await renderSheet()
    await waitForRows()
    expect(screen.getByTestId('nb-sheet-target').textContent).toContain('display xb')
    expect(lastReq().target).toEqual({ node_id: 'xb' })
    selectNode('rsi')
    await waitFor(() => expect(lastReq().target).toEqual({ node_id: 'rsi' }))
    expect(screen.getByTestId('nb-sheet-target').textContent).toContain('node rsi · input stream (bypassed)')
  })

  it('re-requests with graph and window when the cook expired (410)', async () => {
    load()
    selectNode('rsi')
    inspectMock.mockReset()
    inspectMock
      .mockImplementationOnce(async () => { throw expired() })
      .mockImplementation(async req => fakeInspect(req, 'ck_2'))
    await renderSheet()
    await waitForRows()
    expect(inspectMock).toHaveBeenCalledTimes(2)
    const [first, second] = inspectMock.mock.calls.map(c => c[0])
    expect(first).toMatchObject({ cook_id: 'ck_1', graph: null, window: null })
    expect(second.cook_id).toBe('ck_1')
    expect(second.window).toEqual(WINDOW)
    expect(second.graph?.nodes.rsi).toBeDefined()
    expect(screen.queryByTestId('nb-sheet-error')).toBeNull()

    // Later pages use the cook the re-cook returned, without graph and window.
    const grid = screen.getByTestId('nb-sheet-grid')
    act(() => { grid.scrollTop = 900 * 28; fireEvent.scroll(grid) })
    await waitFor(() => expect(inspectMock).toHaveBeenCalledTimes(3))
    expect(lastReq()).toMatchObject({ cook_id: 'ck_2', graph: null, window: null })
  })

  it('re-requests with graph and window when the target is not in the cook (404 target_not_found)', async () => {
    // The sheet's cook can predate a node the user just added: the server
    // answers 404 target_not_found, and sending the graph cooks it fresh.
    load()
    selectNode('rsi')
    inspectMock.mockReset()
    inspectMock
      .mockImplementationOnce(async () => {
        throw new AxiosError('Not Found', 'ERR_BAD_REQUEST', undefined, undefined, {
          status: 404, statusText: 'Not Found',
          data: { detail: { code: 'target_not_found', message: "no cooked node 'rsi' in the graph" } },
          headers: {}, config: { headers: {} },
        } as never)
      })
      .mockImplementation(async req => fakeInspect(req, 'ck_2'))
    await renderSheet()
    await waitForRows()
    expect(inspectMock).toHaveBeenCalledTimes(2)
    const second = inspectMock.mock.calls[1][0]
    expect(second.window).toEqual(WINDOW)
    expect(second.graph?.nodes.rsi).toBeDefined()
    expect(screen.queryByTestId('nb-sheet-error')).toBeNull()
    // Not an expiry: no cook_expired banner.
    expect(useNoticeStore.getState().notices.some(n => /expired/.test(String(n.text)))).toBe(false)
  })

  it('shows "X of Y rows" from total_unfiltered when the filter is set on the first load', async () => {
    // A filter restored from nb.sheet: no unfiltered page was ever loaded,
    // so Y comes from the server's total_unfiltered.
    act(() => { useSheetUi.setState({ filter: '@xb_rsi > 0.5' }) })
    inspectMock.mockImplementation(async req => ({ ...fakeInspect(req), total_unfiltered: N }))
    load()
    selectNode('xb')
    await renderSheet()
    await waitFor(() => expect(norm(screen.getByTestId('nb-sheet-count').textContent)).toBe('31 of 1 256 rows'))
    expect(inspectMock.mock.calls[0][0].filter).toEqual({ attr: '@xb_rsi', op: 'gt', value: 0.5 })
  })

  it('shows the error state and the cook_expired banner when it cannot re-cook', async () => {
    load()
    selectNode('rsi')
    inspectMock.mockReset()
    inspectMock.mockImplementation(async () => { throw expired() })
    await renderSheet({ window: null })
    const err = await screen.findByTestId('nb-sheet-error')
    expect(err.textContent).toContain('Could not load data')
    expect(inspectMock).toHaveBeenCalledTimes(1)
    expect(useNoticeStore.getState().notices.some(n => n.key === 'cook_expired')).toBe(true)
    // Retry asks again.
    inspectMock.mockImplementation(async req => fakeInspect(req))
    fireEvent.click(within(err).getByText('Retry'))
    await waitForRows()
  })

  it('jumps to trade entries with around_time and flashes the row', async () => {
    load()
    selectNode('rsi')
    const trades = [barTime(700), barTime(100)]
    await renderSheet({ tradeTimes: trades })
    await waitForRows()
    const next = screen.getByTestId('nb-sheet-jump-next')
    expect(next).not.toBeDisabled()
    inspectMock.mockClear()
    fireEvent.click(next)
    await waitFor(() => expect(inspectMock).toHaveBeenCalled())
    expect(lastReq().around_time).toBe(barTime(100))
    const row = await screen.findByTestId('nb-sheet-row-100')
    await waitFor(() => expect(row.className).toContain('nb-sheet__tr--flash'))
    expect(row.getAttribute('data-time')).toBe(String(barTime(100)))
    expect(screen.getByText('1 / 2')).toBeTruthy()

    fireEvent.click(next)
    await waitFor(() => expect(lastReq().around_time).toBe(barTime(700)))
    const row700 = await screen.findByTestId('nb-sheet-row-700')
    await waitFor(() => expect(row700.className).toContain('nb-sheet__tr--flash'))
  })

  it('disables jump without trades', async () => {
    load()
    selectNode('rsi')
    await renderSheet({ tradeTimes: null })
    await waitForRows()
    const next = screen.getByTestId('nb-sheet-jump-next')
    expect(next).toBeDisabled()
    expect(next.getAttribute('title')).toBe('Run the backtest to get trades')
  })

  it('shows the no-cook and no-target states without requesting', async () => {
    load()
    const onRun = vi.fn()
    const onAuto = vi.fn()
    const { rerender } = render(
      <div className="nodebuilder-root">
        <DataSheet open cookId={null} window={WINDOW} onRunBacktest={onRun} onAutoCookOn={onAuto} />
      </div>,
    )
    const noCook = screen.getByTestId('nb-sheet-nocook')
    fireEvent.click(within(noCook).getByText('Run backtest'))
    fireEvent.click(within(noCook).getByText('Auto cook on'))
    expect(onRun).toHaveBeenCalledTimes(1)
    expect(onAuto).toHaveBeenCalledTimes(1)
    expect(inspectMock).not.toHaveBeenCalled()

    // A graph with no selection and no display node.
    const g = makeGraph()
    g.nodes.xb.display = false
    load(g)
    rerender(
      <div className="nodebuilder-root">
        <DataSheet open cookId="ck_1" window={WINDOW} />
      </div>,
    )
    expect(screen.getByTestId('nb-sheet-notarget').textContent).toContain('Select a node or wire')
    expect(inspectMock).not.toHaveBeenCalled()
  })

  it('shows the stale bar and runs the preview cook from it', async () => {
    load()
    selectNode('rsi')
    const onCook = vi.fn()
    await renderSheet({ stale: true, onCook })
    await waitForRows()
    const bar = screen.getByTestId('nb-sheet-stale')
    fireEvent.click(within(bar).getByRole('button'))
    expect(onCook).toHaveBeenCalledTimes(1)
  })

  it('a pinned target that is deleted falls back to follow mode', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    fireEvent.change(screen.getByTestId('nb-sheet-follow'), { target: { value: 'pinned' } })
    expect(useSheetUi.getState().follow).toBe('pinned')
    // A new selection does not move a pinned sheet.
    selectNode('xb')
    expect(screen.getByTestId('nb-sheet-target').textContent).toContain('node rsi')
    act(() => { useNodeBuilderStore.getState().removeNodes(['rsi']) })
    await waitFor(() => expect(useSheetUi.getState().follow).toBe('follow'))
    expect(useNodeBuilderStore.getState().flash?.text).toBe('Pinned target was deleted')
  })

  it('starts open (S28), is not rendered when closed, and persists its state under nb.sheet', async () => {
    load()
    selectNode('rsi')
    // Nothing stored: open by default (UX-16, S28 "default open at 240px").
    expect(useSheetUi.getState().open).toBe(true)
    render(<div className="nodebuilder-root"><DataSheet cookId="ck_1" window={WINDOW} /></div>)
    expect(screen.getByTestId('nb-sheet')).toBeTruthy()
    act(() => { useSheetUi.getState().toggle() })
    expect(screen.queryByTestId('nb-sheet')).toBeNull()
    expect(JSON.parse(window.localStorage.getItem(SHEET_STORAGE_KEY) ?? '{}')).toMatchObject({ open: false })
    act(() => { useSheetUi.getState().toggle() })
    expect(screen.getByTestId('nb-sheet')).toBeTruthy()
    const saved = JSON.parse(window.localStorage.getItem(SHEET_STORAGE_KEY) ?? '{}')
    expect(saved).toMatchObject({ open: true, height: 240, follow: 'follow' })
    // A stored closed state wins over the default.
    window.localStorage.setItem(SHEET_STORAGE_KEY, JSON.stringify({ ...saved, open: false }))
    act(() => { resetSheetUi() })
    expect(useSheetUi.getState().open).toBe(false)
  })

  it('arrow keys move the row focus and never reach the canvas', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const grid = screen.getByTestId('nb-sheet-grid')
    const docKey = vi.fn()
    document.addEventListener('keydown', docKey)
    fireEvent.keyDown(grid, { key: 'ArrowDown' })
    fireEvent.keyDown(grid, { key: 'ArrowDown' })
    document.removeEventListener('keydown', docKey)
    expect(docKey).not.toHaveBeenCalled()
    expect(screen.getByTestId('nb-sheet-row-1').className).toContain('nb-sheet__tr--focus')
  })
})

// ---------------------------------------------------------------------------
// W4 review fixes (DV-*, UX-*, BE-*)
// ---------------------------------------------------------------------------

function httpError(status: number, detail: unknown): AxiosError {
  return new AxiosError('err', 'ERR_BAD_REQUEST', undefined, undefined, {
    status, statusText: String(status), data: { detail }, headers: {}, config: { headers: {} },
  } as never)
}

describe('Data Sheet review fixes', () => {
  it('keeps the previous rows (dimmed) while a new target loads (DV-1)', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const before = screen.getByTestId('nb-sheet-row-0').textContent
    inspectMock.mockImplementation(() => new Promise(() => {}))
    selectNode('xb')
    await waitFor(() => expect(inspectMock).toHaveBeenCalled())
    const row = screen.getByTestId('nb-sheet-row-0')
    expect(row.textContent).not.toContain('…')
    expect(row.textContent).toBe(before)
    expect(screen.getByTestId('nb-sheet-grid').className).toContain('nb-sheet__scroll--dim')
  })

  it('drops a filter the server rejects as attr_unknown and loads the rows (DV-2)', async () => {
    load()
    selectNode('rsi')
    act(() => { useSheetUi.getState().setFilter('@gone > 3') })
    inspectMock.mockImplementation(async req => {
      if (req.filter?.attr === '@gone') throw httpError(422, { code: 'attr_unknown', message: '@gone is not on this stream' })
      return fakeInspect(req)
    })
    await renderSheet()
    await waitFor(() => expect(useSheetUi.getState().filter).toBe(''))
    await waitForRows()
    expect(screen.queryByTestId('nb-sheet-error')).toBeNull()
    expect(lastReq().filter).toBeNull()
  })

  it('a page from a different cook under the same key starts a new row map (DV-4)', () => {
    const rows = new Map<number, SheetRow>([[0, { time: 1, values: [1] }], [600, { time: 2, values: [2] }]])
    const prev = { key: 'n:rsi|ck_1|', targetKey: 'n:rsi', cookId: 'ck_1', columns: [], detail: [], readBy: null, total: 1256, stats: {}, rows, staleData: false } as SheetData
    const page = { ...fakeInspect({ cook_id: 'ck_2', graph: null, window: null, target: { node_id: 'rsi' }, attrs: null, offset: 0, limit: 10, around_time: null, filter: null }, 'ck_2') }
    const next = mergePage(prev, prev.key, 'n:rsi', page, 0)
    expect(next.cookId).toBe('ck_2')
    expect(next.rows.has(600)).toBe(false)
    expect(next.rows.size).toBe(10)
    // The same cook keeps the rows it had.
    const same = mergePage(prev, prev.key, 'n:rsi', { ...page, cook_id: 'ck_1' }, 0)
    expect(same.rows.has(600)).toBe(true)
  })

  it('the chip follows the kind: display xb, then node xb when it is selected (DV-9)', async () => {
    load()
    await renderSheet()
    expect(screen.getByTestId('nb-sheet-target').textContent).toContain('display xb')
    selectNode('xb')
    expect(screen.getByTestId('nb-sheet-target').textContent).toContain('node xb')
  })

  it('re-renders the times when the ET/local switch changes (DV-10)', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const cell = () => screen.getByTestId('nb-sheet-row-0').querySelector('.nb-sheet__td--time')!.textContent
    expect(cell()).toBe('2024-03-04 09:30')
    act(() => { setTimezone('local') })
    expect(cell()).toBe(formatSheetTime(T0))
    act(() => { setTimezone('ET') })
    expect(cell()).toBe('2024-03-04 09:30')
  })

  it('after a re-cook, a target change asks the new cook directly, with no second 410 (DV-11)', async () => {
    load()
    selectNode('rsi')
    inspectMock.mockReset()
    inspectMock.mockImplementation(async req => {
      if (req.cook_id === 'ck_1' && !req.graph) throw expired()
      return fakeInspect(req, 'ck_2')
    })
    await renderSheet()
    await waitForRows()
    expect(inspectMock).toHaveBeenCalledTimes(2)
    selectNode('xb')
    await waitFor(() => expect(inspectMock).toHaveBeenCalledTimes(3))
    expect(lastReq()).toMatchObject({ cook_id: 'ck_2', graph: null, window: null, target: { node_id: 'xb' } })
  })

  it('the expired banner says what to do when nothing can re-cook (DV-11)', async () => {
    load()
    selectNode('rsi')
    inspectMock.mockReset()
    inspectMock.mockImplementation(async () => { throw expired() })
    await renderSheet({ window: null })
    await screen.findByTestId('nb-sheet-error')
    const n = useNoticeStore.getState().notices.find(x => x.key === 'cook_expired')
    expect(n?.text).toBe(EXPIRED_NO_FALLBACK_TEXT)
    expect(n?.text).not.toContain('Cooking again')
  })

  it('clamps the drawer to the column and gives way below 700px (UX-06)', () => {
    expect(clampSheetHeight(300, 0)).toBe(300)
    expect(clampSheetHeight(600, 800)).toBe(480)
    expect(clampSheetHeight(240, 1000)).toBe(240)
    expect(clampSheetHeight(400, SHEET_SMALL_COLUMN - 1)).toBe(SHEET_MIN_HEIGHT)
  })

  it('the handle is a focusable separator with values, resized by the arrow keys (UX-06, UX-13)', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    const handle = screen.getByTestId('nb-split-handle-sheet')
    expect(handle.getAttribute('role')).toBe('separator')
    expect(handle.getAttribute('tabindex')).toBe('0')
    expect(handle.getAttribute('aria-valuenow')).toBe('240')
    expect(handle.getAttribute('aria-valuemin')).toBe(String(SHEET_MIN_HEIGHT))
    fireEvent.keyDown(handle, { key: 'ArrowUp' })
    expect(useSheetUi.getState().height).toBe(256)
    fireEvent.keyDown(handle, { key: 'ArrowDown' })
    fireEvent.keyDown(handle, { key: 'ArrowDown' })
    expect(useSheetUi.getState().height).toBe(224)
  })

  it('a read-only graph offers Edit this graph, not the cook buttons (UX-08)', () => {
    const g = { ...makeGraph(), readOnly: true }
    const onEdit = vi.fn()
    render(
      <div className="nodebuilder-root">
        <DataSheet open cookId={null} window={WINDOW} graph={g} autoCook onRunBacktest={vi.fn()} onAutoCookOn={vi.fn()} onEditGraph={onEdit} />
      </div>,
    )
    const msg = screen.getByTestId('nb-sheet-nocook')
    expect(msg.textContent).toContain('Edit this graph to cook its data.')
    expect(within(msg).queryByText('Run backtest')).toBeNull()
    expect(within(msg).queryByText('Auto cook on')).toBeNull()
    fireEvent.click(within(msg).getByText('Edit this graph'))
    expect(onEdit).toHaveBeenCalledTimes(1)
  })

  it('the no-cook copy fits the auto cook state (UX-09)', () => {
    const base = { readOnly: false, autoCook: true, previewPhase: 'idle', blockedByErrors: false, errorCount: 0, hasTicker: true }
    expect(noCookMessage({ ...base, autoCook: false })).toContain('turn on auto cook')
    expect(noCookMessage(base)).toBe('loading rows…')
    expect(noCookMessage({ ...base, previewPhase: 'cooking' })).toBe('loading rows…')
    expect(noCookMessage({ ...base, blockedByErrors: true, errorCount: 2 })).toBe('Fix the errors to cook (2 errors).')
    expect(noCookMessage({ ...base, blockedByErrors: true, errorCount: 1 })).toBe('Fix the errors to cook (1 error).')
    expect(noCookMessage({ ...base, hasTicker: false })).toBe('Add a Ticker node to cook.')
    expect(noCookMessage({ ...base, previewPhase: 'failed' })).toContain('Auto cook failed')
    for (const m of [noCookMessage(base), noCookMessage({ ...base, hasTicker: false })]) expect(m).not.toContain('turn on auto cook')
    load()
    render(<div className="nodebuilder-root"><DataSheet open cookId={null} window={WINDOW} autoCook /></div>)
    expect(screen.getByTestId('nb-sheet-nocook').textContent).toBe('loading rows…')
  })

  it('rows expose the focused row to assistive tech (UX-13)', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const grid = screen.getByTestId('nb-sheet-grid')
    fireEvent.keyDown(grid, { key: 'ArrowDown' })
    fireEvent.keyDown(grid, { key: 'ArrowDown' })
    expect(grid.getAttribute('aria-activedescendant')).toBe('nb-sheet-row-1')
    expect(screen.getByTestId('nb-sheet-row-1').getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('nb-sheet-row-0').getAttribute('aria-selected')).toBe('false')
  })

  it('the header menu opens from the keyboard and the stats are in the header (UX-13)', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    const th = screen.getByTestId('nb-sheet-col-@rsi')
    expect(th.getAttribute('tabindex')).toBe('0')
    expect(th.textContent).toContain('min 20.0 · max 80.0 · 14 nan')
    fireEvent.keyDown(th, { key: 'F10', shiftKey: true })
    const menu = screen.getByTestId('nb-sheet-menu')
    expect(document.activeElement).toBe(within(menu).getAllByRole('menuitem')[0])
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('nb-sheet-menu')).toBeNull()
    fireEvent.keyDown(screen.getByTestId('nb-sheet-col-@xb_rsi'), { key: 'ContextMenu' })
    expect(screen.getByTestId('nb-sheet-menu').textContent).toContain('Filter: only true')
    expect(headerStatsText({ name: '@x', dtype: 'bool', written_by: null }, { true_count: 31 }, 1256)).toBe('2.5 % true')
    expect(headerStatsText({ name: '@x', dtype: 'float', written_by: null }, undefined, 10)).toBe('')
  })

  it('a cook the server did not keep is paged with graph and window (BE-2)', async () => {
    load()
    selectNode('rsi')
    inspectMock.mockImplementation(async req => ({ ...fakeInspect(req, 'ck_big'), kept: false }))
    await renderSheet()
    await waitForRows()
    expect(inspectMock.mock.calls[0][0]).toMatchObject({ cook_id: 'ck_1', graph: null })
    const grid = screen.getByTestId('nb-sheet-grid')
    act(() => { grid.scrollTop = 900 * 28; fireEvent.scroll(grid) })
    await waitFor(() => expect(inspectMock).toHaveBeenCalledTimes(2))
    expect(lastReq().cook_id).toBe('ck_big')
    expect(lastReq().window).toEqual(WINDOW)
    expect(lastReq().graph?.nodes.rsi).toBeDefined()
  })

  it('shows a note when the server answered from the last good cook (BE-3)', async () => {
    load()
    selectNode('rsi')
    await renderSheet()
    await waitForRows()
    expect(screen.queryByTestId('nb-sheet-stale-data')).toBeNull()
    cleanup()
    inspectMock.mockImplementation(async req => ({ ...fakeInspect(req), stale_data: true }))
    await renderSheet()
    await waitForRows()
    expect(screen.getByTestId('nb-sheet-stale-data').textContent).toBe(SHEET_STALE_DATA_TEXT)
  })

  it('a 502 data_unavailable shows the server message with Retry (BE-4)', async () => {
    load()
    selectNode('rsi')
    inspectMock.mockImplementation(async () => {
      throw httpError(502, { code: 'data_unavailable', message: 'Could not fetch AAPL (5m) from yahoo.' })
    })
    await renderSheet()
    const err = await screen.findByTestId('nb-sheet-error')
    expect(err.textContent).toContain('The market data could not be loaded: Could not fetch AAPL (5m) from yahoo.')
    expect(inspectMock).toHaveBeenCalledTimes(1)
    inspectMock.mockImplementation(async req => fakeInspect(req))
    fireEvent.click(within(err).getByText('Retry'))
    await waitForRows()
  })
})
