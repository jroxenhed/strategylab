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
import type { Graph } from './nodebuilder'

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
  | 'name_invalid' | 'name_duplicate' | 'regime_unsupported'
  // W1 errors outside the plan table (backend/nodebuilder/diagnostics.py)
  | 'graph_invalid' | 'parent_missing' | 'parent_cycle' | 'port_duplicate'
  | 'duplicate_terminal' | 'request_invalid'
  // W1 warnings
  | 'exit_unconnected' | 'size_unit_suspect'
  // W2
  | 'attr_missing' | 'attr_type' | 'attr_clash' | 'prims_no_producer' | 'port_unknown'
  | 'attr_shadowed'

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
  /** Filled from W2 (stream per wire); empty in W1. */
  streams: Record<string, unknown>
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
    streams: data?.streams ?? {},
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
