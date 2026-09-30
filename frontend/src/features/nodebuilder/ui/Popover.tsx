/**
 * Node builder popover shell (surfaces-w1-w4.md 0.4).
 *
 * Mount it to open it, unmount it to close it. It renders into
 * `document.body` with the `nodebuilder-root` class (so the tokens apply),
 * positioned `fixed` next to its anchor:
 * - `anchor` is an element (opens under it) or a screen point `{x, y}`.
 * - It stays inside `bounds` (the canvas column; the window if not given)
 *   with an 8px margin, and flips above the anchor when it would leave the
 *   bottom.
 *
 * It asks to close (`onClose(reason)`) on a pointer-down outside it, `Esc`,
 * window blur, and `closeActivePopover()`, which the canvas calls on zoom or
 * pan. A press on the anchor element itself does not count as outside, so
 * the anchor's own click can toggle the popover.
 *
 * Only one popover is open at a time: opening one asks the open one to close.
 * After an `Esc` close, focus goes back to the anchor element.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { AriaRole, CSSProperties, ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type PopoverPoint = { x: number; y: number }
export type PopoverAnchor = HTMLElement | PopoverPoint | null
export type PopoverCloseReason = 'outside' | 'escape' | 'tab' | 'blur' | 'signal' | 'replaced'

export interface Rect { left: number; top: number; right: number; bottom: number }

/** Space between the popover and the edge of its bounds. */
export const POPOVER_MARGIN = 8
/** Space between the anchor and the popover. */
export const POPOVER_GAP = 4

export interface PopoverPlacementInput {
  anchor: Rect
  size: { width: number; height: number }
  bounds: Rect
  /** 'start': left edges line up. 'end': right edges line up. */
  align?: 'start' | 'end'
  margin?: number
  gap?: number
}

export interface PopoverPlacement {
  left: number
  top: number
  side: 'below' | 'above'
}

/**
 * Where the popover goes. Pure, so it is tested without a layout engine.
 * Below the anchor by default; above it when below would leave the bottom
 * and above has room. When neither side fits, the side with more room wins
 * and the popover is clamped inside the bounds.
 */
export function placePopover({
  anchor,
  size,
  bounds,
  align = 'start',
  margin = POPOVER_MARGIN,
  gap = POPOVER_GAP,
}: PopoverPlacementInput): PopoverPlacement {
  const minLeft = bounds.left + margin
  const maxLeft = bounds.right - margin - size.width
  let left = align === 'end' ? anchor.right - size.width : anchor.left
  left = Math.max(minLeft, Math.min(left, maxLeft))
  // Wider than the bounds: pin to the left margin.
  if (maxLeft < minLeft) left = minLeft

  const minTop = bounds.top + margin
  const maxBottom = bounds.bottom - margin
  const below = anchor.bottom + gap
  const above = anchor.top - gap - size.height
  let side: 'below' | 'above' = 'below'
  let top = below
  if (below + size.height > maxBottom) {
    if (above >= minTop) {
      side = 'above'
      top = above
    } else {
      const roomBelow = maxBottom - below
      const roomAbove = anchor.top - gap - minTop
      if (roomAbove > roomBelow) {
        side = 'above'
        top = minTop
      } else {
        top = Math.max(minTop, maxBottom - size.height)
      }
    }
  }
  top = Math.max(minTop, top)
  return { left, top, side }
}

// The one open popover: its close function, so a new one or the canvas can
// ask it to close.
let active: { token: object; close: (reason: PopoverCloseReason) => void } | null = null

/** Ask the open popover (if any) to close. The canvas calls this on zoom or pan. */
export function closeActivePopover(reason: PopoverCloseReason = 'signal'): void {
  active?.close(reason)
}

/** True while a popover is open (for keyboard scope checks). */
export function isPopoverOpen(): boolean {
  return active !== null
}

function isPoint(a: PopoverAnchor): a is PopoverPoint {
  return a !== null && !(a instanceof HTMLElement) && typeof (a as PopoverPoint).x === 'number'
}

function anchorRect(a: PopoverAnchor): Rect {
  if (a === null) return { left: 0, top: 0, right: 0, bottom: 0 }
  if (isPoint(a)) return { left: a.x, top: a.y, right: a.x, bottom: a.y }
  const r = a.getBoundingClientRect()
  return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }
}

function boundsRect(b: HTMLElement | null | undefined): Rect {
  if (b) {
    const r = b.getBoundingClientRect()
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }
  }
  return { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight }
}

export interface PopoverProps {
  anchor: PopoverAnchor
  onClose: (reason: PopoverCloseReason) => void
  children?: ReactNode
  /** Per use: 'dialog', 'menu', 'listbox'. */
  role?: AriaRole
  ariaLabel?: string
  ariaLabelledBy?: string
  /** The canvas column. The popover stays inside it with an 8px margin. */
  bounds?: HTMLElement | null
  align?: 'start' | 'end'
  width?: number | string
  maxHeight?: number | string
  /** Move focus to the first focusable item on open (menus, lists). */
  autoFocus?: boolean
  className?: string
  style?: CSSProperties
  'data-testid'?: string
}

export function Popover({
  anchor,
  onClose,
  children,
  role = 'dialog',
  ariaLabel,
  ariaLabelledBy,
  bounds,
  align = 'start',
  width,
  maxHeight,
  autoFocus,
  className,
  style,
  ...rest
}: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  // Latest values for the listeners, so they never go stale.
  const latest = useRef({ onClose, anchor })
  const roleRef = useRef(role)
  useLayoutEffect(() => {
    latest.current = { onClose, anchor }
    roleRef.current = role
  })
  const closedWith = useRef<PopoverCloseReason | null>(null)

  // Ask to close once, however many signals arrive together.
  const requestClose = useRef((reason: PopoverCloseReason) => {
    if (closedWith.current) return
    closedWith.current = reason
    latest.current.onClose(reason)
  }).current

  // Place it before the browser paints, and again when its size, the
  // window or the anchor changes.
  useLayoutEffect(() => {
    const panel = panelRef.current
    if (!panel) return
    const place = () => {
      const r = panel.getBoundingClientRect()
      const next = placePopover({
        anchor: anchorRect(latest.current.anchor),
        size: { width: r.width, height: r.height },
        bounds: boundsRect(bounds),
        align,
      })
      setPos(prev => (prev && prev.left === next.left && prev.top === next.top ? prev : { left: next.left, top: next.top }))
    }
    place()
    window.addEventListener('resize', place)
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(place) : null
    ro?.observe(panel)
    return () => {
      window.removeEventListener('resize', place)
      ro?.disconnect()
    }
  }, [anchor, bounds, align])

  // Become the one open popover; ask the previous one to close.
  useEffect(() => {
    const token = {}
    const prev = active
    active = { token, close: requestClose }
    if (prev) prev.close('replaced')
    return () => {
      if (active?.token === token) active = null
      // After Esc, give focus back to the anchor element.
      const a = latest.current.anchor
      if (closedWith.current === 'escape' && a instanceof HTMLElement && a.isConnected) {
        a.focus({ preventScroll: true })
      }
    }
  }, [requestClose])

  // First focus waits for the first placement: until `pos` is set the panel
  // is visibility:hidden, and a hidden element cannot take focus (the open
  // click is a sync update, so a plain effect would run while it is hidden).
  const focused = useRef(false)
  useLayoutEffect(() => {
    if (!autoFocus || !pos || focused.current) return
    focused.current = true
    const first = panelRef.current?.querySelector<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )
    ;(first ?? panelRef.current)?.focus({ preventScroll: true })
  }, [autoFocus, pos])

  // Outside press, Esc and window blur. Capture phase, so React Flow
  // stopping the event on the pane cannot hide it from us.
  useEffect(() => {
    const onDown = (e: Event) => {
      const t = e.target
      if (t instanceof Node) {
        if (panelRef.current?.contains(t)) return
        const a = latest.current.anchor
        if (a instanceof HTMLElement && a.contains(t)) return
      }
      requestClose('outside')
    }
    const onKey = (e: KeyboardEvent) => {
      // A menu closes on Tab (menu button pattern): focus goes back to the
      // anchor first, so the Tab then moves on from there instead of leaving
      // the page from this portal at the end of <body>.
      if (e.key === 'Tab' && roleRef.current === 'menu') {
        const t = e.target
        if (!(t instanceof Node) || !panelRef.current?.contains(t)) return
        const a = latest.current.anchor
        if (a instanceof HTMLElement && a.isConnected) a.focus({ preventScroll: true })
        requestClose('tab')
        return
      }
      if (e.key !== 'Escape') return
      // The popover eats this Esc; a dialog or the canvas behind it must not
      // act on it too.
      e.preventDefault()
      e.stopPropagation()
      requestClose('escape')
    }
    const onBlur = () => requestClose('blur')
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('mousedown', onDown, true)
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('blur', onBlur)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('mousedown', onDown, true)
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('blur', onBlur)
    }
  }, [requestClose])

  const panel = (
    <div
      ref={panelRef}
      className={className ? `nodebuilder-root nb-popover ${className}` : 'nodebuilder-root nb-popover'}
      role={role}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      tabIndex={-1}
      data-testid={rest['data-testid']}
      style={{
        ...style,
        width,
        maxHeight,
        left: pos?.left ?? 0,
        top: pos?.top ?? 0,
        // Hidden until the first placement, so it never flashes at 0,0.
        visibility: pos ? style?.visibility : 'hidden',
      }}
    >
      {children}
    </div>
  )

  return createPortal(panel, document.body)
}

export default Popover
