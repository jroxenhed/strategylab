/**
 * The Data Sheet's "only rows where ..." filter (S25 header item 3).
 *
 * The text is parsed here, in the browser, into the inspect contract's
 * `filter`:
 *   `@x`         -> is_true
 *   `!@x`        -> is_false
 *   `@x > 30`    -> gt
 *   `@x < 30`    -> lt
 *   `@x is set`  -> not_nan
 * Empty text means no filter.
 */

import type { InspectFilter } from '../../../api/nodebuilderInspect'

export const FILTER_HELP = 'Use @attr, !@attr, @attr > n, @attr < n, or @attr is set'

export type FilterParse =
  | { ok: true; filter: InspectFilter | null }
  | { ok: false }

const NAME = '@[A-Za-z_][A-Za-z0-9_]*'
const NUM = '[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?'
const RE_TRUE = new RegExp(`^(${NAME})$`)
const RE_FALSE = new RegExp(`^!\\s*(${NAME})$`)
const RE_CMP = new RegExp(`^(${NAME})\\s*([<>])\\s*(${NUM})$`)
const RE_SET = new RegExp(`^(${NAME})\\s+is\\s+set$`, 'i')

/** Parse filter text. Invalid text gives `{ ok: false }`. */
export function parseSheetFilter(text: string): FilterParse {
  const s = text.trim()
  if (!s) return { ok: true, filter: null }
  let m = RE_FALSE.exec(s)
  if (m) return { ok: true, filter: { attr: m[1], op: 'is_false', value: null } }
  m = RE_SET.exec(s)
  if (m) return { ok: true, filter: { attr: m[1], op: 'not_nan', value: null } }
  m = RE_CMP.exec(s)
  if (m) {
    const value = Number(m[3])
    if (!Number.isFinite(value)) return { ok: false }
    return { ok: true, filter: { attr: m[1], op: m[2] === '>' ? 'gt' : 'lt', value } }
  }
  m = RE_TRUE.exec(s)
  if (m) return { ok: true, filter: { attr: m[1], op: 'is_true', value: null } }
  return { ok: false }
}

/** The text that parses back into `filter` (for header menu actions). */
export function filterText(filter: InspectFilter | null): string {
  if (!filter) return ''
  switch (filter.op) {
    case 'is_true': return filter.attr
    case 'is_false': return `!${filter.attr}`
    case 'not_nan': return `${filter.attr} is set`
    case 'gt': return `${filter.attr} > ${filter.value ?? 0}`
    case 'lt': return `${filter.attr} < ${filter.value ?? 0}`
  }
}

/** Same filter, field by field. */
export function sameFilter(a: InspectFilter | null, b: InspectFilter | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  return a.attr === b.attr && a.op === b.op && (a.value ?? null) === (b.value ?? null)
}

export interface FilterSuggestion {
  /** Text shown in the list. */
  label: string
  /** The whole filter text after picking it. */
  text: string
}

/**
 * Autocomplete rows for the filter input (max 6): attribute names while the
 * name is being typed, then the operators once a full name is there.
 */
export function filterSuggestions(text: string, attrs: readonly string[], max = 6): FilterSuggestion[] {
  const s = text.trimStart()
  const neg = s.startsWith('!')
  const body = neg ? s.slice(1).trimStart() : s
  const nameMatch = /^@?[A-Za-z0-9_]*$/.exec(body)
  if (nameMatch) {
    const typed = body.startsWith('@') ? body : `@${body}`
    const exact = attrs.includes(typed)
    if (exact && !neg) {
      return [
        { label: `${typed}`, text: typed },
        { label: `!${typed}`, text: `!${typed}` },
        { label: `${typed} > …`, text: `${typed} > ` },
        { label: `${typed} < …`, text: `${typed} < ` },
        { label: `${typed} is set`, text: `${typed} is set` },
      ].slice(0, max)
    }
    const lower = typed.toLowerCase()
    return attrs
      .filter(a => a.toLowerCase().startsWith(lower) && a !== typed)
      .slice(0, max)
      .map(a => ({ label: neg ? `!${a}` : a, text: neg ? `!${a}` : a }))
  }
  const afterName = new RegExp(`^(${NAME})\\s+$`).exec(body)
  if (afterName && !neg && attrs.includes(afterName[1])) {
    const a = afterName[1]
    return [
      { label: '> …', text: `${a} > ` },
      { label: '< …', text: `${a} < ` },
      { label: 'is set', text: `${a} is set` },
    ]
  }
  return []
}
