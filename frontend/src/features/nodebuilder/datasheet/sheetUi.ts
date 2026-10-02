/**
 * The Data Sheet's own small store (S25): open or closed, height, follow or
 * pin, and the filter text. Kept outside the node builder store because none
 * of it is graph data or undo history.
 *
 * Persisted in `nb.sheet = {height, open, follow, filter}` (amendment A5).
 * Every storage read and write is wrapped in try/catch; the sheet works
 * without storage. The pinned target itself lives only for the session.
 *
 * The toolbar toggle and the `S` key (item 4.D) call `toggleSheet()`.
 */

import { create } from 'zustand'
import type { SheetTarget } from './target'

export const SHEET_STORAGE_KEY = 'nb.sheet'
export const SHEET_MIN_HEIGHT = 120
/** Largest share of the canvas column the sheet may take. */
export const SHEET_MAX_SHARE = 0.6

export type FollowMode = 'follow' | 'pinned'

/** Default height by screen width: 200 at 1280, 240 between, 280 at 2560. */
export function defaultSheetHeight(width: number = typeof window !== 'undefined' ? window.innerWidth : 1600): number {
  if (width <= 1280) return 200
  if (width >= 2560) return 280
  return 240
}

interface Persisted {
  height: number
  open: boolean
  follow: FollowMode
  filter: string
}

function readPersisted(): Partial<Persisted> {
  try {
    const raw = window.localStorage.getItem(SHEET_STORAGE_KEY)
    if (!raw) return {}
    const v = JSON.parse(raw) as Record<string, unknown>
    const out: Partial<Persisted> = {}
    if (typeof v.height === 'number' && Number.isFinite(v.height)) out.height = Math.max(SHEET_MIN_HEIGHT, v.height)
    if (typeof v.open === 'boolean') out.open = v.open
    if (v.follow === 'follow' || v.follow === 'pinned') out.follow = v.follow
    if (typeof v.filter === 'string') out.filter = v.filter
    return out
  } catch {
    return {}
  }
}

function writePersisted(p: Persisted): void {
  try {
    window.localStorage.setItem(SHEET_STORAGE_KEY, JSON.stringify(p))
  } catch {
    // Storage blocked or full: the sheet still works for this session.
  }
}

export interface SheetUiState {
  open: boolean
  height: number
  follow: FollowMode
  /** The pinned target (pin mode only). Not persisted. */
  pinned: SheetTarget | null
  /** The applied filter text ('' = none). */
  filter: string
  setOpen(open: boolean): void
  toggle(): void
  setHeight(h: number): void
  /** Pin `current` (or unpin with 'follow'). */
  setFollow(mode: FollowMode, current?: SheetTarget | null): void
  setFilter(text: string): void
}

function initial(): Pick<SheetUiState, 'open' | 'height' | 'follow' | 'pinned' | 'filter'> {
  const p = readPersisted()
  return {
    // S28: the sheet starts open (at the S25 default height) until the user closes it.
    open: p.open ?? true,
    height: p.height ?? defaultSheetHeight(),
    // A pin does not survive a reload, so a stored 'pinned' starts as follow.
    follow: 'follow',
    pinned: null,
    filter: p.filter ?? '',
  }
}

export const useSheetUi = create<SheetUiState>()((set, get) => ({
  ...initial(),
  setOpen(open) {
    if (get().open !== open) set({ open })
  },
  toggle() {
    set({ open: !get().open })
  },
  setHeight(h) {
    const height = Math.max(SHEET_MIN_HEIGHT, Math.round(h))
    if (get().height !== height) set({ height })
  },
  setFollow(mode, current = null) {
    if (mode === 'pinned' && current) set({ follow: 'pinned', pinned: current })
    else set({ follow: 'follow', pinned: null })
  },
  setFilter(text) {
    if (get().filter !== text) set({ filter: text })
  },
}))

useSheetUi.subscribe((s, prev) => {
  if (s.open === prev.open && s.height === prev.height && s.follow === prev.follow && s.filter === prev.filter) return
  writePersisted({ open: s.open, height: s.height, follow: s.follow, filter: s.filter })
})

/** Open or close the sheet (toolbar `▤`, key `S`). */
export function toggleSheet(): void {
  useSheetUi.getState().toggle()
}

/** Back to the stored (or default) state. Tests use it. */
export function resetSheetUi(): void {
  useSheetUi.setState(initial())
}
