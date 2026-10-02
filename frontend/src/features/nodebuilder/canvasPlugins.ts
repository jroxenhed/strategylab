/**
 * Canvas plugins (W3 pre-step 3.0, critic 19).
 *
 * A plugin hooks into the canvas's React Flow handlers without editing
 * Canvas.tsx. Canvas calls every registered plugin, in registration order,
 * from its handlers. Each hook gets a `CanvasCtx`: the React Flow instance,
 * the store, the pointer and a few canvas actions.
 *
 * Plugin modules live in `plugins/*.ts` and are loaded automatically. A
 * module exports its plugin instead of registering it:
 *
 *   // plugins/myPlugin.ts
 *   export const plugin: CanvasPlugin = { id: 'myPlugin', onMove(vp, ctx) { ... } }
 *
 * (`export const plugins = [a, b]` works too.) Registering at load time from
 * a module in plugins/ would run before this file has finished loading;
 * exporting avoids that. `registerCanvasPlugin` is for code that runs later
 * (tests, effects).
 *
 * Drag-stop rules: Canvas wraps the whole drag stop in one store batch, so
 * every commit a plugin makes there plus the default move is ONE undo step.
 * A hook that returns true has handled the move itself and the default
 * commit is skipped. Every plugin is still called.
 */

import type {
  Connection,
  Edge as RFEdge,
  FinalConnectionState,
  HandleType,
  Node as RFNode,
  ReactFlowInstance,
  Viewport,
} from '@xyflow/react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import type { Graph } from '../../api/nodebuilder'
import type { NodeBuilderStoreApi } from './store'

export interface XY {
  x: number
  y: number
}

/** Options for opening the Tab menu from code (a context menu, the Inspector). */
export interface TabMenuRequest {
  /** Screen point for the menu's top-left. Default: the pointer, or the canvas middle. */
  screen?: XY
  /** Flow position for the new node. Default: the flow position of `screen`. */
  flow?: XY
  /**
   * The key press that asked for the menu. When it came from outside the
   * canvas (focus on a toolbar button), the menu does not open and
   * openTabMenu returns false, so Tab keeps moving focus.
   */
  keyEvent?: KeyboardEvent | null
  /**
   * Runs right after the new node is added, inside the same store batch, so
   * extra wiring (splice into a wire, connect into a port) is the same undo
   * step. When given, the default auto-wire from the selection is skipped
   * (and the menu does not offer it): onCreate does the wiring.
   */
  onCreate?(nodeId: string, ctx: CanvasCtx): void
}

/** What a Delete press removes, split by kind. */
export interface DeleteRequest {
  /** Graph node ids. */
  nodeIds: string[]
  /** Wire ids. */
  wireIds: string[]
  /** Selected canvas items that are not graph nodes (boxes, notes). */
  otherIds: string[]
  /** False for Shift+Delete (no reconnect). */
  rewire: boolean
}

export interface CanvasCtx {
  rf: ReactFlowInstance
  store: NodeBuilderStoreApi
  /** Last pointer position in flow coordinates (the middle of the canvas when the pointer is not over it). */
  pointer(): XY
  /** True while the pointer is over the canvas. */
  pointerOnCanvas(): boolean
  /**
   * The graph on screen. In the read-only view it is not in the store.
   * While editing it is the store graph, so it already holds a commit made
   * earlier in the same event (by another plugin, EA-10).
   */
  graph(): Graph
  /**
   * A React Flow node's position in graph coordinates (absolute flow units,
   * EA-1). Use it for every position a drag callback hands out: a W5 child
   * node's `position` is relative to its parent frame.
   */
  graphPosition(n: RFNode): [number, number]
  /** True when the graph on screen can be edited. */
  editable(): boolean
  /** The canvas root element. It holds keyboard focus (spec 0.8). */
  container(): HTMLElement | null
  /** Give the canvas root keyboard focus (menus call this when they close). */
  focus(): void
  /** Open the Tab menu (editable graphs only). Returns false when it did not open. */
  openTabMenu(req?: TabMenuRequest): boolean
  /** Delete the selection as one undo step. Returns false when nothing was selected. */
  deleteSelection(opts?: { rewire?: boolean }): boolean
}

export interface CanvasPlugin {
  id: string
  onNodeDragStart?(e: ReactMouseEvent, node: RFNode, nodes: RFNode[], ctx: CanvasCtx): void
  onNodeDrag?(e: ReactMouseEvent, node: RFNode, nodes: RFNode[], ctx: CanvasCtx): void
  /** Return true when the plugin committed the move itself (skip the default commit). */
  onNodeDragStop?(e: ReactMouseEvent, node: RFNode, nodes: RFNode[], ctx: CanvasCtx): boolean | void
  /** Dragging the box drawn around a marquee selection (not a node). */
  onSelectionDragStart?(e: ReactMouseEvent, nodes: RFNode[], ctx: CanvasCtx): void
  onSelectionDrag?(e: ReactMouseEvent, nodes: RFNode[], ctx: CanvasCtx): void
  /** Return true when the plugin committed the move itself (skip the default commit). */
  onSelectionDragStop?(e: ReactMouseEvent, nodes: RFNode[], ctx: CanvasCtx): boolean | void
  /** A wire end was dropped on a port. While any plugin has this hook, wire ends can be picked up. */
  onReconnect?(oldEdge: RFEdge, conn: Connection, ctx: CanvasCtx): void
  onReconnectStart?(e: ReactMouseEvent, edge: RFEdge, handleType: HandleType, ctx: CanvasCtx): void
  onReconnectEnd?(e: MouseEvent | TouchEvent, edge: RFEdge, handleType: HandleType, state: FinalConnectionState, ctx: CanvasCtx): void
  onNodeContextMenu?(e: ReactMouseEvent, node: RFNode, ctx: CanvasCtx): void
  onEdgeContextMenu?(e: ReactMouseEvent, edge: RFEdge, ctx: CanvasCtx): void
  onPaneContextMenu?(e: ReactMouseEvent | MouseEvent, ctx: CanvasCtx): void
  onEdgeDoubleClick?(e: ReactMouseEvent, edge: RFEdge, ctx: CanvasCtx): void
  /** Pointer moved over the canvas; `flowPos` is in flow coordinates. Called on every move: keep it cheap. */
  onPointerMove?(flowPos: XY, ctx: CanvasCtx): void
  /** Pointer left the canvas. */
  onPointerLeave?(ctx: CanvasCtx): void
  /** Pan or zoom, on every frame of the gesture. Keep it cheap (throttle). */
  onMove?(viewport: Viewport, ctx: CanvasCtx): void
  /**
   * Delete or Backspace, before the default delete, inside the same store
   * batch. Graph nodes and wires are deleted by the canvas; a plugin deletes
   * what it owns (`otherIds`). Return true when it deleted something, so the
   * key counts as handled even when no graph node or wire was selected.
   */
  onDeleteSelection?(req: DeleteRequest, ctx: CanvasCtx): boolean | void
}

/** Every hook name, for code that walks plugins generically. */
export type CanvasPluginHook = Exclude<keyof CanvasPlugin, 'id'>

// Registry (EA-12, IP-7). Per id a stack of registrations: the newest is
// live, and removing it brings back the one it replaced (a test or an HMR
// session that overrides 'wireOps' and then unregisters gets the built-in
// back). The live list keeps the position of an id's first registration.
// Lists are rebuilt on (rare) registration changes and read without
// copying; the per-hook lists make hasPluginHook O(1) and let the canvas
// call only the plugins that have a hook on hot paths (pointer move, drag,
// pan frames).
const stacks = new Map<string, CanvasPlugin[]>()
const order: string[] = []
let live: readonly CanvasPlugin[] = []
let byHook = new Map<CanvasPluginHook, readonly CanvasPlugin[]>()

function rebuild(): void {
  live = order.map(id => stacks.get(id)!.at(-1)!)
  byHook = new Map()
}

/**
 * Add a plugin. A plugin with the same id is replaced while this one is
 * registered (and comes back when it is removed). Returns a function that
 * removes it again.
 */
export function registerCanvasPlugin(p: CanvasPlugin): () => void {
  loadPluginModules()
  const stack = stacks.get(p.id)
  if (stack) stack.push(p)
  else {
    stacks.set(p.id, [p])
    order.push(p.id)
  }
  rebuild()
  return () => {
    const st = stacks.get(p.id)
    const j = st ? st.lastIndexOf(p) : -1
    if (!st || j < 0) return
    st.splice(j, 1)
    if (st.length === 0) {
      stacks.delete(p.id)
      order.splice(order.indexOf(p.id), 1)
    }
    rebuild()
  }
}

/** Every registered plugin, in registration order. */
export function listCanvasPlugins(): readonly CanvasPlugin[] {
  loadPluginModules()
  return live
}

/** The plugins that have this hook, in registration order. */
export function pluginsWithHook(hook: CanvasPluginHook): readonly CanvasPlugin[] {
  loadPluginModules()
  let list = byHook.get(hook)
  if (!list) {
    list = live.filter(p => p[hook] != null)
    byHook.set(hook, list)
  }
  return list
}

/** True when at least one plugin has this hook (Canvas skips work otherwise). */
export function hasPluginHook(hook: CanvasPluginHook): boolean {
  return pluginsWithHook(hook).length > 0
}

/**
 * Call `fn` for every plugin (or, with `hook`, every plugin that has that
 * hook) and say whether any returned true. Every plugin is called, even
 * after one has returned true. A plugin that throws is reported on the
 * console and skipped, so one bad plugin cannot break the canvas.
 */
export function anyPluginHandled(fn: (p: CanvasPlugin) => boolean | void, hook?: CanvasPluginHook): boolean {
  let handled = false
  // The lists are replaced, never changed in place, so a plugin that
  // registers or unregisters during the call does not disturb this loop.
  for (const p of hook ? pluginsWithHook(hook) : listCanvasPlugins()) {
    try {
      if (fn(p) === true) handled = true
    } catch (err) {
      console.error(`nodebuilder: canvas plugin "${p.id}" failed`, err)
    }
  }
  return handled
}

// ── Auto-load plugins/*.ts ──────────────────────────────────────────────────
// Sorted by file name, so the order is the same on every load. Read lazily
// on the first registry call, like commands/index.ts (EA-2): a plugin
// module that imports this file first would otherwise be read while it is
// still loading.

interface PluginModule {
  plugin?: CanvasPlugin
  plugins?: readonly CanvasPlugin[]
}

const modules = import.meta.glob<PluginModule>(['./plugins/*.ts', '!./plugins/*.test.ts'], { eager: true })
let modulesLoaded = false

function loadPluginModules(): void {
  if (modulesLoaded) return
  modulesLoaded = true
  // Module plugins go first: a plugin registered earlier at runtime (a test)
  // still overrides a module plugin with the same id.
  const early = order.map(id => stacks.get(id)!)
  stacks.clear()
  order.length = 0
  const add = (p: CanvasPlugin) => {
    const st = stacks.get(p.id)
    if (st) st.push(p)
    else {
      stacks.set(p.id, [p])
      order.push(p.id)
    }
  }
  for (const path of Object.keys(modules).sort()) {
    const mod = modules[path]
    if (mod.plugin) add(mod.plugin)
    for (const p of mod.plugins ?? []) add(p)
  }
  for (const st of early) for (const p of st) add(p)
  rebuild()
}
