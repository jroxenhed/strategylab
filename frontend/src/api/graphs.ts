/**
 * Saved graphs API (plan W1 item 1.B contract, D2).
 *
 * Graphs are named server objects: one JSON envelope per graph with a `rev`
 * that goes up on every save. A save must carry the rev it was based on; a
 * stale rev comes back as 409 `rev_conflict`, which `saveGraph` and
 * `deleteGraph` throw as a `RevConflictError`. A name clash (create or
 * rename) comes back as 409 `name_taken`, thrown as a `NameTakenError`.
 */

import { api } from './client'
import type { Graph } from './nodebuilder'

export interface GraphEnvelope {
  id: string
  rev: number
  name: string
  description: string
  created_at: string
  updated_at: string
  graph: Graph
}

export interface GraphListItem {
  id: string
  rev: number
  name: string
  description: string
  updated_at: string
  node_count: number
  groups: string[]
}

export interface RevConflict {
  code: 'rev_conflict'
  current_rev: number
}

export interface NameTaken {
  code: 'name_taken'
}

/** Thrown when the server has a newer rev than the one sent. */
export class RevConflictError extends Error implements RevConflict {
  readonly code = 'rev_conflict' as const
  readonly current_rev: number
  constructor(currentRev: number) {
    super(`The graph changed on the server (rev ${currentRev}).`)
    this.name = 'RevConflictError'
    this.current_rev = currentRev
  }
}

/** Thrown when another graph already has this name (case-insensitive). */
export class NameTakenError extends Error implements NameTaken {
  readonly code = 'name_taken' as const
  constructor() {
    super('That name is taken.')
    this.name = 'NameTakenError'
  }
}

export function isRevConflict(e: unknown): e is RevConflictError {
  return e instanceof RevConflictError
}

/**
 * 409 `{detail: {code: 'graph_corrupt', detail}}`: the stored graph file on
 * the server cannot be read (GET, PUT, POST duplicate_of). Never a rev
 * conflict: nothing on the server is newer, and nothing may overwrite it
 * silently. `graphErrorDetail` shows its sentence.
 */
export interface GraphCorrupt {
  code: 'graph_corrupt'
  detail: string
}

export function isGraphCorrupt(e: unknown): boolean {
  const resp = (e as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  const detail = resp?.data?.detail
  return resp?.status === 409 && !!detail && typeof detail === 'object' && (detail as { code?: unknown }).code === 'graph_corrupt'
}

export function isNameTaken(e: unknown): e is NameTakenError {
  return e instanceof NameTakenError
}

/** Turn an axios 409 into the typed error it stands for; anything else is rethrown as is. */
function rethrow(e: unknown): never {
  const resp = (e as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  if (resp?.status === 409) {
    const detail = resp.data?.detail
    if (detail && typeof detail === 'object') {
      const code = (detail as { code?: unknown }).code
      if (code === 'rev_conflict') {
        const rev = Number((detail as { current_rev?: unknown }).current_rev)
        throw new RevConflictError(Number.isFinite(rev) ? rev : 0)
      }
      if (code === 'name_taken') throw new NameTakenError()
    }
  }
  throw e
}

/**
 * The sentence to show for a failed graphs call (S07): the server's
 * `detail` string, a sentence for a known `{code}` detail, else
 * `Request failed (<status>)`. Never the raw axios message.
 */
export function graphErrorDetail(e: unknown): string {
  if (e instanceof RevConflictError) return 'The graph changed on the server.'
  if (e instanceof NameTakenError) return 'That name is taken.'
  const resp = (e as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  const detail = resp?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = (detail as { code?: unknown }).code
    if (code === 'rev_conflict') return 'The graph changed on the server.'
    if (code === 'name_taken') return 'That name is taken.'
    const inner = (detail as { detail?: unknown }).detail
    if (typeof inner === 'string' && inner) return inner
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown }
    if (typeof first?.msg === 'string') return first.msg
  }
  if (resp?.status) return `Request failed (${resp.status})`
  return 'Could not reach the server.'
}

/** The diagnostics list of a 400 graph error body, when there is one (plan 4.4). */
export function errorDiagnostics(e: unknown): unknown[] | null {
  const data = (e as { response?: { data?: unknown } } | null)?.response?.data
  if (!data || typeof data !== 'object') return null
  const top = (data as { diagnostics?: unknown }).diagnostics
  if (Array.isArray(top)) return top
  const detail = (data as { detail?: unknown }).detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const inner = (detail as { diagnostics?: unknown }).diagnostics
    if (Array.isArray(inner)) return inner
  }
  return null
}

export async function listGraphs(): Promise<GraphListItem[]> {
  const { data } = await api.get<{ graphs: GraphListItem[] }>('/api/graphs')
  return Array.isArray(data?.graphs) ? data.graphs : []
}

export async function getGraph(id: string): Promise<GraphEnvelope> {
  const { data } = await api.get<GraphEnvelope>(`/api/graphs/${encodeURIComponent(id)}`)
  return data
}

export async function createGraph(body: {
  name: string
  description?: string
  graph?: Graph
  duplicate_of?: string
}): Promise<GraphEnvelope> {
  try {
    const { data } = await api.post<GraphEnvelope>('/api/graphs', body)
    return data
  } catch (e) {
    rethrow(e)
  }
}

/** Save a graph. Throws RevConflictError when `rev` is stale, NameTakenError on a name clash. */
export async function saveGraph(
  id: string,
  body: { rev: number; name?: string; description?: string; graph: Graph },
): Promise<GraphEnvelope> {
  try {
    const { data } = await api.put<GraphEnvelope>(`/api/graphs/${encodeURIComponent(id)}`, body)
    return data
  } catch (e) {
    rethrow(e)
  }
}

/** Delete a graph. Throws RevConflictError when it changed on the server since `rev`. */
export async function deleteGraph(id: string, rev: number): Promise<void> {
  try {
    await api.delete(`/api/graphs/${encodeURIComponent(id)}`, { params: { rev } })
  } catch (e) {
    rethrow(e)
  }
}

export async function seedLegacyGraphs(
  legacy: unknown,
): Promise<{ imported: string[]; skipped: { name: string; reason: string }[] }> {
  const { data } = await api.post<{ imported: string[]; skipped: { name: string; reason: string }[] }>(
    '/api/graphs/seed',
    { legacy },
  )
  return {
    imported: Array.isArray(data?.imported) ? data.imported : [],
    skipped: Array.isArray(data?.skipped) ? data.skipped : [],
  }
}
