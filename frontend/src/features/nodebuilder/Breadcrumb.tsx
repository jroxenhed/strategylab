/**
 * Network breadcrumb (W6 item 6.C, spec S37): `/ › long_leg › regime` in
 * the graph toolbar, after the graph name.
 *
 * - One crumb per level, from the root (`/`) to the network on screen. The
 *   current crumb has `aria-current="location"`.
 * - Click a crumb: go to that level (going up selects the network you came
 *   down through). Right-click a network crumb: `Open in place` (dive) and
 *   `Frame in parent` (go to its parent and frame it).
 * - Deeper than 4 levels, the middle crumbs fold into `…`, a button that
 *   lists them.
 * - Left and right arrows move focus between crumbs.
 *
 * Shown while the graph on screen has networks, or while inside one.
 */

import { memo, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { useNodeBuilderStore } from './store'
import { useScreenGraph } from './screen'
import { currentParentId } from './store/view'
import { crumbsOf, frameInParent, goToCrumb, isNetworkNode, diveInto, type Crumb } from './networkNav'
import { NETWORK_TYPES } from './rfMapping'
import { Popover } from './ui/Popover'
import { CARD_GLYPH } from './nodes/subnetFormat'
import './nodes/subnetNode.css'

/** More levels than this and the middle crumbs fold into `…` (S37). */
const MAX_LEVELS = 4

type MenuState = { crumb: Crumb; anchor: { x: number; y: number } } | null

function hasNetworks(nodes: Record<string, { type: string }>): boolean {
  for (const n of Object.values(nodes)) if (NETWORK_TYPES.has(n.type)) return true
  return false
}

function Breadcrumb() {
  const { graph } = useScreenGraph()
  const network = useNodeBuilderStore(s => s.network)
  const currentNetworkId = useNodeBuilderStore(s => s.currentNetworkId)
  const current = useMemo(() => {
    if (!graph) return null
    const id = currentParentId({ network, currentNetworkId, graph }, graph)
    return id && isNetworkNode(graph.nodes[id]) ? id : null
  }, [graph, network, currentNetworkId])
  const crumbs = useMemo(() => crumbsOf(graph, current), [graph, current])
  const [menu, setMenu] = useState<MenuState>(null)
  const [hiddenAnchor, setHiddenAnchor] = useState<HTMLButtonElement | null>(null)
  const listRef = useRef<HTMLOListElement>(null)

  if (!graph || (current === null && !hasNetworks(graph.nodes))) return null

  // Fold the middle crumbs when the path is deep: root, first level, …, last two.
  const levels = crumbs.length - 1
  const folded = levels > MAX_LEVELS
  const hidden = folded ? crumbs.slice(2, crumbs.length - 2) : []
  const shown: Array<Crumb | 'fold'> = folded
    ? [crumbs[0], crumbs[1], 'fold', ...crumbs.slice(crumbs.length - 2)]
    : crumbs

  const go = (c: Crumb) => {
    setMenu(null)
    setHiddenAnchor(null)
    goToCrumb(c.id)
  }

  const onContextMenu = (c: Crumb) => (e: MouseEvent) => {
    e.preventDefault()
    if (c.id === null) return
    setMenu({ crumb: c, anchor: { x: e.clientX, y: e.clientY } })
  }

  // Arrow keys move between the crumb buttons.
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])
    const i = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (i < 0) return
    e.preventDefault()
    e.stopPropagation()
    const next = buttons[e.key === 'ArrowLeft' ? Math.max(0, i - 1) : Math.min(buttons.length - 1, i + 1)]
    next?.focus()
  }

  const crumbButton = (c: Crumb) => {
    const isCurrent = c.id === current
    const glyph = c.type ? CARD_GLYPH[c.type] ?? CARD_GLYPH.subnet : null
    return (
      <button
        type="button"
        className="nb-crumbs__crumb"
        aria-current={isCurrent ? 'location' : undefined}
        title={c.path}
        data-testid={`nb-crumb-${c.id ?? 'root'}`}
        onClick={() => { if (!isCurrent) go(c) }}
        onContextMenu={onContextMenu(c)}
      >
        {glyph && <span className="nb-crumbs__glyph" aria-hidden="true" style={{ background: glyph.color }}>{glyph.glyph}</span>}
        <span>{c.label}</span>
        {c.locked && <span className="nb-crumbs__lock" role="img" aria-label="locked">🔒</span>}
      </button>
    )
  }

  return (
    <nav className="nb-crumbs" aria-label="Network path" data-testid="nb-breadcrumb" onKeyDown={onKeyDown}>
      <ol className="nb-crumbs__list" ref={listRef}>
        {shown.map((c, i) => (
          <li key={c === 'fold' ? 'fold' : c.id ?? 'root'} className="nb-crumbs__item">
            {i > 0 && <span className="nb-crumbs__sep" aria-hidden="true">›</span>}
            {c === 'fold' ? (
              <button
                type="button"
                className="nb-crumbs__crumb"
                aria-label={`${hidden.length} more levels`}
                aria-haspopup="menu"
                aria-expanded={hiddenAnchor != null}
                data-testid="nb-crumb-fold"
                onClick={e => { const el = e.currentTarget; setHiddenAnchor(a => (a ? null : el)) }}
              >
                …
              </button>
            ) : crumbButton(c)}
          </li>
        ))}
      </ol>

      {hiddenAnchor && (
        <Popover anchor={hiddenAnchor} onClose={() => setHiddenAnchor(null)} role="menu" ariaLabel="Hidden levels" autoFocus>
          <div className="nb-crumbs__menu">
            {hidden.map(c => (
              <button key={c.id ?? 'root'} type="button" role="menuitem" className="nb-crumbs__menu-item" title={c.path} onClick={() => go(c)}>
                {c.label}
              </button>
            ))}
          </div>
        </Popover>
      )}

      {menu && (
        <Popover anchor={menu.anchor} onClose={() => setMenu(null)} role="menu" ariaLabel={`Network ${menu.crumb.label}`} autoFocus>
          <div className="nb-crumbs__menu">
            <button
              type="button"
              role="menuitem"
              className="nb-crumbs__menu-item"
              data-testid="nb-crumb-open"
              onClick={() => { const id = menu.crumb.id; setMenu(null); if (id) diveInto(id) }}
            >
              Open in place
            </button>
            <button
              type="button"
              role="menuitem"
              className="nb-crumbs__menu-item"
              data-testid="nb-crumb-frame"
              onClick={() => { const id = menu.crumb.id; setMenu(null); if (id) frameInParent(id) }}
            >
              Frame in parent
            </button>
          </div>
        </Popover>
      )}
    </nav>
  )
}

export default memo(Breadcrumb)
