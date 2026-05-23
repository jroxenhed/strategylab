/**
 * TabMenu — keyboard-driven node-add overlay (Unit 6).
 *
 * Opens on Tab key press in editable mode.  Two-column category browser when
 * query is empty; flat scored list when query is non-empty.
 *
 * Keyboard nav:
 *   ↑ / ↓          — prev/next row in the active column
 *   ← / →          — switch columns (two-column mode)
 *   Enter          — create focused node (auto-wire if node selected)
 *   Shift+Enter    — create without auto-wire
 *   Esc / Tab      — close
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { NODE_CATALOG, catalogByCategory } from './catalog'
import { CATS, type CatKey } from './categories'
import { rankCatalog, friendlyName, type MatchResult } from './search'
import type { NodeCatalogEntry } from './catalog'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface TabMenuProps {
  open: boolean
  /** Position in screen coordinates where the menu should anchor. */
  screenPosition: { x: number; y: number }
  /** Graph-coordinate position to place the new node. */
  graphPosition: { x: number; y: number }
  /** Currently selected node id (for auto-wire hint). */
  selectedNodeId: string | null
  /** Allow auto-wire hint; controlled by the session-level toggle. */
  autoWire: boolean
  onToggleAutoWire(): void
  /** Create a node at graphPosition; if autoWire and withWire, auto-wire from selectedNodeId. */
  onCreate(catalogEntry: NodeCatalogEntry, withWire: boolean): void
  onClose(): void
}

// ---------------------------------------------------------------------------
// Highlighted text — bold matched characters
// ---------------------------------------------------------------------------

interface HighlightedNameProps {
  name: string
  indices: number[]
}

function HighlightedName({ name, indices }: HighlightedNameProps) {
  const friendly = friendlyName(name)
  const indexSet = new Set(indices)
  return (
    <span>
      {Array.from(friendly).map((ch, i) =>
        indexSet.has(i) ? (
          <strong key={i} style={{ fontWeight: 700, background: 'oklch(0.72 0.16 155 / 0.18)' }}>
            {ch}
          </strong>
        ) : (
          <span key={i}>{ch}</span>
        )
      )}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Category pill
// ---------------------------------------------------------------------------

interface CatPillProps {
  cat: CatKey
  glyph: string
}

function CatPill({ cat, glyph }: CatPillProps) {
  const color = CATS[cat]?.color ?? 'var(--nb-text-muted)'
  return (
    <div style={{
      width: 22,
      height: 22,
      borderRadius: 4,
      background: color,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexShrink: 0,
    }}>
      <span style={{
        fontFamily: 'var(--nb-font-mono)',
        fontWeight: 700,
        fontSize: 10,
        color: 'var(--nb-bg)',
        lineHeight: 1,
      }}>{glyph}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Category order for two-column mode
// ---------------------------------------------------------------------------

const CAT_ORDER: CatKey[] = ['ticker', 'indicator', 'comparison', 'logic', 'settings', 'output']

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TabMenu({
  open,
  screenPosition,
  selectedNodeId,
  autoWire,
  onToggleAutoWire,
  onCreate,
  onClose,
}: TabMenuProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')

  // Two-column state
  const [focusedCat, setFocusedCat] = useState<CatKey>(CAT_ORDER[0])
  const [focusedCatIndex, setFocusedCatIndex] = useState(0)
  const [focusedNodeIndex, setFocusedNodeIndex] = useState(0)
  // which column is active: 'cat' | 'node'
  const [activeCol, setActiveCol] = useState<'cat' | 'node'>('cat')

  // Flat list state
  const [focusedFlatIndex, setFocusedFlatIndex] = useState(0)

  // Reset state when menu opens
  useEffect(() => {
    if (open) {
      setQuery('')
      setFocusedCat(CAT_ORDER[0])
      setFocusedCatIndex(0)
      setFocusedNodeIndex(0)
      setActiveCol('cat')
      setFocusedFlatIndex(0)
      // Focus the input after a tick (React portal timing)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  // Derived data
  const byCategory = catalogByCategory()
  const isSearching = query.trim().length > 0

  const flatResults: MatchResult[] = isSearching
    ? rankCatalog(query.trim(), NODE_CATALOG)
    : []

  // Nodes in focused category (two-col mode)
  const catNodes: NodeCatalogEntry[] = byCategory[focusedCat] ?? []

  // Clamp indices when data changes
  const clampedFlatIndex = Math.min(focusedFlatIndex, Math.max(0, flatResults.length - 1))
  const clampedCatIndex = Math.min(focusedCatIndex, Math.max(0, CAT_ORDER.length - 1))
  const clampedNodeIndex = Math.min(focusedNodeIndex, Math.max(0, catNodes.length - 1))

  const confirm = useCallback(
    (entry: NodeCatalogEntry, withWire: boolean) => {
      onCreate(entry, withWire)
      onClose()
    },
    [onCreate, onClose]
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!open) return

      if (e.key === 'Escape' || e.key === 'Tab') {
        e.preventDefault()
        onClose()
        return
      }

      if (e.key === 'Enter') {
        e.preventDefault()
        if (isSearching) {
          const hit = flatResults[clampedFlatIndex]
          if (hit) {
            const entry = NODE_CATALOG.find(n => n.name === hit.name)
            if (entry) confirm(entry, autoWire && !e.shiftKey && !!selectedNodeId)
          }
        } else {
          if (activeCol === 'node' || catNodes.length > 0) {
            const entry = catNodes[clampedNodeIndex]
            if (entry) confirm(entry, autoWire && !e.shiftKey && !!selectedNodeId)
          }
        }
        return
      }

      if (isSearching) {
        // Flat list nav
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          setFocusedFlatIndex(i => Math.min(i + 1, flatResults.length - 1))
        } else if (e.key === 'ArrowUp') {
          e.preventDefault()
          setFocusedFlatIndex(i => Math.max(i - 1, 0))
        }
      } else {
        // Two-column nav
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          if (activeCol === 'cat') {
            const next = Math.min(clampedCatIndex + 1, CAT_ORDER.length - 1)
            setFocusedCatIndex(next)
            setFocusedCat(CAT_ORDER[next])
            setFocusedNodeIndex(0)
          } else {
            setFocusedNodeIndex(i => Math.min(i + 1, catNodes.length - 1))
          }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault()
          if (activeCol === 'cat') {
            const prev = Math.max(clampedCatIndex - 1, 0)
            setFocusedCatIndex(prev)
            setFocusedCat(CAT_ORDER[prev])
            setFocusedNodeIndex(0)
          } else {
            setFocusedNodeIndex(i => Math.max(i - 1, 0))
          }
        } else if (e.key === 'ArrowRight') {
          e.preventDefault()
          setActiveCol('node')
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault()
          setActiveCol('cat')
        }
      }
    },
    [
      open, isSearching, flatResults, clampedFlatIndex, activeCol, catNodes,
      clampedCatIndex, clampedNodeIndex, autoWire, selectedNodeId, confirm, onClose,
    ]
  )

  if (!open) return null

  // Clamp menu position to stay roughly within viewport
  const menuWidth = isSearching ? 340 : 520
  const left = Math.min(screenPosition.x, (typeof window !== 'undefined' ? window.innerWidth : 1200) - menuWidth - 16)
  const top = Math.min(screenPosition.y, (typeof window !== 'undefined' ? window.innerHeight : 900) - 420)

  const menuStyle: React.CSSProperties = {
    position: 'fixed',
    left: Math.max(8, left),
    top: Math.max(8, top),
    width: menuWidth,
    maxHeight: 420,
    background: 'oklch(0.18 0.014 250)',
    border: '1px solid oklch(0.34 0.020 250)',
    borderRadius: 'var(--nb-radius-menu)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    zIndex: 9999,
    fontFamily: 'var(--nb-font-sans)',
  }

  return (
    <div className="nodebuilder-root" style={menuStyle} onKeyDown={handleKeyDown}>
      {/* Header: search input + auto-wire toggle */}
      <div style={{
        padding: '8px 10px 6px',
        borderBottom: '1px solid oklch(0.28 0.018 250)',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}>
        {/* Auto-wire hint */}
        {selectedNodeId && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 11,
            color: 'var(--nb-text-muted)',
          }}>
            <span style={{ color: 'var(--nb-wire-hot)' }}>↪</span>
            <span>wire from <code style={{
              fontFamily: 'var(--nb-font-mono)',
              fontSize: 10,
              background: 'oklch(0.22 0.012 250)',
              padding: '1px 4px',
              borderRadius: 3,
            }}>{selectedNodeId}</code></span>
            <button
              onClick={onToggleAutoWire}
              style={{
                marginLeft: 'auto',
                fontSize: 10,
                padding: '1px 6px',
                borderRadius: 3,
                background: autoWire ? 'oklch(0.72 0.16 155 / 0.22)' : 'oklch(0.22 0.012 250)',
                border: `1px solid ${autoWire ? 'oklch(0.72 0.16 155 / 0.5)' : 'oklch(0.30 0.018 250)'}`,
                color: autoWire ? 'oklch(0.72 0.16 155)' : 'var(--nb-text-muted)',
                cursor: 'pointer',
              }}
            >
              {autoWire ? '↪ auto-wire on' : '↪ auto-wire off'}
            </button>
          </div>
        )}

        {/* Search input */}
        <input
          ref={inputRef}
          type="text"
          autoComplete="off"
          name={`nb-search-${Math.random().toString(36).slice(2)}`}
          data-1p-ignore=""
          data-lpignore="true"
          data-form-type="other"
          spellCheck={false}
          placeholder="Search nodes…"
          value={query}
          onChange={e => {
            setQuery(e.target.value)
            setFocusedFlatIndex(0)
          }}
          style={{
            background: 'oklch(0.13 0.010 250)',
            border: '1px solid oklch(0.30 0.018 250)',
            borderRadius: 4,
            padding: '5px 9px',
            fontSize: 13,
            color: 'var(--nb-text)',
            outline: 'none',
            width: '100%',
            boxSizing: 'border-box',
            fontFamily: 'var(--nb-font-sans)',
          }}
        />

        {/* Keyboard hint */}
        <div style={{ fontSize: 10, color: 'var(--nb-text-dim)', lineHeight: '14px' }}>
          {isSearching
            ? '↑↓ navigate · Enter confirm · Shift+Enter no-wire · Esc close'
            : '↑↓ category · →← switch col · Enter place · Esc close'}
        </div>
      </div>

      {/* Body */}
      {isSearching ? (
        <FlatList
          results={flatResults}
          focusedIndex={clampedFlatIndex}
          onHover={setFocusedFlatIndex}
          onConfirm={(entry) => confirm(entry, autoWire && !!selectedNodeId)}
        />
      ) : (
        <TwoColumnBrowser
          byCategory={byCategory}
          catOrder={CAT_ORDER}
          focusedCatIndex={clampedCatIndex}
          focusedCat={focusedCat}
          focusedNodeIndex={clampedNodeIndex}
          activeCol={activeCol}
          onHoverCat={(cat, idx) => {
            setFocusedCat(cat)
            setFocusedCatIndex(idx)
            setFocusedNodeIndex(0)
            setActiveCol('cat')
          }}
          onHoverNode={(idx) => {
            setFocusedNodeIndex(idx)
            setActiveCol('node')
          }}
          onConfirm={(entry) => confirm(entry, autoWire && !!selectedNodeId)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Flat list (search results)
// ---------------------------------------------------------------------------

interface FlatListProps {
  results: MatchResult[]
  focusedIndex: number
  onHover(idx: number): void
  onConfirm(entry: NodeCatalogEntry): void
}

function FlatList({ results, focusedIndex, onHover, onConfirm }: FlatListProps) {
  if (results.length === 0) {
    return (
      <div style={{
        padding: '24px 16px',
        textAlign: 'center',
        color: 'var(--nb-text-muted)',
        fontSize: 12,
      }}>
        No nodes match
      </div>
    )
  }

  return (
    <div style={{ overflowY: 'auto', flex: 1 }}>
      {results.map((r, i) => {
        const cat = r.cat as CatKey
        const catEntry = CATS[cat] ?? CATS.indicator
        const entry = NODE_CATALOG.find(n => n.name === r.name)
        const isFocused = i === focusedIndex

        return (
          <div
            key={r.name}
            onMouseEnter={() => onHover(i)}
            onClick={() => entry && onConfirm(entry)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 12px',
              cursor: 'pointer',
              background: isFocused ? 'oklch(0.24 0.016 250)' : 'transparent',
              borderLeft: isFocused ? `3px solid ${catEntry.color}` : '3px solid transparent',
            }}
          >
            <CatPill cat={cat} glyph={catEntry.glyph} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nb-text)' }}>
                <HighlightedName name={r.name} indices={r.matchedIndices} />
              </div>
              {entry?.desc && (
                <div style={{
                  fontSize: 10,
                  color: 'var(--nb-text-muted)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {entry.desc}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Two-column browser
// ---------------------------------------------------------------------------

interface TwoColumnBrowserProps {
  byCategory: Record<string, NodeCatalogEntry[]>
  catOrder: CatKey[]
  focusedCatIndex: number
  focusedCat: CatKey
  focusedNodeIndex: number
  activeCol: 'cat' | 'node'
  onHoverCat(cat: CatKey, idx: number): void
  onHoverNode(idx: number): void
  onConfirm(entry: NodeCatalogEntry): void
}

function TwoColumnBrowser({
  byCategory,
  catOrder,
  focusedCatIndex,
  focusedCat,
  focusedNodeIndex,
  activeCol,
  onHoverCat,
  onHoverNode,
  onConfirm,
}: TwoColumnBrowserProps) {
  const catNodes = byCategory[focusedCat] ?? []

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      {/* Left: category column */}
      <div style={{
        width: 160,
        borderRight: '1px solid oklch(0.26 0.018 250)',
        overflowY: 'auto',
        flexShrink: 0,
      }}>
        {catOrder.map((cat, i) => {
          const catEntry = CATS[cat]
          const count = byCategory[cat]?.length ?? 0
          const isFocused = i === focusedCatIndex
          return (
            <div
              key={cat}
              onMouseEnter={() => onHoverCat(cat, i)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                padding: '6px 10px',
                cursor: 'default',
                background: isFocused && activeCol === 'cat'
                  ? 'oklch(0.24 0.016 250)'
                  : isFocused
                    ? 'oklch(0.21 0.014 250)'
                    : 'transparent',
                borderLeft: isFocused ? `3px solid ${catEntry.color}` : '3px solid transparent',
              }}
            >
              <CatPill cat={cat} glyph={catEntry.glyph} />
              <span style={{
                fontSize: 11,
                color: 'var(--nb-text-secondary)',
                flex: 1,
                textTransform: 'capitalize',
              }}>
                {cat}
              </span>
              <span style={{
                fontSize: 10,
                color: 'var(--nb-text-dim)',
                fontFamily: 'var(--nb-font-mono)',
              }}>
                {count}
              </span>
              <span style={{ fontSize: 10, color: 'var(--nb-text-dim)' }}>▸</span>
            </div>
          )
        })}
      </div>

      {/* Right: nodes in focused category */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {catNodes.length === 0 ? (
          <div style={{
            padding: '24px 12px',
            color: 'var(--nb-text-muted)',
            fontSize: 12,
            textAlign: 'center',
          }}>
            No nodes in {focusedCat}
          </div>
        ) : (
          catNodes.map((entry, i) => {
            const cat = entry.cat as CatKey
            const catEntry = CATS[cat] ?? CATS.indicator
            const isFocused = i === focusedNodeIndex && activeCol === 'node'
            return (
              <div
                key={entry.name}
                onMouseEnter={() => onHoverNode(i)}
                onClick={() => onConfirm(entry)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 12px',
                  cursor: 'pointer',
                  background: isFocused ? 'oklch(0.24 0.016 250)' : 'transparent',
                  borderLeft: isFocused ? `3px solid ${catEntry.color}` : '3px solid transparent',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nb-text)' }}>
                    {friendlyName(entry.name)}
                  </div>
                  {entry.defaults.subtitle && (
                    <div style={{
                      fontSize: 10,
                      color: 'var(--nb-text-muted)',
                      fontFamily: 'var(--nb-font-mono)',
                    }}>
                      {entry.defaults.subtitle}
                    </div>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
