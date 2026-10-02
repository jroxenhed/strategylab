/**
 * W6 frontend integration joins (F435): the promoted-target rewrite twin,
 * the status bar's `in /path`, the Promote guards that match the backend,
 * the library as the locked-instance source, and Unlock to a local copy.
 */

import type { FC } from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import { api } from '../../../api/client'
import { cachedAsset, clearAssetCache, type AssetFile } from '../../../api/graphLibrary'
import { useNodeBuilderStore } from '../store'
import { getCommand, isCommandEnabled, runCommand } from '../commands'
import { buildMenu, closeContextMenu, openContextMenu, type MenuItemRow } from '../contextMenuModel'
import type { CanvasCtx } from '../canvasPlugins'
import { relativePath, rewritePathRefs } from '../paths'
import { selectionSlotText } from '../statusChannels'
import StatusBar from '../StatusBar'
import { BuilderContext, type BuilderApi } from '../slots'
import { promoteProblem } from '../operations/promote'
import { unlockInstance } from '../operations/assets'
import { unlockAssetInstance, unlockTarget } from '../commands/assets'
import { assetNetworkOf, registerAssetNetworkSource, useAssetNetworksVersion } from '../networkNav'
import { ensureAssetNetworks, installAssetNetworks, lockedRefsOf, resetAssetNetworks, RETRY_AFTER_MS } from '../assetNetworks'
import SubnetNode from '../nodes/SubnetNode'
import { ParametersWithPromoted } from '../PromotedSection'

function node(id: string, type: string, params: GraphNode['params'] = {}, parent: string | null = null, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent, params, position: [0, 0], display: false, bypass: false, ...extra }
}

const s = () => useNodeBuilderStore.getState()

function load(g: Graph) {
  s().discardEdits()
  s().openGraph(g, { id: 'g_000000000003', rev: 1, name: 'w6' })
}

/** An asset file: `sma` -> `out`, positions relative to the subnet, one promoted param. */
function assetFile(name = 'regime_filter', version = 3): AssetFile {
  return {
    name,
    version,
    description: '',
    stream_schema: 1,
    interface: { reads: [], writes: [] },
    promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
    palette: null,
    network: {
      nodes: {
        sma: { ...node('sma', 'sma', { period: 50 }), position: [10, 20] },
        out: { ...node('out', 'subnet_output'), position: [10, 140] },
      },
      wires: [{ id: 'w1', from: 'sma', to: 'out', from_port: 'out', to_port: 'in0' }] as GraphWire[],
    },
    created_at: '2026-10-01T00:00:00Z',
  }
}

/** A root ticker wired into locked instance `rf` (at 900,0) of regime_filter v3. */
function lockedGraph(): Graph {
  const g = emptyGraph()
  g.nodes = {
    aapl: node('aapl', 'ticker', { symbol: 'AAPL', interval: '1d' }),
    rf: node('rf', 'subnet', { lookback: 30 }, null, {
      position: [900, 0],
      meta: { view: 'card' },
      asset_ref: { name: 'regime_filter', version: 3 },
      locked: true,
      promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
    }),
  }
  g.wires = [{ id: 'win', from: 'aapl', to: 'rf', from_port: 'out', to_port: 'in0' }]
  return g
}

function mockLibrary(files: AssetFile[] = [assetFile()]) {
  return vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
    const m = /^\/api\/graph_library\/([^/]+)\/(\d+)$/.exec(url)
    const f = m && files.find(x => x.name === decodeURIComponent(m[1]) && x.version === Number(m[2]))
    if (f) return { data: f }
    throw Object.assign(new Error('404'), { isAxiosError: true, response: { status: 404, data: { detail: 'Asset not found' } } })
  })
}

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

/** GETs of asset files (the list request the card dot needs is not counted). */
const fileGets = (get: ReturnType<typeof mockLibrary>) =>
  get.mock.calls.filter(c => /^\/api\/graph_library\/[^/]+\/\d+$/.test(String(c[0]))).length

beforeEach(() => {
  clearAssetCache()
  resetAssetNetworks()
})

afterEach(() => {
  cleanup()
  closeContextMenu()
  vi.restoreAllMocks()
})

// ── paths.ts twin of migrate._relative_path ──────────────────────────────────

describe('relativePath (twin of migrate._relative_path)', () => {
  it('goes down without .., up with .., and . for itself', () => {
    expect(relativePath('/a/b', '/a/b/c/d')).toBe('c/d')
    expect(relativePath('/a/b', '/a/x')).toBe('../x')
    expect(relativePath('/a', '/a')).toBe('.')
    expect(relativePath('/', '/a/b')).toBe('a/b')
  })

  it('onlyNodes limits the promoted rewrite to those networks', () => {
    const g = emptyGraph()
    g.nodes = {
      mom: node('mom', 'subnet', {}, null, { promoted: [{ name: 'p', label: 'p', target: 'rsi/period', type: 'int', default: 14 }] }),
      rsi: { ...node('rsi', 'rsi', { period: 14 }, 'mom'), name: 'rsi_fast' },
    }
    expect(rewritePathRefs(g, '/mom/rsi', '/mom/rsi_fast', new Set(['other'])).nodes.mom.promoted![0].target).toBe('rsi/period')
    expect(rewritePathRefs(g, '/mom/rsi', '/mom/rsi_fast', new Set(['mom'])).nodes.mom.promoted![0].target).toBe('rsi_fast/period')
  })
})

// ── Status bar: `in /path` (S37) ─────────────────────────────────────────────

describe('status bar inside a network (S37)', () => {
  it('selectionSlotText puts the path alone or after the selection', () => {
    expect(selectionSlotText('no selection', '')).toBe('no selection')
    expect(selectionSlotText('no selection', 'in /mom')).toBe('in /mom')
    expect(selectionSlotText('rsi', 'in /mom')).toBe('rsi · in /mom')
  })

  it('the selection slot reads `in /mom` after a dive and follows a rename', () => {
    const g = emptyGraph()
    g.nodes = { mom: node('mom', 'subnet'), rsi: node('rsi', 'rsi', { period: 14 }, 'mom') }
    load(g)
    const builder = { session: { save: vi.fn() }, runBacktest: vi.fn(), stopBacktest: vi.fn(), openDiagnostics: vi.fn() } as unknown as BuilderApi
    render(<BuilderContext.Provider value={builder}><div className="nodebuilder-root"><StatusBar /></div></BuilderContext.Provider>)
    const slot = () => screen.getByTestId('nb-status-selection')
    expect(slot()).toHaveTextContent(/^no selection$/)
    act(() => s().enterNetwork('mom'))
    expect(slot()).toHaveTextContent(/^in \/mom$/)
    act(() => s().setSelection({ nodeIds: ['rsi'], primary: 'rsi' }))
    expect(slot()).toHaveTextContent('rsi · in /mom')
    act(() => s().commit('rename', gr => ({ ...gr, nodes: { ...gr.nodes, mom: { ...gr.nodes.mom, name: 'momentum' } } })))
    expect(slot()).toHaveTextContent('rsi · in /momentum')
    act(() => s().enterNetwork(null))
    expect(slot()).not.toHaveTextContent('in /')
  })
})

// ── Promote guards (backend kernel/flatten.py _check_promoted) ──────────────

describe('Promote is not offered where the backend refuses it', () => {
  function graph(): Graph {
    const g = emptyGraph()
    g.nodes = {
      mom: node('mom', 'subnet'),
      inp: node('inp', 'subnet_input', { port: 0 }, 'mom'),
      grp: node('grp', 'output_group', { direction: 'long', ticker: '/aapl', capital_weight: 1 }, 'mom'),
      reg: node('reg', 'regime_net', {}, 'mom'),
      inner: node('inner', 'subnet', { k: 3 }, 'mom', { promoted: [{ name: 'k', label: 'k', target: 'x/period', type: 'int', default: 3 }] }),
      rsi: node('rsi', 'rsi', { period: 14 }, 'mom'),
    }
    return g
  }

  it('refuses a network node\'s own params and a port node\'s, allows a promoted param and a plain child', () => {
    const g = graph()
    expect(promoteProblem(g, 'grp', 'direction')).toMatch(/promoted params/)
    expect(promoteProblem(g, 'grp', 'capital_weight')).toMatch(/promoted params/)
    expect(promoteProblem(g, 'reg', 'anything')).toMatch(/promoted params/)
    expect(promoteProblem(g, 'inp', 'port')).toMatch(/port/)
    expect(promoteProblem(g, 'inner', 'k')).toBeNull()
    expect(promoteProblem(g, 'rsi', 'period')).toBeNull()
  })

  it('the param menu disables "Promote to parent…" on them', () => {
    load(graph())
    const cmd = getCommand('assets.promote')!
    const at = (nodeId: string, param: string) => {
      openContextMenu({ kind: 'param', screen: { x: 0, y: 0 }, flow: { x: 0, y: 0 }, canvas: {} as CanvasCtx, target: { nodeId, param } })
      return isCommandEnabled(cmd)
    }
    expect(at('grp', 'ticker')).toBe(false)
    expect(at('inp', 'port')).toBe(false)
    expect(at('rsi', 'period')).toBe(true)
  })

  it('"+ Promote a parameter…" lists neither', () => {
    load(graph())
    render(<ParametersWithPromoted nodeId="mom" node={s().graph!.nodes.mom} editable />)
    act(() => { screen.getByRole('button', { name: /Promote a parameter/ }).click() })
    const picker = screen.getByTestId('nb-promote-picker')
    expect(within(picker).queryByRole('menu', { name: 'grp' })).toBeNull()
    expect(within(picker).queryByRole('menu', { name: 'inp' })).toBeNull()
    expect(within(picker).getByRole('menu', { name: 'inner' })).toBeInTheDocument()
    expect(within(picker).getByRole('menu', { name: 'rsi' })).toBeInTheDocument()
  })
})

// ── The library as the locked-instance source ───────────────────────────────

describe('locked instances get their children from the library', () => {
  it('lockedRefsOf lists each locked ref once and skips unlocked copies', () => {
    const g = lockedGraph()
    g.nodes.rf2 = { ...g.nodes.rf, id: 'rf2', name: 'rf2' }
    g.nodes.copy = { ...g.nodes.rf, id: 'copy', name: 'copy', locked: false }
    expect(lockedRefsOf(g.nodes)).toEqual([{ name: 'regime_filter', version: 3 }])
  })

  it('fetches once, notifies on landing, serves the cached network, and does not retry a miss at once', async () => {
    const get = mockLibrary()
    const off = installAssetNetworks()
    try {
      const versions: number[] = []
      function Probe() {
        versions.push(useAssetNetworksVersion())
        return null
      }
      render(<Probe />)
      const before = versions[versions.length - 1]
      act(() => load(lockedGraph())) // the store subscription starts the fetch
      ensureAssetNetworks(s().graph) // a second ask while pending sends nothing
      await flush()
      expect(fileGets(get)).toBe(1)
      expect(cachedAsset('regime_filter', 3)).not.toBeNull()
      expect(versions[versions.length - 1]).toBeGreaterThan(before)
      expect(assetNetworkOf(s().graph!.nodes.rf)?.nodes.sma.params.period).toBe(50)

      // A missing version: one GET, then none until RETRY_AFTER_MS has passed.
      const missing = { ...lockedGraph(), nodes: { ...lockedGraph().nodes } }
      missing.nodes.rf = { ...missing.nodes.rf, asset_ref: { name: 'gone', version: 1 } }
      ensureAssetNetworks(missing)
      await flush()
      ensureAssetNetworks(missing)
      await flush()
      expect(fileGets(get)).toBe(2)
      ensureAssetNetworks(missing, Date.now() + RETRY_AFTER_MS + 1)
      await flush()
      expect(fileGets(get)).toBe(3)
    } finally {
      off()
    }
  })

  it('also fetches a locked instance nested inside a fetched asset', async () => {
    const outer = assetFile('outer', 1)
    outer.network.nodes.nested = { ...node('nested', 'subnet'), asset_ref: { name: 'regime_filter', version: 3 }, locked: true }
    const get = mockLibrary([outer, assetFile()])
    const g = lockedGraph()
    g.nodes.rf = { ...g.nodes.rf, asset_ref: { name: 'outer', version: 1 } }
    ensureAssetNetworks(g)
    await flush()
    await flush()
    expect(get.mock.calls.map(c => String(c[0])).filter(u => u !== '/api/graph_library')).toEqual(['/api/graph_library/outer/1', '/api/graph_library/regime_filter/3'])
  })
})

// ── Unlock to a local copy (S38) ─────────────────────────────────────────────

describe('Unlock to a local copy', () => {
  it('unlockInstance copies the asset under bake_assets ids, at the instance\'s place', () => {
    const g = lockedGraph()
    g.nodes.stray = node('stray', 'rsi', {}, 'rf') // a locked instance stores no children
    const out = unlockInstance(g, 'rf', assetFile())
    expect(out.nodes.stray).toBeUndefined()
    expect(out.nodes['rf::sma']).toMatchObject({ id: 'rf::sma', parent: 'rf', name: 'sma', position: [910, 20], params: { period: 50 } })
    expect(out.nodes['rf::out']).toMatchObject({ parent: 'rf', position: [910, 140] })
    expect(out.wires.find(w => w.id === 'rf::w1')).toMatchObject({ from: 'rf::sma', to: 'rf::out' })
    expect(out.wires.find(w => w.id === 'win')).toBeTruthy() // the outside wire stays
    expect(out.nodes.rf.locked).toBe(false)
    expect(out.nodes.rf.asset_ref).toEqual({ name: 'regime_filter', version: 3 })
    expect(out.nodes.rf.params.lookback).toBe(30) // the value stays on the instance
    expect(out.nodes.rf.promoted).toEqual(assetFile().promoted)
    expect(g.nodes.rf.locked).toBe(true) // input unchanged
    expect(() => unlockInstance({ ...g, readOnly: true }, 'rf', assetFile())).toThrow()
    expect(() => unlockInstance(out, 'rf', assetFile())).toThrow(/Not a locked/)
  })

  it('a nested child parent becomes its composite id, and a nested locked instance stays locked', () => {
    const f = assetFile()
    f.network.nodes.box = node('box', 'subnet')
    f.network.nodes.inner = { ...node('inner', 'rsi', {}, 'box'), position: [0, 0] }
    f.network.nodes.lockedIn = { ...node('lockedIn', 'subnet'), asset_ref: { name: 'x', version: 2 }, locked: true }
    const out = unlockInstance(lockedGraph(), 'rf', f)
    expect(out.nodes['rf::inner'].parent).toBe('rf::box')
    expect(out.nodes['rf::lockedIn']).toMatchObject({ locked: true, asset_ref: { name: 'x', version: 2 } })
  })

  it('the command targets the locked network on screen or a selected instance; one commit, one undo', async () => {
    mockLibrary()
    load(lockedGraph())
    expect(unlockTarget(s())).toBeNull()
    act(() => s().setSelection({ nodeIds: ['rf'], primary: 'rf' }))
    expect(unlockTarget(s())).toBe('rf')
    const ids = () => buildMenu('node', s(), true).filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
    expect(ids()).toContain('assets.unlock')
    act(() => s().setSelection({ nodeIds: ['aapl'], primary: 'aapl' }))
    expect(ids()).not.toContain('assets.unlock')
    act(() => s().enterNetwork('rf'))
    expect(unlockTarget(s())).toBe('rf')
    expect(runCommand('assets.unlock')).toBe(true)
    await flush()
    const g = s().graph!
    expect(g.nodes.rf.locked).toBe(false)
    expect(g.nodes['rf::sma'].parent).toBe('rf')
    expect(s().dirty).toBe(true)
    act(() => s().undo())
    expect(s().graph!.nodes.rf.locked).toBe(true)
    expect(s().graph!.nodes['rf::sma']).toBeUndefined()
  })

  it('a failed fetch flashes and leaves the graph alone; a read-only graph is refused', async () => {
    mockLibrary([])
    load(lockedGraph())
    const before = s().graph
    expect(await unlockAssetInstance('rf')).toBe(false)
    expect(s().graph).toBe(before)
    expect(s().flash?.text ?? '').toMatch(/Could not unlock regime_filter v3/)
    // openGraph always opens an editable copy; set a read-only graph directly.
    act(() => useNodeBuilderStore.setState({ graph: { ...lockedGraph(), readOnly: true } }))
    act(() => s().setSelection({ nodeIds: ['rf'], primary: 'rf' }))
    expect(isCommandEnabled(getCommand('assets.unlock')!)).toBe(false)
  })
})

// ── The card draws promoted rows (S38 / S40) ─────────────────────────────────

describe('subnet card body', () => {
  it('shows the promoted rows of an instance', () => {
    const off = registerAssetNetworkSource(() => null)
    try {
      load(lockedGraph())
      const n = s().graph!.nodes.rf
      const data = {
        name: 'rf', nodePath: '/rf', editable: true, bypass: false, node: n, backendType: 'subnet',
        card: { inputs: [], outputId: null, childCount: 0, asset: { name: 'regime_filter', version: 3, locked: true } },
      }
      const Card = SubnetNode as unknown as FC<{ id: string; data: typeof data }>
      render(<Card id="rf" data={data} />)
      expect(screen.getByTestId('nb-promoted-rows-rf')).toHaveTextContent('Lookback')
    } finally {
      off()
    }
  })
})
