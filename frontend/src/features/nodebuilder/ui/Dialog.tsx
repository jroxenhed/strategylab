/**
 * Node builder dialog shell (surfaces-w1-w4.md 0.3).
 *
 * Mount it to open it, unmount it to close it. It renders into
 * `document.body` with the `nodebuilder-root` class so the design tokens
 * apply, over a backdrop that covers the whole app.
 *
 * Keys and focus:
 * - Focus is trapped inside the dialog while it is the top one.
 * - First focus goes to `initialFocusRef`, else the first input, else the
 *   primary button, else the dialog itself.
 * - `Esc` calls `onCancel`. So does the `✕` button.
 * - `Enter` clicks the primary button, unless focus is in a textarea, on a
 *   button or link (those keep their own Enter), or an inner widget already
 *   handled the key (`preventDefault`).
 * - On close, focus goes back to `returnFocusTo`, else to whatever had focus
 *   when the dialog opened.
 *
 * The primary button: the default footer marks it. A custom `footer` marks
 * its own with the `data-nb-primary` attribute (see `PRIMARY_ATTR`).
 *
 * Dialogs can stack (a confirm over the Graph Browser). Only the top one
 * handles keys and holds focus.
 */
import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject } from 'react'
import { createPortal } from 'react-dom'
import { Button } from './Button'
import { isTopDialog as isTop, isDialogOpen, popDialog, pushDialog, topDialogPanel } from './dialogStack'

/** Put this attribute on the primary button of a custom footer. */
export const PRIMARY_ATTR = 'data-nb-primary'

export interface DialogProps {
  title: ReactNode
  /** Esc, the ✕ button and the default Cancel button call this. */
  onCancel: () => void
  children?: ReactNode
  /** Width in px (or any CSS length). The shell caps it at min(90vw, 960px). */
  width?: number | string
  /** Fixed height (for list dialogs). By default the dialog fits its content, up to 80vh. */
  height?: number | string
  /** Extra header content (a search field, a select), placed before the ✕. */
  headerExtra?: ReactNode
  /** Left side of the footer (a note). */
  footerLeft?: ReactNode
  /**
   * Replace the default footer buttons. Mark the primary with
   * `data-nb-primary` so Enter and the initial focus find it.
   * Pass `null` for no footer at all.
   */
  footer?: ReactNode | null
  /** Default footer: the primary button's label. No label means no primary. */
  primaryLabel?: ReactNode
  onPrimary?: () => void
  primaryDisabled?: boolean
  /** Title for the disabled primary, saying why. */
  primaryDisabledReason?: string
  /** Default footer: the primary uses the danger kind (Delete, Discard). */
  danger?: boolean
  cancelLabel?: ReactNode
  initialFocusRef?: RefObject<HTMLElement | null>
  returnFocusTo?: HTMLElement | null
  'data-testid'?: string
  /** id of an element that describes the dialog (its main sentence). */
  ariaDescribedBy?: string
}

const ParentDialog = createContext<string | null>(null)

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',')

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    el => !el.hasAttribute('inert') && el.getAttribute('aria-hidden') !== 'true',
  )
}

function findPrimary(root: HTMLElement): HTMLButtonElement | null {
  return root.querySelector<HTMLButtonElement>(`[${PRIMARY_ATTR}]`)
}

/** Enter keeps its own meaning on these; it does not press the primary. */
function enterBelongsToTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || typeof el.tagName !== 'string') return false
  const tag = el.tagName
  if (tag === 'TEXTAREA' || tag === 'BUTTON' || tag === 'SELECT') return true
  if (tag === 'A' && el.hasAttribute('href')) return true
  return el.isContentEditable === true
}

function pickInitialFocus(panel: HTMLElement): HTMLElement {
  const input = panel.querySelector<HTMLElement>(
    'input:not([disabled]):not([type="hidden"]), textarea:not([disabled])',
  )
  if (input) return input
  const primary = findPrimary(panel)
  if (primary && !primary.disabled) return primary
  return focusables(panel)[0] ?? panel
}

export function Dialog({
  title,
  onCancel,
  children,
  width = 360,
  height,
  headerExtra,
  footerLeft,
  footer,
  primaryLabel,
  onPrimary,
  primaryDisabled,
  primaryDisabledReason,
  danger,
  cancelLabel = 'Cancel',
  initialFocusRef,
  returnFocusTo,
  ariaDescribedBy,
  ...rest
}: DialogProps) {
  const id = useId()
  const parentDialog = useContext(ParentDialog)
  const titleId = `${id}-title`
  const panelRef = useRef<HTMLDivElement>(null)
  // Whatever had focus when the dialog first rendered opened it.
  const [opener] = useState<Element | null>(() =>
    typeof document !== 'undefined' ? document.activeElement : null,
  )
  // Latest props for the listeners, so they never go stale.
  const latest = useRef({ onCancel, returnFocusTo })
  useLayoutEffect(() => {
    latest.current = { onCancel, returnFocusTo }
  })

  // Join the stack, take focus; on close give focus back.
  useLayoutEffect(() => {
    const panel = panelRef.current
    pushDialog(id, parentDialog, panel)
    // Only the top dialog takes focus (an inner dialog opened in the same
    // render keeps it).
    if (panel && isTop(id)) (initialFocusRef?.current ?? pickInitialFocus(panel)).focus({ preventScroll: true })
    return () => {
      popDialog(id)
      const back = latest.current.returnFocusTo ?? opener
      if (back instanceof HTMLElement && back.isConnected && back !== document.body) {
        back.focus({ preventScroll: true })
      } else if (isDialogOpen()) {
        // The opener is gone but a dialog below is still open: focus it.
        topDialogPanel()?.focus({ preventScroll: true })
      } else {
        // The opener is gone (a menu item that unmounted) or was the page
        // body: give focus to the node builder (its canvas, else its root),
        // so its keys keep working.
        const home =
          document.querySelector<HTMLElement>('.nodebuilder-root[tabindex="0"]') ??
          document.querySelector<HTMLElement>('[data-nb-builder]')
        home?.focus({ preventScroll: true })
      }
    }
    // Runs once per open; the focus choice is made on open only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keys whose target is outside the dialog (focus fell to the page body, or
  // sits somewhere behind the backdrop). The dialog is modal: nothing behind
  // it hears them. Tab brings focus back in, Esc still cancels. Keys inside
  // a popover (its own portal) are left alone.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const panel = panelRef.current
      if (!panel || !isTop(id)) return
      const t = e.target as Element | null
      if (t instanceof Node && panel.contains(t)) return
      if (t instanceof Element && t.closest('.nb-popover')) return
      e.stopPropagation()
      // Cmd+S / Cmd+O would open the browser's own Save or Open dialog.
      if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S' || e.key === 'o' || e.key === 'O')) {
        e.preventDefault()
        return
      }
      if (e.key === 'Tab') {
        e.preventDefault()
        const items = focusables(panel)
        const next = e.shiftKey ? items[items.length - 1] : items[0]
        ;(next ?? panel).focus()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        latest.current.onCancel()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [id])

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const panel = panelRef.current
    if (!panel || !isTop(id)) return
    if (e.key === 'Escape') {
      if (e.defaultPrevented) return
      e.preventDefault()
      e.stopPropagation()
      onCancel()
      return
    }
    if (e.key === 'Enter') {
      if (e.defaultPrevented || e.nativeEvent.isComposing) return
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
      if (enterBelongsToTarget(e.target)) return
      const primary = findPrimary(panel)
      if (!primary || primary.disabled) return
      e.preventDefault()
      e.stopPropagation()
      primary.click()
      return
    }
    if (e.key === 'Tab') {
      const items = focusables(panel)
      if (items.length === 0) {
        e.preventDefault()
        panel.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  const defaultFooter = (
    <>
      <Button onClick={onCancel} data-testid="nb-dialog-cancel">{cancelLabel}</Button>
      {primaryLabel != null && (
        <Button
          kind={danger ? 'danger' : 'primary'}
          onClick={onPrimary}
          disabled={primaryDisabled}
          disabledReason={primaryDisabledReason}
          data-testid="nb-dialog-primary"
          {...{ [PRIMARY_ATTR]: '' }}
        >
          {primaryLabel}
        </Button>
      )}
    </>
  )
  const footerContent = footer === undefined ? defaultFooter : footer

  const dialog = (
    <div
      className="nodebuilder-root nb-dialog-backdrop"
      // A press on the backdrop must not move focus out of the dialog.
      onMouseDown={e => {
        if (e.target === e.currentTarget) e.preventDefault()
      }}
    >
      <div
        ref={panelRef}
        className="nb-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={ariaDescribedBy}
        tabIndex={-1}
        style={{ width, height }}
        onKeyDown={onKeyDown}
        data-testid={rest['data-testid']}
      >
        <div className="nb-dialog__header">
          <h2 id={titleId} className="nb-dialog__title">{title}</h2>
          {headerExtra}
          <Button kind="icon" onClick={onCancel} aria-label="Close" title="Close" data-testid="nb-dialog-close">
            ✕
          </Button>
        </div>
        <div className="nb-dialog__body">
          <ParentDialog.Provider value={id}>{children}</ParentDialog.Provider>
        </div>
        {footerContent !== null && (
          <div className="nb-dialog__footer">
            {footerLeft != null && <div className="nb-dialog__footer-left">{footerLeft}</div>}
            <div className="nb-dialog__footer-actions">{footerContent}</div>
          </div>
        )}
      </div>
    </div>
  )

  return typeof document !== 'undefined' ? createPortal(dialog, document.body) : dialog
}

export default Dialog
