/**
 * GraphBrowser — the Graphs dialog (surface S02).
 *
 * The one place saved graphs are listed, opened, duplicated, renamed,
 * exported and deleted. The list comes from the server (`listGraphs`),
 * never from localStorage. Search and sort are local to the list.
 *
 * Opening, duplicating, renaming, creating and importing go through the
 * callbacks, because they change the editor (dirty checks, the store).
 * Delete and export are self-contained and happen here.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import {
  deleteGraph,
  getGraph,
  graphErrorDetail,
  isRevConflict,
  listGraphs,
  type GraphListItem,
} from '../../api/graphs'
import { Dialog, PRIMARY_ATTR } from './ui/Dialog'
import { Button } from './ui/Button'
import { Popover } from './ui/Popover'
import { onMenuKeyDown } from './ui/menuKeys'
import { DeleteGraphDialog, RelativeTimeText } from './GraphDialogs'
import { BROWSER_SORT_KEY, downloadJson, exportFileName, storageGet, storageSet } from './persistence'
import { filterGraphs, sortGraphs, type BrowserSort } from './graphText'

export interface GraphBrowserProps {
  /** The graph open in the editor now (gets the `open` pill). */
  openGraphId: string | null
  /** Bump to make the list refetch (after a rename done elsewhere). */
  refreshKey?: number
  onClose: () => void
  /** Open a graph. Reject to keep the dialog open and show the error. */
  onOpen: (item: GraphListItem) => Promise<void>
  onDuplicate: (item: GraphListItem) => Promise<void>
  onRename: (item: GraphListItem) => void
  onNew: () => void
  onImport: () => void
  /** A graph was deleted on the server. */
  onDeleted: (item: GraphListItem) => void
}

function readSort(): BrowserSort {
  const v = storageGet(BROWSER_SORT_KEY)
  return v === 'name' || v === 'nodes' ? v : 'updated'
}

/** The name with the matched part underlined. */
function highlight(name: string, query: string): ReactNode {
  const q = query.trim().toLowerCase()
  const i = q ? name.toLowerCase().indexOf(q) : -1
  if (i < 0) return name
  return (
    <>
      {name.slice(0, i)}
      <mark className="nb-browser__match">{name.slice(i, i + q.length)}</mark>
      {name.slice(i + q.length)}
    </>
  )
}

function Updated({ iso }: { iso: string }) {
  return <RelativeTimeText iso={iso} className="nb-browser__updated" />
}

type Load = { state: 'loading' } | { state: 'ready' } | { state: 'error'; detail: string }

export default function GraphBrowser({
  openGraphId,
  refreshKey = 0,
  onClose,
  onOpen,
  onDuplicate,
  onRename,
  onNew,
  onImport,
  onDeleted,
}: GraphBrowserProps) {
  const [items, setItems] = useState<GraphListItem[]>([])
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<BrowserSort>(readSort)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<GraphListItem | null>(null)
  const [menuFor, setMenuFor] = useState<{ item: GraphListItem; anchor: HTMLElement } | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  // Only the newest list request may land (a rename's refresh and a delete's
  // refetch can overlap).
  const listReq = useRef(0)
  const refetch = useCallback(async () => {
    const req = ++listReq.current
    // The loading row shows only while there is nothing to show yet. Later
    // refreshes keep the rows (and the listbox focus) and swap them in place.
    setLoad(l => (l.state === 'ready' ? l : { state: 'loading' }))
    try {
      const list = await listGraphs()
      if (req !== listReq.current) return
      setItems(list)
      setLoad({ state: 'ready' })
    } catch (e) {
      if (req !== listReq.current) return
      setLoad({ state: 'error', detail: graphErrorDetail(e) })
    }
  }, [])

  useEffect(() => {
    void refetch()
  }, [refetch, refreshKey])

  const visible = useMemo(() => filterGraphs(sortGraphs(items, sort), query), [items, sort, query])
  const selected = visible.find(g => g.id === selectedId) ?? null

  const changeSort = (s: BrowserSort) => {
    setSort(s)
    storageSet(BROWSER_SORT_KEY, s)
  }

  const moveSelection = (step: 1 | -1) => {
    if (visible.length === 0) return
    const i = visible.findIndex(g => g.id === selectedId)
    const next = i < 0 ? (step === 1 ? 0 : visible.length - 1) : (i + step + visible.length) % visible.length
    setSelectedId(visible[next].id)
  }

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setBanner(null)
    try {
      await fn()
    } catch (e) {
      setBanner(graphErrorDetail(e))
    } finally {
      setBusy(false)
    }
  }

  const openItem = (item: GraphListItem | null) => {
    if (!item || busy) return
    if (item.id === openGraphId) {
      onClose()
      return
    }
    void run(() => onOpen(item))
  }

  const exportItem = (item: GraphListItem) =>
    run(async () => {
      const env = await getGraph(item.id)
      downloadJson(exportFileName(env.name), env)
    })

  const doDelete = async (item: GraphListItem) => {
    setConfirmDelete(null)
    setDeletingId(item.id)
    setBanner(null)
    try {
      await deleteGraph(item.id, item.rev)
      setItems(list => list.filter(g => g.id !== item.id))
      if (selectedId === item.id) setSelectedId(null)
      onDeleted(item)
      void refetch()
    } catch (e) {
      setBanner(
        isRevConflict(e)
          ? `Could not delete "${item.name}": it changed on the server.`
          : `Could not delete "${item.name}": ${graphErrorDetail(e)}`,
      )
    } finally {
      setDeletingId(null)
    }
  }

  const onListKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const fromRowButton = e.target !== e.currentTarget
    // A row's Open / Duplicate / ⋯ button keeps its own Enter and Space, and
    // typing there does not jump to the search field.
    if (fromRowButton && (e.key === 'Enter' || e.key.length === 1)) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      moveSelection(e.key === 'ArrowDown' ? 1 : -1)
      // The button had focus but belongs to the old row now (tabIndex -1).
      if (fromRowButton) listRef.current?.focus()
    } else if (e.key === 'Enter') {
      e.preventDefault()
      openItem(selected)
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && selected) {
      e.preventDefault()
      setConfirmDelete(selected)
    } else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
      // Typing in the list goes to the search field, keystroke kept.
      e.preventDefault()
      setQuery(q => q + e.key)
      searchRef.current?.focus()
    }
  }

  const onSearchKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      moveSelection(e.key === 'ArrowDown' ? 1 : -1)
    }
  }

  const loading = load.state === 'loading'
  const empty = load.state === 'ready' && items.length === 0
  const activeRowId = selected ? `${listId}-${selected.id}` : undefined

  const headerExtra = (
    <div className="nb-browser__header-tools">
      <input
        ref={searchRef}
        className="nb-input nb-input--mono nb-browser__search"
        placeholder="Search graphs…"
        aria-label="Search graphs"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={onSearchKeyDown}
        // Arrow keys here move the list's selection; say so to screen readers.
        aria-controls={load.state === 'ready' && visible.length > 0 ? listId : undefined}
        aria-activedescendant={activeRowId}
        data-testid="nb-browser-search"
      />
      <select
        className="nb-input nb-browser__sort"
        aria-label="Sort by"
        value={sort}
        onChange={e => changeSort(e.target.value as BrowserSort)}
      >
        <option value="updated">Updated</option>
        <option value="name">Name</option>
        <option value="nodes">Nodes</option>
      </select>
    </div>
  )

  const footer = (
    <>
      <Button onClick={onImport} disabled={loading || busy}>Import JSON…</Button>
      {empty ? (
        <Button kind="primary" onClick={onNew} disabled={busy} data-testid="nb-browser-new" {...{ [PRIMARY_ATTR]: '' }}>
          New graph
        </Button>
      ) : (
        <>
          <Button onClick={onNew} disabled={loading || busy} data-testid="nb-browser-new">New graph</Button>
          <Button
            kind="primary"
            onClick={() => openItem(selected)}
            disabled={!selected || loading || busy}
            disabledReason="Select a graph"
            data-testid="nb-browser-open"
            {...{ [PRIMARY_ATTR]: '' }}
          >
            Open
          </Button>
        </>
      )}
    </>
  )

  return (
    <Dialog
      title="Graphs"
      width={760}
      height="min(560px, 80vh)"
      onCancel={onClose}
      headerExtra={headerExtra}
      footerLeft={
        <span className="nb-browser__note">
          Graphs live here and in the bot picker. The rule strategy list does not show them.
        </span>
      }
      footer={footer}
      initialFocusRef={searchRef}
      data-testid="nb-graph-browser"
    >
      {banner && (
        <div className="nb-banner nb-banner--error" role="alert" data-testid="nb-browser-banner">
          <span className="nb-banner__dot" aria-hidden="true" />
          <span className="nb-banner__text">{banner}</span>
          <Button kind="icon" className="nb-banner__dismiss" aria-label="Dismiss" title="Dismiss" onClick={() => setBanner(null)}>
            ✕
          </Button>
        </div>
      )}
      {load.state === 'error' && (
        <div className="nb-banner nb-banner--error" role="alert" data-testid="nb-browser-error">
          <span className="nb-banner__dot" aria-hidden="true" />
          <span className="nb-banner__text">Could not load graphs: {load.detail}</span>
          <span className="nb-banner__actions">
            <Button kind="text" onClick={() => void refetch()}>Retry</Button>
          </span>
        </div>
      )}
      {loading && (
        <div className="nb-dialog-status">
          <span className="nb-spinner" aria-hidden="true" /> Loading graphs…
        </div>
      )}
      {empty && (
        <div className="nb-browser__empty" data-testid="nb-browser-empty">
          <div>No graphs yet.</div>
          <div>Create one with New graph, or import a JSON file.</div>
        </div>
      )}
      {load.state === 'ready' && items.length > 0 && visible.length === 0 && (
        <div className="nb-browser__empty">No graphs match "{query.trim()}".</div>
      )}
      {load.state === 'ready' && visible.length > 0 && (
        <div
          ref={listRef}
          id={listId}
          className="nb-browser__list"
          role="listbox"
          aria-label="Graphs"
          aria-activedescendant={activeRowId}
          tabIndex={0}
          onKeyDown={onListKeyDown}
        >
          {visible.map(item => {
            const isSel = item.id === selected?.id
            const isOpen = item.id === openGraphId
            const deleting = deletingId === item.id
            return (
              <div
                key={item.id}
                id={`${listId}-${item.id}`}
                role="option"
                aria-selected={isSel}
                className={`nb-browser__row${isSel ? ' nb-browser__row--selected' : ''}`}
                data-testid={`nb-browser-row-${item.id}`}
                onClick={() => setSelectedId(item.id)}
                onDoubleClick={() => openItem(item)}
              >
                <span className="nb-browser__name" title={item.description || 'No description'}>
                  {highlight(item.name, query)}
                  {isOpen && (
                    <>
                      <span className="nb-browser__open-pill" aria-hidden="true">open</span>
                      <span className="nb-sr-only"> (currently open)</span>
                    </>
                  )}
                </span>
                <span className="nb-browser__desc">{item.description}</span>
                <span className="nb-browser__count">
                  {item.node_count} node{item.node_count === 1 ? '' : 's'}
                </span>
                <Updated iso={item.updated_at} />
                <span className="nb-browser__actions" onDoubleClick={e => e.stopPropagation()}>
                  {deleting ? (
                    <span className="nb-browser__deleting">Deleting…</span>
                  ) : (
                    <>
                      <Button kind="text" tabIndex={isSel ? 0 : -1} onClick={e => { e.stopPropagation(); openItem(item) }}>
                        Open
                      </Button>
                      <Button
                        kind="text"
                        tabIndex={isSel ? 0 : -1}
                        onClick={e => {
                          e.stopPropagation()
                          void run(() => onDuplicate(item))
                        }}
                      >
                        Duplicate
                      </Button>
                      <Button
                        kind="icon"
                        title="More"
                        aria-label={`More actions for ${item.name}`}
                        tabIndex={isSel ? 0 : -1}
                        data-testid={`nb-browser-more-${item.id}`}
                        onClick={e => {
                          e.stopPropagation()
                          setSelectedId(item.id)
                          const anchor = e.currentTarget
                          setMenuFor(m => (m?.item.id === item.id ? null : { item, anchor }))
                        }}
                      >
                        ⋯
                      </Button>
                    </>
                  )}
                </span>
              </div>
            )
          })}
        </div>
      )}
      {menuFor && (
        <Popover
          anchor={menuFor.anchor}
          onClose={() => setMenuFor(null)}
          role="menu"
          ariaLabel="Graph actions"
          align="end"
          width={200}
          autoFocus
        >
          <div onKeyDown={onMenuKeyDown}>
          <button
            type="button"
            role="menuitem"
            className="nb-menu__item"
            onClick={() => {
              const it = menuFor.item
              // Back to the row's ⋯ first, so the dialog this opens returns
              // focus there, not to this item (which unmounts).
              menuFor.anchor.focus({ preventScroll: true })
              setMenuFor(null)
              onRename(it)
            }}
          >
            Rename…
          </button>
          <button
            type="button"
            role="menuitem"
            className="nb-menu__item"
            onClick={() => {
              const it = menuFor.item
              menuFor.anchor.focus({ preventScroll: true })
              setMenuFor(null)
              void exportItem(it)
            }}
          >
            Export JSON
          </button>
          <div className="nb-menu__sep" role="separator" />
          <button
            type="button"
            role="menuitem"
            className="nb-menu__item nb-menu__item--danger"
            data-testid="nb-browser-delete"
            onClick={() => {
              const it = menuFor.item
              menuFor.anchor.focus({ preventScroll: true })
              setMenuFor(null)
              setConfirmDelete(it)
            }}
          >
            Delete…
          </button>
          </div>
        </Popover>
      )}
      {confirmDelete && (
        <DeleteGraphDialog
          name={confirmDelete.name}
          onCancel={() => setConfirmDelete(null)}
          onDelete={() => void doDelete(confirmDelete)}
        />
      )}
    </Dialog>
  )
}
