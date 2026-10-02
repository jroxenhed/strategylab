/**
 * DayOfWeekInput: toggle pills for a multi-pick param (spec S12).
 *
 * Built for the `day_of_week` node's `days` param (`M T W T F`, value an
 * array such as `['mon', 'tue']` in fixed weekday order), but it works for
 * any select param whose value is a list: the pills are the param's
 * options. Weekends are not offered unless the catalog lists them.
 *
 * Each click is one `updateNodeParams` (one undo step). All off gets an
 * amber ring: the signal would never be true.
 */

import { useRef } from 'react'
import { useNodeBuilderStore } from '../store'
import '../stream.css'

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri'] as const

const DAY_NAMES: Record<string, { short: string; long: string }> = {
  mon: { short: 'M', long: 'Monday' },
  tue: { short: 'T', long: 'Tuesday' },
  wed: { short: 'W', long: 'Wednesday' },
  thu: { short: 'T', long: 'Thursday' },
  fri: { short: 'F', long: 'Friday' },
  sat: { short: 'S', long: 'Saturday' },
  sun: { short: 'S', long: 'Sunday' },
}

export const NO_DAYS_MESSAGE = 'No days selected: the signal is always false'

/** The picked options, as a list in option order (unknown entries dropped). */
export function pickedDays(value: unknown, options: readonly string[]): string[] {
  const list = Array.isArray(value)
    ? value.filter((v): v is string => typeof v === 'string')
    : typeof value === 'string'
      ? value.split(/[\s,]+/).filter(Boolean)
      : []
  return options.filter(o => list.includes(o))
}

/** Toggle one option, keeping option order. */
export function toggleDay(picked: readonly string[], day: string, options: readonly string[]): string[] {
  const on = picked.includes(day)
  return options.filter(o => (o === day ? !on : picked.includes(o)))
}

export interface DayOfWeekInputProps {
  nodeId: string
  param: string
  value: unknown
  /** The param's options; the weekdays when the catalog gives none. */
  options?: readonly string[]
  disabled?: boolean
}

export function DayOfWeekInput({ nodeId, param, value, options, disabled = false }: DayOfWeekInputProps) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const opts = options && options.length > 0 ? options : WEEKDAYS
  const picked = pickedDays(value, opts)
  const none = picked.length === 0
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  const toggle = (day: string) => {
    if (disabled) return
    updateNodeParams(nodeId, { [param]: toggleDay(picked, day, opts) })
  }

  return (
    <span
      className={`nb-days${none ? ' nb-days--none' : ''}`}
      role="group"
      aria-label={param}
      title={none ? NO_DAYS_MESSAGE : undefined}
      onPointerDown={e => e.stopPropagation()}
    >
      {opts.map((day, i) => {
        const on = picked.includes(day)
        const names = DAY_NAMES[day] ?? { short: day.slice(0, 1).toUpperCase(), long: day }
        return (
          <button
            key={day}
            ref={el => { refs.current[i] = el }}
            type="button"
            role="checkbox"
            aria-checked={on}
            aria-label={names.long}
            className={`nb-day${on ? ' nb-day--on' : ''}`}
            data-testid={`nb-day-${day}`}
            disabled={disabled}
            onClick={() => toggle(day)}
            onKeyDown={e => {
              // Keys stay in the picker; the canvas must not act on them.
              if (e.key === 'Escape') return
              e.stopPropagation()
              if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                e.preventDefault()
                const next = (i + (e.key === 'ArrowRight' ? 1 : -1) + opts.length) % opts.length
                refs.current[next]?.focus()
              }
            }}
          >
            {names.short}
          </button>
        )
      })}
    </span>
  )
}

export default DayOfWeekInput
