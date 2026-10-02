/**
 * Nodebuilder API client — Unit 3 + Unit 8b.
 *
 * fetchAutoRender:      POST /api/nodebuilder/auto_render
 * fetchGraphBacktest:   POST /api/nodebuilder/backtest
 *
 * Graph errors come back as HTTP 400 with GraphErrorBody ({detail, node_id}).
 */

import { api } from './client'
import type { StrategyRequest, GroupResult, CombinedResult } from '../shared/types/strategy'

export type { GroupResult, CombinedResult, GraphGroupDirection } from '../shared/types/strategy'

// ---------------------------------------------------------------------------
// Graph JSON v2 (plan section 4.1). The server migrates older graphs to this
// shape on every load path, so the frontend only ever sees v2 (v3 from W2).
// ---------------------------------------------------------------------------

/**
 * A node parameter value. A list of names is used by `attr_list` params
 * (logic `terms`) and by multi-pick params such as the weekday picker
 * (`['mon', 'tue']`). W7 adds `{ expr: string }` for code expressions.
 */
export type ParamValue = number | string | boolean | null | string[]

/** A single node in the graph. */
export interface GraphNode {
  /** Stable, opaque id. Wires point at ids, so a rename never breaks a wire. */
  id: string
  type: string
  /** Leaf name, unique among siblings, matching ^[a-z_][a-z0-9_]{0,63}$. */
  name: string
  /** Id of the containing network node, or null at the root. */
  parent: string | null
  params: Record<string, ParamValue>
  position: [number, number]
  display: boolean
  bypass: boolean
}

/** Wire between two nodes in the graph. */
export interface GraphWire {
  id: string
  /** Source node id. */
  from: string
  /** Destination node id. */
  to: string
  /** Every node has one output port, always 'out'. */
  from_port: 'out'
  /** Input port on the destination: 'in0', 'in1', ... */
  to_port: string
  /** Attribute label on the wire (e.g. "@rsi"). v1/v2 only; W2's v3 migration removes it. */
  attr?: string | null
}

/** A labelled, colored box drawn around a group of nodes (W3 draws them). */
export interface NetworkBox {
  id: string
  label: string
  color: string
  rect: [number, number, number, number]
  members: string[]
  parent: string | null
}

/** A free-text note on the canvas (W3 draws them). */
export interface StickyNote {
  id: string
  text: string
  rect: [number, number, number, number]
  color: string
  parent: string | null
}

/** Top-level graph. */
export interface Graph {
  _version: 2 | 3
  /** Version of the stream schema (plan section 3). */
  stream_schema: number
  readOnly: boolean
  /** Free map, at most 32 keys, string/number/bool values. */
  meta: Record<string, string | number | boolean>
  nodes: Record<string, GraphNode>
  wires: GraphWire[]
  annotations: { boxes: NetworkBox[]; notes: StickyNote[] }
}

/** Graph version and stream schema the frontend writes for new graphs. */
export const GRAPH_VERSION = 3
export const STREAM_SCHEMA_VERSION = 1

// ---------------------------------------------------------------------------
// The stream schema (plan section 3.3). /validate returns one per node,
// describing that node's OUTPUT stream. A node's input stream is the union
// of the output streams of the nodes wired into it.
// ---------------------------------------------------------------------------

/** Per-bar attribute types. */
export type PointDtype = 'float' | 'bool'
/** One-value-per-cook attribute types. W7 adds 'any' for an unannotated code write. */
export type DetailDtype = 'float' | 'int' | 'bool' | 'str'

/** One attribute on a stream: its name (with the @ sigil), type and writer. */
export interface AttrInfo {
  name: string
  dtype: PointDtype | DetailDtype | 'any'
  /** Id of the node that wrote it, or null when the server does not know. */
  written_by: string | null
}

/** One node's output stream, as /validate describes it. */
export interface StreamSchema {
  stream_schema: number
  /** Per-bar columns, in stream order. */
  points: AttrInfo[]
  /** Scalars, one value per cook (settings such as @stop_pct). */
  detail: AttrInfo[]
  /** Reserved: always [] until W8. */
  prims: { kind: string; attrs: AttrInfo[] }[]
}

/** A new, empty, editable graph at the current version. */
export function emptyGraph(): Graph {
  return {
    _version: GRAPH_VERSION,
    stream_schema: STREAM_SCHEMA_VERSION,
    readOnly: false,
    meta: {},
    nodes: {},
    wires: [],
    annotations: { boxes: [], notes: [] },
  }
}

export interface AutoRenderResponse {
  graph: Graph
}

/**
 * Translate a StrategyRequest into a read-only Graph for the T1 viewer.
 *
 * @param req - The StrategyRequest to render as a node graph.
 * @returns   - The AutoRenderResponse containing the read-only Graph.
 */
export async function fetchAutoRender(req: StrategyRequest): Promise<Graph> {
  const { data } = await api.post<AutoRenderResponse>('/api/nodebuilder/auto_render', req)
  return data.graph
}

// ---------------------------------------------------------------------------
// Unit 8b: Graph backtest
// ---------------------------------------------------------------------------

/** Simulator-level settings that accompany a graph backtest request. */
export interface GraphBacktestRequest {
  graph: Graph
  ticker: string
  start: string
  end: string
  interval?: string
  source?: string
  initial_capital?: number
  position_size?: number
  stop_loss_pct?: number | null
  trailing_stop?: unknown | null
  max_bars_held?: number | null
  slippage_bps?: number
  commission_pct?: number
  per_share_rate?: number
  min_per_order?: number
  borrow_rate_annual?: number
  dynamic_sizing?: unknown | null
  skip_after_stop?: unknown | null
  trading_hours?: unknown | null
  direction?: string
}

/** One entry in the equity or baseline curve. */
export interface CurvePoint {
  time: string | number
  value: number
}

/** A position still open when the backtest window ended. */
export interface OpenPosition {
  direction: 'long' | 'short'
  entry_price: number
  /** Unrealized gain or loss in percent (4.2 means +4.2%). */
  unrealized_pct: number
}

/**
 * Summary statistics returned by the graph backtest. Known keys are typed;
 * the rest stay loose because the rule backtester owns the full shape.
 */
export type BacktestSummary = Record<string, unknown> & {
  num_trades?: number
  total_return_pct?: number
  sharpe_ratio?: number
  /** Set when a position is still open at the end; the return includes it. */
  open_position?: OpenPosition | null
  /** False when nothing is wired into the Exit terminal. */
  exit_connected?: boolean
}

/**
 * Body of a graph error (HTTP 400). node_id names the node at fault, when
 * the server knows it.
 */
export interface GraphErrorBody {
  detail: string
  node_id?: string | null
}

/** Trade record returned by the graph backtest. */
export type TradeRecord = Record<string, unknown>

/** Response from POST /api/nodebuilder/backtest. */
export interface GraphBacktestResult {
  summary: BacktestSummary
  trades: TradeRecord[]
  equity_curve: CurvePoint[]
  baseline_curve: CurvePoint[]
  /** W4: the server's cook cache id for this run (null when not kept). */
  cook_id?: string | null
  /**
   * W5: one result per Output Group, in file order (a graph with no group
   * gives one implicit group named `main`). Older servers leave it out.
   */
  groups?: GroupResult[]
  /** W5: all groups together (summed equity, exposure, gross deployed). */
  combined?: CombinedResult
}

/**
 * Run a backtest using a compiled node graph.
 *
 * @param req    - GraphBacktestRequest with the graph + simulator settings.
 * @param signal - Optional AbortSignal; Stop in the toolbar aborts the request (S01).
 * @returns      - GraphBacktestResult with summary, trades, equity_curve, baseline_curve.
 */
export async function fetchGraphBacktest(
  req: GraphBacktestRequest,
  signal?: AbortSignal,
): Promise<GraphBacktestResult> {
  const { data } = await api.post<GraphBacktestResult>('/api/nodebuilder/backtest', req, { signal })
  return data
}
