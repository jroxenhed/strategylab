/**
 * DataSheet: the node builder's Data Sheet drawer (F435 W4 item 4.B, S25).
 *
 * Houdini's Geometry Spreadsheet for streams: every bar and every attribute
 * of the selected wire or node, with the node that wrote each column. It
 * follows the selected wire, else the selected node, else the display-flag
 * node (or keeps a pinned target). Rows come page by page from POST /inspect
 * (the cook cache, D6); at most 2 000 are held and only the visible ones are
 * drawn.
 *
 * Item 4.D mounts it (bottomPanel slot) and passes what only the app knows:
 * the current cook id, the sidebar window, the trade entry times, the stale
 * flag and the cook actions. Open, height, follow and filter live in
 * `datasheet/sheetUi.ts` (`toggleSheet()` for the toolbar and the `S` key);
 * `datasheet/trades.ts` has `entryTimesFromTrades` for `tradeTimes`.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import type { Graph } from '../../api/nodebuilder'
import type { InspectColumn, InspectFilter, InspectWindow } from '../../api/nodebuilderInspect'
import { useNodeBuilderStore } from './store'
import { useTimezone } from '../../shared/utils/time'
import { useDiagnostics } from './useDiagnostics'
import { FIX_ERRORS_NOTE } from './useAutoCook'
import { currentParentId } from './store/view'
import { catVars, categoryOf } from './inspector/util'
import { HeaderCell, type WriterInfo } from './datasheet/HeaderCell'
import { copyText, formatNumber, formatRowCount, formatSheetTime } from './datasheet/format'
import { FILTER_HELP, filterSuggestions, filterText, parseSheetFilter, type FilterSuggestion } from './datasheet/filter'
import {
  defaultSheetHeight,
  SHEET_MAX_SHARE,
  SHEET_MIN_HEIGHT,
  useSheetUi,
} from './datasheet/sheetUi'
import {
  resolveSheetTarget,
  streamNodeId,
  targetBypassed,
  targetExists,
  targetKey,
  targetLabel,
  type SheetTarget,
} from './datasheet/target'
import { useSheetData, type SheetRow } from './datasheet/useSheetData'
import './datasheet/datasheet.css'
import { sortTimes } from './datasheet/trades'

/** Row height in px (S25). */
export const SHEET_ROW_HEIGHT = 28
/** Rows drawn above and below the visible ones. */
export const SHEET_OVERSCAN = 8
const HEADER_ROW = 28
const HANDLE = 6
const STRIP = 22
const HIST = 24
/** The histogram strip shows only when the drawer is at least this tall. */
const HIST_MIN_HEIGHT = 200
const TIME_COL = 140
const FLASH_MS = 1500
/** Below this column height the sheet drops to its minimum (S28: it gives way first). */
export const SHEET_SMALL_COLUMN = 700
/** Keyboard resize step of the handle (UX-13). */
const HANDLE_STEP = 16
/** The note when the server answered from the last good cook (fetch failed). */
export const SHEET_STALE_DATA_TEXT = 'Showing the last cook: fresh data could not be loaded.'

/** The drawer height to draw: the stored one, clamped to the column (UX-06). 0 = column unknown. */
export function clampSheetHeight(stored: number, columnHeight: number): number {
  if (!(columnHeight > 0)) return stored
  if (columnHeight < SHEET_SMALL_COLUMN) return SHEET_MIN_HEIGHT
  return Math.max(SHEET_MIN_HEIGHT, Math.min(stored, Math.floor(columnHeight * SHEET_MAX_SHARE)))
}

/** The no-cook message (S25 "no cook" state, UX-08/UX-09). */
export function noCookMessage(o: {
  readOnly: boolean
  autoCook: boolean
  previewPhase: string
  blockedByErrors: boolean
  errorCount: number
  hasTicker: boolean
}): string {
  if (o.readOnly) return 'Edit this graph to cook its data.'
  if (!o.autoCook) return 'Run the backtest (⌘↵) or turn on auto cook to see data here.'
  if (o.blockedByErrors) return `Fix the errors to cook (${o.errorCount} ${o.errorCount === 1 ? 'error' : 'errors'}).`
  if (!o.hasTicker) return 'Add a Ticker node to cook.'
  if (o.previewPhase === 'failed') return 'Auto cook failed. Run the backtest (⌘↵) or fix the graph to see data here.'
  return 'loading rows…'
}

export interface DataSheetProps {
  /** The newest cook (preview or backtest), or null when nothing has cooked. */
  cookId: string | null
  /** The sidebar window (D11): sent again only when the cook has expired. */
  window: InspectWindow | null
  /** Trade entry times of the shown graph result, or null when there is none. */
  tradeTimes?: readonly (string | number)[] | null
  /** The graph changed since `cookId` was made (shows the amber bar). */
  stale?: boolean
  /** Whether auto cook is on (the no-cook state then hides its button). */
  autoCook?: boolean
  /** Run the preview cook (the stale bar's `Cook`). */
  onCook?(): void
  /** Run the backtest cook (the no-cook state). */
  onRunBacktest?(): void
  /** Turn auto cook on (the no-cook state). */
  onAutoCookOn?(): void
  /** Make an editable copy of a read-only graph (the no-cook state on the read-only view). */
  onEditGraph?(): void
  /** A row was clicked: the chart can move its crosshair there. */
  onRowClick?(time: string | number, index: number): void
  /** The graph on screen when it is not the store's (read-only view). */
  graph?: Graph | null
  /** Overrides the sheet's own open state. */
  open?: boolean
  /** Close button; defaults to closing through the sheet store. */
  onClose?(): void
}

function colWidth(c: InspectColumn): number {
  return Math.max(96, c.name.length * 7 + 64)
}

function detailValue(v: unknown): string {
  if (typeof v === 'number') return Number.isFinite(v) ? String(Number(v.toFixed(4))) : String(v)
  return String(v)
}

interface MenuState {
  x: number
  y: number
  column: InspectColumn
}

export function DataSheet(props: DataSheetProps) {
  const ui = useSheetUi()
  const open = props.open ?? ui.open
  if (!open) return null
  return <DataSheetBody {...props} />
}

function DataSheetBody(props: DataSheetProps) {
  const { cookId, window: win, tradeTimes = null, stale = false, autoCook = false } = props
  const ui = useSheetUi()
  // Times follow the app's ET/local switch (DV-10): re-render on a toggle.
  useTimezone()

  // ---- graph and target ------------------------------------------------
  const storeGraph = useNodeBuilderStore(s => s.graph)
  const graph = props.graph !== undefined ? props.graph : storeGraph
  const selectedNodeId = useNodeBuilderStore(s => s.selectedNodeId)
  const selectedWireIds = useNodeBuilderStore(s => s.selectedWireIds)
  const parentId = useNodeBuilderStore(s => currentParentId(s, graph))
  const previewCook = useNodeBuilderStore(s => s.cooks.preview)
  const diag = useDiagnostics()

  const followed = useMemo(
    () => resolveSheetTarget(graph, { selectedNodeId, selectedWireIds }, parentId),
    [graph, selectedNodeId, selectedWireIds, parentId],
  )
  const pinnedLive = ui.follow === 'pinned' && ui.pinned != null && targetExists(graph, ui.pinned)
  const resolved = pinnedLive ? ui.pinned : followed
  // Keep one object per target, so effects keyed on it run only on a real change.
  // The kind is compared too (DV-9): 'display xb' and 'node xb' share a
  // request key but not a chip label.
  const targetRef = useRef<SheetTarget | null>(null)
  if (targetKey(targetRef.current) !== targetKey(resolved) || targetRef.current?.kind !== resolved?.kind) targetRef.current = resolved
  const target = targetRef.current

  // A pinned target that was deleted: back to follow mode (S25 states).
  useEffect(() => {
    if (ui.follow === 'pinned' && ui.pinned && graph && !targetExists(graph, ui.pinned)) {
      useSheetUi.getState().setFollow('follow')
      useNodeBuilderStore.getState().showFlash('Pinned target was deleted')
    }
  }, [ui.follow, ui.pinned, graph])

  // ---- filter ----------------------------------------------------------
  const applied = useMemo(() => {
    const p = parseSheetFilter(ui.filter)
    return p.ok ? p.filter : null
  }, [ui.filter])
  const [draft, setDraft] = useState(ui.filter)
  const [draftFocused, setDraftFocused] = useState(false)
  const [suggestIdx, setSuggestIdx] = useState(-1)
  useEffect(() => { setDraft(ui.filter) }, [ui.filter])
  const draftValid = useMemo(() => parseSheetFilter(draft).ok, [draft])

  // ---- layout ----------------------------------------------------------
  const [dragHeight, setDragHeight] = useState<number | null>(null)
  // The column's height, measured after layout and on window resize (no
  // ResizeObserver needed: the column follows the window). 0 = not known.
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [columnH, setColumnH] = useState(0)
  useEffect(() => {
    const measure = () => setColumnH(rootRef.current?.parentElement?.clientHeight ?? 0)
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  const baseH = clampSheetHeight(ui.height, columnH)
  const height = dragHeight ?? baseH
  const [detailOpen, setDetailOpen] = useState(true)
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set())
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [focusRow, setFocusRow] = useState<number | null>(null)
  const [jump, setJump] = useState<{ trade: number; row: number } | null>(null)
  const [flashRow, setFlashRow] = useState<number | null>(null)

  // ---- data ------------------------------------------------------------
  const graphRef = useRef(graph)
  graphRef.current = graph
  const winRef = useRef(win)
  winRef.current = win
  const getFallback = useCallback(() => ({ graph: graphRef.current, window: winRef.current }), [])

  const showHist = baseH >= HIST_MIN_HEIGHT
  const headH = HEADER_ROW + (showHist ? HIST : 0)

  // The table's height comes from the drawer height, not from measuring, so
  // the visible row count is known before layout (and in tests). The page
  // requests use the height without the strips: a row or two more is fine.
  const firstVisible = Math.floor(scrollTop / SHEET_ROW_HEIGHT)
  const maxRowsH = Math.max(SHEET_ROW_HEIGHT, baseH - HANDLE - HEADER_ROW - headH)
  const sheet = useSheetData({
    target,
    cookId,
    filter: applied,
    range: { first: firstVisible, last: firstVisible + Math.ceil(maxRowsH / SHEET_ROW_HEIGHT) },
    getFallback,
  })
  const data = sheet.data
  const current = data != null && data.key === sheet.key
  const detail = data?.detail ?? []
  const staleData = data?.staleData === true
  const stripH = (detailOpen && detail.length > 0 ? STRIP : 0) + (stale && cookId ? STRIP : 0) + (staleData ? STRIP : 0)
  const bodyH = Math.max(0, baseH - HANDLE - HEADER_ROW - stripH)
  const rowsH = Math.max(SHEET_ROW_HEIGHT, bodyH - headH)
  const visibleCount = Math.ceil(rowsH / SHEET_ROW_HEIGHT)

  const total = data?.total ?? 0
  const columns = useMemo(() => (data ? data.columns.filter(c => !hidden.has(c.name)) : []), [data, hidden])
  const colIndex = useMemo(() => {
    const m = new Map<string, number>()
    data?.columns.forEach((c, i) => m.set(c.name, i))
    return m
  }, [data])
  const readBy = useMemo(() => new Set(data?.readBy ?? []), [data])
  const attrNames = useMemo(() => (data ? data.columns.map(c => c.name) : []), [data])

  // A filter on an attribute this target does not have is dropped (S25):
  // from the columns once they are known, or from the server's 422
  // `attr_unknown` when the first request already carried the filter (DV-2).
  useEffect(() => {
    if (!applied) return
    if (sheet.errorCode === 'attr_unknown') { useSheetUi.getState().setFilter(''); return }
    if (!current || !data) return
    if (!data.columns.some(c => c.name === applied.attr)) useSheetUi.getState().setFilter('')
  }, [current, data, applied, sheet.errorCode])

  // Back to the top when the target or the filter changes.
  const viewKey = `${targetKey(target)}|${ui.filter}`
  const lastViewKey = useRef(viewKey)
  useEffect(() => {
    if (lastViewKey.current === viewKey) return
    lastViewKey.current = viewKey
    setScrollTop(0)
    setFocusRow(null)
    setJump(null)
    if (scrollRef.current) scrollRef.current.scrollTop = 0
  }, [viewKey])

  // ---- writers ---------------------------------------------------------
  // One stable object per writer, so the memoized header cells skip
  // re-rendering on scroll (DV-7).
  const writers = useMemo(() => {
    const m = new Map<string, WriterInfo>()
    const add = (id: string | null) => {
      if (!id || m.has(id)) return
      const n = graph?.nodes[id]
      m.set(id, { id, name: n?.name ?? id, cat: n ? categoryOf(n.type) : null })
    }
    data?.columns.forEach(c => add(c.written_by))
    data?.detail.forEach(d => add(d.written_by))
    return m
  }, [graph, data])
  const writerOf = useCallback((id: string | null): WriterInfo | null => (id ? writers.get(id) ?? null : null), [writers])
  const selectWriter = useCallback((id: string) => {
    const s = useNodeBuilderStore.getState()
    if (s.graph && id in s.graph.nodes) s.select(id)
  }, [])

  // ---- trades ----------------------------------------------------------
  const trades = useMemo(() => (tradeTimes ? sortTimes(tradeTimes) : null), [tradeTimes])
  useEffect(() => { setJump(null) }, [trades])
  useEffect(() => {
    if (flashRow == null) return
    const t = setTimeout(() => setFlashRow(null), FLASH_MS)
    return () => clearTimeout(t)
  }, [flashRow])

  const scrollToRow = useCallback((row: number, center: boolean) => {
    const el = scrollRef.current
    let top: number
    if (center) {
      top = row * SHEET_ROW_HEIGHT - (rowsH - SHEET_ROW_HEIGHT) / 2
    } else {
      const cur = el ? el.scrollTop : scrollTop
      const rowTop = row * SHEET_ROW_HEIGHT
      if (rowTop < cur) top = rowTop
      else if (rowTop + SHEET_ROW_HEIGHT > cur + rowsH) top = rowTop + SHEET_ROW_HEIGHT - rowsH
      else return
    }
    top = Math.max(0, Math.min(top, Math.max(0, total * SHEET_ROW_HEIGHT - rowsH)))
    if (el) el.scrollTop = top
    setScrollTop(top)
  }, [rowsH, scrollTop, total])

  const jumpBy = useCallback(async (dir: 1 | -1) => {
    if (!trades || trades.length === 0) return
    const cur = jump?.trade ?? -1
    const next = cur < 0 ? (dir > 0 ? 0 : trades.length - 1) : Math.max(0, Math.min(trades.length - 1, cur + dir))
    const row = await sheet.jumpTo(trades[next])
    if (row == null) return
    setJump({ trade: next, row })
    setFlashRow(row)
    setFocusRow(row)
    scrollToRow(row, true)
  }, [trades, jump, sheet, scrollToRow])

  // ---- rows: focus, keys, copy -----------------------------------------
  // The previous rows stay on screen (dimmed) while a new key loads (S25,
  // DV-1); clicks and copies only act on rows of the current key.
  const shownRowAt = useCallback((i: number): SheetRow | undefined => data?.rows.get(i), [data])
  const rowAt = useCallback((i: number): SheetRow | undefined => (current ? data?.rows.get(i) : undefined), [current, data])

  const moveFocus = useCallback((to: number) => {
    if (total <= 0) return
    const row = Math.max(0, Math.min(total - 1, to))
    setFocusRow(row)
    scrollToRow(row, false)
  }, [total, scrollToRow])

  const copyRow = useCallback((i: number) => {
    const r = rowAt(i)
    if (!r || !data) return
    const cells = [copyText(r.time), ...columns.map(c => copyText(r.values[colIndex.get(c.name) ?? -1]))]
    try { void navigator.clipboard?.writeText(cells.join('\t')) } catch { /* no clipboard */ }
  }, [rowAt, data, columns, colIndex])

  const onTableKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const page = Math.max(1, visibleCount - 1)
    const at = focusRow ?? firstVisible
    let handled = true
    if (e.key === 'ArrowDown') moveFocus(focusRow == null ? firstVisible : at + 1)
    else if (e.key === 'ArrowUp') moveFocus(focusRow == null ? firstVisible : at - 1)
    else if (e.key === 'PageDown') moveFocus(at + page)
    else if (e.key === 'PageUp') moveFocus(at - page)
    else if (e.key === 'Home') moveFocus(0)
    else if (e.key === 'End') moveFocus(total - 1)
    else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'c' && focusRow != null) copyRow(focusRow)
    else handled = false
    // Keys used here never reach the canvas commands.
    if (handled) { e.preventDefault(); e.stopPropagation() }
  }

  const onRowClick = (i: number) => {
    setFocusRow(i)
    const r = rowAt(i)
    if (!r) return
    useNodeBuilderStore.getState().showFlash(`bar ${formatSheetTime(r.time)}`)
    props.onRowClick?.(r.time, i)
  }

  // ---- header menu -----------------------------------------------------
  const [menu, setMenu] = useState<MenuState | null>(null)
  const openMenu = useCallback((e: ReactMouseEvent, column: InspectColumn) => {
    e.preventDefault()
    setMenu({ x: e.clientX, y: e.clientY, column })
  }, [])
  // Shift+F10 / the ContextMenu key on a focused header (UX-13).
  const openMenuAt = useCallback((x: number, y: number, column: InspectColumn) => {
    setMenu({ x, y, column })
  }, [])
  const menuRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (menu) menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
  }, [menu])
  useEffect(() => {
    if (!menu) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return
      if (e.type === 'pointerdown' && (e.target as Element | null)?.closest?.('.nb-sheet__menu')) return
      setMenu(null)
    }
    document.addEventListener('pointerdown', close, true)
    document.addEventListener('keydown', close, true)
    window.addEventListener('blur', close)
    return () => {
      document.removeEventListener('pointerdown', close, true)
      document.removeEventListener('keydown', close, true)
      window.removeEventListener('blur', close)
    }
  }, [menu])
  const applyFilter = useCallback((f: InspectFilter | null) => {
    useSheetUi.getState().setFilter(filterText(f))
  }, [])

  // ---- filter input ----------------------------------------------------
  const suggestions: FilterSuggestion[] = draftFocused ? filterSuggestions(draft, attrNames) : []
  const commitDraft = (text: string) => {
    const p = parseSheetFilter(text)
    if (p.ok) useSheetUi.getState().setFilter(text.trim())
  }
  const pickSuggestion = (s: FilterSuggestion) => {
    setDraft(s.text)
    setSuggestIdx(-1)
    if (!s.text.endsWith(' ') && parseSheetFilter(s.text).ok) commitDraft(s.text)
  }
  const onFilterKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation()
    if (e.key === 'ArrowDown' && suggestions.length) { e.preventDefault(); setSuggestIdx(i => (i + 1) % suggestions.length) }
    else if (e.key === 'ArrowUp' && suggestions.length) { e.preventDefault(); setSuggestIdx(i => (i <= 0 ? suggestions.length - 1 : i - 1)) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (suggestIdx >= 0 && suggestions[suggestIdx]) pickSuggestion(suggestions[suggestIdx])
      else commitDraft(draft)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setSuggestIdx(-1)
      e.currentTarget.blur()
    }
  }

  // ---- resize ----------------------------------------------------------
  const maxHeight = () => {
    const col = rootRef.current?.parentElement?.clientHeight
    const base = col && col > 0 ? col : (typeof window !== 'undefined' ? window.innerHeight : 800)
    return Math.max(SHEET_MIN_HEIGHT, Math.floor(base * SHEET_MAX_SHARE))
  }
  const onHandleKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null
    if (e.key === 'ArrowUp') next = baseH + HANDLE_STEP
    else if (e.key === 'ArrowDown') next = baseH - HANDLE_STEP
    else if (e.key === 'Home') next = SHEET_MIN_HEIGHT
    else if (e.key === 'End') next = maxHeight()
    else if (e.key === 'Enter') next = defaultSheetHeight()
    if (next == null) return
    e.preventDefault()
    e.stopPropagation()
    useSheetUi.getState().setHeight(Math.max(SHEET_MIN_HEIGHT, Math.min(maxHeight(), next)))
  }
  const onHandleDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    const startY = e.clientY
    const startH = baseH
    const max = maxHeight()
    let last = startH
    const move = (ev: PointerEvent) => {
      last = Math.max(SHEET_MIN_HEIGHT, Math.min(max, startH + (startY - ev.clientY)))
      setDragHeight(last)
    }
    const up = () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
      setDragHeight(null)
      useSheetUi.getState().setHeight(last)
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
  }

  // ---- header pieces ---------------------------------------------------
  const targetNode = target ? graph?.nodes[streamNodeId(target)] : undefined
  const dotColor = !target
    ? 'var(--nb-text-dim)'
    : target.kind === 'wire'
      ? 'var(--nb-wire-selected)'
      : target.kind === 'display'
        ? 'var(--nb-flag-display)'
        : (() => { const c = categoryOf(targetNode?.type); return c ? `var(--nb-cat-${c})` : 'var(--nb-text-dim)' })()
  const chipText = target
    ? `${targetLabel(graph, target)}${targetBypassed(graph, target) ? ' · input stream (bypassed)' : ''}`
    : 'no target'
  const filtered = applied != null
  const countText = data ? formatRowCount(total, current ? sheet.unfilteredTotal : null, filtered) : ''
  const noTrades = !trades || trades.length === 0

  const close = () => {
    if (props.onClose) props.onClose()
    else useSheetUi.getState().setOpen(false)
  }

  // ---- body ------------------------------------------------------------
  let body: ReactNode
  if (!cookId) {
    const readOnly = graph?.readOnly === true
    const msg = noCookMessage({
      readOnly,
      autoCook,
      previewPhase: previewCook.phase,
      blockedByErrors: previewCook.staleNote === FIX_ERRORS_NOTE,
      errorCount: diag.errorCount,
      hasTicker: graph != null && Object.values(graph.nodes).some(n => n.type === 'ticker'),
    })
    body = (
      <div className="nb-sheet__msg" data-testid="nb-sheet-nocook">
        <span>{msg}</span>
        <span className="nb-sheet__msg-actions">
          {readOnly ? (
            props.onEditGraph && (
              <button type="button" className="nb-sheet__textbtn" onClick={props.onEditGraph}>Edit this graph</button>
            )
          ) : (
            <>
              {props.onRunBacktest && (
                <button type="button" className="nb-sheet__textbtn" onClick={props.onRunBacktest}>Run backtest</button>
              )}
              {!autoCook && props.onAutoCookOn && (
                <button type="button" className="nb-sheet__textbtn" onClick={props.onAutoCookOn}>Auto cook on</button>
              )}
            </>
          )}
        </span>
      </div>
    )
  } else if (!target) {
    body = <div className="nb-sheet__msg" data-testid="nb-sheet-notarget">Select a node or wire, or set a display flag (D).</div>
  } else if (sheet.error) {
    body = (
      <div className="nb-sheet__msg nb-sheet__msg--error" data-testid="nb-sheet-error">
        <span>Could not load data: {sheet.error}</span>
        <span className="nb-sheet__msg-actions">
          <button type="button" className="nb-sheet__textbtn" onClick={sheet.retry}>Retry</button>
          {filtered && (
            <button type="button" className="nb-sheet__textbtn" onClick={() => applyFilter(null)}>Clear filter</button>
          )}
        </span>
      </div>
    )
  } else if (!data) {
    body = <div className="nb-sheet__msg" data-testid="nb-sheet-loading">loading rows…</div>
  } else if (current && total === 0) {
    body = filtered ? (
      <div className="nb-sheet__msg" data-testid="nb-sheet-empty">
        <span>No rows match the filter.</span>
        <button type="button" className="nb-sheet__textbtn" onClick={() => applyFilter(null)}>Clear filter</button>
      </div>
    ) : (
      <div className="nb-sheet__msg" data-testid="nb-sheet-empty">No rows.</div>
    )
  } else {
    const start = Math.max(0, firstVisible - SHEET_OVERSCAN)
    const end = Math.min(total, firstVisible + visibleCount + SHEET_OVERSCAN)
    const widths = columns.map(colWidth)
    const fullWidth = TIME_COL + widths.reduce((a, b) => a + b, 0)
    const rows: ReactNode[] = []
    for (let i = start; i < end; i++) {
      const r = shownRowAt(i)
      const cls = ['nb-sheet__tr']
      if (i % 2 === 1) cls.push('nb-sheet__tr--alt')
      if (focusRow === i) cls.push('nb-sheet__tr--focus')
      if (flashRow === i) cls.push('nb-sheet__tr--flash')
      if (jump && jump.row === i) cls.push('nb-sheet__tr--trade')
      rows.push(
        <div
          key={i}
          id={`nb-sheet-row-${i}`}
          role="row"
          aria-rowindex={i + 2}
          aria-selected={focusRow === i}
          data-testid={`nb-sheet-row-${i}`}
          data-time={r ? String(r.time) : undefined}
          className={cls.join(' ')}
          style={{ top: headH + i * SHEET_ROW_HEIGHT, width: fullWidth }}
          onClick={() => onRowClick(i)}
        >
          <div role="gridcell" className="nb-sheet__td nb-sheet__td--time" style={{ width: TIME_COL, minWidth: TIME_COL }}>
            {r ? formatSheetTime(r.time) : <span className="nb-sheet__dim">…</span>}
          </div>
          {columns.map((c, ci) => {
            const w = widths[ci]
            if (!r) return <div key={c.name} role="gridcell" className="nb-sheet__td" style={{ width: w, minWidth: w }}><span className="nb-sheet__dim">…</span></div>
            const v = r.values[colIndex.get(c.name) ?? -1]
            if (c.dtype === 'bool' || typeof v === 'boolean') {
              const on = v === true
              return (
                <div key={c.name} role="gridcell" className={`nb-sheet__td nb-sheet__td--bool${on ? ' nb-sheet__td--true' : ''}`} style={{ width: w, minWidth: w }}>
                  {v == null ? <span className="nb-sheet__dim">nan</span> : on ? 'true' : <span className="nb-sheet__dim">·</span>}
                </div>
              )
            }
            const text = formatNumber(v as number | null)
            return (
              <div key={c.name} role="gridcell" className="nb-sheet__td" style={{ width: w, minWidth: w }}>
                {text === 'nan' ? <span className="nb-sheet__dim">nan</span> : text}
              </div>
            )
          })}
        </div>,
      )
    }
    body = (
      <div
        ref={scrollRef}
        className={`nb-sheet__scroll${!current ? ' nb-sheet__scroll--dim' : ''}`}
        style={{ height: bodyH }}
        role="grid"
        aria-rowcount={total + 1}
        aria-colcount={columns.length + 1}
        aria-label="Data sheet rows"
        aria-activedescendant={focusRow != null && focusRow >= start && focusRow < end ? `nb-sheet-row-${focusRow}` : undefined}
        tabIndex={0}
        data-testid="nb-sheet-grid"
        onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
        onWheel={e => e.stopPropagation()}
        onKeyDown={onTableKey}
      >
        <div className="nb-sheet__inner" style={{ height: headH + total * SHEET_ROW_HEIGHT, width: fullWidth }}>
          <div role="row" aria-rowindex={1} className="nb-sheet__thead" style={{ height: headH, width: fullWidth }}>
            <div role="columnheader" aria-colindex={1} className="nb-sheet__th nb-sheet__th--time" style={{ width: TIME_COL, minWidth: TIME_COL }}>
              <div className="nb-sheet__th-line"><span className="nb-sheet__th-name">@time</span></div>
            </div>
            {columns.map((c, ci) => (
              <HeaderCell
                key={c.name}
                column={c}
                index={ci}
                width={widths[ci]}
                writer={writerOf(c.written_by)}
                stats={data.stats[c.name]}
                total={sheet.unfilteredTotal ?? total}
                showHist={showHist}
                read={readBy.has(c.name)}
                onSelectWriter={selectWriter}
                onMenu={openMenu}
                onMenuKey={openMenuAt}
              />
            ))}
          </div>
          {rows}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={rootRef}
      className="nb-sheet"
      role="region"
      aria-label="Data sheet"
      data-testid="nb-sheet"
      style={{ height, flex: `0 0 ${height}px` } as CSSProperties}
    >
      <div
        className={`nb-sheet__handle${dragHeight != null ? ' nb-sheet__handle--active' : ''}`}
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize data sheet"
        aria-valuenow={height}
        aria-valuemin={SHEET_MIN_HEIGHT}
        aria-valuemax={columnH > 0 ? Math.max(SHEET_MIN_HEIGHT, Math.floor(columnH * SHEET_MAX_SHARE)) : undefined}
        tabIndex={0}
        data-testid="nb-split-handle-sheet"
        title="Drag to resize (or focus and use ↑ ↓). Double-click to reset."
        onKeyDown={onHandleKey}
        onPointerDown={onHandleDown}
        onDoubleClick={() => useSheetUi.getState().setHeight(defaultSheetHeight())}
      />
      <div className="nb-sheet__header">
        <span
          className="nb-sheet__chip"
          data-testid="nb-sheet-target"
          title="Follows the selected wire, then the selected node, then the display node."
        >
          <span className="nb-sheet__dot" style={{ background: dotColor }} />
          {chipText}
        </span>
        <select
          className="nb-sheet__follow"
          data-testid="nb-sheet-follow"
          aria-label="Follow mode"
          value={ui.follow === 'pinned' && pinnedLive ? 'pinned' : 'follow'}
          onChange={e => useSheetUi.getState().setFollow(e.target.value === 'pinned' ? 'pinned' : 'follow', target)}
        >
          <option value="follow">follow selection</option>
          <option value="pinned" disabled={!target}>pinned</option>
        </select>
        <span className="nb-sheet__filter-wrap">
          <input
            type="text"
            className="nb-sheet__filter"
            data-testid="nb-sheet-filter"
            placeholder="only rows where …"
            spellCheck={false}
            autoComplete="off"
            value={draft}
            aria-invalid={draftValid ? undefined : true}
            aria-describedby="nb-sheet-filter-help"
            title={draftValid ? undefined : FILTER_HELP}
            onChange={e => { setDraft(e.target.value); setSuggestIdx(-1) }}
            onFocus={() => setDraftFocused(true)}
            onBlur={() => { setDraftFocused(false); setSuggestIdx(-1) }}
            onKeyDown={onFilterKey}
          />
          <span id="nb-sheet-filter-help" className="nb-sheet__sr">{FILTER_HELP}</span>
          {suggestions.length > 0 && (
            <div className="nb-sheet__suggest" role="listbox" data-testid="nb-sheet-suggest">
              {suggestions.map((s, i) => (
                <div
                  key={s.text + i}
                  role="option"
                  aria-selected={i === suggestIdx}
                  className={`nb-sheet__suggest-row${i === suggestIdx ? ' nb-sheet__suggest-row--on' : ''}`}
                  onPointerDown={e => { e.preventDefault(); pickSuggestion(s) }}
                >
                  {s.label}
                </div>
              ))}
            </div>
          )}
        </span>
        <span className="nb-sheet__count" data-testid="nb-sheet-count" aria-live="polite">
          {countText}
          {sheet.loading && <span className="nb-sheet__spinner" aria-label="loading" />}
        </span>
        <span className="nb-sheet__jump">
          <span>jump to trades</span>
          <button
            type="button"
            className="nb-sheet__icon"
            data-testid="nb-sheet-jump-prev"
            aria-label="Previous trade entry"
            title={noTrades ? 'Run the backtest to get trades' : 'Previous trade entry'}
            disabled={noTrades || !current}
            onClick={() => { void jumpBy(-1) }}
          >◂</button>
          {jump && trades && <span className="nb-sheet__jump-count">{jump.trade + 1} / {trades.length}</span>}
          <button
            type="button"
            className="nb-sheet__icon"
            data-testid="nb-sheet-jump-next"
            aria-label="Next trade entry"
            title={noTrades ? 'Run the backtest to get trades' : 'Next trade entry'}
            disabled={noTrades || !current}
            onClick={() => { void jumpBy(1) }}
          >▸</button>
        </span>
        {detail.length > 0 && (
          <button
            type="button"
            className="nb-sheet__textbtn"
            data-testid="nb-sheet-detail-toggle"
            aria-pressed={detailOpen}
            onClick={() => setDetailOpen(o => !o)}
          >
            detail {detail.length}
          </button>
        )}
        {hidden.size > 0 && (
          <button type="button" className="nb-sheet__textbtn" data-testid="nb-sheet-hidden" onClick={() => setHidden(new Set())}>
            +{hidden.size} hidden
          </button>
        )}
        <span className="nb-sheet__spacer" />
        <button type="button" className="nb-sheet__icon" aria-label="Close data sheet" title="Close (S)" onClick={close}>✕</button>
      </div>
      {detailOpen && detail.length > 0 && (
        <div className="nb-sheet__detail" data-testid="nb-sheet-detail">
          {detail.map(d => {
            const w = writerOf(d.written_by)
            return (
              <span
                key={d.name}
                className="nb-sheet__detail-chip"
                style={catVars(w?.cat ?? null)}
                title={w ? `written by ${w.name}` : undefined}
              >
                {d.name} = {detailValue(d.value)}
              </span>
            )
          })}
        </div>
      )}
      {stale && cookId && (
        <div className="nb-sheet__stale" data-testid="nb-sheet-stale">
          <span>stale — graph changed ·</span>
          <button type="button" className="nb-sheet__textbtn nb-sheet__textbtn--warn" onClick={props.onCook} disabled={!props.onCook}>
            Cook (⌘↵)
          </button>
        </div>
      )}
      {staleData && (
        <div className="nb-sheet__stale" role="status" data-testid="nb-sheet-stale-data">
          <span>{SHEET_STALE_DATA_TEXT}</span>
        </div>
      )}
      {body}
      {menu && (
        <div
          ref={menuRef}
          className="nb-sheet__menu"
          role="menu"
          data-testid="nb-sheet-menu"
          style={{ left: menu.x, top: menu.y }}
        >
          <button type="button" role="menuitem" onClick={() => {
            try { void navigator.clipboard?.writeText(menu.column.name) } catch { /* no clipboard */ }
            setMenu(null)
          }}>Copy column name</button>
          {menu.column.dtype === 'bool' ? (
            <>
              <button type="button" role="menuitem" onClick={() => { applyFilter({ attr: menu.column.name, op: 'is_true', value: null }); setMenu(null) }}>Filter: only true</button>
              <button type="button" role="menuitem" onClick={() => { applyFilter({ attr: menu.column.name, op: 'is_false', value: null }); setMenu(null) }}>Filter: only false</button>
            </>
          ) : (
            <button type="button" role="menuitem" onClick={() => { applyFilter({ attr: menu.column.name, op: 'not_nan', value: null }); setMenu(null) }}>Filter: is set</button>
          )}
          <button type="button" role="menuitem" onClick={() => {
            const name = menu.column.name
            setHidden(h => new Set([...h, name]))
            setMenu(null)
          }}>Hide column</button>
        </div>
      )}
    </div>
  )
}

export default DataSheet
