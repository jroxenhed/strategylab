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
 */

import type { NodeCatalogEntry } from './catalog'

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface MatchResult {
  name: string
  cat: string
  score: number
  /** Character positions in the *friendly* name that matched. */
  matchedIndices: number[]
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
export function fuzzyMatch(query: string, entry: NodeCatalogEntry): MatchResult | null {
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
  const friendly = friendlyName(entry.name)
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
export function rankCatalog(query: string, catalog: readonly NodeCatalogEntry[]): MatchResult[] {
  const results: MatchResult[] = []
  for (const entry of catalog) {
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
