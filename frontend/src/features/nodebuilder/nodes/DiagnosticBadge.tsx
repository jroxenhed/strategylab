/**
 * DiagnosticBadge: the 14x14 error / warning circle in a node header
 * (spec S05, ui-ux-spec 4.8).
 *
 * - Any error: red with `!`; warnings only: amber with `▲`. More than one
 *   problem: the count replaces the glyph (the count includes both kinds).
 * - Hover for 400 ms: a tooltip lists the messages (5 at most, then
 *   "and N more"). The tooltip is portalled so the node card's
 *   `overflow: hidden` cannot clip it.
 * - Click: selects the node (from W3 it also opens the Inspector).
 * Info-severity problems do not show a badge.
 */

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import '../diagnostics.css'

/** Hover delay before the tooltip shows. */
export const BADGE_TOOLTIP_DELAY_MS = 400
const TOOLTIP_MAX_LINES = 5

/** Only errors and warnings make a badge. */
export function badgeDiagnostics(list: readonly Diagnostic[]): Diagnostic[] {
  return list.filter(d => d.severity === 'error' || d.severity === 'warning')
}

/** `1 error: <message>` / `2 errors, 1 warning` for the button's aria-label. */
export function badgeLabel(list: readonly Diagnostic[]): string {
  const errors = list.filter(d => d.severity === 'error').length
  const warnings = list.filter(d => d.severity === 'warning').length
  const parts: string[] = []
  if (errors) parts.push(`${errors} ${errors === 1 ? 'error' : 'errors'}`)
  if (warnings) parts.push(`${warnings} ${warnings === 1 ? 'warning' : 'warnings'}`)
  const head = parts.join(', ')
  return list.length === 1 ? `${head}: ${list[0].message}` : head
}

/** :focus-visible, where the browser supports the selector (keyboard focus). */
function isFocusVisible(el: Element): boolean {
  try {
    return el.matches(':focus-visible')
  } catch {
    return true
  }
}

export function DiagnosticBadge({
  nodeId,
  diagnostics,
  onActivate,
}: {
  nodeId: string
  diagnostics: readonly Diagnostic[]
  /** Called on click (after the node is selected by the caller). */
  onActivate?: () => void
}) {
  const list = badgeDiagnostics(diagnostics)
  const btnRef = useRef<HTMLButtonElement>(null)
  const [tipAt, setTipAt] = useState<{ x: number; y: number } | null>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearHover = () => {
    if (hoverTimer.current !== null) {
      clearTimeout(hoverTimer.current)
      hoverTimer.current = null
    }
  }
  useEffect(() => clearHover, [])

  if (list.length === 0) return null

  const hasError = list.some(d => d.severity === 'error')
  const glyph = list.length > 1 ? String(list.length) : hasError ? '!' : '▲'
  const kind = hasError ? 'error' : 'warning'
  const tipId = `nb-diag-tip-${nodeId}`
  // Every message, always in the page for screen readers (the tooltip is
  // hover or keyboard-focus only, and the label counts when there are several).
  const descId = `nb-diag-desc-${nodeId}`

  const showTip = () => {
    const r = btnRef.current?.getBoundingClientRect()
    if (r) setTipAt({ x: r.left, y: r.bottom + 4 })
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={`nb-diag-badge nb-diag-badge--${kind}${list.length > 1 ? ' nb-diag-badge--count' : ''}`}
        data-testid={`nb-diag-badge-${nodeId}`}
        aria-label={badgeLabel(list)}
        aria-describedby={list.length > 1 ? descId : undefined}
        onMouseEnter={() => {
          clearHover()
          hoverTimer.current = setTimeout(showTip, BADGE_TOOLTIP_DELAY_MS)
        }}
        onMouseLeave={() => { clearHover(); setTipAt(null) }}
        // Keyboard focus shows the tooltip too; a mouse click's focus does not.
        onFocus={e => { if (isFocusVisible(e.currentTarget)) showTip() }}
        onBlur={() => { clearHover(); setTipAt(null) }}
        onClick={() => { clearHover(); setTipAt(null); onActivate?.() }}
      >
        {glyph}
      </button>
      {list.length > 1 && (
        <span id={descId} className="nb-sr-only">
          {list.map(d => d.message).join('. ')}
        </span>
      )}
      {tipAt && createPortal(
        <div
          id={tipId}
          role="tooltip"
          className="nodebuilder-root nb-diag-tooltip"
          style={{ left: tipAt.x, top: tipAt.y }}
        >
          {list.slice(0, TOOLTIP_MAX_LINES).map((d, i) => (
            <div key={i} className="nb-diag-tooltip__line">
              <span className={`nb-diag-tooltip__mark nb-diag-tooltip__mark--${d.severity}`}>
                {d.severity === 'error' ? '●' : '▲'}
              </span>
              <span>{d.message}</span>
            </div>
          ))}
          {list.length > TOOLTIP_MAX_LINES && (
            <div className="nb-diag-tooltip__more">and {list.length - TOOLTIP_MAX_LINES} more</div>
          )}
        </div>,
        document.body,
      )}
    </>
  )
}

export default DiagnosticBadge
