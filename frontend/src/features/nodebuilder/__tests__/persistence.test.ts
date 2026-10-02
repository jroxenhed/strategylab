/**
 * persistence.ts tests (F435 W1 item 1.F; S01 seed and import, S03 drafts,
 * S04 diff, A5 storage keys): drafts round-trip and survive blocked
 * storage, the restore prompt compares content not rev, the one-time seed
 * renames the legacy key and runs once, imports accept the three shapes,
 * and the autosave writes a dirty graph within 5 s.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'

vi.mock('../../../api/graphs', async importOriginal => {
  const orig = await importOriginal<typeof import('../../../api/graphs')>()
  return { ...orig, seedLegacyGraphs: vi.fn() }
})

import { seedLegacyGraphs } from '../../../api/graphs'
import {
  clearDraft,
  compareGraphs,
  copyName,
  countLegacyGraphs,
  describeGraphDiff,
  draftKey,
  draftPromptFor,
  exportFileName,
  exportPayload,
  graphsEqual,
  importName,
  nameError,
  parseImport,
  readDraft,
  runLegacySeed,
  saveDraft,
  seedBannerText,
  storageGet,
  DRAFT_INTERVAL_MS,
} from '../persistence'
import { useNodeBuilderStore } from '../store'
import { renderHook } from '@testing-library/react'
import { useDraftAutosave } from '../persistence'

function node(id: string, name: string, extra: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    type: 'rsi',
    name,
    parent: null,
    params: { period: 14 },
    position: [0, 0],
    display: false,
    bypass: false,
    ...extra,
  }
}

function graphWith(...nodes: GraphNode[]): Graph {
  const g = emptyGraph()
  return { ...g, nodes: Object.fromEntries(nodes.map(n => [n.id, n])) }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('drafts', () => {
  it('uses nb.draft.<id> and nb.draft.new keys', () => {
    expect(draftKey('g_abc')).toBe('nb.draft.g_abc')
    expect(draftKey(null)).toBe('nb.draft.new')
  })

  it('round-trips a draft and clears it', () => {
    const g = graphWith(node('n_a', 'rsi'))
    expect(saveDraft({ graphId: 'g_1', rev: 3, name: 'alpha', graph: g })).toBe(true)
    const d = readDraft('g_1')
    expect(d?.rev).toBe(3)
    expect(d?.name).toBe('alpha')
    expect(graphsEqual(d!.graph, g)).toBe(true)
    clearDraft('g_1')
    expect(readDraft('g_1')).toBeNull()
  })

  it('never throws when storage is blocked', () => {
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(localStorage, 'removeItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(saveDraft({ graphId: 'g_1', rev: 1, name: 'a', graph: emptyGraph() })).toBe(false)
    expect(readDraft('g_1')).toBeNull()
    expect(() => clearDraft('g_1')).not.toThrow()
    expect(storageGet('nb.seeded')).toBeNull()
  })

  it('skips drafts over 1 MB', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const big = graphWith(node('n_a', 'rsi', { params: { blob: 'x'.repeat(1_100_000) } }))
    expect(saveDraft({ graphId: 'g_big', rev: 1, name: 'big', graph: big })).toBe(false)
    expect(localStorage.getItem('nb.draft.g_big')).toBeNull()
    warn.mockRestore()
  })

  it('ignores a draft that does not parse', () => {
    localStorage.setItem('nb.draft.g_1', '{not json')
    expect(readDraft('g_1')).toBeNull()
    localStorage.setItem('nb.draft.g_1', JSON.stringify({ graph: 5 }))
    expect(readDraft('g_1')).toBeNull()
  })

  it('prompts on content, not on rev', () => {
    const server = graphWith(node('n_a', 'rsi'))
    const same = { graphId: 'g_1', rev: 3, name: 'a', savedAt: '2026-09-30T10:00:00Z', graph: server }
    // Content-equal (even with a different key order): no prompt.
    const { nodes, wires, ...rest } = server
    const reordered = JSON.parse(JSON.stringify({ wires, nodes, ...rest })) as Graph
    expect(draftPromptFor({ ...same, graph: reordered }, server, 3)).toBeNull()
    const edited = graphWith(node('n_a', 'rsi', { params: { period: 20 } }))
    expect(draftPromptFor({ ...same, graph: edited }, server, 3)).toBe('same')
    expect(draftPromptFor({ ...same, graph: edited }, server, 5)).toBe('moved')
    expect(draftPromptFor(null, server, 3)).toBeNull()
  })
})

describe('draft autosave', () => {
  it('writes a dirty graph within 5 s and stops when clean', () => {
    vi.useFakeTimers()
    const g = graphWith(node('n_a', 'rsi'))
    useNodeBuilderStore.getState().openGraph(g, { id: 'g_auto', rev: 2, name: 'auto' })
    const { unmount } = renderHook(() => useDraftAutosave())
    useNodeBuilderStore.getState().updateNodeParams('n_a', { period: 21 })
    vi.advanceTimersByTime(DRAFT_INTERVAL_MS - 1)
    expect(localStorage.getItem('nb.draft.g_auto')).toBeNull()
    vi.advanceTimersByTime(1)
    const d = readDraft('g_auto')
    expect(d?.rev).toBe(2)
    expect(d?.graph.nodes.n_a.params.period).toBe(21)
    // Undo back to the saved graph: clean, so no further write.
    clearDraft('g_auto')
    useNodeBuilderStore.getState().undo()
    vi.advanceTimersByTime(DRAFT_INTERVAL_MS * 2)
    expect(localStorage.getItem('nb.draft.g_auto')).toBeNull()
    unmount()
  })

  it('never writes drafts of a read-only graph', () => {
    vi.useFakeTimers()
    useNodeBuilderStore.setState({ graph: { ...emptyGraph(), readOnly: true }, dirty: true, graphMeta: null })
    const { unmount } = renderHook(() => useDraftAutosave())
    useNodeBuilderStore.setState({ commitSeq: useNodeBuilderStore.getState().commitSeq + 1 })
    vi.advanceTimersByTime(DRAFT_INTERVAL_MS * 2)
    expect(localStorage.getItem('nb.draft.new')).toBeNull()
    unmount()
  })
})

describe('legacy seed', () => {
  const legacy = JSON.stringify({ 'RSI dip': { _version: 1, nodes: {}, wires: [] } })

  it('counts both legacy shapes and garbage', () => {
    expect(countLegacyGraphs(legacy)).toBe(1)
    expect(countLegacyGraphs(JSON.stringify([{ name: 'a', graph: {} }, { name: 'b' }]))).toBe(1)
    expect(countLegacyGraphs('garbage')).toBe(0)
    expect(countLegacyGraphs(null)).toBe(0)
  })

  it('imports once, renames the legacy key and sets nb.seeded', async () => {
    localStorage.setItem('strategylab-saved-graphs', legacy)
    vi.mocked(seedLegacyGraphs).mockResolvedValue({ imported: ['g_1'], skipped: [{ name: 'x', reason: 'duplicate' }] })
    const out = await runLegacySeed()
    expect(seedLegacyGraphs).toHaveBeenCalledWith(legacy)
    expect(out).toEqual({ imported: 1, duplicates: 1, unreadable: 0 })
    expect(localStorage.getItem('nb.seeded')).toBe('1')
    expect(localStorage.getItem('strategylab-saved-graphs')).toBeNull()
    expect(localStorage.getItem('strategylab-saved-graphs.migrated')).toBe(legacy)
    expect(seedBannerText(out!)).toBe(
      'Imported 1 saved graph from this browser to the server. Open one from ⋯ › Open. · 1 skipped (duplicates)',
    )
    vi.mocked(seedLegacyGraphs).mockClear()
    expect(await runLegacySeed()).toBeNull()
    expect(seedLegacyGraphs).not.toHaveBeenCalled()
  })

  it('does nothing without legacy graphs, and retries after a failure', async () => {
    vi.mocked(seedLegacyGraphs).mockReset()
    expect(await runLegacySeed()).toBeNull()
    expect(seedLegacyGraphs).not.toHaveBeenCalled()
    localStorage.setItem('strategylab-saved-graphs', legacy)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(seedLegacyGraphs).mockRejectedValueOnce(new Error('down'))
    expect(await runLegacySeed()).toBeNull()
    expect(localStorage.getItem('nb.seeded')).toBeNull()
    expect(localStorage.getItem('strategylab-saved-graphs')).toBe(legacy)
    warn.mockRestore()
  })

  it('keeps the legacy key when the .migrated copy cannot be written (DI-06)', async () => {
    localStorage.setItem('strategylab-saved-graphs', legacy)
    vi.mocked(seedLegacyGraphs).mockResolvedValue({ imported: [], skipped: [{ name: 'RSI dip', reason: 'invalid' }] })
    // setItem lives on the storage object (test polyfill) or on its prototype (jsdom).
    const ls = window.localStorage
    const owner = (Object.prototype.hasOwnProperty.call(ls, 'setItem') ? ls : Object.getPrototypeOf(ls)) as Storage
    const realSet = owner.setItem
    const spy = vi.spyOn(owner, 'setItem').mockImplementation(function (this: Storage, k: string, v: string) {
      if (k === 'strategylab-saved-graphs.migrated') throw new DOMException('full', 'QuotaExceededError')
      realSet.call(this, k, v)
    })
    const out = await runLegacySeed()
    spy.mockRestore()
    expect(out).toEqual({ imported: 0, duplicates: 0, unreadable: 1 })
    expect(localStorage.getItem('strategylab-saved-graphs')).toBe(legacy)
    expect(localStorage.getItem('nb.seeded')).toBe('1')
    expect(seedBannerText(out!)).toContain('1 could not be read (kept in this browser)')
    expect(seedBannerText(out!)).not.toContain('duplicates')
  })

  it('a 413 reports too large, keeps everything and retries later', async () => {
    localStorage.setItem('strategylab-saved-graphs', legacy)
    vi.mocked(seedLegacyGraphs).mockRejectedValueOnce({ response: { status: 413 } })
    const out = await runLegacySeed()
    expect(out).toEqual({ imported: 0, duplicates: 0, unreadable: 0, tooLarge: true })
    expect(seedBannerText(out!)).toContain('too large')
    expect(localStorage.getItem('strategylab-saved-graphs')).toBe(legacy)
    expect(localStorage.getItem('nb.seeded')).toBeNull()
  })
})

describe('names', () => {
  it('validates names and builds copy/import names', () => {
    expect(nameError('  ')).toBe('Enter a name.')
    expect(nameError('x'.repeat(81))).toBe('Use 80 characters or fewer.')
    expect(nameError('ok')).toBeNull()
    expect(copyName('alpha')).toBe('alpha copy')
    expect(copyName('alpha', 1)).toBe('alpha copy 2')
    expect(importName('alpha')).toBe('alpha')
    expect(importName('alpha', 1)).toBe('alpha (imported)')
    expect(importName('alpha', 2)).toBe('alpha (imported 2)')
  })
})

describe('import and export', () => {
  const g = graphWith(node('n_a', 'rsi'))

  it('accepts an envelope, a bare graph and a legacy map', () => {
    expect(parseImport(JSON.stringify({ id: 'g_1', rev: 2, name: 'env', graph: g }), 'x.json')).toEqual([
      { name: 'env', graph: g },
    ])
    expect(parseImport(JSON.stringify(g), 'my strat.graph.json')).toEqual([{ name: 'my strat', graph: g }])
    expect(parseImport(JSON.stringify({ one: g, two: g, junk: 5 }), 'm.json').map(e => e.name)).toEqual([
      'one',
      'two',
    ])
  })

  it('rejects files that hold no graph', () => {
    expect(() => parseImport('nope', 'a.json')).toThrow('not valid JSON')
    expect(() => parseImport('[1,2]', 'a.json')).toThrow('does not hold a graph')
    expect(() => parseImport('{"a": 1}', 'a.json')).toThrow('does not hold a graph')
  })

  it('exports the envelope with the graph on screen, or {name, graph} when untitled', () => {
    const env = { id: 'g_1', rev: 2, name: 'env', description: '', created_at: 'c', updated_at: 'u', graph: emptyGraph() }
    expect(exportPayload(g, 'env', env)).toEqual({ ...env, graph: g })
    expect(exportPayload(g, 'untitled', null)).toEqual({ name: 'untitled', graph: g })
    expect(exportFileName('a/b: c')).toBe('a_b_ c.graph.json')
  })
})

describe('compareGraphs', () => {
  it('lists added, removed and changed nodes', () => {
    const local = graphWith(node('n_a', 'rsi'), node('n_v', 'vol_ok'), node('n_p', 'pos'))
    const server = graphWith(
      node('n_a', 'rsi', { params: { period: 9 } }),
      node('n_s', 'sma_spy'),
      node('n_p', 'pos', { position: [50, 0] }),
    )
    const d = compareGraphs(local, server)
    expect(d.added).toEqual(['sma_spy'])
    expect(d.removed).toEqual(['vol_ok'])
    expect(describeGraphDiff(d)).toEqual([
      '+ 1 node added on the server: sma_spy',
      '− 1 node removed on the server: vol_ok',
      '~ 2 nodes changed: rsi (params), pos (position)',
    ])
  })

  it('says when only positions differ, and when nothing does', () => {
    const local = graphWith(node('n_a', 'rsi'))
    const moved = graphWith(node('n_a', 'rsi', { position: [10, 10] }))
    expect(describeGraphDiff(compareGraphs(local, moved))).toEqual(['Only node positions differ.'])
    expect(compareGraphs(local, local).identical).toBe(true)
  })

  it('reports wire changes and a name change', () => {
    const a = graphWith(node('n_a', 'rsi'), node('n_b', 'cmp'))
    const b = { ...a, wires: [{ id: 'w1', from: 'n_a', to: 'n_b', from_port: 'out' as const, to_port: 'in0' }] }
    const lines = describeGraphDiff(compareGraphs(a, b, { local: 'mine', server: 'theirs' }))
    expect(lines[0]).toBe('~ 2 nodes changed: rsi (wires), cmp (wires)')
    expect(lines[1]).toBe('~ name changed: mine → theirs')
  })
})
