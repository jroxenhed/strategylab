/**
 * Bot spawn and graph update API (plan W5, D7; item 5.F).
 *
 * `spawnBots` turns a saved graph revision into one STOPPED bot per Output
 * Group, in one all-or-nothing call. The server loads the graph by `rev`;
 * the client never posts graph JSON. `updateBotGraph` moves one graph bot
 * to a newer saved revision through its own route, never the generic bot
 * PATCH (that request model would drop the graph fields).
 *
 * Errors keep the server's shape: `graphActionError(e)` reads the status,
 * the `detail.code`, the plain message and `current_rev` out of an axios
 * error, so the UI can show plain copy for each code.
 */

import { api } from './client'

export type SpawnBroker = 'alpaca' | 'ibkr'
export type SpawnDataSource = 'yahoo' | 'alpaca' | 'alpaca-iex' | 'ibkr'

export interface SpawnLeg {
  group: string
  allocated_capital: number
  broker: SpawnBroker
  data_source: SpawnDataSource
  /** null = the group's own interval. */
  interval_override: string | null
  /** null = the server default, "<graph name> ▸ <group>". */
  strategy_name: string | null
  /**
   * The implicit `main` group only (D7, decisions-5C): it has no direction
   * of its own, so the leg carries the sidebar's. Left out for an explicit
   * group (the server refuses it there); the server default is long.
   */
  direction?: 'long' | 'short'
}

export interface SpawnResult {
  bots: { bot_id: string; group: string; symbol: string; direction: string; running: false }[]
}

export interface GraphUpdateResult {
  bot_id: string
  graph_rev: number
}

/**
 * The sidebar-owned gates the graph backtest ran with (plan D11, LM-5),
 * shared by every leg. Each has the BotConfig field's shape; a gate left
 * out is off.
 */
export interface SpawnGates {
  trading_hours?: unknown
  skip_after_stop?: unknown
  dynamic_sizing?: unknown
}

/** Create one stopped bot per leg from saved revision `rev` of the graph. One request for all legs. */
export async function spawnBots(graphId: string, rev: number, legs: SpawnLeg[], gates: SpawnGates = {}): Promise<SpawnResult> {
  const body: Record<string, unknown> = { rev, legs }
  if (gates.trading_hours != null) body.trading_hours = gates.trading_hours
  if (gates.skip_after_stop != null) body.skip_after_stop = gates.skip_after_stop
  if (gates.dynamic_sizing != null) body.dynamic_sizing = gates.dynamic_sizing
  const { data } = await api.post<SpawnResult>(`/api/graphs/${encodeURIComponent(graphId)}/spawn`, body)
  return data
}

/** Move a graph bot to saved revision `rev` of its graph (POST /api/bots/{id}/graph_update). */
export async function updateBotGraph(botId: string, graphId: string, rev: number): Promise<GraphUpdateResult> {
  const { data } = await api.post<GraphUpdateResult>(`/api/bots/${encodeURIComponent(botId)}/graph_update`, {
    graph_id: graphId,
    rev,
  })
  return data
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** What the UI needs from a failed spawn or graph update. */
export interface GraphActionError {
  /** 'network' when no answer came back at all. */
  kind: 'http' | 'network'
  status: number | null
  /** `detail.code` (rev_conflict, in_position, group_unknown, ...), or null. */
  code: string | null
  /** `detail.message`, or `detail` when it is a string, or null. */
  message: string | null
  /** `detail.current_rev` on a rev_conflict, else null. */
  currentRev: number | null
  /** `detail.diagnostics` when present, else null. */
  diagnostics: unknown[] | null
  /** The raw detail object, for fields a caller wants to read (group names). */
  detail: Record<string, unknown> | null
}

/** Read a failed spawnBots / updateBotGraph call. Never returns the axios text. */
export function graphActionError(e: unknown): GraphActionError {
  const resp = (e as { response?: { status?: number; data?: unknown } } | null)?.response
  if (!resp) {
    return { kind: 'network', status: null, code: null, message: null, currentRev: null, diagnostics: null, detail: null }
  }
  const status = typeof resp.status === 'number' ? resp.status : null
  const data = resp.data as { detail?: unknown } | null | undefined
  const raw = data && typeof data === 'object' ? data.detail : undefined
  if (typeof raw === 'string') {
    return { kind: 'http', status, code: null, message: raw || null, currentRev: null, diagnostics: null, detail: null }
  }
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const d = raw as Record<string, unknown>
    const code = typeof d.code === 'string' ? d.code : null
    const msg =
      typeof d.message === 'string' && d.message ? d.message
        : typeof d.detail === 'string' && d.detail ? d.detail
          : null
    const rev = Number(d.current_rev)
    return {
      kind: 'http',
      status,
      code,
      message: msg,
      currentRev: d.current_rev != null && Number.isFinite(rev) ? rev : null,
      diagnostics: Array.isArray(d.diagnostics) ? d.diagnostics : null,
      detail: d,
    }
  }
  // FastAPI's 422 list, or no body: the first message, if any.
  if (Array.isArray(raw) && raw.length > 0) {
    const first = raw[0] as { msg?: unknown }
    if (typeof first?.msg === 'string') {
      return { kind: 'http', status, code: null, message: first.msg, currentRev: null, diagnostics: null, detail: null }
    }
  }
  return { kind: 'http', status, code: null, message: null, currentRev: null, diagnostics: null, detail: null }
}

/** The sentence for an error that has no special copy: the server's message, else the status. */
export function plainErrorText(err: GraphActionError): string {
  if (err.kind === 'network') return 'Could not reach the server.'
  if (err.message) return err.message
  return err.status ? `Request failed (${err.status})` : 'Request failed.'
}
