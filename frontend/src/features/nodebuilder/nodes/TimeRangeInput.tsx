/**
 * TimeRangeInput: the value cell of a `time_range` param (spec S12).
 *
 * The value is `"HH:MM-HH:MM"` (24 h, zero padded, a hyphen), New York wall
 * clock (ET), the same clock as the chart's `toET()` and the backend. It is
 * never converted to the browser's time zone, and there is no
 * `<input type="time">` (locale and AM/PM surprises).
 *
 * Closed: `09:35–15:55`, or `session ▾` when unset (the server then uses
 * the whole session). Open: two typed fields, a session strip with two
 * grips (5-minute steps, Shift for 1 minute), presets, Apply and Cancel.
 * Only Apply commits, so one range change is one undo step.
 *
 * A stored value that does not parse (`9:30-16:00` from a hand edit) is
 * shown raw in the error style with the server's message, never as the
 * whole session, and the popover opens with it so it can be fixed.
 */

import { useRef, useState } from 'react'
import { useNodeBuilderStore } from '../store'
import { useParamDiagnostic } from '../useDiagnostics'
import { Popover } from '../ui/Popover'
import '../stream.css'

/** 09:30 and 16:00 in minutes: the regular session drawn on the strip. */
export const SESSION_OPEN = 570
export const SESSION_CLOSE = 960

const HHMM_RE = /^([01]\d|2[0-3]):([0-5]\d)$/

/** `'09:35'` gives 575; anything else gives null (`9:5`, `25:00`). */
export function parseHHMM(text: string): number | null {
  const m = HHMM_RE.exec(text.trim())
  return m ? Number(m[1]) * 60 + Number(m[2]) : null
}

/** 575 gives `'09:35'`. */
export function formatHHMM(minutes: number): string {
  const m = Math.max(0, Math.min(24 * 60 - 1, Math.round(minutes)))
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

/** `'09:35-15:55'` gives [575, 955]; null when unset or not a valid range. */
export function parseRange(value: unknown): [number, number] | null {
  if (typeof value !== 'string') return null
  const parts = value.split('-')
  if (parts.length !== 2) return null
  const from = parseHHMM(parts[0])
  const to = parseHHMM(parts[1])
  if (from === null || to === null || from >= to) return null
  return [from, to]
}

/** The stored form: `'09:35-15:55'`. */
export function formatRange(from: number, to: number): string {
  return `${formatHHMM(from)}-${formatHHMM(to)}`
}

/** The cell text: `09:35–15:55` (en dash), or null when unset or invalid. */
export function displayRange(value: unknown): string | null {
  const r = parseRange(value)
  return r ? `${formatHHMM(r[0])}–${formatHHMM(r[1])}` : null
}

/** A set value that is not a valid range (shown raw, as an error). */
export function isInvalidRange(value: unknown): boolean {
  return typeof value === 'string' && value.trim() !== '' && parseRange(value) === null
}

/** The from / to texts the popover opens with: the stored range, its raw halves when it does not parse, else the session. */
export function initialRangeTexts(value: unknown): [string, string] {
  const r = parseRange(value)
  if (r) return [formatHHMM(r[0]), formatHHMM(r[1])]
  if (isInvalidRange(value)) {
    const parts = (value as string).split('-')
    if (parts.length === 2) return [parts[0].trim(), parts[1].trim()]
  }
  return [formatHHMM(SESSION_OPEN), formatHHMM(SESSION_CLOSE)]
}

/** Round to a step of minutes (5, or 1 with Shift). */
export function snapMinutes(minutes: number, step: number): number {
  return Math.round(minutes / step) * step
}

const PRESETS: { label: string; from: number; to: number }[] = [
  { label: 'Open 09:30–10:30', from: 570, to: 630 },
  { label: 'Midday 10:30–15:00', from: 630, to: 900 },
  { label: 'Close 15:00–16:00', from: 900, to: 960 },
  { label: 'Full session', from: 570, to: 960 },
]

const TICKS = [570, 720, 960]

/** Where a time sits on the strip, 0..100 %, clamped to the session. */
function stripPct(minutes: number): number {
  const m = Math.max(SESSION_OPEN, Math.min(SESSION_CLOSE, minutes))
  return ((m - SESSION_OPEN) / (SESSION_CLOSE - SESSION_OPEN)) * 100
}

export interface TimeRangeInputProps {
  nodeId: string
  param: string
  value: unknown
  disabled?: boolean
}

export function TimeRangeInput({ nodeId, param, value, disabled = false }: TimeRangeInputProps) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLButtonElement>(null)
  const serverDiag = useParamDiagnostic(nodeId, param)
  const shown = displayRange(value)
  const invalid = isInvalidRange(value)
  const raw = invalid ? (value as string) : null
  const cls = shown ? 'nb-time-cell' : invalid ? 'nb-time-cell nb-time-cell--invalid' : 'nb-time-cell nb-time-cell--unset'
  const title = invalid ? (serverDiag?.message ?? `${raw} is not a time range. Use HH:MM-HH:MM.`) : undefined
  const text = shown ?? raw ?? (disabled ? 'session' : 'session ▾')

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        className={cls}
        data-testid={`nb-timerange-${nodeId}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-invalid={invalid ? true : undefined}
        aria-label={`${param}: ${shown ?? (invalid ? `${raw}, not a valid range` : 'whole session')}`}
        title={title}
        disabled={disabled}
        onPointerDown={e => e.stopPropagation()}
        onClick={() => setOpen(o => !o)}
      >
        {text}
      </button>
      {open && !disabled && (
        <TimeRangePopover
          anchor={anchorRef.current}
          value={value}
          onApply={next => {
            setOpen(false)
            if (next !== value) updateNodeParams(nodeId, { [param]: next })
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}

function TimeRangePopover({
  anchor,
  value,
  onApply,
  onClose,
}: {
  anchor: HTMLElement | null
  value: unknown
  onApply: (next: string) => void
  onClose: () => void
}) {
  const [fromText, setFromText] = useState(() => initialRangeTexts(value)[0])
  const [toText, setToText] = useState(() => initialRangeTexts(value)[1])
  const stripRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<'from' | 'to' | null>(null)

  const from = parseHHMM(fromText)
  const to = parseHHMM(toText)
  const valid = from !== null && to !== null && from < to

  const apply = () => { if (valid) onApply(formatRange(from, to)) }

  const setEnd = (end: 'from' | 'to', minutes: number) => {
    const m = Math.max(0, Math.min(24 * 60 - 1, minutes))
    if (end === 'from') setFromText(formatHHMM(m))
    else setToText(formatHHMM(m))
  }

  const minutesAt = (clientX: number, shift: boolean): number => {
    const r = stripRef.current?.getBoundingClientRect()
    const width = r && r.width > 0 ? r.width : 1
    const frac = Math.max(0, Math.min(1, (clientX - (r?.left ?? 0)) / width))
    return snapMinutes(SESSION_OPEN + frac * (SESSION_CLOSE - SESSION_OPEN), shift ? 1 : 5)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') return
    e.stopPropagation()
    if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') {
      e.preventDefault()
      apply()
    }
  }

  const grip = (end: 'from' | 'to') => {
    const minutes = end === 'from' ? from : to
    if (minutes === null) return null
    return (
      <div
        role="slider"
        tabIndex={0}
        className="nb-time-strip__grip"
        aria-label={end === 'from' ? 'from' : 'to'}
        aria-valuemin={SESSION_OPEN}
        aria-valuemax={SESSION_CLOSE}
        aria-valuenow={minutes}
        aria-valuetext={formatHHMM(minutes)}
        data-testid={`nb-timerange-grip-${end}`}
        style={{ left: `${stripPct(minutes)}%` }}
        onPointerDown={e => {
          e.preventDefault()
          e.stopPropagation()
          dragRef.current = end
          ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
        }}
        onPointerMove={e => {
          if (dragRef.current !== end) return
          setEnd(end, minutesAt(e.clientX, e.shiftKey))
        }}
        onPointerUp={() => { dragRef.current = null }}
        onKeyDown={e => {
          const step = e.shiftKey ? 1 : 5
          if (e.key === 'ArrowLeft') { e.preventDefault(); setEnd(end, minutes - step) }
          else if (e.key === 'ArrowRight') { e.preventDefault(); setEnd(end, minutes + step) }
        }}
      />
    )
  }

  const barLeft = from !== null ? stripPct(from) : 0
  const barRight = to !== null ? stripPct(to) : 100

  return (
    <Popover
      anchor={anchor}
      onClose={onClose}
      role="dialog"
      ariaLabel="Time of day range"
      width={300}
      autoFocus
      className="nb-time-pop"
      data-testid="nb-timerange-popover"
    >
      <div onKeyDown={onKeyDown} style={{ padding: 6 }}>
        <div className="nb-time-pop__inputs">
          <input
            type="text"
            inputMode="numeric"
            placeholder="HH:MM"
            aria-label="from"
            aria-invalid={from === null || !valid ? true : undefined}
            className={`nb-time-pop__input${from === null || !valid ? ' nb-time-pop__input--invalid' : ''}`}
            data-testid="nb-timerange-from"
            value={fromText}
            onChange={e => setFromText(e.target.value)}
          />
          <input
            type="text"
            inputMode="numeric"
            placeholder="HH:MM"
            aria-label="to"
            aria-invalid={to === null || !valid ? true : undefined}
            className={`nb-time-pop__input${to === null || !valid ? ' nb-time-pop__input--invalid' : ''}`}
            data-testid="nb-timerange-to"
            value={toText}
            onChange={e => setToText(e.target.value)}
          />
          <span className="nb-time-pop__tz" title="America/New_York wall clock">ET</span>
        </div>
        {!valid && <div className="nb-time-pop__help">Use HH:MM, from before to</div>}
        <div className="nb-time-strip" ref={stripRef}>
          <div className="nb-time-strip__track" />
          {valid && (
            <div className="nb-time-strip__bar" style={{ left: `${barLeft}%`, width: `${Math.max(0, barRight - barLeft)}%` }} />
          )}
          {from !== null && from < SESSION_OPEN && <span className="nb-time-strip__clip" style={{ left: -10 }}>◂</span>}
          {to !== null && to > SESSION_CLOSE && <span className="nb-time-strip__clip" style={{ right: -10 }}>▸</span>}
          {grip('from')}
          {grip('to')}
          {TICKS.map(t => (
            <span key={t} className="nb-time-strip__tick" style={{ left: `${stripPct(t)}%` }}>{formatHHMM(t)}</span>
          ))}
        </div>
        <div className="nb-time-pop__presets">
          {PRESETS.map(p => (
            <button
              key={p.label}
              type="button"
              className="nb-time-pop__preset"
              onClick={() => { setFromText(formatHHMM(p.from)); setToText(formatHHMM(p.to)) }}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="nb-time-pop__footer">
          <button type="button" className="nb-btn" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="nb-btn nb-btn--primary"
            data-testid="nb-timerange-apply"
            disabled={!valid}
            title={valid ? undefined : 'Use HH:MM, from before to'}
            onClick={apply}
          >
            Apply
          </button>
        </div>
      </div>
    </Popover>
  )
}

export default TimeRangeInput
