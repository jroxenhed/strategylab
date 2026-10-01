/**
 * Wave 3 acceptance (plan W3 "Acceptance (scripted)"): walking
 * listCommands() finds a command bound to every Houdini key the plan names.
 *
 * The commands come from several W3 items (edit, view, layout, flags,
 * clipboard, history, help), all loaded by the commands/*.ts auto-registry.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { dispatchKey, listCommands, registerCommand, type Command, type CommandScope } from '../commands'
import { useNodeBuilderStore } from '../store'
import { emptyGraph, type Graph } from '../../../api/nodebuilder'

/** Each required key, with the chords that count for it. */
const REQUIRED: ReadonlyArray<[name: string, chords: readonly string[]]> = [
  ['Tab', ['tab']],
  ['Space (pan)', ['space', ' ']],
  ['F', ['f']],
  ['H', ['h']],
  ['G', ['g']],
  ['L', ['l']],
  ['B', ['b']],
  ['D', ['d']],
  // chordOf gives `shift+?` on a US keyboard; either form counts.
  ['?', ['?', 'shift+?']],
  ['mod+C', ['mod+c']],
  ['mod+V', ['mod+v']],
  ['mod+D', ['mod+d']],
  ['mod+Z', ['mod+z']],
  ['mod+shift+Z', ['mod+shift+z']],
  ['mod+Y', ['mod+y']],
  ['Delete', ['delete']],
  ['Backspace', ['backspace']],
]

function boundTo(chords: readonly string[], cmds: readonly Command[]): Command | undefined {
  return cmds.find(c => c.keys?.some(k => chords.includes(k)))
}

describe('W3 key map', () => {
  const cmds = listCommands()

  it.each(REQUIRED)('%s is bound to a command', (_name, chords) => {
    expect(boundTo(chords, cmds)).toBeDefined()
  })

  it('every bound key is written in canonical chord form', () => {
    // mod, alt, shift, then the key in lower case (chordOf's form).
    const order = ['mod', 'alt', 'shift']
    for (const c of cmds) {
      for (const k of c.keys ?? []) {
        if (k === ' ' || k.endsWith('++')) continue
        const parts = k.split('+')
        const key = parts.pop()!
        expect(key, `${c.id}: ${k}`).toBe(key.toLowerCase())
        const idx = parts.map(p => order.indexOf(p))
        expect(idx.every(i => i >= 0), `${c.id}: ${k}`).toBe(true)
        expect([...idx].sort((a, b) => a - b), `${c.id}: ${k}`).toEqual(idx)
      }
    }
  })

  it('the ? key opens the shortcut overlay', () => {
    expect(boundTo(['?'], cmds)?.id).toBe('help.shortcuts')
  })
})

const CANVAS: ReadonlySet<CommandScope> = new Set<CommandScope>(['canvas'])

function key(k: string, init: KeyboardEventInit = {}): KeyboardEvent {
  return new KeyboardEvent('keydown', { key: k, cancelable: true, ...init })
}

describe('key dispatch (integration)', () => {
  const offs: Array<() => void> = []
  afterEach(() => { for (const off of offs.splice(0)) off() })

  it('a read-only view runs only readOnlyOk commands; an editable one runs the newest', () => {
    const ran: string[] = []
    offs.push(registerCommand({ id: 't.safe', label: 'safe', keys: ['f9'], readOnlyOk: true, run: () => { ran.push('safe') } }))
    offs.push(registerCommand({ id: 't.edit', label: 'edit', keys: ['f9'], run: () => { ran.push('edit') } }))
    expect(dispatchKey(key('F9'), { scopes: CANVAS, canvas: null, readOnly: true })).toBe(true)
    expect(dispatchKey(key('F9'), { scopes: CANVAS, canvas: null })).toBe(true)
    expect(ran).toEqual(['safe', 'edit'])
  })

  it('Copy, Frame, Frame all and Snap to grid are readOnlyOk (S19); editing keys are not', () => {
    const byId = new Map(listCommands().map(c => [c.id, c]))
    for (const id of ['clipboard.copy', 'view.frameSelection', 'view.frameAll', 'view.toggleSnap']) {
      expect(byId.get(id)?.readOnlyOk, id).toBe(true)
    }
    for (const id of ['clipboard.paste', 'clipboard.cut', 'edit.delete', 'flags.toggleBypass', 'layout.tidy']) {
      expect(byId.get(id)?.readOnlyOk, id).toBeFalsy()
    }
  })

  it('F2 with no graph node selected falls through edit.rename to the box/note editor', () => {
    const g: Graph = {
      ...emptyGraph(),
      nodes: { r: { id: 'r', type: 'rsi', name: 'rsi1', parent: null, params: { period: 14 }, position: [0, 0], display: false, bypass: false } },
      annotations: { boxes: [], notes: [{ id: 'note1', text: 'hi', rect: [200, 0, 200, 88], color: 'amber', parent: null }] },
    } as Graph
    const s = useNodeBuilderStore.getState()
    s.openGraph(g, { id: 'g_k', rev: 1, name: 'k' })
    s.setSelection({ annotationIds: ['note1'] })
    expect(dispatchKey(key('F2'), { scopes: CANVAS, canvas: null })).toBe(true)
    expect(useNodeBuilderStore.getState().editingAnnotationId).toBe('note1')
    useNodeBuilderStore.getState().stopAnnotationEdit()
  })
})
