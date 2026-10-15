/**
 * Canvas helper tests (F435 Wave 0 item 0.C): multi-select delete and drag,
 * selection sync, the local mirror merge, key gating and Tab menu placement.
 */

import { describe, it, expect, afterEach } from 'vitest'
import {
  alignSelection,
  clampMenuPosition,
  dragStopMoves,
  groupByCategory,
  isTypingTarget,
  markHotEdges,
  menuCatalog,
  menuScreenPoint,
  mergeLocalNodes,
  newNodePosition,
  nodeLabel,
  nudgeFree,
  outermostRoot,
  planDeletion,
  primaryAttrFor,
  primarySelection,
  selectOnly,
  shouldHandleCanvasKey,
  suppressTextSelection,
  BELOW_GAP,
  TICKER_DEFAULT_ATTR,
  DEFAULT_NODE_HEIGHT,
  type FlowNodeLike,
  type FlowEdgeLike,
} from '../canvasHelpers'
import { NODE_CATALOG, canWire, hasInputPort, hasOutputPort } from '../catalog'

function node(id: string, extra: Partial<FlowNodeLike> = {}): FlowNodeLike {
  return { id, position: { x: 0, y: 0 }, ...extra }
}
function edge(id: string, source: string, target: string, selected = false): FlowEdgeLike {
  return { id, source, target, selected }
}

describe('planDeletion', () => {
  it('deletes every selected node from a box selection', () => {
    const nodes = [node('a', { selected: true }), node('b', { selected: true }), node('c')]
    const plan = planDeletion(nodes, [], 'a', null)
    expect(plan.nodeIds.sort()).toEqual(['a', 'b'])
    expect(plan.wireIds).toEqual([])
  })

  it('also deletes selected wires that do not touch a deleted node', () => {
    const nodes = [node('a', { selected: true }), node('b'), node('c'), node('d')]
    const edges = [edge('ab', 'a', 'b', true), edge('cd', 'c', 'd', true), edge('bc', 'b', 'c')]
    const plan = planDeletion(nodes, edges, null, null)
    expect(plan.nodeIds).toEqual(['a'])
    // 'ab' is left to the rewire step of node a
    expect(plan.wireIds).toEqual(['cd'])
  })

  it('falls back to the store selection when React Flow has none', () => {
    expect(planDeletion([node('a'), node('b')], [], 'b', null)).toEqual({ nodeIds: ['b'], wireIds: [] })
    expect(planDeletion([node('a')], [edge('w', 'a', 'a')], null, 'w')).toEqual({ nodeIds: [], wireIds: ['w'] })
  })

  it('ignores a stale fallback id', () => {
    expect(planDeletion([node('a')], [], 'gone', 'gone-wire')).toEqual({ nodeIds: [], wireIds: [] })
  })
})

describe('dragStopMoves', () => {
  it('saves every node that moved with the grabbed one', () => {
    const a = node('a', { position: { x: 10, y: 20 } })
    const b = node('b', { position: { x: 30, y: 40 } })
    const moves = dragStopMoves(a, [a, b])
    expect(moves).toEqual([
      { id: 'a', position: [10, 20] },
      { id: 'b', position: [30, 40] },
    ])
  })

  it('handles a single node drag and a selection-box drag', () => {
    const a = node('a', { position: { x: 1, y: 2 } })
    expect(dragStopMoves(a, undefined)).toEqual([{ id: 'a', position: [1, 2] }])
    expect(dragStopMoves(null, [a])).toEqual([{ id: 'a', position: [1, 2] }])
  })
})

describe('primarySelection', () => {
  it('keeps the current node while it stays selected', () => {
    expect(primarySelection(['a', 'b'], 'b')).toBe('b')
  })
  it('takes the first selected node otherwise, or none', () => {
    expect(primarySelection(['a', 'b'], 'z')).toBe('a')
    expect(primarySelection([], 'a')).toBeNull()
  })
})

describe('alignSelection', () => {
  it('keeps a multi-selection that already contains the store node', () => {
    const curr = [node('a', { selected: true }), node('b', { selected: true })]
    expect(alignSelection(curr, 'a')).toBe(curr)
  })
  it('selects only the store node when it is not selected locally', () => {
    const curr = [node('a', { selected: true }), node('b')]
    const next = alignSelection(curr, 'b')
    expect(next.map(n => !!n.selected)).toEqual([false, true])
  })
  it('clears local selection when the store clears it', () => {
    const curr = [node('a', { selected: true }), node('b')]
    expect(alignSelection(curr, null).some(n => n.selected)).toBe(false)
    const none = [node('a')]
    expect(alignSelection(none, null)).toBe(none)
  })
  it('does nothing for an id not on the canvas', () => {
    const curr = [node('a')]
    expect(alignSelection(curr, 'missing')).toBe(curr)
  })
})

describe('mergeLocalNodes', () => {
  const data = { k: 1 }

  it('returns the same array when nothing changed', () => {
    const curr = [node('a', { data, position: { x: 1, y: 2 } })]
    const next = [node('a', { data, position: { x: 1, y: 2 } })]
    expect(mergeLocalNodes(curr, next)).toBe(curr)
  })

  it('keeps measured size and selection when a node is replaced', () => {
    const curr = [node('a', { data, selected: true, measured: { width: 180, height: 90 } })]
    const next = [node('a', { data: { k: 2 } })]
    const out = mergeLocalNodes(curr, next)
    expect(out).not.toBe(curr)
    expect(out[0].data).toEqual({ k: 2 })
    expect(out[0].measured).toEqual({ width: 180, height: 90 })
    expect(out[0].selected).toBe(true)
  })

  it('does not snap group-dragged nodes back once the store has caught up', () => {
    // After a drag, React Flow shows the new positions; the store then saves
    // them and the rebuilt nodes carry the same values in new objects.
    const curr = [
      node('a', { data, position: { x: 50, y: 60 }, selected: true }),
      node('b', { data, position: { x: 70, y: 80 }, selected: true }),
    ]
    const next = [
      node('a', { data, position: { x: 50, y: 60 } }),
      node('b', { data, position: { x: 70, y: 80 } }),
    ]
    expect(mergeLocalNodes(curr, next)).toBe(curr)
  })

  it('adds new nodes unselected and drops removed ones', () => {
    const curr = [node('a', { data })]
    const out = mergeLocalNodes(curr, [node('b', { data })])
    expect(out.map(n => n.id)).toEqual(['b'])
    expect(out[0].selected).toBe(false)
  })
})

describe('key gating', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('treats inputs, selects, textareas and editable elements as typing', () => {
    for (const tag of ['input', 'select', 'textarea']) {
      expect(isTypingTarget(document.createElement(tag))).toBe(true)
    }
    const div = document.createElement('div')
    expect(isTypingTarget(div)).toBe(false)
    div.contentEditable = 'true'
    // jsdom does not compute isContentEditable, so set it the way a browser would.
    Object.defineProperty(div, 'isContentEditable', { value: true })
    expect(isTypingTarget(div)).toBe(true)
  })

  it('isTypingTarget: a div inside a code editor (Monaco EditContext) is a typing target', () => {
    const host = document.createElement('div')
    host.className = 'nb-code-editor'
    const edit = document.createElement('div')
    edit.className = 'native-edit-context'
    host.appendChild(edit)
    expect(isTypingTarget(edit)).toBe(true)
    const wrap = document.createElement('div')
    wrap.className = 'nb-code-editor'
    const inner = document.createElement('div')
    wrap.appendChild(inner)
    expect(isTypingTarget(inner)).toBe(true)
  })

  it('handles keys from inside the node builder or the page body only', () => {
    const outer = document.createElement('div')
    outer.className = 'nodebuilder-root'
    const toolbarButton = document.createElement('button')
    const inner = document.createElement('div')
    inner.className = 'nodebuilder-root'
    outer.append(toolbarButton, inner)
    const elsewhere = document.createElement('button')
    document.body.append(outer, elsewhere)

    const root = outermostRoot(inner)
    expect(root).toBe(outer)
    const base = { root, inView: true, modifier: false, defaultPrevented: false }
    expect(shouldHandleCanvasKey({ ...base, target: toolbarButton })).toBe(true)
    expect(shouldHandleCanvasKey({ ...base, target: document.body })).toBe(true)
    expect(shouldHandleCanvasKey({ ...base, target: elsewhere })).toBe(false)
  })

  it('never fires while typing, hidden, with a modifier, or already handled', () => {
    const root = document.createElement('div')
    root.className = 'nodebuilder-root'
    const input = document.createElement('input')
    root.append(input)
    document.body.append(root)
    const base = { root, target: root as EventTarget, inView: true, modifier: false, defaultPrevented: false }
    expect(shouldHandleCanvasKey(base)).toBe(true)
    expect(shouldHandleCanvasKey({ ...base, target: input })).toBe(false)
    expect(shouldHandleCanvasKey({ ...base, inView: false })).toBe(false)
    expect(shouldHandleCanvasKey({ ...base, modifier: true })).toBe(false)
    expect(shouldHandleCanvasKey({ ...base, defaultPrevented: true })).toBe(false)
  })
})

describe('Tab menu placement', () => {
  it('opens at the last pointer position, else near the canvas top middle', () => {
    expect(menuScreenPoint({ x: 400, y: 300 }, { left: 0, top: 0, width: 800, height: 600 })).toEqual({ x: 400, y: 300 })
    expect(menuScreenPoint(null, { left: 100, top: 50, width: 800, height: 600 })).toEqual({ x: 500, y: 130 })
  })

  it('places a keyboard-opened node below the selected node', () => {
    const sel = node('s', { position: { x: 10, y: 20 }, measured: { width: 180, height: 120 } })
    expect(newNodePosition({ openedBy: 'keyboard', pointFlow: { x: 999, y: 999 }, selected: sel }))
      .toEqual({ x: 10, y: 20 + 120 + BELOW_GAP })
    const unmeasured = node('s', { position: { x: 0, y: 0 } })
    expect(newNodePosition({ openedBy: 'keyboard', pointFlow: { x: 5, y: 5 }, selected: unmeasured }))
      .toEqual({ x: 0, y: DEFAULT_NODE_HEIGHT + BELOW_GAP })
  })

  it('uses the cursor point when the spot below the selection is off screen (UXP-10)', () => {
    const sel = node('s', { position: { x: 10, y: 20 }, measured: { width: 180, height: 120 } })
    const inView = { minX: 0, minY: 0, maxX: 800, maxY: 600 }
    expect(newNodePosition({ openedBy: 'keyboard', pointFlow: { x: 5, y: 6 }, selected: sel, visible: inView }))
      .toEqual({ x: 10, y: 20 + 120 + BELOW_GAP })
    const scrolledAway = { minX: 2000, minY: 2000, maxX: 2800, maxY: 2600 }
    expect(newNodePosition({ openedBy: 'keyboard', pointFlow: { x: 2400, y: 2300 }, selected: sel, visible: scrolledAway }))
      .toEqual({ x: 2400, y: 2300 })
  })

  it('places the node at the cursor or wire drop point otherwise', () => {
    expect(newNodePosition({ openedBy: 'keyboard', pointFlow: { x: 5, y: 6 }, selected: null })).toEqual({ x: 5, y: 6 })
    const sel = node('s')
    expect(newNodePosition({ openedBy: 'wire', pointFlow: { x: 7, y: 8 }, selected: sel })).toEqual({ x: 7, y: 8 })
  })

  it('steps a new node down past nodes already on that spot', () => {
    expect(nudgeFree({ x: 0, y: 0 }, [])).toEqual({ x: 0, y: 0 })
    expect(nudgeFree({ x: 0, y: 0 }, [{ x: 0, y: 0 }, { x: 2, y: 24 }])).toEqual({ x: 0, y: 48 })
  })

  it('keeps the menu inside the window', () => {
    const size = { width: 520, height: 420 }
    const vp = { width: 1200, height: 900 }
    expect(clampMenuPosition({ x: 100, y: 100 }, size, vp)).toEqual({ x: 100, y: 100 })
    expect(clampMenuPosition({ x: 1100, y: 800 }, size, vp)).toEqual({ x: 1200 - 520 - 8, y: 900 - 420 - 8 })
    expect(clampMenuPosition({ x: -50, y: -50 }, size, vp)).toEqual({ x: 8, y: 8 })
  })
})

describe('catalog helpers', () => {
  it('menu catalog hides entries compile does not act on', () => {
    const menu = menuCatalog()
    expect(menu.every(e => e.compileActive)).toBe(true)
    const hidden = NODE_CATALOG.filter(e => !e.compileActive).map(e => e.name)
    for (const name of hidden) expect(menu.some(e => e.name === name)).toBe(false)
  })

  it('menu catalog drops a compileActive false entry from any catalog', () => {
    // A made-up catalog, so the check still bites if every real node compiles.
    const base = NODE_CATALOG.find(e => e.compileActive)!
    const catalog = [
      { ...base, name: 'kept', compileActive: true },
      { ...base, name: 'stub', compileActive: false },
    ]
    expect(menuCatalog(catalog).map(e => e.name)).toEqual(['kept'])
  })

  it('groups entries by category in catalog order', () => {
    const grouped = groupByCategory(NODE_CATALOG)
    const flat = Object.values(grouped).flat()
    expect(flat.length).toBe(NODE_CATALOG.length)
    for (const [cat, entries] of Object.entries(grouped)) {
      expect(entries.every(e => e.cat === cat)).toBe(true)
    }
  })

  it('primaryAttrFor returns the first written attribute for non-Ticker nodes', () => {
    const withWrites = NODE_CATALOG.find(e => e.writes.length > 0 && e.cat !== 'ticker')!
    expect(primaryAttrFor(withWrites.name)).toBe(withWrites.writes[0])
    expect(primaryAttrFor('rsi')).toBe('@rsi')
    expect(primaryAttrFor('no-such-node')).toBeNull()
    expect(primaryAttrFor(undefined)).toBeNull()
  })

  it('primaryAttrFor labels a wire out of a Ticker @close, not its first write', () => {
    const ticker = NODE_CATALOG.find(e => e.name === 'ticker')!
    // The Ticker writes @open first; a new RSI must not compute on the open.
    expect(ticker.writes[0]).toBe('@open')
    expect(ticker.writes).toContain(TICKER_DEFAULT_ATTR)
    expect(primaryAttrFor('ticker')).toBe('@close')
  })

  it('primaryAttrFor returns null for terminals that write nothing', () => {
    const terminal = NODE_CATALOG.find(e => e.writes.length === 0 && e.cat !== 'ticker')
    if (terminal) expect(primaryAttrFor(terminal.name)).toBeNull()
  })
})

describe('suppressTextSelection', () => {
  it('turns off text selection and puts it back', () => {
    document.body.style.userSelect = 'text'
    const restore = suppressTextSelection()
    expect(document.body.style.userSelect).toBe('none')
    restore()
    expect(document.body.style.userSelect).toBe('text')
    document.body.style.userSelect = ''
  })
})


// ---------------------------------------------------------------------------
// F435 wave 0 review fixes
// ---------------------------------------------------------------------------

describe('selectOnly (UXP-3)', () => {
  it('collapses a multi-selection to the clicked member', () => {
    const curr = [node('a', { selected: true }), node('b', { selected: true }), node('c')]
    const out = selectOnly(curr, 'b')
    expect(out.filter(n => n.selected).map(n => n.id)).toEqual(['b'])
    expect(out[1]).toBe(curr[1])  // unchanged node keeps its object
  })

  it('returns the same array when only that node is selected already', () => {
    const curr = [node('a'), node('b', { selected: true })]
    expect(selectOnly(curr, 'b')).toBe(curr)
  })
})

describe('shouldHandleCanvasKey bodyActive (UXP-4)', () => {
  const root = document.createElement('div')
  it('ignores a key on the page body after a click outside the node builder', () => {
    expect(shouldHandleCanvasKey({
      target: document.body, root, inView: true, modifier: false, defaultPrevented: false, bodyActive: false,
    })).toBe(false)
  })

  it('still takes a key on the body while the canvas is active', () => {
    expect(shouldHandleCanvasKey({
      target: document.body, root, inView: true, modifier: false, defaultPrevented: false, bodyActive: true,
    })).toBe(true)
  })

  it('takes a key inside the root whatever bodyActive says', () => {
    const inner = document.createElement('button')
    root.appendChild(inner)
    expect(shouldHandleCanvasKey({
      target: inner, root, inView: true, modifier: false, defaultPrevented: false, bodyActive: false,
    })).toBe(true)
  })
})

describe('ports (FC-1 / UXP-1 / UXP-18)', () => {
  it('knows which types have no input or no output', () => {
    expect(hasInputPort('ticker')).toBe(false)
    expect(hasInputPort('stop_loss')).toBe(false)
    expect(hasOutputPort('entry')).toBe(false)
    expect(hasOutputPort('exit')).toBe(false)
    expect(hasOutputPort('trailing_stop')).toBe(false)
    expect(hasInputPort('rsi') && hasOutputPort('rsi')).toBe(true)
    // Unknown types (read-only viewer only) keep both.
    expect(hasInputPort('stochastic') && hasOutputPort('stochastic')).toBe(true)
  })

  it('canWire needs an output at the source and an input at the target', () => {
    expect(canWire('rsi', 'above')).toBe(true)
    expect(canWire('entry', 'rsi')).toBe(false)
    expect(canWire('rsi', 'ticker')).toBe(false)
    expect(canWire('above', 'slippage')).toBe(false)
  })
})

describe('nodeLabel (UXP-6)', () => {
  it('names a node by type, with the symbol for a Ticker', () => {
    expect(nodeLabel({ type: 'rsi', params: {} })).toBe('rsi')
    expect(nodeLabel({ type: 'ticker', params: { symbol: 'AAPL' } })).toBe('ticker AAPL')
    expect(nodeLabel(undefined)).toBe('')
  })
})

describe('markHotEdges (spec S11, FP-12)', () => {
  const e = (id: string, source: string, target: string, hot = false) => ({ id, source, target, data: { hot } })

  it('marks only the hovered node\'s wires and keeps every other edge object', () => {
    const edges = [e('w1', 'a', 'b'), e('w2', 'b', 'c'), e('w3', 'c', 'd')]
    const next = markHotEdges(edges, 'b')
    expect(next.map(x => x.data.hot)).toEqual([true, true, false])
    expect(next[2]).toBe(edges[2])
    // Leaving clears them; nothing hot and nothing hovered returns the same array.
    const cleared = markHotEdges(next, null)
    expect(cleared.map(x => x.data.hot)).toEqual([false, false, false])
    expect(markHotEdges(cleared, null)).toBe(cleared)
    expect(markHotEdges(edges, 'zzz')).toBe(edges)
  })
})
