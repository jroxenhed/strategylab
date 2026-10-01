/**
 * Toolbar helps (F435 W3 item 3.H, spec S22 and amendment A1):
 *
 * - `HintBar`: one dim line in the free middle of the graph toolbar,
 *   "Drag node to move · Space+drag to pan · Wheel to zoom · Tab to add".
 *   Shown only when the toolbar is at least 1440px wide and the text fits;
 *   the hint box must be a flex item of the toolbar row (it takes the free
 *   middle);
 *   hidden while the empty-graph state is on screen; `✕` hides it for this
 *   browser (`nb.hint = 'off'`). GraphToolbar renders it in its middle.
 * - `ResetViewButton`: "Reset view" (key cap H) runs `view.frameAll`, the
 *   same command as H. Under 1440px it becomes the icon button `⌖`.
 *   Registered in `toolbarRight` at order 10 by plugins/pointerTracker.ts.
 */

import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { Button } from './ui/Button'
import { getActiveCanvas, runCommand } from './commands'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import './statusChrome.css'

/** The hint's text (S22, exact). */
export const HINT_TEXT = 'Drag node to move · Space+drag to pan · Wheel to zoom · Tab to add'

/** Toolbar width from which the hint shows and Reset view has its label. */
export const WIDE_TOOLBAR_PX = 1440

/** Storage key for a dismissed hint (A5). */
export const HINT_STORAGE_KEY = 'nb.hint'

function readHintOff(): boolean {
  try {
    return localStorage.getItem(HINT_STORAGE_KEY) === 'off'
  } catch {
    return false
  }
}

function writeHintOff(): void {
  try {
    localStorage.setItem(HINT_STORAGE_KEY, 'off')
  } catch {
    // The page works without storage; the hint just comes back next time.
  }
}

/**
 * True while the element `pick` returns (the toolbar) is at least
 * WIDE_TOOLBAR_PX wide. When it has no layout width (jsdom, not yet laid
 * out), the window width counts. Only the boolean is state, so a resize
 * (an Inspector handle drag, a window resize) re-renders the caller only
 * when it crosses the line (IP-3).
 */
function useWideToolbar(ref: RefObject<HTMLElement | null>, pick: (el: HTMLElement) => HTMLElement | null): boolean {
  const [wide, setWide] = useState(() => (typeof window !== 'undefined' ? window.innerWidth : WIDE_TOOLBAR_PX) >= WIDE_TOOLBAR_PX)
  const pickRef = useRef(pick)
  useLayoutEffect(() => {
    const own = ref.current
    const target = own ? pickRef.current(own) ?? own : null
    const measure = () => {
      const w = target?.getBoundingClientRect().width ?? 0
      setWide((w > 0 ? w : window.innerWidth) >= WIDE_TOOLBAR_PX)
    }
    measure()
    window.addEventListener('resize', measure)
    let ro: ResizeObserver | null = null
    if (target && typeof ResizeObserver === 'function') {
      ro = new ResizeObserver(measure)
      ro.observe(target)
    }
    return () => {
      window.removeEventListener('resize', measure)
      ro?.disconnect()
    }
  }, [ref])
  return wide
}

/** The toolbar row an element sits in (a toolbar slot's parent, or GraphToolbar). */
function toolbarOf(el: HTMLElement): HTMLElement | null {
  return (
    (el.closest('[data-testid="nb-toolbar"]') as HTMLElement | null) ??
    (el.closest('[data-nb-slot]')?.parentElement as HTMLElement | null) ??
    el.parentElement
  )
}

// -1 when no graph is loaded (the read-only view or nothing at all).
const selectNodeCount = (s: NodeBuilderState) => (s.graph ? Object.keys(s.graph.nodes).length : -1)

export function HintBar() {
  const ref = useRef<HTMLDivElement>(null)
  const [off, setOff] = useState(readHintOff)
  const wide = useWideToolbar(ref, toolbarOf)
  const nodeCount = useNodeBuilderStore(selectNodeCount)
  // The empty-graph state says the same thing in the canvas.
  const emptyGraph = nodeCount === 0
  const hidden = off || emptyGraph || !wide
  // When the text would overlap the right cluster, CSS drops it (the
  // `.nb-hint` box wraps it onto a clipped second line; statusChrome.css).
  return (
    <div ref={ref} className="nb-hint" aria-hidden="true">
      {!hidden && (
        <span className="nb-hint__wrap" data-testid="nb-hint">
          <span className="nb-hint__text">{HINT_TEXT}</span>
          <button
            type="button"
            className="nb-hint__dismiss"
            data-testid="nb-hint-dismiss"
            tabIndex={-1}
            title="Hide this hint"
            onClick={() => { writeHintOff(); setOff(true) }}
          >
            ✕
          </button>
        </span>
      )}
    </div>
  )
}

export function ResetViewButton() {
  const ref = useRef<HTMLButtonElement>(null)
  const wide = useWideToolbar(ref, toolbarOf)
  const nodeCount = useNodeBuilderStore(selectNodeCount)
  // No store graph: the read-only view frames whatever its canvas shows.
  const empty = nodeCount === 0 || (nodeCount < 0 && getActiveCanvas() == null)
  const title = 'Frame all nodes (H)'
  return (
    <Button
      ref={ref}
      kind={wide ? 'default' : 'icon'}
      keyCap={wide ? 'H' : undefined}
      aria-label="Reset view"
      title={title}
      disabled={empty}
      disabledReason="Nothing to frame"
      data-testid="nb-btn-reset-view"
      onClick={() => {
        runCommand('view.frameAll')
        getActiveCanvas()?.focus()
      }}
    >
      {wide ? 'Reset view' : '⌖'}
    </Button>
  )
}

export default HintBar
