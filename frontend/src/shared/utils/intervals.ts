const INTERVAL_ORDER = ['1m', '2m', '5m', '15m', '30m', '1h', '60m', '1d', '1wk', '1mo'] as const

const INTERVAL_LABELS: Record<string, string> = {
  '1m': '1m', '2m': '2m', '5m': '5m', '15m': '15m', '30m': '30m',
  '1h': '1h', '60m': '1h', '1d': '1D', '1wk': '1W', '1mo': '1M',
}

/** Canonical seconds-per-bar map. Single source of truth — import this instead of
 *  redefining inline. Includes '2m' so auto-downsampling works for 2-minute bases.
 *  '60m' is an alias for '1h' (same seconds). */
export const INTERVAL_SECS: Record<string, number> = {
  '1m': 60, '2m': 120, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '60m': 3600, '1d': 86400,
}

export function getCoarserIntervals(base: string): { value: string; label: string }[] {
  const baseIdx = INTERVAL_ORDER.indexOf(base as any)
  if (baseIdx < 0) return []
  const seen = new Set<string>()
  const result: { value: string; label: string }[] = []
  for (let i = baseIdx; i < INTERVAL_ORDER.length; i++) {
    const v = INTERVAL_ORDER[i]
    const label = INTERVAL_LABELS[v] ?? v
    if (seen.has(label)) continue
    seen.add(label)
    result.push({ value: v, label })
  }
  return result
}

export function isIntraday(interval: string): boolean {
  return ['1m', '2m', '5m', '15m', '30m', '1h', '60m'].includes(interval)
}

/** Fixed coarser-interval ladder keyed by base interval.
 *  Tier 0 = first step up; tier 1 = second step (used when visible span is very large).
 *  Only intraday bases are listed — daily/unknown returns null (auto never engages). */
const COARSER_LADDER: Record<string, [string, string | null]> = {
  '1m':  ['5m',  '15m'],
  '2m':  ['5m',  '15m'],
  '5m':  ['15m', '1h'],
  '15m': ['1h',  '1d'],
  '30m': ['1h',  '1d'],
  '1h':  ['1d',  null],
  '60m': ['1d',  null],
}

/** AUTOS_ON threshold (base-equivalent bars) — auto-aggregate when visible span exceeds this. */
export const AUTOS_ON_BARS = 8_000
/** AUTOS_OFF threshold (base-equivalent bars) — de-aggregate when visible span drops below this. */
export const AUTOS_OFF_BARS = 6_000

/**
 * Return the coarser interval to auto-switch to given the base interval and
 * the current number of visible base-equivalent bars.
 * Returns null when: base is daily/unknown; already at the coarsest supported level.
 * Picks tier 2 when visible bars are large enough that tier 1 would still exceed
 * AUTOS_ON_BARS — avoids immediately re-triggering auto after the first switch.
 */
export function coarserIntervalFor(base: string, visibleBaseBars: number): string | null {
  const ladder = COARSER_LADDER[base]
  if (!ladder) return null
  const [tier1, tier2] = ladder
  // Estimate bars after switching to tier1:
  // visibleBaseBars / (tier1Secs / baseSecs) = visibleBaseBars * (baseSecs / tier1Secs).
  // For 1m base → 5m tier1: ratio=5, barsAfterTier1 = visibleBaseBars / 5 (fewer bars).
  // If still above ON threshold, use tier2 to avoid immediately re-triggering.
  if (tier2 !== null) {
    const baseSecs = INTERVAL_SECS[base] ?? 0
    const tier1Secs = INTERVAL_SECS[tier1] ?? 0
    if (baseSecs > 0 && tier1Secs > 0) {
      const barsAfterTier1 = visibleBaseBars / (tier1Secs / baseSecs)
      if (barsAfterTier1 > AUTOS_ON_BARS) return tier2
    }
  }
  return tier1
}

/**
 * Compute the number of base-equivalent visible bars given a raw logical span,
 * the current view interval, and the base interval (D3 normalization).
 *
 * After a 5m→15m auto-switch, the raw logical span drops to ~1/3 of the pre-switch
 * value (fewer 15m bars in the same time window). Without normalization this would
 * trigger the de-aggregate branch immediately, causing a re-aggregate loop.
 *
 * Formula: rawSpan * (viewSecs / baseSecs)
 * Example: rawSpan=1000 at 15m view on 5m base → 1000 * (900/300) = 3000 base-equiv bars.
 * Example (inverted ratio check): rawSpan=1000 at 5m view on 1m base → 1000 * (300/60) = 5000.
 */
export function calcVisibleBaseBars(rawSpan: number, viewInterval: string, baseInterval: string): number {
  const viewSecs = INTERVAL_SECS[viewInterval] ?? 0
  const baseSecs = INTERVAL_SECS[baseInterval] ?? 0
  if (baseSecs > 0 && viewSecs > 0) {
    return rawSpan * (viewSecs / baseSecs)
  }
  return rawSpan
}

/** Result of auto-interval span evaluation. */
export type AutoIntervalAction = { action: 'coarsen'; target: string } | { action: 'restore'; target: string } | { action: 'none' }

/**
 * Pure helper: evaluate whether to coarsen, restore, or do nothing, given the
 * current chart state. Used by Chart.tsx's debounced syncHandler and directly
 * testable without DOM/chart dependencies.
 *
 * pendingSince: timestamp (Date.now()) when the pending switch was initiated, or null.
 * Treats a pending switch older than 5000ms as expired (safety valve for COR-01/RACE-03).
 * Reverse-zoom detection (RACE-01): if a switch is pending but the new evaluation
 * resolves to 'none' or 'restore' (user zoomed back in), cancels the pending state.
 */
export function evaluateAutoInterval(opts: {
  visibleBaseBars: number
  viewInterval: string
  baseInterval: string
  autoActive: boolean
  pendingSince: number | null
  now: number
}): AutoIntervalAction {
  const { visibleBaseBars, viewInterval, baseInterval, autoActive, pendingSince, now } = opts

  // Safety valve: if a switch is pending > 5s, treat as expired (COR-01 / RACE-03).
  const pendingExpired = pendingSince !== null && (now - pendingSince) > 5000

  // While a valid (non-expired) switch is pending, suppress further evaluations.
  if (pendingSince !== null && !pendingExpired) {
    // RACE-01: if user zoomed back in while pending, cancel the pending state → 'none'.
    if (visibleBaseBars <= AUTOS_OFF_BARS) {
      return { action: 'none' }
    }
    // RACE-01: if evaluation would resolve to the current view (no switch needed), cancel.
    const coarser = coarserIntervalFor(baseInterval, visibleBaseBars)
    if (!coarser || coarser === viewInterval) {
      return { action: 'none' }
    }
    // Otherwise still pending — suppress.
    return { action: 'none' }
  }

  // Only engage for intraday base intervals; daily/unknown passthrough.
  if (!isIntraday(baseInterval)) return { action: 'none' }

  if (visibleBaseBars > AUTOS_ON_BARS && (viewInterval === baseInterval || autoActive)) {
    // FIX-C: allow re-escalation when autoActive (already on auto-chosen coarse view).
    const coarser = coarserIntervalFor(baseInterval, visibleBaseBars)
    if (coarser && coarser !== viewInterval) {
      return { action: 'coarsen', target: coarser }
    }
    return { action: 'none' }
  }

  if (visibleBaseBars <= AUTOS_OFF_BARS && viewInterval !== baseInterval && autoActive) {
    return { action: 'restore', target: baseInterval }
  }

  return { action: 'none' }
}
