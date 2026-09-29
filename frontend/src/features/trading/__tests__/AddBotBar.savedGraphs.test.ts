/**
 * F435 0.F: AddBotBar must never crash on the saved-graphs localStorage key.
 *
 * The node builder store writes `{ name: Graph }`; older code wrote an array.
 * Both shapes load, and garbage loads as an empty list. A throw here blanks
 * the whole app, because Live Trading is the landing tab.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { createElement } from 'react'
import AddBotBar, { parseSavedGraphs } from '../AddBotBar'

const KEY = 'strategylab-saved-graphs'
const graph = { _version: 1, readOnly: false, nodes: {}, wires: [] }

describe('parseSavedGraphs', () => {
  it('reads the object map the node builder store writes', () => {
    const raw = JSON.stringify({ 'RSI dip': graph, 'MACD cross': graph })
    expect(parseSavedGraphs(raw)).toEqual([
      { name: 'RSI dip', graph },
      { name: 'MACD cross', graph },
    ])
  })

  it('reads the legacy array shape', () => {
    const raw = JSON.stringify([{ name: 'Old one', graph }])
    expect(parseSavedGraphs(raw)).toEqual([{ name: 'Old one', graph }])
  })

  it('drops broken entries in either shape', () => {
    expect(parseSavedGraphs(JSON.stringify({ good: graph, bad: 5, nul: null, arr: [] })))
      .toEqual([{ name: 'good', graph }])
    expect(parseSavedGraphs(JSON.stringify([
      { name: 'good', graph },
      { name: 7, graph },
      { name: 'no graph' },
      null,
      'text',
    ]))).toEqual([{ name: 'good', graph }])
  })

  it.each([
    ['null', null],
    ['empty string', ''],
    ['invalid JSON', '{not json'],
    ['a number', '42'],
    ['a string', '"hello"'],
    ['JSON null', 'null'],
    ['a boolean', 'true'],
  ])('treats %s as empty and does not throw', (_label, raw) => {
    expect(() => parseSavedGraphs(raw as string | null)).not.toThrow()
    expect(parseSavedGraphs(raw as string | null)).toEqual([])
  })
})

describe('AddBotBar with saved graphs in localStorage', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => { cleanup(); localStorage.clear() })

  const renderBar = () =>
    render(createElement(AddBotBar, { fund: null, onAdd: () => {} }))

  const openGraphMode = () => fireEvent.click(screen.getByText('Graph'))

  it('renders the object shape and lists each graph by name', () => {
    localStorage.setItem(KEY, JSON.stringify({ 'RSI dip': graph }))
    renderBar()
    openGraphMode()
    expect(screen.getByRole('option', { name: 'RSI dip' })).toBeTruthy()
  })

  it('renders the array shape and lists each graph by name', () => {
    localStorage.setItem(KEY, JSON.stringify([{ name: 'Old one', graph }]))
    renderBar()
    openGraphMode()
    expect(screen.getByRole('option', { name: 'Old one' })).toBeTruthy()
  })

  it.each([['invalid JSON', '{oops'], ['a number', '42'], ['JSON null', 'null']])(
    'renders with %s stored, showing no graphs',
    (_label, raw) => {
      localStorage.setItem(KEY, raw)
      expect(() => renderBar()).not.toThrow()
      openGraphMode()
      expect(screen.getByRole('option', { name: 'Select graph…' })).toBeTruthy()
      expect(screen.getAllByRole('option').map(o => o.textContent)).not.toContain('undefined')
    },
  )

  it('renders when saved strategies are not an array', () => {
    localStorage.setItem('strategylab-saved-strategies', JSON.stringify({ a: 1 }))
    expect(() => renderBar()).not.toThrow()
  })
})
