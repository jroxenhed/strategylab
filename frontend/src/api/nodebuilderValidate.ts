/**
 * Node builder validate client (plan 4.2, F435 W1 item 1.G).
 *
 * POST /api/nodebuilder/validate checks a graph without fetching any market
 * data and returns every problem it finds as a Diagnostic. The same
 * Diagnostic shape rides on every 400 from a graph route (plan 4.4), so
 * `diagnosticsFromError` reads it back out of a failed Run too.
 */

import axios from 'axios'
import { api } from './client'
import type { AttrInfo, Graph, StreamSchema } from './nodebuilder'

export type Severity = 'error' | 'warning' | 'info'

/**
 * Diagnostic codes the backend sends (backend/nodebuilder/diagnostics.py).
 * Each wave adds codes there; an unknown code is still a valid string here,
 * so a newer server never breaks an older page.
 */
export type KnownDiagnosticCode =
  // W1 errors
  | 'missing_terminal' | 'unsupported_node' | 'unknown_node_type' | 'dangling_wire'
  | 'cycle' | 'missing_input' | 'param_invalid' | 'param_out_of_range' | 'family_cap'
  | 'name_invalid' | 'name_duplicate'
  // W1 errors outside the plan table (backend/nodebuilder/diagnostics.py)
  | 'graph_invalid' | 'parent_missing' | 'parent_cycle' | 'port_duplicate'
  | 'duplicate_terminal' | 'request_invalid'
  // W1 warnings
  | 'exit_unconnected' | 'size_unit_suspect'
  // W2
  | 'attr_missing' | 'attr_type' | 'attr_clash' | 'prims_no_producer' | 'port_unknown'
  | 'attr_shadowed'
  // W5 (Output Groups, networks, terminals)
  | 'group_invalid' | 'group_terminal_outside' | 'group_duplicate_terminal' | 'ticker_missing'
  | 'wire_crosses_network' | 'boundary_invalid'
  // W5 warnings
  | 'setting_shadowed' | 'group_weight_zero' | 'setting_unscoped'

export type DiagnosticCode = KnownDiagnosticCode | (string & {})

export interface Diagnostic {
  node_id: string | null
  path: string | null
  severity: Severity
  code: DiagnosticCode
  message: string
  param: string | null
  port: string | null
  line: number | null
  col: number | null
  end_line: number | null
  end_col: number | null
}

export interface ValidateResponse {
  ok: boolean
  diagnostics: Diagnostic[]
  /**
   * Each node's OUTPUT stream, keyed by node id (plan 3.3, filled from W2).
   * A node missing here has no known stream (it could not be checked).
   */
  streams: Record<string, StreamSchema>
}

function readAttrs(raw: unknown): AttrInfo[] {
  if (!Array.isArray(raw)) return []
  const out: AttrInfo[] = []
  for (const a of raw) {
    if (!a || typeof a !== 'object') continue
    const { name, dtype, written_by } = a as Record<string, unknown>
    if (typeof name !== 'string' || !name) continue
    out.push({
      name,
      dtype: (typeof dtype === 'string' ? dtype : 'any') as AttrInfo['dtype'],
      written_by: typeof written_by === 'string' ? written_by : null,
    })
  }
  return out
}

/**
 * The `streams` map from a validate reply, with every entry checked. A
 * server that sends no streams (W1) or a broken entry gives an empty map
 * or skips that entry; it never throws.
 */
export function readStreams(raw: unknown): Record<string, StreamSchema> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: Record<string, StreamSchema> = {}
  for (const [id, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!value || typeof value !== 'object') continue
    const v = value as Record<string, unknown>
    out[id] = {
      stream_schema: typeof v.stream_schema === 'number' ? v.stream_schema : 1,
      points: readAttrs(v.points),
      detail: readAttrs(v.detail),
      prims: Array.isArray(v.prims)
        ? (v.prims as unknown[]).flatMap(p => {
            if (!p || typeof p !== 'object') return []
            const kind = (p as Record<string, unknown>).kind
            return typeof kind === 'string' ? [{ kind, attrs: readAttrs((p as Record<string, unknown>).attrs) }] : []
          })
        : [],
    }
  }
  return out
}

/**
 * Validate a graph. Pass `signal` to cancel it (a newer edit supersedes it).
 * Throws on a network error or a non-2xx reply; see `diagnosticsFromError`.
 */
export async function validateGraph(graph: Graph, signal?: AbortSignal): Promise<ValidateResponse> {
  const { data } = await api.post<ValidateResponse>('/api/nodebuilder/validate', { graph }, { signal })
  return {
    ok: data?.ok ?? true,
    diagnostics: Array.isArray(data?.diagnostics) ? data.diagnostics : [],
    streams: readStreams(data?.streams),
  }
}

/** True when the request was cancelled on purpose (not a failure). */
export function isAbortError(e: unknown): boolean {
  if (axios.isCancel(e)) return true
  return e instanceof Error && (e.name === 'AbortError' || e.name === 'CanceledError')
}

/**
 * The `diagnostics` list from a 400 graph-route reply (plan 4.4), or null
 * when the error is anything else (network down, 500, no list).
 */
export function diagnosticsFromError(e: unknown): Diagnostic[] | null {
  if (!axios.isAxiosError(e)) return null
  const body = e.response?.data as { diagnostics?: unknown } | undefined
  if (e.response?.status !== 400 || !body || !Array.isArray(body.diagnostics)) return null
  return body.diagnostics as Diagnostic[]
}

/** A short reason for a failed validate, for the offline notice. */
export function describeValidateError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    if (e.response) {
      const detail = (e.response.data as { detail?: unknown } | undefined)?.detail
      if (typeof detail === 'string' && detail) return detail
      return `HTTP ${e.response.status}`
    }
    return e.message || 'Network error'
  }
  return e instanceof Error ? e.message : String(e)
}
