import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'
import {
  loadWatchlist,
  saveWatchlist,
  dropDuplicate,
  genGroupId,
  emptyState,
} from './watchlistStorage'
import type { WatchlistState, WatchlistGroup } from './watchlistStorage'

const POLL_INTERVAL = 30_000 // 30 seconds

interface Quote {
  symbol: string
  price: number | null
  change_pct: number | null
  error?: string
}

/** Parse a comma/whitespace-separated ticker string into deduped uppercase symbols. */
function parseTickerInput(input: string): string[] {
  return [...new Set(
    input.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
  )]
}

/**
 * Collect all tickers across groups + ungrouped in flat order.
 * Used to keep the quotes cache fresh.
 */
function allTickers(state: WatchlistState): string[] {
  return [
    ...state.ungrouped,
    ...state.groups.flatMap(g => g.tickers),
  ]
}

// ---------------------------------------------------------------------------
// Drag data helpers
// ---------------------------------------------------------------------------

/** Drag source identifies where a ticker came from. */
interface DragSource {
  ticker: string
  /** null = ungrouped */
  groupId: string | null
  /** index within the source list */
  index: number
}

const DRAG_KEY = 'watchlist-drag'

function encodeDrag(src: DragSource): string {
  return JSON.stringify(src)
}

function decodeDrag(raw: string): DragSource | null {
  try { return JSON.parse(raw) } catch { return null }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function WatchlistPanel({
  currentSymbol,
  onSymbolClick,
}: {
  currentSymbol: string
  onSymbolClick: (symbol: string) => void
}) {
  const [state, setState] = useState<WatchlistState>(emptyState)
  const [corruptBanner, setCorruptBanner] = useState(false)
  const [quotes, setQuotes] = useState<Map<string, Quote>>(new Map())
  const [hoveredSymbol, setHoveredSymbol] = useState<string | null>(null)

  // Bulk-add input state
  const [showAddInput, setShowAddInput] = useState(false)
  const [addInputValue, setAddInputValue] = useState('')
  const addInputRef = useRef<HTMLInputElement>(null)

  // Drag-over tracking: { groupId: string | null (ungrouped), index: number }
  const [dragOver, setDragOver] = useState<{ groupId: string | null; index: number } | null>(null)
  const dragSrcRef = useRef<DragSource | null>(null)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Load on mount
  useEffect(() => {
    const { state: loaded, wasCorrupt } = loadWatchlist()
    setState(loaded)
    if (wasCorrupt) setCorruptBanner(true)
  }, [])

  // Persist to localStorage whenever state changes
  useEffect(() => {
    saveWatchlist(state)
  }, [state])

  // Focus add input when shown
  useEffect(() => {
    if (showAddInput) addInputRef.current?.focus()
  }, [showAddInput])

  // Fetch quotes for all watchlist symbols
  const fetchQuotes = useCallback(async () => {
    const tickers = allTickers(state)
    if (tickers.length === 0) return
    try {
      const { data } = await api.post('/api/quotes', tickers)
      const map = new Map<string, Quote>()
      for (const q of data as Quote[]) map.set(q.symbol, q)
      setQuotes(map)
    } catch {
      // silently ignore — prices will just be stale
    }
  }, [state])

  // Poll quotes on mount + interval
  useEffect(() => {
    fetchQuotes()
    timerRef.current = setInterval(fetchQuotes, POLL_INTERVAL)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [fetchQuotes])

  // ---------------------------------------------------------------------------
  // State mutations
  // ---------------------------------------------------------------------------

  const addTickerToUngrouped = useCallback((sym: string) => {
    setState(prev => {
      const upper = sym.toUpperCase()
      const exists = prev.ungrouped.some(t => t.toUpperCase() === upper) ||
        prev.groups.some(g => g.tickers.some(t => t.toUpperCase() === upper))
      if (exists) return prev
      return { ...prev, ungrouped: [...prev.ungrouped, upper] }
    })
  }, [])

  const addCurrent = useCallback(() => {
    addTickerToUngrouped(currentSymbol)
  }, [currentSymbol, addTickerToUngrouped])

  const removeSymbol = useCallback((sym: string, groupId: string | null) => {
    setState(prev => {
      if (groupId === null) {
        return { ...prev, ungrouped: prev.ungrouped.filter(t => t !== sym) }
      }
      return {
        ...prev,
        groups: prev.groups.map(g =>
          g.id === groupId
            ? { ...g, tickers: g.tickers.filter(t => t !== sym) }
            : g
        ),
      }
    })
  }, [])

  /** Commit the bulk-add input: parse, dedupe, append to ungrouped. */
  const commitAddInput = useCallback(() => {
    const parsed = parseTickerInput(addInputValue)
    if (parsed.length > 0) {
      setState(prev => {
        const allExisting = new Set(allTickers(prev).map(t => t.toUpperCase()))
        const newOnes = parsed.filter(s => !allExisting.has(s))
        return newOnes.length > 0
          ? { ...prev, ungrouped: [...prev.ungrouped, ...newOnes] }
          : prev
      })
    }
    setAddInputValue('')
  }, [addInputValue])

  // Group management
  const addGroup = useCallback(() => {
    setState(prev => {
      const n = prev.groups.length + 1
      const name = `Group ${n}`
      const newGroup: WatchlistGroup = {
        id: genGroupId(),
        name,
        tickers: [],
        collapsed: false,
      }
      return { ...prev, groups: [...prev.groups, newGroup] }
    })
  }, [])

  const removeGroup = useCallback((groupId: string) => {
    setState(prev => {
      const group = prev.groups.find(g => g.id === groupId)
      if (!group) return prev
      // Move group's tickers back to ungrouped (dedupe)
      const movedBack = group.tickers.filter(t => {
        const upper = t.toUpperCase()
        return !prev.ungrouped.some(u => u.toUpperCase() === upper)
      })
      return {
        groups: prev.groups.filter(g => g.id !== groupId),
        ungrouped: [...prev.ungrouped, ...movedBack],
      }
    })
  }, [])

  const renameGroup = useCallback((groupId: string, name: string) => {
    setState(prev => ({
      ...prev,
      groups: prev.groups.map(g => g.id === groupId ? { ...g, name } : g),
    }))
  }, [])

  const toggleCollapse = useCallback((groupId: string) => {
    setState(prev => ({
      ...prev,
      groups: prev.groups.map(g =>
        g.id === groupId ? { ...g, collapsed: !g.collapsed } : g
      ),
    }))
  }, [])

  // ---------------------------------------------------------------------------
  // Drag handlers (extended for cross-group moves)
  // ---------------------------------------------------------------------------

  const handleDragStart = useCallback((
    ticker: string,
    groupId: string | null,
    index: number,
    e: React.DragEvent,
  ) => {
    const src: DragSource = { ticker, groupId, index }
    dragSrcRef.current = src
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData(DRAG_KEY, encodeDrag(src))
    // Firefox fallback
    e.dataTransfer.setData('text/plain', ticker)
  }, [])

  const handleDragOver = useCallback((
    groupId: string | null,
    index: number,
    e: React.DragEvent,
  ) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOver({ groupId, index })
  }, [])

  const handleDragLeave = useCallback(() => {
    setDragOver(null)
  }, [])

  const handleDrop = useCallback((
    targetGroupId: string | null,
    targetIndex: number,
    e: React.DragEvent,
  ) => {
    e.preventDefault()
    setDragOver(null)

    const rawData = e.dataTransfer.getData(DRAG_KEY)
    const src: DragSource | null = rawData ? decodeDrag(rawData) : dragSrcRef.current
    dragSrcRef.current = null

    if (!src) return

    const { ticker, groupId: srcGroupId, index: srcIndex } = src

    // Same list, same position — no-op
    if (srcGroupId === targetGroupId && srcIndex === targetIndex) return

    setState(prev => {
      // Remove from source
      let next: WatchlistState
      if (srcGroupId === null) {
        next = { ...prev, ungrouped: prev.ungrouped.filter((_, i) => i !== srcIndex) }
      } else {
        next = {
          ...prev,
          groups: prev.groups.map(g =>
            g.id === srcGroupId
              ? { ...g, tickers: g.tickers.filter((_, i) => i !== srcIndex) }
              : g
          ),
        }
      }

      // Insert into target
      if (targetGroupId === null) {
        const arr = [...next.ungrouped]
        // Adjust targetIndex if moving within the same list
        const insertAt = srcGroupId === null && srcIndex < targetIndex
          ? targetIndex - 1
          : targetIndex
        arr.splice(insertAt, 0, ticker)
        next = { ...next, ungrouped: arr }
      } else {
        next = {
          ...next,
          groups: next.groups.map(g => {
            if (g.id !== targetGroupId) return g
            const arr = [...g.tickers]
            const insertAt = srcGroupId === targetGroupId && srcIndex < targetIndex
              ? targetIndex - 1
              : targetIndex
            arr.splice(insertAt, 0, ticker)
            return { ...g, tickers: arr }
          }),
        }
      }

      // Enforce uniqueness: winner is the target location
      return dropDuplicate(next, ticker, targetGroupId)
    })
  }, [])

  const handleDragEnd = useCallback(() => {
    dragSrcRef.current = null
    setDragOver(null)
  }, [])

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  function renderTicker(
    sym: string,
    groupId: string | null,
    index: number,
  ) {
    const q = quotes.get(sym)
    const isActive = sym === currentSymbol.toUpperCase()
    const isHovered = hoveredSymbol === sym
    const isDragOver = dragOver?.groupId === groupId && dragOver.index === index
    const changePct = q?.change_pct ?? 0
    const changeColor =
      changePct > 0 ? 'var(--accent-green)'
      : changePct < 0 ? 'var(--accent-red)'
      : 'var(--text-muted)'
    const changePrefix = changePct > 0 ? '+' : ''

    return (
      <div
        key={`${groupId ?? 'ug'}-${sym}`}
        draggable
        onDragStart={e => handleDragStart(sym, groupId, index, e)}
        onDragOver={e => handleDragOver(groupId, index, e)}
        onDragLeave={handleDragLeave}
        onDrop={e => handleDrop(groupId, index, e)}
        onDragEnd={handleDragEnd}
        style={{
          ...styles.row,
          background: isDragOver
            ? 'var(--accent-primary-dim, rgba(99,102,241,0.15))'
            : isActive
              ? 'var(--bg-panel-hover)'
              : isHovered
                ? 'var(--bg-input)'
                : 'transparent',
          borderTop: isDragOver ? '2px solid var(--accent-primary)' : '2px solid transparent',
          cursor: 'grab',
        }}
        onClick={() => onSymbolClick(sym)}
        onMouseEnter={() => setHoveredSymbol(sym)}
        onMouseLeave={() => setHoveredSymbol(null)}
      >
        <span style={styles.dragHandle} aria-hidden>⋮⋮</span>
        <span style={{ ...styles.symbol, color: isActive ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
          {sym}
        </span>
        <span style={styles.priceBlock}>
          {q?.price != null ? (
            <>
              <span style={styles.price}>{q.price.toFixed(2)}</span>
              <span style={{ ...styles.change, color: changeColor }}>
                {changePrefix}{changePct.toFixed(2)}%
              </span>
            </>
          ) : q?.error ? (
            <span style={styles.error} title={q.error}>!</span>
          ) : (
            <span style={styles.loading}>...</span>
          )}
        </span>
        {isHovered && (
          <button
            style={styles.removeBtn}
            onClick={e => { e.stopPropagation(); removeSymbol(sym, groupId) }}
            title={`Remove ${sym}`}
          >
            ×
          </button>
        )}
      </div>
    )
  }

  /** Drop zone appended after the last item in each list. */
  function renderAppendDropZone(groupId: string | null, listLength: number) {
    const isActive = dragOver?.groupId === groupId && dragOver.index === listLength
    return (
      <div
        key={`dropzone-${groupId ?? 'ug'}`}
        style={{
          ...styles.appendZone,
          borderTop: isActive ? '2px solid var(--accent-primary)' : '2px solid transparent',
          background: isActive ? 'var(--accent-primary-dim, rgba(99,102,241,0.08))' : 'transparent',
        }}
        onDragOver={e => handleDragOver(groupId, listLength, e)}
        onDragLeave={handleDragLeave}
        onDrop={e => handleDrop(groupId, listLength, e)}
      />
    )
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const isEmpty = state.ungrouped.length === 0 && state.groups.length === 0

  return (
    <div style={styles.container}>
      {/* Corrupt data banner */}
      {corruptBanner && (
        <div
          style={styles.corruptBanner}
          onClick={() => setCorruptBanner(false)}
          title="Click to dismiss"
        >
          ⚠ Watchlist reset due to corrupt data. Click to dismiss.
        </div>
      )}

      {/* Header */}
      <div style={styles.header}>
        <span style={styles.title}>Watchlist</span>
        <div style={styles.headerActions}>
          <button
            onClick={addGroup}
            style={styles.addLabelBtn}
            title="New group"
          >
            + Group
          </button>
          <button
            onClick={() => setShowAddInput(v => !v)}
            style={styles.addLabelBtn}
            title="Add tickers"
          >
            + Add
          </button>
          <button
            onClick={addCurrent}
            style={styles.addBtn}
            title={`Add ${currentSymbol.toUpperCase()}`}
          >
            +
          </button>
        </div>
      </div>

      {/* Bulk-add input row */}
      {showAddInput && (
        <div style={styles.addInputRow}>
          <input
            ref={addInputRef}
            value={addInputValue}
            onChange={e => setAddInputValue(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { e.preventDefault(); commitAddInput() }
              if (e.key === 'Escape') { setShowAddInput(false); setAddInputValue('') }
            }}
            onBlur={commitAddInput}
            placeholder="e.g. AAPL, MSFT, GOOGL"
            style={styles.addInput}
          />
        </div>
      )}

      {/* List */}
      <div style={styles.list}>
        {isEmpty && (
          <div style={styles.emptyMsg}>
            Click + to add {currentSymbol}
          </div>
        )}

        {/* Groups */}
        {state.groups.map(group => (
          <div key={group.id} style={styles.groupContainer}>
            {/* Group header row */}
            <div style={styles.groupHeader}>
              <button
                style={styles.collapseBtn}
                onClick={() => toggleCollapse(group.id)}
                title={group.collapsed ? 'Expand group' : 'Collapse group'}
              >
                <span style={{
                  display: 'inline-block',
                  transform: group.collapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
                  transition: 'transform 0.15s',
                  fontSize: 10,
                  lineHeight: 1,
                }}>▼</span>
              </button>
              <GroupNameEditor
                name={group.name}
                onRename={name => renameGroup(group.id, name)}
              />
              <span style={styles.groupCount}>({group.tickers.length})</span>
              <button
                style={styles.removeGroupBtn}
                onClick={() => removeGroup(group.id)}
                title={`Remove group "${group.name}" (tickers move to ungrouped)`}
              >
                ×
              </button>
            </div>

            {/* Group tickers */}
            {!group.collapsed && (
              <div style={styles.groupTickers}>
                {group.tickers.map((sym, i) =>
                  renderTicker(sym, group.id, i)
                )}
                {renderAppendDropZone(group.id, group.tickers.length)}
              </div>
            )}
          </div>
        ))}

        {/* Ungrouped tickers */}
        {state.ungrouped.map((sym, i) =>
          renderTicker(sym, null, i)
        )}
        {renderAppendDropZone(null, state.ungrouped.length)}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inline group name editor (click-to-edit)
// ---------------------------------------------------------------------------

function GroupNameEditor({ name, onRename }: { name: string; onRename: (n: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(name)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => { setValue(name) }, [name])
  useEffect(() => { if (editing) ref.current?.select() }, [editing])

  const commit = () => {
    const trimmed = value.trim()
    if (trimmed && trimmed !== name) onRename(trimmed)
    else setValue(name)
    setEditing(false)
  }

  if (editing) {
    return (
      <input
        ref={ref}
        value={value}
        onChange={e => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); commit() }
          if (e.key === 'Escape') { setValue(name); setEditing(false) }
        }}
        style={styles.groupNameInput}
        onClick={e => e.stopPropagation()}
      />
    )
  }

  return (
    <span
      style={styles.groupName}
      onDoubleClick={() => setEditing(true)}
      title="Double-click to rename"
    >
      {name}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
  },
  corruptBanner: {
    background: 'rgba(239,68,68,0.15)',
    borderBottom: '1px solid rgba(239,68,68,0.4)',
    color: 'var(--accent-red, #ef4444)',
    fontSize: 11,
    padding: '5px 10px',
    cursor: 'pointer',
    flexShrink: 0,
    lineHeight: 1.4,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 10px',
    borderBottom: '1px solid var(--border-light)',
    flexShrink: 0,
  },
  title: {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--text-secondary)',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
  },
  headerActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  addLabelBtn: {
    background: 'none',
    border: '1px solid var(--border-light)',
    color: 'var(--text-secondary)',
    borderRadius: 4,
    height: 22,
    fontSize: 11,
    lineHeight: '20px',
    cursor: 'pointer',
    padding: '0 6px',
    whiteSpace: 'nowrap' as const,
  },
  addBtn: {
    background: 'none',
    border: '1px solid var(--border-light)',
    color: 'var(--text-secondary)',
    borderRadius: 4,
    width: 22,
    height: 22,
    fontSize: 15,
    lineHeight: '20px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
  },
  addInputRow: {
    padding: '4px 8px',
    borderBottom: '1px solid var(--border-light)',
    flexShrink: 0,
  },
  addInput: {
    width: '100%',
    boxSizing: 'border-box' as const,
    background: 'var(--bg-input)',
    border: '1px solid var(--border-light)',
    borderRadius: 4,
    color: 'var(--text-primary)',
    fontSize: 12,
    padding: '3px 6px',
    outline: 'none',
  },
  list: {
    flex: 1,
    overflowY: 'auto' as const,
    overflowX: 'hidden' as const,
  },
  emptyMsg: {
    padding: '16px 10px',
    fontSize: 12,
    color: 'var(--text-muted)',
    textAlign: 'center' as const,
  },
  // Group
  groupContainer: {
    borderBottom: '1px solid var(--border-light)',
  },
  groupHeader: {
    display: 'flex',
    alignItems: 'center',
    padding: '3px 6px 3px 4px',
    gap: 4,
    background: 'var(--bg-input, rgba(255,255,255,0.04))',
    cursor: 'default',
    userSelect: 'none' as const,
  },
  collapseBtn: {
    background: 'none',
    border: 'none',
    color: 'var(--text-muted)',
    cursor: 'pointer',
    padding: '0 2px',
    lineHeight: 1,
    fontSize: 12,
    display: 'flex',
    alignItems: 'center',
  },
  groupName: {
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--text-secondary)',
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
    cursor: 'default',
  },
  groupNameInput: {
    flex: 1,
    fontSize: 11,
    fontWeight: 600,
    background: 'var(--bg-surface)',
    border: '1px solid var(--accent-primary)',
    borderRadius: 3,
    color: 'var(--text-primary)',
    padding: '1px 4px',
    outline: 'none',
    minWidth: 0,
  },
  groupCount: {
    fontSize: 10,
    color: 'var(--text-muted)',
    flexShrink: 0,
  },
  removeGroupBtn: {
    background: 'none',
    border: 'none',
    color: 'var(--text-muted)',
    cursor: 'pointer',
    fontSize: 14,
    lineHeight: 1,
    padding: '0 2px',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
  },
  groupTickers: {
    paddingLeft: 8,
  },
  appendZone: {
    height: 6,
    transition: 'background 0.1s, border-top 0.1s',
  },
  // Ticker row
  row: {
    display: 'flex',
    alignItems: 'center',
    padding: '4px 10px',
    height: 30,
    position: 'relative' as const,
    transition: 'background 0.1s',
    userSelect: 'none' as const,
  },
  dragHandle: {
    fontSize: 10,
    color: 'var(--text-muted)',
    marginRight: 4,
    opacity: 0.5,
    letterSpacing: '-2px',
    lineHeight: 1,
  },
  symbol: {
    fontWeight: 700,
    fontSize: 12,
    minWidth: 48,
  },
  priceBlock: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'flex-end',
    flex: 1,
    marginRight: 4,
  },
  price: {
    fontSize: 12,
    color: 'var(--text-primary)',
    fontVariantNumeric: 'tabular-nums',
  },
  change: {
    fontSize: 11,
    fontVariantNumeric: 'tabular-nums',
  },
  loading: {
    fontSize: 11,
    color: 'var(--text-muted)',
  },
  error: {
    fontSize: 11,
    color: 'var(--accent-red)',
    fontWeight: 700,
  },
  removeBtn: {
    position: 'absolute' as const,
    right: 4,
    top: '50%',
    transform: 'translateY(-50%)',
    background: 'var(--bg-input)',
    border: '1px solid var(--border-light)',
    color: 'var(--text-muted)',
    borderRadius: 3,
    width: 18,
    height: 18,
    fontSize: 13,
    lineHeight: '16px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
  },
}
