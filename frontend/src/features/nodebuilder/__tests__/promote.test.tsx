/**
 * Promoted params (F435 W6 item 6.D, spec S40): promote a child param to
 * its network, the read-only child row, the promoted rows on the subnet
 * card and in the Inspector, unpromote, reorder, the Promote popover and
 * the param-menu commands.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode, type GraphPromotedParam } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { getCommand, isCommandEnabled } from '../commands'
import { closeContextMenu, openContextMenu, buildMenu, type MenuItemRow } from '../contextMenuModel'
import type { CanvasCtx } from '../canvasPlugins'
import { openPromote, resetAssetUi } from '../assetUi'
import { ParamRow, PromotedRows } from '../nodes/ParamRow'
import { PromoteDialogHost } from '../PromoteDialog'
import { ParametersWithPromoted } from '../PromotedSection'
import {
  movePromoted,
  promoteParam,
  promotionOf,
  renamePromoted,
  unpromoteParam,
} from '../operations/promote'
import { commitRename } from '../operations/rename'

function node(id: string, type: string, params: GraphNode['params'] = {}, parent: string | null = null, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent, params, position: [0, 0], display: false, bypass: false, ...extra }
}

/** Subnet `mom` holding `rsi` (period 14) and `sma`. */
function fixture(): Graph {
  const g = emptyGraph()
  g.nodes = {
    mom: node('mom', 'subnet', {}, null, { meta: { view: 'card' } }),
    rsi: node('rsi', 'rsi', { period: 14 }, 'mom'),
    sma: node('sma', 'sma', { period: 20 }, 'mom'),
    top: node('top', 'rsi', { period: 9 }),
  }
  return g
}

const s = () => useNodeBuilderStore.getState()

function load(g: Graph) {
  s().discardEdits()
  s().openGraph(g, { id: 'g_000000000002', rev: 1, name: 'promote' })
}

const ENTRY: GraphPromotedParam = { name: 'rsi_period', label: 'period', target: 'rsi/period', type: 'int', default: 14 }

beforeEach(() => {
  resetAssetUi()
  load(fixture())
})

afterEach(() => {
  cleanup()
  closeContextMenu()
})

describe('promoteParam', () => {
  it('adds the entry and the value on the network, and leaves the child alone', () => {
    const g = promoteParam(fixture(), 'rsi', 'period', { name: 'rsi_period', label: '' })
    expect(g.nodes.mom.promoted).toEqual([ENTRY])
    expect(g.nodes.mom.params.rsi_period).toBe(14)
    expect(g.nodes.rsi).toEqual(fixture().nodes.rsi)
    expect(promotionOf(g, 'rsi', 'period')?.entry.name).toBe('rsi_period')
    expect(promotionOf(g, 'rsi', 'type')).toBeNull()
  })

  it('refuses a node at the root, a duplicate, a taken name, and a locked network', () => {
    expect(() => promoteParam(fixture(), 'top', 'period', { name: 'p', label: 'p' })).toThrow(/inside a network/)
    const once = promoteParam(fixture(), 'rsi', 'period', { name: 'rsi_period', label: 'p' })
    expect(() => promoteParam(once, 'rsi', 'period', { name: 'other', label: 'p' })).toThrow(/Already promoted/)
    expect(() => promoteParam(once, 'sma', 'period', { name: 'rsi_period', label: 'p' })).toThrow(/already has a param/)
    expect(() => promoteParam(once, 'sma', 'period', { name: '9bad', label: 'p' })).toThrow(/a-z/)
    const locked = fixture()
    locked.nodes.mom = { ...locked.nodes.mom, locked: true, asset_ref: { name: 'x', version: 1 } }
    expect(() => promoteParam(locked, 'rsi', 'period', { name: 'p', label: 'p' })).toThrow(/locked/)
  })

  it('unpromote removes the entry and copies the last network value back to the child', () => {
    let g = promoteParam(fixture(), 'rsi', 'period', { name: 'rsi_period', label: 'Period' })
    g = { ...g, nodes: { ...g.nodes, mom: { ...g.nodes.mom, params: { rsi_period: 21 } } } }
    const back = unpromoteParam(g, 'mom', 'rsi_period')
    expect(back.nodes.mom.promoted).toEqual([])
    expect(back.nodes.mom.params).toEqual({})
    expect(back.nodes.rsi.params.period).toBe(21)
  })

  it('renames a promoted name with its value, and reorders', () => {
    let g = promoteParam(fixture(), 'rsi', 'period', { name: 'a', label: 'A' })
    g = promoteParam(g, 'sma', 'period', { name: 'b', label: 'B' })
    g = renamePromoted(g, 'mom', 'a', 'fast')
    expect(g.nodes.mom.promoted!.map(p => p.name)).toEqual(['fast', 'b'])
    expect(g.nodes.mom.params).toEqual({ fast: 14, b: 20 })
    g = movePromoted(g, 'mom', 'b', -1)
    expect(g.nodes.mom.promoted!.map(p => p.name)).toEqual(['b', 'fast'])
    expect(movePromoted(g, 'mom', 'b', -1)).toBe(g)
  })
})

describe('promoted rows', () => {
  function promoteInStore(name = 'rsi_period') {
    act(() => s().commit('promote', g => promoteParam(g, 'rsi', 'period', { name, label: 'Period' })))
  }

  it('the promoted child row is read-only with the ↑ glyph and the network value', () => {
    promoteInStore()
    render(<ParamRow nodeId="rsi" paramKey="period" value={14} />)
    const row = screen.getByTestId('nb-param-rsi-period')
    expect(row.getAttribute('data-promoted-to')).toBe('rsi_period')
    expect(screen.getByTestId('nb-param-rsi-period-promoted-glyph').textContent).toBe('↑')
    const value = row.querySelector('[aria-readonly="true"]')!
    expect(value).not.toBeNull()
    expect(value.textContent).toBe('14')
    expect(row.textContent).toContain('Promoted to ../rsi_period. Edit it on the subnet.')
    // No editable field on the child.
    expect(row.querySelector('input')).toBeNull()
  })

  it('editing the promoted row on the card commits the network param, not the child', () => {
    promoteInStore()
    render(<PromotedRows nodeId="mom" />)
    const input = screen.getByTestId('nb-param-mom-rsi_period') as HTMLInputElement
    expect(input.value).toBe('14')
    act(() => input.focus())
    fireEvent.change(input, { target: { value: '30' } })
    fireEvent.blur(input)
    expect(s().graph!.nodes.mom.params.rsi_period).toBe(30)
    expect(s().graph!.nodes.rsi.params.period).toBe(14)
  })

  it('unpromote makes the child row editable again with the last network value', () => {
    promoteInStore()
    act(() => s().updateNodeParams('mom', { rsi_period: 33 }))
    act(() => s().commit('unpromote', g => unpromoteParam(g, 'mom', 'rsi_period')))
    render(<ParamRow nodeId="rsi" paramKey="period" value={s().graph!.nodes.rsi.params.period} typeSpec={{ type: 'number' }} />)
    const input = screen.getByTestId('nb-param-rsi-period') as HTMLInputElement
    expect(input.tagName).toBe('INPUT')
    expect(input.value).toBe('33')
  })

  it('with 5 promoted params the card shows 4 rows and "+1 more in Inspector"', () => {
    const g = fixture()
    const promoted: GraphPromotedParam[] = ['a', 'b', 'c', 'd', 'e'].map(n => ({ name: n, label: n.toUpperCase(), target: 'rsi/period', type: 'int', default: 1 }))
    g.nodes.mom = { ...g.nodes.mom, promoted, params: Object.fromEntries(promoted.map(p => [p.name, 1])) }
    load(g)
    render(<PromotedRows nodeId="mom" />)
    const rows = screen.getByTestId('nb-promoted-rows-mom')
    expect(rows.querySelectorAll('input')).toHaveLength(4)
    expect(rows.textContent).toContain('+1 more in Inspector')
  })

  it('a row whose target no longer exists is struck through with the target in its tooltip', () => {
    const g = fixture()
    g.nodes.mom = { ...g.nodes.mom, promoted: [{ ...ENTRY, target: 'gone/period' }], params: { rsi_period: 14 } }
    load(g)
    render(<PromotedRows nodeId="mom" />)
    const wrap = document.querySelector('[data-target-missing="true"]') as HTMLElement
    expect(wrap).not.toBeNull()
    expect(wrap.title).toBe('Target gone/period no longer exists.')
  })

  it('the Inspector lists Promoted rows, reorders with Alt+Arrow and unpromotes from the overflow', () => {
    promoteInStore('a')
    act(() => s().commit('promote', g => promoteParam(g, 'sma', 'period', { name: 'b', label: 'B' })))
    const view = () => {
      const n = s().graph!.nodes.mom
      return <ParametersWithPromoted nodeId="mom" node={n} editable />
    }
    const { rerender } = render(view())
    expect(screen.getByTestId('nb-inspector-promoted').textContent).toContain('Promoted')
    fireEvent.keyDown(screen.getByTestId('nb-inspector-promoted-a'), { key: 'ArrowDown', altKey: true })
    expect(s().graph!.nodes.mom.promoted!.map(p => p.name)).toEqual(['b', 'a'])
    rerender(view())
    fireEvent.click(screen.getByLabelText('More for B'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Unpromote' }))
    expect(s().graph!.nodes.mom.promoted!.map(p => p.name)).toEqual(['a'])
    expect(s().graph!.nodes.sma.params.period).toBe(20)
  })

  it('a locked instance shows "Defined by", editable values, no grip and no + Promote link', () => {
    const g = fixture()
    g.nodes = {
      inst: node('inst', 'subnet', { lookback: 50 }, null, {
        locked: true,
        asset_ref: { name: 'regime_filter', version: 3 },
        promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }],
      }),
    }
    load(g)
    render(<ParametersWithPromoted nodeId="inst" node={s().graph!.nodes.inst} editable />)
    const list = screen.getByTestId('nb-inspector-promoted')
    expect(list.textContent).toContain('Defined by regime_filter v3')
    expect(list.querySelector('[aria-roledescription="drag handle"]')).toBeNull()
    expect(screen.queryByText('+ Promote a parameter…')).toBeNull()
    expect(document.querySelector('[data-target-missing]')).toBeNull()
    expect((screen.getByTestId('nb-param-inspector-inst-lookback') as HTMLInputElement).value).toBe('50')
  })
})

describe('Promote popover and param menu', () => {
  it('opens with the default name, promotes in one undo step and closes', () => {
    render(<PromoteDialogHost />)
    act(() => openPromote({ nodeId: 'rsi', param: 'period' }))
    expect(screen.getByText('Promote period to mom')).toBeTruthy()
    const name = screen.getByLabelText('Name') as HTMLInputElement
    expect(name.value).toBe('rsi_period')
    expect(screen.getByText('Used in ch("../rsi_period") and in the file.')).toBeTruthy()
    const past = s().past.length
    fireEvent.click(screen.getByRole('button', { name: 'Promote' }))
    expect(s().past.length).toBe(past + 1)
    expect(s().graph!.nodes.mom.promoted).toEqual([ENTRY])
    expect(screen.queryByTestId('nb-promote-dialog')).toBeNull()
  })

  it('a taken name turns the field red and disables Promote', () => {
    act(() => s().updateNodeParams('mom', { taken: 1 }))
    render(<PromoteDialogHost />)
    act(() => openPromote({ nodeId: 'rsi', param: 'period' }))
    const name = screen.getByLabelText('Name') as HTMLInputElement
    fireEvent.change(name, { target: { value: 'taken' } })
    expect(name.getAttribute('aria-invalid')).toBe('true')
    expect((screen.getByRole('button', { name: 'Promote' }) as HTMLButtonElement).disabled).toBe(true)
  })

  function openParamMenuOn(nodeId: string, param: string) {
    openContextMenu({ kind: 'param', screen: { x: 0, y: 0 }, flow: { x: 0, y: 0 }, canvas: {} as CanvasCtx, target: { nodeId, param } })
  }

  it('"Promote to parent…" is enabled inside a network and disabled at the root', () => {
    const cmd = getCommand('assets.promote')!
    openParamMenuOn('rsi', 'period')
    expect(isCommandEnabled(cmd)).toBe(true)
    openParamMenuOn('top', 'period')
    expect(isCommandEnabled(cmd)).toBe(false)
  })

  it('Unpromote and "Go to promoted parameter" show only on a promoted child row', () => {
    const ids = () => buildMenu('param', s(), true).filter((r): r is MenuItemRow => r.type === 'item').map(r => r.cmd.id)
    openParamMenuOn('rsi', 'period')
    expect(ids()).toContain('assets.promote')
    expect(ids()).not.toContain('assets.unpromote')
    act(() => s().commit('promote', g => promoteParam(g, 'rsi', 'period', { name: 'rsi_period', label: 'Period' })))
    expect(ids()).toContain('assets.unpromote')
    expect(ids()).toContain('assets.goToPromoted')
    getCommand('assets.unpromote')!.run({ canvas: null, store: useNodeBuilderStore, event: null })
    expect(s().graph!.nodes.mom.promoted).toEqual([])
  })

  it('"Go to promoted parameter" selects the network', () => {
    act(() => s().commit('promote', g => promoteParam(g, 'rsi', 'period', { name: 'rsi_period', label: 'Period' })))
    openParamMenuOn('rsi', 'period')
    getCommand('assets.goToPromoted')!.run({ canvas: null, store: useNodeBuilderStore, event: null })
    expect(s().selectedNodeIds).toEqual(['mom'])
  })

  it('renaming rsi to rsi_fast keeps the promoted row working (target rewritten)', () => {
    act(() => s().commit('promote', g => promoteParam(g, 'rsi', 'period', { name: 'rsi_period', label: 'Period' })))
    expect(commitRename(useNodeBuilderStore, 'rsi', 'rsi_fast')).toBe(true)
    const g = s().graph!
    expect(g.nodes.mom.promoted?.map(p => p.target)).toEqual(['rsi_fast/period'])
    expect(promotionOf(g, 'rsi', 'period')?.entry.name).toBe('rsi_period')
    // Renaming the network itself leaves the relative target alone.
    expect(commitRename(useNodeBuilderStore, 'mom', 'momentum')).toBe(true)
    expect(s().graph!.nodes.mom.promoted?.map(p => p.target)).toEqual(['rsi_fast/period'])
    expect(promotionOf(s().graph!, 'rsi', 'period')?.entry.name).toBe('rsi_period')
  })
})
