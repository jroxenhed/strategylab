/**
 * canvasHelpers — pure logic behind Canvas.tsx and TabMenu.tsx.
 *
 * Kept free of React and React Flow runtime imports so it can be unit-tested
 * with plain objects. The node and edge shapes below are the small subset of
 * React Flow's Node / Edge that this logic reads.
 */

import { NODE_CATALOG, type NodeCatalogEntry } from './catalog'

export interface XY { x: number; y: number }

/** The parts of a React Flow node that the helpers read or carry over. */
export interface FlowNodeLike {
  id: string
  position: XY
  data?: unknown
  type?: string
  draggable?: boolean
  selected?: boolean
  measured?: { width?: number; height?: number }
}

/** The parts of a React Flow edge that the helpers read. */
export interface FlowEdgeLike {
  id: string
  source: string
  target: string
  selected?: boolean
}

// ---------------------------------------------------------------------------
// Catalog lookups
// ---------------------------------------------------------------------------

const CATALOG_BY_NAME: Map<string, NodeCatalogEntry> = new Map(
  NODE_CATALOG.map(e => [e.name, e]),
)

/** The price a new wire out of a Ticker reads by default. */
export const TICKER_DEFAULT_ATTR = '@close'

/**
 * The attribute a new hand-drawn wire carries when it leaves a node of this
 * type. The backend compiler reads this label, so it decides which price an
 * indicator computes on. A Ticker wire gets @close (the price every backtest
 * rule uses); taking the Ticker's first write would silently feed @open.
 * Every other node gets the first attribute it writes. Null for unknown
 * types and terminals.
 */
export function primaryAttrFor(nodeType: string | undefined): string | null {
  if (!nodeType) return null
  const entry = CATALOG_BY_NAME.get(nodeType)
  if (!entry) return null
  if (entry.cat === 'ticker' && entry.writes.includes(TICKER_DEFAULT_ATTR)) {
    return TICKER_DEFAULT_ATTR
  }
  return (entry.writes[0] as string | undefined) ?? null
}

/** Catalog entries the Tab menu offers: only nodes that compile does something with. */
export function menuCatalog(catalog: readonly NodeCatalogEntry[] = NODE_CATALOG): NodeCatalogEntry[] {
  return catalog.filter(e => e.compileActive)
}

/** Group catalog entries by category, keeping catalog order inside each group. */
export function groupByCategory(entries: readonly NodeCatalogEntry[]): Record<string, NodeCatalogEntry[]> {
  const out: Record<string, NodeCatalogEntry[]> = {}
  for (const e of entries) {
    if (!out[e.cat]) out[e.cat] = []
    out[e.cat].push(e)
  }
  return out
}

// ---------------------------------------------------------------------------
// Selection, delete and drag
// ---------------------------------------------------------------------------

/**
 * What the Delete key should remove.
 *
 * Uses React Flow's own selection (every node or wire with `selected`), so a
 * box selection deletes everything in the box. Falls back to the store's single
 * selection when React Flow has nothing selected.
 *
 * A selected wire that touches a node being deleted is left to the node's
 * rewire step, so deleting a chain of nodes still reconnects what was above
 * the chain to what was below it.
 */
export function planDeletion(
  nodes: readonly FlowNodeLike[],
  edges: readonly FlowEdgeLike[],
  fallbackNodeId: string | null,
  fallbackWireId: string | null,
): { nodeIds: string[]; wireIds: string[] } {
  const nodeIds = nodes.filter(n => n.selected).map(n => n.id)
  let wireIds = edges.filter(e => e.selected).map(e => e.id)
  if (nodeIds.length === 0 && wireIds.length === 0) {
    if (fallbackNodeId && nodes.some(n => n.id === fallbackNodeId)) nodeIds.push(fallbackNodeId)
    else if (fallbackWireId && edges.some(e => e.id === fallbackWireId)) wireIds.push(fallbackWireId)
  }
  if (nodeIds.length > 0) {
    const doomed = new Set(nodeIds)
    const byId = new Map(edges.map(e => [e.id, e]))
    wireIds = wireIds.filter(id => {
      const e = byId.get(id)
      return !!e && !doomed.has(e.source) && !doomed.has(e.target)
    })
  }
  return { nodeIds, wireIds }
}

/**
 * Positions to save after a drag ends. React Flow passes the grabbed node plus
 * the list of every node that moved with it; all of them must be saved or the
 * others snap back on the next sync from the store.
 */
export function dragStopMoves(
  primary: FlowNodeLike | null | undefined,
  dragged: readonly FlowNodeLike[] | null | undefined,
  /** Graph (absolute) position of a React Flow node (EA-1); default: its own position. */
  toGraph: (n: FlowNodeLike) => [number, number] = n => [n.position.x, n.position.y],
): Array<{ id: string; position: [number, number] }> {
  const out = new Map<string, [number, number]>()
  for (const n of dragged ?? []) out.set(n.id, toGraph(n))
  if (primary && !out.has(primary.id)) out.set(primary.id, toGraph(primary))
  return Array.from(out, ([id, position]) => ({ id, position }))
}

/**
 * The single node the store should treat as "the selected node" when React Flow
 * reports a selection: keep the current one if it is still selected, otherwise
 * take the first selected node, otherwise none.
 */
export function primarySelection(selectedIds: readonly string[], current: string | null): string | null {
  if (current && selectedIds.includes(current)) return current
  return selectedIds[0] ?? null
}

/**
 * Bring React Flow's local selection in line with the store's selected node.
 * Returns `curr` unchanged when they already agree, so a multi-selection that
 * contains the store's node is kept.
 */
export function alignSelection<T extends FlowNodeLike>(curr: T[], selectedId: string | null): T[] {
  if (selectedId === null) {
    if (!curr.some(n => n.selected)) return curr
    return curr.map(n => (n.selected ? { ...n, selected: false } : n))
  }
  const target = curr.find(n => n.id === selectedId)
  if (!target || target.selected) return curr
  return curr.map(n => {
    if (n.id === selectedId) return { ...n, selected: true }
    return n.selected ? { ...n, selected: false } : n
  })
}

/**
 * Select only `id` in React Flow's local selection. Unlike alignSelection it
 * also collapses a multi-selection that already contains `id`: a plain click
 * on one node of a group selects just that node, as in Houdini, so a Delete
 * that follows removes only it. Returns `curr` when nothing changes.
 */
export function selectOnly<T extends FlowNodeLike>(curr: T[], id: string): T[] {
  if (curr.every(n => (n.id === id) === !!n.selected)) return curr
  return curr.map(n => {
    const want = n.id === id
    return !!n.selected === want ? n : { ...n, selected: want }
  })
}

/** A short name for a node in UI hints: its type, plus the symbol for a Ticker. */
export function nodeLabel(node: { type: string; params?: Record<string, unknown> } | undefined): string {
  if (!node) return ''
  const symbol = node.type === 'ticker' ? node.params?.symbol : undefined
  return typeof symbol === 'string' && symbol ? `${node.type} ${symbol}` : node.type
}

/**
 * Replace the local node mirror with fresh nodes built from the store, while
 * keeping what only React Flow knows: each node's measured size and whether it
 * is selected. Dropping `measured` makes React Flow hide and re-measure every
 * node, which is slow and can flicker.
 *
 * Nodes whose content did not change keep their old object, and when nothing
 * changed at all the old array comes back, so no extra render happens.
 */
export function mergeLocalNodes<T extends FlowNodeLike>(curr: T[], next: T[]): T[] {
  const currById = new Map(curr.map(n => [n.id, n]))
  let changed = curr.length !== next.length
  const out = next.map((r, i) => {
    const c = currById.get(r.id)
    if (
      c
      && c.data === r.data
      && c.type === r.type
      && c.draggable === r.draggable
      && c.position.x === r.position.x
      && c.position.y === r.position.y
    ) {
      if (curr[i] !== c) changed = true
      return c
    }
    changed = true
    const merged: T = { ...r, selected: c?.selected ?? false }
    if (c?.measured) (merged as FlowNodeLike).measured = c.measured
    return merged
  })
  return changed ? out : curr
}

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------

/** True when the key press belongs to a text field, select or editable element. */
/**
 * True when a pointer release landed on empty canvas for wire drops (UX-02):
 * the pane itself, or the empty inside of a network box or sticky note
 * (they are React Flow nodes over the pane, and boxes are drawn around node
 * groups, so most drops land on one). A port, a graph node or a text field
 * inside them is not empty.
 */
export function isEmptyCanvasTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target.classList.contains('react-flow__pane')) return true
  if (isTypingTarget(target) || target.closest('.react-flow__handle')) return false
  return target.closest('.react-flow__node-nbBox, .react-flow__node-nbNote') != null
}

export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || typeof el.tagName !== 'string') return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return el.isContentEditable === true
}

/**
 * Should the canvas act on this key press? Only when the node builder is on
 * screen, the press is not inside a text field, no modifier key is held, and
 * focus is inside the node builder or nowhere in particular (the page body).
 *
 * A press on the page body counts only while `bodyActive` is true, i.e. the
 * last click landed in the node builder. Clicking blank space elsewhere (the
 * sidebar) also leaves focus on the body, and Backspace there must not
 * delete graph nodes.
 */
export function shouldHandleCanvasKey(opts: {
  target: EventTarget | null
  root: Element | null
  inView: boolean
  modifier: boolean
  defaultPrevented: boolean
  bodyActive?: boolean
}): boolean {
  const { target, root, inView, modifier, defaultPrevented, bodyActive = true } = opts
  if (!inView || modifier || defaultPrevented) return false
  if (isTypingTarget(target)) return false
  const el = target as Node | null
  if (!el) return bodyActive
  if (typeof document !== 'undefined' && (el === document.body || el === document.documentElement || el === document)) {
    return bodyActive
  }
  return !!root && root.contains(el)
}

/**
 * Does a key press belong to the node builder, for `global` commands
 * (foundation 6.2)? Yes when the builder is on screen and the press is inside
 * it, inside one of its portals (dialogs, popovers, menus carry the
 * `nodebuilder-root` class), or on the page body right after a press inside
 * it. Text fields count too; which commands run from a field is up to the
 * command (`inFields`).
 */
export function belongsToBuilder(opts: {
  target: EventTarget | null
  root: Element | null
  inView: boolean
  bodyActive: boolean
}): boolean {
  const { target, root, inView, bodyActive } = opts
  if (!inView || !root) return false
  const el = target as Node | null
  if (!el) return bodyActive
  if (typeof document !== 'undefined' && (el === document.body || el === document.documentElement || el === document)) {
    return bodyActive
  }
  if (root.contains(el)) return true
  return el instanceof Element && el.closest('.nodebuilder-root') != null
}

/** The outermost `.nodebuilder-root` around an element (the whole node builder view). */
export function outermostRoot(el: Element | null): Element | null {
  let found: Element | null = null
  let cur: Element | null = el
  while (cur) {
    if (cur.classList?.contains('nodebuilder-root')) found = cur
    cur = cur.parentElement
  }
  return found
}

// ---------------------------------------------------------------------------
// Tab menu placement
// ---------------------------------------------------------------------------

/** Space left between a selected node and a new node placed below it. */
export const BELOW_GAP = 40
/** Height assumed for a node React Flow has not measured yet. */
export const DEFAULT_NODE_HEIGHT = 100

/**
 * Where the Tab menu opens, in screen pixels: at the last pointer position
 * over the canvas, or near the top middle of the canvas if the pointer was
 * never over it.
 */
export function menuScreenPoint(
  lastPointer: XY | null,
  containerRect: { left: number; top: number; width: number; height: number } | null,
): XY {
  if (lastPointer) return lastPointer
  if (containerRect) {
    return { x: containerRect.left + containerRect.width / 2, y: containerRect.top + 80 }
  }
  return { x: 300, y: 200 }
}

/**
 * Where a node created from the Tab menu lands, in graph coordinates.
 * Opened by keyboard with a node selected: just below that node, when that
 * spot is on screen (`visible`, in graph coordinates). Otherwise: at the
 * point the menu was opened (cursor or wire drop), so the new node never
 * lands where the user cannot see it.
 */
export function newNodePosition(opts: {
  openedBy: 'keyboard' | 'wire'
  pointFlow: XY
  selected: FlowNodeLike | null
  visible?: { minX: number; minY: number; maxX: number; maxY: number }
}): XY {
  const { openedBy, pointFlow, selected, visible } = opts
  if (openedBy === 'keyboard' && selected) {
    const h = selected.measured?.height ?? DEFAULT_NODE_HEIGHT
    const below = { x: selected.position.x, y: selected.position.y + h + BELOW_GAP }
    const onScreen = !visible || (
      below.x >= visible.minX && below.x <= visible.maxX
      && below.y >= visible.minY && below.y <= visible.maxY
    )
    if (onScreen) return below
  }
  return pointFlow
}

/**
 * Step a position down until no existing node sits on (almost) the same spot,
 * so creating several nodes in a row does not stack them exactly on top of
 * each other.
 */
export function nudgeFree(pos: XY, occupied: readonly XY[], step = 24, tolerance = 8): XY {
  let p = pos
  for (let i = 0; i < 50; i++) {
    const hit = occupied.some(o => Math.abs(o.x - p.x) < tolerance && Math.abs(o.y - p.y) < tolerance)
    if (!hit) return p
    p = { x: p.x, y: p.y + step }
  }
  return p
}

/** Keep a menu of the given size inside the window, with a small margin. */
export function clampMenuPosition(
  pos: XY,
  size: { width: number; height: number },
  viewport: { width: number; height: number },
  margin = 8,
): XY {
  const x = Math.min(pos.x, viewport.width - size.width - margin)
  const y = Math.min(pos.y, viewport.height - size.height - margin)
  return { x: Math.max(margin, x), y: Math.max(margin, y) }
}

// ---------------------------------------------------------------------------
// Wire drag
// ---------------------------------------------------------------------------

/**
 * Stop the page from selecting text while a wire is dragged. Returns a function
 * that puts the previous setting back.
 */
export function suppressTextSelection(doc: Document = document): () => void {
  const style = doc.body.style
  const prev = style.userSelect
  const prevWebkit = style.getPropertyValue('-webkit-user-select')
  style.userSelect = 'none'
  style.setProperty('-webkit-user-select', 'none')
  return () => {
    style.userSelect = prev
    if (prevWebkit) style.setProperty('-webkit-user-select', prevWebkit)
    else style.removeProperty('-webkit-user-select')
  }
}

/**
 * Mark the wires of the hovered node "hot" (spec S11), touching only the
 * edges whose flag changes: every other edge object is returned as is, and
 * the same array comes back when nothing changed. Hover never re-runs the
 * full edge build in Canvas.tsx; this patch runs on the local edge mirror.
 */
export function markHotEdges<T extends FlowEdgeLike & { data?: Record<string, unknown> }>(
  edges: T[],
  hoveredId: string | null,
): T[] {
  let changed = false
  const out = edges.map(e => {
    const hot = hoveredId !== null && (e.source === hoveredId || e.target === hoveredId)
    if (!e.data || !!e.data.hot === hot) return e
    changed = true
    return { ...e, data: { ...e.data, hot } }
  })
  return changed ? out : edges
}
