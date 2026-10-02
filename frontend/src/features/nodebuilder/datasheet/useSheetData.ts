/**
 * Loads the Data Sheet's rows from POST /inspect (S25 "Paging").
 *
 * - Pages of 500 rows are asked for around the visible rows
 *   (offset = first missing visible row - 250, clamped).
 * - At most 2 000 rows are kept; rows more than 1 000 away from the
 *   viewport are dropped. `total` drives the scroll height.
 * - A request is made only when the target, the cook, the filter or the
 *   visible page changes, never on pointer moves or keystrokes.
 * - When the server no longer has the cook (410 `cook_expired`), the same
 *   request is sent again with `graph` and `window` so the server cooks it
 *   again. Later pages then use the new `cook_id`.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Graph } from '../../../api/nodebuilder'
import {
  inspect,
  inspectErrorCode,
  isCookExpired,
  needsRecook,
  describeInspectError,
  type InspectColumn,
  type InspectColumnStats,
  type InspectDetail,
  type InspectErrorCode,
  type InspectFilter,
  type InspectRequest,
  type InspectResponse,
  type InspectWindow,
} from '../../../api/nodebuilderInspect'
import { isAbortError } from '../../../api/nodebuilderValidate'
import { dismissNotice, pushNotice } from '../notices'
import { inspectTargetOf, targetKey, type SheetTarget } from './target'
import { timeAfter, timeKey } from './format'

export const SHEET_PAGE = 500
export const SHEET_MAX_ROWS = 2000
export const SHEET_KEEP_DISTANCE = 1000
/** Rows asked for before the first missing visible row. */
export const SHEET_LEAD = 250

const EXPIRED_NOTICE = 'cook_expired'
/** The banner when an expired cook could not be cooked again (DV-11). */
export const EXPIRED_NO_FALLBACK_TEXT = 'The cached data for this cook expired. Run the backtest or turn on auto cook to cook it again.'
export const EXPIRED_RECOOK_FAILED_TEXT = 'The cached data for this cook expired, and cooking it again failed.'
/** Wait this long while the visible rows keep moving before asking for a page (DV-8). */
export const SHEET_PAGE_DEBOUNCE_MS = 100

export interface SheetRow {
  time: string | number
  values: (number | boolean | null)[]
}

export interface SheetData {
  /** The request key this data answers (target, cook and filter). */
  key: string
  targetKey: string
  /** The cook these rows came from (after a re-cook, the new one). */
  cookId: string
  /** Data columns in row order (the time column left out). */
  columns: InspectColumn[]
  detail: InspectDetail[]
  /** Wire targets: what the consumer reads. Null for a node target. */
  readBy: string[] | null
  total: number
  stats: Record<string, InspectColumnStats>
  /** Rows by absolute index (filtered index when a filter is on). */
  rows: Map<number, SheetRow>
  /** The server could not fetch fresh data and answered from the last good cook. */
  staleData: boolean
}

export interface SheetDataOptions {
  target: SheetTarget | null
  cookId: string | null
  filter: InspectFilter | null
  /** First and last visible row (overscan included). */
  range: { first: number; last: number }
  /** Graph and window sent again when the cook has expired. */
  getFallback(): { graph: Graph | null; window: InspectWindow | null }
}

export interface SheetDataResult {
  data: SheetData | null
  /** The current request key; `data.key` differs while new data loads. */
  key: string
  loading: boolean
  error: string | null
  /** The server's error code for `error` (`attr_unknown`, ...), or null. */
  errorCode: InspectErrorCode | null
  /** Rows without the filter, when known for this target and cook. */
  unfilteredTotal: number | null
  /** Load the page around `time`; resolves with that row's index, or null. */
  jumpTo(time: string | number): Promise<number | null>
  retry(): void
}

interface InFlight {
  ctrl: AbortController
  key: string
  offset: number
  limit: number
  jump: boolean
}

/** The request key for a target, cook and filter. */
export function sheetKey(target: SheetTarget | null, cookId: string | null, filter: InspectFilter | null): string {
  if (!target || !cookId) return ''
  return `${targetKey(target)}|${cookId}|${filter ? `${filter.attr}:${filter.op}:${filter.value ?? ''}` : ''}`
}

/** Drop rows far from `center`, then the farthest rows over the cap. */
export function evictRows(rows: Map<number, SheetRow>, center: number, keep = SHEET_KEEP_DISTANCE, cap = SHEET_MAX_ROWS): void {
  for (const idx of [...rows.keys()]) {
    if (Math.abs(idx - center) > keep) rows.delete(idx)
  }
  if (rows.size <= cap) return
  const byDistance = [...rows.keys()].sort((a, b) => Math.abs(b - center) - Math.abs(a - center))
  for (const idx of byDistance) {
    if (rows.size <= cap) break
    rows.delete(idx)
  }
}

/** First index in [first, last] (inside total) with no row, or -1. */
export function firstMissing(rows: Map<number, SheetRow> | undefined, first: number, last: number, total: number): number {
  const hi = Math.min(last, total - 1)
  for (let i = Math.max(0, first); i <= hi; i++) {
    if (!rows || !rows.has(i)) return i
  }
  return -1
}

/** The page offset for a missing row (S25: first visible row - 250, clamped). */
export function pageOffsetFor(missing: number, total: number): number {
  return Math.max(0, Math.min(missing - SHEET_LEAD, Math.max(0, total - 1)))
}

/** Index inside a page of the bar at `time`: an exact match, else the first later bar. */
export function indexOfTime(times: readonly (string | number)[], time: string | number): number {
  const k = timeKey(time)
  const exact = times.findIndex(t => timeKey(t) === k)
  if (exact >= 0) return exact
  const later = times.findIndex(t => timeAfter(t, time))
  return later >= 0 ? later : times.length - 1
}

export function useSheetData(opts: SheetDataOptions): SheetDataResult {
  const { target, cookId, filter, range } = opts
  const key = sheetKey(target, cookId, filter)
  const tKey = targetKey(target)

  const [data, setData] = useState<SheetData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ text: string; code: InspectErrorCode | null } | null>(null)
  const [unfiltered, setUnfiltered] = useState<{ key: string; total: number } | null>(null)
  const [retrySeq, setRetrySeq] = useState(0)

  const keyRef = useRef(key)
  keyRef.current = key
  const dataRef = useRef<SheetData | null>(null)
  dataRef.current = data
  const optsRef = useRef(opts)
  optsRef.current = opts
  const inFlight = useRef<InFlight | null>(null)
  /**
   * Expired cook id -> the cook a re-cook returned for it (DV-11). Every
   * later key built from the expired prop cook id asks for the new cook
   * directly, so a target or filter change does not 410 again.
   */
  const recooked = useRef<Map<string, string>>(new Map())
  /**
   * Cook ids the server did not keep (`kept: false`, too big): paging one by
   * id alone would only 410, so every request for it sends graph and window.
   */
  const unkept = useRef<Set<string>>(new Set())
  /** The cook to ask for: the prop, or the one a re-cook returned. */
  const cookRef = useRef<{ key: string; cookId: string | null }>({ key, cookId })
  if (cookRef.current.key !== key) cookRef.current = { key, cookId: cookId ? (recooked.current.get(cookId) ?? cookId) : cookId }
  /** The last page answered: a still-missing row inside it is not asked again. */
  const lastAnswer = useRef<{ key: string; offset: number; limit: number } | null>(null)
  const pushedExpired = useRef(false)
  const centerRef = useRef(0)
  centerRef.current = Math.round((range.first + range.last) / 2)

  const abort = useCallback(() => {
    inFlight.current?.ctrl.abort()
    inFlight.current = null
  }, [])

  // Abort whatever is in flight when the sheet goes away.
  useEffect(() => abort, [abort])

  const request = useCallback(async (
    reqKey: string,
    offset: number,
    limit: number,
    aroundTime: string | number | null,
  ): Promise<InspectResponse | null> => {
    const { target: t, filter: f } = optsRef.current
    if (!t || reqKey !== keyRef.current) return null
    abort()
    const ctrl = new AbortController()
    inFlight.current = { ctrl, key: reqKey, offset, limit, jump: aroundTime != null }
    setLoading(true)
    const askCook = cookRef.current.cookId
    const withGraph = askCook != null && unkept.current.has(askCook) ? optsRef.current.getFallback() : null
    const sendGraph = withGraph != null && withGraph.graph != null && withGraph.window != null
    const base: InspectRequest = {
      cook_id: askCook,
      graph: sendGraph ? withGraph!.graph : null,
      window: sendGraph ? withGraph!.window : null,
      target: inspectTargetOf(t),
      attrs: null,
      offset,
      limit,
      around_time: aroundTime,
      filter: f,
    }
    // Shown only when no re-cook can answer (DV-11: never "Cooking again…"
    // while nothing is cooking).
    const expired = (text: string) => {
      pushedExpired.current = true
      pushNotice({ key: EXPIRED_NOTICE, severity: 'warn', text })
    }
    try {
      let res: InspectResponse
      try {
        res = await inspect(base, ctrl.signal)
      } catch (e) {
        // A 410 (cook expired) or a 404 target_not_found (the cook predates
        // this node or wire) is answered by sending the graph and window.
        if (!needsRecook(e)) throw e
        const wasExpired = isCookExpired(e)
        const fb = optsRef.current.getFallback()
        if (!fb.graph || !fb.window) { if (wasExpired) expired(EXPIRED_NO_FALLBACK_TEXT); throw e }
        try {
          res = await inspect({ ...base, graph: fb.graph, window: fb.window }, ctrl.signal)
        } catch (e2) {
          if (wasExpired && !isAbortError(e2)) expired(EXPIRED_RECOOK_FAILED_TEXT)
          throw e2
        }
        const asked = base.cook_id
        const propCook = optsRef.current.cookId
        if (res.cook_id && propCook && res.cook_id !== asked) recooked.current.set(propCook, res.cook_id)
      }
      if (inFlight.current?.ctrl === ctrl) inFlight.current = null
      if (reqKey !== keyRef.current || ctrl.signal.aborted) return null
      if (pushedExpired.current) { pushedExpired.current = false; dismissNotice(EXPIRED_NOTICE) }
      if (res.cook_id) {
        cookRef.current = { key: reqKey, cookId: res.cook_id }
        if (res.kept === false) unkept.current.add(res.cook_id)
        else unkept.current.delete(res.cook_id)
      }
      lastAnswer.current = { key: reqKey, offset: res.offset ?? offset, limit }
      const center = aroundTime != null && Array.isArray(res.time)
        ? (res.offset ?? 0) + indexOfTime(res.time, aroundTime)
        : centerRef.current
      setData(prev => mergePage(prev, reqKey, tKeyOf(reqKey), res, center))
      // "X of Y rows": the server's unfiltered total when it sends one (any
      // page, filtered or not); else remember an unfiltered page's total.
      if (typeof res.total_unfiltered === 'number') {
        setUnfiltered({ key: `${tKeyOf(reqKey)}|${res.cook_id}`, total: res.total_unfiltered })
      } else if (!f) {
        setUnfiltered({ key: `${tKeyOf(reqKey)}|${res.cook_id}`, total: res.total })
      }
      setError(null)
      setLoading(false)
      return res
    } catch (e) {
      if (inFlight.current?.ctrl === ctrl) inFlight.current = null
      if (isAbortError(e) || ctrl.signal.aborted || reqKey !== keyRef.current) return null
      setError({ text: describeInspectError(e), code: inspectErrorCode(e) })
      setLoading(false)
      return null
    }
  }, [abort])

  // A new target, cook or filter: ask for the first page. The old table stays
  // (dimmed) until the answer lands. A new cook for the same target and
  // filter keeps the scroll position.
  useEffect(() => {
    if (!key) { abort(); setLoading(false); setError(null); return }
    const prev = dataRef.current
    const sameView = prev != null && prev.key.split('|')[0] === key.split('|')[0] && prev.key.split('|')[2] === key.split('|')[2]
    const { first } = optsRef.current.range
    const offset = sameView ? pageOffsetFor(first, prev!.total) : 0
    setError(null)
    void request(key, offset, SHEET_PAGE, null)
  }, [key, retrySeq, abort, request])

  // Visible rows that are not loaded yet: ask for the page around them.
  useEffect(() => {
    if (!data || data.key !== key || error) return
    const missing = firstMissing(data.rows, range.first, range.last, data.total)
    if (missing < 0) return
    const fl = inFlight.current
    if (fl && fl.key === key && (fl.jump || (missing >= fl.offset && missing < fl.offset + fl.limit))) return
    const la = lastAnswer.current
    if (la && la.key === key && missing >= la.offset && missing < la.offset + la.limit) return
    // Scrolling (a thumb drag crosses many pages): wait until the visible rows
    // settle, so one request goes out instead of one per page crossed (DV-8).
    // A new key and a jump are asked for at once (the effects above and jumpTo).
    const total = data.total
    const t = setTimeout(() => { void request(key, pageOffsetFor(missing, total), SHEET_PAGE, null) }, SHEET_PAGE_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [data, key, error, range.first, range.last, request])

  const jumpTo = useCallback(async (time: string | number) => {
    const k = keyRef.current
    if (!k) return null
    const res = await request(k, 0, SHEET_PAGE, time)
    if (!res || !Array.isArray(res.time) || res.time.length === 0) return null
    return (res.offset ?? 0) + indexOfTime(res.time, time)
  }, [request])

  const retry = useCallback(() => {
    lastAnswer.current = null
    setRetrySeq(n => n + 1)
  }, [])

  const unfilteredTotal = unfiltered && data && unfiltered.key === `${tKey}|${data.cookId}` ? unfiltered.total : null

  return { data, key, loading, error: error?.text ?? null, errorCode: error?.code ?? null, unfilteredTotal, jumpTo, retry }
}

function tKeyOf(reqKey: string): string {
  return reqKey.split('|')[0]
}

/**
 * Put one answered page into the row map. A new key starts a new map, and so
 * does a different cook under the same key (a re-cook mid-paging): rows of two
 * cooks never sit in one table (DV-4).
 */
export function mergePage(prev: SheetData | null, key: string, tKey: string, res: InspectResponse, center: number): SheetData {
  const same = prev != null && prev.key === key && prev.cookId === res.cook_id
  const rows = same ? new Map(prev!.rows) : new Map<number, SheetRow>()
  const offset = res.offset ?? 0
  const times = Array.isArray(res.time) ? res.time : []
  const pageRows = Array.isArray(res.rows) ? res.rows : []
  for (let i = 0; i < pageRows.length; i++) rows.set(offset + i, { time: times[i], values: pageRows[i] })
  evictRows(rows, center)
  const columns = (Array.isArray(res.columns) ? res.columns : []).filter(c => c.dtype !== 'time')
  return {
    key,
    targetKey: tKey,
    cookId: res.cook_id,
    columns,
    detail: Array.isArray(res.detail) ? res.detail : [],
    readBy: Array.isArray(res.read_by_consumer) ? res.read_by_consumer : null,
    total: typeof res.total === 'number' ? res.total : rows.size,
    stats: res.stats && typeof res.stats === 'object' ? res.stats : {},
    rows,
    staleData: res.stale_data === true,
  }
}
