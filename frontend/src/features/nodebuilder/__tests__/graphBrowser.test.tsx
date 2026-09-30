/**
 * Graph Browser tests (F435 W1 item 1.F, surface S02): search filters the
 * list, arrow keys and Enter open a graph into the store, the empty state
 * and footer note, delete only after the confirm, and errors stay inside
 * the dialog.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { emptyGraph } from '../../../api/nodebuilder'

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

import {
  createGraph,
  deleteGraph,
  getGraph,
  listGraphs,
  NameTakenError,
  RevConflictError,
  type GraphListItem,
} from '../../../api/graphs'
import GraphBrowser from '../GraphBrowser'
import { filterGraphs, sortGraphs } from '../graphText'
import { useLayoutEffect } from 'react'
import { useGraphSession, type GraphSession } from '../useGraphSession'
import { useNodeBuilderStore } from '../store'
import { clearNotices } from '../notices'

function item(id: string, name: string, updated: string, nodes = 3, description = ''): GraphListItem {
  return { id, rev: 2, name, description, updated_at: updated, node_count: nodes, groups: ['main'] }
}

const three = [
  item('g_a', 'alpha rsi', '2026-09-28T10:00:00Z', 4),
  item('g_b', 'beta breakout', '2026-09-30T10:00:00Z', 7, 'trend follower'),
  item('g_c', 'gamma', '2026-09-29T10:00:00Z', 1),
]

function envelope(id: string, name: string) {
  return { id, rev: 2, name, description: '', created_at: 'c', updated_at: 'u', graph: emptyGraph() }
}

// The hook's latest return value, for the test to call actions on.
const holder: { current: GraphSession | null } = { current: null }
function Harness() {
  const s = useGraphSession()
  useLayoutEffect(() => {
    holder.current = s
  })
  return <>{s.element}</>
}

function renderBrowser(overrides: Partial<Parameters<typeof GraphBrowser>[0]> = {}) {
  const props = {
    openGraphId: null,
    onClose: vi.fn(),
    onOpen: vi.fn().mockResolvedValue(undefined),
    onDuplicate: vi.fn().mockResolvedValue(undefined),
    onRename: vi.fn(),
    onNew: vi.fn(),
    onImport: vi.fn(),
    onDeleted: vi.fn(),
    ...overrides,
  }
  render(<GraphBrowser {...props} />)
  return props
}

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  vi.mocked(listGraphs).mockReset()
  vi.mocked(getGraph).mockReset()
  vi.mocked(deleteGraph).mockReset()
  vi.mocked(createGraph).mockReset()
  useNodeBuilderStore.getState().discardEdits()
})

afterEach(() => cleanup())

describe('sort and filter', () => {
  it('sorts by updated (newest first), name and nodes', () => {
    expect(sortGraphs(three, 'updated').map(g => g.id)).toEqual(['g_b', 'g_c', 'g_a'])
    expect(sortGraphs(three, 'name').map(g => g.id)).toEqual(['g_a', 'g_b', 'g_c'])
    expect(sortGraphs(three, 'nodes').map(g => g.id)).toEqual(['g_b', 'g_a', 'g_c'])
  })

  it('matches name and description, case-insensitive', () => {
    expect(filterGraphs(three, 'BETA').map(g => g.id)).toEqual(['g_b'])
    expect(filterGraphs(three, 'follower').map(g => g.id)).toEqual(['g_b'])
    expect(filterGraphs(three, '')).toHaveLength(3)
  })
})

describe('GraphBrowser', () => {
  it('filters to one row, then ArrowDown + Enter opens it into the store', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    vi.mocked(getGraph).mockResolvedValue(envelope('g_b', 'beta breakout'))
    render(<Harness />)
    act(() => holder.current!.openBrowser())
    await screen.findByTestId('nb-browser-row-g_a')
    const search = screen.getByTestId('nb-browser-search')
    expect(search).toHaveFocus()
    fireEvent.change(search, { target: { value: 'beta' } })
    expect(screen.queryByTestId('nb-browser-row-g_a')).toBeNull()
    expect(screen.getByTestId('nb-browser-row-g_b')).toBeInTheDocument()
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    expect(screen.getByTestId('nb-browser-row-g_b')).toHaveAttribute('aria-selected', 'true')
    await act(async () => {
      fireEvent.keyDown(search, { key: 'Enter' })
    })
    await waitFor(() => expect(useNodeBuilderStore.getState().graphMeta?.id).toBe('g_b'))
    expect(getGraph).toHaveBeenCalledWith('g_b')
    expect(screen.queryByTestId('nb-graph-browser')).toBeNull()
  })

  it('shows the empty state, and the footer note about the strategy list', async () => {
    vi.mocked(listGraphs).mockResolvedValue([])
    renderBrowser()
    const empty = await screen.findByTestId('nb-browser-empty')
    expect(empty).toHaveTextContent('No graphs yet.')
    expect(screen.getByTestId('nb-graph-browser')).toHaveTextContent('The rule strategy list does not show them.')
    // Empty: New graph is the primary, no Open button.
    expect(screen.queryByTestId('nb-browser-open')).toBeNull()
    expect(screen.getByTestId('nb-browser-new').className).toContain('nb-btn--primary')
  })

  it('marks the open graph and says when a search matches nothing', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    renderBrowser({ openGraphId: 'g_c' })
    const row = await screen.findByTestId('nb-browser-row-g_c')
    // The pill is visual; screen readers get hidden text, not a label on a generic span (UX-19).
    expect(row).toHaveTextContent('(currently open)')
    expect(within(row).getByText('open')).toHaveAttribute('aria-hidden', 'true')
    fireEvent.change(screen.getByTestId('nb-browser-search'), { target: { value: 'zzz' } })
    expect(screen.getByText('No graphs match "zzz".')).toBeInTheDocument()
  })

  it('calls deleteGraph only after the confirm dialog Delete', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    vi.mocked(deleteGraph).mockResolvedValue(undefined)
    const props = renderBrowser()
    await screen.findByTestId('nb-browser-row-g_a')
    fireEvent.click(screen.getByTestId('nb-browser-more-g_a'))
    fireEvent.click(await screen.findByTestId('nb-browser-delete'))
    const confirm = await screen.findByTestId('nb-delete-dialog')
    expect(confirm).toHaveTextContent('Delete alpha rsi?')
    expect(deleteGraph).not.toHaveBeenCalled()
    await act(async () => fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' })))
    expect(deleteGraph).toHaveBeenCalledWith('g_a', 2)
    expect(props.onDeleted).toHaveBeenCalledWith(three[0])
  })

  it('keeps the row and says why when the delete hits a 409', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    vi.mocked(deleteGraph).mockRejectedValue(new RevConflictError(3))
    renderBrowser()
    const row = await screen.findByTestId('nb-browser-row-g_c')
    fireEvent.click(row)
    const list = screen.getByRole('listbox', { name: 'Graphs' })
    fireEvent.keyDown(list, { key: 'Delete' })
    const confirm = await screen.findByTestId('nb-delete-dialog')
    await act(async () => fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' })))
    expect(screen.getByTestId('nb-browser-banner')).toHaveTextContent('Could not delete "gamma": it changed on the server.')
    expect(screen.getByTestId('nb-browser-row-g_c')).toBeInTheDocument()
  })

  it('shows a load error with Retry', async () => {
    vi.mocked(listGraphs).mockRejectedValueOnce({ response: { status: 500, data: { detail: 'disk full' } } })
    renderBrowser()
    const err = await screen.findByTestId('nb-browser-error')
    expect(err).toHaveTextContent('Could not load graphs: disk full')
    vi.mocked(listGraphs).mockResolvedValueOnce(three)
    await act(async () => fireEvent.click(within(err).getByRole('button', { name: 'Retry' })))
    expect(await screen.findByTestId('nb-browser-row-g_a')).toBeInTheDocument()
  })

  it('keeps the dialog open with the error when opening fails', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    const onOpen = vi.fn().mockRejectedValue({ response: { status: 404, data: { detail: 'Graph not found' } } })
    const props = renderBrowser({ onOpen })
    fireEvent.doubleClick(await screen.findByTestId('nb-browser-row-g_a'))
    expect(await screen.findByTestId('nb-browser-banner')).toHaveTextContent('Graph not found')
    expect(props.onClose).not.toHaveBeenCalled()
    expect(screen.getByTestId('nb-graph-browser')).toBeInTheDocument()
  })

  it('Duplicate picks "<name> copy 2" when "<name> copy" is taken, and opens it', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    vi.mocked(createGraph)
      .mockRejectedValueOnce(new NameTakenError())
      .mockResolvedValueOnce(envelope('g_d', 'gamma copy 2'))
    render(<Harness />)
    act(() => holder.current!.openBrowser())
    const row = await screen.findByTestId('nb-browser-row-g_c')
    await act(async () => fireEvent.click(within(row).getByRole('button', { name: 'Duplicate' })))
    expect(createGraph).toHaveBeenNthCalledWith(1, { name: 'gamma copy', duplicate_of: 'g_c' })
    expect(createGraph).toHaveBeenNthCalledWith(2, { name: 'gamma copy 2', duplicate_of: 'g_c' })
    await waitFor(() => expect(useNodeBuilderStore.getState().graphMeta?.id).toBe('g_d'))
  })

  it('asks to save a dirty graph before opening another, and Cancel keeps the browser', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    const store = useNodeBuilderStore.getState()
    store.newGraph()
    store.commit('edit', g => ({ ...g, meta: { note: 'x' } }))
    render(<Harness />)
    act(() => holder.current!.openBrowser())
    fireEvent.doubleClick(await screen.findByTestId('nb-browser-row-g_a'))
    const ask = await screen.findByTestId('nb-save-changes-dialog')
    expect(ask).toHaveTextContent('Save changes to untitled?')
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByTestId('nb-save-changes-dialog')).toBeNull())
    expect(getGraph).not.toHaveBeenCalled()
    expect(screen.getByTestId('nb-graph-browser')).toBeInTheDocument()
  })
})

describe('GraphBrowser: W1 fix pass', () => {
  it('Enter and Space on a row button belong to the button, not the list (UX-04)', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    const props = renderBrowser()
    fireEvent.click(await screen.findByTestId('nb-browser-row-g_a'))
    const row = screen.getByTestId('nb-browser-row-g_a')
    const dup = within(row).getByRole('button', { name: 'Duplicate' })
    dup.focus()
    // Not prevented, so the button's own click still happens; the list does not open.
    expect(fireEvent.keyDown(dup, { key: 'Enter' })).toBe(true)
    expect(fireEvent.keyDown(dup, { key: ' ' })).toBe(true)
    expect(props.onOpen).not.toHaveBeenCalled()
    expect(screen.getByTestId('nb-browser-search')).toHaveValue('')
    expect(dup).toHaveFocus()
    // Arrows still move the selection, and focus goes back to the list.
    fireEvent.keyDown(dup, { key: 'ArrowDown' })
    expect(screen.getByRole('listbox')).toHaveFocus()
  })

  it('a refresh keeps the rows in place instead of a loading row (UX-05)', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    const props = {
      openGraphId: null, onClose: vi.fn(), onOpen: vi.fn().mockResolvedValue(undefined),
      onDuplicate: vi.fn().mockResolvedValue(undefined), onRename: vi.fn(), onNew: vi.fn(), onImport: vi.fn(), onDeleted: vi.fn(),
    }
    const { rerender } = render(<GraphBrowser {...props} refreshKey={0} />)
    const list = await screen.findByRole('listbox')
    act(() => list.focus())
    let resolve!: (v: GraphListItem[]) => void
    vi.mocked(listGraphs).mockReturnValue(new Promise(r => { resolve = r }))
    rerender(<GraphBrowser {...props} refreshKey={1} />)
    expect(screen.queryByText('Loading graphs…')).toBeNull()
    expect(screen.getByRole('listbox')).toBe(list)
    expect(list).toHaveFocus()
    await act(async () => resolve(three.slice(1)))
    expect(screen.queryByTestId('nb-browser-row-g_a')).toBeNull()
    expect(list).toHaveFocus()
  })

  it('the search field points at the list it drives (UX-19)', async () => {
    vi.mocked(listGraphs).mockResolvedValue(three)
    renderBrowser()
    const list = await screen.findByRole('listbox')
    const search = screen.getByTestId('nb-browser-search')
    expect(search).toHaveAttribute('aria-controls', list.id)
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    const active = search.getAttribute('aria-activedescendant')
    expect(active).toBeTruthy()
    expect(document.getElementById(active!)).toHaveAttribute('aria-selected', 'true')
  })
})
