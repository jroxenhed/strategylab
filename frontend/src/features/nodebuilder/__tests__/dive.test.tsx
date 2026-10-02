/**
 * Collapse-to-node and dive (W6 item 6.C, specs S37, S38, FA1).
 *
 * - rfMapping: only the network on screen is drawn; a network in card view
 *   is one card and its insides are not drawn; inside a network its boundary
 *   nodes are half-height cards (never cards and frame ports at once);
 *   wires are drawn only when both ends are.
 * - Store: the network on screen is a node id, so a rename keeps the view;
 *   a deleted network climbs to the nearest existing one with a toast;
 *   viewports are kept per network and in localStorage per saved graph.
 * - Commands: I / Enter dive, U up (selects the network you left), Shift+U
 *   to the root, X / Shift+X card or frame as one undoable commit.
 * - The canvas and the breadcrumb: `/ › long_leg › regime`, the inset
 *   outline, the aria-label, the asset instance card and the locked view.
 */

import { describe, it, expect, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act, within } from '@testing-library/react'
import type { Graph, GraphNode, GraphWire } from '../../../api/nodebuilder'
import Canvas from '../Canvas'
import Breadcrumb from '../Breadcrumb'
import { useNodeBuilderStore } from '../store'
import { getActiveCanvas, getCommand, setActiveCanvas } from '../commands'
import { commitRename } from '../operations/rename'
import { buildMenu } from '../contextMenuModel'
import {
  BOUNDARY_RF_TYPE,
  CARD_RF_TYPE,
  FRAME_RF_TYPE,
  computeFrameLayouts,
  createEdgeMapper,
  createNodeMapper,
  drawnNodes,
  edgeEndsOf,
  setLayoutScope,
  type CardNodeData,
} from '../rfMapping'
import {
  crumbsOf,
  networkStatusText,
  registerAssetNetworkSource,
  setNetworkView,
  withLockedChildren,
  type AssetNetworkSource,
} from '../networkNav'
import { ROOT_NETWORK, currentParentId, viewportStorageKey } from '../store/view'
import { cardTypeText } from '../nodes/subnetFormat'
import { reparentTargetOf } from '../plugins/networkFrames'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'

// ---------------------------------------------------------------------------
// Fixture: /long_leg/regime. `long_leg` is an Output Group at the root; the
// subnet `regime` sits inside it with one input, an SMA, an RSI and an output.
// ---------------------------------------------------------------------------

function node(
  id: string,
  type: string,
  parent: string | null,
  position: [number, number],
  params: GraphNode['params'] = {},
  extra: Partial<GraphNode> = {},
): GraphNode {
  return { id, type, name: id, parent, params, position, display: false, bypass: false, ...extra }
}

function makeGraph(regimeView: 'frame' | 'card' = 'frame'): Graph {
  const nodes: Record<string, GraphNode> = {
    aapl: node('aapl', 'ticker', null, [0, 0], { symbol: 'AAPL', interval: '1d' }),
    long_leg: node('long_leg', 'output_group', null, [200, 200], { direction: 'long', ticker: '/aapl', capital_weight: 1 }),
    lin0: node('lin0', 'subnet_input', 'long_leg', [260, 260], { port: 0 }),
    regime: node('regime', 'subnet', 'long_leg', [300, 300], {}, { meta: { view: regimeView } }),
    rin0: node('rin0', 'subnet_input', 'regime', [320, 340], { port: 0 }),
    sma: node('sma', 'sma', 'regime', [320, 420], { period: 20 }),
    rsi: node('rsi', 'rsi', 'regime', [520, 420], { period: 14 }),
    rout: node('rout', 'subnet_output', 'regime', [320, 520]),
    entry: node('entry', 'entry', 'long_leg', [300, 700], { signal: '@go' }),
    exit: node('exit', 'exit', 'long_leg', [450, 700], { signal: '@stop' }),
  }
  const wires: GraphWire[] = [
    { id: 'w_aapl', from: 'aapl', to: 'long_leg', from_port: 'out', to_port: 'in0' },
    { id: 'w_lin', from: 'lin0', to: 'regime', from_port: 'out', to_port: 'in0' },
    { id: 'w_rin', from: 'rin0', to: 'sma', from_port: 'out', to_port: 'in0' },
    { id: 'w_sma', from: 'sma', to: 'rout', from_port: 'out', to_port: 'in0' },
    { id: 'w_reg', from: 'regime', to: 'entry', from_port: 'out', to_port: 'in0' },
  ]
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes,
    wires,
    annotations: { boxes: [], notes: [] },
  }
}

/** A graph with a locked asset instance `rf` at the root. */
function lockedGraph(): Graph {
  const g = makeGraph()
  g.nodes.rf = node('rf', 'subnet', null, [900, 0], { lookback: 30 }, {
    meta: { view: 'card' },
    asset_ref: { name: 'regime_filter', version: 3 },
    locked: true,
    promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
  })
  return g
}

const st = () => useNodeBuilderStore.getState()
const nextFrame = () => act(() => new Promise<void>(r => requestAnimationFrame(() => r())))
/** jsdom has no layout: give every builder root a client rect, so keys count as "in view". */
function inView(container: HTMLElement) {
  for (const el of Array.from(container.querySelectorAll<HTMLElement>('.nodebuilder-root'))) {
    el.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  }
}

const drawnIds = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('.react-flow__node')).map(n => n.getAttribute('data-id')).sort()

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
})

afterEach(() => {
  cleanup()
  setActiveCanvas(null)
  setLayoutScope(null)
  resetDiagnostics()
  st().enterNetwork(null)
  st().discardEdits()
})

function open(g: Graph = makeGraph(), id: string | null = null) {
  st().openGraph(g, { id, rev: 1, name: 'test' })
}

/** The canvas and the breadcrumb, sharing one store, as in the builder. */
function mount(g: Graph = makeGraph(), id: string | null = null) {
  open(g, id)
  const graph = st().graph!
  const utils = render(
    <div className="nodebuilder-root" style={{ width: 1200, height: 900 }}>
      <Breadcrumb />
      <Canvas graph={graph} />
    </div>,
  )
  inView(utils.container)
  // Re-render with the store graph after each commit (NodeBuilder does this).
  const rerenderStore = () => utils.rerender(
    <div className="nodebuilder-root" style={{ width: 1200, height: 900 }}>
      <Breadcrumb />
      <Canvas graph={st().graph!} />
    </div>,
  )
  return { ...utils, rerenderStore }
}

function press(key: string, shiftKey = false) {
  act(() => { fireEvent.keyDown(document.body, { key, shiftKey }) })
}

function selectNode(id: string) {
  act(() => { st().setSelection({ nodeIds: [id], primary: id }) })
}

// ---------------------------------------------------------------------------
// rfMapping
// ---------------------------------------------------------------------------

describe('rfMapping: the network on screen (S37) and card view (FA1, S38)', () => {
  it('at the root, frames open and cards stay closed', () => {
    const asFrame = drawnNodes(makeGraph().nodes, null)
    expect([...asFrame.keys()].sort()).toEqual(['aapl', 'entry', 'exit', 'long_leg', 'regime', 'rsi', 'sma'])
    expect(asFrame.get('regime')).toBe('frame')
    const asCard = drawnNodes(makeGraph('card').nodes, null)
    expect([...asCard.keys()].sort()).toEqual(['aapl', 'entry', 'exit', 'long_leg', 'regime'])
    expect(asCard.get('regime')).toBe('card')
  })

  it('inside a network: only its children, its boundary nodes as boundary cards', () => {
    const d = drawnNodes(makeGraph().nodes, 'regime')
    expect([...d.keys()].sort()).toEqual(['rin0', 'rout', 'rsi', 'sma'])
    expect(d.get('rin0')).toBe('boundary')
    expect(d.get('rout')).toBe('boundary')
    // One level up: long_leg's boundary is a card, regime's are its frame ports.
    const up = drawnNodes(makeGraph().nodes, 'long_leg')
    expect(up.get('lin0')).toBe('boundary')
    expect(up.has('rin0')).toBe(false)
  })

  it('the mapper draws a card as nbSubnet with its ports, and boundary cards inside', () => {
    const map = createNodeMapper()
    const root = map(makeGraph('card').nodes, true, undefined, null)
    const card = root.find(n => n.id === 'regime')!
    expect(card.type).toBe(CARD_RF_TYPE)
    expect(card.parentId).toBe('long_leg')
    const info = (card.data as CardNodeData).card
    expect(info.inputs).toEqual([{ boundaryId: 'rin0', handle: 'in0', label: 'rin0' }])
    expect(info.outputId).toBe('rout')
    expect(info.childCount).toBe(2)
    // No cards for its insides.
    expect(root.some(n => ['sma', 'rsi', 'rin0', 'rout'].includes(n.id))).toBe(false)

    const inside = map(makeGraph('card').nodes, true, undefined, 'regime')
    expect(inside.map(n => n.id).sort()).toEqual(['rin0', 'rout', 'rsi', 'sma'])
    expect(inside.find(n => n.id === 'rin0')!.type).toBe(BOUNDARY_RF_TYPE)
    // The network on screen is the canvas: nothing is its React Flow child.
    expect(inside.every(n => n.parentId === undefined)).toBe(true)
    // Children keep their absolute positions.
    expect(inside.find(n => n.id === 'sma')!.position).toEqual({ x: 320, y: 420 })
  })

  it('never boundary cards and frame ports at once', () => {
    const map = createNodeMapper()
    const root = map(makeGraph().nodes, true, undefined, null)
    expect(root.some(n => n.type === BOUNDARY_RF_TYPE)).toBe(false)
    expect(root.find(n => n.id === 'regime')!.type).toBe(FRAME_RF_TYPE)
    const inLong = map(makeGraph().nodes, true, undefined, 'long_leg')
    // long_leg's own boundary is a card; regime (a frame) keeps its ports.
    expect(inLong.filter(n => n.type === BOUNDARY_RF_TYPE).map(n => n.id)).toEqual(['lin0'])
    expect(inLong.some(n => n.id === 'rin0' || n.id === 'rout')).toBe(false)
  })

  it('wires are drawn only when both ends are, with boundary ends as plain nodes inside', () => {
    const g = makeGraph()
    expect(edgeEndsOf(g.nodes, g.wires[2], null)).toEqual({ source: 'regime', sourceHandle: 'bnd:rin0', target: 'sma', targetHandle: 'in0' })
    expect(edgeEndsOf(g.nodes, g.wires[2], 'regime')).toEqual({ source: 'rin0', sourceHandle: 'out', target: 'sma', targetHandle: 'in0' })
    // A root wire is not drawn inside regime.
    expect(edgeEndsOf(g.nodes, g.wires[0], 'regime')).toBeNull()
    // Inside a card nothing is drawn; into the card goes to its port.
    const c = makeGraph('card')
    expect(edgeEndsOf(c.nodes, c.wires[2], null)).toBeNull()
    expect(edgeEndsOf(c.nodes, c.wires[1], null)).toEqual({ source: 'long_leg', sourceHandle: 'bnd:lin0', target: 'regime', targetHandle: 'in0' })
    const edges = createEdgeMapper()({
      graph: c, selectedWireId: null, labels: {}, placements: {}, lowZoom: false, diagByWire: new Map(), scope: 'regime',
    })
    expect(edges.map(e => e.id).sort()).toEqual(['w_rin', 'w_sma'])
  })

  it('frame layouts follow the view: the network on screen holds everything, outside frames are gone', () => {
    const g = makeGraph()
    const inLong = computeFrameLayouts(g.nodes, undefined, 'long_leg')
    expect(inLong.get('long_leg')!.w).toBeGreaterThan(1e8)
    expect(inLong.has('regime')).toBe(true)
    const inRegime = computeFrameLayouts(g.nodes, undefined, 'regime')
    expect([...inRegime.keys()]).toEqual(['regime'])
    // A card is not a frame, so nothing can be dropped into it.
    expect(computeFrameLayouts(makeGraph('card').nodes, undefined, null).has('regime')).toBe(false)
  })

  it('a child dragged far inside the dived network stays in it (the frame plugin sees the scope)', () => {
    const g = makeGraph()
    setLayoutScope('regime')
    const layouts = computeFrameLayouts(g.nodes)
    // Dragged 5000px away: still inside regime, no reparent.
    expect(reparentTargetOf(g.nodes, layouts, 'sma', { x: 5000, y: 5000, w: 100, h: 50 }, new Set(['sma']))).toBeUndefined()
    setLayoutScope('long_leg')
    const l2 = computeFrameLayouts(g.nodes)
    // A child of the regime frame dragged out of it lands in long_leg, never the root.
    expect(reparentTargetOf(g.nodes, l2, 'sma', { x: 5000, y: 5000, w: 100, h: 50 }, new Set(['sma']))).toBe('long_leg')
  })
})

// ---------------------------------------------------------------------------
// Card or frame (FA1)
// ---------------------------------------------------------------------------

describe('card and frame view (FA1)', () => {
  it('frame to card puts the card at the frame corner; back to frame keeps children relative', () => {
    const g = makeGraph()
    const corner = computeFrameLayouts(g.nodes, undefined, null).get('regime')!
    const card = setNetworkView(g, ['regime'], 'card')
    expect(card.nodes.regime.meta?.view).toBe('card')
    expect(card.nodes.regime.position).toEqual([corner.x, corner.y])
    expect(card.nodes.sma).toBe(g.nodes.sma) // children untouched

    // Move the card, then expand: the frame lands on the card.
    const moved = { ...card, nodes: { ...card.nodes, regime: { ...card.nodes.regime, position: [1000, 1000] as [number, number] } } }
    const frame = setNetworkView(moved, ['regime'], 'frame')
    expect(frame.nodes.regime.meta?.view).toBe('frame')
    const l = computeFrameLayouts(frame.nodes, undefined, null).get('regime')!
    expect(l.x).toBeCloseTo(1000, 6)
    expect(l.y).toBeCloseTo(1000, 6)
    const dx = frame.nodes.rsi.position[0] - frame.nodes.sma.position[0]
    expect(dx).toBe(g.nodes.rsi.position[0] - g.nodes.sma.position[0])
  })

  it('X shows the selected network as a card in one undoable commit; Shift+X back', () => {
    mount()
    selectNode('regime')
    press('x')
    expect(st().graph!.nodes.regime.meta?.view).toBe('card')
    expect(st().dirty).toBe(true)
    act(() => { st().undo() })
    expect(st().graph!.nodes.regime.meta?.view ?? 'frame').toBe('frame')
    act(() => { st().redo() })
    expect(st().graph!.nodes.regime.meta?.view).toBe('card')
    press('X', true)
    expect(st().graph!.nodes.regime.meta?.view).toBe('frame')
  })

  it('X does nothing (not handled) for a plain node', () => {
    mount()
    selectNode('aapl')
    const before = st().graph
    press('x')
    expect(st().graph).toBe(before)
    expect(getCommand('network.toggleView')!.label).toBe('Show as card')
  })
})

// ---------------------------------------------------------------------------
// Store: id-based network, viewports
// ---------------------------------------------------------------------------

describe('store: the network on screen is a node id', () => {
  it('renaming the network (or its parent) keeps the view inside it', () => {
    open()
    act(() => { st().enterNetwork('regime') })
    expect(st().network).toBe('/long_leg/regime')
    act(() => { commitRename(useNodeBuilderStore, 'regime', 'filter') })
    expect(st().currentNetworkId).toBe('regime')
    expect(st().network).toBe('/long_leg/filter')
    act(() => { commitRename(useNodeBuilderStore, 'long_leg', 'leg') })
    expect(st().network).toBe('/leg/filter')
    expect(currentParentId(st())).toBe('regime')
    // Undo the renames: still inside, the path follows.
    act(() => { st().undo(); st().undo() })
    expect(st().network).toBe('/long_leg/regime')
  })

  it('a network deleted while inside it climbs to the nearest one left, with a toast', () => {
    open()
    act(() => { st().enterNetwork('regime') })
    act(() => { st().removeNodes(['regime']) })
    expect(st().currentNetworkId).toBe('long_leg')
    expect(st().network).toBe('/long_leg')
    expect(st().flash?.text).toBe('regime no longer exists. Moved up to long_leg.')
  })

  it('keeps one viewport per network, and a saved graph keeps them in localStorage', () => {
    open(makeGraph(), 'g_dive')
    act(() => { st().setViewport({ x: 10, y: 20, zoom: 0.5 }) })
    act(() => { st().enterNetwork('regime') })
    expect(st().rememberedViewport()).toBeNull()
    act(() => { st().setViewport({ x: 1, y: 2, zoom: 2 }) })
    expect(st().viewports).toEqual({ [ROOT_NETWORK]: { x: 10, y: 20, zoom: 0.5 }, regime: { x: 1, y: 2, zoom: 2 } })
    const stored = JSON.parse(localStorage.getItem(viewportStorageKey('g_dive'))!)
    expect(stored).toEqual({ '/': { x: 10, y: 20, zoom: 0.5 }, regime: { x: 1, y: 2, zoom: 2 } })

    // Reopen the graph: the views come back from storage.
    open(makeGraph(), 'g_dive')
    expect(st().network).toBe(ROOT_NETWORK)
    expect(st().rememberedViewport()).toEqual({ x: 10, y: 20, zoom: 0.5 })
    expect(st().rememberedViewport('regime')).toEqual({ x: 1, y: 2, zoom: 2 })
    expect(st().rememberedViewport('/long_leg/regime')).toEqual({ x: 1, y: 2, zoom: 2 })
    // An unsaved graph writes nothing.
    open(makeGraph(), null)
    act(() => { st().setViewport({ x: 3, y: 3, zoom: 1 }) })
    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i)!)
    expect(keys.filter(k => k.startsWith('nb.viewports.'))).toEqual([viewportStorageKey('g_dive')])
  })

  it('a broken stored entry is ignored', () => {
    localStorage.setItem(viewportStorageKey('g_bad'), '{"/": {"x": "a"}, "n": 5}')
    open(makeGraph(), 'g_bad')
    expect(st().rememberedViewport()).toBeNull()
  })

  it('a load goes back to the root', () => {
    open()
    act(() => { st().enterNetwork('regime') })
    open()
    expect(st().network).toBe(ROOT_NETWORK)
    expect(currentParentId(st())).toBeNull()
  })

  it('crumbs and the status text', () => {
    const g = makeGraph()
    expect(crumbsOf(g, 'regime').map(c => c.label)).toEqual(['/', 'long_leg', 'regime'])
    expect(crumbsOf(g, null).map(c => c.id)).toEqual([null])
    expect(networkStatusText(g, 'regime')).toBe('in /long_leg/regime')
    expect(networkStatusText(g, null)).toBe('')
  })
})

// ---------------------------------------------------------------------------
// The canvas and the breadcrumb
// ---------------------------------------------------------------------------

describe('canvas: dive and up (S37)', () => {
  it('I on regime: 3 crumbs, the last current, and exactly regime\'s children plus its boundary nodes', () => {
    const { container } = mount()
    selectNode('regime')
    press('i')
    expect(st().currentNetworkId).toBe('regime')
    const nav = screen.getByRole('navigation', { name: 'Network path' })
    const crumbs = within(nav).getAllByRole('button')
    expect(crumbs.map(b => b.textContent)).toEqual(['/', 'Olong_leg', 'Nregime'])
    expect(crumbs[2]).toHaveAttribute('aria-current', 'location')
    expect(crumbs[0]).not.toHaveAttribute('aria-current')
    expect(drawnIds(container)).toEqual(['rin0', 'rout', 'rsi', 'sma'])
    // Boundary cards: 26px half-height cards; the output has one target handle only.
    expect(screen.getByTestId('nb-boundary-rin0')).toHaveAttribute('data-kind', 'input')
    const out = container.querySelector('.react-flow__node[data-id="rout"]')!
    expect(out.querySelectorAll('.react-flow__handle.target')).toHaveLength(1)
    expect(out.querySelectorAll('.react-flow__handle.source')).toHaveLength(0)
    // "You are inside": the outline and the announced label.
    expect(screen.getByTestId('nb-dive-outline')).toBeTruthy()
    expect(container.querySelector('[aria-label="Network /long_leg/regime"]')).not.toBeNull()
  })

  it('selection is cleared on dive; U goes up, selects the network and restores the saved view', async () => {
    const { container } = mount()
    // Lay the canvas out so the "off screen" check sees a real pane.
    const pane = container.querySelector('[data-network]') as HTMLElement
    pane.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1200, bottom: 900, width: 1200, height: 900, x: 0, y: 0, toJSON() {} }) as DOMRect
    await nextFrame()
    const saved = { x: 12, y: 34, zoom: 0.8 }
    act(() => { st().setViewport(saved) })
    selectNode('regime')
    press('i')
    expect(st().selectedNodeIds).toEqual([])
    expect(st().selectedNodeId).toBeNull()
    await nextFrame()
    press('u')
    expect(st().currentNetworkId).toBe('long_leg')
    expect(st().selectedNodeId).toBe('regime')
    await nextFrame()
    press('u')
    expect(st().network).toBe(ROOT_NETWORK)
    expect(st().selectedNodeId).toBe('long_leg')
    await nextFrame()
    await nextFrame()
    expect(getActiveCanvas()!.rf.getViewport()).toEqual(saved)
    // At the root U is not handled.
    expect(getCommand('network.up')!.when!(st())).toBe(false)
  })

  it('Shift+U goes to the root and selects the top-level network', () => {
    mount()
    act(() => { st().enterNetwork('regime') })
    press('U', true)
    expect(st().network).toBe(ROOT_NETWORK)
    expect(st().selectedNodeId).toBe('long_leg')
  })

  it('a crumb click goes to that level; the crumb menu frames a network in its parent', () => {
    mount()
    act(() => { st().enterNetwork('regime') })
    fireEvent.click(screen.getByTestId('nb-crumb-root'))
    expect(st().network).toBe(ROOT_NETWORK)
    expect(st().selectedNodeId).toBe('long_leg')
    act(() => { st().enterNetwork('regime') })
    fireEvent.contextMenu(screen.getByTestId('nb-crumb-regime'))
    fireEvent.click(screen.getByTestId('nb-crumb-frame'))
    expect(st().currentNetworkId).toBe('long_leg')
    expect(st().selectedNodeId).toBe('regime')
    expect(st().revealAlways).toBe(true)
    fireEvent.contextMenu(screen.getByTestId('nb-crumb-long_leg'))
    fireEvent.click(screen.getByTestId('nb-crumb-open'))
    expect(st().currentNetworkId).toBe('long_leg')
  })

  it('deeper than 4 levels the middle crumbs fold into …, which lists them', () => {
    const g = makeGraph()
    // /long_leg/regime/a/b/c: five levels.
    g.nodes.a = node('a', 'subnet', 'regime', [700, 400])
    g.nodes.b = node('b', 'subnet', 'a', [700, 400])
    g.nodes.c = node('c', 'subnet', 'b', [700, 400])
    mount(g)
    act(() => { st().enterNetwork('c') })
    const nav = screen.getByRole('navigation', { name: 'Network path' })
    expect(within(nav).getAllByRole('button').map(b => b.textContent)).toEqual(['/', 'Olong_leg', '…', 'Nb', 'Nc'])
    fireEvent.click(screen.getByTestId('nb-crumb-fold'))
    const menu = screen.getByRole('menu', { name: 'Hidden levels' })
    expect(within(menu).getAllByRole('menuitem').map(b => b.textContent)).toEqual(['regime', 'a'])
    fireEvent.click(within(menu).getByText('regime'))
    expect(st().currentNetworkId).toBe('regime')
    expect(st().selectedNodeId).toBe('a')
  })

  it('the pane menu has `Go up` only inside a network; the dive row only on an editable graph', () => {
    mount()
    const paneIds = () => buildMenu('pane', st(), true).flatMap(r => (r.type === 'item' ? [r.cmd.id] : []))
    expect(paneIds()).not.toContain('network.up')
    act(() => { st().enterNetwork('regime') })
    expect(paneIds()).toContain('network.up')
    selectNode('sma')
    expect(getCommand('network.dive')!.menu).toBe('node')
  })

  it('double-click on a frame tab or a card header dives', () => {
    const { container, rerenderStore } = mount()
    act(() => { fireEvent.doubleClick(screen.getByTestId('nb-frame-tab-regime')) })
    expect(st().currentNetworkId).toBe('regime')
    act(() => { st().enterNetwork(null) })
    selectNode('regime')
    press('x')
    rerenderStore()
    expect(container.querySelector('.react-flow__node[data-id="regime"]')!.className).toContain('react-flow__node-nbSubnet')
    act(() => { fireEvent.doubleClick(screen.getByTestId('nb-subnet-header-regime')) })
    expect(st().currentNetworkId).toBe('regime')
  })

  it('a rename while inside keeps the canvas on the same network', () => {
    const { container, rerenderStore } = mount()
    act(() => { st().enterNetwork('regime') })
    act(() => { commitRename(useNodeBuilderStore, 'regime', 'filter') })
    rerenderStore()
    expect(drawnIds(container)).toEqual(['rin0', 'rout', 'rsi', 'sma'])
    expect(screen.getByTestId('nb-crumb-regime').textContent).toContain('filter')
  })

  it('the read-only view dives by key too', () => {
    const g = { ...makeGraph(), readOnly: true }
    const { container } = render(
      <div className="nodebuilder-root" style={{ width: 1200, height: 900 }}>
        <Canvas graph={g} />
      </div>,
    )
    inView(container)
    act(() => { st().setSelection({ nodeIds: ['regime'], primary: 'regime' }) })
    act(() => { fireEvent.pointerDown(container.querySelector('[data-network]')!) })
    press('i')
    expect(drawnIds(container)).toEqual(['rin0', 'rout', 'rsi', 'sma'])
  })
})

// ---------------------------------------------------------------------------
// The card (S38)
// ---------------------------------------------------------------------------

describe('network card (S38)', () => {
  it('2 inputs and 1 output: 2 labelled input handles and 1 output handle; double stripe', () => {
    const g = makeGraph('card')
    g.nodes.rin1 = node('rin1', 'subnet_input', 'regime', [520, 340], { port: 1 })
    g.nodes.rin1.name = 'signal'
    const { container } = mount(g)
    const card = container.querySelector('.react-flow__node[data-id="regime"]')!
    const targets = card.querySelectorAll('.react-flow__handle.target')
    expect(targets).toHaveLength(2)
    expect(Array.from(targets).map(h => h.getAttribute('aria-label'))).toEqual(['input rin0', 'input signal'])
    expect(card.querySelectorAll('.react-flow__handle.source')).toHaveLength(1)
    expect(screen.getByTestId('nb-subnet-type-regime').textContent).toBe('subnet · 2 nodes')
    expect(screen.getByTestId('nb-subnet-stripe2-regime')).toBeTruthy()
    expect(screen.getByTestId('nb-subnet-regime')).toHaveAttribute('aria-label', 'Subnet regime, 2 nodes, 2 inputs, 1 output')
  })

  it('an asset instance reads `regime_filter @ v3` with a lock; promoted rows show and edit the instance', () => {
    mount(lockedGraph())
    const type = screen.getByTestId('nb-subnet-type-rf')
    expect(type.textContent).toBe('regime_filter @ v3🔒')
    expect(type.querySelector('[role="img"][aria-label="locked"]')).not.toBeNull()
    expect(screen.getByTestId('nb-subnet-rf').className).toContain('nb-subnet--locked')
    const field = screen.getByTestId('nb-param-rf-lookback') as HTMLInputElement
    expect(field.value).toBe('30')
    act(() => { field.focus() })
    act(() => { fireEvent.change(field, { target: { value: '40' } }) })
    act(() => { fireEvent.blur(field) })
    expect(st().graph!.nodes.rf.params.lookback).toBe(40)
  })

  it('asset_missing: the type slot says missing and the card has the error border', () => {
    mount(lockedGraph())
    act(() => {
      setServerDiagnostics([{ code: 'asset_missing', severity: 'error', node_id: 'rf', message: 'gone' } as never])
    })
    expect(screen.getByTestId('nb-subnet-type-rf').textContent).toContain('missing')
    expect(screen.getByTestId('nb-subnet-rf').className).toContain('nb-subnet--error')
  })

  it('type slot copy', () => {
    const base = { inputs: [], outputId: 'o', childCount: 7, asset: null }
    expect(cardTypeText({ type: 'subnet', card: base }).text).toBe('subnet · 7 nodes')
    expect(cardTypeText({ type: 'subnet', card: { ...base, childCount: 0 } }).text).toBe('subnet · empty')
    expect(cardTypeText({ type: 'subnet', card: { ...base, outputId: null } }).noOutput).toBe(true)
    const asset = { ...base, asset: { name: 'regime_filter', version: 3, locked: false } }
    expect(cardTypeText({ type: 'subnet', card: asset }).text).toBe('regime_filter v3 · local copy')
    expect(cardTypeText({ type: 'subnet', card: asset, missing: true }).text).toBe('regime_filter @ v3 · missing')
  })
})

// ---------------------------------------------------------------------------
// Locked instance (S38)
// ---------------------------------------------------------------------------

describe('inside a locked asset instance (S38)', () => {
  it('shows the locked bar and the dashed outline; children come from the library, read-only', () => {
    const asset = {
      nodes: {
        sma: node('sma', 'sma', null, [0, 0], { period: 50 }),
        out: node('out', 'subnet_output', null, [0, 120]),
      },
      wires: [{ id: 'w1', from: 'sma', to: 'out', from_port: 'out', to_port: 'in0' }] as GraphWire[],
    }
    const source = vi.fn<AssetNetworkSource>(() => asset)
    const off = registerAssetNetworkSource(source)
    try {
      const { container } = mount(lockedGraph())
      act(() => { st().enterNetwork('rf') })
      expect(screen.getByTestId('nb-locked-bar').textContent).toContain('Locked asset regime_filter v3. Unlock to edit a local copy.')
      expect(screen.getByTestId('nb-dive-outline').className).toContain('nb-dive-outline--locked')
      expect(drawnIds(container)).toEqual(['rf::out', 'rf::sma'])
      expect(source).toHaveBeenCalledWith({ name: 'regime_filter', version: 3 })
      // Read-only: nothing is draggable, and the graph is unchanged.
      const n = container.querySelector('.react-flow__node[data-id="rf::sma"]')!
      expect(n.className).not.toContain('draggable')
      expect(st().graph!.nodes['rf::sma']).toBeUndefined()
      // The assets.unlock command is registered, so Unlock is live (the flow is in assetIntegration.test.tsx).
      expect(within(screen.getByTestId('nb-locked-bar')).getByRole('button', { name: 'Unlock' })).not.toBeDisabled()
      // Drawn at the instance's place: the asset keeps positions relative to it.
      expect(withLockedChildren(st().graph!, 'rf').nodes['rf::out'].position).toEqual([900, 120])
    } finally {
      off()
    }
  })

  it('composite ids for the copy; the graph itself when the definition is not loaded', () => {
    const g = lockedGraph()
    expect(withLockedChildren(g, 'rf')).toBe(g)
    const off = registerAssetNetworkSource(() => ({ nodes: { a: node('a', 'sma', null, [0, 0]) }, wires: [] }))
    try {
      const v = withLockedChildren(g, 'rf')
      expect(v.nodes['rf::a'].parent).toBe('rf')
      expect(v.readOnly).toBe(true)
    } finally {
      off()
    }
  })
})
