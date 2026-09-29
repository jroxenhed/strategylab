/**
 * TabMenu tests (F435 Wave 0 item 0.C): renders nothing while closed, opens
 * at the given screen point, focuses search, closes on an outside press, and
 * hides catalog entries that compile does not act on.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import TabMenu, { type TabMenuProps } from '../TabMenu'
import { NODE_CATALOG } from '../catalog'
import { friendlyName } from '../search'

function props(extra: Partial<TabMenuProps> = {}): TabMenuProps {
  return {
    open: true,
    screenPosition: { x: 120, y: 140 },
    graphPosition: { x: 0, y: 0 },
    selectedNodeId: null,
    autoWire: true,
    onToggleAutoWire: vi.fn(),
    onCreate: vi.fn(),
    onClose: vi.fn(),
    ...extra,
  }
}

afterEach(() => cleanup())

describe('TabMenu', () => {
  it('renders nothing while closed', () => {
    render(<TabMenu {...props({ open: false })} />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens at the given screen point, in document.body, with search focused', () => {
    const { container } = render(<TabMenu {...props()} />)
    const menu = screen.getByRole('dialog')
    // Portalled out of the render container, so app panels cannot cover it.
    expect(container.contains(menu)).toBe(false)
    expect(document.body.contains(menu)).toBe(true)
    expect(menu.style.left).toBe('120px')
    expect(menu.style.top).toBe('140px')
    expect(document.activeElement).toBe(screen.getByPlaceholderText('Search nodes…'))
  })

  it('closes on a press outside the menu but not inside it', () => {
    const p = props()
    render(<TabMenu {...p} />)
    fireEvent.pointerDown(screen.getByPlaceholderText('Search nodes…'))
    expect(p.onClose).not.toHaveBeenCalled()
    const outside = document.createElement('div')
    document.body.append(outside)
    fireEvent.pointerDown(outside)
    expect(p.onClose).toHaveBeenCalled()
    outside.remove()
  })

  it('closes on Escape', () => {
    const p = props()
    render(<TabMenu {...p} />)
    fireEvent.keyDown(screen.getByPlaceholderText('Search nodes…'), { key: 'Escape' })
    expect(p.onClose).toHaveBeenCalled()
  })

  it('does not offer entries that compile ignores', () => {
    const stubs = NODE_CATALOG.filter(e => !e.compileActive)
    render(<TabMenu {...props()} />)
    const input = screen.getByPlaceholderText('Search nodes…')
    for (const stub of stubs) {
      fireEvent.change(input, { target: { value: stub.name } })
      // Search rows show each hit's description; the stub's must be absent.
      expect(screen.getByRole('dialog').textContent).not.toContain(stub.desc)
    }
  })

  it('does not list the stubs when browsing categories either', () => {
    const stubs = NODE_CATALOG.filter(e => !e.compileActive)
    render(<TabMenu {...props()} />)
    const input = screen.getByPlaceholderText('Search nodes…')
    // Walk every category with the arrow keys and read the node column.
    for (let i = 0; i < 8; i++) {
      const text = screen.getByRole('dialog').textContent ?? ''
      for (const stub of stubs) {
        if (stub.defaults.subtitle) expect(text).not.toContain(stub.defaults.subtitle)
      }
      fireEvent.keyDown(input, { key: 'ArrowDown' })
    }
    expect(friendlyName('rsi')).toBe('RSI')
  })

  it('creates the Enter-focused search hit and closes', () => {
    const p = props()
    render(<TabMenu {...p} />)
    const input = screen.getByPlaceholderText('Search nodes…')
    fireEvent.change(input, { target: { value: 'rsi' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(p.onCreate).toHaveBeenCalledTimes(1)
    const [entry] = (p.onCreate as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(entry.compileActive).toBe(true)
    expect(p.onClose).toHaveBeenCalled()
  })
})
