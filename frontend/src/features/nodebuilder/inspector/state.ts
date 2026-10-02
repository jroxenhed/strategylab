/**
 * The Inspector's own UI state (specs S14, S15): open or closed, width,
 * which sections are expanded, and short-lived requests from elsewhere
 * (flash a param row, start a rename).
 *
 * This is panel state, not graph state: it is never part of undo and never
 * saved in the graph file. Width, open and the sections map persist per
 * browser in `localStorage['nb.inspector']` (A5). Every storage read and
 * write is wrapped in try/catch, so the panel works without storage.
 */

import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { registerGraphReconciler } from '../store/reconcile'

export const INSPECTOR_STORAGE_KEY = 'nb.inspector'
export const INSPECTOR_MIN_WIDTH = 240
export const INSPECTOR_MAX_WIDTH = 520
/** Width in overlay mode (app narrower than OVERLAY_BELOW). */
export const INSPECTOR_OVERLAY_WIDTH = 280
/** Below this app width the Inspector floats over the canvas (foundation 3.2). */
export const OVERLAY_BELOW = 1100
/** How long a flashed param row stays lit (S05). */
export const FLASH_MS = 800

/** Sections open by default. Ids not listed here start open. */
const DEFAULT_SECTIONS: Record<string, boolean> = {
  parameters: true,
  code: false,
  stream: true,
  diagnostics: true,
  notes: false,
}

/** Default width for an app this wide: 280 at 1280, 300 at 1600, 360 at 2560. */
export function defaultInspectorWidth(appWidth: number): number {
  if (appWidth >= 2560) return 360
  if (appWidth < 1440) return 280
  return 300
}

export function clampInspectorWidth(w: number): number {
  return Math.round(Math.min(INSPECTOR_MAX_WIDTH, Math.max(INSPECTOR_MIN_WIDTH, w)))
}

export interface InspectorUiState {
  /** Docked panel open (app at least OVERLAY_BELOW wide). Persisted. */
  open: boolean
  /**
   * Overlay panel open (app narrower than OVERLAY_BELOW). Session only, never
   * persisted, closed on load: dismissing the overlay must not close the
   * docked panel for the next wide window, and the overlay must not cover
   * the canvas on load (UX-08).
   */
  overlayOpen: boolean
  /** Chosen width in px, or null for the default for the app width. */
  width: number | null
  /** Section id to expanded. A missing id uses its default. */
  sections: Record<string, boolean>
  /** A param row to flash (diagnostics click). `seq` makes a repeat flash again. */
  flash: { nodeId: string; param: string | null; focus: boolean; seq: number } | null
  /** Start the inline rename of this node (F2). */
  renameRequest: { nodeId: string; seq: number } | null
}

interface Persisted {
  open?: unknown
  width?: unknown
  sections?: unknown
}

function readPersisted(): Pick<InspectorUiState, 'open' | 'width' | 'sections'> {
  const out = { open: true, width: null as number | null, sections: {} as Record<string, boolean> }
  try {
    const raw = globalThis.localStorage?.getItem(INSPECTOR_STORAGE_KEY)
    if (!raw) return out
    const p = JSON.parse(raw) as Persisted
    if (typeof p.open === 'boolean') out.open = p.open
    if (typeof p.width === 'number' && Number.isFinite(p.width)) out.width = clampInspectorWidth(p.width)
    if (p.sections && typeof p.sections === 'object') {
      for (const [k, v] of Object.entries(p.sections as Record<string, unknown>)) {
        if (typeof v === 'boolean') out.sections[k] = v
      }
    }
  } catch {
    // No storage, or a bad value: use the defaults.
  }
  return out
}

function writePersisted(s: InspectorUiState): void {
  try {
    globalThis.localStorage?.setItem(
      INSPECTOR_STORAGE_KEY,
      JSON.stringify({ width: s.width, open: s.open, sections: s.sections }),
    )
  } catch {
    // Storage full or blocked: the panel still works for this visit.
  }
}

export const useInspectorUi = create<InspectorUiState>()(() => ({
  ...readPersisted(),
  overlayOpen: false,
  flash: null,
  renameRequest: null,
}))

// EA-5: a flash or a rename request for a node the graph no longer has
// (undo, redo, a delete, a load) is dropped in the same step, so it cannot
// fire later on a node that reuses the id.
registerGraphReconciler((_s, next) => {
  const ui = useInspectorUi.getState()
  const gone = (id: string | undefined) => id !== undefined && !(next && id in next.nodes)
  const patch: Partial<InspectorUiState> = {}
  if (gone(ui.flash?.nodeId)) patch.flash = null
  if (gone(ui.renameRequest?.nodeId)) patch.renameRequest = null
  if (Object.keys(patch).length > 0) useInspectorUi.setState(patch)
})

function setPersisted(patch: Partial<InspectorUiState>): void {
  useInspectorUi.setState(patch)
  writePersisted(useInspectorUi.getState())
}

/** True when the section is expanded. */
export function isSectionOpen(sections: Record<string, boolean>, id: string): boolean {
  return sections[id] ?? DEFAULT_SECTIONS[id] ?? true
}

function appWidth(): number {
  return typeof window === 'undefined' ? 1600 : window.innerWidth
}

/** True when the app is narrow enough that the Inspector floats over the canvas. */
export function isOverlayMode(width: number = appWidth()): boolean {
  return width < OVERLAY_BELOW
}

/** The window width, following resizes. */
export function useAppWidth(): number {
  const [w, setW] = useState(appWidth)
  useEffect(() => {
    const on = () => setW(appWidth())
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return w
}

/** True when the panel is on screen in the current mode (docked `open` or session `overlayOpen`). */
export function isInspectorShown(s: Pick<InspectorUiState, 'open' | 'overlayOpen'> = useInspectorUi.getState(), overlay: boolean = isOverlayMode()): boolean {
  return overlay ? s.overlayOpen : s.open
}

/** Reactive `isInspectorShown`, following window resizes. */
export function useInspectorShown(): boolean {
  const overlay = isOverlayMode(useAppWidth())
  return useInspectorUi(s => (overlay ? s.overlayOpen : s.open))
}

/**
 * Open, close, or (no argument) toggle the panel. In overlay mode this is
 * the session `overlayOpen`; docked, the persisted `open`.
 */
export function toggleInspector(open?: boolean): void {
  const s = useInspectorUi.getState()
  if (isOverlayMode()) {
    const next = open ?? !s.overlayOpen
    if (next !== s.overlayOpen) useInspectorUi.setState({ overlayOpen: next })
    return
  }
  const next = open ?? !s.open
  if (next === s.open) return
  setPersisted({ open: next })
}

/** Set the width (clamped), or null to go back to the default. */
export function setInspectorWidth(width: number | null): void {
  setPersisted({ width: width === null ? null : clampInspectorWidth(width) })
}

/** Expand or collapse a section; with no `open`, toggle it. */
export function setSectionOpen(id: string, open?: boolean): void {
  const { sections } = useInspectorUi.getState()
  const next = open ?? !isSectionOpen(sections, id)
  if (sections[id] === next) return
  setPersisted({ sections: { ...sections, [id]: next } })
}

/**
 * Open the Inspector on its Diagnostics section, and flash the param row
 * the problem is about (S05 badge and popover row clicks). The caller
 * selects the node.
 */
export function openInspectorDiagnostics(nodeId: string, param?: string | null): void {
  const s = useInspectorUi.getState()
  const sections = isSectionOpen(s.sections, 'diagnostics') ? s.sections : { ...s.sections, diagnostics: true }
  const withParams = param && !isSectionOpen(sections, 'parameters') ? { ...sections, parameters: true } : sections
  if (withParams !== s.sections) setPersisted({ sections: withParams })
  toggleInspector(true)
  flashParam(nodeId, param ?? null)
}

/**
 * Flash a param row of a node (null: nothing to flash). With `focus`, the
 * row's field also gets keyboard focus (a click inside the Inspector; never
 * for a click on the canvas, which would steal the canvas keys).
 */
export function flashParam(nodeId: string, param: string | null, focus = false): void {
  const seq = (useInspectorUi.getState().flash?.seq ?? 0) + 1
  useInspectorUi.setState({ flash: { nodeId, param, focus, seq } })
}

/** Ask the Inspector to start renaming this node (opens the panel). */
export function requestRename(nodeId: string): void {
  const s = useInspectorUi.getState()
  toggleInspector(true)
  useInspectorUi.setState({ renameRequest: { nodeId, seq: (s.renameRequest?.seq ?? 0) + 1 } })
}

/** Tests only: back to the defaults, forgetting storage. */
export function resetInspectorUi(): void {
  try {
    globalThis.localStorage?.removeItem(INSPECTOR_STORAGE_KEY)
  } catch {
    // ignore
  }
  useInspectorUi.setState({ open: true, overlayOpen: false, width: null, sections: {}, flash: null, renameRequest: null })
}

/** Tests only: read storage again, as a fresh page load would. */
export function reloadInspectorUi(): void {
  useInspectorUi.setState({ ...readPersisted(), overlayOpen: false, flash: null, renameRequest: null })
}
