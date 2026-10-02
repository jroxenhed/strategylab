/**
 * W3 review-fix pass (F435): store, registries, coordinates and canvas keys.
 * One block per finding id; the Inspector fixes are in
 * inspector.fixes.test.tsx and the menu/status/node-chrome fixes in
 * chrome.fixes.test.tsx.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useRef } from 'react'
import type { Node as RFNode } from '@xyflow/react'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import Canvas from '../Canvas'
import { useGlobalKeys } from '../commands/useGlobalKeys'
import {
  dispatchKey,
  findCommands,
  getActiveCanvas,
  getCommand,
  isCommandEnabled,
  listCommands,
  registerCommand,
  setActiveCanvas,
  type CommandScope,
} from '../commands'
import { pluginsWithHook, registerCanvasPlugin, listCanvasPlugins, type CanvasCtx } from '../canvasPlugins'
import { getNodeTypes, registerNodeType } from '../nodeTypes'
import { getEdgeTypes, registerEdgeType } from '../edgeTypes'
import {
  absoluteLookup,
  createNodeMapper,
  graphPositionOf,
  listRfNodeSources,
  registerRfNodeSource,
  rfPositionOf,
  DEFAULT_NODE_SIZE as RF_DEFAULT_SIZE,
} from '../rfMapping'
import { DEFAULT_NODE_SIZE } from '../geometry'
import { DEFAULT_NODE_SIZE as WIRE_DEFAULT_SIZE } from '../plugins/wireOps'
import { dragStopMoves } from '../canvasHelpers'
import { mountCanvas, getScreenGraph, publishScreenGraph, useScreenGraph } from '../screen'
import { useNodeBuilderStore } from '../store'
import { registerGraphReconciler, registerSelectableIds } from '../store/reconcile'
import { currentParentId, ROOT_NETWORK } from '../store/view'
import { opRefitBoxesOf } from '../store/annotations'
import { createAnnotationNodeMapper } from '../plugins/boxDrag'
import { plugin as contextMenus } from '../plugins/contextMenus'
import { mapBoxMembers, removeNodes, replaceNode } from '../operations'
import { copyFromGraph, pastePathRenames, pastePayload } from '../clipboard'
import { resetDiagnostics } from '../useDiagnostics'

// ── Fixtures ────────────────────────────────────────────────────────────────

function node(id: string, type: string, x: number, y: number, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [x, y], display: false, bypass: false, ...extra }
}

function makeGraph(): Graph {
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', 0, 0, { params: { symbol: 'AAPL', interval: '1d' } }),
      r: node('r', 'rsi', 0, 150, { params: { period: 14 } }),
      e: node('e', 'entry', 0, 300),
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'r', to: 'e', from_port: 'out', to_port: 'in0' },
    ],
    annotations: {
      boxes: [{ id: 'box1', label: '', color: 'network', rect: [-24, -24, 224, 258], members: ['t', 'r'], parent: null }],
      notes: [{ id: 'note1', text: 'hi', rect: [400, 0, 200, 88], color: 'amber', parent: null }],
    },
  }
}

const st = () => useNodeBuilderStore.getState()
const open = (g: Graph = makeGraph()) => act(() => { st().openGraph(g, { id: null, rev: 0, name: 'test' }) })

beforeAll(() => {
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

const offs: Array<() => void> = []
afterEach(() => {
  cleanup()
  for (const off of offs.splice(0)) off()
  resetDiagnostics()
  setActiveCanvas(null)
  st().discardEdits()
  vi.useRealTimers()
})

function BuilderRoot({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useGlobalKeys(ref)
  return <div ref={ref} className="nodebuilder-root" style={{ width: 800, height: 600 }}>{children}</div>
}

function mount(g: Graph = makeGraph()) {
  open(g)
  const utils = render(<BuilderRoot><Canvas graph={st().graph!} /></BuilderRoot>)
  const canvas = utils.container.querySelector('.nodebuilder-root .nodebuilder-root') as HTMLElement
  canvas.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  ;(utils.container.querySelector('.nodebuilder-root') as HTMLElement).getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  return { ...utils, canvas }
}

const press = (init: KeyboardEventInit) => act(() => { fireEvent.keyDown(document.body, init) })

// ── EA-1: coordinate spaces ─────────────────────────────────────────────────

describe('EA-1 graph positions are absolute; React Flow children are parent-relative', () => {
  it('rfPositionOf and graphPositionOf round-trip under a parent', () => {
    const parentAbs = { x: 100, y: 50 }
    const rf = rfPositionOf([130, 90], parentAbs)
    expect(rf).toEqual({ x: 30, y: 40 })
    expect(graphPositionOf({ position: rf, parentId: 'p' }, () => parentAbs)).toEqual([130, 90])
    // No parent: both are the same.
    expect(rfPositionOf([7, 8], null)).toEqual({ x: 7, y: 8 })
    expect(graphPositionOf({ position: { x: 7, y: 8 } }, () => null)).toEqual([7, 8])
  })

  it('absoluteLookup resolves nested parents', () => {
    const nodes = [
      { id: 'a', position: { x: 100, y: 100 } },
      { id: 'b', position: { x: 10, y: 20 }, parentId: 'a' },
      { id: 'c', position: { x: 1, y: 2 }, parentId: 'b' },
    ]
    const absOf = absoluteLookup(nodes)
    expect(absOf('b')).toEqual({ x: 110, y: 120 })
    expect(graphPositionOf(nodes[2], absOf)).toEqual([111, 122])
  })

  it('the default drag commit saves graph positions of child nodes', () => {
    const nodes = [
      { id: 'frame', position: { x: 200, y: 0 } },
      { id: 'kid', position: { x: 5, y: 5 }, parentId: 'frame' },
    ]
    const absOf = absoluteLookup(nodes)
    const moves = dragStopMoves(null, [nodes[1]], n => graphPositionOf(n, absOf))
    expect(moves).toEqual([{ id: 'kid', position: [205, 5] }])
  })

  it('a pasted network moves its children by the same offset', () => {
    open()
    const g = st().graph!
    const withNet: Graph = {
      ...g,
      nodes: {
        ...g.nodes,
        sub: node('sub', 'subnet', 500, 500),
        inner: node('inner', 'rsi', 520, 540, { parent: 'sub' }),
      },
    }
    const payload = copyFromGraph(withNet, { nodeIds: ['sub'] })!
    const out = pastePayload(withNet, payload, { offset: [100, 0] })
    const newSub = out.idMap.get('sub')!
    const newInner = out.idMap.get('inner')!
    expect(out.graph.nodes[newSub].position).toEqual([600, 500])
    expect(out.graph.nodes[newInner].position).toEqual([620, 540])
    expect(out.graph.nodes[newInner].parent).toBe(newSub)
  })
})

// ── EA-2 / EA-3 / EA-14: the command registry ───────────────────────────────

const COMMAND_MODULES: Record<string, () => Promise<{ commands?: readonly { id: string }[] }>> = {
  annotations: () => import('../commands/annotations'),
  clipboard: () => import('../commands/clipboard'),
  edit: () => import('../commands/edit'),
  flags: () => import('../commands/flags'),
  help: () => import('../commands/help'),
  history: () => import('../commands/history'),
  inspector: () => import('../commands/inspector'),
  layout: () => import('../commands/layout'),
  params: () => import('../commands/params'),
  selection: () => import('../commands/selection'),
  unsupported: () => import('../commands/unsupported'),
  view: () => import('../commands/view'),
  wires: () => import('../commands/wires'),
}

describe('EA-2 a command module loaded before the registry still registers', () => {
  for (const [name, load] of Object.entries(COMMAND_MODULES)) {
    it(`commands/${name}.ts first`, async () => {
      vi.resetModules()
      const mod = await load()
      const registry = await import('../commands/index')
      const ids = new Set(registry.listCommands().map(c => c.id))
      expect(mod.commands?.length ?? 0).toBeGreaterThan(0)
      for (const c of mod.commands ?? []) expect(ids.has(c.id), c.id).toBe(true)
    })
  }
  it('also through a helper that imports the registry (viewOps, contextMenuModel)', async () => {
    vi.resetModules()
    await import('../viewOps')
    await import('../contextMenuModel')
    const registry = await import('../commands/index')
    expect(registry.getCommand('view.frameAll')).not.toBeNull()
    expect(registry.getCommand('wires.insertNode')).not.toBeNull()
  })
})

describe('EA-3 priority decides same-key order', () => {
  const both: ReadonlySet<CommandScope> = new Set<CommandScope>(['canvas', 'global'])
  it('a higher priority gets the first try, whatever the registration order', () => {
    open()
    const hi = { id: 't.hi', label: 'hi', keys: ['f9'], priority: 10, run: vi.fn() }
    const lo = { id: 't.lo', label: 'lo', keys: ['f9'], run: vi.fn() }
    offs.push(registerCommand(hi))
    offs.push(registerCommand(lo))
    expect(findCommands('f9', both).map(c => c.id)).toEqual(['t.hi', 't.lo'])
  })
})

describe('EA-14 / EA-9 mounted canvases and the graph on screen', () => {
  const fake = (graph: Graph | null = null) => ({ graph: () => graph, focus: () => {} }) as unknown as CanvasCtx
  it('the last mounted canvas is active; unmounting it gives the one below back', () => {
    const a = fake()
    const b = fake()
    const offA = mountCanvas(a)
    const offB = mountCanvas(b)
    expect(getActiveCanvas()).toBe(b)
    offB()
    expect(getActiveCanvas()).toBe(a)
    offA()
    expect(getActiveCanvas()).toBeNull()
  })

  it('useScreenGraph re-renders when a canvas publishes a new graph', () => {
    const ctx = fake()
    offs.push(mountCanvas(ctx))
    const seen: Array<Graph | null> = []
    function Probe() {
      const { graph } = useScreenGraph()
      seen.push(graph)
      return null
    }
    render(<Probe />)
    const g1 = makeGraph()
    const g2 = { ...makeGraph(), readOnly: true }
    act(() => { publishScreenGraph(ctx, g1, true) })
    act(() => { publishScreenGraph(ctx, g2, false) })
    expect(seen.at(-1)).toBe(g2)
    expect(seen).toContain(g1)
    expect(getScreenGraph().editable).toBe(false)
  })

  it('a mounted read-only canvas publishes its graph for panels', () => {
    const g = { ...makeGraph(), readOnly: true }
    render(<BuilderRoot><Canvas graph={g} /></BuilderRoot>)
    expect(getScreenGraph().graph).toBe(g)
    expect(getScreenGraph().editable).toBe(false)
  })
})

// ── EA-5 / FC-10 / EA-4: reconcile, loads, the current network ─────────────

describe('EA-5 graph reconcilers', () => {
  it('a registered reconciler runs in the same write as commit, undo and redo', () => {
    open()
    const causes: string[] = []
    offs.push(registerGraphReconciler((_s, _next, cause) => { causes.push(cause) }))
    let writes = 0
    const unsub = useNodeBuilderStore.subscribe(() => { writes += 1 })
    act(() => { st().moveNode('t', [1, 1]) })
    act(() => { st().undo() })
    act(() => { st().redo() })
    unsub()
    expect(causes).toEqual(['commit', 'undo', 'redo'])
    expect(writes).toBe(3)
  })

  it('a box or note in edit stops editing when an undo removes it', () => {
    open()
    let id: string | null = null
    act(() => { id = st().addNote({ text: 'x' }) })
    act(() => { st().startAnnotationEdit(id!) })
    act(() => { st().undo() })
    expect(st().editingAnnotationId).toBeNull()
  })

  it('registerSelectableIds keeps a source item selected through a commit', () => {
    open()
    offs.push(registerSelectableIds(() => ['frame1']))
    act(() => { st().setSelection({ annotationIds: ['frame1', 'gone'] }) })
    act(() => { st().moveNode('t', [2, 2]) })
    expect(st().selectedAnnotationIds).toEqual(['frame1'])
  })
})

describe('FC-10 / EA-4 loads reset the network and the annotation in edit', () => {
  it('open, new and discard put the network back to the root and stop editing', () => {
    open()
    act(() => { useNodeBuilderStore.setState({ network: '/sub1', editingAnnotationId: 'note1' }) })
    open()
    expect(st().network).toBe(ROOT_NETWORK)
    expect(st().editingAnnotationId).toBeNull()
    act(() => { useNodeBuilderStore.setState({ network: '/sub1', editingAnnotationId: 'note1' }) })
    act(() => { st().newGraph() })
    expect(st().network).toBe(ROOT_NETWORK)
    expect(st().editingAnnotationId).toBeNull()
  })

  it('a network that no longer exists after a commit goes back to the root (EA-4)', () => {
    const g = makeGraph()
    g.nodes.sub = node('sub', 'subnet', 500, 0)
    open(g)
    act(() => { st().setNetwork('/sub') })
    expect(currentParentId(st())).toBe('sub')
    act(() => { st().removeNodes(['sub']) })
    expect(st().network).toBe(ROOT_NETWORK)
    expect(currentParentId(st())).toBeNull()
  })

  it('new notes go into the network on screen, and the annotations source draws only that network (EA-4)', () => {
    const g = makeGraph()
    g.nodes.sub = node('sub', 'subnet', 500, 0)
    open(g)
    act(() => { st().setNetwork('/sub') })
    let id: string | null = null
    act(() => { id = st().addNote({ text: 'inside' }) })
    const note = st().graph!.annotations.notes.find(n => n.id === id)!
    expect(note.parent).toBe('sub')
    const map = createAnnotationNodeMapper()
    expect(map(st().graph!, true, 'sub').map(n => n.id)).toEqual([id])
    expect(map(st().graph!, true, null).map(n => n.id).sort()).toEqual(['box1', 'note1'])
  })
})

// ── EA-6: node mapper keyed on the node object ─────────────────────────────

describe('EA-6 the card gets the whole node', () => {
  it('reuses the React Flow node while the node object is the same; data.node is the node', () => {
    const g = makeGraph()
    const map = createNodeMapper()
    const first = map(g.nodes, true)
    const again = map({ ...g.nodes }, true)
    expect(again[0]).toBe(first[0])
    expect((first[1].data as { node?: GraphNode }).node).toBe(g.nodes.r)
    const renamed = { ...g.nodes, r: { ...g.nodes.r, name: 'rsi_fast' } }
    const after = map(renamed, true)
    expect(after[1]).not.toBe(first[1])
    expect((after[1].data as { nodePath: string }).nodePath).toBe('/rsi_fast')
    expect(after[0]).toBe(first[0])
  })
})

// ── EA-10 / FC-3: ctx.graph() sees a commit made earlier in the event ───────

describe('EA-10 ctx.graph() while editing', () => {
  it('returns the store graph right after a commit, before React re-renders', () => {
    mount()
    const ctx = getActiveCanvas()!
    act(() => {
      st().moveNode('t', [42, 42])
      expect(ctx.graph()).toBe(st().graph)
      expect(ctx.graph().nodes.t.position).toEqual([42, 42])
    })
  })
})

// ── EA-11: read-only safety is a command property ──────────────────────────

describe('EA-11 read-only keys come from readOnlyOk', () => {
  it('no menu-layout or read-only id list is needed: Esc and Cmd+A work read-only', () => {
    expect(getCommand('selection.all')?.readOnlyOk).toBe(true)
    expect(getCommand('selection.clear')?.readOnlyOk).toBe(true)
    expect(getCommand('edit.nudge')?.readOnlyOk).toBeFalsy()
  })
})

// ── EA-12: overrides restore the built-in ───────────────────────────────────

describe('EA-12 removing an override brings back the built-in', () => {
  it('node types, edge types, node sources and plugins', () => {
    const builtTicker = getNodeTypes().ticker
    const offNode = registerNodeType('ticker', () => null)
    expect(getNodeTypes().ticker).not.toBe(builtTicker)
    offNode()
    expect(getNodeTypes().ticker).toBe(builtTicker)

    const builtAttr = getEdgeTypes().attr
    const offEdge = registerEdgeType('attr', () => null)
    offEdge()
    expect(getEdgeTypes().attr).toBe(builtAttr)

    const builtSource = listRfNodeSources().find(s => s.id === 'annotations')!
    const offSource = registerRfNodeSource({ id: 'annotations', nodes: () => [] })
    expect(listRfNodeSources().find(s => s.id === 'annotations')).not.toBe(builtSource)
    offSource()
    expect(listRfNodeSources().find(s => s.id === 'annotations')).toBe(builtSource)

    const builtWireOps = listCanvasPlugins().find(p => p.id === 'wireOps')!
    const index = listCanvasPlugins().indexOf(builtWireOps)
    const offPlugin = registerCanvasPlugin({ id: 'wireOps' })
    expect(listCanvasPlugins().indexOf(listCanvasPlugins().find(p => p.id === 'wireOps')!)).toBe(index)
    offPlugin()
    expect(listCanvasPlugins().find(p => p.id === 'wireOps')).toBe(builtWireOps)
  })
})

// ── EA-13: one node-size constant ───────────────────────────────────────────

describe('EA-13 node sizes live in geometry.ts', () => {
  it('rfMapping and wireOps re-export the same constant', () => {
    expect(RF_DEFAULT_SIZE).toBe(DEFAULT_NODE_SIZE)
    expect(WIRE_DEFAULT_SIZE).toBe(DEFAULT_NODE_SIZE)
  })
})

// ── IP-5 / IP-7 ─────────────────────────────────────────────────────────────

describe('IP-5 select(null) with nothing selected writes nothing', () => {
  it('no store write, and lists keep their identity', () => {
    open()
    let writes = 0
    const unsub = useNodeBuilderStore.subscribe(() => { writes += 1 })
    act(() => { st().select(null) })
    expect(writes).toBe(0)
    act(() => { st().select('t') })
    const wires = st().selectedWireIds
    act(() => { st().select('r') })
    expect(st().selectedWireIds).toBe(wires)
    unsub()
  })
})

describe('IP-7 per-hook plugin lists', () => {
  it('pluginsWithHook lists only plugins with the hook and keeps its identity until a registration', () => {
    const a = pluginsWithHook('onPointerMove')
    expect(a.every(p => p.onPointerMove)).toBe(true)
    expect(pluginsWithHook('onPointerMove')).toBe(a)
    const off = registerCanvasPlugin({ id: 't.ptr', onPointerMove: () => {} })
    const b = pluginsWithHook('onPointerMove')
    expect(b).not.toBe(a)
    expect(b.some(p => p.id === 't.ptr')).toBe(true)
    off()
    expect(pluginsWithHook('onPointerMove').some(p => p.id === 't.ptr')).toBe(false)
  })
})

// ── UX-04: right-click makes the clicked node the primary ───────────────────

describe('UX-04 the right-clicked node is the menu subject', () => {
  it('keeps a multi-selection but makes the clicked node primary', () => {
    open()
    act(() => { st().setSelection({ nodeIds: ['t', 'r'], primary: 'r' }) })
    const ctx = {
      store: useNodeBuilderStore,
      graph: () => st().graph!,
      rf: { screenToFlowPosition: (p: { x: number; y: number }) => p },
    } as unknown as CanvasCtx
    const e = { preventDefault: () => {}, clientX: 1, clientY: 1, target: document.body } as unknown as React.MouseEvent
    act(() => { contextMenus.onNodeContextMenu!(e, { id: 't', type: 'ticker' } as RFNode, ctx) })
    expect(st().selectedNodeIds).toEqual(['t', 'r'])
    expect(st().selectedNodeId).toBe('t')
  })
})

// ── UX-13: Esc, Cmd+A, arrow nudge ──────────────────────────────────────────

describe('UX-13 Esc, Cmd+A and arrow nudge', () => {
  it('Cmd+A selects every node in the network; Esc clears', () => {
    mount()
    press({ key: 'a', metaKey: true })
    expect([...st().selectedNodeIds].sort()).toEqual(['e', 'r', 't'])
    press({ key: 'Escape' })
    expect(st().selectedNodeIds).toEqual([])
  })

  it('arrows nudge 1px, Shift+arrows 24px; a burst is one undo step', () => {
    mount()
    act(() => { st().setSelection({ nodeIds: ['r'], primary: 'r' }) })
    const g0 = st().graph
    press({ key: 'ArrowRight' })
    press({ key: 'ArrowRight' })
    press({ key: 'ArrowDown', shiftKey: true })
    expect(st().graph!.nodes.r.position).toEqual([2, 174])
    expect(st().past).toHaveLength(1)
    act(() => { st().undo() })
    expect(st().graph).toBe(g0)
  })

  it('a nudge after another edit, or after the window, is a new step', () => {
    mount()
    act(() => { st().setSelection({ nodeIds: ['r'], primary: 'r' }) })
    press({ key: 'ArrowLeft' })
    act(() => { st().updateNodeParams('r', { period: 9 }) })
    press({ key: 'ArrowLeft' })
    expect(st().past).toHaveLength(3)
    vi.useFakeTimers()
    vi.setSystemTime(Date.now() + 5000)
    press({ key: 'ArrowLeft' })
    expect(st().past).toHaveLength(4)
  })

  it('in the read-only view Cmd+A still works and arrows do nothing', () => {
    open()
    const e = new KeyboardEvent('keydown', { key: 'a', metaKey: true, cancelable: true })
    const g = makeGraph()
    const ctx = { graph: () => g } as unknown as CanvasCtx
    act(() => { st().discardEdits() })
    act(() => { dispatchKey(e, { scopes: new Set<CommandScope>(['canvas']), canvas: ctx, readOnly: true }) })
    expect([...st().selectedNodeIds].sort()).toEqual(['e', 'r', 't'])
    const arrow = new KeyboardEvent('keydown', { key: 'ArrowLeft', cancelable: true })
    expect(dispatchKey(arrow, { scopes: new Set<CommandScope>(['canvas']), canvas: ctx, readOnly: true })).toBe(false)
  })
})

// ── UX-07: double-click empty canvas ────────────────────────────────────────

describe('UX-07 double-click on empty canvas opens the Tab menu', () => {
  it('opens on the pane, not on a node', () => {
    const { canvas } = mount()
    const pane = canvas.querySelector('.react-flow__pane') as HTMLElement
    act(() => { fireEvent.doubleClick(pane, { clientX: 100, clientY: 100 }) })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

// ── UX-14: unsupported nodes ────────────────────────────────────────────────

describe('UX-14 unsupported nodes are not renamed or bypassed', () => {
  it('F2 rename is off and B skips the node', () => {
    const g = makeGraph()
    g.nodes.u = node('u', 'no_such_type', 300, 0)
    open(g)
    act(() => { st().setSelection({ nodeIds: ['u'], primary: 'u' }) })
    expect(isCommandEnabled(getCommand('edit.rename')!)).toBe(false)
    getCommand('flags.toggleBypass')!.run({ canvas: null, store: useNodeBuilderStore, event: null })
    expect(st().graph!.nodes.u.bypass).toBe(false)
    expect(st().flash?.text).toBe('Unsupported nodes cannot be bypassed')
  })
})

// ── FC-1: a failing onCreate never strands the Tab menu ─────────────────────

describe('FC-1 Tab menu onCreate that throws', () => {
  it('keeps the node, selects it, closes the menu and flashes', () => {
    mount()
    const ctx = getActiveCanvas()!
    const onCreate = () => { throw new Error('Cannot add wire: no port') }
    act(() => { ctx.openTabMenu({ screen: { x: 100, y: 100 }, onCreate }) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: 'above' } }) })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
    const above = Object.values(st().graph!.nodes).find(n => n.type === 'above')
    expect(above).toBeTruthy()
    expect(st().selectedNodeId).toBe(above!.id)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(st().flash?.text).toBe('Cannot add wire: no port')
  })
})

// ── FC-2 / FC-5 / FC-9 ──────────────────────────────────────────────────────

describe('FC-2 box members never name a deleted node', () => {
  it('removeNodes drops the id; replaceNode hands membership to the new node', () => {
    const g = makeGraph()
    const out = removeNodes(g, ['r'])
    expect(out.annotations.boxes[0].members).toEqual(['t'])
    // Untouched boxes keep their identity.
    expect(removeNodes(g, ['e']).annotations.boxes[0]).toBe(g.annotations.boxes[0])
    const withNew: Graph = { ...g, nodes: { ...g.nodes, n2: node('n2', 'sma', 900, 900) } }
    const replaced = replaceNode(withNew, 'r', 'n2')
    expect(replaced.annotations.boxes[0].members).toEqual(['t', 'n2'])
    expect(mapBoxMembers(g, id => id)).toBe(g)
  })
})

describe('FC-5 tidy refits the boxes of moved nodes', () => {
  it('opRefitBoxesOf wraps the members again', () => {
    const g = makeGraph()
    const moved: Graph = { ...g, nodes: { ...g.nodes, r: { ...g.nodes.r, position: [600, 600] } } }
    const out = opRefitBoxesOf(moved, ['r'], () => ({ w: 100, h: 50 }))
    const rect = out.annotations.boxes[0].rect
    expect(rect[0] + rect[2]).toBeGreaterThanOrEqual(700)
    expect(rect[1] + rect[3]).toBeGreaterThanOrEqual(650)
    expect(opRefitBoxesOf(g, ['e'], () => null)).toBe(g)
  })
})

describe('FC-9 paste renames feed rewritePathRefs', () => {
  it('lists the old and new paths of renamed pasted nodes, in the payload tree', () => {
    const g = makeGraph()
    const payload = copyFromGraph(g, { nodeIds: ['r'] })!
    const out = pastePayload(g, payload, { offset: [10, 10] })
    const id = out.idMap.get('r')!
    expect(out.graph.nodes[id].name).not.toBe('r')
    expect(pastePathRenames(payload, out.graph.nodes, out.idMap)).toEqual([['/r', `/${out.graph.nodes[id].name}`]])
  })
  it.todo('W7: a ch("../rsi") ref inside a pasted subnet follows the sibling renamed on paste')
})

// ── UX-03 (store side), IP-4 ────────────────────────────────────────────────

describe('IP-4 the first fit of Edit this graph waits for the tidy', () => {
  it('loadFromAutoRender marks the layout pending until the tidy settles', async () => {
    act(() => { st().loadFromAutoRender({ ...makeGraph(), readOnly: true }) })
    expect(st().layoutPending).toBe(true)
    await vi.waitFor(() => expect(st().layoutPending).toBe(false), { timeout: 10000 })
    // Other loads never wait.
    open()
    expect(st().layoutPending).toBe(false)
  })
})

// The registry itself still lists everything (sanity for the lazy glob).
describe('registry sanity', () => {
  it('lists the built-in commands', () => {
    const ids = listCommands().map(c => c.id)
    for (const id of ['history.undo', 'edit.addNode', 'selection.all', 'params.reset']) expect(ids).toContain(id)
  })
})
