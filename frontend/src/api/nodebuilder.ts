/**
 * Nodebuilder API client — Unit 3.
 *
 * fetchAutoRender: POST /api/nodebuilder/auto_render
 * Returns the Graph representation of an existing rule strategy (read-only).
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
