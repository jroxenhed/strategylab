/**
 * Live data for the status bar (F435 W3 item 3.H, spec S20) that does not
 * belong in the store, plus its text helpers.
 *
 * - Cursor: plugins/pointerTracker.ts writes the text straight into the
 *   cursor segment's DOM node (`writeCursor`), so pointer moves never cause
 *   a React render.
 * - Zoom: the tracker passes the zoom on at most every 100 ms; the zoom
 *   segment re-renders only when the whole percent changes.
 * - Last save: when the store records a save (a new rev or a first id for
 *   the same load, ending clean), the time is kept here, so "saved 2 min
 *   ago" works whether or not the bar was mounted at that moment.
 */

import type { Graph } from '../../api/nodebuilder'
import { useNodeBuilderStore } from './store'

// ── Cursor ──────────────────────────────────────────────────────────────────

/** Text of the cursor segment when the pointer is not over the canvas. */
export const NO_CURSOR = '—, —'

let cursorEl: HTMLElement | null = null
let cursorText = NO_CURSOR

/** Write the cursor segment's text directly (no React render). */
export function writeCursor(text: string): void {
  if (text === cursorText) return
  cursorText = text
  if (cursorEl) cursorEl.textContent = text
}

/** The cursor segment's ref callback. */
export function bindCursorEl(el: HTMLElement | null): void {
  cursorEl = el
  if (el) el.textContent = cursorText
}

// ── Zoom ────────────────────────────────────────────────────────────────────

// Zoom in whole percent, or null before the canvas has reported one.
let liveZoom: number | null = null
const zoomListeners = new Set<() => void>()

/** Set the zoom the status bar shows (React Flow zoom, 1 = 100%). */
export function setLiveZoom(zoom: number): void {
  if (!Number.isFinite(zoom)) return
  const pct = Math.round(zoom * 100)
  if (pct === liveZoom) return
  liveZoom = pct
  for (const l of [...zoomListeners]) l()
}

export function subscribeZoom(l: () => void): () => void {
  zoomListeners.add(l)
  return () => { zoomListeners.delete(l) }
}

export function getLiveZoom(): number | null {
  return liveZoom
}

// ── Last save ───────────────────────────────────────────────────────────────

let lastSave: { id: string; at: number } | null = null
const saveListeners = new Set<() => void>()

useNodeBuilderStore.subscribe((s, prev) => {
  const id = s.graphMeta?.id ?? null
  const rev = s.graphMeta?.rev ?? 0
  if (!id || s.dirty || s.layoutEpoch !== prev.layoutEpoch) return
  if (id === (prev.graphMeta?.id ?? null) && rev === (prev.graphMeta?.rev ?? 0)) return
  lastSave = { id, at: Date.now() }
  for (const l of [...saveListeners]) l()
})

export function subscribeSave(l: () => void): () => void {
  saveListeners.add(l)
  return () => { saveListeners.delete(l) }
}

/** When the graph with this id was last saved in this page, or null. */
export function lastSaveOf(id: string | null): number | null {
  return id && lastSave?.id === id ? lastSave.at : null
}

/** Tests: forget the live cursor, zoom and last save. */
export function resetStatusChannels(): void {
  liveZoom = null
  lastSave = null
  cursorText = NO_CURSOR
  if (cursorEl) cursorEl.textContent = NO_CURSOR
  for (const l of [...zoomListeners]) l()
  for (const l of [...saveListeners]) l()
}

// ── Text ────────────────────────────────────────────────────────────────────

export function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`
}

/**
 * The selection segment's text (S20): `rsi`, `3 nodes`,
 * `wire spread_z → below_entry`, `2 nodes · 1 wire`, or `no selection`.
 */
export function selectionText(
  graph: Graph | null,
  nodeIds: readonly string[],
  wireIds: readonly string[],
  annotationIds: readonly string[] = [],
): string {
  const name = (id: string) => graph?.nodes[id]?.name ?? id
  const only = nodeIds.length + wireIds.length + annotationIds.length === 1
  if (nodeIds.length === 1 && only) return name(nodeIds[0])
  if (wireIds.length === 1 && only) {
    const w = graph?.wires.find(x => x.id === wireIds[0])
    return w ? `wire ${name(w.from)} → ${name(w.to)}` : 'wire'
  }
  const parts: string[] = []
  if (nodeIds.length > 0) parts.push(plural(nodeIds.length, 'node'))
  if (wireIds.length > 0) parts.push(plural(wireIds.length, 'wire'))
  if (annotationIds.length > 0) {
    const boxIds = new Set(graph?.annotations.boxes.map(b => b.id) ?? [])
    const boxes = annotationIds.filter(id => boxIds.has(id)).length
    const notes = annotationIds.length - boxes
    if (boxes > 0) parts.push(plural(boxes, 'box', 'boxes'))
    if (notes > 0) parts.push(plural(notes, 'note'))
  }
  return parts.length > 0 ? parts.join(' · ') : 'no selection'
}

/**
 * The selection slot's text (S37): the selection, and inside a network
 * `in /long_leg/regime` (alone when nothing is selected).
 */
export function selectionSlotText(selection: string, where: string): string {
  if (!where) return selection
  return selection === 'no selection' ? where : `${selection} · ${where}`
}

/** The short id the graph segment shows: the last 4 hex characters. */
export function shortGraphId(id: string): string {
  const hex = id.replace(/[^0-9a-f]/gi, '')
  return (hex || id).slice(-4)
}

/** `HH:MM:SS`, local time. */
export function clockText(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
