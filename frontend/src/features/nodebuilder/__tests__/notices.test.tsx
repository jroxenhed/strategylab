/**
 * Notice banners (F435 W1 item 1.F, surface S07): one banner per key,
 * ok/info close after 6 s unless hovered, sticky ones and errors stay,
 * derived banners show under pushed ones.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import NoticeStack from '../NoticeStack'
import { clearNotices, pushNotice } from '../notices'

beforeEach(() => {
  clearNotices()
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('NoticeStack', () => {
  it('renders one banner for two pushes with the same key', () => {
    render(<NoticeStack />)
    act(() => {
      pushNotice({ key: 'import_ok', severity: 'ok', text: 'Imported "a".' })
      pushNotice({ key: 'import_ok', severity: 'ok', text: 'Imported "b".' })
    })
    const banners = screen.getAllByTestId('nb-banner-import_ok')
    expect(banners).toHaveLength(1)
    expect(banners[0]).toHaveTextContent('Imported "b".')
  })

  it('closes an ok banner after 6 s unless hovered', () => {
    render(<NoticeStack />)
    act(() => pushNotice({ key: 'saved_copy', severity: 'ok', text: 'Saved as "x".' }))
    const banner = screen.getByTestId('nb-banner-saved_copy')
    fireEvent.mouseEnter(banner)
    act(() => vi.advanceTimersByTime(7000))
    expect(screen.getByTestId('nb-banner-saved_copy')).toBeInTheDocument()
    fireEvent.mouseLeave(banner)
    act(() => vi.advanceTimersByTime(6000))
    expect(screen.queryByTestId('nb-banner-saved_copy')).toBeNull()
  })

  it('keeps errors and sticky banners, and ✕ calls onDismiss', () => {
    const onDismiss = vi.fn()
    render(<NoticeStack />)
    act(() => {
      pushNotice({ key: 'server_error', severity: 'error', text: 'boom' })
      pushNotice({ key: 'draft_found', severity: 'info', sticky: true, text: 'draft', onDismiss })
    })
    act(() => vi.advanceTimersByTime(60_000))
    expect(screen.getByTestId('nb-banner-server_error')).toHaveAttribute('role', 'alert')
    const draft = screen.getByTestId('nb-banner-draft_found')
    expect(draft).toHaveAttribute('role', 'status')
    fireEvent.click(draft.querySelector('[data-testid="nb-banner-dismiss"]')!)
    expect(onDismiss).toHaveBeenCalled()
    expect(screen.queryByTestId('nb-banner-draft_found')).toBeNull()
  })

  it('shows at most 3 and collapses the rest', () => {
    render(
      <NoticeStack
        extra={[
          { key: 'run_error', severity: 'warn', text: 'r' },
          { key: 'unsupported_nodes', severity: 'error', text: 'u' },
        ]}
      />,
    )
    act(() => {
      pushNotice({ key: 'a', severity: 'error', text: 'a' })
      pushNotice({ key: 'b', severity: 'error', text: 'b' })
    })
    expect(screen.getByTestId('nb-notices').querySelectorAll('.nb-banner')).toHaveLength(3)
    fireEvent.click(screen.getByText('+1 more notice'))
    expect(screen.getByTestId('nb-notices').querySelectorAll('.nb-banner')).toHaveLength(4)
  })

  it('an expanded stack collapses again after 10 s without pointer contact (UX-10)', () => {
    render(
      <NoticeStack
        extra={[
          { key: 'run_error', severity: 'warn', text: 'r' },
          { key: 'unsupported_nodes', severity: 'error', text: 'u' },
        ]}
      />,
    )
    act(() => {
      pushNotice({ key: 'a', severity: 'error', text: 'a' })
      pushNotice({ key: 'b', severity: 'error', text: 'b' })
    })
    const stack = screen.getByTestId('nb-notices')
    const count = () => stack.querySelectorAll('.nb-banner').length
    // Keyboard expand (no pointer): the 10 s start at once.
    fireEvent.click(screen.getByText('+1 more notice'), { detail: 0 })
    expect(count()).toBe(4)
    act(() => { vi.advanceTimersByTime(9_999) })
    expect(count()).toBe(4)
    act(() => { vi.advanceTimersByTime(1) })
    expect(count()).toBe(3)
    // Pointer on the stack holds it open; leaving restarts the 10 s.
    fireEvent.click(screen.getByText('+1 more notice'), { detail: 1 })
    fireEvent.pointerEnter(stack)
    act(() => { vi.advanceTimersByTime(20_000) })
    expect(count()).toBe(4)
    fireEvent.pointerLeave(stack)
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(count()).toBe(3)
  })
})
