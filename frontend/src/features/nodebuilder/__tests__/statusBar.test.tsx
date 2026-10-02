/**
 * Item 3.H (F435 W3): status bar (S20), `?` shortcut overlay (S21), hint,
 * Reset view and minimap colors (S22).
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { Profiler, useRef } from 'react'
import '../canvasPlugins' // loads plugins/*.ts, which fill the 3.H slots
import StatusBar from '../StatusBar'
import { resetStatusChannels, selectionText, shortGraphId } from '../statusChannels'
import ShortcutHelp from '../ShortcutHelp'
import { formatChord, shortcutGroups } from '../shortcutList'
import { HintBar, ResetViewButton, HINT_TEXT, HINT_STORAGE_KEY } from '../HintBar'
import { minimapNodeColor } from '../minimapColors'
import { plugin as pointerTracker, ZOOM_THROTTLE_MS } from '../plugins/pointerTracker'
import { getCommand, listCommands } from '../commands'
import { setShortcutHelpOpen } from '../commands/help'
import { handleGlobalKey } from '../commands/useGlobalKeys'
import { listSlot, BuilderContext, type BuilderApi } from '../slots'
import { useNodeBuilderStore } from '../store'
import type { CanvasCtx } from '../canvasPlugins'
import type { Graph } from '../../../api/nodebuilder'
import type { Node as RFNode } from '@xyflow/react'

const initialState = useNodeBuilderStore.getState()

function makeGraph(): Graph {
  const node = (id: string, type: string, name: string, y: number) => ({
    id, type, name, parent: null, params: {}, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 3,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      n1: node('n1', 'ticker', 'aapl', 0),
      n2: node('n2', 'rsi', 'rsi', 150),
      n3: node('n3', 'entry', 'below_entry', 300),
    },
    wires: [
      { id: 'w1', from: 'n1', to: 'n2', from_port: 'out', to_port: 'in0' },
      { id: 'w2', from: 'n2', to: 'n3', from_port: 'out', to_port: 'in0' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

const ctx = {} as CanvasCtx

beforeEach(() => {
  // Run animation frames at once, so the cursor write is synchronous.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => {})
})

afterEach(() => {
  cleanup()
  setShortcutHelpOpen(false)
  resetStatusChannels()
  useNodeBuilderStore.setState(initialState, true)
  vi.unstubAllGlobals()
  vi.useRealTimers()
  try { localStorage.removeItem(HINT_STORAGE_KEY) } catch { /* no storage */ }
})

function renderBar(api: Partial<BuilderApi> = {}) {
  const builder = {
    session: { save: vi.fn() },
    runBacktest: vi.fn(),
    stopBacktest: vi.fn(),
    openDiagnostics: vi.fn(),
    ...api,
  } as unknown as BuilderApi
  const utils = render(
    <BuilderContext.Provider value={builder}>
      <div className="nodebuilder-root"><StatusBar /></div>
    </BuilderContext.Provider>,
  )
  return { ...utils, builder }
}

function openTestGraph(meta = { id: null as string | null, rev: 0, name: 'test' }) {
  act(() => useNodeBuilderStore.getState().openGraph(makeGraph(), meta))
}

// ── Slots ───────────────────────────────────────────────────────────────────

describe('3.H registrations', () => {
  it('fills the status bar, overlay and toolbar slots', () => {
    expect(listSlot('statusBar').map(e => e.id)).toContain('status')
    expect(listSlot('overlays').map(e => e.id)).toContain('shortcutHelp')
    const reset = listSlot('toolbarRight').find(e => e.id === 'resetView')
    expect(reset?.order).toBe(10)
  })
})

// ── Status bar (S20) ────────────────────────────────────────────────────────

describe('StatusBar', () => {
  it('draws every segment with its test id', () => {
    renderBar()
    for (const id of ['nb-status', 'nb-status-zoom', 'nb-status-cursor', 'nb-status-selection', 'nb-status-cook',
      'nb-status-diag', 'nb-status-flash', 'nb-status-saved', 'nb-status-graph']) {
      expect(screen.getByTestId(id)).toBeInTheDocument()
    }
    expect(screen.getByTestId('nb-status')).toHaveAttribute('role', 'contentinfo')
    expect(screen.getByTestId('nb-status-cursor')).toHaveTextContent('—, —')
    expect(screen.getByTestId('nb-status-selection')).toHaveTextContent('no selection')
    expect(screen.getByTestId('nb-status-cook')).toHaveTextContent('idle')
  })

  it('shows the selection: one node by name, two as a count, a wire by its ends', () => {
    openTestGraph()
    renderBar()
    act(() => useNodeBuilderStore.getState().setSelection({ nodeIds: ['n2'] }))
    expect(screen.getByTestId('nb-status-selection')).toHaveTextContent(/^rsi$/)
    act(() => useNodeBuilderStore.getState().setSelection({ nodeIds: ['n2', 'n3'] }))
    expect(screen.getByTestId('nb-status-selection')).toHaveTextContent('2 nodes')
    act(() => useNodeBuilderStore.getState().setSelection({ wireIds: ['w2'] }))
    expect(screen.getByTestId('nb-status-selection')).toHaveTextContent('wire rsi → below_entry')
  })

  it('selection text covers mixed selections', () => {
    const g = makeGraph()
    expect(selectionText(g, ['n1', 'n2'], ['w1'])).toBe('2 nodes · 1 wire')
    expect(selectionText(g, [], [])).toBe('no selection')
    g.annotations.boxes.push({ id: 'b1', label: '', color: 'logic', rect: [0, 0, 1, 1], members: [], parent: null })
    expect(selectionText(g, ['n1'], [], ['b1', 'note1'])).toBe('1 node · 1 box · 1 note')
  })

  it('shows the zoom after a pan or zoom ends (store viewport)', () => {
    openTestGraph()
    renderBar()
    act(() => useNodeBuilderStore.getState().setViewport({ x: 0, y: 0, zoom: 0.85 }))
    expect(screen.getByTestId('nb-status-zoom')).toHaveTextContent('85%')
  })

  it('takes live zoom from onMove, at most every 100 ms', () => {
    vi.useFakeTimers()
    renderBar()
    act(() => { pointerTracker.onMove!({ x: 0, y: 0, zoom: 0.5 }, ctx) })
    expect(screen.getByTestId('nb-status-zoom')).toHaveTextContent('50%')
    act(() => { pointerTracker.onMove!({ x: 0, y: 0, zoom: 0.6 }, ctx) })
    // Throttled: still the first value.
    expect(screen.getByTestId('nb-status-zoom')).toHaveTextContent('50%')
    act(() => { vi.advanceTimersByTime(ZOOM_THROTTLE_MS) })
    expect(screen.getByTestId('nb-status-zoom')).toHaveTextContent('60%')
  })

  it('writes the cursor without re-rendering the bar on pointer move', () => {
    let renders = 0
    render(
      <Profiler id="bar" onRender={() => { renders++ }}>
        <StatusBar />
      </Profiler>,
    )
    const before = renders
    act(() => {
      for (let i = 0; i < 20; i++) pointerTracker.onPointerMove!({ x: 1173.4 + i, y: 276.6 }, ctx)
    })
    expect(screen.getByTestId('nb-status-cursor')).toHaveTextContent('1192, 277')
    expect(renders).toBe(before)
    act(() => { pointerTracker.onPointerLeave!(ctx) })
    expect(screen.getByTestId('nb-status-cursor')).toHaveTextContent('—, —')
    expect(renders).toBe(before)
  })

  it('shows the cook state', () => {
    openTestGraph()
    renderBar()
    const cook = () => screen.getByTestId('nb-status-cook')
    act(() => useNodeBuilderStore.getState().setCook({ phase: 'cooking', startedAt: Date.now() }))
    expect(cook()).toHaveTextContent(/^cooking… \d+ ms$/)
    act(() => useNodeBuilderStore.getState().setCook({ phase: 'cooked', endedAt: Date.now() }))
    expect(cook()).toHaveTextContent(/^cooked \d\d:\d\d:\d\d$/)
    expect(cook()).toHaveClass('nb-status--ok')
    act(() => useNodeBuilderStore.getState().setCook({ stale: true }))
    expect(cook()).toHaveTextContent('stale')
    expect(cook()).toHaveClass('nb-status--warn')
    act(() => useNodeBuilderStore.getState().setCook({ staleNote: 'fix errors to cook' }))
    expect(cook()).toHaveTextContent(/^stale · fix errors to cook$/)
    act(() => useNodeBuilderStore.getState().setCook({ staleNote: null }))
    act(() => useNodeBuilderStore.getState().setCook({ phase: 'failed', stale: false, failedNodeId: 'n2' }))
    expect(cook()).toHaveTextContent('cook failed')
    expect(cook().tagName).toBe('BUTTON')
    act(() => useNodeBuilderStore.getState().setCook({ phase: 'cooked', kind: 'preview', endedAt: Date.now() }))
    expect(cook()).toHaveTextContent(/· preview$/)
  })

  it('announces only cooked and failed, never cooking or stale (UX-11)', () => {
    openTestGraph()
    renderBar()
    const live = () => screen.getByTestId('nb-status-cook-live')
    expect(live()).toHaveAttribute('role', 'status')
    expect(screen.getByTestId('nb-status-cook')).not.toHaveAttribute('role')
    act(() => useNodeBuilderStore.getState().setCook({ kind: 'preview', phase: 'cooking', startedAt: Date.now() }))
    expect(screen.getByTestId('nb-status-cook')).toHaveTextContent(/cooking/)
    expect(live().textContent).toBe('')
    act(() => useNodeBuilderStore.getState().setCook({ kind: 'preview', phase: 'cooked', endedAt: Date.now() }))
    expect(live().textContent).toMatch(/^cooked \d\d:\d\d:\d\d$/)
    act(() => useNodeBuilderStore.getState().setCook({ kind: 'preview', stale: true }))
    expect(screen.getByTestId('nb-status-cook')).toHaveTextContent(/stale/)
    expect(live().textContent).toBe('')
    act(() => useNodeBuilderStore.getState().setCook({ kind: 'preview', phase: 'failed', stale: false }))
    expect(live().textContent).toBe('cook failed')
  })

  it('a cook served from the last good data says so (BE-3)', () => {
    openTestGraph()
    renderBar()
    act(() => useNodeBuilderStore.getState().setCook({ kind: 'preview', phase: 'cooked', endedAt: Date.now(), staleNote: 'showing the last cook: fresh data could not be loaded' }))
    const seg = screen.getByTestId('nb-status-cook')
    expect(seg).toHaveTextContent(/^cooked \d\d:\d\d:\d\d · showing the last cook: fresh data could not be loaded · preview$/)
    expect(seg).toHaveClass('nb-status--warn')
  })

  it('shows auto cook on/off (S20 segment 7) and toggles it on click', () => {
    openTestGraph()
    act(() => useNodeBuilderStore.getState().setAutoCook(true))
    renderBar()
    const seg = () => screen.getByTestId('nb-status-autocook')
    expect(seg()).toHaveTextContent('auto cook on')
    expect(seg()).not.toHaveClass('nb-status--dim')
    fireEvent.click(seg())
    expect(useNodeBuilderStore.getState().autoCook).toBe(false)
    expect(seg()).toHaveTextContent('auto cook off')
    expect(seg()).toHaveClass('nb-status--dim')
    act(() => useNodeBuilderStore.getState().setAutoCook(true))
  })

  it('shows a flash for 2 s', () => {
    vi.useFakeTimers()
    renderBar()
    act(() => useNodeBuilderStore.getState().showFlash('Select a node first'))
    expect(screen.getByTestId('nb-status-flash')).toHaveTextContent('Select a node first')
    act(() => { vi.advanceTimersByTime(2100) })
    expect(screen.getByTestId('nb-status-flash')).toHaveTextContent('')
  })

  it('shows problems and opens the diagnostics list on click', () => {
    openTestGraph()
    const { builder } = renderBar()
    const diag = screen.getByTestId('nb-status-diag')
    expect(diag).toHaveTextContent('no problems')
    fireEvent.click(diag)
    expect(builder.openDiagnostics).toHaveBeenCalledWith(diag)
  })

  it('shows save state and the graph id', () => {
    openTestGraph({ id: 'g_00000000003f9a', rev: 12, name: 'x' })
    const { builder } = renderBar()
    expect(screen.getByTestId('nb-status-graph')).toHaveTextContent('graph 3f9a @ rev 12')
    expect(screen.getByTestId('nb-status-saved')).toHaveTextContent('saved')
    act(() => useNodeBuilderStore.getState().moveNode('n1', [10, 10]))
    const saved = screen.getByTestId('nb-status-saved')
    expect(saved).toHaveTextContent('unsaved')
    expect(saved).toHaveClass('nb-status--warn')
    fireEvent.click(saved)
    expect(builder.session.save).toHaveBeenCalled()
    act(() => useNodeBuilderStore.getState().markSaved({ id: 'g_00000000003f9a', rev: 13, name: 'x' }))
    expect(screen.getByTestId('nb-status-saved')).toHaveTextContent('saved just now')
    expect(screen.getByTestId('nb-status-graph')).toHaveTextContent('rev 13')
  })

  it('says untitled and empty graph', () => {
    openTestGraph()
    renderBar()
    expect(screen.getByTestId('nb-status-graph')).toHaveTextContent('untitled')
    act(() => useNodeBuilderStore.getState().newGraph())
    expect(screen.getByTestId('nb-status-graph')).toHaveTextContent('empty graph')
    expect(shortGraphId('g_ab12cd')).toBe('12cd')
  })
})

// ── `?` overlay (S21) ───────────────────────────────────────────────────────

function KeyRoot() {
  const ref = useRef<HTMLDivElement>(null)
  return (
    <div ref={ref} className="nodebuilder-root" tabIndex={0} data-testid="root">
      <ShortcutHelp />
    </div>
  )
}

describe('ShortcutHelp', () => {
  it('formats chords per platform', () => {
    expect(formatChord('mod+shift+z', true)).toBe('⌘⇧Z')
    expect(formatChord('mod+shift+z', false)).toBe('Ctrl+Shift+Z')
    expect(formatChord('shift+?', true)).toBe('?')
    expect(formatChord(' ', true)).toBe('Space')
    expect(formatChord('b', true)).toBe('B')
    expect(formatChord('f2', false)).toBe('F2')
  })

  it('opens on ?, lists every command that has keys, and B on bypass', () => {
    render(<KeyRoot />)
    const root = screen.getByTestId('root')
    root.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
    root.focus()
    const e = new KeyboardEvent('keydown', { key: '?', shiftKey: true, bubbles: true, cancelable: true })
    root.dispatchEvent(e)
    act(() => { handleGlobalKey(e, root, true) })
    expect(screen.getByTestId('nb-shortcuts')).toBeInTheDocument()
    const seen = new Set<string>()
    for (const c of listCommands()) {
      if (!c.keys?.length || seen.has(c.id)) continue
      seen.add(c.id)
      expect(screen.getByTestId(`nb-shortcuts-row-${c.id}`)).toBeInTheDocument()
    }
    if (getCommand('flags.toggleBypass')) {
      expect(screen.getByTestId('nb-shortcuts-row-flags.toggleBypass')).toHaveTextContent(/B$/)
    }
  })

  it('filters rows by label', () => {
    render(<KeyRoot />)
    act(() => setShortcutHelpOpen(true))
    fireEvent.change(screen.getByTestId('nb-shortcuts-filter'), { target: { value: 'frame' } })
    const rows = screen.queryAllByTestId(/^nb-shortcuts-row-/).map(r => r.getAttribute('data-testid'))
    expect(rows.sort()).toEqual(['nb-shortcuts-row-view.frameAll', 'nb-shortcuts-row-view.frameSelection'])
    fireEvent.change(screen.getByTestId('nb-shortcuts-filter'), { target: { value: 'zzzz' } })
    expect(screen.getByText('No shortcuts match "zzzz"')).toBeInTheDocument()
  })

  it('groups follow the id prefix, with MOUSE last', () => {
    const groups = shortcutGroups('')
    expect(groups[groups.length - 1].title).toBe('MOUSE')
    const titles = groups.map(g => g.title)
    expect(titles.indexOf('HISTORY')).toBeLessThan(titles.indexOf('VIEW'))
  })

  it('Esc closes it and gives focus back to the canvas root', () => {
    render(<KeyRoot />)
    const root = screen.getByTestId('root')
    root.focus()
    act(() => setShortcutHelpOpen(true))
    const filter = screen.getByTestId('nb-shortcuts-filter')
    expect(filter).toHaveFocus()
    fireEvent.keyDown(filter, { key: 'Escape' })
    expect(screen.queryByTestId('nb-shortcuts')).toBeNull()
    expect(document.activeElement).toBe(root)
    expect(document.activeElement).toHaveClass('nodebuilder-root')
  })

  it('? in the empty filter closes it again', () => {
    render(<KeyRoot />)
    act(() => setShortcutHelpOpen(true))
    fireEvent.keyDown(screen.getByTestId('nb-shortcuts-filter'), { key: '?' })
    expect(screen.queryByTestId('nb-shortcuts')).toBeNull()
  })
})

// ── Hint, Reset view, minimap (S22) ─────────────────────────────────────────

describe('HintBar and Reset view', () => {
  function setWidth(w: number) {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: w })
  }

  it('shows the hint at 1600 and hides it at 1280', () => {
    openTestGraph()
    setWidth(1600)
    const { unmount } = render(<div className="nodebuilder-root"><HintBar /></div>)
    expect(screen.getByTestId('nb-hint')).toHaveTextContent(HINT_TEXT)
    expect(HINT_TEXT).toBe('Drag node to move · Space+drag to pan · Wheel to zoom · Tab to add')
    unmount()
    setWidth(1280)
    render(<div className="nodebuilder-root"><HintBar /></div>)
    expect(screen.queryByTestId('nb-hint')).toBeNull()
  })

  it('stays dismissed after a remount', () => {
    openTestGraph()
    setWidth(1600)
    const { unmount } = render(<HintBar />)
    fireEvent.click(screen.getByTestId('nb-hint-dismiss'))
    expect(screen.queryByTestId('nb-hint')).toBeNull()
    expect(localStorage.getItem(HINT_STORAGE_KEY)).toBe('off')
    unmount()
    render(<HintBar />)
    expect(screen.queryByTestId('nb-hint')).toBeNull()
  })

  it('hides while the empty-graph state shows', () => {
    setWidth(1600)
    act(() => useNodeBuilderStore.getState().newGraph())
    render(<HintBar />)
    expect(screen.queryByTestId('nb-hint')).toBeNull()
  })

  it('Reset view runs the same command as H', () => {
    openTestGraph()
    setWidth(1600)
    const cmd = getCommand('view.frameAll')
    expect(cmd?.keys).toContain('h')
    const spy = vi.spyOn(cmd!, 'run').mockImplementation(() => undefined)
    vi.spyOn(cmd!, 'when').mockReturnValue(true)
    render(<ResetViewButton />)
    const btn = screen.getByTestId('nb-btn-reset-view')
    expect(btn).toHaveTextContent('Reset view')
    expect(btn).toHaveAttribute('title', 'Frame all nodes (H)')
    fireEvent.click(btn)
    expect(spy).toHaveBeenCalledTimes(1)
    vi.restoreAllMocks()
  })

  it('Reset view is disabled with no nodes and an icon under 1440px', () => {
    setWidth(1280)
    act(() => useNodeBuilderStore.getState().newGraph())
    render(<ResetViewButton />)
    const btn = screen.getByTestId('nb-btn-reset-view')
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('title', 'Nothing to frame')
    expect(btn).toHaveTextContent('⌖')
  })
})

describe('minimap colors', () => {
  const rf = (type: string, data: Record<string, unknown>) => ({ id: 'x', type, position: { x: 0, y: 0 }, data }) as RFNode

  it('colors nodes by category', () => {
    expect(minimapNodeColor(rf('indicator', { catalog: { cat: 'indicator' } }))).toBe('#34d399')
    expect(minimapNodeColor(rf('ticker', { catalog: { cat: 'ticker' } }))).toBe('#22d3ee')
    expect(minimapNodeColor(rf('indicator', { catalog: null }))).toBe('#7a8296')
    expect(minimapNodeColor(rf('nbNote', { note: {} }))).toBe('rgba(217, 119, 6, 0.4)')
    expect(minimapNodeColor(rf('nbBox', { box: { color: 'logic' } }))).toMatch(/^#|^rgb/)
  })
})
