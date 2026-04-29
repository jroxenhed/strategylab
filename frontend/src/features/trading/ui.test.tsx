/**
 * Tests for shared UI primitives in ui.tsx:
 *   - StatCell
 *   - btnStyle
 *   - CARD_COLUMN_FLEX
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { StatCell, btnStyle, CARD_COLUMN_FLEX } from './ui'

// ---------------------------------------------------------------------------
// CARD_COLUMN_FLEX
// ---------------------------------------------------------------------------

describe('CARD_COLUMN_FLEX', () => {
  it('has the expected CSS flex shorthand value', () => {
    expect(CARD_COLUMN_FLEX).toBe('1 1 50%')
  })
})

// ---------------------------------------------------------------------------
// StatCell
// ---------------------------------------------------------------------------

describe('StatCell', () => {
  it('renders the label text', () => {
    render(createElement(StatCell, { label: 'Allocated', value: '$1,000' }))
    expect(screen.getByText('Allocated')).toBeInTheDocument()
  })

  it('renders a string value', () => {
    render(createElement(StatCell, { label: 'Trades', value: '42' }))
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('renders a ReactNode value', () => {
    const value = createElement('span', { 'data-testid': 'inner' }, 'hello')
    render(createElement(StatCell, { label: 'P&L', value }))
    expect(screen.getByTestId('inner')).toBeInTheDocument()
    expect(screen.getByText('hello')).toBeInTheDocument()
  })

  it('label is uppercase via CSS', () => {
    const { container } = render(createElement(StatCell, { label: 'Status', value: 'running' }))
    const labelEl = container.querySelector('span')
    expect(labelEl?.style.textTransform).toBe('uppercase')
  })

  it('renders both label and value in a column flex container', () => {
    const { container } = render(createElement(StatCell, { label: 'Lbl', value: 'Val' }))
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.style.flexDirection).toBe('column')
  })
})

// ---------------------------------------------------------------------------
// btnStyle
// ---------------------------------------------------------------------------

describe('btnStyle', () => {
  it('uses the supplied background when not disabled', () => {
    const style = btnStyle('#1e3a5f')
    expect(style.background).toBe('#1e3a5f')
    expect(style.color).toBe('#ccc')
    expect(style.cursor).toBe('pointer')
  })

  it('overrides background to #1a1a1a when disabled', () => {
    const style = btnStyle('#1e3a5f', true)
    expect(style.background).toBe('#1a1a1a')
  })

  it('sets color to #444 when disabled', () => {
    const style = btnStyle('#1e3a5f', true)
    expect(style.color).toBe('#444')
  })

  it('sets cursor to not-allowed when disabled', () => {
    const style = btnStyle('#1e3a5f', true)
    expect(style.cursor).toBe('not-allowed')
  })

  it('defaults to enabled (disabled = false)', () => {
    const style = btnStyle('#abc')
    expect(style.cursor).toBe('pointer')
    expect(style.background).toBe('#abc')
  })

  it('returns expected static properties regardless of disabled state', () => {
    for (const disabled of [true, false]) {
      const style = btnStyle('#000', disabled)
      expect(style.border).toBe('1px solid #2a3040')
      expect(style.borderRadius).toBe(4)
      expect(style.fontSize).toBe(12)
    }
  })
})
