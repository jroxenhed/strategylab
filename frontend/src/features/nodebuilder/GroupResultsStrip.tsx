/**
 * Per-group result tabs (W5, spec S33): a strip above the Results sub-tabs
 * for a graph with two or more Output Groups. `Combined` first, then one
 * pill per group in file order (name, direction, ticker, return).
 *
 * It only reports the choice (`onSelect`); the owner keeps it in
 * `graphResult.displayedGroup` (never in `lastRequest` or `backtestResult`,
 * D10). Results.tsx then feeds the chosen result to its existing sub-tabs.
 *
 * App tokens (`--gh-*`), since this renders inside Results.tsx.
 */

import { useRef, type CSSProperties, type KeyboardEvent } from 'react'
import type { CombinedResult, GroupResult } from '../../shared/types/strategy'
import {
  COMBINED,
  COMBINED_LABEL,
  OPEN_POSITION_TITLE,
  groupReturnText,
  stripStatusText,
} from './groupResults'

interface Props {
  groups: readonly GroupResult[]
  combined: CombinedResult | null | undefined
  /** The active tab key: `combined` or a group name. */
  active: string
  onSelect: (key: string) => void
  /** Double-click on a group pill: frame that group on the canvas. */
  onFrameGroup?: (group: GroupResult) => void
  /** The graph or window changed after the run. */
  stale?: boolean
}

const TONE: Record<'ok' | 'error' | 'dim', string> = {
  ok: 'var(--gh-green)',
  error: 'var(--gh-red)',
  dim: 'var(--gh-text-dim, var(--gh-text-muted))',
}

const stripStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  height: 32,
  padding: '0 12px',
  borderBottom: '1px solid var(--gh-border)',
  overflowX: 'auto',
  scrollbarWidth: 'none',
  flexShrink: 0,
}

const pillBase: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 24,
  padding: '0 10px',
  borderRadius: 12,
  fontSize: 12,
  fontWeight: 500,
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  background: 'transparent',
  color: 'var(--gh-text-muted)',
  border: '1px solid transparent',
  flexShrink: 0,
}

const pillActive: CSSProperties = {
  background: 'rgba(255, 255, 255, 0.08)',
  color: 'var(--gh-text-primary)',
  border: '1px solid var(--gh-border)',
}

const tag: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  height: 14,
  padding: '0 5px',
  borderRadius: 3,
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '0.06em',
}

const DIR_TAG: Record<string, { text: string; style: CSSProperties }> = {
  long: { text: 'LONG', style: { background: 'rgba(52, 211, 153, 0.16)', color: '#34d399' } },
  short: { text: 'SHORT', style: { background: 'rgba(248, 113, 113, 0.16)', color: '#f87171' } },
  regime_switch: { text: 'SWITCH', style: { background: '#252b43', color: '#818cf8' } },
}

function ReturnText({ summary }: { summary: GroupResult['summary'] }) {
  const r = groupReturnText(summary)
  return (
    <span aria-label={r.aria} style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: 11, color: TONE[r.tone] }}>
      {r.text}
    </span>
  )
}

export default function GroupResultsStrip({ groups, combined, active, onSelect, onFrameGroup, stale = false }: Props) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const keys = [COMBINED, ...groups.map(g => g.name)]

  const move = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = keys.indexOf(active)
    let next = -1
    if (e.key === 'ArrowRight') next = Math.min(keys.length - 1, i + 1)
    else if (e.key === 'ArrowLeft') next = Math.max(0, i - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = keys.length - 1
    if (next < 0) return
    e.preventDefault()
    onSelect(keys[next])
    refs.current[keys[next]]?.focus()
  }

  const staleMark = (key: string) => (stale && key === active
    ? <span style={{ color: 'var(--gh-yellow)', fontSize: 11 }}>stale ·</span>
    : null)

  return (
    <div style={stripStyle} data-testid="graph-group-strip">
      <div role="tablist" aria-label="Result groups" style={{ display: 'flex', gap: 6 }} onKeyDown={move}>
        <button
          type="button"
          role="tab"
          ref={el => { refs.current[COMBINED] = el }}
          aria-selected={active === COMBINED}
          tabIndex={active === COMBINED ? 0 : -1}
          data-testid="graph-group-tab-combined"
          style={{ ...pillBase, ...(active === COMBINED ? pillActive : {}) }}
          onClick={() => onSelect(COMBINED)}
        >
          {staleMark(COMBINED)}
          <span aria-hidden="true">Σ</span>
          <span>{COMBINED_LABEL}</span>
          {combined && <ReturnText summary={combined.summary} />}
        </button>
        {groups.map(g => {
          const dir = DIR_TAG[g.direction] ?? DIR_TAG.long
          const isActive = active === g.name
          return (
            <button
              key={g.name}
              type="button"
              role="tab"
              ref={el => { refs.current[g.name] = el }}
              aria-selected={isActive}
              tabIndex={isActive ? 0 : -1}
              data-testid={`graph-group-tab-${g.name}`}
              style={{ ...pillBase, ...(isActive ? pillActive : {}) }}
              onClick={() => onSelect(g.name)}
              onDoubleClick={onFrameGroup ? () => onFrameGroup(g) : undefined}
            >
              {staleMark(g.name)}
              <span aria-hidden="true" style={{ ...tag, height: 12, padding: '0 3px', background: '#f1f5f9', color: '#0b0e14' }}>O</span>
              <span>{g.name}</span>
              {g.summary.open_position && (
                <span
                  title={OPEN_POSITION_TITLE}
                  aria-label={OPEN_POSITION_TITLE}
                  style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--gh-yellow)' }}
                />
              )}
              <span style={{ ...tag, ...dir.style }}>{dir.text}</span>
              <span style={{ ...tag, letterSpacing: 0, fontWeight: 500, fontFamily: 'var(--font-mono, monospace)', background: '#163741', color: '#22d3ee' }}>
                {g.interval ? `${g.symbol} · ${g.interval}` : g.symbol}
              </span>
              <ReturnText summary={g.summary} />
            </button>
          )
        })}
      </div>
      {active === COMBINED && (
        <span
          data-testid="graph-group-status"
          style={{ marginLeft: 'auto', paddingLeft: 12, fontFamily: 'var(--font-mono, monospace)', fontSize: 11, color: 'var(--gh-text-muted)', whiteSpace: 'nowrap' }}
        >
          {stripStatusText(groups, combined)}
        </span>
      )}
    </div>
  )
}
