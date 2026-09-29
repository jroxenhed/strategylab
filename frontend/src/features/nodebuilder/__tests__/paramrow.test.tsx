/**
 * ParamRow tests (F435 Wave 0, item 0.D).
 *
 * Covers:
 * - F278 trap: numeric inputs render type="text" inputMode="decimal", never type="number".
 * - Bug 7, the Cmd+Z desync guard: an input event that reaches an unfocused
 *   field (what a native undo can do) is ignored, and the field keeps showing
 *   the store value; a blur after an undo while focused commits what the field
 *   shows; the field and the store agree after every blur.
 * - Bug 22: no React warning from mixing `border` and `borderColor`.
 *
 * The harness reads the param from the real store and passes it to ParamRow,
 * the same way the node renderers do.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useNodeBuilderStore } from '../store'
import { ParamRow } from '../nodes/ParamRow'
import type { Graph } from '../../../api/nodebuilder'
import type { ParamTypeSpec } from '../catalog'

const NODE_ID = 'rsi_1'

function seedGraph(params: Record<string, unknown>) {
  const graph: Graph = {
    _version: 1,
    readOnly: false,
    nodes: {
      [NODE_ID]: {
        id: NODE_ID,
        type: 'rsi',
        params,
        position: [0, 0],
        display: false,
        bypass: false,
      },
    },
    wires: [],
  }
  act(() => { useNodeBuilderStore.setState({ graph }) })
}

function storeParam(key: string): unknown {
  return useNodeBuilderStore.getState().graph?.nodes[NODE_ID]?.params[key]
}

/** Change a param in the store from outside the field (like a graph undo would). */
function setStoreParam(key: string, value: unknown) {
  act(() => { useNodeBuilderStore.getState().updateNodeParams(NODE_ID, { [key]: value }) })
}

function Harness({ paramKey, typeSpec }: { paramKey: string; typeSpec?: ParamTypeSpec }) {
  const value = useNodeBuilderStore(s => s.graph?.nodes[NODE_ID]?.params[paramKey])
  return <ParamRow nodeId={NODE_ID} paramKey={paramKey} value={value} typeSpec={typeSpec} />
}

function renderRow(paramKey: string, typeSpec?: ParamTypeSpec) {
  render(<Harness paramKey={paramKey} typeSpec={typeSpec} />)
  return screen.getByLabelText(paramKey) as HTMLInputElement
}

beforeEach(() => {
  seedGraph({ period: 14, label: 'fast' })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ParamRow input type (F278)', () => {
  it('renders numeric params as type="text" inputMode="decimal"', () => {
    const input = renderRow('period', { type: 'number' })
    expect(input.getAttribute('type')).toBe('text')
    expect(input.getAttribute('inputMode') ?? input.getAttribute('inputmode')).toBe('decimal')
    expect(input.getAttribute('type')).not.toBe('number')
  })

  it('infers a number from the value and still uses type="text"', () => {
    const input = renderRow('period')
    expect(input.getAttribute('type')).toBe('text')
    expect(input.getAttribute('inputmode')).toBe('decimal')
  })

  it('renders string params as type="text" inputMode="text"', () => {
    const input = renderRow('label')
    expect(input.getAttribute('type')).toBe('text')
    expect(input.getAttribute('inputmode')).toBe('text')
  })
})

describe('ParamRow commit on blur', () => {
  it('commits a typed number to the store on blur', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.type(input, '20')
    await user.tab()
    expect(storeParam('period')).toBe(20)
    expect(input.value).toBe('20')
  })

  it('shows the stored number after blur ("20.0" becomes "20")', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.type(input, '20.0')
    await user.tab()
    expect(storeParam('period')).toBe(20)
    expect(input.value).toBe('20')
  })

  it('Escape reverts to the store value and commits nothing', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.type(input, '99')
    await user.keyboard('{Escape}')
    expect(storeParam('period')).toBe(14)
    expect(input.value).toBe('14')
  })

  it('empty numeric input silently reverts (F275)', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.tab()
    expect(storeParam('period')).toBe(14)
    expect(input.value).toBe('14')
  })
})

describe('ParamRow Cmd+Z desync guard (bug 7)', () => {
  it('ignores an input event on an unfocused field and keeps showing the store value', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    // Real edit: 14 -> 20, committed on blur.
    await user.clear(input)
    await user.type(input, '20')
    await user.tab()
    expect(storeParam('period')).toBe(20)
    expect(document.activeElement).not.toBe(input)

    // A native undo reaching the unfocused field writes the old text back.
    fireEvent.input(input, { target: { value: '14' } })
    expect(input.value).toBe('20')
    expect(storeParam('period')).toBe(20)

    // The next focus + blur must not commit the stale "14".
    await user.click(input)
    await user.tab()
    expect(storeParam('period')).toBe(20)
    expect(input.value).toBe('20')
  })

  it('a blur after a native undo while focused commits what the field shows', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.type(input, '20')
    await user.tab()
    expect(storeParam('period')).toBe(20)

    // Focus the field, then the browser's undo puts "14" back in it.
    await user.click(input)
    expect(document.activeElement).toBe(input)
    fireEvent.input(input, { target: { value: '14' } })
    expect(input.value).toBe('14')

    await user.tab()
    expect(storeParam('period')).toBe(14)
    expect(input.value).toBe('14')
  })

  it('re-syncs from the store when the store changes while the field is not focused', () => {
    const input = renderRow('period', { type: 'number' })
    setStoreParam('period', 30)
    expect(input.value).toBe('30')
  })

  it('keeps the user text while focused, then re-syncs on blur if nothing was typed', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.click(input)
    setStoreParam('period', 30)
    // Focused: the field is not yanked away from under the user.
    expect(input.value).toBe('14')
    await user.tab()
    // Nothing typed, so the blur must not push the old "14" over the new 30.
    expect(storeParam('period')).toBe(30)
    expect(input.value).toBe('30')
  })

  it('typed text wins over a store change that lands while the field is focused', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.click(input)
    await user.clear(input)
    await user.type(input, '21')
    setStoreParam('period', 30)
    expect(input.value).toBe('21')
    await user.tab()
    expect(storeParam('period')).toBe(21)
    expect(input.value).toBe('21')
  })

  it('an invalid value stays visible and red, and a later store change clears it', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.type(input, 'abc')
    await user.tab()
    expect(storeParam('period')).toBe(14)
    expect(input.value).toBe('abc')
    expect(input.title).toBe('Must be a number')

    setStoreParam('period', 15)
    expect(input.value).toBe('15')
    expect(input.title).toBe('')
  })
})

describe('ParamRow styling (bug 22)', () => {
  it('switching to the invalid state logs no React style warning', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    await user.clear(input)
    await user.type(input, 'abc')
    await user.tab()
    expect(input.title).toBe('Must be a number')
    // Back to valid, which re-renders the style the other way.
    await user.clear(input)
    await user.type(input, '12')
    await user.tab()
    const messages = [...errorSpy.mock.calls, ...warnSpy.mock.calls].map(c => c.map(String).join(' '))
    expect(messages.filter(m => /border|conflicting property/i.test(m))).toEqual([])
  })

  it('the invalid state overrides borderColor only, on top of longhand borders', async () => {
    const user = userEvent.setup()
    const input = renderRow('period', { type: 'number' })
    expect(input.style.borderStyle).toBe('solid')
    await user.clear(input)
    await user.type(input, 'abc')
    await user.tab()
    expect(input.style.borderStyle).toBe('solid')
    expect(input.style.borderColor).toContain('--nb-cat-rules')
  })
})
