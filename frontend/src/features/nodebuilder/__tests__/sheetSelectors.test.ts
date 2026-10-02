/**
 * The Data Sheet's store selectors (F435 W4 review fixes DV-5, UX-02/DV-15)
 * and the status slice's cookedAt stamp they rely on.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { useNodeBuilderStore, type NodeBuilderState } from '../store'
import { EMPTY_PREVIEW, IDLE_COOK, IDLE_PREVIEW_COOK } from '../store/status'
import { selectSheetCookId, selectSheetStale } from '../GraphChartSplit'
import { FIX_ERRORS_NOTE } from '../useAutoCook'

const st = () => useNodeBuilderStore.getState()

beforeEach(() => {
  useNodeBuilderStore.setState({
    preview: EMPTY_PREVIEW,
    cooks: { backtest: IDLE_COOK, preview: IDLE_PREVIEW_COOK },
    lastCookKind: 'backtest',
    cook: IDLE_COOK,
    autoCook: true,
  })
})

describe('selectSheetCookId (DV-5)', () => {
  it('a failed preview after a backtest keeps the backtest cook', () => {
    // An older successful preview cook (graph G1).
    st().setPreview('ck_p1', {})
    st().setCook({ kind: 'preview', phase: 'cooked', cookId: 'ck_p1', endedAt: 1, cookedAt: 1 })
    // The backtest of G2 ends later.
    st().setCook({ phase: 'cooked', cookId: 'ck_bt', endedAt: 10, cookedAt: 10 })
    expect(selectSheetCookId(st())).toBe('ck_bt')
    // The refresh preview fails afterwards: endedAt moves, the cook id does not.
    st().setCook({ kind: 'preview', phase: 'failed', endedAt: 11 })
    expect(st().cooks.preview.cookId).toBe('ck_p1')
    expect(selectSheetCookId(st())).toBe('ck_bt')
  })

  it('a newer preview cook wins', () => {
    st().setCook({ phase: 'cooked', cookId: 'ck_bt', endedAt: 10, cookedAt: 10 })
    st().setPreview('ck_p2', {})
    st().setCook({ kind: 'preview', phase: 'cooked', cookId: 'ck_p2', endedAt: 20, cookedAt: 20 })
    expect(selectSheetCookId(st())).toBe('ck_p2')
  })

  it('setCook stamps cookedAt when a write carries a cook id, not otherwise', () => {
    st().setCook({ phase: 'cooked', cookId: 'ck_a' })
    const at = st().cooks.backtest.cookedAt
    expect(typeof at).toBe('number')
    st().setCook({ phase: 'failed' })
    expect(st().cooks.backtest.cookedAt).toBe(at)
  })
})

describe('selectSheetStale (UX-02, DV-15)', () => {
  const stale = (patch: Partial<NodeBuilderState>) => {
    useNodeBuilderStore.setState(patch)
    return selectSheetStale(st())
  }

  it('shows with auto cook off when the graph changed', () => {
    expect(stale({ autoCook: false, preview: { ...EMPTY_PREVIEW, stale: true } })).toBe(true)
  })

  it('hides with auto cook on while the next preview is on its way', () => {
    expect(stale({ autoCook: true, preview: { ...EMPTY_PREVIEW, stale: true } })).toBe(false)
    st().setCook({ kind: 'preview', stale: true, phase: 'cooking' })
    expect(selectSheetStale(st())).toBe(false)
  })

  it('shows with auto cook on when the preview failed or errors block it', () => {
    useNodeBuilderStore.setState({ autoCook: true, preview: { ...EMPTY_PREVIEW, stale: true } })
    st().setCook({ kind: 'preview', phase: 'failed' })
    expect(selectSheetStale(st())).toBe(true)
    st().setCook({ kind: 'preview', phase: 'idle', stale: true, staleNote: FIX_ERRORS_NOTE })
    expect(selectSheetStale(st())).toBe(true)
  })

  it('never shows when nothing is stale', () => {
    expect(stale({ autoCook: false, preview: EMPTY_PREVIEW })).toBe(false)
  })
})
