/**
 * Dialog shell tests (F435 Wave 1 item 1.S, surfaces-w1-w4.md 0.3): role and
 * aria wiring, initial focus, focus trap, Esc = Cancel, Enter = primary
 * (not in a textarea, not on a button), focus back to the opener, stacking.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { useState } from 'react'
import { Dialog, PRIMARY_ATTR } from '../ui/Dialog'
import { Button } from '../ui/Button'
import { Switch } from '../ui/Switch'

afterEach(() => cleanup())

describe('Dialog', () => {
  it('is a labelled modal dialog in a nodebuilder-root portal', () => {
    render(<Dialog title="Delete graph?" onCancel={vi.fn()} primaryLabel="Delete" danger />)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('Delete graph?')
    expect(dialog.closest('.nodebuilder-root')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Delete' }).className).toContain('nb-btn--danger')
  })

  it('focuses the first input, else the primary', () => {
    render(
      <Dialog title="Save as" onCancel={vi.fn()} primaryLabel="Save">
        <input aria-label="Name" />
      </Dialog>,
    )
    expect(screen.getByLabelText('Name')).toHaveFocus()
    cleanup()
    render(<Dialog title="Delete?" onCancel={vi.fn()} primaryLabel="Delete" danger />)
    expect(screen.getByRole('button', { name: 'Delete' })).toHaveFocus()
  })

  it('traps Tab and Shift+Tab inside the dialog', () => {
    render(
      <Dialog title="Save as" onCancel={vi.fn()} primaryLabel="Save">
        <input aria-label="Name" />
      </Dialog>,
    )
    const close = screen.getByRole('button', { name: 'Close' })
    const save = screen.getByRole('button', { name: 'Save' })
    // Focusable order: Close, Name, Cancel, Save.
    save.focus()
    fireEvent.keyDown(save, { key: 'Tab' })
    expect(close).toHaveFocus()
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
    expect(save).toHaveFocus()
  })

  it('pulls a Tab from outside the dialog back in, and hides it from the page', () => {
    const pageKey = vi.fn()
    document.addEventListener('keydown', pageKey)
    render(<Dialog title="Confirm" onCancel={vi.fn()} primaryLabel="OK" />)
    ;(document.activeElement as HTMLElement).blur()
    fireEvent.keyDown(document.body, { key: 'Tab' })
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    fireEvent.keyDown(document.body, { key: 'Delete' })
    expect(pageKey).not.toHaveBeenCalled()
    document.removeEventListener('keydown', pageKey)
  })

  it('Esc calls onCancel, and so does the close button', () => {
    const onCancel = vi.fn()
    render(<Dialog title="Save as" onCancel={onCancel} primaryLabel="Save"><input aria-label="Name" /></Dialog>)
    fireEvent.keyDown(screen.getByLabelText('Name'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onCancel).toHaveBeenCalledTimes(2)
  })

  it('Enter in an input presses the primary; not when disabled', () => {
    const onPrimary = vi.fn()
    const { rerender } = render(
      <Dialog title="Save as" onCancel={vi.fn()} primaryLabel="Save" onPrimary={onPrimary}>
        <input aria-label="Name" />
      </Dialog>,
    )
    fireEvent.keyDown(screen.getByLabelText('Name'), { key: 'Enter' })
    expect(onPrimary).toHaveBeenCalledTimes(1)
    rerender(
      <Dialog title="Save as" onCancel={vi.fn()} primaryLabel="Save" onPrimary={onPrimary} primaryDisabled>
        <input aria-label="Name" />
      </Dialog>,
    )
    fireEvent.keyDown(screen.getByLabelText('Name'), { key: 'Enter' })
    expect(onPrimary).toHaveBeenCalledTimes(1)
  })

  it('Enter in a textarea or on another button does not press the primary', () => {
    const onPrimary = vi.fn()
    render(
      <Dialog title="Notes" onCancel={vi.fn()} primaryLabel="Save" onPrimary={onPrimary}>
        <textarea aria-label="Description" />
      </Dialog>,
    )
    fireEvent.keyDown(screen.getByLabelText('Description'), { key: 'Enter' })
    fireEvent.keyDown(screen.getByRole('button', { name: 'Cancel' }), { key: 'Enter' })
    expect(onPrimary).not.toHaveBeenCalled()
  })

  it('Enter already handled by an inner widget is left alone', () => {
    const onPrimary = vi.fn()
    render(
      <Dialog title="Graphs" onCancel={vi.fn()} primaryLabel="Open" onPrimary={onPrimary}>
        <input aria-label="Search" onKeyDown={e => { if (e.key === 'Enter') e.preventDefault() }} />
      </Dialog>,
    )
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    expect(onPrimary).not.toHaveBeenCalled()
  })

  it('a custom footer marks its primary for Enter', () => {
    const onOverwrite = vi.fn()
    render(
      <Dialog
        title="Saved elsewhere"
        onCancel={vi.fn()}
        footer={<>
          <Button>Save as copy</Button>
          <Button kind="danger" onClick={onOverwrite} {...{ [PRIMARY_ATTR]: '' }}>Overwrite</Button>
        </>}
      >
        <input aria-label="x" />
      </Dialog>,
    )
    fireEvent.keyDown(screen.getByLabelText('x'), { key: 'Enter' })
    expect(onOverwrite).toHaveBeenCalledTimes(1)
  })

  it('gives focus back to the opener on close', () => {
    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>Open dialog</button>
          {open && <Dialog title="Hi" onCancel={() => setOpen(false)} primaryLabel="OK" />}
        </>
      )
    }
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Open dialog' })
    opener.focus()
    fireEvent.click(opener)
    expect(screen.getByRole('button', { name: 'OK' })).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('button', { name: 'OK' }), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(opener).toHaveFocus()
  })

  it('when the opener is gone, focus goes to the node builder, not the page body (UX-03)', () => {
    function Harness() {
      const [open, setOpen] = useState(false)
      const [menu, setMenu] = useState(true)
      return (
        <div className="nodebuilder-root" tabIndex={-1} data-nb-builder="" data-testid="builder">
          {menu && (
            <button
              onClick={() => {
                // Like a menu item: it opens the dialog and unmounts.
                setMenu(false)
                setOpen(true)
              }}
            >
              Rename…
            </button>
          )}
          {open && <Dialog title="Rename graph" onCancel={() => setOpen(false)} primaryLabel="Rename" />}
        </div>
      )
    }
    render(<Harness />)
    const item = screen.getByRole('button', { name: 'Rename…' })
    item.focus()
    fireEvent.click(item)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(screen.getByTestId('builder'))
  })

  it('Cmd+S from outside the dialog never reaches the browser (UX-02)', () => {
    render(<Dialog title="Graphs" onCancel={vi.fn()} />)
    ;(document.activeElement as HTMLElement | null)?.blur()
    expect(fireEvent.keyDown(document.body, { key: 's', metaKey: true })).toBe(false)
  })

  it('with two dialogs open, Esc only closes the top one', () => {
    const outerCancel = vi.fn()
    const innerCancel = vi.fn()
    render(
      <Dialog title="Graphs" onCancel={outerCancel}>
        <Dialog title="Save changes?" onCancel={innerCancel} primaryLabel="Save" />
      </Dialog>,
    )
    // The inner dialog is on top, so it holds the first focus.
    expect(screen.getByRole('button', { name: 'Save' })).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Save' }), { key: 'Escape' })
    expect(innerCancel).toHaveBeenCalledTimes(1)
    expect(outerCancel).not.toHaveBeenCalled()
  })
})

describe('Button and Switch', () => {
  it('a disabled button shows its reason as the title, and a key cap', () => {
    render(<Button kind="primary" keyCap="⌘S" disabled disabledReason="No changes to save">Save</Button>)
    const b = screen.getByRole('button')
    expect(b).toBeDisabled()
    expect(b).toHaveAttribute('title', 'No changes to save')
    expect(b).toHaveAttribute('type', 'button')
    expect(b.querySelector('.nb-keycap')?.textContent).toBe('⌘S')
  })

  it('the switch reports its state and toggles on click', () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onChange={onChange} label="Auto cook" />)
    const sw = screen.getByRole('switch', { name: 'Auto cook' })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(sw)
    expect(onChange).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByText('Auto cook'))
    expect(onChange).toHaveBeenCalledTimes(2)
  })
})
