/**
 * The strategy as plain text (F454).
 *
 * Two routes give the same answer: the node builder sends the graph on
 * screen (POST /api/nodebuilder/describe), and a bot card asks for its bot
 * (GET /api/bots/{id}/describe). The server reads the graph only: it never
 * cooks data and never runs user code, so a call is quick.
 *
 * `lines` is the full text, one plain sentence per line, ready to show.
 */

import axios from 'axios'
import { api } from './client'
import type { Graph } from './nodebuilder'

export interface DescribeGroup {
  /** The Output Group's name, or "main". */
  name: string
  symbol: string | null
  interval: string | null
  direction: 'long' | 'short' | 'long_short' | null
  /** Null when nothing is wired. */
  entry: string | null
  exit: string | null
  stops: string[]
  sizing: string | null
  other: string[]
}

export interface DescribeResponse {
  groups: DescribeGroup[]
  lines: string[]
  warnings: string[]
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : []
}

/** Read a reply defensively: a missing list is an empty list. */
export function readDescribe(raw: unknown): DescribeResponse {
  const d = (raw ?? {}) as { groups?: unknown; lines?: unknown; warnings?: unknown }
  return {
    groups: Array.isArray(d.groups) ? (d.groups as DescribeGroup[]) : [],
    lines: strings(d.lines),
    warnings: strings(d.warnings),
  }
}

/** What the editor run uses besides the graph (F453 contract). */
export interface DescribeRunWindow {
  /** The symbol the run uses (the Ticker's own, or the sidebar's). */
  ticker?: string
  /** The interval the run uses. */
  interval?: string
  /**
   * The settings-panel direction. The server uses it only for a graph with
   * no Output Group (the implicit "main" group), as the run does.
   */
  direction?: 'long' | 'short' | null
}

/**
 * Describe a graph. Send the symbol, interval and direction the editor run
 * uses, so the text names what "Run backtest" runs. The server uses the
 * symbol and interval only where the primary Ticker leaves them empty.
 * Pass `signal` to cancel it (a newer edit supersedes it).
 */
export async function describeGraph(
  graph: Graph,
  run: DescribeRunWindow = {},
  signal?: AbortSignal,
): Promise<DescribeResponse> {
  const body: Record<string, unknown> = { graph }
  if (run.ticker) body.ticker = run.ticker
  if (run.interval) body.interval = run.interval
  if (run.direction === 'long' || run.direction === 'short') body.direction = run.direction
  const { data } = await api.post('/api/nodebuilder/describe', body, { signal })
  return readDescribe(data)
}

/** Describe a bot's strategy (graph bots and rule bots alike). */
export async function describeBot(botId: string, signal?: AbortSignal): Promise<DescribeResponse> {
  const { data } = await api.get(`/api/bots/${encodeURIComponent(botId)}/describe`, { signal })
  return readDescribe(data)
}

/** True when the request was cancelled on purpose (not a failure). */
export function isDescribeAbort(e: unknown): boolean {
  if (axios.isCancel(e)) return true
  return e instanceof Error && (e.name === 'AbortError' || e.name === 'CanceledError')
}

/**
 * A short, plain reason a describe call failed. `source` says who asked:
 * a 404 means "no such bot" only for a bot card. For the node builder it
 * means the server does not have this route yet (it needs a restart).
 */
export function describeErrorText(e: unknown, source: 'graph' | 'bot' = 'graph'): string {
  if (axios.isAxiosError(e)) {
    if (!e.response) return 'Could not reach the server.'
    const status = e.response.status
    const detail = (e.response.data as { detail?: unknown } | undefined)?.detail
    if (status === 404) {
      return source === 'bot' ? 'Bot not found.' : 'The server does not know this request yet. It may need a restart.'
    }
    if (typeof detail === 'string' && detail) return detail
    return `The server answered with an error (HTTP ${status}).`
  }
  return e instanceof Error && e.message ? e.message : 'Something went wrong.'
}
