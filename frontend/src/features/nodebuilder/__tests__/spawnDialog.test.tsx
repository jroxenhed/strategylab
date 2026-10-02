/**
 * Item 5.F tests (F435 W5, surfaces S34, S35, S36):
 * - the Spawn bots dialog: rows per group, the weighted capital split, the
 *   one-request body, the unsaved bar, the 409 and 400 copy, the toast;
 * - BotCard's graph line: the update button comes from the bot summary and
 *   no graph is ever fetched; refusals and the "graph deleted" state;
 * - AddBotBar's group selector: options in file order, graph_group on the
 *   POST, no `graph` key.
 *
 * Money safety (plan 8.4): the HTTP client is mocked. Nothing here starts
 * a bot, and every spawn is checked to ask for stopped bots only.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
// BotCard's chart pieces draw with lightweight-charts; not needed here.
vi.mock('../../trading/MiniSparkline', () => ({ default: () => null }))
vi.mock('../../trading/DailyPnlChart', () => ({ default: () => null }))

import { api } from '../../../api/client'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import type { BotSummary } from '../../../shared/types'
import { graphActionError } from '../../../api/graphSpawn'
import {
  SpawnBotsDialog,
  SpawnBotsDialogHost,
  SpawnToastHost,
  type SpawnBotsDialogProps,
} from '../SpawnBotsDialog'
import { closeSpawnDialog, listenForSpawnRequests, openSpawnDialog, resetSpawnUi } from '../spawnUi'
import { formatCapital, listGraphGroups, parseCapital, splitCapital, type GraphGroupInfo } from '../graphGroups'
import { OPEN_GRAPH_EVENT, OPEN_TRADING_EVENT } from '../graphLinks'
import { useNodeBuilderStore } from '../store'
import BotCard from '../../trading/BotCard'
import AddBotBar from '../../trading/AddBotBar'

const get = vi.mocked(api.get)
const post = vi.mocked(api.post)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function node(id: string, type: string, name: string, params: GraphNode['params'], parent: string | null = null): GraphNode {
  return { id, type, name, parent, params, position: [0, 0], display: false, bypass: false }
}

function graphOf(nodes: GraphNode[]): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires: [],
    annotations: { boxes: [], notes: [] },
  }
}

/** AAPL long / MSFT short pair, one group each. */
function pairGraph(): Graph {
  return graphOf([
    node('t1', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }),
    node('t2', 'ticker', 'msft', { symbol: 'MSFT', interval: '1d' }),
    node('g1', 'output_group', 'long_leg', { direction: 'long', ticker: '/aapl', capital_weight: 1 }),
    node('g2', 'output_group', 'short_leg', { direction: 'short', ticker: '/msft', capital_weight: 1 }),
  ])
}

function oneGroupGraph(): Graph {
  return graphOf([
    node('t1', 'ticker', 'tsla', { symbol: 'TSLA', interval: '15m' }),
    node('g1', 'output_group', 'solo', { direction: 'short', ticker: '/tsla' }),
  ])
}

function groupsOf(g: Graph): GraphGroupInfo[] {
  return listGraphGroups(g)
}

function dialogProps(over: Partial<SpawnBotsDialogProps> = {}): SpawnBotsDialogProps {
  return {
    graphId: 'g_pair',
    graphName: 'pair_aapl_msft',
    rev: 7,
    groups: groupsOf(pairGraph()),
    dirty: false,
    errorCount: 0,
    initialCapital: 10000,
    onClose: vi.fn(),
    onSaveNow: vi.fn(),
    onReload: vi.fn(),
    onShowDiagnostics: vi.fn(),
    onCreated: vi.fn(),
    ...over,
  }
}

function httpError(status: number, detail: unknown) {
  return { response: { status, data: { detail } } }
}

function spawnOk(n = 2) {
  const bots = [
    { bot_id: 'b_1', group: 'long_leg', symbol: 'AAPL', direction: 'long', running: false },
    { bot_id: 'b_2', group: 'short_leg', symbol: 'MSFT', direction: 'short', running: false },
  ].slice(0, n)
  return { status: 201, data: { bots } }
}

function primary(): HTMLButtonElement {
  return screen.getByTestId('nb-dialog-primary') as HTMLButtonElement
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  try { localStorage.clear() } catch { /* ignore */ }
  resetSpawnUi()
})

afterEach(() => {
  cleanup()
  resetSpawnUi()
})

// ---------------------------------------------------------------------------
// Helpers in graphGroups.ts
// ---------------------------------------------------------------------------

describe('graphGroups', () => {
  it('lists Output Groups in file order with their primary ticker', () => {
    const g = groupsOf(pairGraph())
    expect(g.map(x => [x.name, x.direction, x.symbol, x.interval, x.weight])).toEqual([
      ['long_leg', 'long', 'AAPL', '1d', 1],
      ['short_leg', 'short', 'MSFT', '1d', 1],
    ])
  })

  it('builds the implicit main group from root terminals when there is no Output Group', () => {
    const g = groupsOf(graphOf([
      node('t1', 'ticker', 'spy', { symbol: 'SPY', interval: '1d', prefix: 'spy_' }),
      node('t2', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1h' }),
      node('e1', 'entry', 'entry', {}),
    ]))
    expect(g).toEqual([{ name: 'main', nodeId: null, direction: null, symbol: 'AAPL', interval: '1h', weight: 1, implicit: true }])
    expect(groupsOf(graphOf([node('t1', 'ticker', 'aapl', { symbol: 'AAPL' })]))).toEqual([])
  })

  it('splits capital by weight, formats and parses capital', () => {
    expect(splitCapital(10000, [{ weight: 1 }, { weight: 3 }])).toEqual([2500, 7500])
    expect(formatCapital(10000)).toBe('10 000')
    expect(formatCapital(1234567.5)).toBe('1 234 567.5')
    expect(parseCapital('5 000')).toBe(5000)
    expect(parseCapital('2500,5')).toBe(2500.5)
    expect(Number.isNaN(parseCapital('abc'))).toBe(true)
    expect(Number.isNaN(parseCapital(''))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// S34 Spawn dialog
// ---------------------------------------------------------------------------

describe('SpawnBotsDialog (S34)', () => {
  it('renders one row per group with the weighted capital split and the footer total', () => {
    render(<SpawnBotsDialog {...dialogProps()} />)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByTestId('nb-spawn-row-long_leg')).toBeTruthy()
    expect(screen.getByTestId('nb-spawn-row-short_leg')).toBeTruthy()
    const caps = [screen.getByLabelText('Capital for long_leg'), screen.getByLabelText('Capital for short_leg')] as HTMLInputElement[]
    expect(caps.map(c => c.value)).toEqual(['5 000', '5 000'])
    expect(screen.getByTestId('nb-spawn-total').textContent).toBe('2 bots · 10 000 total capital')
    expect(primary().textContent).toBe('Create 2 bots')
    expect(screen.getByText('Every bot is created stopped. Start them from the Trading tab when you are ready.')).toBeTruthy()
    // The header row is a real table with column headers.
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toEqual(['Group', 'Bot name', 'Capital', 'Broker', 'Data source', 'Interval'])
  })

  it('the implicit main group leg carries the sidebar direction; an explicit group leg never does (D7)', async () => {
    post.mockResolvedValue(spawnOk(1))
    const implicit = graphOf([
      node('t1', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }),
      node('e1', 'entry', 'entry', {}),
    ])
    const props = dialogProps({ groups: groupsOf(implicit), implicitDirection: 'short' })
    render(<SpawnBotsDialog {...props} />)
    fireEvent.click(primary())
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    const body = post.mock.calls[0][1] as { legs: Record<string, unknown>[] }
    expect(body.legs).toHaveLength(1)
    expect(body.legs[0]).toMatchObject({ group: 'main', direction: 'short' })
    cleanup()
    post.mockClear()
    const props2 = dialogProps({ implicitDirection: 'short' })
    render(<SpawnBotsDialog {...props2} />)
    fireEvent.click(primary())
    await waitFor(() => expect(props2.onCreated).toHaveBeenCalled())
    for (const leg of (post.mock.calls[0][1] as { legs: Record<string, unknown>[] }).legs) {
      expect('direction' in leg, String(leg.group)).toBe(false)
    }
  })

  it('capital inputs are text fields with a decimal keyboard (F278)', () => {
    render(<SpawnBotsDialog {...dialogProps()} />)
    const cap = screen.getByLabelText('Capital for long_leg') as HTMLInputElement
    expect(cap.getAttribute('type')).toBe('text')
    expect(cap.getAttribute('inputmode')).toBe('decimal')
  })

  it('sends one request with every checked leg, in the contract shape, asking for nothing to start', async () => {
    post.mockResolvedValue(spawnOk(1))
    const props = dialogProps()
    render(<SpawnBotsDialog {...props} />)
    fireEvent.click(screen.getByLabelText('Include short_leg'))
    expect(primary().textContent).toBe('Create 1 bot')
    expect(screen.getByTestId('nb-spawn-total').textContent).toBe('1 bot · 5 000 total capital')
    fireEvent.click(primary())
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(post).toHaveBeenCalledTimes(1)
    const [url, body] = post.mock.calls[0]
    expect(url).toBe('/api/graphs/g_pair/spawn')
    expect(body).toEqual({
      rev: 7,
      legs: [{
        group: 'long_leg',
        allocated_capital: 5000,
        broker: 'alpaca',
        data_source: 'alpaca-iex',
        interval_override: null,
        strategy_name: null,
      }],
    })
    // No graph JSON and no start flag anywhere in the request.
    const text = JSON.stringify(body)
    expect(text).not.toMatch(/"graph"|running|start/)
  })

  it('sends edited names, capital, broker, source and interval per leg', async () => {
    post.mockResolvedValue(spawnOk())
    const props = dialogProps()
    render(<SpawnBotsDialog {...props} />)
    fireEvent.change(screen.getByLabelText('Bot name for long_leg'), { target: { value: 'my long' } })
    fireEvent.change(screen.getByLabelText('Capital for long_leg'), { target: { value: '2 500' } })
    fireEvent.change(screen.getByLabelText('Broker for short_leg'), { target: { value: 'ibkr' } })
    fireEvent.change(screen.getByLabelText('Interval for short_leg'), { target: { value: '15m' } })
    // No auto-balance: the other leg keeps its 5 000; the total follows.
    expect((screen.getByLabelText('Capital for short_leg') as HTMLInputElement).value).toBe('5 000')
    expect(screen.getByTestId('nb-spawn-total').textContent).toBe('2 bots · 7 500 total capital')
    // The broker change moved the untouched source to the broker's default.
    expect((screen.getByLabelText('Data source for short_leg') as HTMLSelectElement).value).toBe('ibkr')
    act(() => { fireEvent.keyDown(screen.getByLabelText('Bot name for long_leg'), { key: 'Enter', metaKey: true }) })
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect((post.mock.calls[0][1] as { legs: unknown[] }).legs).toEqual([
      { group: 'long_leg', allocated_capital: 2500, broker: 'alpaca', data_source: 'alpaca-iex', interval_override: null, strategy_name: 'my long' },
      { group: 'short_leg', allocated_capital: 5000, broker: 'ibkr', data_source: 'ibkr', interval_override: '15m', strategy_name: null },
    ])
    // The browser remembers the broker and source, never capital or names.
    await waitFor(() => expect(localStorage.getItem('nb.spawn.broker')).toBe('alpaca'))
    expect(localStorage.getItem('nb.spawn.source')).toBe('alpaca-iex')
  })

  it('a touched data source stays when the broker changes', () => {
    render(<SpawnBotsDialog {...dialogProps()} />)
    fireEvent.change(screen.getByLabelText('Data source for long_leg'), { target: { value: 'yahoo' } })
    fireEvent.change(screen.getByLabelText('Broker for long_leg'), { target: { value: 'ibkr' } })
    expect((screen.getByLabelText('Data source for long_leg') as HTMLSelectElement).value).toBe('yahoo')
  })

  it('disables the primary for no legs and for a bad capital', () => {
    render(<SpawnBotsDialog {...dialogProps()} />)
    fireEvent.change(screen.getByLabelText('Capital for long_leg'), { target: { value: '0' } })
    expect(primary().disabled).toBe(true)
    expect(screen.getByTestId('nb-spawn-capital-hint').textContent).toBe('Capital must be a number above 0.')
    expect(screen.getByLabelText('Capital for long_leg').getAttribute('aria-invalid')).toBe('true')
    fireEvent.click(screen.getByLabelText('Include long_leg'))
    fireEvent.click(screen.getByLabelText('Include short_leg'))
    expect(primary().disabled).toBe(true)
    expect(primary().getAttribute('title')).toBe('Pick at least one group')
  })

  it('with unsaved edits the table is disabled and the Save now bar renders', () => {
    const props = dialogProps({ dirty: true })
    render(<SpawnBotsDialog {...props} />)
    expect(screen.getByTestId('nb-spawn-save-bar').textContent).toContain('Save the graph first. Bots pin a saved revision.')
    expect((screen.getByLabelText('Capital for long_leg') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Include long_leg') as HTMLInputElement).disabled).toBe(true)
    expect(primary().disabled).toBe(true)
    fireEvent.click(screen.getByTestId('nb-spawn-save-now'))
    expect(props.onSaveNow).toHaveBeenCalled()
  })

  it('a graph never saved says "Save the graph first."', () => {
    render(<SpawnBotsDialog {...dialogProps({ graphId: null, rev: null })} />)
    expect(screen.getByTestId('nb-spawn-save-bar').textContent).toContain('Save the graph first.')
    expect(primary().disabled).toBe(true)
  })

  it('validation errors block the primary and offer the diagnostics', () => {
    const props = dialogProps({ errorCount: 2 })
    render(<SpawnBotsDialog {...props} />)
    expect(screen.getByTestId('nb-spawn-errors-bar').textContent).toContain('Fix 2 errors before spawning.')
    expect(primary().disabled).toBe(true)
    fireEvent.click(screen.getByText('Show diagnostics'))
    expect(props.onShowDiagnostics).toHaveBeenCalled()
  })

  it('a graph with no group says there is nothing to spawn', () => {
    render(<SpawnBotsDialog {...dialogProps({ groups: [] })} />)
    expect(screen.getByTestId('nb-spawn-empty').textContent).toBe('This graph has no Output Group, so there is nothing to spawn.')
    expect(primary().disabled).toBe(true)
  })

  it('hints at two legs on the same symbol and direction but leaves the server to refuse', () => {
    const g = graphOf([
      node('t1', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }),
      node('g1', 'output_group', 'long_leg', { direction: 'long', ticker: '/aapl' }),
      node('g2', 'output_group', 'long_leg_2', { direction: 'long', ticker: '/aapl' }),
    ])
    render(<SpawnBotsDialog {...dialogProps({ groups: groupsOf(g) })} />)
    expect(screen.getByTestId('nb-spawn-dup-hint').textContent).toBe('long_leg and long_leg_2 trade AAPL long. The server refuses this.')
    expect(screen.getByTestId('nb-spawn-row-long_leg').getAttribute('data-marked')).toBe('true')
    expect(primary().disabled).toBe(false)
  })

  it('a 409 rev_conflict shows the plain copy and Reload graph', async () => {
    post.mockRejectedValue(httpError(409, { code: 'rev_conflict', current_rev: 8 }))
    const props = dialogProps()
    render(<SpawnBotsDialog {...props} />)
    fireEvent.click(primary())
    const bar = await screen.findByTestId('nb-spawn-error')
    expect(bar.getAttribute('role')).toBe('alert')
    expect(bar.textContent).toContain('The graph was saved elsewhere (rev 8). Reload it, then spawn again.')
    fireEvent.click(screen.getByTestId('nb-spawn-reload'))
    await waitFor(() => expect(props.onReload).toHaveBeenCalled())
    expect(props.onCreated).not.toHaveBeenCalled()
  })

  it('400 codes show plain copy and mark the named rows', async () => {
    post.mockRejectedValueOnce(httpError(400, { code: 'group_unknown', message: 'No group short_leg in rev 7.', diagnostics: [] }))
    render(<SpawnBotsDialog {...dialogProps()} />)
    fireEvent.click(primary())
    expect((await screen.findByTestId('nb-spawn-error')).textContent).toContain('No group short_leg in rev 7.')
    expect(screen.getByTestId('nb-spawn-row-short_leg').getAttribute('data-marked')).toBe('true')
    expect(screen.getByTestId('nb-spawn-row-long_leg').getAttribute('data-marked')).toBeNull()

    post.mockRejectedValueOnce(httpError(400, { code: 'code_disabled', message: 'x' }))
    fireEvent.click(primary())
    await waitFor(() => expect(screen.getByTestId('nb-spawn-error').textContent).toContain(
      'Code nodes are disabled on this server, so this graph cannot run as a bot.'))

    post.mockRejectedValueOnce(httpError(400, { code: 'graph_invalid', message: 'bad', diagnostics: [{ code: 'x' }] }))
    fireEvent.click(primary())
    await waitFor(() => expect(screen.getByTestId('nb-spawn-error').textContent).toContain('The graph has errors.'))
    expect(within(screen.getByTestId('nb-spawn-error')).getByText('Show diagnostics')).toBeTruthy()
  })

  it('a network failure offers Retry', async () => {
    post.mockRejectedValueOnce(new Error('Network Error'))
    post.mockResolvedValueOnce(spawnOk())
    const props = dialogProps()
    render(<SpawnBotsDialog {...props} />)
    fireEvent.click(primary())
    expect((await screen.findByTestId('nb-spawn-error')).textContent).toContain('Could not reach the server.')
    fireEvent.click(screen.getByTestId('nb-spawn-retry'))
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('Esc is ignored while the request is out', async () => {
    let resolve!: (v: unknown) => void
    post.mockReturnValue(new Promise(r => { resolve = r }))
    const props = dialogProps()
    render(<SpawnBotsDialog {...props} />)
    fireEvent.click(primary())
    await waitFor(() => expect(primary().textContent).toContain('Creating…'))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(props.onClose).not.toHaveBeenCalled()
    await act(async () => { resolve(spawnOk()) })
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
  })

  it('graphActionError reads the server shape', () => {
    expect(graphActionError(httpError(409, { code: 'rev_conflict', current_rev: 9 }))).toMatchObject({ kind: 'http', status: 409, code: 'rev_conflict', currentRev: 9 })
    expect(graphActionError(httpError(400, 'plain'))).toMatchObject({ code: null, message: 'plain' })
    expect(graphActionError(new Error('x')).kind).toBe('network')
  })
})

describe('Spawn dialog in the builder (slot hosts)', () => {
  function loadPair(dirty = false) {
    act(() => {
      useNodeBuilderStore.getState().openGraph(pairGraph(), { id: 'g_pair', rev: 7, name: 'pair_aapl_msft' })
      if (dirty) useNodeBuilderStore.setState({ dirty: true })
    })
  }

  afterEach(() => {
    act(() => { useNodeBuilderStore.setState({ graph: null, graphMeta: null, dirty: false }) })
  })

  it('a 201 closes the dialog and shows the toast with Open Trading', async () => {
    post.mockResolvedValue(spawnOk())
    loadPair()
    render(<><SpawnBotsDialogHost /><SpawnToastHost /></>)
    expect(screen.queryByRole('dialog')).toBeNull()
    act(() => openSpawnDialog())
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByRole('dialog').textContent).toContain('Spawn bots from pair_aapl_msft')
    expect(screen.getByTestId('nb-spawn-rev').textContent).toBe('rev 7')
    fireEvent.click(primary())
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const toast = screen.getByTestId('nb-spawn-toast')
    expect(toast.getAttribute('role')).toBe('status')
    expect(toast.textContent).toContain('Created 2 stopped bots.')
    const seen: unknown[] = []
    const onOpen = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_TRADING_EVENT, onOpen)
    fireEvent.click(within(toast).getByText('Open Trading'))
    window.removeEventListener(OPEN_TRADING_EVENT, onOpen)
    expect(seen).toEqual([{ botId: 'b_1' }])
    // Spawn is the only request: nothing started a bot.
    expect(post.mock.calls.map(c => c[0])).toEqual(['/api/graphs/g_pair/spawn'])
  })

  it('a dirty editor graph opens the dialog with the Save now bar', () => {
    loadPair(true)
    render(<SpawnBotsDialogHost />)
    act(() => openSpawnDialog())
    expect(screen.getByTestId('nb-spawn-save-bar')).toBeTruthy()
    act(() => closeSpawnDialog())
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('the bot card Spawn… request opens the dialog once that graph is loaded', async () => {
    const remove = listenForSpawnRequests()
    render(<SpawnBotsDialogHost />)
    act(() => { window.dispatchEvent(new CustomEvent(OPEN_GRAPH_EVENT, { detail: { graphId: 'g_pair', group: 'long_leg', spawn: true } })) })
    expect(screen.queryByRole('dialog')).toBeNull()
    loadPair()
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    remove()
  })
})

// ---------------------------------------------------------------------------
// S35 BotCard graph line
// ---------------------------------------------------------------------------

function summary(over: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_id: 'b1',
    strategy_name: 'pair_aapl_msft ▸ long_leg',
    symbol: 'AAPL',
    interval: '15m',
    allocated_capital: 5000,
    status: 'stopped',
    trades_count: 0,
    total_pnl: 0,
    backtest_summary: null,
    has_position: false,
    direction: 'long',
    broker: 'alpaca',
    kind: 'graph',
    graph_id: 'g_pair',
    graph_name: 'pair_aapl_msft',
    graph_group: 'long_leg',
    graph_rev: 7,
    graph_latest_rev: 9,
    ...over,
  }
}

function card(s: BotSummary) {
  return render(
    <BotCard
      summary={s}
      onStart={vi.fn()} onStop={vi.fn()} onBacktest={vi.fn()} onDelete={vi.fn()}
      onManualBuy={vi.fn()} onUpdate={vi.fn()} onResetPnl={vi.fn()}
      adaptiveInterval={ms => ms}
    />,
  )
}

function graphFetches(): unknown[] {
  return get.mock.calls.filter(c => typeof c[0] === 'string' && (c[0] as string).startsWith('/api/graphs'))
}

describe('BotCard graph line (S35)', () => {
  it('renders graph, group and rev from the summary and offers the newer rev without fetching a graph', () => {
    card(summary())
    const line = screen.getByTestId('botcard-graph-line')
    expect(line.textContent?.replace(/\s+/g, ' ')).toContain('pair_aapl_msft ▸ long_leg @ rev 7')
    expect(screen.getByRole('button', { name: 'Update to rev 9' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open graph pair_aapl_msft at group long_leg' })).toBeTruthy()
    expect(graphFetches()).toEqual([])
  })

  it('in position: the button is disabled (still focusable) with the reason', () => {
    card(summary({ has_position: true }))
    const btn = screen.getByTestId('botcard-graph-update')
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    expect(btn.getAttribute('title')).toBe('Close the position first')
    expect((btn as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(btn)
    expect(post).not.toHaveBeenCalled()
    expect(screen.getByText('rev 9 available')).toBeTruthy()
  })

  it('no button when the bot runs the latest rev', () => {
    card(summary({ graph_latest_rev: 7 }))
    expect(screen.queryByTestId('botcard-graph-update')).toBeNull()
  })

  it('graph deleted: plain name, "(graph deleted)", no button and no link', () => {
    card(summary({ graph_latest_rev: null, graph_name: null }))
    const line = screen.getByTestId('botcard-graph-line')
    expect(line.textContent).toContain('(graph deleted)')
    expect(screen.queryByTestId('botcard-graph-update')).toBeNull()
    expect(within(line).queryByRole('link')).toBeNull()
  })

  it('a legacy graph bot without graph_id says it has no source graph', () => {
    card(summary({ graph_id: null, graph_name: null, graph_latest_rev: null, graph_rev: null }))
    expect(screen.getByTestId('botcard-graph-line').textContent).toBe('graph snapshot · no source graph')
  })

  it('a rule bot renders no graph line', () => {
    card(summary({ kind: 'rule' }))
    expect(screen.queryByTestId('botcard-graph-line')).toBeNull()
  })

  it('updates a stopped bot through graph_update and shows the new rev', async () => {
    post.mockResolvedValue({ data: { bot_id: 'b1', graph_rev: 9 } })
    card(summary())
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    await waitFor(() => expect(screen.getByTestId('botcard-graph-ok').textContent).toBe('long_leg updated to rev 9'))
    expect(post).toHaveBeenCalledWith('/api/bots/b1/graph_update', { graph_id: 'g_pair', rev: 9 })
    expect(screen.getByTestId('botcard-graph-line').textContent).toContain('@ rev 9')
    expect(screen.queryByTestId('botcard-graph-update')).toBeNull()
    // Never the generic PATCH, never a start, never a graph fetch.
    expect(vi.mocked(api.patch)).not.toHaveBeenCalled()
    expect(post.mock.calls.map(c => c[0])).toEqual(['/api/bots/b1/graph_update'])
    expect(graphFetches()).toEqual([])
  })

  it('a running bot asks first', async () => {
    post.mockResolvedValue({ data: { bot_id: 'b1', graph_rev: 9 } })
    card(summary({ status: 'running' }))
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    expect(post).not.toHaveBeenCalled()
    expect(screen.getByText('Update while running? The bot uses the new rules on its next tick.')).toBeTruthy()
    fireEvent.click(screen.getByTestId('botcard-graph-confirm'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
  })

  it('a 400 symbol_changed hides the button and suggests spawning', async () => {
    post.mockRejectedValue(httpError(400, { code: 'symbol_changed' }))
    card(summary())
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    const err = await screen.findByTestId('botcard-graph-error')
    expect(err.getAttribute('role')).toBe('alert')
    expect(err.textContent).toContain('In rev 9 this group trades a different symbol. Spawn a new bot instead.')
    expect(screen.queryByTestId('botcard-graph-update')).toBeNull()
    const seen: unknown[] = []
    const onOpen = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_GRAPH_EVENT, onOpen)
    fireEvent.click(within(err).getByText('Spawn…'))
    window.removeEventListener(OPEN_GRAPH_EVENT, onOpen)
    expect(seen).toEqual([{ graphId: 'g_pair', group: 'long_leg', spawn: true }])
  })

  it('a 400 interval_changed hides the button and suggests spawning', async () => {
    post.mockRejectedValue(httpError(400, { code: 'interval_changed', message: 'In rev 9 group long_leg runs on 1h.' }))
    card(summary())
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    const err = await screen.findByTestId('botcard-graph-error')
    expect(err.textContent).toContain('In rev 9 this group runs on another interval. Spawn a new bot instead.')
    expect(screen.queryByTestId('botcard-graph-update')).toBeNull()
    const seen: unknown[] = []
    const onOpen = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_GRAPH_EVENT, onOpen)
    fireEvent.click(within(err).getByText('Spawn…'))
    window.removeEventListener(OPEN_GRAPH_EVENT, onOpen)
    expect(seen).toEqual([{ graphId: 'g_pair', group: 'long_leg', spawn: true }])
  })

  it('a 503 broker_unavailable asks to try again and keeps the button', async () => {
    post.mockRejectedValueOnce(httpError(503, { code: 'broker_unavailable', message: 'x' }))
    card(summary())
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    expect((await screen.findByTestId('botcard-graph-error')).textContent).toBe('Could not check the broker for an open position. Try again.')
    expect(screen.getByRole('button', { name: 'Update to rev 9' })).toBeTruthy()
  })

  it('409 answers: in_position and rev_conflict (the button relabels)', async () => {
    post.mockRejectedValueOnce(httpError(409, { code: 'in_position' }))
    card(summary())
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    expect((await screen.findByTestId('botcard-graph-error')).textContent).toBe('Close the position first.')
    post.mockRejectedValueOnce(httpError(409, { code: 'rev_conflict', current_rev: 10 }))
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    await waitFor(() => expect(screen.getByTestId('botcard-graph-error').textContent).toBe('The graph changed again (rev 10). Try once more.'))
    expect(screen.getByRole('button', { name: 'Update to rev 10' })).toBeTruthy()
  })

  it('direction_changed, group_missing and graph_invalid copy', async () => {
    post.mockRejectedValueOnce(httpError(400, { code: 'graph_invalid' }))
    card(summary())
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    expect((await screen.findByTestId('botcard-graph-error')).textContent).toContain('Rev 9 has errors.Fix them in the node builder.')
    post.mockRejectedValueOnce(httpError(400, { code: 'group_missing' }))
    fireEvent.click(screen.getByRole('button', { name: 'Update to rev 9' }))
    await waitFor(() => expect(screen.getByTestId('botcard-graph-error').textContent).toBe('Group long_leg no longer exists in rev 9.'))
    expect(screen.queryByTestId('botcard-graph-update')).toBeNull()
  })

  it('shows SWITCH for a regime_switch bot', () => {
    card(summary({ graph_direction_mode: 'regime_switch' }))
    expect(within(screen.getByTestId('botcard-graph-line')).getByText('SWITCH')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// S36 AddBotBar group selector
// ---------------------------------------------------------------------------

describe('AddBotBar group selector (S36)', () => {
  const fund = { bot_fund: 10000, allocated: 0, available: 10000 }

  function listItem(id: string, name: string, rev = 4) {
    return { id, rev, name, description: '', updated_at: '2026-10-01T10:00:00Z', node_count: 4, groups: [] }
  }

  function mockGraphs(graph: Graph, id = 'g_pair', name = 'pair_aapl_msft') {
    get.mockImplementation(async (url: string) => {
      if (url === '/api/graphs') return { data: { graphs: [listItem(id, name)] } }
      if (url === `/api/graphs/${id}`) {
        return { data: { id, rev: 4, name, description: '', created_at: 'c', updated_at: 'u', graph } }
      }
      throw new Error(`unexpected GET ${url}`)
    })
  }

  async function pickGraph(id = 'g_pair') {
    fireEvent.click(screen.getByLabelText('Graph', { selector: 'input[type="radio"]' }))
    const select = await screen.findByTestId('addbot-graph-select')
    await waitFor(() => expect((select as HTMLSelectElement).disabled).toBe(false))
    fireEvent.change(select, { target: { value: id } })
  }

  it('a two-group graph shows the groups in file order and posts graph_group without graph JSON', async () => {
    mockGraphs(pairGraph())
    const onAdd = vi.fn().mockResolvedValue(undefined)
    render(<AddBotBar fund={fund} onAdd={onAdd} />)
    await pickGraph()
    // The `loading…` select comes first, then the real one.
    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Output group' }) as HTMLSelectElement).disabled).toBe(false))
    const groupSelect = screen.getByRole('combobox', { name: 'Output group' })
    expect(Array.from((groupSelect as HTMLSelectElement).options).map(o => o.textContent)).toEqual([
      'long_leg · LONG · AAPL 1d',
      'short_leg · SHORT · MSFT 1d',
    ])
    expect(screen.getByText('one bot per group')).toBeTruthy()
    // First group preselected: the symbol comes from it, read-only.
    const sym = screen.getByTestId('addbot-symbol') as HTMLInputElement
    expect(sym.value).toBe('AAPL')
    expect(sym.readOnly).toBe(true)
    fireEvent.change(groupSelect, { target: { value: 'short_leg' } })
    expect(sym.value).toBe('MSFT')
    expect((screen.getByTestId('addbot-direction') as HTMLSelectElement).value).toBe('short')
    fireEvent.change(screen.getByPlaceholderText('Allocation $'), { target: { value: '2500' } })
    fireEvent.click(screen.getByTestId('addbot-add'))
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1))
    const body = onAdd.mock.calls[0][0]
    expect(body).toMatchObject({
      kind: 'graph', graph_id: 'g_pair', graph_rev: 4, graph_group: 'short_leg',
      symbol: 'MSFT', direction: 'short', allocated_capital: 2500,
    })
    expect('graph' in body).toBe(false)
    // The graph was fetched once, for its groups.
    expect(get.mock.calls.filter(c => c[0] === '/api/graphs/g_pair')).toHaveLength(1)
  })

  it('a one-group graph hides the selector and fills the symbol from the group', async () => {
    mockGraphs(oneGroupGraph(), 'g_one', 'solo_tsla')
    render(<AddBotBar fund={fund} onAdd={vi.fn()} />)
    await pickGraph('g_one')
    await waitFor(() => expect((screen.getByTestId('addbot-symbol') as HTMLInputElement).value).toBe('TSLA'))
    expect(screen.queryByRole('combobox', { name: 'Output group' })).toBeNull()
  })

  it('a failed graph fetch shows the detail and Retry', async () => {
    get.mockImplementation(async (url: string) => {
      if (url === '/api/graphs') return { data: { graphs: [listItem('g_pair', 'pair_aapl_msft')] } }
      throw { response: { status: 500, data: { detail: 'disk on fire' } } }
    })
    render(<AddBotBar fund={fund} onAdd={vi.fn()} />)
    await pickGraph()
    const err = await screen.findByTestId('addbot-graph-load-error')
    expect(err.textContent).toContain('disk on fire')
    get.mockImplementation(async (url: string) => {
      if (url === '/api/graphs') return { data: { graphs: [listItem('g_pair', 'pair_aapl_msft')] } }
      return { data: { id: 'g_pair', rev: 4, name: 'pair_aapl_msft', description: '', created_at: 'c', updated_at: 'u', graph: pairGraph() } }
    })
    fireEvent.click(within(err).getByText('Retry'))
    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Output group' }) as HTMLSelectElement).options).toHaveLength(2))
  })

  it('a 400 with diagnostics shows the first message and keeps Create off for that rev', async () => {
    mockGraphs(pairGraph())
    const onAdd = vi.fn().mockRejectedValue({ response: { status: 400, data: { detail: { code: 'graph_invalid', diagnostics: [{ message: 'Exit is not wired.' }] } } } })
    render(<AddBotBar fund={fund} onAdd={onAdd} />)
    await pickGraph()
    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Output group' }) as HTMLSelectElement).disabled).toBe(false))
    fireEvent.change(screen.getByPlaceholderText('Allocation $'), { target: { value: '1000' } })
    fireEvent.click(screen.getByTestId('addbot-add'))
    expect(await screen.findByText('Exit is not wired.')).toBeTruthy()
    const add = screen.getByTestId('addbot-add') as HTMLButtonElement
    expect(add.disabled).toBe(true)
    expect(add.getAttribute('title')).toBe("Fix the graph's errors first")
  })
})
