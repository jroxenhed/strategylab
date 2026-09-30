/**
 * Node builder button (surfaces-w1-w4.md 0.1). Styles live in tokens.css
 * (`.nb-btn` plus a kind modifier), so this is a thin wrapper.
 *
 * - `kind`: default, primary, danger, icon or text.
 * - `keyCap`: a short shortcut shown after the label, e.g. "⌘S".
 * - `disabledReason`: when the button is disabled, this becomes its `title`,
 *   so the user can see why.
 * - `pressed`: for icon toggles; sets `aria-pressed`.
 *
 * Buttons are `type="button"` unless told otherwise, so one inside a form
 * never submits it by accident.
 */
import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'

export type ButtonKind = 'default' | 'primary' | 'danger' | 'icon' | 'text'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: ButtonKind
  keyCap?: ReactNode
  disabledReason?: string
  pressed?: boolean
  ref?: Ref<HTMLButtonElement>
}

/** The class list for a button kind, for callers that style a plain element. */
export function buttonClass(kind: ButtonKind = 'default', extra?: string): string {
  const parts = ['nb-btn']
  if (kind !== 'default') parts.push(`nb-btn--${kind}`)
  if (extra) parts.push(extra)
  return parts.join(' ')
}

export function Button({
  kind = 'default',
  keyCap,
  disabledReason,
  pressed,
  className,
  title,
  type = 'button',
  children,
  ref,
  ...rest
}: ButtonProps) {
  const shownTitle = rest.disabled && disabledReason ? disabledReason : title
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClass(kind, className)}
      title={shownTitle}
      aria-pressed={pressed === undefined ? undefined : pressed}
      {...rest}
    >
      {children}
      {keyCap != null && <span className="nb-keycap" aria-hidden="true">{keyCap}</span>}
    </button>
  )
}

export default Button
