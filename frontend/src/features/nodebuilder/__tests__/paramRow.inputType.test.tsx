/**
 * ParamRow numeric input type (F435 W1 item 1.G, critic 35, F278).
 *
 * Every numeric param in the catalog renders as type="text"
 * inputMode="decimal", never type="number": a number input swallows text it
 * cannot parse, so the red invalid state could never show.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { ParamRows } from '../nodes/ParamRow'
import { NODE_CATALOG } from '../catalog'

afterEach(() => cleanup())

const numericEntries = NODE_CATALOG
  .map(entry => ({
    entry,
    keys: Object.entries(entry.paramTypes ?? {})
      .filter(([, spec]) => spec.type === 'number')
      .map(([key]) => key),
  }))
  .filter(({ keys }) => keys.length > 0)

describe('ParamRow numeric inputs render type="text"', () => {
  it('the catalog has numeric params to check', () => {
    expect(numericEntries.length).toBeGreaterThan(0)
  })

  it.each(numericEntries.map(({ entry, keys }) => [entry.name, entry, keys] as const))(
    '%s',
    (_name, entry, keys) => {
      const { getByTestId } = render(
        <ParamRows nodeId="n_x" params={entry.defaults.params} paramTypes={entry.paramTypes} />,
      )
      for (const key of keys) {
        const input = getByTestId(`nb-param-n_x-${key}`)
        expect(input.tagName).toBe('INPUT')
        expect(input.getAttribute('type')).toBe('text')
        expect(input.getAttribute('inputmode')).toBe('decimal')
      }
    },
  )

  it('a plain number value with no catalog spec is also type="text" inputMode="decimal"', () => {
    const { getByTestId } = render(<ParamRows nodeId="n_y" params={{ period: 14 }} />)
    const input = getByTestId('nb-param-n_y-period')
    expect(input.getAttribute('type')).toBe('text')
    expect(input.getAttribute('inputmode')).toBe('decimal')
  })
})
