/**
 * layout.ts — tidy layout for the node builder (W3 item 3.F).
 *
 * Lays nodes out top-down in layers with elkjs (the "layered" algorithm):
 * data flows from the top (tickers, indicators) to the bottom (Entry, Exit).
 * Inputs sit on a node's top edge and the one output on its bottom edge, so
 * the ports given to elk are the same ones the canvas draws (BaseNode).
 *
 * elkjs is large, so it is loaded on first use with a dynamic import and is
 * not part of the main bundle.
 *
 * The result is deterministic: nodes and wires are handed to elk in a fixed
 * order (by their current position, then id), so the same graph always gives
 * the same positions, whatever the order of keys in `graph.nodes`.
 *
 * Each network (nodes sharing a `parent`) is laid out on its own. The laid
 * out block keeps the top-left corner the nodes had before, so a tidy does
 * not throw the nodes somewhere else on the canvas.
 *
 * Pure helpers only: the L command (commands/layout.ts) and "Edit this
 * graph" (store.ts) apply the result through the store.
 */

import type { ELK, ElkExtendedEdge, ElkNode, ElkPort } from 'elkjs/lib/elk-api'
import type { Graph, GraphNode } from '../../api/nodebuilder'
import { portFraction, portsOf } from './streamLabels'

/** Width and height of a node on the canvas, in flow units. */
export interface LayoutSize {
  width: number
  height: number
}

/** Size of a node, when known (measured by React Flow); undefined = estimate. */
export type SizeOf = (node: GraphNode) => LayoutSize | null | undefined

/** New top-left position per laid-out node id. */
export type Positions = Map<string, [number, number]>

// Default BaseNode width, and the height of a node's header and of one param
// row in edit mode. Used only when React Flow has not measured the node.
const EST_WIDTH = 176
const EST_HEADER = 60
const EST_ROW = 24
const EST_MAX_ROWS = 12

/** A best guess at a node's size before React Flow has measured it. */
export function estimateNodeSize(node: GraphNode): LayoutSize {
  const rows = Math.min(Object.keys(node.params ?? {}).length, EST_MAX_ROWS)
  return { width: EST_WIDTH, height: EST_HEADER + rows * EST_ROW }
}

/** elk options. Spacing leaves room for the wire labels between layers. */
const LAYOUT_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'DOWN',
  'elk.edgeRouting': 'POLYLINE',
  'elk.spacing.nodeNode': '40',
  'elk.layered.spacing.nodeNodeBetweenLayers': '72',
  'elk.layered.spacing.edgeNodeBetweenLayers': '24',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
  'elk.layered.nodePlacement.bk.fixedAlignment': 'BALANCED',
  // Keep the order nodes are handed over in when nothing else decides it:
  // the result stays close to the old left-to-right order, and is stable.
  'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
  'elk.randomSeed': '1',
  'elk.padding': '[top=0,left=0,bottom=0,right=0]',
}

// ---------------------------------------------------------------------------
// Loading elk lazily
// ---------------------------------------------------------------------------

let elkPromise: Promise<ELK> | null = null
let workerFailed = false

/**
 * elk in a real Web Worker (IP-4): the chunk parses and the layered layout
 * runs off the main thread, so "Edit this graph" does not freeze the editor
 * while it first paints. Only where `Worker` exists (not in tests).
 */
async function loadWorkerElk(): Promise<ELK> {
  const [api, worker] = await Promise.all([
    import('elkjs/lib/elk-api.js'),
    import('elkjs/lib/elk-worker.min.js?url'),
  ])
  const workerUrl = worker.default as string
  return new api.default({ workerUrl, workerFactory: (url?: string) => new Worker(url ?? workerUrl) })
}

/** elk on the main thread (its built-in fake worker): tests, and the fallback when the worker fails. */
async function loadBundledElk(): Promise<ELK> {
  const m = await import('elkjs/lib/elk.bundled.js')
  return new m.default()
}

/** The elk engine, loaded on first use. A failed load is retried next time. */
function loadElk(): Promise<ELK> {
  if (!elkPromise) {
    const useWorker = !workerFailed && typeof Worker !== 'undefined'
    elkPromise = (useWorker ? loadWorkerElk().catch(() => { workerFailed = true; return loadBundledElk() }) : loadBundledElk())
      .catch(err => {
        elkPromise = null
        throw err
      })
  }
  return elkPromise
}

/** Run one elk layout; a worker that fails (blocked by the page, crashed) gives way to the main-thread engine once. */
async function runLayout(elkGraph: ElkNode): Promise<ElkNode> {
  const elk = await loadElk()
  try {
    return await elk.layout(elkGraph)
  } catch (err) {
    if (workerFailed || typeof Worker === 'undefined') throw err
    workerFailed = true
    elkPromise = null
    return (await loadElk()).layout(elkGraph)
  }
}

/**
 * Start loading elk now (IP-4), so the tidy of "Edit this graph" usually
 * lands before the first fit. Called when the pointer or focus reaches the
 * Edit button. Never throws.
 */
export function prefetchLayout(): void {
  loadElk().catch(() => {})
}

// ---------------------------------------------------------------------------
// Building the elk graph
// ---------------------------------------------------------------------------

/** Fixed order for nodes: top to bottom, then left to right, then id. */
function byPosition(a: GraphNode, b: GraphNode): number {
  return (
    a.position[1] - b.position[1] ||
    a.position[0] - b.position[0] ||
    (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
  )
}

function portId(nodeId: string, port: string): string {
  return `${nodeId}::${port}`
}

/**
 * One elk graph for one group of nodes. Ports are fixed where the canvas
 * draws them, so wires into `a` and `b` of a comparison stay left and right.
 */
function buildElkGraph(
  group: GraphNode[],
  graph: Graph,
  sizeOf: SizeOf | undefined,
): { elkGraph: ElkNode; sizes: Map<string, LayoutSize> } {
  const inGroup = new Set(group.map(n => n.id))

  // Ports each node draws: every wire into it counts, even from outside the group.
  const connected = new Map<string, string[]>()
  for (const w of graph.wires) {
    const list = connected.get(w.to)
    if (list) list.push(w.to_port)
    else connected.set(w.to, [w.to_port])
  }

  const sizes = new Map<string, LayoutSize>()
  const children: ElkNode[] = group.map(n => {
    const measured = sizeOf?.(n)
    const size = measured && measured.width > 0 && measured.height > 0 ? measured : estimateNodeSize(n)
    sizes.set(n.id, size)
    const inputs = portsOf(n.type, connected.get(n.id) ?? [])
    const ports: ElkPort[] = inputs.map(p => ({
      id: portId(n.id, p.id),
      x: Math.round(size.width * portFraction(p.index, inputs.length)),
      y: 0,
      width: 1,
      height: 1,
      layoutOptions: { 'elk.port.side': 'NORTH' },
    }))
    ports.push({
      id: portId(n.id, 'out'),
      x: Math.round(size.width / 2),
      y: size.height,
      width: 1,
      height: 1,
      layoutOptions: { 'elk.port.side': 'SOUTH' },
    })
    return {
      id: n.id,
      width: size.width,
      height: size.height,
      ports,
      layoutOptions: { 'elk.portConstraints': 'FIXED_POS' },
    }
  })

  // Wires inside the group only, in a fixed order. A wire into a port the
  // node does not draw (an old graph) is still handed over by its port id,
  // which portsOf always draws for a connected port.
  const edges: ElkExtendedEdge[] = graph.wires
    .filter(w => w.from !== w.to && inGroup.has(w.from) && inGroup.has(w.to))
    .map(w => ({
      id: w.id,
      sources: [portId(w.from, 'out')],
      targets: [portId(w.to, w.to_port)],
    }))
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))

  return {
    elkGraph: { id: 'root', layoutOptions: LAYOUT_OPTIONS, children, edges },
    sizes,
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Tidy positions for the given nodes (or every node when `ids` is empty or
 * missing). Returns the new top-left position of each node laid out; nodes
 * not in the list are not in the map and do not move.
 *
 * Each network (nodes with the same `parent`) is laid out apart, and its
 * block keeps the top-left corner its nodes had.
 */
export async function tidyPositions(
  graph: Graph,
  ids?: readonly string[] | null,
  sizeOf?: SizeOf,
): Promise<Positions> {
  const picked = ids && ids.length > 0
    ? ids.filter(id => id in graph.nodes).map(id => graph.nodes[id])
    : Object.values(graph.nodes)
  // Group by network, in a fixed order.
  const groups = new Map<string, GraphNode[]>()
  for (const n of [...new Set(picked)].sort(byPosition)) {
    const key = n.parent ?? ''
    const list = groups.get(key)
    if (list) list.push(n)
    else groups.set(key, [n])
  }
  const positions: Positions = new Map()
  if (groups.size === 0) return positions
  const keys = [...groups.keys()].sort()
  for (const key of keys) {
    const group = groups.get(key)!
    const { elkGraph } = buildElkGraph(group, graph, sizeOf)
    const out = await runLayout(elkGraph)
    const placed = out.children ?? []
    if (placed.length === 0) continue
    // Keep the group's old top-left corner.
    const anchorX = Math.min(...group.map(n => n.position[0]))
    const anchorY = Math.min(...group.map(n => n.position[1]))
    const minX = Math.min(...placed.map(c => c.x ?? 0))
    const minY = Math.min(...placed.map(c => c.y ?? 0))
    for (const c of placed) {
      positions.set(c.id, [
        Math.round(anchorX + (c.x ?? 0) - minX),
        Math.round(anchorY + (c.y ?? 0) - minY),
      ])
    }
  }
  return positions
}

/**
 * The graph with the given positions applied. Returns the same graph object
 * when nothing moves (so a commit records nothing), and keeps unmoved nodes
 * as the same objects.
 */
export function applyPositions(graph: Graph, positions: Positions): Graph {
  let nodes: Record<string, GraphNode> | null = null
  for (const [id, pos] of positions) {
    const n = graph.nodes[id]
    if (!n || (n.position[0] === pos[0] && n.position[1] === pos[1])) continue
    nodes ??= { ...graph.nodes }
    nodes[id] = { ...n, position: [pos[0], pos[1]] }
  }
  return nodes ? { ...graph, nodes } : graph
}

/** `tidyPositions` then `applyPositions`: the tidied graph. */
export async function tidyGraph(
  graph: Graph,
  ids?: readonly string[] | null,
  sizeOf?: SizeOf,
): Promise<Graph> {
  return applyPositions(graph, await tidyPositions(graph, ids, sizeOf))
}
