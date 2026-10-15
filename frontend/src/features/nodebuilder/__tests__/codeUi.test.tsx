/**
 * Code UI on the canvas and in the Inspector (F435 W7, specs S45, S46, S48,
 * S49). parse_code and code_capabilities are mocked; the store, the canvas
 * and the Inspector are real. The Wrangle catalog entry arrives with the
 * regenerated catalog after the wave, so it is added to the generated
 * catalog here, shaped as the plan says (Code category, in0..in3).
 */

import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act, cleanup, within } from '@testing-library/react'
import { useRef } from 'react'
import type { Graph, GraphNode, ParamValue, SpareParamSpec } from '../../../api/nodebuilder'
import type { ParseCodeResponse } from '../../../api/nodebuilderCode'
import type { Diagnostic } from '../../../api/nodebuilderValidate'

vi.mock('../catalog.generated', async importOriginal => {
  const orig = await importOriginal<typeof import('../catalog.generated')>()
  const wrangle = {
    name: 'wrangle', cat: 'code', desc: 'Code that reads the merged input stream and writes new attributes.',
    compile_active: true,
    inputs: { ports: [{ label: 'in0' }, { label: 'in1', optional: true }], dynamic: true, min: 1, max: 4 },
    params: [], reads: [], writes: [], subtitle: 'wrangle', setting_key: null, ins: 1, outs: 1,
  }
  // The generated catalog has carried 7.C's wrangle entry since the W7
  // regeneration; this stand-in is only for a tree without it.
  if (orig.GENERATED_CATALOG.some(e => e.name === 'wrangle')) return orig
  return { ...orig, GENERATED_CATALOG: [...orig.GENERATED_CATALOG, wrangle] }
})
vi.mock('../../../api/nodebuilderCode', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilderCode')>()
  return { ...orig, parseCode: vi.fn(), getCodeCapabilities: vi.fn() }
})

import { getCodeCapabilities, parseCode } from '../../../api/nodebuilderCode'
import Canvas from '../Canvas'
import Inspector from '../Inspector'
import NoticeStack from '../NoticeStack'
import { useGlobalKeys } from '../commands/useGlobalKeys'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import { pruneCodeState, resetCodeStore, runParse, setDrawerOpen, setLiveDraft, useCodeStore, loadCodeCapabilities } from '../code/codeStore'
import { CODE_OFF_TOOLTIP, useCodeBlocksRun, useCodeModeController } from '../code/CodeBanner'
import { defaultWrangleCode, hasCode, setNodeCode } from '../code/codeOps'
import { openCodeInInspector } from '../code/codeUi'
import { toggleInspector, resetInspectorUi, useInspectorUi } from '../inspector/state'
import { clearNotices } from '../notices'
import { PauseReasonRow } from '../../trading/BotPauseRows'
import { CODE_DISABLED_PAUSE_TEXT } from '../../trading/botPauseText'

const parseMock = parseCode as unknown as ReturnType<typeof vi.fn>
const capsMock = getCodeCapabilities as unknown as ReturnType<typeof vi.fn>

function node(id: string, type: string, params: Record<string, ParamValue>, y: number, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, y], display: false, bypass: false, ...extra }
}

function makeGraph(extraNodes: GraphNode[] = [], extraWires: Graph['wires'] = []): Graph {
  const nodes: Record<string, GraphNode> = {
    t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d' }, 0),
    rsi: node('rsi', 'rsi', { period: 14, type: 'wilder', source: '@close' }, 150),
  }
  for (const n of extraNodes) nodes[n.id] = n
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes,
    wires: [{ id: 'w1', from: 't', to: 'rsi', from_port: 'out', to_port: 'in0' }, ...extraWires],
    annotations: { boxes: [], notes: [] },
  }
}

function res(partial: Partial<ParseCodeResponse> = {}): ParseCodeResponse {
  return { ok: true, params: [], reads: [], writes: [], result_type: null, diagnostics: [], ...partial }
}

function diag(partial: Partial<Diagnostic>): Diagnostic {
  return {
    node_id: 'w', path: '/w', severity: 'error', code: 'code_runtime', message: 'boom',
    param: null, port: null, line: null, col: null, end_line: null, end_col: null, ...partial,
  }
}

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class { m22 = 1; constructor() {} }
  }
})

beforeEach(() => {
  resetCodeStore()
  resetDiagnostics()
  resetInspectorUi()
  clearNotices()
  parseMock.mockReset()
  parseMock.mockResolvedValue(res())
  capsMock.mockReset()
  capsMock.mockResolvedValue({ enabled: true, language: 'python', limits: { max_source_bytes: 8192, default_lookback_bars: 500, cook_timeout_s: { bot: 10, backtest: 60 } }, modules: [], functions: [], leaked_cooks: 0 })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  useNodeBuilderStore.getState().discardEdits()
})

/** The canvas on the store graph, with the global keys, as NodeBuilder mounts it. */
function Live({ withInspector = false }: { withInspector?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useGlobalKeys(ref)
  const graph = useNodeBuilderStore(s => s.graph)!
  return (
    <div ref={ref} className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      <Canvas graph={graph} />
      {withInspector && <Inspector />}
    </div>
  )
}

function mount(graph: Graph, withInspector = false) {
  act(() => { useNodeBuilderStore.getState().openGraph(graph, { id: 'g_1', rev: 1, name: 'g' }) })
  if (withInspector) act(() => toggleInspector(true))
  const utils = render(<Live withInspector={withInspector} />)
  inView(utils.container)
  return utils
}

/** jsdom has no layout: give the builder roots a client rect so keys count as "in view". */
function inView(container: HTMLElement) {
  for (const el of container.querySelectorAll<HTMLElement>('.nodebuilder-root')) {
    el.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  }
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

function g(): Graph {
  return useNodeBuilderStore.getState().graph!
}

const SMOOTH = '@rsi_smooth = sl.ema(@rsi, chi("smooth", default=3))'

// ---------------------------------------------------------------------------
// S45: code drawer and the Inspector Code section
// ---------------------------------------------------------------------------

describe('S45 code drawer', () => {
  it('a node with one line of code shows `code · 1 line` and opens to highlighted text', async () => {
    const graph = makeGraph()
    graph.nodes.rsi.code = SMOOTH
    mount(graph)
    await flush()
    const row = screen.getByTestId('nb-code-drawer-row-rsi')
    expect(row.textContent).toContain('code · 1 line')
    expect(row.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(row)
    const block = screen.getByTestId('nb-codeblk-rsi')
    const write = [...block.querySelectorAll('[data-token="attr.write"]')].find(e => e.textContent === '@rsi_smooth') as HTMLElement
    const fn = [...block.querySelectorAll('[data-token="func"]')].find(e => e.textContent === 'sl.ema') as HTMLElement
    expect(write.style.color).toBe('var(--nb-code-keyword)')
    expect(fn.style.color).toBe('var(--nb-code-func)')
    // No Monaco and no textarea on the canvas.
    expect(block.querySelector('textarea')).toBeNull()
  })

  it('a node without code has no drawer row', () => {
    mount(makeGraph())
    expect(screen.queryByTestId('nb-code-drawer-row-rsi')).toBeNull()
  })

  it('typing in the Inspector parses once after 400 ms, and the answer’s writes become a node chip', async () => {
    const graph = makeGraph()
    graph.nodes.rsi.code = '@rsi = @rsi'
    mount(graph, true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }) })
    await flush()
    parseMock.mockClear()
    vi.useFakeTimers()
    parseMock.mockResolvedValue(res({ writes: [{ name: '@rsi_smooth', class: 'point', dtype: 'any' }] }))
    const editor = screen.getByTestId('nb-code-editor-rsi-plain') as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: SMOOTH } })
    await act(async () => { vi.advanceTimersByTime(399) })
    expect(parseMock).not.toHaveBeenCalled()
    await act(async () => { vi.advanceTimersByTime(1) })
    expect(parseMock).toHaveBeenCalledTimes(1)
    expect(parseMock.mock.calls[0][0].context).toBe('node_code')
    // The 800 ms burst commit writes the code; the parse answer gives the chip.
    await act(async () => { vi.advanceTimersByTime(400) })
    await act(async () => { await Promise.resolve() })
    expect(g().nodes.rsi.code).toBe(SMOOTH)
    expect(screen.getByTestId('nb-code-write-rsi-@rsi_smooth')).toBeInTheDocument()
  })

  it('a parse error at 2:8 lists `2:8` under the editor and badges the node', async () => {
    const graph = makeGraph()
    graph.nodes.rsi.code = 'x = 1\ny = (\n'
    parseMock.mockResolvedValue(res({ ok: false, diagnostics: [diag({ node_id: 'rsi', code: 'code_syntax', message: 'bad', line: 2, col: 8 })] }))
    mount(graph, true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }) })
    await flush()
    const list = screen.getByTestId('nb-code-diags')
    expect(list.textContent).toContain('2:8')
    expect(screen.getByTestId('nb-diag-badge-rsi')).toBeInTheDocument()
  })

  it('Clear code… then Remove sets code to null and keeps spare values in params', async () => {
    const graph = makeGraph()
    graph.nodes.rsi.code = SMOOTH
    graph.nodes.rsi.spare_params = [{ name: 'smooth', type: 'int', default: 3, label: 'smooth' }]
    graph.nodes.rsi.params.smooth = 5
    mount(graph, true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }) })
    await flush()
    fireEvent.click(screen.getByTestId('nb-code-menu'))
    fireEvent.click(screen.getByTestId('nb-code-clear'))
    const dialog = screen.getByTestId('nb-code-clear-dialog')
    expect(dialog.textContent).toContain('Remove the code block from rsi? Spare parameters and their values are kept until you save.')
    fireEvent.click(within(dialog).getByText('Remove'))
    expect(g().nodes.rsi.code).toBeNull()
    expect(g().nodes.rsi.params.smooth).toBe(5)
  })
})

// ---------------------------------------------------------------------------
// S46: Wrangle
// ---------------------------------------------------------------------------

function wrangle(id: string, code: string, y = 300): GraphNode {
  return node(id, 'wrangle', {}, y, { code })
}

describe('S46 Wrangle node', () => {
  it('a new Wrangle from the Tab menu gets the default code, its write made unique', async () => {
    mount(makeGraph([node('o', 'constant', { value: 1, out: '@out' }, 400)]))
    ;(document.activeElement as HTMLElement | null)?.blur()
    act(() => { fireEvent.keyDown(document.body, { key: 'Tab' }) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: 'wrangle' } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    const made = Object.values(g().nodes).find(n => n.type === 'wrangle')
    expect(made?.code).toBe('# write attributes with @name = expr\n@out_2 = @close')
    expect(defaultWrangleCode(new Set())).toBe('# write attributes with @name = expr\n@out = @close')
  })

  it('shows the parse answer’s writes (purple) and reads (grey), in that order', async () => {
    parseMock.mockResolvedValue(res({
      reads: [{ name: '@close', class: 'point', dtype: 'float' }, { name: '@msft_close', class: 'point', dtype: 'float' }],
      writes: [{ name: '@spread', class: 'point', dtype: 'any' }, { name: '@spread_z', class: 'point', dtype: 'any' }],
    }))
    const { container } = mount(makeGraph([wrangle('w', '@spread = @close - @msft_close\n@spread_z = @spread')], [
      { id: 'w2', from: 't', to: 'w', from_port: 'out', to_port: 'in0' },
    ]))
    await flush()
    const card = container.querySelector('.react-flow__node-nbWrangle') as HTMLElement
    const chips = [...card.querySelectorAll('.nb-chip-row .nb-chip')].map(c => c.textContent)
    expect(chips).toEqual(['@close', '@msft_close', '+@spread', '+@spread_z'])
    expect(card.querySelector('.nb-node-card')?.getAttribute('aria-label')).toBe('Wrangle w, writes @spread and @spread_z')
    // The code block is the first thing in the body.
    const body = screen.getByTestId('nb-node-body-w')
    expect(body.firstElementChild?.getAttribute('data-testid')).toBe('nb-codeblk-w')
  })

  it('a diagnostic on line 3 tints the third line', async () => {
    parseMock.mockResolvedValue(res({ ok: false, diagnostics: [diag({ line: 3, col: 0 })] }))
    mount(makeGraph([wrangle('w', 'a = 1\nb = 2\nc = 1 / 0\n@x = @close')]))
    await flush()
    const block = screen.getByTestId('nb-codeblk-w')
    const third = block.querySelector('[data-line="3"]') as HTMLElement
    expect(third.className).toContain('nb-codeblk__line--error')
    expect(block.querySelector('[data-line="2"]')?.className).not.toContain('--error')
    expect(block.className).toContain('nb-codeblk--error')
  })

  it('draws connected inputs plus one dashed spare, and no spare at 4', async () => {
    const extra = ['a', 'b', 'c'].map((id, i) => node(id, 'ticker', { symbol: 'X', interval: '1d' }, -100 * (i + 1)))
    mount(makeGraph([...extra, wrangle('w', '@x = @close')], [
      { id: 'wa', from: 't', to: 'w', from_port: 'out', to_port: 'in0' },
      { id: 'wb', from: 'a', to: 'w', from_port: 'out', to_port: 'in1' },
    ]))
    await flush()
    expect(screen.getByTestId('nb-port-w-in0')).toBeInTheDocument()
    expect(screen.getByTestId('nb-port-w-in1')).toBeInTheDocument()
    expect(screen.getByTestId('nb-port-w-in2').className).toContain('nb-port--spare')
    cleanup()
    useNodeBuilderStore.getState().discardEdits()
    mount(makeGraph([...extra, wrangle('w', '@x = @close')], [
      { id: 'wa', from: 't', to: 'w', from_port: 'out', to_port: 'in0' },
      { id: 'wb', from: 'a', to: 'w', from_port: 'out', to_port: 'in1' },
      { id: 'wc', from: 'b', to: 'w', from_port: 'out', to_port: 'in2' },
      { id: 'wd', from: 'c', to: 'w', from_port: 'out', to_port: 'in3' },
    ]))
    await flush()
    expect(screen.getByTestId('nb-port-w-in3')).toBeInTheDocument()
    expect(screen.queryByTestId('nb-port-w-in4')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// S48: spare params
// ---------------------------------------------------------------------------

const THRESHOLD: SpareParamSpec = { name: 'threshold', type: 'float', default: 2.0, min: 0, max: 10, label: 'threshold' }

describe('S48 spare params', () => {
  it('a parsed chf() becomes a row on the node and in the Inspector (with a slider), valued at its default', async () => {
    parseMock.mockResolvedValue(res({ params: [THRESHOLD] }))
    mount(makeGraph([wrangle('w', '@x = @close > chf("threshold", default=2.0)')]), true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['w'], primary: 'w' }) })
    await flush()
    expect(g().nodes.w.params.threshold).toBe(2.0)
    expect(g().nodes.w.spare_params).toEqual([THRESHOLD])
    expect(screen.getAllByTestId('nb-spare-w-threshold').length).toBe(2)
    expect(screen.getByTestId('nb-inspector-slider-threshold')).toBeInTheDocument()
    // One "Spare parameters" title in the Inspector (Code section is open).
    expect(screen.getAllByTestId('nb-spare-subtitle')).toHaveLength(1)
  })

  it('a later answer without the param removes the row and keeps the value', async () => {
    const graph = makeGraph([wrangle('w', '@x = @close')])
    graph.nodes.w.spare_params = [THRESHOLD]
    graph.nodes.w.params.threshold = 4
    parseMock.mockResolvedValue(res({ params: [] }))
    mount(graph)
    await flush()
    expect(g().nodes.w.spare_params).toEqual([])
    expect(g().nodes.w.params.threshold).toBe(4)
    expect(screen.queryByTestId('nb-spare-w-threshold')).toBeNull()
  })

  it('a vector spare draws x y z cells; editing the second commits the list', async () => {
    const graph = makeGraph([wrangle('w', '@x = @close')])
    const vec: SpareParamSpec = { name: 'weights', type: 'vector', default: [1, 2, 3], label: 'weights' }
    graph.nodes.w.spare_params = [vec]
    graph.nodes.w.params.weights = [1, 2, 3]
    parseMock.mockResolvedValue(res({ params: [vec] }))
    mount(graph)
    await flush()
    const group = document.querySelector('[role="group"][aria-label="weights vector"]') as HTMLElement
    expect(group.querySelectorAll('input')).toHaveLength(3)
    expect(group.textContent).toContain('x')
    const y = screen.getByTestId('nb-param-w-weights-y')
    fireEvent.change(y, { target: { value: '9' } })
    fireEvent.blur(y)
    expect(g().nodes.w.params.weights).toEqual([1, 9, 3])
  })

  it('with 3 built-in rows and 3 spare params the node shows 4 rows and `+2 more in Inspector`', async () => {
    const graph = makeGraph()
    const specs: SpareParamSpec[] = ['a', 'b', 'c'].map(n => ({ name: n, type: 'int', default: 1, label: n }))
    graph.nodes.rsi.code = '@rsi = @rsi + chi("a") + chi("b") + chi("c")'
    graph.nodes.rsi.spare_params = specs
    Object.assign(graph.nodes.rsi.params, { a: 1, b: 1, c: 1 })
    parseMock.mockResolvedValue(res({ params: specs }))
    mount(graph)
    await flush()
    const rows = screen.getByTestId('nb-spare-rows-rsi')
    expect(rows.querySelectorAll('.nb-spare-row')).toHaveLength(1)
    expect(screen.getByTestId('nb-spare-more-rsi').textContent).toBe('+2 more in Inspector')
    // The spare values are not drawn again as unknown params.
    expect(screen.queryByTestId('nb-param-rsi-b')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// S49: code off on the server
// ---------------------------------------------------------------------------

function Controller() {
  useCodeModeController()
  const blocked = useCodeBlocksRun()
  return (
    <div className="nodebuilder-root">
      <NoticeStack />
      <button type="button" data-testid="run" disabled={blocked} title={blocked ? CODE_OFF_TOOLTIP : 'Run'}>Run</button>
    </div>
  )
}

describe('S49 code off', () => {
  it('a graph with a Wrangle: banner with `Show 1 code nodes`, Run disabled with the tooltip, no = buttons', async () => {
    capsMock.mockResolvedValue({ enabled: false, language: 'python', limits: { max_source_bytes: 8192, default_lookback_bars: 500, cook_timeout_s: { bot: 10, backtest: 60 } }, modules: [], functions: [], leaked_cooks: 0 })
    act(() => { useNodeBuilderStore.getState().openGraph(makeGraph([wrangle('w', '@x = @close')]), { id: 'g', rev: 1, name: 'g' }) })
    render(<><Controller /><Live /></>)
    await act(async () => { await loadCodeCapabilities() })
    await flush()
    const banner = screen.getByTestId('nb-banner-code_disabled')
    expect(banner.getAttribute('role')).toBe('status')
    expect(banner.textContent).toContain('Code nodes are disabled on this server (SL_CODE_NODES=0).')
    expect(screen.getByTestId('nb-code-banner-show').textContent).toBe('Show 1 code nodes')
    const run = screen.getByTestId('run')
    expect(run).toBeDisabled()
    expect(run.getAttribute('title')).toBe('Code nodes are disabled on this server')
    expect(screen.queryByTestId('nb-expr-toggle')).toBeNull()
    fireEvent.click(screen.getByTestId('nb-code-banner-dismiss'))
    expect(screen.queryByTestId('nb-banner-code_disabled')).toBeNull()
  })

  it('a graph without code: no banner, and the Tab menu’s Wrangle row is disabled and places nothing', async () => {
    useCodeStore.setState({ enabled: false })
    act(() => { useNodeBuilderStore.getState().openGraph(makeGraph(), { id: 'g', rev: 1, name: 'g' }) })
    capsMock.mockResolvedValue({ enabled: false, language: 'python', limits: { max_source_bytes: 8192, default_lookback_bars: 500, cook_timeout_s: { bot: 10, backtest: 60 } }, modules: [], functions: [], leaked_cooks: 0 })
    const { container } = render(<><Controller /><Live /></>)
    inView(container)
    await flush()
    expect(screen.queryByTestId('nb-banner-code_disabled')).toBeNull()
    ;(document.activeElement as HTMLElement | null)?.blur()
    act(() => { fireEvent.keyDown(document.body, { key: 'Tab' }) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: 'wrangle' } }) })
    const row = screen.getByTestId('nb-tab-row-wrangle')
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(row.getAttribute('title')).toBe('disabled')
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    expect(Object.values(g().nodes).some(n => n.type === 'wrangle')).toBe(false)
    // Trying to use code brings the banner.
    await flush()
    expect(screen.getByTestId('nb-banner-code_disabled')).toBeInTheDocument()
  })

  it('BotCard: a code_disabled pause reads as a sentence with an Open graph link', () => {
    render(<PauseReasonRow reason="code_disabled" graphId="g_1" group="main" />)
    const row = screen.getByTestId('bot-pause-reason')
    expect(row.textContent).toContain(CODE_DISABLED_PAUSE_TEXT)
    expect(row.textContent?.startsWith('Paused: code nodes are disabled')).toBe(true)
    expect(within(row).getByText('Open graph')).toBeInTheDocument()
    cleanup()
    render(<PauseReasonRow reason="code_timeout: vol ran longer than 10 s" graphId="g_1" group={null} />)
    expect(screen.getByTestId('bot-pause-reason').textContent).toBe('code_timeout: vol ran longer than 10 s')
  })

  it('hasCode: an {expr} param, a code block, a Wrangle', () => {
    const base = makeGraph()
    expect(hasCode(base)).toBe(false)
    const expr = makeGraph()
    expr.nodes.rsi.params.period = { expr: '7' }
    expect(hasCode(expr)).toBe(true)
    const block = makeGraph()
    block.nodes.rsi.code = '@rsi = @rsi'
    expect(hasCode(block)).toBe(true)
    const blank = makeGraph()
    blank.nodes.rsi.code = '   '
    expect(hasCode(blank)).toBe(false)
    expect(hasCode(makeGraph([wrangle('w', '')]))).toBe(true)
  })
})

describe('session drawer state', () => {
  it('is not saved in the graph', () => {
    act(() => { useNodeBuilderStore.getState().openGraph(makeGraph(), { id: 'g', rev: 1, name: 'g' }) })
    setDrawerOpen('rsi', true)
    expect(g().nodes.rsi.meta).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// W7 review fixes (fixer C)
// ---------------------------------------------------------------------------

describe('W7 review fixes: expression rows', () => {
  function withExpr(expr = '21'): Graph {
    const graph = makeGraph()
    graph.nodes.rsi.params.period = { expr }
    return graph
  }

  it('FE-5: Esc on a node expression row reverts the typed text and commits nothing', async () => {
    mount(withExpr())
    await flush()
    const input = screen.getByTestId('expr-input') as HTMLInputElement
    act(() => input.focus())
    fireEvent.change(input, { target: { value: '99' } })
    act(() => { fireEvent.keyDown(input, { key: 'Escape' }) })
    expect(g().nodes.rsi.params.period).toEqual({ expr: '21' })
    expect((screen.getByTestId('expr-input') as HTMLInputElement).value).toBe('21')
    expect(useNodeBuilderStore.getState().past).toHaveLength(0)
  })

  it('FE-5: Esc in the Inspector expression field reverts too', async () => {
    mount(withExpr(), true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }) })
    await flush()
    const row = screen.getByTestId('nb-param-inspector-rsi-period')
    const input = row.querySelector('[data-testid="expr-input"]') as HTMLInputElement
    act(() => input.focus())
    fireEvent.change(input, { target: { value: '99' } })
    act(() => { fireEvent.keyDown(input, { key: 'Escape' }) })
    expect(g().nodes.rsi.params.period).toEqual({ expr: '21' })
    expect(input.value).toBe('21')
  })

  it('UX-3: an ok parse with no result type shows no "→ int"; a result type is shown', async () => {
    mount(withExpr())
    await flush()
    expect(screen.getByTestId('nb-expr-status-period').textContent).toBe('')
    cleanup()
    resetCodeStore()
    parseMock.mockResolvedValue(res({ result_type: 'float' } as Partial<ParseCodeResponse>))
    mount(withExpr())
    await flush()
    expect(screen.getByTestId('nb-expr-status-period').textContent).toBe('→ float')
  })

  it('UX-4: a server ch_cycle on the param turns the expression field red', async () => {
    mount(withExpr())
    await flush()
    act(() => setServerDiagnostics([diag({ node_id: 'rsi', path: '/rsi', code: 'ch_cycle', param: 'period', message: 'ch() cycle: rsi.period -> rsi.period' })]))
    const input = screen.getByTestId('expr-input') as HTMLInputElement
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByTestId('nb-expr-status-period').textContent).toContain('ch_cycle')
  })

  it('UX-2: the Inspector shows no slider and no changed dot for a param in code mode', async () => {
    mount(withExpr(), true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }) })
    await flush()
    expect(screen.queryByTestId('nb-inspector-slider-period')).toBeNull()
    const prow = screen.getByTestId('nb-inspector-param-period')
    expect(prow.querySelector('.nb-insp-prow__changed')).toBeNull()
  })

  it('UX-10: the = toggle is not inside the field label', () => {
    mount(makeGraph())
    const toggle = screen.getAllByTestId('nb-expr-toggle')[0]
    expect(toggle.closest('label')).toBeNull()
    const field = screen.getByTestId('nb-param-rsi-period')
    expect(field.closest('label')?.textContent).not.toContain('=')
  })
})

describe('W7 review fixes: spare vector cell', () => {
  it('FE-5: Esc in a vector cell reverts and commits nothing', async () => {
    const graph = makeGraph([wrangle('w', '@x = @close')])
    const vec: SpareParamSpec = { name: 'weights', type: 'vector', default: [1, 2, 3], label: 'weights' }
    graph.nodes.w.spare_params = [vec]
    graph.nodes.w.params.weights = [1, 2, 3]
    parseMock.mockResolvedValue(res({ params: [vec] }))
    mount(graph)
    await flush()
    const y = screen.getByTestId('nb-param-w-weights-y') as HTMLInputElement
    act(() => y.focus())
    fireEvent.change(y, { target: { value: '9' } })
    act(() => { fireEvent.keyDown(y, { key: 'Escape' }) })
    expect(g().nodes.w.params.weights).toEqual([1, 2, 3])
    expect(y.value).toBe('2')
  })
})

describe('W7 review fixes: Wrangle card', () => {
  it('UX-5: a Wrangle that reads but writes nothing says "writes nothing" next to its read chips', async () => {
    parseMock.mockResolvedValue(res({ reads: [{ name: '@close', class: 'point', dtype: 'float' }], writes: [] }))
    mount(makeGraph([wrangle('w', 'x = @close * 2')]))
    await flush()
    expect(screen.getByTestId('nb-chips-none-w').textContent).toBe('writes nothing')
  })

  it('UX-10: the named card is a group, so its label is exposed', async () => {
    parseMock.mockResolvedValue(res({ writes: [{ name: '@spread', class: 'point', dtype: 'float' }] }))
    mount(makeGraph([wrangle('w', '@spread = @close')]))
    await flush()
    const card = document.querySelector('.nb-node-card[aria-label^="Wrangle"]') as HTMLElement
    expect(card.getAttribute('role')).toBe('group')
  })

  it('UX-4: a server attr_missing on a Wrangle line turns its block red', async () => {
    mount(makeGraph([wrangle('w', '@x = @foo')]))
    await flush()
    expect(screen.getByTestId('nb-codeblk-w').className).not.toContain('nb-codeblk--error')
    act(() => setServerDiagnostics([diag({ node_id: 'w', code: 'attr_missing', message: 'Missing @foo', line: 1, col: 5 })]))
    expect(screen.getByTestId('nb-codeblk-w').className).toContain('nb-codeblk--error')
  })
})

describe('W7 review fixes: code store and Inspector', () => {
  it('FE-11: opening code on a node whose code is only whitespace opens the code.off section', () => {
    const graph = makeGraph()
    graph.nodes.rsi.code = '   \n'
    mount(graph, true)
    act(() => openCodeInInspector('rsi', 1))
    expect(useInspectorUi.getState().sections['code.off']).toBe(true)
  })

  it('FE-6: a parse answer that arrives after an undo does not write spare params onto the old code', async () => {
    const graph = makeGraph()
    graph.nodes.rsi.code = '@rsi = @rsi'
    mount(graph)
    await flush()
    const spec: SpareParamSpec = { name: 'k', type: 'int', default: 3, label: 'k' }
    let answer: (r: ParseCodeResponse) => void = () => {}
    parseMock.mockImplementation(() => new Promise<ParseCodeResponse>(r => { answer = r }))
    const next = '@rsi = @rsi * chi("k", default=3)'
    act(() => { useNodeBuilderStore.getState().commit('edit code', gr => setNodeCode(gr, 'rsi', next)) })
    const pending = runParse({ nodeId: 'rsi', slot: 'code', code: next, context: 'node_code', expected: null, applySpares: true })
    act(() => { useNodeBuilderStore.getState().undo() })
    expect(g().nodes.rsi.code).toBe('@rsi = @rsi')
    await act(async () => { answer(res({ params: [spec] })); await pending })
    expect(g().nodes.rsi.spare_params ?? []).toEqual([])
    expect(useNodeBuilderStore.getState().future.length).toBeGreaterThan(0)
  })

  it('FE-9: a first code block still being typed keeps its parse when the graph changes', async () => {
    mount(makeGraph())
    await flush()
    setLiveDraft('rsi', 'code', '@rsi = @rsi')
    await act(async () => { await runParse({ nodeId: 'rsi', slot: 'code', code: '@rsi = @rsi', context: 'node_code', expected: null }) })
    expect(useCodeStore.getState().parses['rsi|code']).toBeDefined()
    pruneCodeState(g())
    expect(useCodeStore.getState().parses['rsi|code']).toBeDefined()
    setLiveDraft('rsi', 'code', null)
    pruneCodeState(g())
    expect(useCodeStore.getState().parses['rsi|code']).toBeUndefined()
  })

  it('FE-2: switching nodes never carries a code draft over; the typed burst is committed to its own node', async () => {
    const graph = makeGraph([node('rsi2', 'rsi', { period: 7, type: 'wilder', source: '@close' }, 300, { code: 'b = 1' })])
    graph.nodes.rsi.code = 'a = 1'
    mount(graph, true)
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }) })
    await flush()
    const ed = screen.getByTestId('nb-code-editor-rsi-plain') as HTMLTextAreaElement
    fireEvent.change(ed, { target: { value: 'a = 2' } })
    act(() => { useNodeBuilderStore.getState().setSelection({ nodeIds: ['rsi2'], primary: 'rsi2' }) })
    await flush()
    expect((screen.getByTestId('nb-code-editor-rsi2-plain') as HTMLTextAreaElement).value).toBe('b = 1')
    expect(g().nodes.rsi.code).toBe('a = 2')
    expect(g().nodes.rsi2.code).toBe('b = 1')
  })
})
