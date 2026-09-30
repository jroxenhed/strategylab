/**
 * Persistence UI flows (F435 W1 item 1.F): the S03 draft restore prompt,
 * the S04 conflict dialog (a 409 never overwrites), and Save on an
 * untitled graph (S01 name dialog). The graphs API is mocked; the store
 * is the real one.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

import { createGraph, getGraph, isGraphCorrupt, RevConflictError, saveGraph, type GraphEnvelope } from '../../../api/graphs'
import { useNodeBuilderStore } from '../store'
import { useLayoutEffect } from 'react'
import { useGraphSession, type GraphSession } from '../useGraphSession'
import NoticeStack from '../NoticeStack'
import { clearNotices } from '../notices'
import { graphsEqual, readDraft, saveDraft, setLastGraphId, DRAFT_INTERVAL_MS } from '../persistence'

function node(id: string, name: string, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type: 'rsi', name, parent: null, params: { period: 14 }, position: [0, 0], display: false, bypass: false, ...extra }
}

function graphWith(...nodes: GraphNode[]): Graph {
  return { ...emptyGraph(), nodes: Object.fromEntries(nodes.map(n => [n.id, n])) }
}

function envelope(id: string, rev: number, name: string, graph: Graph): GraphEnvelope {
  return { id, rev, name, description: '', created_at: '2026-09-30T10:00:00Z', updated_at: new Date().toISOString(), graph }
}

// The hook's latest return value, for the test to call actions on.
const holder: { current: GraphSession | null } = { current: null }
function Harness() {
  const s = useGraphSession()
  useLayoutEffect(() => {
    holder.current = s
  })
  return (
    <>
      <NoticeStack />
      {s.element}
    </>
  )
}

const st = () => useNodeBuilderStore.getState()
const serverGraph = graphWith(node('n_a', 'rsi'))

beforeEach(() => {
  localStorage.clear()
  clearNotices()
  vi.mocked(getGraph).mockReset()
  vi.mocked(saveGraph).mockReset()
  vi.mocked(createGraph).mockReset()
  st().discardEdits()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('S03 draft restore', () => {
  it('offers a newer draft on reload and restores it only when asked', async () => {
    // Edit a saved graph and let the autosave write the draft.
    vi.useFakeTimers()
    st().openGraph(serverGraph, { id: 'g_1', rev: 3, name: 'alpha' })
    const first = render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS))
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(20)
    first.unmount()
    vi.useRealTimers()

    // "Reload": nothing in the store, the last graph comes back from the server.
    st().discardEdits()
    setLastGraphId('g_1')
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 3, 'alpha', serverGraph))
    render(<Harness />)
    await act(async () => holder.current!.openLastGraph())
    const banner = await screen.findByTestId('nb-banner-draft_found')
    expect(banner).toHaveTextContent('A newer unsaved draft of alpha from')
    // Nothing restored yet: the server version is on screen.
    expect(st().graph?.nodes.n_a.params.period).toBe(14)
    expect(st().dirty).toBe(false)

    fireEvent.click(screen.getByTestId('nb-draft-restore'))
    expect(st().graph?.nodes.n_a.params.period).toBe(20)
    expect(st().dirty).toBe(true)
    expect(screen.queryByTestId('nb-banner-draft_found')).toBeNull()
    // Undo goes back to the server version.
    act(() => st().undo())
    expect(st().graph?.nodes.n_a.params.period).toBe(14)
  })

  it('Discard removes the draft key', async () => {
    saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: graphWith(node('n_a', 'rsi', { params: { period: 30 } })) })
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 3, 'alpha', serverGraph))
    setLastGraphId('g_1')
    render(<Harness />)
    await act(async () => holder.current!.openLastGraph())
    await screen.findByTestId('nb-banner-draft_found')
    fireEvent.click(screen.getByTestId('nb-draft-discard'))
    expect(localStorage.getItem('nb.draft.g_1')).toBeNull()
    expect(screen.queryByTestId('nb-banner-draft_found')).toBeNull()
    expect(st().graph?.nodes.n_a.params.period).toBe(14)
  })

  it('shows no banner for a content-equal draft', async () => {
    saveDraft({ graphId: 'g_1', rev: 2, name: 'alpha', graph: serverGraph })
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 3, 'alpha', serverGraph))
    setLastGraphId('g_1')
    render(<Harness />)
    await act(async () => holder.current!.openLastGraph())
    await waitFor(() => expect(st().graphMeta?.id).toBe('g_1'))
    expect(screen.queryByTestId('nb-banner-draft_found')).toBeNull()
  })

  it('names the newer server rev, and a restored stale draft goes to the conflict dialog on Save', async () => {
    saveDraft({ graphId: 'g_1', rev: 2, name: 'alpha', graph: graphWith(node('n_a', 'rsi', { params: { period: 30 } })) })
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 5, 'alpha', serverGraph))
    setLastGraphId('g_1')
    render(<Harness />)
    await act(async () => holder.current!.openLastGraph())
    const banner = await screen.findByTestId('nb-banner-draft_found')
    expect(banner).toHaveTextContent('the server has a newer version (rev 5)')
    fireEvent.click(screen.getByTestId('nb-draft-restore'))
    await act(async () => holder.current!.save())
    expect(saveGraph).not.toHaveBeenCalled()
    expect(await screen.findByText('Saved elsewhere')).toBeInTheDocument()
  })

  it('an edit while the prompt is up keeps the server version and drops the draft after 10 s', async () => {
    saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: graphWith(node('n_a', 'rsi', { params: { period: 30 } })) })
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 3, 'alpha', serverGraph))
    setLastGraphId('g_1')
    render(<Harness />)
    await act(async () => holder.current!.openLastGraph())
    await screen.findByTestId('nb-banner-draft_found')
    vi.useFakeTimers()
    act(() => st().updateNodeParams('n_a', { period: 15 }))
    expect(screen.getByTestId('nb-banner-draft_found')).toHaveTextContent('Draft kept')
    act(() => vi.advanceTimersByTime(10_000))
    expect(screen.queryByTestId('nb-banner-draft_found')).toBeNull()
    // The offered draft (period 30) is gone; the autosave holds the new edit instead.
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(15)
    expect(st().graph?.nodes.n_a.params.period).toBe(15)
  })

  it('a successful save removes the draft key', async () => {
    st().openGraph(serverGraph, { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: st().graph! })
    vi.mocked(saveGraph).mockResolvedValue(envelope('g_1', 4, 'alpha', st().graph!))
    await act(async () => holder.current!.save())
    expect(localStorage.getItem('nb.draft.g_1')).toBeNull()
    expect(st().dirty).toBe(false)
    expect(st().graphMeta).toEqual({ id: 'g_1', rev: 4, name: 'alpha' })
  })
})

describe('S04 save conflict', () => {
  const theirs = graphWith(node('n_a', 'rsi'), node('n_s', 'sma'))

  async function conflict() {
    st().openGraph(serverGraph, { id: 'g_1', rev: 12, name: 'alpha' })
    render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    vi.mocked(saveGraph).mockRejectedValueOnce(new RevConflictError(13))
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 13, 'alpha', theirs))
    await act(async () => holder.current!.save())
    await screen.findByTestId('nb-conflict-text')
  }

  it('shows both revs and never overwrites without Compare', async () => {
    await conflict()
    expect(screen.getByTestId('nb-conflict-text')).toHaveTextContent('(rev 13)')
    expect(screen.getByTestId('nb-conflict-text')).toHaveTextContent('based on rev 12')
    const overwrite = screen.getByTestId('nb-conflict-overwrite')
    expect(overwrite).toBeDisabled()
    expect(overwrite).toHaveAttribute('title', 'Open Compare first')
    expect(saveGraph).toHaveBeenCalledTimes(1)
    // Still dirty; the local edit is still on screen.
    expect(st().dirty).toBe(true)
    expect(st().graph?.nodes.n_a.params.period).toBe(20)

    fireEvent.click(screen.getByTestId('nb-conflict-compare'))
    expect(screen.getByTestId('nb-conflict-diff')).toHaveTextContent('+ 1 node added on the server: sma')
    expect(overwrite).not.toBeDisabled()
    const local = st().graph!
    vi.mocked(saveGraph).mockResolvedValueOnce(envelope('g_1', 14, 'alpha', local))
    await act(async () => fireEvent.click(overwrite))
    expect(saveGraph).toHaveBeenLastCalledWith('g_1', { rev: 13, graph: local })
    expect(st().dirty).toBe(false)
    expect(st().graphMeta?.rev).toBe(14)
    expect(screen.queryByText('Saved elsewhere')).toBeNull()
  })

  it('Reload theirs loads the server graph clean, and Cmd+Z brings the local one back', async () => {
    await conflict()
    const local = st().graph!
    fireEvent.click(screen.getByTestId('nb-conflict-reload'))
    expect(graphsEqual(st().graph, theirs)).toBe(true)
    expect(st().dirty).toBe(false)
    expect(st().graphMeta?.rev).toBe(13)
    act(() => st().undo())
    expect(st().graph).toBe(local)
    expect(st().dirty).toBe(true)
  })

  it('Save as copy creates "<name> copy" with the local graph', async () => {
    await conflict()
    const local = st().graph!
    fireEvent.click(screen.getByTestId('nb-conflict-copy'))
    const input = await screen.findByTestId('nb-name-input')
    expect(input).toHaveValue('alpha copy')
    vi.mocked(createGraph).mockResolvedValueOnce(envelope('g_2', 1, 'alpha copy', local))
    await act(async () => fireEvent.click(screen.getByTestId('nb-dialog-primary')))
    expect(createGraph).toHaveBeenCalledWith({ name: 'alpha copy', graph: local })
    expect(st().graphMeta).toEqual({ id: 'g_2', rev: 1, name: 'alpha copy' })
    expect(st().dirty).toBe(false)
  })

  it('Cancel keeps the graph dirty, and the next Save re-opens the dialog without a PUT', async () => {
    await conflict()
    fireEvent.click(screen.getByTestId('nb-dialog-close'))
    expect(screen.queryByText('Saved elsewhere')).toBeNull()
    expect(st().dirty).toBe(true)
    expect(screen.getByTestId('nb-banner-rev_conflict_pending')).toBeInTheDocument()
    await act(async () => holder.current!.save())
    expect(saveGraph).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Saved elsewhere')).toBeInTheDocument()
  })
})

describe('S01 save of an untitled graph', () => {
  it('asks for a name, creates the graph and clears the untitled draft', async () => {
    st().newGraph()
    render(<Harness />)
    saveDraft({ graphId: null, rev: 0, name: 'untitled', graph: st().graph! })
    await act(async () => holder.current!.save())
    const input = await screen.findByTestId('nb-name-input')
    expect(screen.getByText('Save as')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('nb-dialog-primary'))
    expect(await screen.findByText('Enter a name.')).toBeInTheDocument()
    fireEvent.change(input, { target: { value: 'fresh' } })
    const sent = st().graph!
    vi.mocked(createGraph).mockResolvedValueOnce(envelope('g_9', 1, 'fresh', sent))
    await act(async () => fireEvent.click(screen.getByTestId('nb-dialog-primary')))
    expect(createGraph).toHaveBeenCalledWith({ name: 'fresh', graph: sent })
    expect(st().graphMeta).toEqual({ id: 'g_9', rev: 1, name: 'fresh' })
    expect(localStorage.getItem('nb.draft.new')).toBeNull()
    expect(localStorage.getItem('nb.lastGraph')).toBe('g_9')
  })

  it('keeps the dialog open on a name clash', async () => {
    const { NameTakenError } = await import('../../../api/graphs')
    st().newGraph()
    render(<Harness />)
    await act(async () => holder.current!.save())
    const input = await screen.findByTestId('nb-name-input')
    fireEvent.change(input, { target: { value: 'taken' } })
    vi.mocked(createGraph).mockRejectedValueOnce(new NameTakenError())
    await act(async () => fireEvent.click(screen.getByTestId('nb-dialog-primary')))
    expect(screen.getByText('A graph named "taken" already exists.')).toBeInTheDocument()
    expect(st().graphMeta).toBeNull()
  })
})

describe('W1 fix pass: save, discard and late loads', () => {
  it('Save on a clean saved graph sends nothing and keeps an undecided draft (FC-2)', async () => {
    saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: graphWith(node('n_a', 'rsi', { params: { period: 30 } })) })
    vi.mocked(getGraph).mockResolvedValue(envelope('g_1', 3, 'alpha', serverGraph))
    setLastGraphId('g_1')
    render(<Harness />)
    await act(async () => holder.current!.openLastGraph())
    await screen.findByTestId('nb-banner-draft_found')
    await act(async () => holder.current!.save())
    expect(saveGraph).not.toHaveBeenCalled()
    expect(localStorage.getItem('nb.draft.g_1')).not.toBeNull()
    expect(st().graphMeta?.rev).toBe(3)
  })

  it('edits made while the save is out stay in a draft (FC-7)', async () => {
    st().openGraph(serverGraph, { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    let resolve!: (e: GraphEnvelope) => void
    vi.mocked(saveGraph).mockReturnValue(new Promise<GraphEnvelope>(r => { resolve = r }))
    act(() => holder.current!.save())
    act(() => st().updateNodeParams('n_a', { period: 25 }))
    await act(async () => resolve(envelope('g_1', 4, 'alpha', serverGraph)))
    expect(st().dirty).toBe(true)
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(25)
    expect(readDraft('g_1')?.rev).toBe(4)
  })

  it('Discard cleans the graph at once, so a cancelled follow-up leaves no edits to autosave (FC-5)', async () => {
    vi.useFakeTimers()
    st().openGraph(serverGraph, { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    // Import JSON: Save changes? then the file picker, which the user cancels.
    act(() => holder.current!.importJson())
    fireEvent.click(screen.getByTestId('nb-save-changes-discard'))
    await act(async () => {})
    expect(st().dirty).toBe(false)
    expect(st().graph?.nodes.n_a.params.period).toBe(14)
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS * 2))
    expect(localStorage.getItem('nb.draft.g_1')).toBeNull()
    // One undo step brings the discarded edits back.
    act(() => st().undo())
    expect(st().graph?.nodes.n_a.params.period).toBe(20)
  })

  it('a late reopen of the last graph does not replace a graph started meanwhile (FC-6)', async () => {
    setLastGraphId('g_1')
    let resolve!: (e: GraphEnvelope) => void
    vi.mocked(getGraph).mockReturnValue(new Promise<GraphEnvelope>(r => { resolve = r }))
    render(<Harness />)
    act(() => holder.current!.openLastGraph())
    // The user starts a new graph and edits it before the reopen lands.
    act(() => st().newGraph())
    act(() => st().addNode(node('n_new', 'mine')))
    await act(async () => resolve(envelope('g_1', 3, 'alpha', serverGraph)))
    expect(st().graph?.nodes.n_new).toBeDefined()
    expect(st().graphMeta).toBeNull()
    expect(holder.current!.busy).toBeNull()
  })
})

describe('S04 while the server version loads (UX-12)', () => {
  it('Save as copy waits for the server version; an old time reads "on <date>"', async () => {
    st().openGraph(serverGraph, { id: 'g_1', rev: 12, name: 'alpha' })
    render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    vi.mocked(saveGraph).mockRejectedValueOnce(new RevConflictError(13))
    let resolve!: (e: GraphEnvelope) => void
    vi.mocked(getGraph).mockReturnValue(new Promise<GraphEnvelope>(r => { resolve = r }))
    await act(async () => holder.current!.save())
    expect(screen.getByTestId('nb-conflict-copy')).toBeDisabled()
    const old = new Date(Date.now() - 5 * 86_400_000).toISOString()
    await act(async () => resolve({ ...envelope('g_1', 13, 'alpha', serverGraph), updated_at: old }))
    expect(screen.getByTestId('nb-conflict-copy')).not.toBeDisabled()
    expect(screen.getByTestId('nb-conflict-text')).toHaveTextContent(/another window on \d{4}-\d{2}-\d{2} \(rev 13\)/)
  })
})

describe('W1 fix pass: backend changes', () => {
  const corrupt = {
    response: { status: 409, data: { detail: { code: 'graph_corrupt', detail: 'The saved file for this graph cannot be read.' } } },
  }

  it('a 409 graph_corrupt on save is an error banner, not the conflict dialog', async () => {
    st().openGraph(serverGraph, { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Harness />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    vi.mocked(saveGraph).mockRejectedValueOnce(corrupt)
    await act(async () => holder.current!.save())
    expect(isGraphCorrupt(corrupt)).toBe(true)
    expect(screen.queryByTestId('nb-conflict-dialog')).toBeNull()
    const banner = screen.getByTestId('nb-banner-server_error')
    expect(banner).toHaveTextContent('Could not save: The saved file for this graph cannot be read.')
    expect(within(banner).queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(st().dirty).toBe(true)
    // The next Save tries again (no pending conflict was recorded).
    vi.mocked(saveGraph).mockResolvedValueOnce(envelope('g_1', 4, 'alpha', st().graph!))
    await act(async () => holder.current!.save())
    expect(saveGraph).toHaveBeenCalledTimes(2)
  })

  it('a graph that comes back with its wires in port order does not prompt for an equal draft', () => {
    const w = (id: string, from: string, port: string) => ({ id, from, to: 'n_c', from_port: 'out' as const, to_port: port, attr: null })
    const local = { ...graphWith(node('n_a', 'a'), node('n_b', 'b'), node('n_c', 'c')), wires: [w('w2', 'n_b', 'in1'), w('w1', 'n_a', 'in0')] }
    const echoed = { ...local, wires: [w('w1', 'n_a', 'in0'), w('w2', 'n_b', 'in1')] }
    expect(graphsEqual(local, echoed)).toBe(true)
    expect(graphsEqual(local, { ...echoed, wires: [w('w1', 'n_a', 'in1'), w('w2', 'n_b', 'in0')] })).toBe(false)
  })
})
