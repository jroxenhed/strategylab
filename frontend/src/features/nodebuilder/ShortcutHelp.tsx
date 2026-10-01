/**
 * `?` shortcut overlay (F435 W3 item 3.H, spec S21).
 *
 * Every key in one place, built from the command registry
 * (`listCommands()`, see shortcutList.ts), so it is always true. Commands
 * without keys are left out. Groups come from the command id prefix
 * (`view.frameAll` → VIEW); a static MOUSE group lists the gestures, the
 * only hand-written part.
 *
 * Opened by the `help.shortcuts` command (`?`), the Inspector's "Show all
 * shortcuts" link and the pane menu. Lives in the `overlays` slot
 * (registered by plugins/pointerTracker.ts). Built on the dialog shell for
 * the focus trap, Esc and the return of focus to the canvas.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Dialog } from './ui/Dialog'
import { getActiveCanvas, listCommands } from './commands'
import { setShortcutHelpOpen, useShortcutHelpOpen } from './commands/help'
import { isMacPlatform } from './graphText'
import { shortcutColumns, shortcutGroups } from './shortcutList'
import './statusChrome.css'

function ShortcutPanel({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const bodyRef = useRef<HTMLDivElement>(null)
  const filterRef = useRef<HTMLInputElement>(null)
  const [returnTo] = useState(() => getActiveCanvas()?.container() ?? null)
  const mac = isMacPlatform()
  const groups = useMemo(() => shortcutGroups(query, listCommands(), mac), [query, mac])
  const columns = useMemo(() => shortcutColumns(groups), [groups])

  // A press on the backdrop (outside the panel) closes the overlay.
  const closeRef = useRef(onClose)
  useLayoutEffect(() => { closeRef.current = onClose })
  useEffect(() => {
    const backdrop = bodyRef.current?.closest('.nb-dialog-backdrop')
    if (!backdrop) return
    const onDown = (e: Event) => { if (e.target === backdrop) closeRef.current() }
    backdrop.addEventListener('pointerdown', onDown)
    return () => backdrop.removeEventListener('pointerdown', onDown)
  }, [])

  const filter = (
    <input
      ref={filterRef}
      className="nb-input nb-input--mono nb-shortcuts__filter"
      placeholder="Filter…"
      aria-label="Filter shortcuts"
      data-testid="nb-shortcuts-filter"
      value={query}
      onChange={e => setQuery(e.target.value)}
      onKeyDown={e => {
        // `?` closes it again, unless it is part of a filter being typed.
        if (e.key === '?' && query === '') {
          e.preventDefault()
          onClose()
        }
      }}
    />
  )

  return (
    <Dialog
      title="Keyboard shortcuts"
      onCancel={onClose}
      width={720}
      headerExtra={filter}
      footer={null}
      initialFocusRef={filterRef}
      returnFocusTo={returnTo}
      data-testid="nb-shortcuts"
    >
      <div ref={bodyRef}>
        {groups.length === 0 ? (
          <div className="nb-shortcuts__empty">No shortcuts match "{query}"</div>
        ) : (
          <div className="nb-shortcuts__body">
            {columns.map((col, i) => (
              <div key={i} className="nb-shortcuts__col">
                {col.map(g => (
                  <section key={g.key} className="nb-shortcuts__group" aria-label={g.title}>
                    <h3>{g.title}</h3>
                    {g.rows.map(r => (
                      <div
                        key={r.id}
                        className={`nb-shortcuts__row${r.enabled ? '' : ' nb-shortcuts__row--off'}`}
                        data-testid={`nb-shortcuts-row-${r.id}`}
                      >
                        <span className="nb-shortcuts__label">{r.label}</span>
                        <span className="nb-shortcuts__keys">
                          {r.caps.map((c, j) => (
                            <span key={c}>
                              {j > 0 && ' / '}
                              <kbd>{c}</kbd>
                            </span>
                          ))}
                        </span>
                      </div>
                    ))}
                  </section>
                ))}
              </div>
            ))}
          </div>
        )}
        <div className="nb-shortcuts__foot">
          ⌘ is Ctrl on Windows and Linux · press ? or Esc to close
        </div>
      </div>
    </Dialog>
  )
}

/** The overlay, mounted while open. */
export default function ShortcutHelp() {
  const open = useShortcutHelpOpen()
  if (!open) return null
  return <ShortcutPanel onClose={() => setShortcutHelpOpen(false)} />
}
