/**
 * Time-of-day range and day-of-week widgets (F435 W2 item 2.E, spec S12).
 *
 * The range is stored as "HH:MM-HH:MM" in New York wall-clock time and is
 * committed only on Apply. The day picker stores weekday names in fixed
 * order.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { resetDiagnostics, setServerDiagnostics } from '../useDiagnostics'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import {
  TimeRangeInput,
  displayRange,
  initialRangeTexts,
  isInvalidRange,
  formatRange,
  parseHHMM,
  parseRange,
  snapMinutes,
} from '../nodes/TimeRangeInput'
import { DayOfWeekInput, NO_DAYS_MESSAGE, pickedDays, toggleDay, WEEKDAYS } from '../nodes/DayOfWeekInput'
import { ParamRows } from '../nodes/ParamRow'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

function node(id: string, type: string, params: GraphNode['params']): GraphNode {
  return { id, type, name: id, parent: null, params, position: [0, 0], display: false, bypass: false }
}

const s = useNodeBuilderStore.getState
const params = (id: string) => s().graph!.nodes[id].params

beforeEach(() => {
  const g: Graph = {
    ...emptyGraph(),
    nodes: {
      tod: node('tod', 't2_tod', { range: null, out: '@in_window' }),
      dow: node('dow', 't2_dow', { days: ['mon', 'tue', 'wed', 'thu', 'fri'], out: '@on_day' }),
    },
  }
  act(() => { s().openGraph(g, { id: null, rev: 0, name: 'test' }) })
})

afterEach(() => {
  cleanup()
  resetDiagnostics()
})

describe('time helpers', () => {
  it('parses only zero-padded 24 h times', () => {
    expect(parseHHMM('09:35')).toBe(575)
    expect(parseHHMM('9:5')).toBeNull()
    expect(parseHHMM('25:00')).toBeNull()
    expect(parseHHMM('12:60')).toBeNull()
  })

  it('a range needs from before to', () => {
    expect(parseRange('09:35-15:55')).toEqual([575, 955])
    expect(parseRange('16:00-09:30')).toBeNull()
    expect(parseRange('10:00-10:00')).toBeNull()
    expect(parseRange(null)).toBeNull()
    expect(formatRange(575, 955)).toBe('09:35-15:55')
    expect(displayRange('09:35-15:55')).toBe('09:35–15:55')
  })

  it('grips snap to 5 minutes, or 1 with Shift', () => {
    expect(snapMinutes(577, 5)).toBe(575)
    expect(snapMinutes(578, 5)).toBe(580)
    expect(snapMinutes(578, 1)).toBe(578)
  })
})

/** A range cell that follows the store. */
function Range() {
  const value = useNodeBuilderStore(st => st.graph?.nodes.tod.params.range)
  return <TimeRangeInput nodeId="tod" param="range" value={value} />
}

describe('TimeRangeInput', () => {
  it('unset shows the session; Apply stores HH:MM-HH:MM and the cell shows an en dash', () => {
    render(<Range />)
    const cell = screen.getByTestId('nb-timerange-tod')
    expect(cell.textContent).toBe('session ▾')
    act(() => { fireEvent.click(cell) })
    act(() => { fireEvent.change(screen.getByTestId('nb-timerange-from'), { target: { value: '09:35' } }) })
    act(() => { fireEvent.change(screen.getByTestId('nb-timerange-to'), { target: { value: '15:55' } }) })
    const before = s().past.length
    act(() => { fireEvent.click(screen.getByTestId('nb-timerange-apply')) })
    expect(params('tod').range).toBe('09:35-15:55')
    expect(s().past.length).toBe(before + 1)
    expect(screen.getByTestId('nb-timerange-tod').textContent).toBe('09:35–15:55')
  })

  it('Apply is disabled for 16:00 to 09:30 and for a bad time', () => {
    render(<Range />)
    act(() => { fireEvent.click(screen.getByTestId('nb-timerange-tod')) })
    act(() => { fireEvent.change(screen.getByTestId('nb-timerange-from'), { target: { value: '16:00' } }) })
    act(() => { fireEvent.change(screen.getByTestId('nb-timerange-to'), { target: { value: '09:30' } }) })
    expect((screen.getByTestId('nb-timerange-apply') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Use HH:MM, from before to')).toBeInTheDocument()
    act(() => { fireEvent.change(screen.getByTestId('nb-timerange-from'), { target: { value: '9:5' } }) })
    expect(screen.getByTestId('nb-timerange-from').getAttribute('aria-invalid')).toBe('true')
  })

  it('a grip moves 5 minutes with the arrow keys and nothing commits until Apply', () => {
    render(<Range />)
    act(() => { fireEvent.click(screen.getByTestId('nb-timerange-tod')) })
    const grip = screen.getByTestId('nb-timerange-grip-from')
    expect(grip.getAttribute('aria-valuetext')).toBe('09:30')
    act(() => { fireEvent.keyDown(grip, { key: 'ArrowRight' }) })
    expect(screen.getByTestId('nb-timerange-grip-from').getAttribute('aria-valuetext')).toBe('09:35')
    expect((screen.getByTestId('nb-timerange-from') as HTMLInputElement).value).toBe('09:35')
    expect(params('tod').range).toBeNull()
  })

  it('a stored range that does not parse shows raw, as an error with the server message (FP-10)', () => {
    expect(isInvalidRange('9:30-16:00')).toBe(true)
    expect(isInvalidRange('09:30-16:00')).toBe(false)
    expect(isInvalidRange(null)).toBe(false)
    expect(isInvalidRange('')).toBe(false)
    expect(initialRangeTexts('9:30-16:00')).toEqual(['9:30', '16:00'])
    expect(initialRangeTexts(null)).toEqual(['09:30', '16:00'])
    act(() => { s().updateNodeParams('tod', { range: '9:30-16:00' }) })
    const d: Diagnostic = {
      node_id: 'tod', path: null, severity: 'error', code: 'param_invalid', message: "range '9:30-16:00' is not HH:MM-HH:MM",
      param: 'range', port: null, line: null, col: null, end_line: null, end_col: null,
    }
    act(() => setServerDiagnostics([d]))
    render(<Range />)
    const cell = screen.getByTestId('nb-timerange-tod')
    expect(cell.textContent).toBe('9:30-16:00')
    expect(cell.className).toContain('nb-time-cell--invalid')
    expect(cell.getAttribute('aria-invalid')).toBe('true')
    expect(cell.title).toBe(d.message)
    // The popover opens on the bad value, so it can be fixed.
    act(() => { fireEvent.click(cell) })
    expect((screen.getByTestId('nb-timerange-from') as HTMLInputElement).value).toBe('9:30')
    expect((screen.getByTestId('nb-timerange-apply') as HTMLButtonElement).disabled).toBe(true)
  })

  it('Enter in a field applies', () => {
    render(<Range />)
    act(() => { fireEvent.click(screen.getByTestId('nb-timerange-tod')) })
    const to = screen.getByTestId('nb-timerange-to')
    act(() => { fireEvent.change(to, { target: { value: '10:30' } }) })
    act(() => { fireEvent.keyDown(to, { key: 'Enter' }) })
    expect(params('tod').range).toBe('09:30-10:30')
  })
})

describe('DayOfWeekInput', () => {
  it('keeps weekday order and drops unknown entries', () => {
    expect(pickedDays(['fri', 'mon', 'sun'], WEEKDAYS)).toEqual(['mon', 'fri'])
    expect(toggleDay(['mon', 'fri'], 'wed', WEEKDAYS)).toEqual(['mon', 'wed', 'fri'])
  })

  it('toggling M off stores tue..fri', () => {
    function Days() {
      const value = useNodeBuilderStore(st => st.graph?.nodes.dow.params.days)
      return <DayOfWeekInput nodeId="dow" param="days" value={value} />
    }
    render(<Days />)
    const mon = screen.getByTestId('nb-day-mon')
    expect(mon.getAttribute('aria-checked')).toBe('true')
    expect(mon.getAttribute('aria-label')).toBe('Monday')
    act(() => { fireEvent.click(mon) })
    expect(params('dow').days).toEqual(['tue', 'wed', 'thu', 'fri'])
    expect(screen.getByTestId('nb-day-mon').getAttribute('aria-checked')).toBe('false')
  })

  it('all off warns that the signal is always false', () => {
    render(<DayOfWeekInput nodeId="dow" param="days" value={[]} />)
    expect(screen.getByRole('group').title).toBe(NO_DAYS_MESSAGE)
  })
})

describe('ParamRows picks the widgets from the spec', () => {
  it('a time_range param and a list-valued select', () => {
    render(<>
      <ParamRows nodeId="tod" params={params('tod') as Record<string, unknown>} />
      <ParamRows nodeId="dow" params={params('dow') as Record<string, unknown>} />
    </>)
    expect(screen.getByTestId('nb-timerange-tod')).toBeInTheDocument()
    expect(screen.getByTestId('nb-day-wed')).toBeInTheDocument()
  })
})
