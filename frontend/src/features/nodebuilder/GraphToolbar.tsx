/**
 * GraphToolbar — the 36px row above the canvas (surface S01).
 *
 * Left: the graph name (click to rename), the unsaved dot, the rev, and a
 * VIEW pill for the read-only strategy view. Right: the diagnostics chip,
 * Run, Save, and the `⋯` menu with every other graph action.
 *
 * It only draws and reports clicks. NodeBuilder (with useGraphSession)
 * owns what each action does. Items from later waves (Reset view, Auto
 * cook, Spawn bots, Inspector and Data Sheet toggles) are not here yet.
 */

import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Button } from './ui/Button'
import { Popover } from './ui/Popover'
import { onMenuKeyDown } from './ui/menuKeys'
import { nameError } from './persistence'
import { diagnosticsLabel, modKeyCap, runDisabledReason } from './graphText'

export type ToolbarMode = 'none' | 'view' | 'edit'

export interface GraphToolbarProps {
  /** none: nothing loaded; view: the read-only strategy view; edit: an editable graph. */
  mode: ToolbarMode
  /** The graph's name, or null for an untitled graph. */
  name: string | null
  /** The saved rev, or null for a graph never saved. */
  rev: number | null
  dirty: boolean
  loading?: boolean
  saving?: boolean
  running?: boolean
  hasNodes: boolean
  errorCount: number
  warningCount: number
  /** No validation result yet while one is on its way: the chip shows `…`. */
  diagnosticsUnknown?: boolean
  onRun: () => void
  onStop: () => void
  onSave: () => void
  onNew: () => void
  onOpen: () => void
  onSaveAs: () => void
  onRename: () => void
  /** Inline rename from the name crumb. Resolve with an error sentence, or null when done. */
  onRenameInline: (name: string) => Promise<string | null>
  onDuplicate: () => void
  onExport: () => void
  onImport: () => void
  onDelete: () => void
  onEditThisGraph?: () => void
  /** Leave the edit copy and go back to the strategy view. */
  onCloseGraph?: () => void
  onDiagnosticsClick: (anchor: HTMLElement) => void
}

interface MenuItem {
  label: string
  keyCap?: string
  onSelect: () => void
  disabled?: boolean
  danger?: boolean
  testId?: string
}

type MenuEntry = MenuItem | 'sep'

function OverflowMenu({ anchor, items, onClose }: { anchor: HTMLElement; items: MenuEntry[]; onClose: () => void }) {
  return (
    <Popover anchor={anchor} onClose={onClose} role="menu" ariaLabel="More" align="end" width={232} autoFocus data-testid="nb-menu-more">
      <div onKeyDown={onMenuKeyDown}>
        {items.map((it, i) =>
          it === 'sep' ? (
            <div key={`sep${i}`} className="nb-menu__sep" role="separator" />
          ) : (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              className={`nb-menu__item${it.danger ? ' nb-menu__item--danger' : ''}`}
              disabled={it.disabled}
              data-testid={it.testId}
              onClick={() => {
                // Focus goes back to ⋯ before the item runs, so a dialog it
                // opens returns focus there (not to this item, which unmounts).
                anchor.focus({ preventScroll: true })
                onClose()
                it.onSelect()
              }}
            >
              <span>{it.label}</span>
              {it.keyCap && <span className="nb-keycap" aria-hidden="true">{it.keyCap}</span>}
            </button>
          ),
        )}
      </div>
    </Popover>
  )
}

function NameCrumb({
  name,
  loading,
  editable,
  onStartUntitled,
  onRename,
}: {
  name: string | null
  loading: boolean
  editable: boolean
  onStartUntitled: () => void
  onRename: (name: string) => Promise<string | null>
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(name ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  // Set by Esc, so the blur that follows reverts instead of committing.
  const revertRef = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const start = () => {
    if (!editable || loading) return
    if (name == null) {
      onStartUntitled()
      return
    }
    setValue(name)
    setError(null)
    revertRef.current = false
    setEditing(true)
  }

  /** Enter (and blur): rename. On blur a name that fails the local check reverts. */
  const commit = async (onBlur = false) => {
    if (busyRef.current) return
    const trimmed = value.trim()
    if (trimmed === name) {
      setEditing(false)
      return
    }
    const local = nameError(value)
    if (local) {
      if (onBlur) setEditing(false)
      else setError(local)
      return
    }
    busyRef.current = true
    setBusy(true)
    try {
      const err = await onRename(trimmed)
      if (err) setError(err)
      else setEditing(false)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <span className="nb-toolbar__rename">
        <input
          ref={inputRef}
          className="nb-input nb-input--mono nb-toolbar__rename-input"
          aria-label="Graph name"
          aria-invalid={error ? 'true' : undefined}
          title={error ?? undefined}
          value={value}
          size={Math.max(8, value.length + 1)}
          disabled={busy}
          data-testid="nb-graph-name-input"
          onChange={e => {
            setValue(e.target.value)
            setError(null)
          }}
          onKeyDown={e => {
            // Cmd+S here commits the name, like Enter (the builder's global
            // Save sees the key as handled and stays out of it).
            const saveChord = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's'
            if (e.key === 'Enter' || saveChord) {
              e.preventDefault()
              void commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              revertRef.current = true
              setEditing(false)
            }
          }}
          onBlur={() => {
            // Leaving the field keeps the typed name, as param rows do (S01).
            if (revertRef.current || busyRef.current) return
            void commit(true)
          }}
        />
        {error && <span className="nb-field-error nb-toolbar__rename-error" role="alert">{error}</span>}
      </span>
    )
  }

  return (
    <button
      type="button"
      className={`nb-toolbar__name${name == null || loading ? ' nb-toolbar__name--dim' : ''}`}
      title={editable ? 'Rename' : undefined}
      disabled={!editable}
      onClick={start}
      data-testid="nb-graph-name"
    >
      {loading && <span className="nb-spinner" aria-hidden="true" />}
      {name ?? 'untitled'}
    </button>
  )
}

export default function GraphToolbar(props: GraphToolbarProps) {
  const {
    mode,
    name,
    rev,
    dirty,
    loading = false,
    saving = false,
    running = false,
    hasNodes,
    errorCount,
    warningCount,
    diagnosticsUnknown = false,
  } = props
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const editMode = mode === 'edit'
  const saved = rev != null
  const runReason = runDisabledReason(errorCount, hasNodes)

  // Arrow keys move between the toolbar's buttons.
  const onToolbarKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const target = e.target as HTMLElement
    if (target.tagName === 'INPUT') return
    const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not([disabled])'))
    const i = buttons.indexOf(target as HTMLButtonElement)
    if (i < 0) return
    e.preventDefault()
    const next = e.key === 'ArrowRight' ? (i + 1) % buttons.length : (i - 1 + buttons.length) % buttons.length
    buttons[next]?.focus()
  }

  const menuItems: MenuEntry[] = editMode
    ? [
        { label: 'New', onSelect: props.onNew, disabled: saving || loading, testId: 'nb-menu-new' },
        { label: 'Open…', keyCap: modKeyCap('O'), onSelect: props.onOpen, disabled: saving || loading, testId: 'nb-menu-open' },
        { label: 'Save as…', onSelect: props.onSaveAs, disabled: loading, testId: 'nb-menu-saveas' },
        { label: 'Rename…', onSelect: props.onRename, disabled: loading, testId: 'nb-menu-rename' },
        { label: 'Duplicate', onSelect: props.onDuplicate, disabled: !saved || loading, testId: 'nb-menu-duplicate' },
        'sep',
        { label: 'Export JSON', onSelect: props.onExport, disabled: loading, testId: 'nb-menu-export' },
        { label: 'Import JSON…', onSelect: props.onImport, disabled: loading, testId: 'nb-menu-import' },
        'sep',
        { label: 'Delete…', onSelect: props.onDelete, disabled: !saved || saving || loading, danger: true, testId: 'nb-menu-delete' },
        ...(props.onCloseGraph
          ? (['sep', { label: 'Close graph', onSelect: props.onCloseGraph, disabled: loading, testId: 'nb-menu-close' }] as MenuEntry[])
          : []),
      ]
    : [
        { label: 'New', onSelect: props.onNew, disabled: loading, testId: 'nb-menu-new' },
        { label: 'Open…', keyCap: modKeyCap('O'), onSelect: props.onOpen, disabled: loading, testId: 'nb-menu-open' },
        { label: 'Import JSON…', onSelect: props.onImport, disabled: loading, testId: 'nb-menu-import' },
      ]

  const showChip = editMode && (errorCount > 0 || warningCount > 0 || diagnosticsUnknown)

  return (
    <div className="nb-toolbar" role="toolbar" aria-label="Graph" data-testid="nb-toolbar" onKeyDown={onToolbarKeyDown}>
      <div className="nb-toolbar__left">
        {mode === 'view' ? (
          <>
            <span className="nb-toolbar__name nb-toolbar__name--static">strategy view</span>
            <span
              className="nb-toolbar__view-pill"
              title="Auto-rendered from a rule strategy. Edit this graph to make a copy."
            >
              VIEW
            </span>
          </>
        ) : mode === 'edit' ? (
          <>
            <NameCrumb
              name={name}
              loading={loading}
              editable={!loading}
              onStartUntitled={props.onSaveAs}
              onRename={props.onRenameInline}
            />
            {dirty && (
              <span
                className="nb-toolbar__dirty"
                title="unsaved changes"
                aria-label="Unsaved changes"
                role="img"
                data-testid="nb-dirty-dot"
              />
            )}
            {saved && (
              <span className="nb-toolbar__rev" data-testid="nb-rev">
                rev {rev}
              </span>
            )}
          </>
        ) : null}
      </div>

      <div className="nb-toolbar__right">
        {showChip && (
          <button
            type="button"
            className="nb-diag-chip"
            aria-label={diagnosticsUnknown && errorCount === 0 && warningCount === 0 ? 'Checking the graph' : diagnosticsLabel(errorCount, warningCount)}
            data-testid="nb-diag-chip"
            onClick={e => props.onDiagnosticsClick(e.currentTarget)}
          >
            {errorCount === 0 && warningCount === 0 ? (
              <span className="nb-diag-chip__pending">…</span>
            ) : (
              <>
                {errorCount > 0 && <span className="nb-diag-chip__err">● {errorCount}</span>}
                {warningCount > 0 && <span className="nb-diag-chip__warn">▲ {warningCount}</span>}
              </>
            )}
          </button>
        )}

        {mode === 'view' && props.onEditThisGraph && (
          <Button kind="primary" onClick={props.onEditThisGraph} title="Edit this graph" data-testid="nb-btn-edit">
            Edit this graph
          </Button>
        )}

        {editMode &&
          (running ? (
            <Button kind="primary" onClick={props.onStop} title="Stop" data-testid="nb-btn-run">
              <span className="nb-spinner" aria-hidden="true" />■ Stop
            </Button>
          ) : (
            <Button
              kind="primary"
              onClick={props.onRun}
              disabled={runReason != null || loading}
              title={runReason ?? `Run backtest (${modKeyCap('↵')})`}
              keyCap={modKeyCap('↵')}
              data-testid="nb-btn-run"
            >
              ▶ Run backtest
            </Button>
          ))}

        {editMode && (
          <Button
            onClick={props.onSave}
            disabled={saving || loading || (saved && !dirty)}
            title={saved && !dirty ? 'No changes to save' : `Save (${modKeyCap('S')})`}
            keyCap={saving ? undefined : modKeyCap('S')}
            data-testid="nb-btn-save"
          >
            {saving ? (
              <>
                <span className="nb-spinner" aria-hidden="true" />
                Saving…
              </>
            ) : (
              'Save'
            )}
          </Button>
        )}

        <Button
          kind="icon"
          title="More"
          aria-label="More"
          aria-haspopup="menu"
          aria-expanded={menuAnchor != null}
          disabled={loading && editMode}
          data-testid="nb-btn-more"
          onClick={e => {
            const el = e.currentTarget
            setMenuAnchor(a => (a ? null : el))
          }}
        >
          ⋯
        </Button>
      </div>

      {menuAnchor && <OverflowMenu anchor={menuAnchor} items={menuItems} onClose={() => setMenuAnchor(null)} />}
    </div>
  )
}
