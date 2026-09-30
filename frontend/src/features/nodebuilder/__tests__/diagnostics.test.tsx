/**
 * Diagnostics tests (F435 W1 item 1.G, spec S05).
 *
 * The validate client is mocked; nothing reaches a server. Covers:
 * - an unconnected Entry: badge on the Entry node, one error, red border;
 * - typing `abc` into a number row: aria-invalid at once, no request;
 * - one commit: exactly one /validate after 300 ms (fake timers);
 *   two commits 100 ms apart: one call;
 * - a newer commit cancels the request in flight and its late answer is
 *   dropped; the last result stays while pending; a failed request keeps
 *   the last result and sets `offline`;
 * - selection and viewport changes never validate;
 * - setServerDiagnostics holds until the next commit;
 * - the popover lists rows grouped by node and a row click selects.
 */

import { describe, it, expect, beforeEach, afterEach, beforeAll, vi } from 'vitest'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { ReactFlow, type Node, type NodeTypes } from '@xyflow/react'
import { useNodeBuilderStore } from '../store'
import {
  useDiagnostics,
  useDiagnosticsController,
  setServerDiagnostics,
  resetDiagnostics,
  VALIDATE_DEBOUNCE_MS,
  type DiagnosticsState,
} from '../useDiagnostics'
import { DiagnosticsPopover } from '../DiagnosticsPopover'
import { ParamRow } from '../nodes/ParamRow'
import OutputNode from '../nodes/OutputNode'
import { validateGraph, type Diagnostic, type ValidateResponse } from '../../../api/nodebuilderValidate'
import type { Graph } from '../../../api/nodebuilder'

vi.mock('../../../api/nodebuilderValidate', async importOriginal => {
  const real = await importOriginal<typeof import('../../../api/nodebuilderValidate')>()
  return { ...real, validateGraph: vi.fn() }
})

const validateMock = vi.mocked(validateGraph)

beforeAll(() => {
  // jsdom has no DOMMatrixReadOnly; React Flow reads it when measuring nodes.
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor(_t?: string) {}
    }
  }
})

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function diag(partial: Partial<Diagnostic> & Pick<Diagnostic, 'message'>): Diagnostic {
  return {
    node_id: null,
    path: null,
    severity: 'error',
    code: 'missing_input',
    param: null,
    port: null,
    line: null,
    col: null,
    end_line: null,
    end_col: null,
    ...partial,
  }
}

const ENTRY_MSG = 'Entry is not connected.'
const ENTRY_DIAG = diag({ node_id: 'n_entry', path: '/entry', code: 'missing_input', message: ENTRY_MSG })

/** A ticker, an RSI and an Entry with nothing wired into it. */
function graphWithLooseEntry(): Graph {
  return {
    _version: 2,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      n_tick: { id: 'n_tick', type: 'ticker', name: 'ticker', parent: null, params: { symbol: 'AAPL', interval: '1d' }, position: [0, 0], display: false, bypass: false },
      n_rsi: { id: 'n_rsi', type: 'rsi', name: 'rsi', parent: null, params: { period: 14 }, position: [0, 100], display: false, bypass: false },
      n_entry: { id: 'n_entry', type: 'entry', name: 'entry', parent: null, params: {}, position: [0, 200], display: false, bypass: false },
    },
    wires: [{ id: 'w_1', from: 'n_tick', to: 'n_rsi', from_port: 'out', to_port: 'in0' }],
    annotations: { boxes: [], notes: [] },
  }
}

function ok(diagnostics: Diagnostic[]): ValidateResponse {
  return { ok: !diagnostics.some(d => d.severity === 'error'), diagnostics, streams: {} }
}

/** A promise the test resolves or rejects by hand. */
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

// Latest hook value, read by the tests.
let latest: DiagnosticsState

function Controller() {
  useDiagnosticsController()
  latest = useDiagnostics()
  return (
    <div data-testid="counts">
      {latest.errorCount}/{latest.warningCount}/{latest.pending ? 'pending' : 'idle'}/{latest.offline ? 'offline' : 'online'}
    </div>
  )
}

/** Let the 300 ms wait pass and the mocked request settle. */
async function flush(ms = VALIDATE_DEBOUNCE_MS) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

function commitParam(value: number) {
  act(() => { useNodeBuilderStore.getState().updateNodeParams('n_rsi', { period: value }) })
}

beforeEach(() => {
  vi.useFakeTimers()
  resetDiagnostics()
  validateMock.mockReset()
  validateMock.mockResolvedValue(ok([]))
  act(() => {
    useNodeBuilderStore.getState().openGraph(graphWithLooseEntry(), { id: 'g_000000000001', rev: 1, name: 'loose' })
  })
})

afterEach(() => {
  cleanup()
  resetDiagnostics()
  vi.useRealTimers()
})

/** Mount the controller and let its first (mount) validate finish. */
async function mountController() {
  render(<Controller />)
  await flush()
  validateMock.mockClear()
}

// ---------------------------------------------------------------------------
// Debounce and request rules
// ---------------------------------------------------------------------------

describe('useDiagnosticsController: when it validates', () => {
  it('validates once on mount for a graph not yet checked, not again on remount', async () => {
    render(<Controller />)
    expect(validateMock).not.toHaveBeenCalled()
    await flush()
    expect(validateMock).toHaveBeenCalledTimes(1)
    cleanup()
    render(<Controller />)
    await flush(1000)
    expect(validateMock).toHaveBeenCalledTimes(1)
  })

  it('one commit makes exactly one /validate call, 300 ms later', async () => {
    await mountController()
    commitParam(20)
    await flush(VALIDATE_DEBOUNCE_MS - 1)
    expect(validateMock).not.toHaveBeenCalled()
    await flush(1)
    expect(validateMock).toHaveBeenCalledTimes(1)
    // It sends the graph as it is after the commit.
    expect(validateMock.mock.calls[0][0].nodes.n_rsi.params.period).toBe(20)
    await flush(2000)
    expect(validateMock).toHaveBeenCalledTimes(1)
  })

  it('two commits 100 ms apart make one call', async () => {
    await mountController()
    commitParam(20)
    await flush(100)
    commitParam(21)
    await flush(VALIDATE_DEBOUNCE_MS - 1)
    expect(validateMock).not.toHaveBeenCalled()
    await flush(1)
    expect(validateMock).toHaveBeenCalledTimes(1)
    expect(validateMock.mock.calls[0][0].nodes.n_rsi.params.period).toBe(21)
  })

  it('selection and viewport changes never validate', async () => {
    await mountController()
    act(() => {
      useNodeBuilderStore.getState().select('n_rsi')
      useNodeBuilderStore.getState().setViewport({ x: 40, y: 10, zoom: 1.5 })
      useNodeBuilderStore.getState().select(null)
    })
    await flush(2000)
    expect(validateMock).not.toHaveBeenCalled()
  })

  it('undo and redo validate like a commit', async () => {
    await mountController()
    commitParam(20)
    await flush()
    act(() => { useNodeBuilderStore.getState().undo() })
    await flush()
    expect(validateMock).toHaveBeenCalledTimes(2)
    expect(validateMock.mock.calls[1][0].nodes.n_rsi.params.period).toBe(14)
  })

  it('a newer commit cancels the request in flight and drops its late answer', async () => {
    await mountController()
    const first = deferred<ValidateResponse>()
    validateMock.mockImplementationOnce(() => first.promise)
    commitParam(20)
    await flush()
    const firstSignal = validateMock.mock.calls[0][1] as AbortSignal
    expect(firstSignal.aborted).toBe(false)

    validateMock.mockResolvedValueOnce(ok([ENTRY_DIAG]))
    commitParam(21)
    expect(firstSignal.aborted).toBe(true)
    await flush()
    expect(latest.errorCount).toBe(1)

    // The first request answers late, with a different result: ignored.
    await act(async () => { first.resolve(ok([])) })
    expect(latest.errorCount).toBe(1)
    expect(latest.diagnostics[0].message).toBe(ENTRY_MSG)
  })
})

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

describe('useDiagnostics: states', () => {
  it('counts errors and warnings, not info', async () => {
    validateMock.mockResolvedValue(ok([
      ENTRY_DIAG,
      diag({ node_id: 'n_rsi', severity: 'warning', code: 'size_unit_suspect', message: 'w' }),
      diag({ node_id: null, severity: 'info', code: 'note', message: 'i' }),
    ]))
    render(<Controller />)
    expect(latest.hasResult).toBe(false)
    expect(latest.pending).toBe(true)
    await flush()
    expect(latest.errorCount).toBe(1)
    expect(latest.warningCount).toBe(1)
    expect(latest.diagnostics).toHaveLength(3)
    expect(Object.keys(latest.byNode).sort()).toEqual(['n_entry', 'n_rsi'])
    expect(latest.hasResult).toBe(true)
    expect(latest.pending).toBe(false)
  })

  it('keeps the last result while the next one is pending', async () => {
    validateMock.mockResolvedValue(ok([ENTRY_DIAG]))
    await mountController()
    expect(latest.errorCount).toBe(1)
    const next = deferred<ValidateResponse>()
    validateMock.mockImplementationOnce(() => next.promise)
    commitParam(20)
    expect(latest.pending).toBe(true)
    expect(latest.errorCount).toBe(1)
    await flush()
    expect(latest.pending).toBe(true)
    expect(latest.errorCount).toBe(1)
    await act(async () => { next.resolve(ok([])) })
    expect(latest.pending).toBe(false)
    expect(latest.errorCount).toBe(0)
  })

  it('a failed request keeps the last result and sets offline until the next success', async () => {
    validateMock.mockResolvedValue(ok([ENTRY_DIAG]))
    await mountController()
    validateMock.mockRejectedValueOnce(new Error('Network Error'))
    commitParam(20)
    await flush()
    expect(latest.offline).toBe(true)
    expect(latest.offlineDetail).toBe('Network Error')
    expect(latest.errorCount).toBe(1)
    expect(latest.pending).toBe(false)

    validateMock.mockResolvedValueOnce(ok([]))
    commitParam(21)
    await flush()
    expect(latest.offline).toBe(false)
    expect(latest.errorCount).toBe(0)
  })

  it('does not validate the read-only auto-render view', async () => {
    // openGraph always makes an editable copy, so set the view graph directly.
    act(() => { useNodeBuilderStore.setState({ graph: { ...graphWithLooseEntry(), readOnly: true } }) })
    render(<Controller />)
    await flush(1000)
    expect(validateMock).not.toHaveBeenCalled()
    expect(latest.errorCount).toBe(0)
  })

  it('setServerDiagnostics holds until the next commit, and beats a pending validate', async () => {
    await mountController()
    const late = deferred<ValidateResponse>()
    validateMock.mockImplementationOnce(() => late.promise)
    commitParam(20)
    await flush()
    // Run came back 400 with diagnostics.
    act(() => { setServerDiagnostics([ENTRY_DIAG]) })
    expect(latest.errorCount).toBe(1)
    expect(latest.pending).toBe(false)
    await act(async () => { late.resolve(ok([])) })
    await flush(2000)
    expect(latest.errorCount).toBe(1)

    commitParam(21)
    await flush()
    expect(latest.errorCount).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Node badge
// ---------------------------------------------------------------------------

const nodeTypes: NodeTypes = { nbOutput: OutputNode }

function entryNode(): Node {
  return {
    id: 'n_entry',
    type: 'nbOutput',
    position: { x: 0, y: 0 },
    data: { backendType: 'entry', catalog: null, params: {}, display: false, bypass: false, nodePath: '/entry', editable: true },
  }
}

function Flow() {
  return (
    <div style={{ width: 600, height: 400 }}>
      <ReactFlow nodes={[entryNode()]} edges={[]} nodeTypes={nodeTypes} />
    </div>
  )
}

describe('DiagnosticBadge on the node', () => {
  it('an unconnected Entry gets one red `!` badge and a red border', async () => {
    validateMock.mockResolvedValue(ok([ENTRY_DIAG]))
    const { container } = render(<><Controller /><Flow /></>)
    expect(screen.queryByTestId('nb-diag-badge-n_entry')).toBeNull()
    await flush()
    expect(latest.errorCount).toBe(1)
    const badge = screen.getByTestId('nb-diag-badge-n_entry')
    expect(badge.tagName).toBe('BUTTON')
    expect(badge.textContent).toBe('!')
    expect(badge.className).toContain('nb-diag-badge--error')
    expect(badge.getAttribute('aria-label')).toBe(`1 error: ${ENTRY_MSG}`)
    const card = container.querySelector('.react-flow__node[data-id="n_entry"] .nb-node-card') as HTMLElement
    expect(card.style.border).toContain('rgba(248, 113, 113, 0.6)')
  })

  it('warnings only: amber `▲`; more than one: the count', async () => {
    validateMock.mockResolvedValue(ok([
      diag({ node_id: 'n_entry', severity: 'warning', code: 'exit_unconnected', message: 'w1' }),
    ]))
    render(<><Controller /><Flow /></>)
    await flush()
    let badge = screen.getByTestId('nb-diag-badge-n_entry')
    expect(badge.textContent).toBe('▲')
    expect(badge.className).toContain('nb-diag-badge--warning')

    validateMock.mockResolvedValue(ok([
      ENTRY_DIAG,
      diag({ node_id: 'n_entry', severity: 'warning', code: 'exit_unconnected', message: 'w1' }),
    ]))
    commitParam(20)
    await flush()
    badge = screen.getByTestId('nb-diag-badge-n_entry')
    expect(badge.textContent).toBe('2')
    expect(badge.className).toContain('nb-diag-badge--error')
  })

  it('hover for 400 ms shows the messages; a click selects the node', async () => {
    validateMock.mockResolvedValue(ok([ENTRY_DIAG]))
    render(<><Controller /><Flow /></>)
    await flush()
    const badge = screen.getByTestId('nb-diag-badge-n_entry')
    fireEvent.mouseEnter(badge)
    await flush(399)
    expect(screen.queryByRole('tooltip')).toBeNull()
    await flush(1)
    expect(screen.getByRole('tooltip').textContent).toContain(ENTRY_MSG)
    fireEvent.mouseLeave(badge)
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.click(badge)
    expect(useNodeBuilderStore.getState().selectedNodeId).toBe('n_entry')
  })
})

// ---------------------------------------------------------------------------
// ParamRow invalid state
// ---------------------------------------------------------------------------

function RsiPeriodRow() {
  const value = useNodeBuilderStore(s => s.graph?.nodes.n_rsi?.params.period)
  return <ParamRow nodeId="n_rsi" paramKey="period" value={value} typeSpec={{ type: 'number' }} />
}

describe('ParamRow invalid state', () => {
  it('typing abc sets aria-invalid at once, with no request', async () => {
    await mountController()
    render(<RsiPeriodRow />)
    const input = screen.getByTestId('nb-param-n_rsi-period') as HTMLInputElement
    expect(input.getAttribute('aria-invalid')).toBeNull()
    act(() => { input.focus() })
    fireEvent.change(input, { target: { value: 'abc' } })
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.title).toBe('Enter a number')
    const msgId = input.getAttribute('aria-describedby')!
    expect(document.getElementById(msgId)?.textContent).toBe('Enter a number')
    // One border shorthand in the error color.
    expect(input.style.border).toBe('1px solid var(--nb-error)')

    // The local problem counts (so Run is disabled) without any request.
    expect(latest.errorCount).toBe(1)
    expect(latest.diagnostics[0]).toMatchObject({ node_id: 'n_rsi', param: 'period', code: 'param_invalid' })
    await flush(2000)
    expect(validateMock).not.toHaveBeenCalled()

    // Fixing the text clears it at once.
    fireEvent.change(input, { target: { value: '12' } })
    expect(input.getAttribute('aria-invalid')).toBeNull()
    expect(latest.errorCount).toBe(0)
  })

  it('a server diagnostic on the param turns the field red with its message', async () => {
    const msg = 'period must be between 2 and 500.'
    validateMock.mockResolvedValue(ok([
      diag({ node_id: 'n_rsi', code: 'param_out_of_range', param: 'period', message: msg }),
    ]))
    render(<><Controller /><RsiPeriodRow /></>)
    await flush()
    const input = screen.getByTestId('nb-param-n_rsi-period') as HTMLInputElement
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.title).toBe(msg)

    validateMock.mockResolvedValue(ok([]))
    commitParam(20)
    await flush()
    expect(input.getAttribute('aria-invalid')).toBeNull()
    expect(input.title).toBe('')
  })

  it('the local problem goes away when the row unmounts', async () => {
    await mountController()
    const { unmount } = render(<RsiPeriodRow />)
    const input = screen.getByTestId('nb-param-n_rsi-period') as HTMLInputElement
    act(() => { input.focus() })
    fireEvent.change(input, { target: { value: '1e' } })
    expect(latest.errorCount).toBe(1)
    unmount()
    expect(latest.errorCount).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Popover
// ---------------------------------------------------------------------------

describe('DiagnosticsPopover', () => {
  function PopoverHarness({ onSelectNode, onClose }: { onSelectNode: (id: string) => void; onClose: () => void }) {
    return (
      <>
        <Controller />
        <button data-testid="chip">chip</button>
        <DiagnosticsPopover open anchorEl={document.body} onClose={onClose} onSelectNode={onSelectNode} />
      </>
    )
  }

  it('lists rows grouped by node with the server message, and a row click selects', async () => {
    validateMock.mockResolvedValue(ok([
      ENTRY_DIAG,
      diag({ node_id: 'n_rsi', severity: 'warning', code: 'size_unit_suspect', message: 'Looks like a percent.' }),
      diag({ node_id: null, code: 'missing_terminal', message: 'The graph has no Exit.' }),
    ]))
    const onSelectNode = vi.fn()
    const onClose = vi.fn()
    render(<PopoverHarness onSelectNode={onSelectNode} onClose={onClose} />)
    await flush()
    const pop = screen.getByTestId('nb-diag-popover')
    expect(pop.getAttribute('role')).toBe('dialog')
    expect(pop.getAttribute('aria-label')).toBe('Diagnostics')
    expect(pop.textContent).toContain('2 errors · 1 warning')
    expect(pop.textContent).toContain('Run is disabled until the errors are fixed.')
    // Group headers use node names, and `Graph` for graph-level problems.
    expect(pop.textContent).toContain('entry')
    expect(pop.textContent).toContain('rsi')
    expect(pop.textContent).toContain('Graph')

    const row0 = screen.getByTestId('nb-diag-row-0')
    expect(row0.querySelector('.nb-diag-pop__msg')?.textContent).toBe(ENTRY_MSG)
    expect(row0.querySelector('.nb-diag-pop__code')?.textContent).toBe('missing_input')
    fireEvent.click(row0)
    expect(onSelectNode).toHaveBeenCalledWith('n_entry')

    // A graph-level row does nothing on click.
    fireEvent.click(screen.getByTestId('nb-diag-row-2'))
    expect(onSelectNode).toHaveBeenCalledTimes(1)

    // Arrow keys move between rows.
    act(() => { row0.focus() })
    fireEvent.keyDown(row0, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(screen.getByTestId('nb-diag-row-1'))
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowUp' })
    expect(document.activeElement).toBe(row0)
  })

  it('says so when the graph is clean, and is not rendered when closed', async () => {
    const { rerender } = render(
      <DiagnosticsPopover open anchorEl={document.body} onClose={() => {}} onSelectNode={() => {}} />,
    )
    expect(screen.getByTestId('nb-diag-popover').textContent).toContain('No problems in this graph.')
    rerender(<DiagnosticsPopover open={false} anchorEl={document.body} onClose={() => {}} onSelectNode={() => {}} />)
    expect(screen.queryByTestId('nb-diag-popover')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// W1 fix pass
// ---------------------------------------------------------------------------

describe('W1 fix pass: diagnostics', () => {
  it('ArrowDown from the header ✕ reaches the first row (UX-07)', async () => {
    validateMock.mockResolvedValue(ok([ENTRY_DIAG, diag({ node_id: 'n_rsi', message: 'second' })]))
    render(
      <>
        <Controller />
        <DiagnosticsPopover open anchorEl={document.body} onClose={() => {}} onSelectNode={() => {}} />
      </>,
    )
    await flush()
    const close = screen.getByRole('button', { name: 'Close' })
    act(() => { close.focus() })
    fireEvent.keyDown(close, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(screen.getByTestId('nb-diag-row-0'))
    fireEvent.keyDown(document.activeElement!, { key: 'End' })
    expect(document.activeElement).toBe(screen.getByTestId('nb-diag-row-1'))
  })

  it('a badge with several problems describes all their messages (UX-16)', async () => {
    validateMock.mockResolvedValue(ok([
      ENTRY_DIAG,
      diag({ node_id: 'n_entry', severity: 'warning', code: 'exit_unconnected', message: 'Second problem.' }),
    ]))
    render(<><Controller /><Flow /></>)
    await flush()
    const badge = screen.getByTestId('nb-diag-badge-n_entry')
    const descId = badge.getAttribute('aria-describedby')
    expect(descId).toBeTruthy()
    const desc = document.getElementById(descId!)!
    expect(desc.textContent).toContain(ENTRY_MSG)
    expect(desc.textContent).toContain('Second problem.')
  })

  it('an unchanged answer keeps each node\'s list, and a no-op update renders nothing (UX-18)', async () => {
    validateMock.mockResolvedValue(ok([ENTRY_DIAG]))
    await mountController()
    const before = latest.byNode.n_entry
    expect(before).toHaveLength(1)
    // Same problems again, as new objects: the node keeps its list.
    validateMock.mockResolvedValue(ok([{ ...ENTRY_DIAG }]))
    commitParam(21)
    await flush()
    expect(latest.byNode.n_entry).toBe(before)
    // A request in flight: runValidate's "pending" is already set, so no new view.
    validateMock.mockReturnValue(new Promise(() => {}))
    commitParam(22)
    const pendingView = latest
    expect(pendingView.pending).toBe(true)
    await flush()
    expect(latest).toBe(pendingView)
  })
})
