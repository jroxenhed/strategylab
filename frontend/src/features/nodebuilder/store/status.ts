/**
 * Status slice of the node builder store (W3 pre-step 3.0): what the status
 * bar (S20) shows that no other slice holds.
 *
 * - `flash`: a short message a command sets ("Select a node first"). The
 *   status bar shows it for 2 s; `seq` tells a repeat of the same text apart.
 * - `cook`: the state of the last cook. NodeBuilder writes it for the
 *   backtest cook today; W4 adds the preview cook (foundation amendment A4).
 *
 * Nothing here is part of undo.
 */

import type { StateCreator } from 'zustand'
import type { NodeBuilderState } from '../store'

/** The cook's lifecycle, in the status bar's words (S20). */
export type CookPhase = 'idle' | 'cooking' | 'cooked' | 'cancelled' | 'failed'

export interface CookStatus {
  phase: CookPhase
  /** `backtest` (Run backtest, Cmd+Enter) or `preview` (auto cook, W4). */
  kind: 'backtest' | 'preview'
  /** Date.now() when the cook started, or null. */
  startedAt: number | null
  /** Date.now() when the cook ended (cooked, cancelled or failed), or null. */
  endedAt: number | null
  /** True when the shown result answers an older graph or request. */
  stale: boolean
  /** The node a failed cook names, if the server said which one. */
  failedNodeId: string | null
}

export const IDLE_COOK: CookStatus = {
  phase: 'idle',
  kind: 'backtest',
  startedAt: null,
  endedAt: null,
  stale: false,
  failedNodeId: null,
}

export interface StatusSlice {
  /** The current flash message, or null. */
  flash: { text: string; seq: number } | null
  /** Show a short message in the status bar. */
  showFlash(text: string): void
  cook: CookStatus
  /** Merge fields into the cook status. Writes nothing when no field changes. */
  setCook(patch: Partial<CookStatus>): void
}

export const createStatusSlice: StateCreator<NodeBuilderState, [], [], StatusSlice> = (set, get) => ({
  flash: null,
  showFlash(text) {
    const prev = get().flash
    set({ flash: { text, seq: (prev?.seq ?? 0) + 1 } })
  },

  cook: IDLE_COOK,
  setCook(patch) {
    const cook = get().cook
    const keys = Object.keys(patch) as Array<keyof CookStatus>
    if (keys.every(k => cook[k] === patch[k])) return
    set({ cook: { ...cook, ...patch } })
  },
})
