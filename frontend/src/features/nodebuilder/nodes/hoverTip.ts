/**
 * hoverTip: the small delayed tooltip on ports (S08) and flag dots (S16).
 *
 * The spec says hovering a port or a dot must not update React state, so
 * this is plain DOM: one shared tooltip element on the page body, shown
 * 400 ms after the pointer arrives and hidden when it leaves or presses.
 * It carries the `nodebuilder-root` class so the design tokens apply
 * outside the canvas tree, and it never takes pointer events.
 *
 * Usage, on any element:
 *   onPointerEnter={e => startHoverTip(e.currentTarget, 'in1 · @spread_z')}
 *   onPointerLeave={endHoverTip}
 *   onPointerDown={endHoverTip}
 *
 * While the tip is pending or shown, the element's own `title` is moved
 * aside (and put back on leave), so the browser's slower native tooltip
 * does not show as well.
 */

/** Hover delay before the tip shows (S08, S16). */
export const HOVER_TIP_DELAY_MS = 400

/** Where the tip sits relative to the element. */
export type HoverTipSide = 'above' | 'below' | 'right'

let tipEl: HTMLDivElement | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let owner: HTMLElement | null = null
let ownerTitle: string | null = null
// While the tip shows: checks that its element is still on screen (FC-7).
let watch: ReturnType<typeof setInterval> | null = null

/** How often a shown tip checks that its element is still there. */
export const HOVER_TIP_WATCH_MS = 200

function ensureTip(): HTMLDivElement {
  if (tipEl && tipEl.isConnected) return tipEl
  tipEl = document.createElement('div')
  tipEl.className = 'nodebuilder-root nb-hover-tip'
  tipEl.setAttribute('role', 'tooltip')
  tipEl.setAttribute('data-testid', 'nb-hover-tip')
  tipEl.hidden = true
  document.body.appendChild(tipEl)
  // A wheel zoom moves the element under a still pointer; hide the tip
  // rather than leave it pointing at the old spot.
  window.addEventListener('wheel', endHoverTip, { capture: true, passive: true })
  // A key can remove the element under a still pointer (Delete, Cmd+Z,
  // Cmd+X on the hovered node), and a removed element never gets its
  // pointerleave: hide on any key press (FC-7).
  document.addEventListener('keydown', endHoverTip, true)
  return tipEl
}

function place(tip: HTMLDivElement, anchor: HTMLElement, side: HoverTipSide) {
  const r = anchor.getBoundingClientRect()
  const w = tip.offsetWidth
  const h = tip.offsetHeight
  const vw = window.innerWidth || document.documentElement.clientWidth
  let x: number
  let y: number
  if (side === 'right') {
    x = r.right + 8
    y = r.top + r.height / 2 - h / 2
  } else {
    x = r.left + r.width / 2 - w / 2
    y = side === 'above' ? r.top - h - 8 : r.bottom + 8
  }
  // Keep it on screen with an 8px margin.
  x = Math.max(8, Math.min(x, vw - w - 8))
  y = Math.max(8, y)
  tip.style.left = `${Math.round(x)}px`
  tip.style.top = `${Math.round(y)}px`
}

/**
 * Show `text` near `el` after the hover delay. Calling it again (another
 * element, or new text) restarts the delay.
 */
export function startHoverTip(el: HTMLElement, text: string, side: HoverTipSide = 'above'): void {
  endHoverTip()
  if (!text) return
  owner = el
  ownerTitle = el.getAttribute('title')
  if (ownerTitle != null) el.removeAttribute('title')
  el.setAttribute('data-tip', text)
  timer = setTimeout(() => {
    timer = null
    if (owner !== el || !el.isConnected) return
    const tip = ensureTip()
    tip.textContent = text
    tip.hidden = false
    place(tip, el, side)
    // Hide once the element is gone or hidden (an undo from the toolbar, the
    // app switching tabs), since no pointerleave will come (FC-7).
    // (A layout-less DOM, as in tests, has no rects at all: only the
    // removal check applies there.)
    const hadBox = el.getClientRects().length > 0
    watch = setInterval(() => {
      if (owner !== el || !el.isConnected || (hadBox && el.getClientRects().length === 0)) endHoverTip()
    }, HOVER_TIP_WATCH_MS)
  }, HOVER_TIP_DELAY_MS)
}

/** Hide the tip (and cancel a pending one). Safe to call any time. */
export function endHoverTip(): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  if (watch !== null) {
    clearInterval(watch)
    watch = null
  }
  if (tipEl) tipEl.hidden = true
  if (owner) {
    if (ownerTitle != null) owner.setAttribute('title', ownerTitle)
    owner = null
    ownerTitle = null
  }
}

/** The tip text shown now, or null (tests). */
export function shownHoverTip(): string | null {
  return tipEl && !tipEl.hidden ? tipEl.textContent : null
}
