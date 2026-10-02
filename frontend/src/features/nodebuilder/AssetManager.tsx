/**
 * The Asset Manager (spec S42): every saved asset, its versions, where it
 * is used, and Insert / Export / Delete.
 *
 * - The list comes from `GET /api/graph_library` (each item already has its
 *   versions, palette entry, interface and users). The full file is fetched
 *   only for the selected version (cached by `name@version`).
 * - Assets are immutable per version: the manager never edits content.
 * - `Insert into graph` places a locked instance of the selected version in
 *   the network on screen (at the cursor when opened from the canvas).
 * - Delete states its consequence: graphs that use the version show
 *   "asset missing"; bots are not affected (they keep a baked copy).
 *
 * Opened by `Cmd+Shift+A`, the toolbar overflow, the Tab menu's "Manage
 * assets…" link and the toast after a save (assetUi.openAssetManager).
 * Mounted in the `dialogs` slot by plugins/assets.ts.
 */

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import {
  assetErrorText,
  deleteAssetVersion,
  getAsset,
  type AssetFile,
  type AssetListItem,
} from '../../api/graphLibrary'
import {
  closeAssetManager,
  insertAssetInstance,
  insertProblem,
  refreshLibrary,
  useAssetLibrary,
  useAssetUi,
} from './assetUi'
import {
  deleteConfirmText,
  filterAssets,
  MANAGER_TEXT,
  managerSubline,
  olderVersionText,
  sortAssets,
  userGraphNames,
  type ManagerSort,
} from './assetText'
import { requestOpenGraph } from './graphLinks'
import { InterfaceChips, PromotedTable } from './SaveAssetDialog'
import { useNodeBuilderStore } from './store'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { ActionMenu } from './ui/ActionMenu'
import './assets.css'

export function AssetManagerHost() {
  const req = useAssetUi(s => s.manager)
  if (!req) return null
  return <AssetManager insertAt={req.insertAt} initial={req.select ?? null} />
}

interface PendingDelete {
  asset: AssetListItem
  version: number | 'all'
}

function AssetManager({ insertAt, initial }: { insertAt: { x: number; y: number } | null; initial: string | null }) {
  const items = useAssetLibrary(s => s.items)
  const status = useAssetLibrary(s => s.status)
  const libError = useAssetLibrary(s => s.error)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<ManagerSort>('name')
  const [selected, setSelected] = useState<string | null>(initial)
  const [version, setVersion] = useState<number | null>(null)
  // The fetched file (or error) for one `name@version`; anything else shows nothing.
  const [loaded, setLoaded] = useState<{ key: string; file: AssetFile | null; error: string | null } | null>(null)
  // The ⋯ button while its menu is open (UX-06: a real menu, keyboard and dismiss).
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const listId = useId()
  const listRef = useRef<HTMLDivElement>(null)
  // S42: the delete confirm opens on Cancel, so Delete then Enter deletes nothing (UX-01).
  const deleteCancelRef = useRef<HTMLButtonElement>(null)
  // Re-render on graph edits so "inside a locked asset" stays right.
  const blocked = useNodeBuilderStore(s => insertProblem(s))

  // The list is fetched on every open, so it is never older than the dialog.
  useEffect(() => { void refreshLibrary() }, [])

  const shown = useMemo(() => sortAssets(filterAssets(items, query), sort), [items, query, sort])
  const asset = shown.find(a => a.name === selected) ?? shown[0] ?? null
  const activeVersion = asset ? (version !== null && asset.versions.includes(version) ? version : asset.latest) : null
  const attrQuery = query.trim().startsWith('@') ? query.trim().toLowerCase() : null

  // Fetch the selected version's file once (the API caches it).
  const fileKey = asset && activeVersion !== null ? `${asset.name}@${activeVersion}` : null
  useEffect(() => {
    if (!asset || activeVersion === null || !fileKey) return
    let live = true
    getAsset(asset.name, activeVersion)
      .then(f => { if (live) setLoaded({ key: fileKey, file: f, error: null }) })
      .catch(e => { if (live) setLoaded({ key: fileKey, file: null, error: assetErrorText(e) }) })
    return () => { live = false }
  }, [asset, activeVersion, fileKey])
  const file = loaded && loaded.key === fileKey ? loaded.file : null
  const fileError = loaded && loaded.key === fileKey ? loaded.error : null

  const select = (name: string) => {
    setSelected(name)
    setVersion(null)
    setMenuAnchor(null)
  }

  // Insert waits for the version's file (FE-06): the instance copies its
  // promoted params, and nothing fills them in later.
  const notReady: string | null = !file || file.version !== activeVersion ? (fileError ?? MANAGER_TEXT.loading) : null
  const insert = () => {
    if (!asset || activeVersion === null || blocked || notReady || !file) return
    closeAssetManager()
    insertAssetInstance({ name: asset.name, version: activeVersion, promoted: file.promoted }, insertAt)
  }

  const onListKey = (e: ReactKeyboardEvent) => {
    if (shown.length === 0) return
    const i = Math.max(0, shown.findIndex(a => a.name === asset?.name))
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const j = e.key === 'ArrowDown' ? Math.min(i + 1, shown.length - 1) : Math.max(i - 1, 0)
      select(shown[j].name)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      insert()
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      if (asset && activeVersion !== null) setPendingDelete({ asset, version: activeVersion })
    }
  }

  const exportJson = () => {
    setMenuAnchor(null)
    if (!file) return
    try {
      const blob = new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${file.name}_v${file.version}.json`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch {
      // No download support (tests): nothing to do.
    }
  }

  const confirmDelete = async () => {
    const p = pendingDelete
    if (!p) return
    setDeleteError(null)
    try {
      const versions = p.version === 'all' ? p.asset.versions : [p.version]
      for (const v of versions) await deleteAssetVersion(p.asset.name, v)
      setPendingDelete(null)
      setVersion(null)
      await refreshLibrary()
    } catch (e) {
      setDeleteError(assetErrorText(e))
    }
  }

  const activeRowId = asset ? `${listId}-${asset.name}` : undefined

  return (
    <Dialog
      title={MANAGER_TEXT.title}
      onCancel={closeAssetManager}
      width={840}
      height={520}
      cancelLabel="Close"
      data-testid="nb-asset-manager"
    >
      <div className="nb-am">
        <div className="nb-am__left">
          <div className="nb-am__search">
            <input
              className="nb-asset-input"
              placeholder={MANAGER_TEXT.search}
              aria-label={MANAGER_TEXT.search}
              value={query}
              spellCheck={false}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') onListKey(e)
              }}
            />
            <select
              className="nb-asset-input"
              aria-label="Sort"
              value={sort}
              onChange={e => setSort(e.target.value as ManagerSort)}
            >
              <option value="name">Name</option>
              <option value="used">Most used</option>
            </select>
          </div>
          {status === 'error' && (
            <div className="nb-asset-bar nb-asset-bar--error" role="alert" style={{ margin: '0 8px 8px' }}>
              <span>{libError}</span>
              <button type="button" className="nb-promoted-link" onClick={() => { void refreshLibrary() }}>Retry</button>
            </div>
          )}
          <div
            ref={listRef}
            className="nb-am__list"
            role="listbox"
            aria-label="Assets"
            tabIndex={0}
            aria-activedescendant={activeRowId}
            onKeyDown={onListKey}
            data-testid="nb-am-list"
          >
            {status === 'loading' && items.length === 0 && Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="nb-am__skeleton" aria-hidden="true" />
            ))}
            {status === 'ok' && items.length === 0 && (
              <div className="nb-am__empty">
                <div>{MANAGER_TEXT.empty}</div>
                <div style={{ marginTop: 6 }}>{MANAGER_TEXT.emptyHelp}</div>
              </div>
            )}
            {shown.map(a => {
              const active = a.name === asset?.name
              const rules = !!a.palette
              return (
                <div
                  key={a.name}
                  id={`${listId}-${a.name}`}
                  role="option"
                  aria-selected={active}
                  data-testid={`nb-am-row-${a.name}`}
                  className={`nb-am__row${active ? ' nb-am__row--active' : ''}${rules ? ' nb-am__row--rules' : ''}`}
                  onClick={() => select(a.name)}
                  onDoubleClick={() => { select(a.name); insert() }}
                >
                  <span className={`nb-asset-glyph${rules ? ' nb-asset-glyph--rules' : ''}`}>{a.palette?.glyph ?? 'N'}</span>
                  <span style={{ minWidth: 0 }}>
                    <div className="nb-am__row-name">{a.name}</div>
                    <div className="nb-am__row-sub">{managerSubline(a)}</div>
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        <div className="nb-am__right">
          {asset && activeVersion !== null && (
            <>
              <div className="nb-am__head">
                <span className="nb-am__name">{asset.name}</span>
                {asset.palette && <span className="nb-asset-tag">RULES</span>}
                <span className="nb-am__spacer" />
                <Button
                  kind="primary"
                  onClick={insert}
                  disabled={!!blocked || !!notReady}
                  disabledReason={blocked ?? notReady ?? undefined}
                  title={blocked ? MANAGER_TEXT.insideLocked : undefined}
                  data-testid="nb-am-insert"
                >
                  {MANAGER_TEXT.insert}
                </Button>
                <span className="nb-am__menu">
                  <Button
                    kind="icon"
                    aria-label="More"
                    aria-haspopup="menu"
                    aria-expanded={menuAnchor !== null}
                    data-testid="nb-am-more"
                    onClick={e => { const el = e.currentTarget; setMenuAnchor(a => (a ? null : el)) }}
                  >
                    ⋯
                  </Button>
                  {menuAnchor && (
                    <ActionMenu
                      anchor={menuAnchor}
                      onClose={() => setMenuAnchor(null)}
                      ariaLabel={`More for ${asset.name}`}
                      testId="nb-am-menu"
                      items={[
                        { id: 'export', label: 'Export JSON', onSelect: exportJson, disabled: !file },
                        { id: 'delete', label: 'Delete version…', danger: true, onSelect: () => setPendingDelete({ asset, version: activeVersion }) },
                        { id: 'delete-all', label: 'Delete all versions…', danger: true, onSelect: () => setPendingDelete({ asset, version: 'all' }) },
                      ]}
                    />
                  )}
                </span>
              </div>

              <div className="nb-am__pills" role="radiogroup" aria-label="Version">
                {[...asset.versions].sort((x, y) => x - y).map(v => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={v === activeVersion}
                    className="nb-am__pill"
                    onClick={() => setVersion(v)}
                    title={file && file.version === v ? new Date(file.created_at).toLocaleString() : undefined}
                  >
                    v{v}
                  </button>
                ))}
              </div>

              {activeVersion !== asset.latest && (
                <div className="nb-asset-line">{olderVersionText(activeVersion, asset.latest)}</div>
              )}
              {fileError && <div className="nb-asset-bar nb-asset-bar--error">{fileError}</div>}

              <div className="nb-am__section">
                <div className="nb-asset-section__title">Description</div>
                <div className="nb-am__desc">{file?.description ?? ''}</div>
              </div>
              <div className="nb-am__section">
                <div className="nb-asset-section__title">Interface</div>
                <InterfaceChips iface={file?.interface ?? asset.interface} match={attrQuery} />
              </div>
              <div className="nb-am__section">
                <div className="nb-asset-section__title">Promoted parameters</div>
                <PromotedTable list={file?.promoted ?? []} />
              </div>
              <div className="nb-am__section">
                <div className="nb-asset-section__title">Used by</div>
                <UsedBy asset={asset} />
                <div className="nb-am__foot">{MANAGER_TEXT.botsFootnote}</div>
              </div>
              <div className="nb-am__section nb-asset-help">{MANAGER_TEXT.noPreview}</div>
            </>
          )}
        </div>
      </div>

      {pendingDelete && (
        <Dialog
          title={pendingDelete.version === 'all' ? `Delete all versions of ${pendingDelete.asset.name}` : `Delete ${pendingDelete.asset.name} v${pendingDelete.version}`}
          onCancel={() => setPendingDelete(null)}
          width={420}
          primaryLabel={pendingDelete.version === 'all' ? 'Delete all' : `Delete v${pendingDelete.version}`}
          danger
          onPrimary={() => { void confirmDelete() }}
          cancelRef={deleteCancelRef}
          initialFocusRef={deleteCancelRef}
          data-testid="nb-am-delete-confirm"
        >
          <p style={{ margin: 0, fontSize: 12 }}>
            {deleteConfirmText(pendingDelete.asset.name, pendingDelete.version, userGraphNames(pendingDelete.asset))}
          </p>
          {deleteError && <div className="nb-asset-bar nb-asset-bar--error" role="alert" style={{ marginTop: 10 }}>{deleteError}</div>}
        </Dialog>
      )}
    </Dialog>
  )
}

/** The graphs that use an asset: name, instance count, versions. */
function UsedBy({ asset }: { asset: AssetListItem }) {
  if (asset.used_by.length === 0) return <div className="nb-asset-help">{MANAGER_TEXT.notUsed}</div>
  const byGraph = new Map<string, { name: string; count: number }>()
  for (const u of asset.used_by) {
    const g = byGraph.get(u.graph_id)
    if (g) g.count += 1
    else byGraph.set(u.graph_id, { name: u.name, count: 1 })
  }
  return (
    <ul className="nb-am__users">
      {[...byGraph].map(([graphId, g]) => (
        <li key={graphId}>
          <a
            href="#"
            onClick={e => {
              e.preventDefault()
              closeAssetManager()
              requestOpenGraph({ graphId, group: null, spawn: false })
            }}
          >
            {g.name}
          </a>
          <span className="nb-asset-help">{` · ${g.count} ${g.count === 1 ? 'instance' : 'instances'}`}</span>
        </li>
      ))}
    </ul>
  )
}
