/**
 * The command registry contract (F435 W3 pre-step 3.0): commands/*.ts
 * modules are registered automatically, `when(state)` gates a command,
 * a command that returns false hands the key to the next one, and
 * `runCommand` runs by id against the active canvas.
 */

import { describe, it, expect, afterEach, vi } from 'vitest'
import {
  dispatchKey,
  findCommand,
  getActiveCanvas,
  getCommand,
  isCommandEnabled,
  listCommands,
  registerCommand,
  runCommand,
  setActiveCanvas,
  type CommandScope,
} from '../commands'
import type { CanvasCtx } from '../canvasPlugins'
import { emptyGraph } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'

const both: ReadonlySet<CommandScope> = new Set<CommandScope>(['canvas', 'global'])

function key(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent('keydown', { cancelable: true, ...init })
}

/** A canvas stand-in: only what the edit commands call. */
function fakeCanvas(over: Partial<CanvasCtx> = {}): CanvasCtx {
  return {
    rf: {} as CanvasCtx['rf'],
    store: useNodeBuilderStore,
    graphPosition: n => [n.position.x, n.position.y],
    pointer: () => ({ x: 0, y: 0 }),
    pointerOnCanvas: () => false,
    graph: () => emptyGraph(),
    editable: () => true,
    container: () => null,
    focus: () => {},
    openTabMenu: () => true,
    deleteSelection: () => true,
    ...over,
  }
}

afterEach(() => {
  setActiveCanvas(null)
  useNodeBuilderStore.getState().discardEdits()
})

describe('auto-registry', () => {
  it('registers the commands/*.ts modules: history and the canvas edit commands', () => {
    const ids = listCommands().map(c => c.id)
    for (const id of ['history.undo', 'history.redo', 'edit.addNode', 'edit.delete', 'edit.deleteNoRewire']) {
      expect(ids).toContain(id)
    }
    // Keys the W3 acceptance walk asks for that exist since 3.0.
    const keys = new Set(listCommands().flatMap(c => c.keys ?? []))
    for (const k of ['tab', 'delete', 'backspace', 'mod+z', 'mod+shift+z', 'mod+y']) {
      expect(keys.has(k)).toBe(true)
    }
  })

  it('tags the edit commands for the context menus', () => {
    expect(getCommand('edit.addNode')?.menu).toBe('pane')
    expect(getCommand('edit.delete')?.menu).toBe('node')
  })
})

describe('when, fall-through and runCommand', () => {
  it('when(state) gates a command: the edit commands need an editable graph', () => {
    const del = getCommand('edit.delete')!
    expect(isCommandEnabled(del)).toBe(false)
    expect(findCommand('delete', both)).toBeNull()
    useNodeBuilderStore.getState().newGraph()
    expect(isCommandEnabled(del)).toBe(true)
    expect(findCommand('delete', both)?.id).toBe('edit.delete')
  })

  it('a command that returns false hands the key to the older one', () => {
    let ran = ''
    const off1 = registerCommand({ id: 't.old', label: 'old', keys: ['k'], run: () => { ran = 'old' } })
    const off2 = registerCommand({ id: 't.new', label: 'new', keys: ['k'], run: () => false })
    const e = key({ key: 'k' })
    expect(dispatchKey(e, { scopes: both })).toBe(true)
    expect(ran).toBe('old')
    expect(e.defaultPrevented).toBe(true)
    off2()
    off1()
  })

  it('commands default to the canvas scope', () => {
    const off = registerCommand({ id: 't.scope', label: 's', keys: ['j'], run: () => {} })
    expect(findCommand('j', new Set<CommandScope>(['global']))).toBeNull()
    expect(findCommand('j', new Set<CommandScope>(['canvas']))?.id).toBe('t.scope')
    off()
  })

  it('runCommand runs by id with the active canvas, and skips a disabled command', () => {
    const openTabMenu = vi.fn(() => true)
    setActiveCanvas(fakeCanvas({ openTabMenu }))
    expect(getActiveCanvas()).not.toBeNull()
    // No editable graph: disabled.
    expect(runCommand('edit.addNode')).toBe(false)
    expect(openTabMenu).not.toHaveBeenCalled()
    useNodeBuilderStore.getState().newGraph()
    expect(runCommand('edit.addNode')).toBe(true)
    expect(openTabMenu).toHaveBeenCalledWith({ keyEvent: null })
    expect(runCommand('no.such.command')).toBe(false)
  })

  it('a canvas command with no canvas mounted is not handled', () => {
    useNodeBuilderStore.getState().newGraph()
    const e = key({ key: 'Delete' })
    expect(dispatchKey(e, { scopes: both, canvas: null })).toBe(false)
    expect(e.defaultPrevented).toBe(false)
  })

  it('run gets the store and the key press', () => {
    let seen: unknown = null
    const off = registerCommand({ id: 't.ctx', label: 'c', keys: ['m'], run: ctx => { seen = ctx } })
    const e = key({ key: 'm' })
    const canvas = fakeCanvas()
    dispatchKey(e, { scopes: both, canvas })
    expect(seen).toEqual({ canvas, store: useNodeBuilderStore, event: e })
    off()
  })
})
