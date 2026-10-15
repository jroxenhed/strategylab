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
 *   Click outside  — close
 *
 * Rendered into document.body through a portal, so no panel of the app can
 * sit on top of it or shift its fixed position. Only nodes that compile acts
 * on are offered (catalog entries with compileActive: false are hidden).
 *
 * W6 (spec S43, item 6.D):
 * - Library assets join at runtime (never in catalog.generated.ts): `Rules`
 *   holds the built-in rule nodes plus every asset with a Rules palette
 *   entry, `Library` holds every asset and a "Manage assets…" link. The
 *   built-in rows draw at once; asset rows append when the list arrives.
 * - Choosing an asset places a locked instance of its latest version, wired
 *   like any node (it goes through `onCreate` as a subnet, then becomes the
 *   instance in the same undo step).
 * - Search by attribute: `@volume` lists only what reads or writes it;
 *   the matched attribute shows as a chip after the description.
 * - Inside a network, `Networks` adds `Subnet input` and `Subnet output`
 *   (the output row is disabled when one exists). Inside a locked asset
 *   every row is disabled under a note.
 *
 * W7 (specs S46, S49): the Code category holds the Wrangle. A new Wrangle
 * gets the default code `@out = @close` with its write name made unique,
 * in the same undo step. With code off on the server its row is disabled
 * with the tag `disabled`, and choosing it shows the code-off banner.
 */

import { useEffect, useId, useMemo, useRef, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { assetErrorText, cachedAsset, getAsset, type AssetFile } from '../../api/graphLibrary'
import type { GraphNode } from '../../api/nodebuilder'
import { NODE_CATALOG, type NodeCatalogEntry } from './catalog'
import { rankCatalog, entryLabel, type MatchResult } from './search'
import { clampMenuPosition, groupByCategory, menuCatalog } from './canvasHelpers'
import { currentNetworkOf, ensureLibrary, insertAssetInstance, openAssetManager, refreshLibrary, useAssetLibrary } from './assetUi'
import { noMatchText, TAB_MENU_TEXT } from './assetText'
import { assetRowOf, catPill, isAssetRow, LIBRARY_CAT, pillOf, rowLabel, type AssetRowEntry } from './assetRows'
import { insideLockedAsset } from './operations/collapse'
import { assetInstanceNode } from './operations/assets'
import { uniqueName } from './operations'
import { boundaryPortOf } from './rfMapping'
import { primaryWriteOf } from './streamLabels'
import { useNodeBuilderStore } from './store'
import { defaultWrangleCode, WRANGLE_TYPE } from './code/codeOps'
import { requestCodeBanner, useCodeEnabled } from './code/codeStore'
import { attrNamesInGraph } from './code/codeGraph'
import './assets.css'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface TabMenuProps {
  open: boolean
  /** Position in screen coordinates where the menu should anchor. */
  screenPosition: { x: number; y: number }
  /** Graph-coordinate position to place the new node. */
  graphPosition: { x: number; y: number }
  /**
   * The node a new node would auto-wire from, or null for none (nothing
   * selected, the selected node has no output, or a wire drop decides).
   */
  selectedNodeId: string | null
  /** Friendly name of that node for the hint (falls back to its id). */
  wireFromLabel?: string
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
  // `name` is the text shown (a row label), already friendly.
  const friendly = name
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
  color: string
  glyph: string
}

function CatPill({ color, glyph }: CatPillProps) {
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

// The catalog is static, so what the menu offers is worked out once at load.
const MENU_CATALOG: NodeCatalogEntry[] = menuCatalog()
const MENU_BY_CATEGORY: Record<string, NodeCatalogEntry[]> = groupByCategory(MENU_CATALOG)

// Category order. Rules and Library may be filled by assets at runtime, so
// they are kept even when the catalog has nothing for them.
const BASE_ORDER: string[] = ['ticker', 'indicator', 'comparison', 'logic', 'rules', 'code', 'settings', 'output', 'network', LIBRARY_CAT]

const MENU_HEIGHT = 420
const NO_ENTRIES: NodeCatalogEntry[] = []

/** Boundary rows offered inside a network (S38): built from the catalog. */
const BOUNDARY_ENTRIES: NodeCatalogEntry[] = NODE_CATALOG.filter(e => e.name === 'subnet_input' || e.name === 'subnet_output')

/** The lowest `port` no `subnet_input` of `networkId` uses yet. */
function freeInputPort(nodes: Record<string, GraphNode>, networkId: string): number {
  const used = new Set<number>()
  for (const n of Object.values(nodes)) {
    if (n.type !== 'subnet_input' || n.parent !== networkId) continue
    const k = boundaryPortOf(n)
    if (k !== null) used.add(k)
  }
  let k = 0
  while (used.has(k)) k += 1
  return k
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

// While closed, render nothing and do no work. The panel below mounts fresh on
// every open, so its search and focus state start clean each time.
export default function TabMenu(props: TabMenuProps) {
  if (!props.open) return null
  return <TabMenuPanel {...props} />
}

function TabMenuPanel({
  open,
  screenPosition,
  graphPosition,
  selectedNodeId,
  wireFromLabel,
  autoWire,
  onToggleAutoWire,
  onCreate,
  onClose,
}: TabMenuProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const inputName = `nb-search-${useId()}`
  const [query, setQuery] = useState('')

  // W6: where the menu is (network on screen), the library, and locks.
  const graph = useNodeBuilderStore(s => s.graph)
  const network = useNodeBuilderStore(s => currentNetworkOf(s))
  const libraryItems = useAssetLibrary(s => s.items)
  const libraryStatus = useAssetLibrary(s => s.status)
  const locked = !!graph && insideLockedAsset(graph.nodes, network)
  const hasOutput = !!graph && !!network && Object.values(graph.nodes).some(n => n.type === 'subnet_output' && n.parent === network)
  // The muted "loading assets…" row shows for at most 2 s.
  const [loadingShown, setLoadingShown] = useState(true)
  useEffect(() => {
    void ensureLibrary()
    const t = setTimeout(() => setLoadingShown(false), 2000)
    return () => clearTimeout(t)
  }, [])

  const assetRows = useMemo(() => libraryItems.map(assetRowOf), [libraryItems])
  const searchCatalog = useMemo(() => [...MENU_CATALOG, ...assetRows], [assetRows])
  const byName = useMemo(() => new Map(searchCatalog.map(e => [e.name, e])), [searchCatalog])
  const byCategory = useMemo(() => {
    const out: Record<string, NodeCatalogEntry[]> = { ...MENU_BY_CATEGORY }
    out.rules = [...(MENU_BY_CATEGORY.rules ?? []), ...assetRows.filter(r => r.asset.palette)]
    out[LIBRARY_CAT] = assetRows
    // Inside a network: its boundary nodes (S38). The input gets a free port.
    if (network && graph) {
      const port = freeInputPort(graph.nodes, network)
      const extra = BOUNDARY_ENTRIES.map(e => e.name === 'subnet_input'
        ? { ...e, defaults: { ...e.defaults, params: { ...e.defaults.params, port } } }
        : e)
      out.network = [...(MENU_BY_CATEGORY.network ?? []), ...extra]
    }
    return out
  }, [assetRows, network, graph])
  const catOrder = useMemo(
    () => BASE_ORDER.filter(c => c === LIBRARY_CAT || (byCategory[c]?.length ?? 0) > 0),
    [byCategory],
  )

  /** Why a row cannot be placed, or null. */
  const codeOn = useCodeEnabled()
  const rowDisabled = useCallback((e: NodeCatalogEntry): string | null => {
    if (locked) return TAB_MENU_TEXT.lockedNote
    if (e.name === 'subnet_output' && hasOutput) return 'already present'
    // S49: code off on the server.
    if (e.name === WRANGLE_TYPE && !codeOn) return 'disabled'
    return null
  }, [locked, hasOutput, codeOn])

  // Two-column state
  const [focusedCat, setFocusedCat] = useState<string>(BASE_ORDER[0])
  const [focusedCatIndex, setFocusedCatIndex] = useState(0)
  const [focusedNodeIndex, setFocusedNodeIndex] = useState(0)
  // which column is active: 'cat' | 'node'
  const [activeCol, setActiveCol] = useState<'cat' | 'node'>('cat')

  // Flat list state
  const [focusedFlatIndex, setFocusedFlatIndex] = useState(0)

  // Focus the search box on open. Also retried after a tick, because the key
  // press that opened the menu may still be moving focus.
  useEffect(() => {
    inputRef.current?.focus()
    const t = setTimeout(() => inputRef.current?.focus(), 0)
    return () => clearTimeout(t)
  }, [])

  // Close on a press anywhere outside the menu. Capture phase, so React Flow
  // stopping the event on the pane can't hide it from us.
  useEffect(() => {
    const onDown = (e: Event) => {
      const menu = menuRef.current
      if (menu && e.target instanceof Node && menu.contains(e.target)) return
      onClose()
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('mousedown', onDown, true)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('mousedown', onDown, true)
    }
  }, [onClose])

  // Derived data
  const trimmed = query.trim()
  const isSearching = trimmed.length > 0

  const flatResults: MatchResult[] = useMemo(
    // Asset rows carry their own `label`, which search shows and matches.
    () => (trimmed ? rankCatalog(trimmed, searchCatalog) : []),
    [trimmed, searchCatalog],
  )

  // Nodes in focused category (two-col mode)
  const catNodes: NodeCatalogEntry[] = byCategory[focusedCat] ?? NO_ENTRIES

  // Clamp indices when data changes
  const clampedFlatIndex = Math.min(focusedFlatIndex, Math.max(0, flatResults.length - 1))
  const clampedCatIndex = Math.min(focusedCatIndex, Math.max(0, catOrder.length - 1))
  const clampedNodeIndex = Math.min(focusedNodeIndex, Math.max(0, catNodes.length - 1))

  const confirm = useCallback(
    (entry: NodeCatalogEntry, withWire: boolean) => {
      if (entry.name === WRANGLE_TYPE && !codeOn) requestCodeBanner()
      if (rowDisabled(entry)) return
      if (isAssetRow(entry)) {
        placeAsset(entry, withWire, onCreate, graphPosition)
      } else if (entry.name === 'subnet_input') {
        placeBoundaryInput(entry, withWire, onCreate)
      } else if (entry.name === WRANGLE_TYPE) {
        // S46: a new Wrangle starts with `@out = @close` (the name made unique).
        createThenPatch(entry, withWire, onCreate, (g, node) => ({
          ...node,
          code: defaultWrangleCode(attrNamesInGraph(g)),
        }))
      } else if (entry.name === 'subnet') {
        // S43: an empty Subnet from the menu is a card (FA1), one undo step (UX-03).
        createThenPatch(entry, withWire, onCreate, (_g, node) => ({ ...node, meta: { ...(node.meta ?? {}), view: 'card' } }))
      } else {
        onCreate(entry, withWire)
      }
      onClose()
    },
    [onCreate, onClose, rowDisabled, graphPosition, codeOn]
  )

  // Fetch the file of the highlighted asset row early, so choosing it can
  // place the instance (with its promoted params) without waiting.
  const highlighted = isSearching
    ? byName.get(flatResults[clampedFlatIndex]?.name ?? '')
    : activeCol === 'node' ? catNodes[clampedNodeIndex] : undefined
  useEffect(() => {
    if (highlighted && isAssetRow(highlighted)) {
      getAsset(highlighted.asset.name, highlighted.asset.latest).catch(() => {})
    }
  }, [highlighted])

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
            const entry = byName.get(hit.name)
            if (entry) confirm(entry, autoWire && !e.shiftKey && !!selectedNodeId)
          }
        } else {
          if (activeCol === 'cat') {
            // Enter on a category moves into its nodes; it never creates a
            // node the user has not seen highlighted.
            if (catNodes.length > 0) {
              setActiveCol('node')
              setFocusedNodeIndex(0)
            }
          } else {
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
            const next = Math.min(clampedCatIndex + 1, catOrder.length - 1)
            setFocusedCatIndex(next)
            setFocusedCat(catOrder[next])
            setFocusedNodeIndex(0)
          } else {
            setFocusedNodeIndex(i => Math.min(i + 1, catNodes.length - 1))
          }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault()
          if (activeCol === 'cat') {
            const prev = Math.max(clampedCatIndex - 1, 0)
            setFocusedCatIndex(prev)
            setFocusedCat(catOrder[prev])
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
      byName, catOrder,
    ]
  )

  // Keep the menu inside the window.
  const menuWidth = isSearching ? 340 : 520
  const { x: left, y: top } = clampMenuPosition(
    screenPosition,
    { width: menuWidth, height: MENU_HEIGHT },
    {
      width: typeof window !== 'undefined' ? window.innerWidth : 1200,
      height: typeof window !== 'undefined' ? window.innerHeight : 900,
    },
    16,
  )

  const menuStyle: React.CSSProperties = {
    position: 'fixed',
    left,
    top,
    width: menuWidth,
    maxHeight: MENU_HEIGHT,
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

  const menu = (
    <div
      ref={menuRef}
      className="nodebuilder-root"
      role="dialog"
      aria-label="Add node"
      style={menuStyle}
      onKeyDown={handleKeyDown}
      // Clicking a row or header must not pull focus out of the search box,
      // or the arrow keys and Enter stop working.
      onMouseDown={e => {
        if (e.target !== inputRef.current) e.preventDefault()
      }}
    >
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
            }}>{wireFromLabel ?? selectedNodeId}</code></span>
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
          name={inputName}
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
            ? `↑↓ navigate · Enter confirm${selectedNodeId ? ' · Shift+Enter no-wire' : ''} · Esc close`
            : '↑↓ category · →← switch col · Enter open / place · Esc close'}
        </div>
      </div>

      {locked && (
        <div className="nb-tab-note" role="note" style={{ height: 22, boxSizing: 'border-box' }}>{TAB_MENU_TEXT.lockedNote}</div>
      )}

      {/* Body */}
      {isSearching ? (
        <FlatList
          query={trimmed}
          results={flatResults}
          byName={byName}
          focusedIndex={clampedFlatIndex}
          isDisabled={rowDisabled}
          onHover={setFocusedFlatIndex}
          onConfirm={(entry) => confirm(entry, autoWire && !!selectedNodeId)}
        />
      ) : (
        <TwoColumnBrowser
          byCategory={byCategory}
          catOrder={catOrder}
          isDisabled={rowDisabled}
          libraryNote={
            libraryStatus === 'error'
              ? { text: TAB_MENU_TEXT.unavailable, retry: () => { void refreshLibrary() } }
              : libraryStatus === 'loading' && loadingShown
                ? { text: TAB_MENU_TEXT.loading }
                : null
          }
          onManage={() => {
            onClose()
            openAssetManager({ insertAt: graphPosition })
          }}
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

  return typeof document !== 'undefined' ? createPortal(menu, document.body) : menu
}

// ---------------------------------------------------------------------------
// Flat list (search results)
// ---------------------------------------------------------------------------

interface FlatListProps {
  query: string
  results: MatchResult[]
  byName: Map<string, NodeCatalogEntry>
  focusedIndex: number
  isDisabled(entry: NodeCatalogEntry): string | null
  onHover(idx: number): void
  onConfirm(entry: NodeCatalogEntry): void
}

function FlatList({ query, results, byName, focusedIndex, isDisabled, onHover, onConfirm }: FlatListProps) {
  if (results.length === 0) {
    return (
      <div style={{
        padding: '24px 16px',
        textAlign: 'center',
        color: 'var(--nb-text-muted)',
        fontSize: 12,
      }}>
        {noMatchText(query)}
      </div>
    )
  }

  return (
    <div style={{ overflowY: 'auto', flex: 1 }}>
      {results.map((r, i) => {
        const entry = byName.get(r.name)
        if (!entry) return null
        const pill = pillOf(entry)
        const isFocused = i === focusedIndex
        const disabled = isDisabled(entry)

        return (
          <div
            key={r.name}
            data-testid={`nb-tab-row-${r.name}`}
            aria-disabled={disabled ? true : undefined}
            title={disabled ?? undefined}
            onMouseEnter={() => onHover(i)}
            onClick={() => !disabled && onConfirm(entry)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 12px',
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.45 : 1,
              background: isFocused ? 'oklch(0.24 0.016 250)' : 'transparent',
              borderLeft: isFocused ? `3px solid ${pill.color}` : '3px solid transparent',
            }}
          >
            <CatPill color={pill.color} glyph={pill.glyph} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nb-text)' }}>
                <HighlightedName name={entryLabel({ name: entry.name, label: rowLabel(entry) })} indices={r.matchedIndices} />
              </div>
              {entry.desc && (
                <div style={{
                  fontSize: 10,
                  color: 'var(--nb-text-muted)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {entry.desc}
                  {r.attr && (
                    <span
                      className={`nb-tab-attr-chip${r.attr.kind === 'write' ? ' nb-tab-attr-chip--write' : ''}`}
                      aria-label={`matches ${r.attr.name}`}
                      data-testid="nb-tab-attr-chip"
                    >
                      {r.attr.name}
                    </span>
                  )}
                </div>
              )}
            </div>
            {isAssetRow(entry) && (
              <span className="nb-tab-asset-kind">{entry.asset.palette ? 'RULES' : 'LIBRARY'}</span>
            )}
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
  catOrder: string[]
  focusedCatIndex: number
  focusedCat: string
  focusedNodeIndex: number
  activeCol: 'cat' | 'node'
  isDisabled(entry: NodeCatalogEntry): string | null
  /** A muted last row in Library (loading, or unavailable with a retry). */
  libraryNote: { text: string; retry?: () => void } | null
  onManage(): void
  onHoverCat(cat: string, idx: number): void
  onHoverNode(idx: number): void
  onConfirm(entry: NodeCatalogEntry): void
}

function TwoColumnBrowser({
  byCategory,
  catOrder,
  isDisabled,
  libraryNote,
  onManage,
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
          const catEntry = catPill(cat)
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
              <CatPill color={catEntry.color} glyph={catEntry.glyph} />
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
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1 }}>
        {catNodes.length === 0 && focusedCat === LIBRARY_CAT ? null : catNodes.length === 0 ? (
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
            const pill = pillOf(entry)
            const isFocused = i === focusedNodeIndex && activeCol === 'node'
            const disabled = isDisabled(entry)
            return (
              <div
                key={entry.name}
                data-testid={`nb-tab-row-${entry.name}`}
                aria-disabled={disabled ? true : undefined}
                title={disabled ?? undefined}
                onMouseEnter={() => onHoverNode(i)}
                onClick={() => !disabled && onConfirm(entry)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 12px',
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  opacity: disabled ? 0.45 : 1,
                  background: isFocused ? 'oklch(0.24 0.016 250)' : 'transparent',
                  borderLeft: isFocused ? `3px solid ${pill.color}` : '3px solid transparent',
                }}
              >
                {isAssetRow(entry) && <CatPill color={pill.color} glyph={pill.glyph} />}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nb-text)' }}>
                    {rowLabel(entry)}
                    {disabled && disabled !== TAB_MENU_TEXT.lockedNote && (
                      <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--nb-text-dim)' }}>{disabled}</span>
                    )}
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
        {focusedCat === LIBRARY_CAT && libraryNote && (
          <div
            className="nb-tab-note"
            role={libraryNote.retry ? 'button' : undefined}
            style={{ cursor: libraryNote.retry ? 'pointer' : 'default' }}
            onClick={libraryNote.retry}
          >
            {libraryNote.text}
          </div>
        )}
        </div>
        {focusedCat === LIBRARY_CAT && (
          <button type="button" className="nb-tab-footer-link" onClick={onManage}>
            {TAB_MENU_TEXT.manage}
          </button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Placing asset instances and boundary inputs (W6)
// ---------------------------------------------------------------------------

/**
 * Place a locked instance of the asset's latest version. When its file is
 * cached, the instance is made through `onCreate` (as a subnet, so the
 * canvas wires it as any node: a port drag, or the auto-wire), then turned
 * into the instance, all in one undo step. Otherwise the file is fetched
 * first and the instance placed at the menu point without a wire.
 */
function placeAsset(
  row: AssetRowEntry,
  withWire: boolean,
  onCreate: TabMenuProps['onCreate'],
  at: { x: number; y: number },
): void {
  const a = row.asset
  const file = cachedAsset(a.name, a.latest)
  if (!file) {
    // Not prefetched yet (FE-06): wait for the file, so the instance gets
    // its promoted params. When nothing changed meanwhile, place it as if
    // it had been cached (same spot, same auto-wire); otherwise place it at
    // the menu's spot without a wire. A failed fetch places nothing.
    const graphAtClick = useNodeBuilderStore.getState().graph
    getAsset(a.name, a.latest).then(
      f => {
        if (useNodeBuilderStore.getState().graph === graphAtClick) placeAssetFile(a, f, withWire, onCreate, at)
        else insertAssetInstance({ name: a.name, version: a.latest, promoted: f.promoted }, at)
      },
      err => useNodeBuilderStore.getState().showFlash(assetErrorText(err)),
    )
    return
  }
  placeAssetFile(a, file, withWire, onCreate, at)
}

function placeAssetFile(
  a: AssetRowEntry['asset'],
  file: AssetFile,
  withWire: boolean,
  onCreate: TabMenuProps['onCreate'],
  at: { x: number; y: number },
): void {
  const subnet = NODE_CATALOG.find(e => e.name === 'subnet')
  if (!subnet) {
    insertAssetInstance({ name: a.name, version: a.latest, promoted: file.promoted }, at)
    return
  }
  createThenPatch(subnet, withWire, onCreate, (g, node) => {
    const inst = assetInstanceNode(g, { name: a.name, version: a.latest, promoted: file.promoted }, node.parent, node.position)
    const params = { ...inst.params }
    prefillFirstAttr(g, node.id, file, params)
    return { ...inst, id: node.id, name: uniqueName(g, a.name, node.parent, node.id), params }
  })
}

/** S43: the first promoted `attr` param reads the wired source's primary write. */
function prefillFirstAttr(
  g: { nodes: Record<string, GraphNode>; wires: { from: string; to: string }[] },
  nodeId: string,
  file: AssetFile,
  params: Record<string, unknown>,
): void {
  const first = file.promoted.find(p => p.type === 'attr')
  if (!first) return
  const wire = g.wires.find(w => w.to === nodeId)
  const source = wire ? g.nodes[wire.from] : undefined
  const write = primaryWriteOf(source)
  if (write) params[first.name] = write
}

/** A `subnet_input` named `in<k>` after its free port (S38), in one undo step with its wire. */
function placeBoundaryInput(entry: NodeCatalogEntry, withWire: boolean, onCreate: TabMenuProps['onCreate']): void {
  const port = Number(entry.defaults.params.port ?? 0)
  createThenPatch(entry, withWire, onCreate, (g, node) => ({
    ...node,
    name: uniqueName(g, `in${port}`, node.parent, node.id),
    params: { ...node.params, port },
  }))
}

/**
 * Run the canvas's `onCreate`, then rewrite the node it made, as one undo
 * step (the canvas selects the new node, which is how it is found).
 */
function createThenPatch(
  entry: NodeCatalogEntry,
  withWire: boolean,
  onCreate: TabMenuProps['onCreate'],
  patch: (g: NonNullable<ReturnType<typeof useNodeBuilderStore.getState>['graph']>, node: GraphNode) => GraphNode,
): void {
  const s = useNodeBuilderStore.getState()
  const before = new Set(Object.keys(s.graph?.nodes ?? {}))
  s.beginBatch(`add ${entry.name}`)
  try {
    onCreate(entry, withWire)
    const after = useNodeBuilderStore.getState()
    const id = after.selectedNodeId
    const node = id && !before.has(id) ? after.graph?.nodes[id] : undefined
    if (node && after.graph) {
      after.commit(`add ${entry.name}`, g => {
        const n = g.nodes[node.id]
        return n ? { ...g, nodes: { ...g.nodes, [n.id]: patch(g, n) } } : g
      })
    }
  } finally {
    useNodeBuilderStore.getState().endBatch()
  }
}
