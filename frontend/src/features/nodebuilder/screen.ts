/**
 * The mounted canvases and the graph on screen (W3 review fixes EA-9,
 * EA-14, FC-4).
 *
 * - Every mounted canvas is on a stack. The active canvas (the one commands
 *   run against from a button, a menu or a panel) is the top: the canvas
 *   mounted last, or the one the user last pressed in (`activateCanvas`).
 *   When it unmounts, the one below becomes active again.
 * - Each canvas publishes the graph it draws and whether it is editable
 *   (`publishScreenGraph`, from a layout effect). In the read-only view that
 *   graph is not in the store, so panels (the Inspector, the status bar, a
 *   W4 Data Sheet, a W6 breadcrumb) read it here. `useScreenGraph()` is
 *   reactive: a slot component re-renders when a new graph is on screen,
 *   in the same commit the canvas draws it.
 *
 * This module has no imports from the store or the command registry, so any
 * module can use it without an import cycle.
 */

import { useSyncExternalStore } from 'react'
import type { Graph } from '../../api/nodebuilder'
import type { CanvasCtx } from './canvasPlugins'

export interface ScreenGraph {
  /** The graph the active canvas draws, or null when no canvas is mounted. */
  graph: Graph | null
  /** True when that graph can be edited. */
  editable: boolean
}

const NONE: ScreenGraph = Object.freeze({ graph: null, editable: false })

interface Entry {
  ctx: CanvasCtx
  /** Null until the canvas publishes (then `ctx.graph()` is used). */
  screen: ScreenGraph | null
}

const stack: Entry[] = []
let snapshot: ScreenGraph = NONE
const listeners = new Set<() => void>()

function screenOf(e: Entry | undefined): ScreenGraph {
  if (!e) return NONE
  if (e.screen) return e.screen
  let graph: Graph | null = null
  try {
    graph = e.ctx.graph() ?? null
  } catch {
    graph = null
  }
  return graph ? { graph, editable: false } : NONE
}

function refresh(): void {
  const top = stack[stack.length - 1]
  const next = screenOf(top)
  if (next === snapshot || (next.graph === snapshot.graph && next.editable === snapshot.editable)) return
  snapshot = next
  for (const l of [...listeners]) l()
}

function indexOf(ctx: CanvasCtx): number {
  return stack.findIndex(e => e.ctx === ctx)
}

/** Canvas only: add a mounted canvas on top (it becomes active). Returns the unmount function. */
export function mountCanvas(ctx: CanvasCtx): () => void {
  const i = indexOf(ctx)
  const entry = i >= 0 ? stack.splice(i, 1)[0] : { ctx, screen: null }
  stack.push(entry)
  refresh()
  return () => {
    const j = indexOf(ctx)
    if (j >= 0) stack.splice(j, 1)
    refresh()
  }
}

/** Make a mounted canvas the active one (a press inside it). No-op when it is not mounted. */
export function activateCanvas(ctx: CanvasCtx): void {
  const i = indexOf(ctx)
  if (i < 0 || i === stack.length - 1) return
  stack.push(stack.splice(i, 1)[0])
  refresh()
}

/**
 * Set the active canvas directly (tests, older callers). A ctx goes on top
 * of the stack; null clears every canvas.
 */
export function setActiveCanvas(ctx: CanvasCtx | null): void {
  if (ctx === null) {
    stack.length = 0
    refresh()
    return
  }
  mountCanvas(ctx)
}

/** The active canvas, or null when none is mounted. */
export function getActiveCanvas(): CanvasCtx | null {
  return stack[stack.length - 1]?.ctx ?? null
}

/** Canvas only: the graph this canvas draws now (call from a layout effect). */
export function publishScreenGraph(ctx: CanvasCtx, graph: Graph, editable: boolean): void {
  const e = stack[indexOf(ctx)]
  if (!e) return
  if (e.screen && e.screen.graph === graph && e.screen.editable === editable) return
  e.screen = { graph, editable }
  refresh()
}

/** The graph on screen (not reactive; for event handlers). */
export function getScreenGraph(): ScreenGraph {
  return snapshot
}

/** Call `l` whenever the graph on screen changes (not reactive; for modules). */
export function subscribeScreenGraph(l: () => void): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}
const subscribe = subscribeScreenGraph

/** The graph on screen and whether it is editable. Re-renders when either changes. */
export function useScreenGraph(): ScreenGraph {
  return useSyncExternalStore(subscribe, getScreenGraph, getScreenGraph)
}
