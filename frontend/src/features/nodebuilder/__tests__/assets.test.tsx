/**
 * Asset library authoring (F435 W6 item 6.D, specs S41, S42, S43): the
 * graphLibrary client (mocked HTTP), Save as asset, the Asset Manager,
 * Cmd+Shift+A, and asset rows in the Tab menu.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../../../api/client'
import { clearAssetCache, getAsset, type AssetFile, type AssetListItem } from '../../../api/graphLibrary'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { findCommand, getCommand } from '../commands'
import { openAssetManager, openSaveAsset, resetAssetLibrary, resetAssetUi, useAssetUi } from '../assetUi'
import { SaveAssetDialogHost } from '../SaveAssetDialog'
import { AssetManagerHost } from '../AssetManager'
import TabMenu, { type TabMenuProps } from '../TabMenu'
import { menuCatalog } from '../canvasHelpers'
import { assetNetworkOf } from '../operations/assets'

function node(id: string, type: string, position: [number, number], parent: string | null = null, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent, params: {}, position, display: false, bypass: false, ...extra }
}

/** Ticker -> subnet `mom` (rsi -> out) -> entry. */
function fixture(): Graph {
  const g = emptyGraph()
  g.nodes = {
    t: node('t', 'ticker', [0, 0]),
    mom: node('mom', 'subnet', [100, 100], null, {
      meta: { view: 'card' },
      promoted: [{ name: 'rsi_period', label: 'Period', target: 'rsi/period', type: 'int', default: 14 }],
    }),
    bin: { ...node('bin', 'subnet_input', [120, 60], 'mom'), name: 'in0', params: { port: 0 } },
    rsi: { ...node('rsi', 'rsi', [140, 160], 'mom'), params: { period: 14 } },
    out: node('out', 'subnet_output', [140, 260], 'mom'),
    e: node('e', 'entry', [100, 400]),
  }
  g.nodes.mom.params = { rsi_period: 14 }
  g.wires = [
    { id: 'w1', from: 't', to: 'mom', from_port: 'out', to_port: 'in0' },
    { id: 'w2', from: 'bin', to: 'rsi', from_port: 'out', to_port: 'in0' },
    { id: 'w3', from: 'rsi', to: 'out', from_port: 'out', to_port: 'in' },
    { id: 'w4', from: 'mom', to: 'e', from_port: 'out', to_port: 'in0' },
  ]
  return g
}

function asset(name: string, extra: Partial<AssetListItem> = {}): AssetListItem {
  return {
    name,
    versions: [1],
    latest: 1,
    palette: null,
    interface: { reads: [], writes: [] },
    used_by: [],
    ...extra,
  }
}

function file(name: string, version: number, extra: Partial<AssetFile> = {}): AssetFile {
  return {
    name,
    version,
    description: '',
    stream_schema: 1,
    interface: { reads: [], writes: [] },
    promoted: [],
    palette: null,
    network: { nodes: {}, wires: [] },
    created_at: '2026-10-20T09:00:00Z',
    ...extra,
  }
}

const s = () => useNodeBuilderStore.getState()

function load(g: Graph) {
  s().discardEdits()
  s().openGraph(g, { id: 'g_000000000003', rev: 1, name: 'assets' })
}

/** Mock the library routes. Returns the spies. */
function mockLibrary(list: AssetListItem[], files: Record<string, AssetFile> = {}) {
  const get = vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
    if (url === '/api/graph_library') return { data: { assets: list } }
    const m = /^\/api\/graph_library\/([^/]+)\/(\d+)$/.exec(url)
    if (m) {
      const f = files[`${m[1]}@${m[2]}`]
      if (f) return { data: f }
      throw Object.assign(new Error('404'), { isAxiosError: true, response: { status: 404, data: { detail: 'Asset not found' } } })
    }
    throw new Error(`unexpected GET ${url}`)
  })
  const post = vi.spyOn(api, 'post').mockImplementation(async (url: string, body: unknown) => {
    if (url === '/api/graph_library') {
      const b = body as { name: string }
      const latest = list.find(a => a.name === b.name)?.latest ?? 0
      return { data: file(b.name, latest + 1) }
    }
    // Validate and anything else: an empty, valid answer.
    return { data: { ok: true, diagnostics: [], streams: {} } }
  })
  const del = vi.spyOn(api, 'delete').mockResolvedValue({ data: null })
  return { get, post, del }
}

beforeEach(() => {
  resetAssetUi()
  resetAssetLibrary()
  clearAssetCache()
  load(fixture())
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('graphLibrary client', () => {
  it('fetches a version once and serves it from the cache after that', async () => {
    const { get } = mockLibrary([], { 'regime_filter@2': file('regime_filter', 2) })
    const a = await getAsset('regime_filter', 2)
    const b = await getAsset('regime_filter', 2)
    expect(a).toBe(b)
    expect(get.mock.calls.filter(c => c[0] === '/api/graph_library/regime_filter/2')).toHaveLength(1)
  })
})

describe('assetNetworkOf', () => {
  it('keeps children and inner wires, relative positions, parent null at the top', () => {
    const net = assetNetworkOf(fixture(), 'mom')
    expect(Object.keys(net.nodes).sort()).toEqual(['bin', 'out', 'rsi'])
    expect(net.nodes.rsi.parent).toBeNull()
    expect(net.nodes.rsi.position).toEqual([40, 60])
    expect(net.wires.map(w => w.id).sort()).toEqual(['w2', 'w3'])
  })
})

describe('Save as asset (S41)', () => {
  async function openSave(palette = false) {
    render(<SaveAssetDialogHost />)
    await act(async () => { openSaveAsset({ subnetId: 'mom', palette }) })
  }

  it('POSTs {name, description, network, promoted, interface, palette: null}', async () => {
    const { post } = mockLibrary([])
    await openSave()
    await waitFor(() => expect(screen.getByTestId('nb-save-asset-line').textContent).toBe('New asset'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save asset' })) })
    const call = post.mock.calls.find(c => c[0] === '/api/graph_library')!
    const body = call[1] as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual(['description', 'interface', 'name', 'network', 'palette', 'promoted'])
    expect(body.name).toBe('mom')
    expect(body.palette).toBeNull()
    expect(body.promoted).toEqual([{ name: 'rsi_period', label: 'Period', target: 'rsi/period', type: 'int', default: 14 }])
    expect(Object.keys((body.network as { nodes: object }).nodes).sort()).toEqual(['bin', 'out', 'rsi'])
  })

  it('with the palette on, sends {category: rules, label, glyph}; the glyph keeps two characters', async () => {
    const { post } = mockLibrary([])
    await openSave(true)
    const glyph = screen.getByTestId('nb-save-asset-glyph') as HTMLInputElement
    fireEvent.change(glyph, { target: { value: 'ΣXY' } })
    expect(glyph.value).toBe('ΣX')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save asset' })) })
    const body = post.mock.calls.find(c => c[0] === '/api/graph_library')![1] as Record<string, unknown>
    expect(body.palette).toEqual({ category: 'rules', label: 'Mom', glyph: 'ΣX' })
  })

  it('an existing name says "Saves as version 3" and the primary reads "Save version 3"', async () => {
    mockLibrary([asset('mom', { versions: [1, 2], latest: 2 })])
    await openSave()
    await waitFor(() => expect(screen.getByTestId('nb-save-asset-line').textContent).toBe('Saves as version 3 of mom'))
    expect(screen.getByRole('button', { name: 'Save version 3' })).toBeTruthy()
  })

  it('after a 201 with Replace on, the subnet is a locked instance and its children are gone (one undo step)', async () => {
    mockLibrary([asset('mom', { versions: [1, 2], latest: 2 })])
    await openSave()
    await waitFor(() => screen.getByRole('button', { name: 'Save version 3' }))
    const past = s().past.length
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save version 3' })) })
    const g = s().graph!
    expect(g.nodes.mom.asset_ref).toEqual({ name: 'mom', version: 3 })
    expect(g.nodes.mom.locked).toBe(true)
    expect(Object.values(g.nodes).some(n => n.parent === 'mom')).toBe(false)
    // Outside wires stay; inner wires are gone.
    expect(g.wires.map(w => w.id).sort()).toEqual(['w1', 'w4'])
    expect(s().past.length).toBe(past + 1)
    expect(useAssetUi.getState().toast?.text).toBe('Saved mom v3 to the library.')
    expect(useAssetUi.getState().saveAsset).toBeNull()
  })

  it('with Replace off the graph is unchanged and the toast still shows', async () => {
    mockLibrary([])
    await openSave()
    const before = s().graph
    fireEvent.click(screen.getByLabelText('Replace this node with a locked instance of the saved version'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save asset' })) })
    expect(s().graph).toBe(before)
    expect(useAssetUi.getState().toast?.text).toBe('Saved mom v1 to the library.')
  })

  it('an invalid name disables the primary', async () => {
    mockLibrary([])
    await openSave()
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Bad Name' } })
    expect((screen.getByRole('button', { name: 'Save asset' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('Asset Manager (S42)', () => {
  const three = [
    asset('alpha', { versions: [2, 3], latest: 3, used_by: [
      { graph_id: 'g1', name: 'pair_aapl_msft' }, { graph_id: 'g2', name: 'spy_regime' }, { graph_id: 'g3', name: 'third' },
    ] }),
    asset('beta'),
    asset('regime_filter', {
      versions: [1, 2],
      latest: 2,
      palette: { category: 'rules', label: 'Regime Filter', glyph: 'R' },
      interface: { reads: [{ name: '@close', class: 'point', dtype: 'float' }], writes: [{ name: '@regime_on', class: 'point', dtype: 'bool' }] },
    }),
  ]
  const files = {
    'alpha@3': file('alpha', 3, { description: 'first', promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }] }),
    'beta@1': file('beta', 1),
    'regime_filter@2': file('regime_filter', 2),
  }

  async function openManager() {
    render(<AssetManagerHost />)
    await act(async () => { openAssetManager({ insertAt: { x: 300, y: 200 } }) })
  }

  it('lists the assets with "v3 · 2 versions · used in 3 graphs" and fetches the selected file once', async () => {
    const { get } = mockLibrary(three, files)
    await openManager()
    await waitFor(() => expect(screen.getByTestId('nb-am-row-alpha')).toBeTruthy())
    expect(screen.getByTestId('nb-am-list').querySelectorAll('[role="option"]')).toHaveLength(3)
    expect(screen.getByTestId('nb-am-row-alpha').textContent).toContain('v3 · 2 versions · used in 3 graphs')
    await waitFor(() => expect(screen.getByText('first')).toBeTruthy())
    expect(get.mock.calls.filter(c => c[0] === '/api/graph_library/alpha/3')).toHaveLength(1)
  })

  it('Insert into graph adds a locked instance with no children at the cursor', async () => {
    mockLibrary(three, files)
    await openManager()
    await waitFor(() => screen.getByText('first'))
    const before = Object.keys(s().graph!.nodes).length
    fireEvent.click(screen.getByTestId('nb-am-insert'))
    const g = s().graph!
    const added = Object.values(g.nodes).find(n => n.asset_ref?.name === 'alpha')!
    expect(added).toMatchObject({ type: 'subnet', asset_ref: { name: 'alpha', version: 3 }, locked: true, parent: null, position: [300, 200] })
    expect(added.params).toEqual({ lookback: 50 })
    expect(Object.values(g.nodes).some(n => n.parent === added.id)).toBe(false)
    expect(Object.keys(g.nodes)).toHaveLength(before + 1)
    expect(s().selectedNodeIds).toEqual([added.id])
    expect(useAssetUi.getState().manager).toBeNull()
  })

  it('the delete confirm names the graphs and says bots are not affected; confirming sends DELETE', async () => {
    const { del } = mockLibrary(three, files)
    await openManager()
    await waitFor(() => screen.getByText('first'))
    fireEvent.keyDown(screen.getByTestId('nb-am-list'), { key: 'Delete' })
    const confirm = screen.getByTestId('nb-am-delete-confirm')
    expect(confirm.textContent).toContain('pair_aapl_msft, spy_regime, third')
    expect(confirm.textContent).toContain('Bots are not affected.')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete v3' })) })
    expect(del).toHaveBeenCalledWith('/api/graph_library/alpha/3')
  })

  it('searching @regime_on narrows the list to assets whose interface writes it', async () => {
    mockLibrary(three, files)
    await openManager()
    await waitFor(() => screen.getByTestId('nb-am-row-alpha'))
    fireEvent.change(screen.getByLabelText('Search assets or @attributes'), { target: { value: '@regime_on' } })
    const rows = screen.getByTestId('nb-am-list').querySelectorAll('[role="option"]')
    expect([...rows].map(r => r.getAttribute('data-testid'))).toEqual(['nb-am-row-regime_filter'])
  })

  it('the assets.manager command opens it; Cmd+Shift+A is not bound (FE-13: Chrome tab search)', () => {
    expect(findCommand('mod+shift+a', new Set(['global']))).toBeNull()
    const cmd = getCommand('assets.manager')
    expect(cmd?.keys ?? []).toEqual([])
    cmd!.run({ canvas: null, store: useNodeBuilderStore, event: null })
    expect(useAssetUi.getState().manager).not.toBeNull()
  })
})

describe('Tab menu asset rows (S43)', () => {
  const regime = asset('regime_filter', {
    versions: [1, 2, 3],
    latest: 3,
    palette: { category: 'rules', label: 'Regime Filter', glyph: 'R' },
    interface: { reads: [{ name: '@close', class: 'point', dtype: 'float' }], writes: [{ name: '@regime_on', class: 'point', dtype: 'bool' }] },
  })
  const regimeFile = file('regime_filter', 3, {
    description: 'SPY above its 50-day SMA',
    promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
  })

  function props(extra: Partial<TabMenuProps> = {}): TabMenuProps {
    return {
      open: true,
      screenPosition: { x: 100, y: 100 },
      graphPosition: { x: 500, y: 260 },
      selectedNodeId: null,
      autoWire: false,
      onToggleAutoWire: vi.fn(),
      // Like the canvas: add a node of the entry's type at the menu point and select it.
      onCreate: vi.fn((entry: { name: string; defaults: { params: Record<string, unknown> } }) => {
        const id = `n_${Object.keys(s().graph!.nodes).length.toString().padStart(8, '0')}`
        s().addNode({ id, type: entry.name, name: entry.name, parent: null, params: { ...entry.defaults.params } as GraphNode['params'], position: [500, 260], display: false, bypass: false })
        s().select(id)
      }),
      onClose: vi.fn(),
      ...extra,
    }
  }

  async function openMenu(p = props()) {
    render(<TabMenu {...p} />)
    await act(async () => { await getAsset('regime_filter', 3) })
    return p
  }

  function hoverCategory(label: string) {
    const span = [...document.querySelectorAll('span')].find(el => el.textContent === label)!
    fireEvent.mouseEnter(span.parentElement!)
  }

  it('the Rules category counts the palette asset and its row reads "asset v3 · …"', async () => {
    mockLibrary([regime], { 'regime_filter@3': regimeFile })
    await openMenu()
    const builtIn = menuCatalog().filter(e => e.cat === 'rules').length
    const rulesRow = [...document.querySelectorAll('span')].find(el => el.textContent === 'rules')!.parentElement!
    await waitFor(() => expect(rulesRow.textContent).toContain(String(builtIn + 1)))
    hoverCategory('rules')
    const row = screen.getByTestId('nb-tab-row-asset:regime_filter')
    expect(row.textContent).toContain('Regime Filter')
    expect(row.textContent).toContain('asset v3 · SPY above its 50-day SMA')
  })

  it('choosing the asset row adds a locked instance of the latest version at the menu point, in one undo step', async () => {
    mockLibrary([regime], { 'regime_filter@3': regimeFile })
    const p = await openMenu()
    const input = screen.getByPlaceholderText('Search nodes…')
    fireEvent.change(input, { target: { value: '@regime_on' } })
    expect(screen.getByTestId('nb-tab-attr-chip').textContent).toBe('@regime_on')
    const past = s().past.length
    fireEvent.keyDown(input, { key: 'Enter' })
    const inst = Object.values(s().graph!.nodes).find(n => n.asset_ref)!
    expect(inst).toMatchObject({ type: 'subnet', name: 'regime_filter', locked: true, asset_ref: { name: 'regime_filter', version: 3 }, position: [500, 260] })
    expect(inst.params).toEqual({ lookback: 50 })
    expect(s().past.length).toBe(past + 1)
    expect(p.onClose).toHaveBeenCalled()
  })

  it('inside a network, Networks lists Subnet input; at the root it does not', async () => {
    mockLibrary([])
    const { unmount } = render(<TabMenu {...props()} />)
    hoverCategory('network')
    expect(screen.queryByTestId('nb-tab-row-subnet_input')).toBeNull()
    unmount()

    const st = s() as unknown as { setCurrentNetwork?: (id: string) => void; setNetwork: (p: string) => void }
    act(() => { if (st.setCurrentNetwork) st.setCurrentNetwork('mom'); else st.setNetwork('/mom') })
    render(<TabMenu {...props()} />)
    hoverCategory('network')
    expect(screen.getByTestId('nb-tab-row-subnet_input')).toBeTruthy()
    // One output already exists in `mom`: that row is disabled.
    expect(screen.getByTestId('nb-tab-row-subnet_output').getAttribute('aria-disabled')).toBe('true')
  })

  it('a new Subnet input from the menu takes the next free port and is named after it', async () => {
    mockLibrary([])
    const st = s() as unknown as { setCurrentNetwork?: (id: string) => void; setNetwork: (p: string) => void }
    act(() => { if (st.setCurrentNetwork) st.setCurrentNetwork('mom'); else st.setNetwork('/mom') })
    const p = props({
      onCreate: vi.fn((entry: { name: string; defaults: { params: Record<string, unknown> } }) => {
        s().addNode({ id: 'n_newinput', type: entry.name, name: entry.name, parent: 'mom', params: { ...entry.defaults.params } as GraphNode['params'], position: [0, 0], display: false, bypass: false })
        s().select('n_newinput')
      }),
    })
    render(<TabMenu {...p} />)
    hoverCategory('network')
    fireEvent.click(screen.getByTestId('nb-tab-row-subnet_input'))
    const n = s().graph!.nodes.n_newinput
    expect(n.params.port).toBe(1)
    expect(n.name).toBe('in1')
  })
})
