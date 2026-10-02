/**
 * One attribute column header of the Data Sheet (S25 "Table"): the name,
 * the dtype tag, the "written by" chip, and (when the drawer is tall
 * enough) a histogram strip from `stats`.
 */

import { memo, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react'
import type { InspectColumn, InspectColumnStats } from '../../../api/nodebuilderInspect'
import { catVars } from '../inspector/util'
import { formatEdge, formatTruePct, groupThousands } from './format'

export interface WriterInfo {
  id: string
  name: string
  cat: string | null
}

export interface HeaderCellProps {
  column: InspectColumn
  width: number
  writer: WriterInfo | null
  stats: InspectColumnStats | undefined
  /** Rows the bool share is taken over. */
  total: number
  showHist: boolean
  /** The consumer of the inspected wire reads this column. */
  read: boolean
  index: number
  onSelectWriter(id: string): void
  onMenu(e: MouseEvent, column: InspectColumn): void
  /** Shift+F10 or the ContextMenu key on the focused header: open the menu at (x, y). */
  onMenuKey?(x: number, y: number, column: InspectColumn): void
}

/**
 * The header's stats as text (S25: the histogram's numbers live in the header
 * tooltip; UX-13). Empty when the server sent no stats.
 */
export function headerStatsText(column: InspectColumn, stats: InspectColumnStats | undefined, total: number): string {
  if (!stats) return ''
  const parts: string[] = []
  if (column.dtype === 'bool') {
    parts.push(`${formatTruePct(stats.true_count ?? 0, total)}`)
  } else {
    if (typeof stats.min === 'number') parts.push(`min ${formatEdge(stats.min)}`)
    if (typeof stats.max === 'number') parts.push(`max ${formatEdge(stats.max)}`)
  }
  if ((stats.nan_count ?? 0) > 0) parts.push(`${groupThousands(stats.nan_count ?? 0)} nan`)
  return parts.join(' · ')
}

function HeaderCellImpl({ column, width, writer, stats, total, showHist, read, index, onSelectWriter, onMenu, onMenuKey }: HeaderCellProps) {
  const isBool = column.dtype === 'bool'
  const style: CSSProperties = { width, minWidth: width, ...catVars(writer?.cat ?? null) }
  const selectWriter = () => { if (writer) onSelectWriter(writer.id) }
  const statsText = headerStatsText(column, stats, total)
  const nameTitle = `${writer ? `${column.name}, written by ${writer.name}` : column.name}${statsText ? ` · ${statsText}` : ''}`
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!onMenuKey) return
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault()
      e.stopPropagation()
      const r = e.currentTarget.getBoundingClientRect()
      onMenuKey(r.left, r.bottom, column)
    }
  }
  return (
    <div
      role="columnheader"
      aria-colindex={index + 2}
      aria-haspopup="menu"
      tabIndex={0}
      className={`nb-sheet__th${read ? ' nb-sheet__th--read' : ''}`}
      style={style}
      data-testid={`nb-sheet-col-${column.name}`}
      data-read={read ? 'true' : undefined}
      onContextMenu={e => onMenu(e, column)}
      onKeyDown={onKey}
    >
      <div className="nb-sheet__th-line">
        <span
          className="nb-sheet__th-name"
          title={nameTitle}
          onClick={selectWriter}
        >
          {column.name}
        </span>
        <span className="nb-sheet__th-dtype">{isBool ? 'b' : 'f'}</span>
        {writer && (
          <button
            type="button"
            className="nb-sheet__by"
            data-testid={`nb-sheet-by-${column.name}`}
            aria-label={`written by ${writer.name}; select node`}
            title={`written by ${writer.name}`}
            onClick={selectWriter}
          >
            {writer.name}
          </button>
        )}
      </div>
      {statsText && <span className="nb-sheet__sr">{statsText}</span>}
      {showHist && (
        <div className="nb-sheet__hist" aria-hidden="true">
          {isBool ? <BoolBar stats={stats} total={total} /> : <NumberHist stats={stats} />}
        </div>
      )}
    </div>
  )
}

function BoolBar({ stats, total }: { stats: InspectColumnStats | undefined; total: number }) {
  const t = stats?.true_count ?? 0
  const share = total > 0 ? Math.min(1, t / total) : 0
  return (
    <div className="nb-sheet__boolbar" title={`${groupThousands(t)} of ${groupThousands(total)} rows true`}>
      <div className="nb-sheet__boolbar-fill" style={{ width: `${share * 100}%` }} />
      <span className="nb-sheet__boolbar-text">{formatTruePct(t, total)}</span>
    </div>
  )
}

function NumberHist({ stats }: { stats: InspectColumnStats | undefined }) {
  const hist = stats?.hist
  const nan = stats?.nan_count ?? 0
  if (!hist || !Array.isArray(hist.counts) || hist.counts.length === 0) {
    return <div className="nb-sheet__hist-none">{nan > 0 ? `${groupThousands(nan)} nan` : ''}</div>
  }
  const peak = Math.max(1, nan, ...hist.counts)
  return (
    <div className="nb-sheet__bars">
      {hist.counts.map((c, i) => {
        const lo = hist.edges[i]
        const hi = hist.edges[i + 1]
        const last = i === hist.counts.length - 1
        const label = `[${formatEdge(lo)}, ${formatEdge(hi)}${last ? ']' : ')'} · ${groupThousands(c)} rows`
        return (
          <div key={i} className="nb-sheet__bar" title={label}>
            <div className="nb-sheet__bar-fill" style={{ height: `${c > 0 ? Math.max(8, (c / peak) * 100) : 0}%` }} />
          </div>
        )
      })}
      {nan > 0 && (
        <div className="nb-sheet__bar nb-sheet__bar--nan" title={`nan · ${groupThousands(nan)} rows`}>
          <div className="nb-sheet__bar-fill" style={{ height: `${Math.max(8, (nan / peak) * 100)}%` }} />
        </div>
      )}
    </div>
  )
}

export const HeaderCell = memo(HeaderCellImpl)
