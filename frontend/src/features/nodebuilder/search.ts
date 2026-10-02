/**
 * search.ts — Fuzzy search algorithm for the Tab-menu node catalog (Unit 6).
 *
 * Score tiers (descending):
 *   1. Exact prefix       → "r" → "RSI"               2000 + bonus
 *   2a. All-initials      → "cb" → "Crosses Below"     1500 - penalty
 *   2b. Partial-initials  → "c" → "Crosses" partial    1200 - penalty
 *   3. Word-start         → "cross" → "Crosses Below"  900  - position
 *   4. Substring          → "rosses" → "Crosses Below" 500  - position
 *   5. Subsequence        → "crsblw" → "Crosses Below" 300  - position
 *   6. Category match     → "logic" → logic nodes      80
 *   7. Description match  → "boolean" → comparison     40
 *
 * Attribute search (W6, S43): `@volume` matches only the entries that read
 * or write `@volume` (catalog `reads` / `writes`, or an asset's interface).
 * `volume` without the `@` gives the same attribute matches first (tier 0,
 * 3000), then the usual name and description matches after them. The
 * result says which attribute matched and whether it is read or written,
 * so the menu can show the chip.
 */

import type { NodeCatalogEntry } from './catalog'

/**
 * What search needs from an entry. Catalog entries fit as they are; an
 * asset row passes its own `name` (a unique key), a `label` to show and
 * search, and its interface as `reads` / `writes`.
 */
export interface SearchEntry {
  name: string
  cat: string
  desc: string
  reads: readonly string[]
  writes: readonly string[]
  /** The text shown and searched; default friendlyName(name). */
  label?: string
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface MatchResult {
  name: string
  cat: string
  score: number
  /** Character positions in the *friendly* name that matched. */
  matchedIndices: number[]
  /** The attribute that matched (attribute search), with read or write. */
  attr?: AttrMatch
}

/** An attribute an entry reads or writes that the query named. */
export interface AttrMatch {
  /** With the @ sigil, e.g. "@volume". */
  name: string
  kind: 'read' | 'write'
}

/** Score of an attribute match: above every name tier. */
export const ATTR_MATCH_SCORE = 3000

const ATTR_BODY = /^[a-z_][a-z0-9_]{0,63}$/

/**
 * The attribute a query names, with the sigil: "@volume" for "@volume" or
 * "volume". Null when the text cannot be an attribute name.
 */
export function attrOfQuery(query: string): string | null {
  const q = query.trim().toLowerCase()
  const body = q.startsWith('@') ? q.slice(1) : q
  return ATTR_BODY.test(body) ? `@${body}` : null
}

/**
 * Port placeholder names the catalog uses for generic inputs (`@a` of a
 * comparison, `@bool` of a logic node). A bare word never means these.
 */
const PLACEHOLDER_ATTRS: ReadonlySet<string> = new Set(['@a', '@b', '@bool', '@float', '@int'])
/** Shortest bare word (no `@`) that counts as an attribute name. */
const BARE_ATTR_MIN = 3

/**
 * The attribute a typed query asks for in the Tab menu ranking. With `@`
 * any name counts; a bare word only when it is at least 3 characters and
 * not a generic port placeholder, so `b` + Enter still places Below, not
 * every node that reads `@b` (FE-04).
 */
export function rankingAttrOf(query: string): string | null {
  const attr = attrOfQuery(query)
  if (!attr || isAttrOnlyQuery(query)) return attr
  if (attr.length - 1 < BARE_ATTR_MIN || PLACEHOLDER_ATTRS.has(attr)) return null
  return attr
}

/** True when the query starts with `@`: only attribute matches count. */
export function isAttrOnlyQuery(query: string): boolean {
  return query.trim().startsWith('@')
}

/** Whether `entry` writes or reads `attr` (writes win when it does both). */
export function attrMatch(entry: Pick<SearchEntry, 'reads' | 'writes'>, attr: string): AttrMatch | null {
  const want = attr.toLowerCase()
  if (entry.writes.some(w => w.toLowerCase() === want)) return { name: attr, kind: 'write' }
  if (entry.reads.some(r => r.toLowerCase() === want)) return { name: attr, kind: 'read' }
  return null
}

/** The text an entry shows and is searched by. */
export function entryLabel(entry: Pick<SearchEntry, 'name' | 'label'>): string {
  return entry.label ?? friendlyName(entry.name)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Convert snake_case (or hyphen-case) to Title Case, suitable for display.
 * "crosses_below" → "Crosses Below"
 * "kalman-filter"  → "Kalman Filter"
 * "rsi"            → "RSI"    (short upper-case names stay upper-case)
 */
export function friendlyName(name: string): string {
  const parts = name.split(/[_-]/)
  return parts
    .map(w => (w.length <= 3 ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(' ')
}

/**
 * Split a friendly name into its component words.
 * "Crosses Below" → ["crosses", "below"]
 * Word separators: space, hyphen (already absorbed by friendlyName).
 */
function words(friendly: string): string[] {
  return friendly.toLowerCase().split(/\s+/).filter(Boolean)
}

// ---------------------------------------------------------------------------
// Core matcher
// ---------------------------------------------------------------------------

/**
 * Try to match `query` against `entry`.
 * Returns null if there is no match at any tier.
 */
export function fuzzyMatch(query: string, entry: SearchEntry | NodeCatalogEntry): MatchResult | null {
  if (!query) {
    // Empty query — include everything with score 0
    return {
      name: entry.name,
      cat: entry.cat,
      score: 0,
      matchedIndices: [],
    }
  }

  const q = query.toLowerCase()
  const friendly = entryLabel(entry)
  const friendlyLow = friendly.toLowerCase()

  // ── Tier 1: Exact prefix ──────────────────────────────────────────────────
  if (friendlyLow.startsWith(q)) {
    return {
      name: entry.name,
      cat: entry.cat,
      score: 2000 - friendlyLow.length,       // shorter name = higher score
      matchedIndices: Array.from({ length: q.length }, (_, i) => i),
    }
  }

  // ── Tier 2: Multi-word initials ───────────────────────────────────────────
  const ws = words(friendly)
  const initials = ws.map(w => w[0]).join('')  // "crosses below" → "cb"

  if (initials.startsWith(q)) {
    // All query chars match initials (in order, prefix of initials string)
    // Collect the positions of each initial in friendlyLow
    const matchPos: number[] = []
    let cursor = 0
    for (let k = 0; k < q.length; k++) {
      // k-th word's first character position in friendlyLow
      let wordStart = 0
      for (let wi = 0; wi < k; wi++) {
        wordStart += ws[wi].length + 1  // +1 for space
      }
      matchPos.push(wordStart)
      cursor = wordStart + 1
      void cursor
    }
    return {
      name: entry.name,
      cat: entry.cat,
      score: 1500 - (ws.length - q.length) * 5,
      matchedIndices: matchPos,
    }
  }

  // Partial initials — query is a prefix of a subsequence of initials
  if (initials.includes(q)) {
    const idx = initials.indexOf(q)
    const matchPos: number[] = []
    for (let k = idx; k < idx + q.length; k++) {
      let wordStart = 0
      for (let wi = 0; wi < k; wi++) {
        wordStart += ws[wi].length + 1
      }
      matchPos.push(wordStart)
    }
    return {
      name: entry.name,
      cat: entry.cat,
      score: 1200 - idx * 10,
      matchedIndices: matchPos,
    }
  }

  // ── Tier 3: Word-start match ──────────────────────────────────────────────
  {
    let bestWordStart: { pos: number; len: number } | null = null
    let cursorInFriendly = 0
    for (const w of ws) {
      if (w.startsWith(q)) {
        bestWordStart = { pos: cursorInFriendly, len: q.length }
        break
      }
      cursorInFriendly += w.length + 1
    }
    if (bestWordStart) {
      const { pos } = bestWordStart
      return {
        name: entry.name,
        cat: entry.cat,
        score: 900 - pos,
        matchedIndices: Array.from({ length: q.length }, (_, i) => pos + i),
      }
    }
  }

  // ── Tier 4: Substring match ───────────────────────────────────────────────
  const subIdx = friendlyLow.indexOf(q)
  if (subIdx !== -1) {
    return {
      name: entry.name,
      cat: entry.cat,
      score: 500 - subIdx,
      matchedIndices: Array.from({ length: q.length }, (_, i) => subIdx + i),
    }
  }

  // ── Tier 5: Subsequence match ─────────────────────────────────────────────
  {
    const positions: number[] = []
    let qi = 0
    for (let ci = 0; ci < friendlyLow.length && qi < q.length; ci++) {
      if (friendlyLow[ci] === q[qi]) {
        positions.push(ci)
        qi++
      }
    }
    if (qi === q.length) {
      return {
        name: entry.name,
        cat: entry.cat,
        score: 300 - positions[0],
        matchedIndices: positions,
      }
    }
  }

  // ── Tier 6: Category name match ───────────────────────────────────────────
  if (entry.cat.toLowerCase().includes(q)) {
    return {
      name: entry.name,
      cat: entry.cat,
      score: 80,
      matchedIndices: [],
    }
  }

  // ── Tier 7: Description match ─────────────────────────────────────────────
  if (entry.desc.toLowerCase().includes(q)) {
    return {
      name: entry.name,
      cat: entry.cat,
      score: 40,
      matchedIndices: [],
    }
  }

  return null
}

/**
 * Run fuzzyMatch against every entry in the catalog and return matches
 * in descending score order.  Entries with score 0 (empty query) are sorted
 * alphabetically by name.
 */
export function rankCatalog(query: string, catalog: readonly (SearchEntry | NodeCatalogEntry)[]): MatchResult[] {
  const results: MatchResult[] = []
  const attr = query ? rankingAttrOf(query) : null
  const attrOnly = isAttrOnlyQuery(query)
  for (const entry of catalog) {
    // Tier 0: the entry reads or writes the named attribute.
    const hit = attr ? attrMatch(entry, attr) : null
    if (hit) {
      results.push({ name: entry.name, cat: entry.cat, score: ATTR_MATCH_SCORE, matchedIndices: [], attr: hit })
      continue
    }
    // `@volume` asks for attribute matches only.
    if (attrOnly) continue
    const m = fuzzyMatch(query, entry)
    if (m !== null) {
      results.push(m)
    }
  }

  results.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score
    return a.name.localeCompare(b.name)
  })

  return results
}
