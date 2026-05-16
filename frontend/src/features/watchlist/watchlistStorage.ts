/**
 * watchlistStorage.ts — F247b
 *
 * Schema-aware persistence for the watchlist.
 *
 * Storage shape (new):
 *   { groups: WatchlistGroup[], ungrouped: string[] }
 *
 * Legacy shape (migrated on first load):
 *   string[]   (flat array of symbols — from F247a)
 *
 * Invariant: every ticker symbol is unique across ALL groups + ungrouped
 * (case-insensitive). Enforced by dropDuplicate() on every mutation.
 */

export interface WatchlistGroup {
  id: string
  name: string
  tickers: string[]
  collapsed: boolean
}

export interface WatchlistState {
  groups: WatchlistGroup[]
  ungrouped: string[]
}

export interface LoadResult {
  state: WatchlistState
  wasCorrupt: boolean
}

export const WATCHLIST_KEY = 'watchlist-symbols'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Generate a stable random ID for a new group. */
export function genGroupId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
}

/** Empty state sentinel. */
export function emptyState(): WatchlistState {
  return { groups: [], ungrouped: [] }
}

/**
 * dropDuplicate — dedup across groups + ungrouped after a mutation.
 *
 * Rule: the TARGET position wins.
 *   - `targetGroupId === null`  → ungrouped wins
 *   - `targetGroupId === <id>` → that group wins
 *
 * All other occurrences of the same ticker (case-insensitive) are removed.
 *
 * When called without a target (e.g. initial load normalization), the FIRST
 * occurrence in document-order wins (ungrouped first, then groups in order).
 */
export function dropDuplicate(
  state: WatchlistState,
  winnerTicker?: string,
  winnerGroupId?: string | null,
): WatchlistState {
  const seen = new Set<string>()

  // Helper: process an array, keeping only unseen symbols.
  // If winnerTicker is specified, skip it in source unless this is the winning bucket.
  const filterList = (
    list: string[],
    isWinner: boolean,
  ): string[] => {
    const result: string[] = []
    for (const t of list) {
      const key = t.toUpperCase()
      if (winnerTicker && key === winnerTicker.toUpperCase()) {
        if (isWinner) {
          // Only add once even in the winner bucket
          if (!seen.has(key)) { seen.add(key); result.push(t) }
        }
        // else: skip — loser occurrence
        continue
      }
      if (!seen.has(key)) { seen.add(key); result.push(t) }
    }
    return result
  }

  // Order: ungrouped first, then groups in array order.
  const ungroupedIsWinner = winnerGroupId === null || winnerGroupId === undefined
  const ungrouped = filterList(state.ungrouped, ungroupedIsWinner)

  const groups: WatchlistGroup[] = state.groups.map(g => ({
    ...g,
    tickers: filterList(g.tickers, g.id === winnerGroupId),
  }))

  return { groups, ungrouped }
}

// ---------------------------------------------------------------------------
// Migration
// ---------------------------------------------------------------------------

/**
 * migrateLegacy — converts the old flat string[] watchlist to the new schema.
 * All tickers land in `ungrouped`. Migration is silent (no toast).
 */
export function migrateLegacy(legacy: string[]): WatchlistState {
  return {
    groups: [],
    ungrouped: legacy.map(s => s.toUpperCase()),
  }
}

// ---------------------------------------------------------------------------
// Load / Save
// ---------------------------------------------------------------------------

/**
 * loadWatchlist — reads from localStorage, migrates if needed, returns state.
 *
 * Return object:
 *   { state: WatchlistState, wasCorrupt: boolean }
 *
 * `wasCorrupt` is true when JSON.parse failed or the value was unrecognizable.
 * On corruption the returned state is emptyState().
 */
export function loadWatchlist(): LoadResult {
  const raw = localStorage.getItem(WATCHLIST_KEY)
  if (!raw) {
    return { state: emptyState(), wasCorrupt: false }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    // Corrupt JSON — reset and surface toast
    return { state: emptyState(), wasCorrupt: true }
  }

  // Old format: top-level array of strings
  if (Array.isArray(parsed)) {
    if (parsed.every(x => typeof x === 'string')) {
      const migrated = migrateLegacy(parsed as string[])
      // Persist migrated shape immediately
      saveWatchlist(migrated)
      return { state: migrated, wasCorrupt: false }
    }
    // Array but not strings — treat as corrupt
    return { state: emptyState(), wasCorrupt: true }
  }

  // New format: object with groups + ungrouped
  if (
    parsed !== null &&
    typeof parsed === 'object' &&
    !Array.isArray(parsed) &&
    'ungrouped' in parsed &&
    'groups' in parsed &&
    Array.isArray((parsed as Record<string, unknown>).ungrouped) &&
    Array.isArray((parsed as Record<string, unknown>).groups)
  ) {
    const raw = parsed as { groups: unknown[]; ungrouped: unknown[] }

    // Validate groups shape
    const groups: WatchlistGroup[] = []
    for (const g of raw.groups) {
      if (
        g !== null &&
        typeof g === 'object' &&
        typeof (g as Record<string, unknown>).id === 'string' &&
        typeof (g as Record<string, unknown>).name === 'string' &&
        Array.isArray((g as Record<string, unknown>).tickers) &&
        typeof (g as Record<string, unknown>).collapsed === 'boolean'
      ) {
        const grp = g as { id: string; name: string; tickers: unknown[]; collapsed: boolean }
        groups.push({
          id: grp.id,
          name: grp.name,
          tickers: grp.tickers.filter((t): t is string => typeof t === 'string'),
          collapsed: grp.collapsed,
        })
      }
    }

    const ungrouped = (raw.ungrouped as unknown[]).filter(
      (t): t is string => typeof t === 'string'
    )

    const state = dropDuplicate({ groups, ungrouped })
    return { state, wasCorrupt: false }
  }

  // Unrecognizable format
  return { state: emptyState(), wasCorrupt: true }
}

/**
 * saveWatchlist — persists the current state to localStorage.
 */
export function saveWatchlist(state: WatchlistState): void {
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(state))
}
