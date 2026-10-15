/**
 * Esc in the Spawn bots dialog closes the dialog only (F435 W7 integration;
 * seen in a scripted browser run: Esc there seemed to also leave the graph
 * editor for the read-only strategy view). NodeBuilder is mounted with
 * the real dialog host and toolbar; leaving the editor would show as a
 * changed or read-only store graph and a missing canvas. Harness as in
 * graphToolbar.test.tsx.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

const CLEAN_DIAG = {
  diagnostics: [] as unknown[], byNode: {}, errorCount: 0, warningCount: 0,
  pending: false, offline: false, offlineDetail: null as string | null, hasResult: true,
}
// A tiny external store, so a change re-renders the (memoized) NodeBuilder.
const diagState = vi.hoisted(() => {
  const listeners = new Set<() => void>()
  const state = {
    value: {} as typeof CLEAN_DIAG,
    set(v: typeof CLEAN_DIAG) {
      state.value = v
      listeners.forEach(l => l())
    },
    subscribe(l: () => void) {
      listeners.add(l)
      return () => { listeners.delete(l) }
    },
  }
  return state
})

vi.mock('../useDiagnostics', async () => {
  const { useSyncExternalStore } = await import('react')
  return {
    useDiagnostics: () => useSyncExternalStore(diagState.subscribe, () => diagState.value),
    useDiagnosticsController: vi.fn(),
    setServerDiagnostics: vi.fn(),
    retryValidation: vi.fn().mockResolvedValue(undefined),
    getDiagnosticsView: () => diagState.value,
  }
})
vi.mock('../DiagnosticsPopover', () => ({
  DiagnosticsPopover: (p: { open: boolean }) => (p.open ? <div data-testid="nb-diag-popover" /> : null),
}))
vi.mock('../Canvas', () => ({ default: () => <div data-testid="canvas-stub" /> }))
vi.mock('../../../api/nodebuilder', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/nodebuilder')>()
  return { ...orig, fetchGraphBacktest: vi.fn(), fetchAutoRender: vi.fn() }
})
vi.mock('../../../api/graphs', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/graphs')>()
  return {
    ...orig,
    listGraphs: vi.fn().mockResolvedValue([]),
    getGraph: vi.fn(),
    createGraph: vi.fn(),
    saveGraph: vi.fn(),
    deleteGraph: vi.fn(),
    seedLegacyGraphs: vi.fn(),
  }
})

import NodeBuilder from '../NodeBuilder'
import { useNodeBuilderStore } from '../store'
import { clearNotices } from '../notices'
import { closeSpawnDialog, openSpawnDialog, useSpawnUi } from '../spawnUi'

function node(id: string, type: string, name: string): GraphNode {
  return { id, type, name, parent: null, params: {}, position: [0, 0], display: false, bypass: false }
}

function smallGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: { n_t: node('n_t', 'ticker', 'aapl'), n_e: node('n_e', 'entry', 'entry') },
  }
}

function renderNodeBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <NodeBuilder request={null} graphViewActive />
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  localStorage.clear()
  clearNotices()
  diagState.value = { ...CLEAN_DIAG }
  useNodeBuilderStore.getState().discardEdits()
  // The plugin registers the toolbar button and the dialog host (Canvas, which
  // loads plugins in the app, is stubbed here).
  await import('../plugins/spawnBots')
})

afterEach(() => {
  closeSpawnDialog()
  cleanup()
})

function openSaved() {
  act(() => { useNodeBuilderStore.getState().openGraph(smallGraph(), { id: 'g1', rev: 3, name: 'alpha' }) })
}

describe('Esc in the Spawn bots dialog', () => {
  it.each([
    ['the focused control in the dialog', () => document.activeElement as Element],
    ['the dialog panel', () => screen.getByRole('dialog')],
    ['the page body (focus fell out of the dialog)', () => document.body],
  ])('from %s closes the dialog and keeps the graph editor', (_label, target) => {
    renderNodeBuilder()
    openSaved()
    act(() => { openSpawnDialog() })
    expect(screen.getByRole('dialog')).toBeTruthy()
    const graphBefore = useNodeBuilderStore.getState().graph
    fireEvent.keyDown(target(), { key: 'Escape', code: 'Escape' })
    expect(useSpawnUi.getState().open).toBe(false)
    expect(screen.queryByRole('dialog')).toBeNull()
    // Still the editor: same editable graph, canvas and edit toolbar shown.
    const s = useNodeBuilderStore.getState()
    expect(s.graph).toBe(graphBefore)
    expect(s.graph?.readOnly).toBeFalsy()
    expect(s.graphMeta?.id).toBe('g1')
    expect(screen.getByTestId('canvas-stub')).toBeTruthy()
    expect(screen.getByTestId('nb-btn-spawn')).toBeTruthy()
  })

  it('a second Esc after the dialog closed still keeps the editor', () => {
    renderNodeBuilder()
    openSaved()
    act(() => { openSpawnDialog() })
    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' })
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(useNodeBuilderStore.getState().graph?.readOnly).toBeFalsy()
    expect(screen.getByTestId('canvas-stub')).toBeTruthy()
  })
})
