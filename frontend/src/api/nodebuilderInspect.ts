/**
 * Node builder inspect and preview client (plan D6, F435 W4 item 4.B).
 *
 * POST /api/nodebuilder/inspect reads one page of a cooked stream (every
 * bar, every attribute) for a node or a wire. POST /api/nodebuilder/preview
 * reads small summaries (sparklines) for many nodes at once. Both read the
 * server's cook cache by `cook_id`. On a cache miss the server cooks again
 * from `graph` and `window`; with neither it answers
 * 410 `{"detail": {"code": "cook_expired"}}`.
 *
 * The types follow the plan's Wave 4 contract exactly.
 */

import axios from 'axios'
import { api } from './client'
import type { Graph } from './nodebuilder'

/** What to inspect: a node's output stream, or the stream on a wire. */
export type InspectTarget = { node_id: string } | { wire_id: string }

/** The sidebar's data window (D11), used only when the cook must run again. */
export interface InspectWindow {
  ticker: string
  start: string
  end: string
  interval: string
  source: string
}

/** A row filter. `value` is used by `gt` and `lt` only. */
export interface InspectFilter {
  attr: string
  op: 'is_true' | 'is_false' | 'gt' | 'lt' | 'not_nan'
  value: number | null
}

export interface InspectRequest {
  cook_id: string | null
  graph: Graph | null
  window: InspectWindow | null
  target: InspectTarget
  attrs: string[] | null
  offset: number
  /** At most 2000. */
  limit: number
  /** Centres the page on this bar: "YYYY-MM-DD" daily, unix seconds intraday. */
  around_time: string | number | null
  filter: InspectFilter | null
}

export interface InspectColumn {
  name: string
  dtype: 'time' | 'float' | 'bool'
  written_by: string | null
}

export interface InspectDetail {
  name: string
  dtype: string
  value: number | string | boolean
  written_by: string | null
}

export interface InspectColumnStats {
  min?: number
  max?: number
  nan_count?: number
  /** 21 edges and 20 counts. */
  hist?: { edges: number[]; counts: number[] }
  true_count?: number
}

export interface InspectResponse {
  cook_id: string
  cache: 'hit' | 'miss'
  /**
   * False when the server did not keep the cook (too big): the cook_id
   * cannot be paged alone, so every later request sends graph and window.
   * Missing (an older server) means kept.
   */
  kept?: boolean
  /** True when the fetch failed and this is the last good cook of the same graph. */
  stale_data?: boolean
  stream_schema: number
  /** Every column, `@time` first. */
  columns: InspectColumn[]
  detail: InspectDetail[]
  prims: never[]
  /** Wire targets only: the attributes the consumer node reads. */
  read_by_consumer?: string[]
  /** One per row: "YYYY-MM-DD" daily, unix seconds (UTC) intraday. */
  time: (string | number)[]
  /** One row per bar, values in column order with `@time` left out. */
  rows: (number | boolean | null)[][]
  /** Rows in the whole result (after the filter). */
  total: number
  /**
   * Every bar of the cook, before the filter (the sheet's "X of Y rows").
   * Optional: a server older than W4 integration leaves it out.
   */
  total_unfiltered?: number
  /** Index of the first returned row. */
  offset: number
  stats: Record<string, InspectColumnStats>
}

export interface PreviewNode {
  attr: string
  kind: 'line' | 'bool'
  min?: number
  max?: number
  nan_count?: number
  true_pct?: number
  values: (number | null)[]
}

export interface PreviewResponse {
  cook_id: string
  nodes: Record<string, PreviewNode>
  /** As on InspectResponse: false when the cook id cannot be reused alone. */
  kept?: boolean
  /** As on InspectResponse: the fetch failed, this is the last good cook. */
  stale_data?: boolean
}

export interface PreviewRequest {
  cook_id: string | null
  graph: Graph | null
  window: InspectRequest['window']
  node_ids: string[] | null
  points: number
}

/** Largest page the server returns. */
export const INSPECT_MAX_LIMIT = 2000

/** Read one page of a cooked stream. Pass `signal` to cancel it. */
export async function inspect(req: InspectRequest, signal?: AbortSignal): Promise<InspectResponse> {
  const body: InspectRequest = { ...req, limit: Math.max(0, Math.min(INSPECT_MAX_LIMIT, Math.floor(req.limit))) }
  const { data } = await api.post<InspectResponse>('/api/nodebuilder/inspect', body, { signal })
  return data
}

/** Read sparkline summaries for some nodes (all when `node_ids` is null). */
export async function preview(req: PreviewRequest, signal?: AbortSignal): Promise<PreviewResponse> {
  const { data } = await api.post<PreviewResponse>('/api/nodebuilder/preview', req, { signal })
  return data
}

/** True when the server no longer holds the cook (410 `cook_expired`). */
export function isCookExpired(e: unknown): boolean {
  if (!axios.isAxiosError(e) || e.response?.status !== 410) return false
  const detail = (e.response.data as { detail?: unknown } | undefined)?.detail
  // A 410 always means the cook is gone; the code is checked when present.
  if (detail && typeof detail === 'object' && 'code' in detail) {
    return (detail as { code?: unknown }).code === 'cook_expired'
  }
  return true
}

/**
 * The `code` of an inspect/preview error body (`{"detail": {"code"}}`):
 * `cook_expired` (410), `target_not_found` (404), `attr_unknown`,
 * `around_time_invalid`, `window_required`, `cook_or_graph_required` (422),
 * `data_unavailable` (502). Null for anything else.
 */
export type InspectErrorCode =
  | 'cook_expired' | 'target_not_found' | 'attr_unknown' | 'around_time_invalid' | 'window_required'
  | 'cook_or_graph_required' | 'data_unavailable'
const INSPECT_ERROR_CODES: ReadonlySet<string> = new Set([
  'cook_expired', 'target_not_found', 'attr_unknown', 'around_time_invalid', 'window_required',
  // 422: neither a cook id nor a graph (a client bug; never re-cooked).
  'cook_or_graph_required',
  // 502: the data could not be fetched and no earlier cook of the graph is live.
  'data_unavailable',
])

export function inspectErrorCode(e: unknown): InspectErrorCode | null {
  if (!axios.isAxiosError(e) || !e.response) return null
  if (isCookExpired(e)) return 'cook_expired'
  const detail = (e.response.data as { detail?: unknown } | undefined)?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = (detail as { code?: unknown }).code
    if (typeof code === 'string' && INSPECT_ERROR_CODES.has(code)) return code as InspectErrorCode
  }
  return null
}

/**
 * True when sending the graph and window again can answer the request: the
 * cook expired (410), or the target is not in the cook the id points at
 * (404 `target_not_found`: the cook predates the node or wire being added).
 * A request that already carried the graph cannot be helped this way.
 */
export function needsRecook(e: unknown): boolean {
  const code = inspectErrorCode(e)
  return code === 'cook_expired' || code === 'target_not_found'
}

/**
 * `inspect`, and when the cook has expired, the same request again with
 * `graph` and `window` so the server cooks it again (S25 "cook expired").
 * With no graph or window to send, the 410 reaches the caller.
 */
export async function inspectWithRecook(
  req: InspectRequest,
  fallback: { graph: Graph | null; window: InspectWindow | null },
  signal?: AbortSignal,
): Promise<InspectResponse> {
  try {
    return await inspect(req, signal)
  } catch (e) {
    if (!needsRecook(e) || !fallback.graph || !fallback.window) throw e
    if (req.graph && req.window) throw e
    return inspect({ ...req, graph: fallback.graph, window: fallback.window }, signal)
  }
}

/** A short reason for a failed inspect, for the sheet's error row. */
export function describeInspectError(e: unknown): string {
  if (isCookExpired(e)) return 'The cached data for this cook expired.'
  if (inspectErrorCode(e) === 'window_required') return 'Nothing is cooked yet: run the graph or turn on auto cook.'
  if (inspectErrorCode(e) === 'data_unavailable') {
    const msg = (axios.isAxiosError(e) ? (e.response?.data as { detail?: { message?: unknown } } | undefined)?.detail?.message : null)
    return `The market data could not be loaded${typeof msg === 'string' && msg ? `: ${msg}` : '.'}`
  }
  if (axios.isAxiosError(e)) {
    if (e.response) {
      const detail = (e.response.data as { detail?: unknown } | undefined)?.detail
      if (typeof detail === 'string' && detail) return detail
      if (detail && typeof detail === 'object') {
        const d = detail as { message?: unknown; detail?: unknown; code?: unknown }
        if (typeof d.message === 'string' && d.message) return d.message
        if (typeof d.detail === 'string' && d.detail) return d.detail
        if (typeof d.code === 'string' && d.code) return d.code
      }
      return `HTTP ${e.response.status}`
    }
    return e.message || 'Network error'
  }
  return e instanceof Error ? e.message : String(e)
}
