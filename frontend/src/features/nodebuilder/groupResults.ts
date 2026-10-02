/**
 * Per-group graph results (W5, spec S33) and Output Group capital (S32a).
 * Pure helpers shared by Results.tsx (the group strip), App (which result
 * the chart shows) and the Output Group header.
 *
 * A graph with two or more Output Groups returns one result per group plus
 * a combined result (plan W5 contract). The strip shows `Combined` first,
 * then the groups in file order. With one group (or the implicit `main`)
 * there is no strip and the legacy top-level fields are shown as in W4.
 *
 * Exposure and gross deployed are never computed here: they arrive in
 * `combined.summary` (S33 "must not").
 */

import type { GraphBacktestResult } from '../../api/nodebuilder'
import type {
  BacktestResult,
  CombinedResult,
  GraphResultSummary,
  GroupResult,
  Trade,
} from '../../shared/types/strategy'

/** The `displayedGroup` value of the Combined tab. */
export const COMBINED = 'combined'

/** Tab label of the combined result. */
export const COMBINED_LABEL = 'Combined'

/** S33 copy. */
export const EXPOSURE_LABEL = 'Exposure'
export const EXPOSURE_TITLE = 'Share of bars with any leg in a position'
export const GROSS_DEPLOYED_LABEL = 'Gross deployed'
export const GROSS_DEPLOYED_TITLE = 'Average share of total capital in positions'
export const OPEN_POSITION_TITLE = 'Open position at the end of the window'
export const GROUP_COLUMN = 'group'

/** The groups of a result when there are two or more (the strip shows), else []. */
export function stripGroups(res: Pick<GraphBacktestResult, 'groups'> | null | undefined): GroupResult[] {
  const groups = res?.groups ?? []
  return groups.length >= 2 ? groups : []
}

/** Tab keys in strip order: `combined` (with 2+ groups), then group names. [] when there is no strip. */
export function groupTabKeys(res: Pick<GraphBacktestResult, 'groups'> | null | undefined): string[] {
  const groups = stripGroups(res)
  return groups.length === 0 ? [] : [COMBINED, ...groups.map(g => g.name)]
}

/**
 * The tab that is really shown: `displayed` when it names a tab, else the
 * first tab (Combined). Null when there is no strip.
 */
export function effectiveGroupKey(res: Pick<GraphBacktestResult, 'groups'> | null | undefined, displayed: string | null | undefined): string | null {
  const keys = groupTabKeys(res)
  if (keys.length === 0) return null
  return displayed && keys.includes(displayed) ? displayed : keys[0]
}

/**
 * One table for the Combined tab: every group's trades, each tagged with
 * its group. The trades table pairs entries and exits by order, so a
 * group's last entry with no exit (an open position at the end) is left
 * out; otherwise it would pair with the next group's first exit.
 */
export function mergedTrades(groups: readonly GroupResult[]): Trade[] {
  const out: Trade[] = []
  for (const g of groups) {
    const isEntry = (t: Trade) => t.type === 'buy' || t.type === 'short'
    const entries = g.trades.filter(isEntry).length
    const exits = g.trades.length - entries
    let extra = Math.max(0, entries - exits)
    // Drop unmatched entries from the end (only an open position leaves one).
    const kept: Trade[] = []
    for (let i = g.trades.length - 1; i >= 0; i--) {
      const t = g.trades[i]
      if (extra > 0 && isEntry(t)) {
        extra -= 1
        continue
      }
      kept.push(t)
    }
    kept.reverse()
    for (const t of kept) out.push({ ...t, group: g.name })
  }
  return out
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

/** A group or combined summary in the shape Results.tsx reads (missing numbers read 0). */
export function asBacktestSummary(s: GraphResultSummary, initialCapital?: number): BacktestResult['summary'] {
  return {
    ...(s as unknown as BacktestResult['summary']),
    initial_capital: num(s.initial_capital ?? initialCapital),
    final_value: num(s.final_value),
    total_return_pct: num(s.total_return_pct),
    buy_hold_return_pct: num(s.buy_hold_return_pct),
    num_trades: num(s.num_trades),
    win_rate_pct: num(s.win_rate_pct),
    sharpe_ratio: num(s.sharpe_ratio),
    max_drawdown_pct: num(s.max_drawdown_pct),
  }
}

/** What a group tab shows. */
export interface DisplayedGroupResult {
  key: string
  /** The result for Results.tsx (summary, trades, equity). */
  result: BacktestResult
  group: GroupResult | null
  combined: CombinedResult | null
}

/**
 * The result for the tab `key` (`combined` or a group name), or null when
 * the response has no strip or no such tab.
 */
export function displayedGroupResult(res: GraphBacktestResult | null | undefined, key: string | null | undefined): DisplayedGroupResult | null {
  const groups = stripGroups(res)
  if (!res || groups.length === 0 || !key) return null
  if (key === COMBINED) {
    const combined = res.combined
    if (!combined) return null
    const capital = groups.reduce((a, g) => a + num(g.capital), 0)
    return {
      key,
      result: {
        summary: asBacktestSummary(combined.summary, capital || undefined),
        trades: mergedTrades(groups),
        equity_curve: combined.equity_curve,
        baseline_curve: [],
      },
      group: null,
      combined,
    }
  }
  const group = groups.find(g => g.name === key)
  if (!group) return null
  return {
    key,
    result: {
      summary: asBacktestSummary(group.summary, group.capital),
      trades: group.trades,
      equity_curve: group.equity_curve,
      baseline_curve: [],
    },
    group,
    combined: null,
  }
}

/** Return text on a pill: `+18.2 %`, `−4.1 %`, or `0 trades`. */
export function groupReturnText(summary: GraphResultSummary): { text: string; aria: string; tone: 'ok' | 'error' | 'dim' } {
  if (num(summary.num_trades) === 0 && !summary.open_position) return { text: '0 trades', aria: 'no trades', tone: 'dim' }
  const r = num(summary.total_return_pct)
  const abs = Math.abs(r).toFixed(1)
  if (r < 0) return { text: `−${abs} %`, aria: `return minus ${abs} percent`, tone: 'error' }
  return { text: `+${abs} %`, aria: `return plus ${abs} percent`, tone: 'ok' }
}

/** A number with spaces between thousands: `10 000`. */
export function spacedNumber(n: number): string {
  return Math.round(n).toLocaleString('en-US').replace(/,/g, ' ')
}

/** A percent for copy: `50`, `33.3`. */
function pct(n: number): string {
  return Number.isInteger(Math.round(n * 10) / 10) ? String(Math.round(n)) : (Math.round(n * 10) / 10).toFixed(1)
}

/** The strip's right-hand text (Combined only): `2 groups · 10 000 capital · exposure 61 %`. */
export function stripStatusText(groups: readonly GroupResult[], combined: CombinedResult | null | undefined): string {
  const capital = num(combined?.summary.initial_capital) || groups.reduce((a, g) => a + num(g.capital), 0)
  const parts = [`${groups.length} groups`, `${spacedNumber(capital)} capital`]
  if (combined && Number.isFinite(combined.summary.exposure_pct)) parts.push(`exposure ${Math.round(combined.summary.exposure_pct)} %`)
  return parts.join(' · ')
}

/** A tile value: `61.2 %`. */
export function tilePercent(v: number | null | undefined): string {
  return typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(1)} %` : '—'
}

// ---- Capital weights (S32a) -------------------------------------------------

/** A group's weight as its params hold it (default 1; anything invalid reads 0). */
export function weightOf(params: Record<string, unknown> | undefined): number {
  const w = params?.capital_weight
  if (w === undefined || w === null || w === '') return 1
  const n = typeof w === 'number' ? w : Number(w)
  return Number.isFinite(n) && n >= 0 ? n : 0
}

/** The weight label on the tab: `1×`, `0.5×`. */
export function weightLabel(w: number): string {
  return `${Number.isInteger(w) ? w : Number(w.toFixed(2))}×`
}

/** The weight tooltip: `Capital weight 1 of 2 (50 % of initial capital)`. */
export function weightTooltip(w: number, totalWeight: number): string {
  if (w === 0) return 'This group gets no capital and will not trade.'
  const share = totalWeight > 0 ? (w / totalWeight) * 100 : 0
  const fmt = (x: number) => (Number.isInteger(x) ? String(x) : String(Number(x.toFixed(2))))
  return `Capital weight ${fmt(w)} of ${fmt(totalWeight)} (${pct(share)} % of initial capital)`
}

/** The Inspector's capital share row: `50 % · 5 000 of 10 000`. */
export function capitalShareText(w: number, totalWeight: number, initialCapital: number): string {
  const share = totalWeight > 0 ? w / totalWeight : 0
  return `${pct(share * 100)} % · ${spacedNumber(initialCapital * share)} of ${spacedNumber(initialCapital)}`
}

/** The sum of every Output Group's weight in a graph (0 when it has none). */
export function totalGroupWeight(nodes: Record<string, { type: string; params?: Record<string, unknown> }> | null | undefined): number {
  if (!nodes) return 0
  let sum = 0
  for (const n of Object.values(nodes)) if (n.type === 'output_group') sum += weightOf(n.params)
  return sum
}
