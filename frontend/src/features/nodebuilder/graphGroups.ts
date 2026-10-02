/**
 * Read the Output Groups of a graph for the Spawn dialog (S34) and the
 * AddBotBar group selector (S36). Pure functions, no store.
 *
 * - Every `output_group` node is a group, in file order (the order of
 *   `graph.nodes`). Its name is the node name; `direction`, `ticker` (a path
 *   to the group's primary Ticker node) and `capital_weight` are its params
 *   (plan W5 "Terminal params").
 * - A graph with no `output_group` but an `entry` terminal at the root has
 *   one implicit group named `main` (D7). Its direction comes from the
 *   request, so it is null here; its ticker is the first root Ticker node
 *   that is not a reference ticker (no `prefix`).
 *
 * The symbol and interval shown here are a courtesy. The server reads the
 * saved revision and decides what each bot trades.
 */

import type { Graph, GraphNode } from '../../api/nodebuilder'
import { findByPath } from './paths'
import { weightOf } from './groupResults'

export type GroupDirection = 'long' | 'short' | 'regime_switch'

export interface GraphGroupInfo {
  name: string
  /** The output_group node id, or null for the implicit `main` group. */
  nodeId: string | null
  /** null when the graph does not say (the implicit group). */
  direction: GroupDirection | null
  /** The primary Ticker's symbol, or '' when it cannot be found. */
  symbol: string
  /** The primary Ticker's interval, or '' when it cannot be found. */
  interval: string
  /** capital_weight, 0 or above (default 1). 0: the group gets no capital and does not trade (S32a). */
  weight: number
  implicit: boolean
}

/** Name of the implicit group (D7). */
export const IMPLICIT_GROUP = 'main'

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function directionOf(v: unknown): GroupDirection | null {
  return v === 'long' || v === 'short' || v === 'regime_switch' ? v : null
}

/**
 * The Ticker node a group's `ticker` path points at, tried relative to the
 * group, its parent, then the root, as the server does
 * (nodes_groups._find_ticker). The one resolver: the frame header
 * (rfMapping.groupTickerOf), the Spawn dialog and AddBotBar all use it (FE-10).
 */
export function resolveGroupTicker(graph: Pick<Graph, 'nodes'>, group: GraphNode): GraphNode | null {
  const path = str(group.params?.ticker)
  if (!path) return null
  for (const base of [group.id, group.parent ?? null, null]) {
    let id: string | null = null
    try {
      id = findByPath(graph, path, base)
    } catch {
      id = null
    }
    const node = id ? graph.nodes[id] : undefined
    if (node && node.type === 'ticker') return node
  }
  return null
}

/** True when Ticker `tickerId` is some Output Group's primary (its `ticker` path names it). */
export function isGroupPrimary(graph: Pick<Graph, 'nodes'> | null | undefined, tickerId: string): boolean {
  if (!graph?.nodes) return false
  for (const n of Object.values(graph.nodes)) {
    if (n.type === 'output_group' && resolveGroupTicker(graph, n)?.id === tickerId) return true
  }
  return false
}

function isReferenceTicker(node: GraphNode): boolean {
  return str(node.params?.prefix).trim() !== ''
}

/** The Output Groups of a graph, in file order, or the implicit `main` group, or none. */
export function listGraphGroups(graph: Graph | null | undefined): GraphGroupInfo[] {
  if (!graph || !graph.nodes) return []
  const nodes = Object.values(graph.nodes)
  const groups = nodes.filter(n => n.type === 'output_group')
  if (groups.length > 0) {
    return groups.map(g => {
      const t = resolveGroupTicker(graph, g)
      return {
        name: g.name,
        nodeId: g.id,
        // No direction param reads LONG, as the header and the server do (UX-06).
        direction: directionOf(g.params?.direction) ?? 'long',
        symbol: t ? str(t.params?.symbol).toUpperCase() : '',
        interval: t ? str(t.params?.interval) : '',
        // One weight rule for the header, results and spawn (S32a, FE-04):
        // 0 is valid and means no capital.
        weight: weightOf(g.params),
        implicit: false,
      }
    })
  }
  const hasRootEntry = nodes.some(n => n.type === 'entry' && n.parent == null)
  if (!hasRootEntry) return []
  const ticker = nodes.find(n => n.type === 'ticker' && n.parent == null && !isReferenceTicker(n))
    ?? nodes.find(n => n.type === 'ticker' && n.parent == null)
    ?? null
  return [{
    name: IMPLICIT_GROUP,
    nodeId: null,
    direction: null,
    symbol: ticker ? str(ticker.params?.symbol).toUpperCase() : '',
    interval: ticker ? str(ticker.params?.interval) : '',
    weight: 1,
    implicit: true,
  }]
}

/**
 * Split a capital over the groups by weight: `capital * weight / sum(weights)`,
 * rounded to whole units (S34 default). A weight-0 group gets 0.
 */
export function splitCapital(capital: number, groups: readonly Pick<GraphGroupInfo, 'weight'>[]): number[] {
  const total = groups.reduce((s, g) => s + g.weight, 0)
  if (!(total > 0) || !(capital > 0)) return groups.map(() => 0)
  return groups.map(g => Math.round((capital * g.weight) / total))
}

/** `10 000`: whole units with a space between thousands (S34 copy). */
export function formatCapital(n: number): string {
  if (!Number.isFinite(n)) return ''
  const rounded = Math.round(n * 100) / 100
  const [whole, frac] = String(Math.abs(rounded)).split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return `${rounded < 0 ? '-' : ''}${grouped}${frac ? `.${frac}` : ''}`
}

/** Read a capital field: spaces and underscores between digits are allowed, a comma counts as the decimal mark. NaN when it is not a number. */
export function parseCapital(text: string): number {
  const cleaned = text.replace(/[\s_]/g, '').replace(',', '.')
  if (!/^\d+(\.\d+)?$/.test(cleaned)) return NaN
  return Number(cleaned)
}

/** `LONG`, `SHORT`, `SWITCH` for the direction pills. */
export function directionLabel(d: GroupDirection | 'long' | 'short' | null | undefined): string {
  if (d === 'regime_switch') return 'SWITCH'
  if (d === 'short') return 'SHORT'
  if (d === 'long') return 'LONG'
  return ''
}
