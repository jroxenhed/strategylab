/**
 * AddBotBar graph picker (F435 W1 item 1.F, surface S06): graphs come from
 * the server (`listGraphs`), never from localStorage. Loading, empty and
 * error states are honest, Retry refetches, and Add bot waits for a pick.
 * No bot is started: `onAdd` is a mock.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import addBotBarSource from '../AddBotBar.tsx?raw'

vi.mock('../../../api/graphs', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/graphs')>()
  return { ...orig, listGraphs: vi.fn(), getGraph: vi.fn() }
})

import { getGraph, listGraphs, type GraphListItem } from '../../../api/graphs'
import AddBotBar from '../AddBotBar'

const fund = { bot_fund: 5000, allocated: 0, available: 5000 } as never

function item(id: string, name: string, updated: string, nodes: number): GraphListItem {
  return { id, rev: 3, name, description: '', updated_at: updated, node_count: nodes, groups: ['main'] }
}

const two = [item('g_old', 'old one', '2026-09-01T10:00:00Z', 4), item('g_new', 'new one', '2026-09-29T10:00:00Z', 1)]

async function pickGraphSource() {
  await act(async () => fireEvent.click(screen.getByLabelText('Graph', { selector: 'input' })))
}

function fillBar() {
  fireEvent.change(screen.getByLabelText('Ticker'), { target: { value: 'AAPL' } })
  fireEvent.change(screen.getByPlaceholderText('Allocation $'), { target: { value: '100' } })
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(listGraphs).mockReset()
  vi.mocked(getGraph).mockReset()
})

afterEach(() => cleanup())

describe('AddBotBar graph picker', () => {
  it('does not fetch graphs until the Graph source is picked', async () => {
    vi.mocked(listGraphs).mockResolvedValue(two)
    render(<AddBotBar fund={fund} onAdd={vi.fn()} />)
    expect(listGraphs).not.toHaveBeenCalled()
    await pickGraphSource()
    expect(listGraphs).toHaveBeenCalledTimes(1)
  })

  it('on a failed list: disabled select, Retry refetches', async () => {
    vi.mocked(listGraphs).mockRejectedValueOnce({ response: { status: 500, data: { detail: 'server down' } } })
    render(<AddBotBar fund={fund} onAdd={vi.fn()} />)
    await pickGraphSource()
    const select = screen.getByTestId('addbot-graph-select')
    await waitFor(() => expect(select).toHaveTextContent('Could not load graphs'))
    expect(select).toBeDisabled()
    const help = screen.getByTestId('addbot-graph-help')
    expect(help).toHaveTextContent('server down · Retry')
    vi.mocked(listGraphs).mockResolvedValueOnce(two)
    await act(async () => fireEvent.click(within(help).getByRole('button', { name: 'Retry' })))
    expect(listGraphs).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(select).not.toBeDisabled())
  })

  it('two graphs give two options plus the placeholder; Add bot waits for a pick', async () => {
    vi.mocked(listGraphs).mockResolvedValue(two)
    const onAdd = vi.fn().mockResolvedValue(undefined)
    render(<AddBotBar fund={fund} onAdd={onAdd} />)
    await pickGraphSource()
    fillBar()
    const select = screen.getByTestId('addbot-graph-select') as HTMLSelectElement
    await waitFor(() => expect(select.options).toHaveLength(3))
    // Placeholder first, then newest first.
    expect(Array.from(select.options).map(o => o.textContent)).toEqual([
      'Select a graph…',
      'new one · 1 node',
      'old one · 4 nodes',
    ])
    expect(select.options[0].disabled).toBe(true)
    const add = screen.getByTestId('addbot-add')
    expect(add).toBeDisabled()
    expect(add).toHaveAttribute('title', 'Select a graph')

    // S36: choosing a graph fetches it once to read its Output Groups.
    // This one has the implicit `main` group and no ticker, so the typed
    // symbol stays.
    const graph = {
      _version: 3, stream_schema: 1, readOnly: false, meta: {}, wires: [], annotations: { boxes: [], notes: [] },
      nodes: { e1: { id: 'e1', type: 'entry', name: 'entry', parent: null, params: {}, position: [0, 0], display: false, bypass: false } },
    }
    vi.mocked(getGraph).mockResolvedValue({
      id: 'g_old', rev: 3, name: 'old one', description: '', created_at: 'c', updated_at: 'u', graph: graph as never,
    })
    fireEvent.change(select, { target: { value: 'g_old' } })
    await waitFor(() => expect(add).not.toBeDisabled())
    const help = screen.getByTestId('addbot-graph-help')
    expect(help).toHaveTextContent('rev 3 · updated')
    // Only the error and empty texts are live; the ticking "updated N min ago" is not (UX-17).
    expect(help).not.toHaveAttribute('aria-live')
    expect(help.querySelector('[aria-live]')).not.toHaveTextContent('rev 3')

    await act(async () => fireEvent.click(add))
    expect(getGraph).toHaveBeenCalledTimes(1)
    expect(getGraph).toHaveBeenCalledWith('g_old')
    // The server loads the saved revision: id, rev and group, no graph JSON.
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'graph', graph_id: 'g_old', graph_rev: 3, graph_group: 'main', strategy_name: 'old one', symbol: 'AAPL',
    }))
    expect('graph' in onAdd.mock.calls[0][0]).toBe(false)
  })

  it('says so when there are no saved graphs', async () => {
    vi.mocked(listGraphs).mockResolvedValue([])
    render(<AddBotBar fund={fund} onAdd={vi.fn()} />)
    await pickGraphSource()
    const select = screen.getByTestId('addbot-graph-select')
    await waitFor(() => expect(select).toHaveTextContent('No saved graphs'))
    expect(select).toBeDisabled()
    expect(screen.getByTestId('addbot-graph-help')).toHaveTextContent('Build one in the Node Editor and save it.')
  })

  it('shows a load failure for the chosen graph and adds no bot', async () => {
    vi.mocked(listGraphs).mockResolvedValue(two)
    const onAdd = vi.fn()
    render(<AddBotBar fund={fund} onAdd={onAdd} />)
    await pickGraphSource()
    fillBar()
    const select = screen.getByTestId('addbot-graph-select')
    await waitFor(() => expect(select).not.toBeDisabled())
    vi.mocked(getGraph).mockRejectedValue({ response: { status: 404, data: { detail: 'Graph not found' } } })
    fireEvent.change(select, { target: { value: 'g_new' } })
    // S36: the fetch happens on choosing; its failure shows with Retry, and Add stays off.
    const err = await screen.findByTestId('addbot-graph-load-error')
    expect(err).toHaveTextContent('Graph not found · Retry')
    expect(screen.getByTestId('addbot-add')).toBeDisabled()
    await act(async () => fireEvent.click(screen.getByTestId('addbot-add')))
    expect(onAdd).not.toHaveBeenCalled()
  })

  it('the refresh button refetches the list', async () => {
    vi.mocked(listGraphs).mockResolvedValue(two)
    render(<AddBotBar fund={fund} onAdd={vi.fn()} />)
    await pickGraphSource()
    await waitFor(() => expect(screen.getByTestId('addbot-graph-refresh')).not.toBeDisabled())
    await act(async () => fireEvent.click(screen.getByTestId('addbot-graph-refresh')))
    expect(listGraphs).toHaveBeenCalledTimes(2)
  })

  it('no longer reads the legacy localStorage key', () => {
    const src = addBotBarSource
    expect(src).not.toContain('strategylab-saved-graphs')
    expect(src).not.toContain('parseSavedGraphs')
  })
})
