/**
 * Popover shell tests (F435 Wave 1 item 1.S, surfaces-w1-w4.md 0.4):
 * placement (8px clamp, flip above), closes on outside press, Esc, window
 * blur and the canvas close signal, one popover at a time.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useState } from 'react'
import { Popover, placePopover, closeActivePopover, isPopoverOpen } from '../ui/Popover'

afterEach(() => cleanup())

const bounds = { left: 0, top: 0, right: 800, bottom: 600 }

describe('placePopover', () => {
  it('opens below the anchor, left edges lined up', () => {
    const p = placePopover({ anchor: { left: 100, top: 50, right: 180, bottom: 76 }, size: { width: 200, height: 100 }, bounds })
    expect(p).toEqual({ left: 100, top: 80, side: 'below' })
  })

  it('clamps inside the bounds with an 8px margin', () => {
    const right = placePopover({ anchor: { left: 750, top: 10, right: 750, bottom: 10 }, size: { width: 200, height: 100 }, bounds })
    expect(right.left).toBe(800 - 8 - 200)
    const left = placePopover({ anchor: { left: -50, top: 10, right: -50, bottom: 10 }, size: { width: 200, height: 100 }, bounds })
    expect(left.left).toBe(8)
  })

  it('flips above when it would leave the bottom', () => {
    const p = placePopover({ anchor: { left: 100, top: 550, right: 180, bottom: 576 }, size: { width: 200, height: 100 }, bounds })
    expect(p.side).toBe('above')
    expect(p.top).toBe(550 - 4 - 100)
  })

  it('when neither side fits, stays on the roomier side, inside the margin', () => {
    const p = placePopover({ anchor: { left: 100, top: 300, right: 100, bottom: 300 }, size: { width: 200, height: 590 }, bounds })
    expect(p.top).toBeGreaterThanOrEqual(8)
  })

  it('end alignment lines up right edges', () => {
    const p = placePopover({ anchor: { left: 500, top: 10, right: 600, bottom: 30 }, size: { width: 200, height: 50 }, bounds, align: 'end' })
    expect(p.left).toBe(400)
  })
})

describe('Popover', () => {
  it('closes on a pointer-down outside, but not inside or on its anchor', () => {
    const onClose = vi.fn()
    render(<button>anchor</button>)
    const anchor = screen.getByRole('button', { name: 'anchor' })
    render(
      <Popover anchor={anchor} onClose={onClose} role="menu" ariaLabel="More">
        <button role="menuitem">New</button>
      </Popover>,
    )
    fireEvent.pointerDown(screen.getByRole('menuitem'))
    fireEvent.pointerDown(anchor)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.pointerDown(document.body)
    expect(onClose).toHaveBeenCalledWith('outside')
  })

  it('closes on Esc, eats the key, and returns focus to the anchor', () => {
    const pageKey = vi.fn()
    document.addEventListener('keydown', pageKey)
    function Harness() {
      const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
      const [open, setOpen] = useState(true)
      return (
        <>
          <button ref={setAnchor}>more</button>
          {open && anchor && (
            <Popover anchor={anchor} onClose={() => setOpen(false)} role="menu" ariaLabel="More" autoFocus>
              <button role="menuitem">New</button>
            </Popover>
          )}
        </>
      )
    }
    render(<Harness />)
    expect(screen.getByRole('menuitem')).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('menuitem'), { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(screen.getByRole('button', { name: 'more' })).toHaveFocus()
    expect(pageKey).not.toHaveBeenCalled()
    document.removeEventListener('keydown', pageKey)
  })

  it('closes on window blur and on the canvas close signal', () => {
    const a = vi.fn()
    const { unmount } = render(<Popover anchor={{ x: 10, y: 10 }} onClose={a}>x</Popover>)
    fireEvent.blur(window)
    expect(a).toHaveBeenCalledWith('blur')
    unmount()
    const b = vi.fn()
    render(<Popover anchor={{ x: 10, y: 10 }} onClose={b}>y</Popover>)
    expect(isPopoverOpen()).toBe(true)
    act(() => closeActivePopover())
    expect(b).toHaveBeenCalledWith('signal')
  })

  it('only one popover is open at a time', () => {
    const first = vi.fn()
    render(<Popover anchor={{ x: 10, y: 10 }} onClose={first}>one</Popover>)
    render(<Popover anchor={{ x: 20, y: 20 }} onClose={vi.fn()}>two</Popover>)
    expect(first).toHaveBeenCalledWith('replaced')
  })

  it('moves first focus only once the panel is visible (UX-01)', () => {
    // A visibility:hidden element cannot take focus in a real browser (jsdom
    // does not care), so record the panel's visibility at each focus() call.
    const seen: string[] = []
    const realFocus = HTMLElement.prototype.focus
    const spy = vi.spyOn(HTMLElement.prototype, 'focus').mockImplementation(function (this: HTMLElement, opts?: FocusOptions) {
      const panel = this.closest('.nb-popover') as HTMLElement | null
      if (panel) seen.push(panel.style.visibility)
      realFocus.call(this, opts)
    })
    function Harness() {
      const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
      return (
        <>
          <button ref={setAnchor}>more</button>
          {anchor && (
            <Popover anchor={anchor} onClose={() => {}} role="menu" ariaLabel="More" autoFocus>
              <button role="menuitem">New</button>
            </Popover>
          )}
        </>
      )
    }
    render(<Harness />)
    spy.mockRestore()
    expect(seen.length).toBeGreaterThan(0)
    expect(seen).not.toContain('hidden')
    expect(screen.getByRole('menuitem')).toHaveFocus()
  })

  it('a menu closes on Tab and hands focus back to its anchor first (UX-08)', () => {
    function Harness() {
      const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
      const [open, setOpen] = useState(true)
      return (
        <>
          <button ref={setAnchor}>more</button>
          {open && anchor && (
            <Popover anchor={anchor} onClose={() => setOpen(false)} role="menu" ariaLabel="More" autoFocus>
              <button role="menuitem">New</button>
            </Popover>
          )}
        </>
      )
    }
    render(<Harness />)
    const item = screen.getByRole('menuitem')
    expect(item).toHaveFocus()
    // Not prevented: the Tab then moves on from the anchor.
    expect(fireEvent.keyDown(item, { key: 'Tab' })).toBe(true)
    expect(screen.queryByRole('menu')).toBeNull()
    expect(screen.getByRole('button', { name: 'more' })).toHaveFocus()
  })

  it('renders in a nodebuilder-root portal with its role', () => {
    render(<Popover anchor={{ x: 10, y: 10 }} onClose={vi.fn()} role="listbox" ariaLabel="Groups" data-testid="nb-pop">z</Popover>)
    const el = screen.getByTestId('nb-pop')
    expect(el).toHaveAttribute('role', 'listbox')
    expect(el.classList.contains('nodebuilder-root')).toBe(true)
    expect(el.style.visibility).not.toBe('hidden')
  })
})
