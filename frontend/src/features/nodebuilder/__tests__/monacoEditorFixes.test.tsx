/**
 * MonacoEditor fixes from the W7 review (F435): FE-1 scoped, disposed key
 * bindings; FE-2 a value the caller sets is not reported as typing; FE-3
 * focus moves over from the stand-in once the editor shows; FE-4 no
 * EditContext; FE-10 Tab leaves a one-line field; UX-10 the aria label
 * follows the prop.
 *
 * jsdom never loads Monaco, so the loader is mocked with a fake that keeps
 * the parts of the API the component uses, including the real behavior
 * that model.setValue fires onDidChangeContent.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, cleanup } from '@testing-library/react'
import { createRef } from 'react'

interface FakeAction { id: string; keybindings: number[]; keybindingContext?: string; run(): void; disposed: boolean }
interface FakeEditor {
  host: HTMLElement
  options: Record<string, unknown>
  actions: FakeAction[]
  contextKeys: string[]
  focusCalls: { hostDisplay: string }[]
  updates: Record<string, unknown>[]
  setValueCalls: string[]
  type(text: string): void
}

const editors: FakeEditor[] = []

function fakeMonaco() {
  return {
    editor: {
      createModel: (text: string) => {
        let value = text
        const listeners: (() => void)[] = []
        return {
          uri: { toString: () => `inmemory://fake/${Math.random()}` },
          getValue: () => value,
          setValue: (v: string) => { value = v; for (const l of [...listeners]) l() },
          _type: (v: string) => { value = v; for (const l of [...listeners]) l() },
          getLineMaxColumn: () => value.length + 1,
          getPositionAt: (o: number) => ({ lineNumber: 1, column: o + 1 }),
          onDidChangeContent: (l: () => void) => { listeners.push(l); return { dispose() { listeners.splice(listeners.indexOf(l), 1) } } },
          dispose() {},
        }
      },
      create: (host: HTMLElement, options: Record<string, unknown>) => {
        const model = options.model as { _type(v: string): void; setValue(v: string): void; getValue(): string }
        const rec: FakeEditor = {
          host, options, actions: [], contextKeys: [], focusCalls: [], updates: [], setValueCalls: [],
          type: (t: string) => model._type(t),
        }
        const origSet = model.setValue
        model.setValue = (v: string) => { rec.setValueCalls.push(v); origSet(v) }
        editors.push(rec)
        return {
          getModel: () => model,
          createDecorationsCollection: () => ({ set() {} }),
          setPosition() {},
          setSelection() {},
          focus() { rec.focusCalls.push({ hostDisplay: host.style.display }) },
          hasTextFocus: () => false,
          onDidBlurEditorText: () => ({ dispose() {} }),
          addCommand() { throw new Error('addCommand registers a global binding; use addAction') },
          createContextKey: (key: string) => { rec.contextKeys.push(key); return { set() {}, reset() {}, get: () => true } },
          addAction: (a: Omit<FakeAction, 'disposed'>) => {
            const act = { ...a, disposed: false }
            rec.actions.push(act)
            return { dispose() { act.disposed = true } }
          },
          updateOptions(o: Record<string, unknown>) { rec.updates.push(o) },
          layout() {},
          dispose() {},
        }
      },
      setModelMarkers() {},
    },
    MarkerSeverity: { Error: 8, Warning: 4, Info: 2 },
    KeyMod: { CtrlCmd: 2048, Shift: 1024 },
    KeyCode: { Enter: 3, Escape: 9, Tab: 2 },
    Range: { fromPositions: () => ({}) },
  }
}

vi.mock('../code/monacoLoader', () => ({
  loadMonaco: () => Promise.resolve(fakeMonaco()),
}))

const { default: MonacoEditor } = await import('../code/MonacoEditor')
type Handle = import('../code/MonacoEditor').MonacoEditorHandle

async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

beforeEach(() => {
  editors.length = 0
  cleanup()
})

describe('MonacoEditor key bindings (FE-1)', () => {
  it('each editor binds its keys under its own context key, and unmount removes them', async () => {
    const a = render(<MonacoEditor ariaLabel="A" value="" singleLine testId="a" />)
    const b = render(<MonacoEditor ariaLabel="B" value="" testId="b" />)
    await settle()
    expect(editors).toHaveLength(2)
    const [ea, eb] = editors
    expect(ea.contextKeys).toHaveLength(1)
    expect(eb.contextKeys).toHaveLength(1)
    expect(ea.contextKeys[0]).not.toBe(eb.contextKeys[0])
    for (const e of editors) {
      expect(e.actions.length).toBeGreaterThan(0)
      for (const act of e.actions) expect(act.keybindingContext?.startsWith(e.contextKeys[0])).toBe(true)
    }
    // Plain Enter commits only in the one-line field.
    expect(ea.actions.some(x => x.keybindings.includes(3))).toBe(true)
    expect(eb.actions.some(x => x.keybindings.includes(3))).toBe(false)
    a.unmount()
    expect(ea.actions.every(x => x.disposed)).toBe(true)
    expect(eb.actions.some(x => x.disposed)).toBe(false)
    b.unmount()
    expect(eb.actions.every(x => x.disposed)).toBe(true)
  })

  it('the commit action commits this editor\'s own text', async () => {
    const onCommit = vi.fn()
    render(<MonacoEditor ariaLabel="A" value="x = 1" onCommit={onCommit} testId="a" />)
    const other = vi.fn()
    render(<MonacoEditor ariaLabel="B" value="y = 2" onCommit={other} testId="b" />)
    await settle()
    editors[0].actions.find(x => x.id.startsWith('nb.commit.'))!.run()
    expect(onCommit).toHaveBeenCalledWith('x = 1')
    expect(other).not.toHaveBeenCalled()
  })
})

describe('MonacoEditor outside updates (FE-2)', () => {
  it('a value the caller sets is not reported through onChange; typing is', async () => {
    const onChange = vi.fn()
    const r = render(<MonacoEditor ariaLabel="A" value="a" onChange={onChange} testId="a" />)
    await settle()
    r.rerender(<MonacoEditor ariaLabel="A" value="b" onChange={onChange} testId="a" />)
    await settle()
    expect(editors[0].setValueCalls).toContain('b')
    expect(onChange).not.toHaveBeenCalled()
    act(() => editors[0].type('bc'))
    expect(onChange).toHaveBeenCalledWith('bc')
  })

  it('revert() puts the text in without onChange', async () => {
    const onChange = vi.fn()
    const ref = createRef<Handle>()
    render(<MonacoEditor ref={ref} ariaLabel="A" value="typed" onChange={onChange} singleLine testId="a" />)
    await settle()
    act(() => ref.current!.revert('21'))
    expect(editors[0].setValueCalls).toContain('21')
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('MonacoEditor focus hand-over (FE-3)', () => {
  it('focus moves to the editor after it is shown, not while it is hidden', async () => {
    render(<MonacoEditor ariaLabel="A" value="14" singleLine autoSelect testId="a" />)
    await settle()
    const e = editors[0]
    expect(e.focusCalls.length).toBeGreaterThan(0)
    expect(e.focusCalls.every(c => c.hostDisplay === 'block')).toBe(true)
  })

  it('no focus is taken when the stand-in did not have it', async () => {
    render(<MonacoEditor ariaLabel="A" value="14" singleLine testId="a" />)
    await settle()
    expect(editors[0].focusCalls).toHaveLength(0)
  })
})

describe('MonacoEditor options', () => {
  it('turns EditContext off so the keys come from a textarea (FE-4)', async () => {
    render(<MonacoEditor ariaLabel="A" value="" testId="a" />)
    await settle()
    expect(editors[0].options.editContext).toBe(false)
  })

  it('the aria label follows the prop (UX-10)', async () => {
    const r = render(<MonacoEditor ariaLabel="Code block for a" value="" testId="a" />)
    await settle()
    r.rerender(<MonacoEditor ariaLabel="Code block for b" value="" testId="a" />)
    await settle()
    expect(editors[0].updates).toContainEqual({ ariaLabel: 'Code block for b' })
  })
})

describe('MonacoEditor Tab in a one-line field (FE-10)', () => {
  it('Tab and Shift+Tab move focus out of the field', async () => {
    const r = render(
      <div>
        <button type="button">before</button>
        <MonacoEditor ariaLabel="A" value="" singleLine testId="a" />
        <button type="button">after</button>
      </div>,
    )
    await settle()
    const acts = editors[0].actions
    const next = acts.find(x => x.id.startsWith('nb.tabNext.'))!
    const prev = acts.find(x => x.id.startsWith('nb.tabPrev.'))!
    expect(next.keybindings).toEqual([2])
    expect(prev.keybindings).toEqual([1024 | 2])
    act(() => next.run())
    expect(document.activeElement).toBe(r.getByText('after'))
    act(() => prev.run())
    expect(document.activeElement).toBe(r.getByText('before'))
  })

  it('a block editor keeps Tab for indenting', async () => {
    render(<MonacoEditor ariaLabel="A" value="" testId="a" />)
    await settle()
    expect(editors[0].actions.some(x => x.keybindings.includes(2))).toBe(false)
  })
})
