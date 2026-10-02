/**
 * F435 Wave 6 review fixes (fixer C, frontend): one block per finding.
 * FE-01 and FE-12 are in collapse.test.ts, FE-04 in search.test.ts and
 * FE-13 in assets.test.tsx.
 */

import { describe, it, expect, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api } from '../../../api/client'
import {
  assetErrorText,
  cachedAsset,
  clearAssetCache,
  deleteAssetVersion,
  forgetMissingAssets,
  getAsset,
  type AssetFile,
  type AssetListItem,
} from '../../../api/graphLibrary'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import Canvas from '../Canvas'
import Inspector from '../Inspector'
import TabMenu, { type TabMenuProps } from '../TabMenu'
import { AssetManagerHost } from '../AssetManager'
import { useNodeBuilderStore } from '../store'
import { getCommand, setActiveCanvas } from '../commands'
import { openAssetManager, resetAssetLibrary, resetAssetUi, useAssetLibrary } from '../assetUi'
import { registerAssetNetworkSource, setNetworkView, type AssetNetworkSource } from '../networkNav'
import { cardInfoOf, createNodeMapper, computeFrameLayouts, setLayoutScope, type CardNodeData } from '../rfMapping'
import {
  assetNetworkOf,
  assetOriginOf,
  localCopyMatches,
  relockInstance,
  replaceWithLockedInstance,
  unlockInstance,
  updateInstanceVersion,
} from '../operations/assets'
import { movePromoted, promoteParam, takenNames } from '../operations/promote'
import { LIFECYCLE_TEXT, newerVersionOf } from '../assetLifecycle'
import { AssetLifecycleRows } from '../inspector/AssetLifecycleRows'
import { StreamSection } from '../inspector/NodeSections'
import { TAB_MENU_TEXT } from '../assetText'
import { resetDiagnostics } from '../useDiagnostics'
import { resetAssetNetworks } from '../assetNetworks'

function setAppWidth(w: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true })
}

function node(id: string, type: string, parent: string | null, position: [number, number], params: GraphNode['params'] = {}, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent, params, position, display: false, bypass: false, ...extra }
}

function wire(id: string, from: string, to: string, toPort = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port: toPort }
}

/** Ticker -> locked instance `rf` (regime_filter v3) -> entry. */
function lockedGraph(): Graph {
  const g = emptyGraph()
  g.nodes = {
    aapl: node('aapl', 'ticker', null, [0, 0], { symbol: 'AAPL', interval: '1d' }),
    rf: node('rf', 'subnet', null, [0, 200], { lookback: 30 }, {
      meta: { view: 'card' },
      asset_ref: { name: 'regime_filter', version: 3 },
      locked: true,
      promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
    }),
    entry: node('entry', 'entry', null, [0, 400], { signal: '@regime_on' }),
  }
  g.wires = [wire('w1', 'aapl', 'rf'), wire('w2', 'rf', 'entry')]
  return g
}

/** The asset network of regime_filter v3 (positions relative to the instance, parent null on top). */
function regimeNetwork(): AssetFile['network'] {
  return {
    nodes: {
      sig: node('sig', 'subnet_input', null, [0, 0], { port: 0 }, { name: 'signal' }),
      sma: node('sma', 'sma', null, [0, 100], { period: 50 }),
      out: node('out', 'subnet_output', null, [0, 200]),
    },
    wires: [wire('a1', 'sig', 'sma'), wire('a2', 'sma', 'out')],
  }
}

function assetFile(name = 'regime_filter', version = 3, extra: Partial<AssetFile> = {}): AssetFile {
  return {
    name,
    version,
    description: '',
    stream_schema: 1,
    interface: { reads: [], writes: [] },
    promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
    palette: null,
    network: regimeNetwork(),
    created_at: '2026-10-02T09:00:00Z',
    ...extra,
  }
}

function listItem(name: string, extra: Partial<AssetListItem> = {}): AssetListItem {
  return { name, versions: [1], latest: 1, palette: null, interface: { reads: [], writes: [] }, used_by: [], ...extra }
}

const notFound = () => Object.assign(new Error('404'), { isAxiosError: true, response: { status: 404, data: { detail: { code: 'asset_missing', message: 'Asset not found' } } } })

/** Library GETs: the list and `name@version` files; anything else 404s. */
function mockLibrary(list: AssetListItem[], files: Record<string, AssetFile | Promise<AssetFile>> = {}) {
  const get = vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
    if (url === '/api/graph_library') return { data: { assets: list } }
    const m = /^\/api\/graph_library\/([^/]+)\/(\d+)$/.exec(url)
    const f = m ? files[`${m[1]}@${m[2]}`] : undefined
    if (f) return { data: await f }
    throw notFound()
  })
  const del = vi.spyOn(api, 'delete').mockResolvedValue({ data: null })
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { ok: true, diagnostics: [], streams: {} } })
  return { get, del, post }
}

const s = () => useNodeBuilderStore.getState()

function load(g: Graph) {
  s().discardEdits()
  s().openGraph(g, { id: 'g_000000000077', rev: 1, name: 'fixes' })
}

/** jsdom has no layout: give every builder root a client rect, so keys count as "in view". */
function inView(container: HTMLElement) {
  for (const el of Array.from(container.querySelectorAll<HTMLElement>('.nodebuilder-root'))) {
    el.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  }
}

function mountCanvas(g: Graph, extra: React.ReactNode = null) {
  load(g)
  const utils = render(
    <div className="nodebuilder-root" style={{ width: 1200, height: 900 }}>
      <Canvas graph={s().graph!} />
      {extra}
    </div>,
  )
  inView(utils.container)
  return utils
}

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

beforeEach(() => {
  try { localStorage.clear() } catch { /* storage blocked */ }
  setAppWidth(1600)
  resetAssetUi()
  resetAssetLibrary()
  clearAssetCache()
  resetAssetNetworks()
})

afterEach(() => {
  cleanup()
  setActiveCanvas(null)
  setLayoutScope(null)
  resetDiagnostics()
  s().enterNetwork(null)
  s().discardEdits()
  vi.restoreAllMocks()
})

// ── FE-02: a locked instance card has ports ─────────────────────────────────

describe('FE-02: locked instance card ports', () => {
  it('before the asset file loads, the ports come from the instance\'s own wires', () => {
    const g = lockedGraph()
    const card = cardInfoOf(g.nodes, g.nodes.rf, { wires: g.wires })
    expect(card.inputs.map(p => p.handle)).toEqual(['in0'])
    expect(card.outputId).not.toBeNull()
    // Without the context (the old code path) there were none.
    expect(cardInfoOf(g.nodes, g.nodes.rf).inputs).toEqual([])
  })

  it('with the asset network, the ports are its top-level boundary nodes (labels, output, child count)', () => {
    const g = lockedGraph()
    const net = regimeNetwork()
    const card = cardInfoOf(g.nodes, g.nodes.rf, { wires: [], assetNetwork: () => net })
    expect(card.inputs).toEqual([{ boundaryId: 'rf::sig', handle: 'in0', label: 'signal' }])
    expect(card.outputId).toBe('rf::out')
    expect(card.childCount).toBe(1)
  })

  it('the mapper redraws the card when the asset network arrives', () => {
    const g = lockedGraph()
    const map = createNodeMapper()
    let net: AssetFile['network'] | null = null
    const ctx = { wires: g.wires, assetNetwork: () => net }
    const before = map(g.nodes, true, undefined, null, ctx).find(n => n.id === 'rf')!
    net = regimeNetwork()
    const after = map(g.nodes, true, undefined, null, ctx).find(n => n.id === 'rf')!
    expect(after).not.toBe(before)
    expect((after.data as CardNodeData).card.inputs[0].label).toBe('signal')
  })

  it('a locked instance stays a card when shown as frame (no portless empty frame)', () => {
    const g = lockedGraph()
    expect(setNetworkView(g, ['rf'], 'frame').nodes.rf.meta?.view).toBe('card')
  })

  it('on the canvas the card draws an input and an output handle, so its wires stay', () => {
    mountCanvas(lockedGraph())
    expect(screen.getByTestId('nb-port-rf-in0')).toBeTruthy()
    expect(screen.getByTestId('nb-port-rf-out')).toBeTruthy()
  })
})

// ── FE-05: Save as asset (replace) makes a card at the frame's corner ───────

describe('FE-05: replace with a locked instance', () => {
  function frameGraph(): Graph {
    const g = emptyGraph()
    g.nodes = {
      // Frame view (no meta.view), stored far from its children.
      mom: node('mom', 'subnet', null, [5000, 5000]),
      bin: node('bin', 'subnet_input', 'mom', [400, 300], { port: 0 }, { name: 'in0' }),
      rsi: node('rsi', 'rsi', 'mom', [400, 400], { period: 14 }),
      out: node('out', 'subnet_output', 'mom', [400, 500]),
    }
    g.wires = [wire('w1', 'bin', 'rsi'), wire('w2', 'rsi', 'out')]
    return g
  }

  it('a frame-view subnet becomes a card at the frame\'s drawn corner', () => {
    const g = frameGraph()
    const corner = computeFrameLayouts(g.nodes, undefined, null).get('mom')!
    const next = replaceWithLockedInstance(g, 'mom', { name: 'mom_asset', version: 1 })
    expect(next.nodes.mom.meta?.view).toBe('card')
    expect(next.nodes.mom.position).toEqual([corner.x, corner.y])
    expect(next.nodes.mom.locked).toBe(true)
  })

  it('the asset positions are relative to that same corner, so the dive draws children where they were', () => {
    const g = frameGraph()
    const [ox, oy] = assetOriginOf(g, 'mom')
    const net = assetNetworkOf(g, 'mom')
    expect([net.nodes.rsi.position[0] + ox, net.nodes.rsi.position[1] + oy]).toEqual([400, 400])
  })

  it('a card keeps its stored position', () => {
    const g = frameGraph()
    g.nodes.mom = { ...g.nodes.mom, meta: { view: 'card' } }
    expect(replaceWithLockedInstance(g, 'mom', { name: 'mom_asset', version: 1 }).nodes.mom.position).toEqual([5000, 5000])
  })
})

// ── FE-06 / UX-12: inserting before the file arrives ───────────────────────

describe('FE-06: an instance always gets its promoted params', () => {
  it('Asset Manager Insert waits for the version\'s file', async () => {
    let release!: (f: AssetFile) => void
    const pending = new Promise<AssetFile>(r => { release = r })
    mockLibrary([listItem('regime_filter', { versions: [3], latest: 3 })], { 'regime_filter@3': pending })
    load(lockedGraph())
    render(<AssetManagerHost />)
    await act(async () => { openAssetManager({ insertAt: { x: 10, y: 10 } }) })
    await waitFor(() => screen.getByTestId('nb-am-row-regime_filter'))
    const insert = screen.getByTestId('nb-am-insert')
    expect(insert).toBeDisabled()
    expect(insert.getAttribute('title')).toBe('Loading this version…')
    await act(async () => { release(assetFile()) })
    await waitFor(() => expect(screen.getByTestId('nb-am-insert')).not.toBeDisabled())
    const before = new Set(Object.keys(s().graph!.nodes))
    fireEvent.click(screen.getByTestId('nb-am-insert'))
    const added = Object.values(s().graph!.nodes).find(n => !before.has(n.id))!
    expect(added.promoted?.map(p => p.name)).toEqual(['lookback'])
    expect(added.params.lookback).toBe(50)
  })

  it('Tab menu: a failed fetch inserts nothing and says why', async () => {
    mockLibrary([listItem('ghost', { versions: [2], latest: 2 })])
    load(lockedGraph())
    const props: TabMenuProps = {
      open: true,
      screenPosition: { x: 100, y: 100 },
      graphPosition: { x: 500, y: 260 },
      selectedNodeId: null,
      autoWire: false,
      onToggleAutoWire: vi.fn(),
      onCreate: vi.fn(),
      onClose: vi.fn(),
    }
    render(<TabMenu {...props} />)
    fireEvent.change(document.querySelector('input')!, { target: { value: 'ghost' } })
    const before = Object.keys(s().graph!.nodes).length
    const row = await screen.findByTestId('nb-tab-row-asset:ghost')
    await act(async () => { fireEvent.click(row) })
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(Object.keys(s().graph!.nodes)).toHaveLength(before)
    expect(s().flash?.text).toBe('Asset not found')
  })
})

// ── FE-03: Enter on a control is the control's ─────────────────────────────

describe('FE-03: Enter does not dive from a focused button', () => {
  it('network.dive passes on Enter from a button and dives on Enter from the canvas', () => {
    load(lockedGraph())
    act(() => { s().setSelection({ nodeIds: ['rf'], primary: 'rf' }) })
    const cmd = getCommand('network.dive')!
    const button = document.createElement('button')
    const pane = document.createElement('div')
    document.body.append(button, pane)
    const keyOn = (el: HTMLElement) => {
      let ev: KeyboardEvent | null = null
      el.addEventListener('keydown', e => { ev = e }, { once: true })
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      return ev!
    }
    expect(cmd.run({ store: useNodeBuilderStore, canvas: null, event: keyOn(button) })).toBe(false)
    expect(s().currentNetworkId ?? null).toBeNull()
    expect(cmd.run({ store: useNodeBuilderStore, canvas: null, event: keyOn(pane) })).not.toBe(false)
    button.remove()
    pane.remove()
  })
})

// ── FE-07 and UX-02: inside a locked dive ───────────────────────────────────

describe('inside a locked asset (FE-07, UX-02)', () => {
  function withSource(fn: () => void | Promise<void>) {
    const source = vi.fn<AssetNetworkSource>(() => regimeNetwork())
    const off = registerAssetNetworkSource(source)
    return Promise.resolve(fn()).finally(off)
  }

  it('FE-07: selecting a node inside shows it in the Inspector, read-only', () => withSource(() => {
    mockLibrary([])
    mountCanvas(lockedGraph(), <Inspector />)
    act(() => { s().enterNetwork('rf') })
    act(() => { s().setSelection({ nodeIds: ['rf::sma'], primary: 'rf::sma' }) })
    const view = screen.getByTestId('nb-inspector-node')
    expect(view.textContent).toContain('sma')
    // Read-only: no Save as asset row, no editable fields.
    expect(within(view).queryAllByRole('spinbutton').every(el => (el as HTMLInputElement).readOnly || (el as HTMLInputElement).disabled)).toBe(true)
  }))

  it('UX-02: double-click on the pane opens the Tab menu with the locked note', () => withSource(async () => {
    mockLibrary([])
    const { container } = mountCanvas(lockedGraph())
    act(() => { s().enterNetwork('rf') })
    const pane = container.querySelector('.react-flow__pane')!
    await act(async () => { fireEvent.doubleClick(pane) })
    expect(screen.getByRole('note').textContent).toBe(TAB_MENU_TEXT.lockedNote)
  }))
})

// ── FE-08, FE-11: promoted list ─────────────────────────────────────────────

describe('promoted params (FE-08, FE-11)', () => {
  function promotedGraph(): Graph {
    const g = emptyGraph()
    g.nodes = {
      mom: node('mom', 'subnet', null, [0, 0], {}, { meta: { view: 'card' } }),
      sma: node('sma', 'sma', 'mom', [0, 100], { period: 20 }),
      rsi: node('rsi', 'rsi', 'mom', [0, 200], { period: 14 }),
      ema: node('ema', 'ema', 'mom', [0, 300], { period: 9 }),
    }
    let next = g
    for (const [n, child] of [['a', 'sma'], ['b', 'rsi'], ['c', 'ema']]) next = promoteParam(next, child, 'period', { name: n, label: n.toUpperCase() })
    return next
  }

  it('FE-08: dragging a row two places moves it, it does not swap', () => {
    const g = movePromoted(promotedGraph(), 'mom', 'a', 2)
    expect(g.nodes.mom.promoted!.map(p => p.name)).toEqual(['b', 'c', 'a'])
    expect(movePromoted(promotedGraph(), 'mom', 'c', -2).nodes.mom.promoted!.map(p => p.name)).toEqual(['c', 'a', 'b'])
  })

  it('FE-11: a group\'s own catalog params are taken even when not stored', () => {
    const grp = node('grp', 'output_group', null, [0, 0])
    const taken = takenNames(grp)
    expect(taken.has('direction')).toBe(true)
    expect(taken.has('ticker')).toBe(true)
  })
})

// ── FE-10, LD-05: the asset file cache and error text ──────────────────────

describe('graphLibrary cache (FE-10) and error text (LD-05)', () => {
  it('a delete wins over a read in flight: the late answer is not cached', async () => {
    let release!: (f: AssetFile) => void
    mockLibrary([], { 'regime_filter@3': new Promise<AssetFile>(r => { release = r }) })
    const read = getAsset('regime_filter', 3)
    await deleteAssetVersion('regime_filter', 3)
    release(assetFile())
    await read
    expect(cachedAsset('regime_filter', 3)).toBeNull()
  })

  it('a 404 is remembered: asking again sends nothing until forgotten', async () => {
    const { get } = mockLibrary([])
    await expect(getAsset('gone', 1)).rejects.toBeTruthy()
    await expect(getAsset('gone', 1)).rejects.toBeTruthy()
    expect(get).toHaveBeenCalledTimes(1)
    forgetMissingAssets()
    await expect(getAsset('gone', 1)).rejects.toBeTruthy()
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('LD-05: a 409 asset_corrupt shows its detail.message', () => {
    const e = Object.assign(new Error('409'), {
      isAxiosError: true,
      response: { status: 409, data: { detail: { code: 'asset_corrupt', message: 'The file regime_filter v3 is damaged.' } } },
    })
    expect(assetErrorText(e)).toBe('The file regime_filter v3 is damaged.')
  })
})

// ── UX-01, UX-06: Asset Manager confirm focus and the ⋯ menu ────────────────

describe('Asset Manager keyboard (UX-01, UX-06)', () => {
  async function openManager() {
    const lib = mockLibrary([listItem('regime_filter', { versions: [3], latest: 3 })], { 'regime_filter@3': assetFile() })
    load(lockedGraph())
    render(<AssetManagerHost />)
    await act(async () => { openAssetManager({ insertAt: null }) })
    await waitFor(() => screen.getByTestId('nb-am-row-regime_filter'))
    return lib
  }

  it('UX-01: the delete confirm opens on Cancel, and Enter there deletes nothing', async () => {
    const { del } = await openManager()
    fireEvent.keyDown(screen.getByTestId('nb-am-list'), { key: 'Delete' })
    const confirm = screen.getByTestId('nb-am-delete-confirm')
    const cancel = within(confirm).getByRole('button', { name: 'Cancel' })
    expect(document.activeElement).toBe(cancel)
    await act(async () => { fireEvent.keyDown(cancel, { key: 'Enter' }) })
    expect(del).not.toHaveBeenCalled()
  })

  it('UX-06: the ⋯ menu takes focus, arrows move, Esc closes the menu and not the dialog', async () => {
    await openManager()
    await waitFor(() => expect(screen.getByTestId('nb-am-insert')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('nb-am-more'))
    const menu = await screen.findByTestId('nb-am-menu')
    const items = within(menu).getAllByRole('menuitem')
    await waitFor(() => expect(document.activeElement).toBe(items[0]))
    fireEvent.keyDown(items[0], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(items[1])
    act(() => { fireEvent.keyDown(items[1], { key: 'Escape' }) })
    expect(screen.queryByTestId('nb-am-menu')).toBeNull()
    expect(screen.getByTestId('nb-am-insert')).toBeTruthy()
  })
})

// ── UX-03: plain Subnet from the Tab menu is a card ─────────────────────────

describe('UX-03: Tab menu Subnet', () => {
  it('choosing Subnet makes a card, in one undo step', async () => {
    mockLibrary([])
    const g = emptyGraph()
    load(g)
    const onCreate = vi.fn((entry: { name: string }) => {
      const id = 'n_00000001'
      s().addNode(node(id, entry.name, null, [500, 260]))
      s().select(id)
    })
    render(<TabMenu open screenPosition={{ x: 100, y: 100 }} graphPosition={{ x: 500, y: 260 }} selectedNodeId={null} autoWire={false} onToggleAutoWire={vi.fn()} onCreate={onCreate as unknown as TabMenuProps['onCreate']} onClose={vi.fn()} />)
    fireEvent.change(document.querySelector('input')!, { target: { value: 'subnet' } })
    fireEvent.click(await screen.findByTestId('nb-tab-row-subnet'))
    expect(s().graph!.nodes.n_00000001.meta?.view).toBe('card')
    act(() => { s().undo() })
    expect(s().graph!.nodes.n_00000001).toBeUndefined()
  })
})

// ── UX-04: asset lifecycle ──────────────────────────────────────────────────

describe('UX-04: update, re-lock, missing, no output', () => {
  it('updateInstanceVersion takes the new promoted list, keeps shared values', () => {
    const g = lockedGraph()
    const v4 = assetFile('regime_filter', 4, {
      promoted: [
        { name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 },
        { name: 'band', label: 'Band', target: 'sma/period', type: 'int', default: 7 },
      ],
    })
    const next = updateInstanceVersion(g, 'rf', v4)
    expect(next.nodes.rf.asset_ref).toEqual({ name: 'regime_filter', version: 4 })
    expect(next.nodes.rf.params).toEqual({ lookback: 30, band: 7 })
    const v5 = assetFile('regime_filter', 5, { promoted: [] })
    expect(updateInstanceVersion(next, 'rf', v5).nodes.rf.params).toEqual({})
  })

  it('localCopyMatches: true right after unlock, false after an edit; re-lock restores the instance', () => {
    const file = assetFile()
    const copy = unlockInstance(lockedGraph(), 'rf', file)
    expect(localCopyMatches(copy, 'rf', file)).toBe(true)
    // Moving a node changes nothing that matters.
    const moved = { ...copy, nodes: { ...copy.nodes, 'rf::sma': { ...copy.nodes['rf::sma'], position: [9, 9] as [number, number] } } }
    expect(localCopyMatches(moved, 'rf', file)).toBe(true)
    const edited = { ...copy, nodes: { ...copy.nodes, 'rf::sma': { ...copy.nodes['rf::sma'], params: { period: 10 } } } }
    expect(localCopyMatches(edited, 'rf', file)).toBe(false)
    const relocked = relockInstance(copy, 'rf')
    expect(relocked.nodes.rf.locked).toBe(true)
    expect(relocked.nodes['rf::sma']).toBeUndefined()
    expect(relocked.wires.map(w => w.id).sort()).toEqual(['w1', 'w2'])
  })

  it('newerVersionOf reads the library list', () => {
    const items = [listItem('regime_filter', { versions: [3, 4], latest: 4 })]
    expect(newerVersionOf(lockedGraph().nodes.rf, items)).toBe(4)
    expect(newerVersionOf({ asset_ref: { name: 'regime_filter', version: 4 } }, items)).toBeNull()
  })

  it('Inspector: "v4 available · Update" updates in one commit', async () => {
    mockLibrary([], { 'regime_filter@4': assetFile('regime_filter', 4) })
    load(lockedGraph())
    useAssetLibrary.setState({ items: [listItem('regime_filter', { versions: [3, 4], latest: 4 })], status: 'ok', error: null, loadedAt: Date.now() })
    render(<AssetLifecycleRows nodeId="rf" node={s().graph!.nodes.rf} editable diagnostics={[]} />)
    expect(screen.getByTestId('nb-inspector-asset-newer').textContent).toContain('v4 available')
    await act(async () => { fireEvent.click(screen.getByTestId('nb-inspector-asset-update')) })
    await waitFor(() => expect(s().graph!.nodes.rf.asset_ref?.version).toBe(4))
    act(() => { s().undo() })
    expect(s().graph!.nodes.rf.asset_ref?.version).toBe(3)
  })

  it('Inspector: Re-lock is offered on an unchanged copy and refused on a changed one', async () => {
    const file = assetFile()
    mockLibrary([], { 'regime_filter@3': file })
    load(unlockInstance(lockedGraph(), 'rf', file))
    const view = () => <AssetLifecycleRows nodeId="rf" node={s().graph!.nodes.rf} editable diagnostics={[]} />
    const { rerender } = render(view())
    await waitFor(() => expect(screen.getByTestId('nb-inspector-asset-relock')).not.toBeDisabled())
    expect(screen.getByTestId('nb-inspector-asset-relock').textContent).toBe('Re-lock to v3')
    act(() => { s().commit('edit', g => ({ ...g, nodes: { ...g.nodes, 'rf::sma': { ...g.nodes['rf::sma'], params: { period: 10 } } } })) })
    rerender(view())
    await waitFor(() => expect(screen.getByTestId('nb-inspector-asset-relock')).toBeDisabled())
    expect(screen.getByTestId('nb-inspector-asset-relock').getAttribute('title')).toBe(LIFECYCLE_TEXT.relockDiffers(3))
  })

  it('Inspector: asset_missing shows the S38 sentence', () => {
    load(lockedGraph())
    render(<AssetLifecycleRows nodeId="rf" node={s().graph!.nodes.rf} editable diagnostics={[{ code: 'asset_missing', severity: 'error', message: 'gone', node_id: 'rf', path: '/rf', param: null, port: null, line: null, col: null, end_line: null, end_col: null }]} />)
    expect(screen.getByTestId('nb-inspector-asset-missing').textContent).toBe(LIFECYCLE_TEXT.missing)
  })

  it('Stream section: a subnet with no subnet_output says so', () => {
    const g = emptyGraph()
    g.nodes = { sub: node('sub', 'subnet', null, [0, 0], {}, { meta: { view: 'card' } }), x: node('x', 'rsi', 'sub', [0, 0]) }
    load(g)
    const { rerender } = render(<StreamSection nodeId="sub" node={s().graph!.nodes.sub} editable />)
    expect(screen.getByTestId('nb-inspector-no-output').textContent).toBe(LIFECYCLE_TEXT.noOutput)
    act(() => { s().addNode(node('o', 'subnet_output', 'sub', [0, 100])) })
    rerender(<StreamSection nodeId="sub" node={s().graph!.nodes.sub} editable />)
    expect(screen.queryByTestId('nb-inspector-no-output')).toBeNull()
  })

  it('card: the newer-version dot and the Rules colour', () => {
    useAssetLibrary.setState({
      items: [listItem('regime_filter', { versions: [3, 4], latest: 4, palette: { category: 'rules', label: 'Regime', glyph: 'R' } })],
      status: 'ok',
      error: null,
      loadedAt: Date.now(),
    })
    mockLibrary([])
    mountCanvas(lockedGraph())
    expect(screen.getByTestId('nb-subnet-newer-rf').getAttribute('aria-label')).toBe('v4 available')
    expect(screen.getByTestId('nb-subnet-type-rf').className).toContain('nb-subnet__type--rules')
  })

  it('node menu: "Update to v4" on a locked instance, "Re-lock to v3" on a copy', () => {
    load(lockedGraph())
    useAssetLibrary.setState({ items: [listItem('regime_filter', { versions: [3, 4], latest: 4 })], status: 'ok', error: null, loadedAt: Date.now() })
    act(() => { s().setSelection({ nodeIds: ['rf'], primary: 'rf' }) })
    const update = getCommand('assets.update')!
    expect(update.menu).toBe('node')
    expect(update.label).toBe('Update to v4')
    expect(getCommand('assets.relock')!.menu).toBeUndefined()
    load(unlockInstance(lockedGraph(), 'rf', assetFile()))
    act(() => { s().setSelection({ nodeIds: ['rf'], primary: 'rf' }) })
    expect(getCommand('assets.relock')!.menu).toBe('node')
    expect(getCommand('assets.relock')!.label).toBe('Re-lock to v3')
    expect(getCommand('assets.update')!.menu).toBeUndefined()
  })
})
