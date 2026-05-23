/**
 * Nodebuilder API client — Unit 3 + Unit 8b.
 *
 * fetchAutoRender:      POST /api/nodebuilder/auto_render
 * fetchGraphBacktest:   POST /api/nodebuilder/backtest
 */

import { api } from './client'
import type { StrategyRequest } from '../shared/types/strategy'

/** Wire between two nodes in the graph. */
export interface GraphWire {
  id: string
  /** Source node path. */
  from: string
  /** Destination node path. */
  to: string
  /** Attribute label on the wire (e.g. "@rsi", "@bool"). */
  attr?: string | null
}

/** A single node in the graph. */
export interface GraphNode {
  id: string
  type: string
  params: Record<string, unknown>
  position: [number, number]
  display: boolean
  bypass: boolean
  subgraph?: string | null
}

/** Top-level graph returned by the auto_render endpoint. */
export interface Graph {
  _version: number
  readOnly: boolean
  nodes: Record<string, GraphNode>
  wires: GraphWire[]
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

/** Summary statistics returned by the graph backtest. */
export type BacktestSummary = Record<string, unknown>

/** Trade record returned by the graph backtest. */
export type TradeRecord = Record<string, unknown>

/** Response from POST /api/nodebuilder/backtest. */
export interface GraphBacktestResult {
  summary: BacktestSummary
  trades: TradeRecord[]
  equity_curve: CurvePoint[]
  baseline_curve: CurvePoint[]
}

/**
 * Run a backtest using a compiled node graph.
 *
 * @param req - GraphBacktestRequest with the graph + simulator settings.
 * @returns   - GraphBacktestResult with summary, trades, equity_curve, baseline_curve.
 */
export async function fetchGraphBacktest(req: GraphBacktestRequest): Promise<GraphBacktestResult> {
  const { data } = await api.post<GraphBacktestResult>('/api/nodebuilder/backtest', req)
  return data
}
