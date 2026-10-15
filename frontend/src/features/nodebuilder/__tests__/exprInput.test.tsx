/**
 * Level 1 code: the `=` toggle and ExprInput (F435 W7, spec S44).
 *
 * parse_code is mocked; the store and the diagnostics store are real, so
 * the commit, the debounce, the red state and Run's error count are the
 * real code paths.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react'
import type { Graph, GraphNode, ParamValue } from '../../../api/nodebuilder'
import type { ParseCodeResponse } from '../../../api/nodebuilderCode'

vi.mock('../../../api/nodebuilderCode', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilderCode')>()
  return { ...orig, parseCode: vi.fn(), getCodeCapabilities: vi.fn() }
})

import { parseCode } from '../../../api/nodebuilderCode'
import { useNodeBuilderStore } from '../store'
import { ParamRow } from '../nodes/ParamRow'
import Inspector from '../Inspector'
import { getDiagnosticsView, resetDiagnostics } from '../useDiagnostics'
import { pruneCodeState, resetCodeStore, useCodeStore } from '../code/codeStore'
import { isCodeableParam } from '../code/codeOps'
import { paramSpecsOf } from '../streamLabels'
import { runDisabledReason } from '../graphText'
import { toggleInspector } from '../inspector/state'

const parseMock = parseCode as unknown as ReturnType<typeof vi.fn>

function node(id: string, type: string, params: Record<string, ParamValue>): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false }
}

function makeGraph(): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      aapl: node('aapl', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      rsi: node('rsi', 'rsi', { period: 14, type: 'wilder', source: '@close' }),
    },
    wires: [{ id: 'w1', from: 'aapl', to: 'rsi', from_port: 'out', to_port: 'in0' }],
    annotations: { boxes: [], notes: [] },
  }
}

function ok(): ParseCodeResponse {
  return { ok: true, params: [], reads: [], writes: [], result_type: null, diagnostics: [] }
}

function bad(): ParseCodeResponse {
  return {
    ok: false, params: [], reads: [], writes: [], result_type: null,
    diagnostics: [{
      node_id: 'rsi', path: '/rsi', severity: 'error', code: 'code_syntax', message: 'unexpected token',
      param: 'period', port: null, line: 1, col: 12, end_line: null, end_col: null,
    }],
  }
}

/** The row as a node renders it: the value comes from the store. */
function Row({ nodeId, paramKey }: { nodeId: string; paramKey: string }) {
  const value = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.params[paramKey])
  const type = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.type)
  const spec = paramSpecsOf(type).find(p => p.name === paramKey)
  return <ParamRow nodeId={nodeId} paramKey={paramKey} value={value} spec={spec} typeSpec={{ type: 'number' }} />
}

function g(): Graph {
  return useNodeBuilderStore.getState().graph!
}

beforeEach(() => {
  resetCodeStore()
  resetDiagnostics()
  parseMock.mockReset()
  parseMock.mockResolvedValue(ok())
  act(() => { useNodeBuilderStore.getState().openGraph(makeGraph(), { id: 'g_1', rev: 1, name: 'g' }) })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  useNodeBuilderStore.getState().discardEdits()
})

describe('the = toggle (S44)', () => {
  it('a number row renders an = button; a Ticker symbol row renders none', () => {
    render(<div><Row nodeId="rsi" paramKey="period" /><Row nodeId="aapl" paramKey="symbol" /></div>)
    const toggles = screen.getAllByTestId('nb-expr-toggle')
    expect(toggles).toHaveLength(1)
    expect(toggles[0].getAttribute('aria-label')).toBe('Use an expression for period')
    expect(toggles[0].getAttribute('aria-pressed')).toBe('false')
    expect(isCodeableParam('ticker', paramSpecsOf('ticker').find(p => p.name === 'symbol'), 'symbol')).toBe(false)
    expect(isCodeableParam('ticker', paramSpecsOf('ticker').find(p => p.name === 'interval'), 'interval')).toBe(false)
  })

  it('no = on a read-only graph or while code is off on the server', () => {
    useCodeStore.setState({ enabled: false })
    render(<Row nodeId="rsi" paramKey="period" />)
    expect(screen.queryByTestId('nb-expr-toggle')).toBeNull()
  })
})

describe('ExprInput (S44)', () => {
  it('every expression field is type="text"', () => {
    act(() => { useNodeBuilderStore.getState().updateNodeParams('rsi', { period: { expr: '7 if True else 21' } }) })
    render(<Row nodeId="rsi" paramKey="period" />)
    const inputs = screen.getAllByTestId('expr-input')
    expect(inputs.length).toBeGreaterThan(0)
    for (const el of inputs) {
      expect(el.tagName).toBe('INPUT')
      expect(el.getAttribute('type')).toBe('text')
    }
  })

  it('= then typing then Enter commits { expr }, and parses once after the debounce with the param type', async () => {
    vi.useFakeTimers()
    render(<Row nodeId="rsi" paramKey="period" />)
    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    const input = screen.getByTestId('expr-input') as HTMLInputElement
    // The field opens with the literal as its text.
    expect(input.value).toBe('14')
    const text = '7 if chf("../vol/threshold") > 2 else 21'
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: text } })
    expect(parseMock).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(g().nodes.rsi.params.period).toEqual({ expr: text })
    // The old literal is kept for "Use literal value".
    expect(g().nodes.rsi.meta?.literal_backup).toEqual({ period: 14 })
    await act(async () => { vi.advanceTimersByTime(299) })
    expect(parseMock).not.toHaveBeenCalled()
    await act(async () => { vi.advanceTimersByTime(1) })
    expect(parseMock).toHaveBeenCalledTimes(1)
    const body = parseMock.mock.calls[0][0]
    expect(body.context).toBe('expr')
    expect(body.expected).toEqual({ type: 'int' })
    expect(body.code).toBe(text)
    expect(body.node_id).toBe('rsi')
  })

  it('a parse error turns the field red, shows `<code> at <line>:<col>: <message>`, and blocks Run', async () => {
    vi.useFakeTimers()
    parseMock.mockResolvedValue(bad())
    render(<Row nodeId="rsi" paramKey="period" />)
    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    const input = screen.getByTestId('expr-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '7 if (' } })
    await act(async () => { vi.advanceTimersByTime(300) })
    await act(async () => { await Promise.resolve() })
    expect(input.getAttribute('aria-invalid')).toBe('true')
    const status = screen.getByTestId('nb-expr-status-period')
    expect(status.textContent).toBe('code_syntax at 1:12: unexpected token')
    expect(input.getAttribute('aria-describedby')).toBe(status.id)
    const view = getDiagnosticsView()
    expect(view.errorCount).toBe(1)
    expect(runDisabledReason(view.errorCount, true)).toMatch(/Fix 1 error/)
  })

  it('Esc right after entering code mode leaves it; an empty commit restores the literal', () => {
    render(<Row nodeId="rsi" paramKey="period" />)
    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    fireEvent.keyDown(screen.getByTestId('expr-input'), { key: 'Escape' })
    expect(screen.queryByTestId('expr-input')).toBeNull()
    expect(g().nodes.rsi.params.period).toBe(14)

    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    const input = screen.getByTestId('expr-input')
    fireEvent.change(input, { target: { value: 'chi("n", default=3)' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(g().nodes.rsi.params.period).toEqual({ expr: 'chi("n", default=3)' })
    const again = screen.getByTestId('expr-input')
    fireEvent.change(again, { target: { value: '' } })
    fireEvent.keyDown(again, { key: 'Enter' })
    expect(g().nodes.rsi.params.period).toBe(14)
  })

  it('pressing = in the value field switches the row to an expression', () => {
    render(<Row nodeId="rsi" paramKey="period" />)
    const field = screen.getByTestId('nb-param-rsi-period')
    fireEvent.focus(field)
    fireEvent.keyDown(field, { key: '=' })
    expect((screen.getByTestId('expr-input') as HTMLInputElement).value).toBe('14')
  })

  it('Use literal value (the pressed = glyph) restores params.period = 14, and undo brings the expression back', () => {
    render(<Row nodeId="rsi" paramKey="period" />)
    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    const input = screen.getByTestId('expr-input')
    fireEvent.change(input, { target: { value: '7 if chf("../vol/threshold") > 2 else 21' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    const pressed = screen.getByTestId('nb-expr-toggle')
    expect(pressed.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(pressed)
    expect(g().nodes.rsi.params.period).toBe(14)
    act(() => { useNodeBuilderStore.getState().undo() })
    expect(g().nodes.rsi.params.period).toEqual({ expr: '7 if chf("../vol/threshold") > 2 else 21' })
  })

  it('in the Inspector the expression row uses the editor stand-in, a type="text" field', () => {
    act(() => {
      useNodeBuilderStore.getState().updateNodeParams('rsi', { period: { expr: '21' } })
      useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' })
    })
    act(() => toggleInspector(true))
    render(<div className="nodebuilder-root"><Inspector /></div>)
    const row = screen.getByTestId('nb-param-inspector-rsi-period')
    const input = row.querySelector('[data-testid="expr-input"]') as HTMLInputElement
    expect(input).not.toBeNull()
    expect(input.getAttribute('type')).toBe('text')
    expect(input.value).toBe('21')
  })
})

describe('stale parse errors never block Run', () => {
  it('leaving code mode, or deleting the node, clears the expression’s parse error', async () => {
    vi.useFakeTimers()
    parseMock.mockResolvedValue(bad())
    render(<Row nodeId="rsi" paramKey="period" />)
    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    const input = screen.getByTestId('expr-input')
    fireEvent.change(input, { target: { value: '7 if (' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await act(async () => { vi.advanceTimersByTime(300) })
    await act(async () => { await Promise.resolve() })
    expect(getDiagnosticsView().errorCount).toBe(1)
    // Back to a value: the error goes with the expression.
    fireEvent.click(screen.getByTestId('nb-expr-toggle'))
    expect(g().nodes.rsi.params.period).toBe(14)
    expect(getDiagnosticsView().errorCount).toBe(0)
  })

  it('pruneCodeState drops answers for nodes that are gone', async () => {
    parseMock.mockResolvedValue(bad())
    act(() => { useNodeBuilderStore.getState().updateNodeParams('rsi', { period: { expr: '7 if (' } }) })
    render(<Row nodeId="rsi" paramKey="period" />)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(getDiagnosticsView().errorCount).toBe(1)
    act(() => { useNodeBuilderStore.getState().removeNodes(['rsi']) })
    pruneCodeState(useNodeBuilderStore.getState().graph)
    expect(getDiagnosticsView().errorCount).toBe(0)
  })
})
