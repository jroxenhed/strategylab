/**
 * notices.ts — the notice banners under the graph toolbar (surface S07).
 *
 * A tiny store of its own: `pushNotice`, `dismissNotice`, `resolveNotice`.
 * A notice with the same `key` as an open one replaces it, so the same
 * message never shows twice. NoticeStack.tsx renders the list.
 */

import type { ReactNode } from 'react'
import { create } from 'zustand'

export type NoticeSeverity = 'info' | 'ok' | 'warn' | 'error'

export interface NoticeAction {
  label: string
  /** May return a promise; the banner shows a spinner while it runs. */
  run: () => void | Promise<unknown>
  testId?: string
  title?: string
}

export interface Notice {
  /** Same key = same banner (replaces the older one). */
  key: string
  severity: NoticeSeverity
  text: ReactNode
  actions?: NoticeAction[]
  /** Called when the user dismisses the banner with ✕. */
  onDismiss?: () => void
  dismissTitle?: string
  /**
   * ok and info banners close by themselves after 6 s unless sticky
   * (the draft prompt waits for an answer).
   */
  sticky?: boolean
  /** Close by itself after this many ms (overrides the 6 s default). */
  timeoutMs?: number
  /** Bumped on each push, so a replaced banner restarts its timer. */
  seq?: number
}

interface NoticeState {
  notices: Notice[]
  push(n: Notice): void
  dismiss(key: string): void
}

let seq = 0

export const useNoticeStore = create<NoticeState>()(set => ({
  notices: [],
  push(n) {
    seq += 1
    set(s => ({ notices: [{ ...n, seq }, ...s.notices.filter(o => o.key !== n.key)] }))
  },
  dismiss(key) {
    set(s => ({ notices: s.notices.filter(o => o.key !== key) }))
  },
}))

export function pushNotice(n: Notice): void {
  useNoticeStore.getState().push(n)
}

/** Close a banner because the user said so. */
export function dismissNotice(key: string): void {
  useNoticeStore.getState().dismiss(key)
}

/** Close a banner because the code resolved what it was about. */
export function resolveNotice(key: string): void {
  useNoticeStore.getState().dismiss(key)
}

/** Remove every notice (tests). */
export function clearNotices(): void {
  useNoticeStore.setState({ notices: [] })
}
