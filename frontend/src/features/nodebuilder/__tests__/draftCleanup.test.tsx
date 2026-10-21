/**
 * A draft goes away when its graph turns clean again without a save
 * (F453 item 3). Before, an edit wrote the draft 5 s later, an undo made the
 * graph clean, and the draft stayed: the next open showed "A newer unsaved
 * draft … was found" for changes that no longer existed.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { DRAFT_INTERVAL_MS, readDraft, RESTORE_DRAFT_LABEL, saveDraft, useDraftAutosave } from '../persistence'

function node(id: string): GraphNode {
  return { id, type: 'rsi', name: id, parent: null, params: { period: 14 }, position: [0, 0], display: false, bypass: false }
}

function graphWith(...nodes: GraphNode[]): Graph {
  return { ...emptyGraph(), nodes: Object.fromEntries(nodes.map(n => [n.id, n])) }
}

function Autosave() {
  useDraftAutosave()
  return null
}

const st = () => useNodeBuilderStore.getState()

beforeEach(() => {
  localStorage.clear()
  st().discardEdits()
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('draft cleanup (F453)', () => {
  it('an undo back to the saved graph deletes the draft', () => {
    st().openGraph(graphWith(node('n_a')), { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Autosave />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS))
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(20)
    act(() => st().undo())
    expect(st().dirty).toBe(false)
    expect(readDraft('g_1')).toBeNull()
  })

  it('a move and its undo leave no draft behind', () => {
    st().openGraph(graphWith(node('n_a')), { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Autosave />)
    act(() => st().moveNodes(['n_a'], [1, 0]))
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS))
    expect(readDraft('g_1')).not.toBeNull()
    act(() => st().undo())
    expect(readDraft('g_1')).toBeNull()
  })

  it('loading another graph keeps the draft of the one left with edits', () => {
    st().openGraph(graphWith(node('n_a')), { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Autosave />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS))
    act(() => st().openGraph(graphWith(node('n_b')), { id: 'g_2', rev: 1, name: 'beta' }))
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(20)
  })

  it('a dirty graph keeps its draft', () => {
    st().openGraph(graphWith(node('n_a')), { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Autosave />)
    act(() => st().updateNodeParams('n_a', { period: 20 }))
    act(() => st().updateNodeParams('n_a', { period: 21 }))
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS))
    act(() => st().undo())
    expect(st().dirty).toBe(true)
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(21)
  })

  // FL-2: undoing a "Restore draft" must never delete the only copy of it.
  it('undoing a restored draft keeps the stored draft', () => {
    const drafted = graphWith({ ...node('n_a'), params: { period: 33 } })
    saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: drafted })
    st().openGraph(graphWith(node('n_a')), { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Autosave />)
    // What "Restore draft" does (useGraphSession.restoreDraft).
    act(() => st().commit(RESTORE_DRAFT_LABEL, () => drafted))
    expect(st().dirty).toBe(true)
    act(() => st().undo())
    expect(st().dirty).toBe(false)
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(33)
  })

  it('an edit on top of a restored draft, both undone, keeps the stored draft', () => {
    const drafted = graphWith({ ...node('n_a'), params: { period: 33 } })
    saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: drafted })
    st().openGraph(graphWith(node('n_a')), { id: 'g_1', rev: 3, name: 'alpha' })
    render(<Autosave />)
    act(() => st().commit(RESTORE_DRAFT_LABEL, () => drafted))
    act(() => st().updateNodeParams('n_a', { period: 40 }))
    act(() => vi.advanceTimersByTime(DRAFT_INTERVAL_MS))
    act(() => st().undo())
    act(() => st().undo())
    expect(st().dirty).toBe(false)
    // The autosaved draft (restored edits plus the new one) is still there.
    expect(readDraft('g_1')?.graph.nodes.n_a.params.period).toBe(40)
  })
})
