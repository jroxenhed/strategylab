/**
 * Node builder on/off switch (surfaces-w1-w4.md 0.2). Label on the left,
 * a 28x16 track on the right. It is a real button with `role="switch"`,
 * so Space (and a click on the label) toggles it.
 */
import { useId } from 'react'
import type { ReactNode } from 'react'

export interface SwitchProps {
  checked: boolean
  onChange: (next: boolean) => void
  label?: ReactNode
  /** Accessible name when there is no visible label. */
  ariaLabel?: string
  disabled?: boolean
  title?: string
  'data-testid'?: string
}

export function Switch({ checked, onChange, label, ariaLabel, disabled, title, ...rest }: SwitchProps) {
  const labelId = useId()
  return (
    <span
      className={disabled ? 'nb-switch nb-switch--disabled' : 'nb-switch'}
      title={title}
      // A click on the label text toggles too, like a native label.
      onClick={e => {
        if (disabled || e.target instanceof HTMLButtonElement) return
        onChange(!checked)
      }}
    >
      {label != null && <span id={labelId}>{label}</span>}
      <button
        type="button"
        role="switch"
        className="nb-switch__track"
        aria-checked={checked}
        aria-label={label == null ? ariaLabel : undefined}
        aria-labelledby={label != null ? labelId : undefined}
        disabled={disabled}
        data-testid={rest['data-testid']}
        onClick={() => onChange(!checked)}
      />
    </span>
  )
}

export default Switch
