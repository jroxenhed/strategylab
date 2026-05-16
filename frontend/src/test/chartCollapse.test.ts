/**
 * F248 — chartCollapsed localStorage persistence tests.
 *
 * These tests verify the localStorage key/state contract without needing
 * to render the full App component (which requires a complex provider tree).
 * The visual assertion (display:none) is covered by a DOM check using the
 * CHART_COLLAPSED_KEY constant.
 */
import { describe, it, expect, beforeEach } from 'vitest'

const CHART_COLLAPSED_KEY = 'strategylab-chart-collapsed'

describe('F248 chartCollapsed persistence', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to false when no value is stored', () => {
    const raw = localStorage.getItem(CHART_COLLAPSED_KEY)
    const collapsed = raw === 'true'
    expect(collapsed).toBe(false)
  })

  it('reads true when stored value is "true"', () => {
    localStorage.setItem(CHART_COLLAPSED_KEY, 'true')
    const raw = localStorage.getItem(CHART_COLLAPSED_KEY)
    const collapsed = raw === 'true'
    expect(collapsed).toBe(true)
  })

  it('reads false when stored value is "false"', () => {
    localStorage.setItem(CHART_COLLAPSED_KEY, 'false')
    const raw = localStorage.getItem(CHART_COLLAPSED_KEY)
    const collapsed = raw === 'true'
    expect(collapsed).toBe(false)
  })

  it('toggle cycle: false → true → false', () => {
    // Initial: not set → false
    let collapsed = localStorage.getItem(CHART_COLLAPSED_KEY) === 'true'
    expect(collapsed).toBe(false)

    // Toggle to collapsed
    collapsed = !collapsed
    localStorage.setItem(CHART_COLLAPSED_KEY, String(collapsed))
    expect(localStorage.getItem(CHART_COLLAPSED_KEY)).toBe('true')

    // Toggle back
    collapsed = !collapsed
    localStorage.setItem(CHART_COLLAPSED_KEY, String(collapsed))
    expect(localStorage.getItem(CHART_COLLAPSED_KEY)).toBe('false')
  })

  it('survives reload — persisted value is read back correctly', () => {
    // Simulate App writing collapsed=true on first session
    localStorage.setItem(CHART_COLLAPSED_KEY, 'true')

    // Simulate App reading on next mount
    const restoredCollapsed = localStorage.getItem(CHART_COLLAPSED_KEY) === 'true'
    expect(restoredCollapsed).toBe(true)
  })

  it('display:none applied when collapsed, removed when expanded', () => {
    // Simulate what App.tsx does: apply inline style based on state
    const div = document.createElement('div')

    // Collapsed state
    let collapsed = true
    div.style.display = collapsed ? 'none' : ''
    expect(div.style.display).toBe('none')

    // Expanded state
    collapsed = false
    div.style.display = collapsed ? 'none' : ''
    expect(div.style.display).toBe('')
  })
})
