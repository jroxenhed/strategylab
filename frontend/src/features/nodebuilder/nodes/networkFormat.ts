/**
 * Text and small helpers for network frames, Output Group headers and
 * terminal cards (W5, specs S31, S32a, S32b). Plain functions, kept apart
 * from the components so React fast refresh works on those files.
 */

import type { FrameNodeData, GroupTicker } from '../rfMapping'

// ---- Network frame (S31) ----------------------------------------------------

/** Empty-frame copy (S31). */
export const EMPTY_FRAME_TEXT = 'Empty network. Drag nodes in, or press Tab inside it.'

/** The frame's accessible name (S31). */
export function frameAriaLabel(data: Pick<FrameNodeData, 'backendType' | 'name' | 'params' | 'frame' | 'groupTicker'>): string {
  const name = data.name ?? ''
  if (data.backendType === 'output_group') {
    const dir = typeof data.params?.direction === 'string' ? data.params.direction : 'long'
    const t = data.groupTicker
    const ticker = t ? `${t.symbol} ${t.interval}`.trim() : 'no ticker'
    return `Output group ${name}, ${dir === 'regime_switch' ? 'switch' : dir}, ${ticker}`
  }
  const n = data.frame.childCount
  return `Network ${name}, ${n} ${n === 1 ? 'node' : 'nodes'}`
}

// ---- Output Group header (S32a) ---------------------------------------------

export type GroupDirection = 'long' | 'short' | 'regime_switch'

/** Pill text per direction (S32a). */
export function directionLabel(d: unknown): 'LONG' | 'SHORT' | 'SWITCH' {
  return d === 'short' ? 'SHORT' : d === 'regime_switch' ? 'SWITCH' : 'LONG'
}

/** Direction popover rows (S32a copy). */
export const DIRECTION_ROWS: ReadonlyArray<{ value: GroupDirection; hint: string }> = [
  { value: 'long', hint: 'Buy on entry, sell on exit' },
  { value: 'short', hint: 'Sell short on entry, cover on exit' },
  { value: 'regime_switch', hint: 'Long or short by regime; one entry and exit per side' },
]

export const pillClass = (d: unknown) => `nb-dir-pill nb-dir-pill--${directionLabel(d).toLowerCase()}`

/** The ticker chip text: `AAPL · 1d`, or `no ticker ▾`. */
export function tickerChipText(t: GroupTicker | null): string {
  if (!t) return 'no ticker ▾'
  return t.interval ? `${t.symbol || t.name} · ${t.interval}` : (t.symbol || t.name)
}

// ---- Terminal cards (S32b) --------------------------------------------------

/** Card width of a terminal (S32b). */
export const TERMINAL_WIDTH = 118

/** The terminal's card name (S32b): the type, in plain words. */
export function terminalTitle(backendType: string): string {
  switch (backendType) {
    case 'trailing_stop': return 'trailing'
    case 'time_stop': return 'time stop'
    case '': return 'terminal'
    default: return backendType.replace(/_/g, ' ')
  }
}

/** Fallback read when the catalog has no entry (older catalogs). */
export function readsFor(backendType: string): readonly string[] {
  switch (backendType) {
    case 'size':
    case 'stop':
      return ['@scalar']
    case 'trailing_stop':
    case 'time_stop':
      return []
    default:
      return ['@bool']
  }
}

function isAttrSet(v: unknown): boolean {
  return typeof v === 'string' && v !== ''
}

/**
 * The type-slot note (S32b). `groupDirection` is the direction of the
 * Output Group the terminal sits in, or null outside a group.
 */
export function terminalNote(backendType: string, params: Record<string, unknown>, groupDirection: string | null): string | undefined {
  if ((backendType === 'entry' || backendType === 'exit') && groupDirection === 'regime_switch') {
    // No stored side reads long, as the server does (UX-14).
    return params.side === 'short' ? 'short' : 'long'
  }
  if (SIDE_TERMINALS.has(backendType) && groupDirection === 'regime_switch' && (params.side === 'long' || params.side === 'short')) {
    return params.side
  }
  if (backendType === 'trailing_stop' && params.activate_on_profit === true) {
    const p = typeof params.activate_pct === 'number' ? params.activate_pct : Number(params.activate_pct)
    return Number.isFinite(p) ? `>+${p.toFixed(1)} %` : undefined
  }
  if ((backendType === 'size' || backendType === 'stop') && !isAttrSet(params.value) && params.constant !== undefined && params.constant !== null) {
    return 'const'
  }
  return undefined
}

/** Terminals that take a `side` in a `regime_switch` group (entry/exit; stop, size, trailing and time stop per direction). */
export const SIDE_TERMINALS: ReadonlySet<string> = new Set(['entry', 'exit', 'stop', 'size', 'trailing_stop', 'time_stop'])

/**
 * The non-attr params shown as rows: everything but the attr reads (BaseNode
 * shows those as chips), and `side` outside a `regime_switch` group. Inside
 * one, a terminal whose catalog has a `side` param shows the row even when
 * no side is stored yet (it reads `long`, as the server does). A size
 * or stop wired to an attribute hides its constant. The write param `out`
 * of a trailing stop or time stop (`@trail_value`, `@max_bars`) is not a row.
 */
export function terminalRowParams(
  backendType: string,
  params: Record<string, unknown>,
  attrParams: ReadonlySet<string>,
  groupDirection: string | null,
  catalogHasSide = false,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (groupDirection === 'regime_switch' && catalogHasSide && SIDE_TERMINALS.has(backendType) && !('side' in params)) {
    out.side = 'long'
  }
  for (const [k, v] of Object.entries(params)) {
    if (attrParams.has(k)) continue
    if (k === 'side' && groupDirection !== 'regime_switch') continue
    if (k === 'constant' && (backendType === 'size' || backendType === 'stop') && isAttrSet(params.value)) continue
    // activate_on_profit and activate_pct live in the Inspector (S32b).
    if (backendType === 'trailing_stop' && (k === 'activate_on_profit' || k === 'activate_pct')) continue
    if (k === 'out' && (backendType === 'trailing_stop' || backendType === 'time_stop')) continue
    out[k] = v
  }
  return out
}


/** Row views on terminal cards (S32b): the size constant reads as a percent, a zero stop as `none`. */
export function terminalRowViews(backendType: string): Readonly<Record<string, 'percent' | 'stop'>> | undefined {
  if (backendType === 'size') return { constant: 'percent' }
  if (backendType === 'stop') return { constant: 'stop' }
  return undefined
}
