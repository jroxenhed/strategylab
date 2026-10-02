/**
 * Attribute picker (F435 W2 item 2.E, spec S09) and the ParamRow dispatch
 * that picks it from the catalog ParamSpec.
 *
 * The picker lists the node's input stream from the last /validate answer
 * (put in place with `setStreams`; no request is made), filters by typing,
 * allows free text, and commits through the store.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act, fireEvent, within } from '@testing-library/react'
import { emptyGraph, type AttrInfo, type Graph, type GraphNode, type GraphWire, type StreamSchema } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics, setServerDiagnostics, setStreams } from '../useDiagnostics'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { AttrPicker, attrOptions, dtypeMismatch, dtypeTag, toggleListName } from '../nodes/AttrPicker'
import { ParamRows, rowsFor, streamWidgetFor } from '../nodes/ParamRow'
import { paramSpecsOf, type InputAttr } from '../streamLabels'
import { renameAttr } from '../renameAttr'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

function node(id: string, type: string, params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false }
}
function wire(id: string, from: string, to: string, to_port = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port }
}
function attr(name: string, written_by: string, dtype: AttrInfo['dtype'] = 'float'): AttrInfo {
  return { name, dtype, written_by }
}
function stream(points: AttrInfo[], detail: AttrInfo[] = []): StreamSchema {
  return { stream_schema: 1, points, detail, prims: [] }
}
const OHLCV = ['@open', '@high', '@low', '@close', '@volume'].map(n => attr(n, 'aapl'))

function load(nodes: GraphNode[], wires: GraphWire[]) {
  const g: Graph = { ...emptyGraph(), nodes: Object.fromEntries(nodes.map(n => [n.id, n])), wires }
  act(() => { useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' }) })
}

const params = (id: string) => useNodeBuilderStore.getState().graph!.nodes[id].params

/** A picker that follows the store, like a node does. */
function Picker({ nodeId, param }: { nodeId: string; param: string }) {
  const value = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.params[param])
  const type = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.type)
  const spec = paramSpecsOf(type).find(p => p.name === param)!
  return <AttrPicker nodeId={nodeId} spec={spec} value={value} />
}

beforeEach(() => {
  resetDiagnostics()
  load(
    [node('aapl', 'ticker'), node('ind', 't2_ind', { period: 14, source: null, out: '@t2' })],
    [wire('w1', 'aapl', 'ind')],
  )
  act(() => setStreams({ aapl: stream(OHLCV) }))
})

afterEach(() => {
  cleanup()
  resetDiagnostics()
})

describe('pure helpers', () => {
  const attrs: InputAttr[] = [
    ...OHLCV.map(a => ({ ...a, detail: false, port: 'in0' })),
    { ...attr('@xb', 'xb', 'bool'), detail: false, port: 'in1' },
  ]

  it('filters by name substring and by writer prefix, grouped by writer', () => {
    expect(attrOptions(attrs, 'clo').flat.map(a => a.name)).toEqual(['@close'])
    expect(attrOptions(attrs, '').groups.map(g => [g.writer, g.attrs.length])).toEqual([['aapl', 5], ['xb', 1]])
    expect(attrOptions(attrs, 'aa', id => id).flat).toHaveLength(5)
  })

  it('offers a free-text row for a valid name no attribute has exactly (FP-4)', () => {
    expect(attrOptions(attrs, 'foo').free).toBe('@foo')
    expect(attrOptions(attrs, '@foo').free).toBe('@foo')
    expect(attrOptions(attrs, '@close').free).toBeNull()
    // A partial name narrows the list AND can still be used as typed.
    expect(attrOptions(attrs, 'clo').free).toBe('@clo')
    expect(attrOptions(attrs, 'Bad Name').free).toBeNull()
  })

  it('a short name that only prefixes others can still be entered (FP-4)', () => {
    const more: InputAttr[] = [
      { ...attr('@rsi_2', 'r2'), detail: false, port: 'in0' },
      { ...attr('@rsi_slope', 'r2'), detail: false, port: 'in0' },
    ]
    const o = attrOptions(more, '@rsi')
    expect(o.flat.map(a => a.name)).toEqual(['@rsi_2', '@rsi_slope'])
    expect(o.free).toBe('@rsi')
  })

  it('type tags and mismatch', () => {
    expect(dtypeTag({ dtype: 'float', detail: false })).toBe('f')
    expect(dtypeTag({ dtype: 'bool', detail: false })).toBe('b')
    expect(dtypeTag({ dtype: 'float', detail: true })).toBe('detail')
    expect(dtypeMismatch('float', { dtype: 'bool', detail: false })).toBe(true)
    expect(dtypeMismatch('bool', { dtype: 'float', detail: false })).toBe(true)
    expect(dtypeMismatch('float', { dtype: 'float', detail: false })).toBe(false)
    expect(dtypeMismatch('any', { dtype: 'bool', detail: false })).toBe(false)
  })

  it('a list pick adds, a second pick takes it out', () => {
    expect(toggleListName(['@a'], '@b')).toEqual(['@a', '@b'])
    expect(toggleListName(['@a', '@b'], '@a')).toEqual(['@b'])
  })
})

describe('AttrPicker (single attr)', () => {
  it('unset and required shows "pick"; the popover lists the stream grouped by writer', () => {
    render(<Picker nodeId="ind" param="source" />)
    const chip = screen.getByTestId('nb-attr-chip-ind-source')
    expect(chip.textContent).toContain('pick')
    expect(chip.className).toContain('nb-attr-chip--required')
    act(() => { fireEvent.click(chip) })
    const pop = screen.getByTestId('nb-attr-popover')
    expect(within(pop).getByRole('group', { name: 'aapl' })).toBeInTheDocument()
    expect(within(pop).getAllByRole('option')).toHaveLength(5)
  })

  it('typing filters and Enter commits through the store, then closes', () => {
    render(<Picker nodeId="ind" param="source" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
    const before = useNodeBuilderStore.getState().past.length
    const search = screen.getByTestId('nb-attr-search')
    act(() => { fireEvent.change(search, { target: { value: 'clo' } }) })
    // @close, then the free row last, so Enter still picks the real match.
    expect(screen.getAllByRole('option').map(o => o.getAttribute('data-testid')))
      .toEqual(['nb-attr-option-@close', 'nb-attr-free'])
    act(() => { fireEvent.keyDown(search, { key: 'Enter' }) })
    expect(params('ind').source).toBe('@close')
    expect(useNodeBuilderStore.getState().past.length).toBe(before + 1)
    expect(screen.queryByTestId('nb-attr-popover')).toBeNull()
    const chip = screen.getByTestId('nb-attr-chip-ind-source')
    expect(chip.textContent).toContain('@close')
    expect(chip.getAttribute('aria-label')).toBe('source: @close')
  })

  it('arrow keys move the active row', () => {
    render(<Picker nodeId="ind" param="source" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
    const search = screen.getByTestId('nb-attr-search')
    act(() => { fireEvent.keyDown(search, { key: 'ArrowDown' }) })
    act(() => { fireEvent.keyDown(search, { key: 'ArrowDown' }) })
    act(() => { fireEvent.keyDown(search, { key: 'Enter' }) })
    expect(params('ind').source).toBe('@low')
  })

  it('free text: Use @foo, stored as typed and flagged as missing', () => {
    render(<Picker nodeId="ind" param="source" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
    act(() => { fireEvent.change(screen.getByTestId('nb-attr-search'), { target: { value: '@foo' } }) })
    const free = screen.getByTestId('nb-attr-free')
    expect(free.textContent).toContain('Use @foo')
    expect(free.textContent).toContain('not on the input yet')
    act(() => { fireEvent.click(free) })
    expect(params('ind').source).toBe('@foo')
    const chip = screen.getByTestId('nb-attr-chip-ind-source')
    expect(chip.getAttribute('aria-invalid')).toBe('true')
    expect(chip.title).toBe('@foo is not present on the input')
  })

  it('a bool attribute offered to a float param is dimmed with the float tooltip', () => {
    act(() => setStreams({ aapl: stream([...OHLCV, attr('@xb', 'aapl', 'bool')]) }))
    render(<Picker nodeId="ind" param="source" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
    const row = screen.getByTestId('nb-attr-option-@xb')
    expect(row.className).toContain('nb-attr-pop__row--dim')
    expect(row.title).toBe('This param needs a float')
    expect(screen.getByTestId('nb-attr-option-@close').className).not.toContain('--dim')
  })

  it('shows the empty-input message with no wires, and free text still works', () => {
    load([node('ind', 't2_ind', { source: null, out: '@t2' })], [])
    render(<Picker nodeId="ind" param="source" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
    expect(screen.getByText('Nothing flows in yet. Wire an input first.')).toBeInTheDocument()
    act(() => { fireEvent.change(screen.getByTestId('nb-attr-search'), { target: { value: 'x' } }) })
    expect(screen.getByTestId('nb-attr-free')).toBeInTheDocument()
  })

  it('an optional unset param shows "none" and can be cleared again', () => {
    load(
      [node('aapl', 'ticker'), node('x', 't2_cmp', { a: '@close', b: '@open', out: '@x' })],
      [wire('w1', 'aapl', 'x')],
    )
    render(<Picker nodeId="x" param="b" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-x-b')) })
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-none')) })
    expect(params('x').b).toBeNull()
    expect(screen.getByTestId('nb-attr-chip-x-b').textContent).toContain('none')
  })

  it('open + Enter on an optional param with a value keeps the value (FP-5)', () => {
    load(
      [node('aapl', 'ticker'), node('x', 't2_cmp', { a: '@close', b: '@open', out: '@x' })],
      [wire('w1', 'aapl', 'x')],
    )
    act(() => setStreams({ aapl: stream(OHLCV) }))
    render(<Picker nodeId="x" param="b" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-x-b')) })
    const search = screen.getByTestId('nb-attr-search')
    // The highlighted row is the current value, not `none`.
    const activeId = search.getAttribute('aria-activedescendant')!
    expect(document.getElementById(activeId)?.getAttribute('data-testid')).toBe('nb-attr-option-@open')
    const before = useNodeBuilderStore.getState().past.length
    act(() => { fireEvent.keyDown(search, { key: 'Enter' }) })
    expect(params('x').b).toBe('@open')
    expect(useNodeBuilderStore.getState().past.length).toBe(before)
    expect(screen.queryByTestId('nb-attr-popover')).toBeNull()
  })

  it('after an upstream rename, the renamed read is not flagged before /validate answers (FP-3)', () => {
    load(
      [
        node('aapl', 'ticker'),
        node('rsi', 't2_ind', { source: '@close', out: '@rsi' }),
        node('x', 't2_cmp', { a: '@rsi', out: '@x' }),
      ],
      [wire('w1', 'aapl', 'rsi'), wire('w2', 'rsi', 'x')],
    )
    const old = { aapl: stream(OHLCV), rsi: stream([...OHLCV, attr('@rsi', 'rsi')]) }
    act(() => setStreams(old))
    render(<Picker nodeId="x" param="a" />)
    act(() => { useNodeBuilderStore.getState().commit('rename @rsi to @rsi14', g => renameAttr(g, 'rsi', 'out', '@rsi14')) })
    const chip = screen.getByTestId('nb-attr-chip-x-a')
    expect(chip.textContent).toContain('@rsi14')
    expect(chip.getAttribute('aria-invalid')).toBeNull()
    expect(chip.className).not.toContain('nb-chip--missing')
    // Had the old streams been the answer for this graph, it would be missing.
    act(() => setStreams(old))
    expect(screen.getByTestId('nb-attr-chip-x-a').getAttribute('aria-invalid')).toBe('true')
  })

  it('a server attr_clash on the read param shows as an error with its message (FP-6)', () => {
    act(() => { useNodeBuilderStore.getState().updateNodeParams('ind', { source: '@close' }) })
    const d: Diagnostic = {
      node_id: 'ind', path: null, severity: 'error', code: 'attr_clash', message: 'ind reads @close, but @close comes from a and b',
      param: 'source', port: null, line: null, col: null, end_line: null, end_col: null,
    }
    act(() => setServerDiagnostics([d]))
    render(<Picker nodeId="ind" param="source" />)
    const chip = screen.getByTestId('nb-attr-chip-ind-source')
    expect(chip.className).toContain('nb-attr-chip--clash')
    expect(chip.getAttribute('aria-invalid')).toBe('true')
    expect(chip.title).toBe(d.message)
  })

  it('clicks in the portaled popover do not reach the node card behind it (FP-2)', () => {
    // Stands in for React Flow's NodeWrapper, whose click selects the host node.
    const hostClick = vi.fn(() => useNodeBuilderStore.getState().select('ind'))
    render(<div onClick={hostClick}><Picker nodeId="ind" param="source" /></div>)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
    hostClick.mockClear()
    const pop = screen.getByTestId('nb-attr-popover')
    // The writer header selects the writer and keeps the popover open.
    act(() => { fireEvent.click(within(pop).getByText('aapl')) })
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('aapl')
    expect(screen.getByTestId('nb-attr-popover')).toBeInTheDocument()
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-option-@close')) })
    expect(hostClick).not.toHaveBeenCalled()
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('aapl')
  })

  it('Tab closes and moves focus to the next param row in the node (FP-11)', () => {
    load(
      [node('aapl', 'ticker'), node('x', 't2_cmp', { a: '@close', b: null, out: '@x' })],
      [wire('w1', 'aapl', 'x')],
    )
    act(() => setStreams({ aapl: stream(OHLCV) }))
    render(
      <div className="react-flow__node">
        <Picker nodeId="x" param="a" />
        <Picker nodeId="x" param="b" />
      </div>,
    )
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-x-a')) })
    const search = screen.getByTestId('nb-attr-search')
    const tab = fireEvent.keyDown(search, { key: 'Tab' })
    expect(tab).toBe(false) // default prevented: the browser does not move focus out of the canvas
    act(() => {})
    expect(screen.queryByTestId('nb-attr-popover')).toBeNull()
    expect(document.activeElement).toBe(screen.getByTestId('nb-attr-chip-x-b'))
    // Shift+Tab from the first row has nowhere to go in the node: the chip itself.
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-x-a')) })
    act(() => { fireEvent.keyDown(screen.getByTestId('nb-attr-search'), { key: 'Tab', shiftKey: true }) })
    expect(document.activeElement).toBe(screen.getByTestId('nb-attr-chip-x-a'))
  })

  it('a server attr_type diagnostic turns the chip amber with its message', () => {
    act(() => { useNodeBuilderStore.getState().updateNodeParams('ind', { source: '@close' }) })
    const d: Diagnostic = {
      node_id: 'ind', path: null, severity: 'warning', code: 'attr_type', message: 'source needs a float',
      param: 'source', port: null, line: null, col: null, end_line: null, end_col: null,
    }
    act(() => setServerDiagnostics([d]))
    render(<Picker nodeId="ind" param="source" />)
    const chip = screen.getByTestId('nb-attr-chip-ind-source')
    expect(chip.className).toContain('nb-attr-chip--type')
    expect(chip.title).toBe('source needs a float')
  })

  it('keys typed in the search never reach the canvas', () => {
    const onKey = vi.fn()
    document.addEventListener('keydown', onKey)
    try {
      render(<Picker nodeId="ind" param="source" />)
      act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-ind-source')) })
      act(() => { fireEvent.keyDown(screen.getByTestId('nb-attr-search'), { key: 'Delete' }) })
      expect(onKey).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', onKey)
    }
  })
})

describe('AttrPicker (attr_list)', () => {
  beforeEach(() => {
    load(
      [node('c1', 't2_cmp', { a: '@close', out: '@c1' }), node('c2', 't2_cmp', { a: '@close', out: '@c2' }), node('all', 't2_and', { terms: ['@c1'], out: '@all' })],
      [wire('w1', 'c1', 'all', 'in0'), wire('w2', 'c2', 'all', 'in1')],
    )
    act(() => setStreams({ c1: stream([attr('@c1', 'c1', 'bool')]), c2: stream([attr('@c2', 'c2', 'bool')]) }))
  })

  it('adds with the + chip and keeps the popover open for more', () => {
    render(<Picker nodeId="all" param="terms" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-all-terms')) })
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-option-@c2')) })
    expect(params('all').terms).toEqual(['@c1', '@c2'])
    expect(screen.getByTestId('nb-attr-popover')).toBeInTheDocument()
  })

  it('removes with the chip ✕ and with Backspace in an empty search', () => {
    act(() => { useNodeBuilderStore.getState().updateNodeParams('all', { terms: ['@c1', '@c2'] }) })
    render(<Picker nodeId="all" param="terms" />)
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-all-terms-remove-0')) })
    expect(params('all').terms).toEqual(['@c2'])
    act(() => { fireEvent.click(screen.getByTestId('nb-attr-chip-all-terms')) })
    act(() => { fireEvent.keyDown(screen.getByTestId('nb-attr-search'), { key: 'Backspace' }) })
    expect(params('all').terms).toEqual([])
  })
})

describe('ParamRow dispatch from ParamSpec', () => {
  it('chooses the widget by spec type, never by node type', () => {
    const specs = paramSpecsOf('t2_ind')
    expect(streamWidgetFor(specs.find(p => p.name === 'source'), null)).toBe('attr')
    expect(streamWidgetFor(specs.find(p => p.name === 'out'), '@t2')).toBe('write')
    expect(streamWidgetFor(specs.find(p => p.name === 'period'), 14)).toBeNull()
    expect(streamWidgetFor(paramSpecsOf('t2_tod')[0], null)).toBe('time_range')
    expect(streamWidgetFor(paramSpecsOf('t2_dow')[0], ['mon'])).toBe('days')
    expect(streamWidgetFor(paramSpecsOf('rsi').find(p => p.name === 'type'), 'wilder')).toBeNull()
  })

  it('rows follow the catalog order; write params are left to the chip row', () => {
    const rows = rowsFor({ period: 14, out: '@t2', extra: 1 }, paramSpecsOf('t2_ind'), false)
    expect(rows.map(r => r.key)).toEqual(['period', 'source', 'extra'])
    expect(rowsFor({ out: '@t2' }, paramSpecsOf('t2_ind'), true).map(r => r.key)).toEqual(['source', 'out'])
  })

  it('ParamRows draws the picker for an attr param of a node in the store', () => {
    render(<ParamRows nodeId="ind" params={params('ind') as Record<string, unknown>} />)
    expect(screen.getByTestId('nb-param-ind-source').getAttribute('data-param-kind')).toBe('attr')
    expect(screen.getByTestId('nb-attr-chip-ind-source')).toBeInTheDocument()
    expect(screen.getByTestId('nb-param-ind-period').getAttribute('inputmode')).toBe('decimal')
    expect(screen.queryByTestId('nb-param-ind-out')).toBeNull()
  })
})
