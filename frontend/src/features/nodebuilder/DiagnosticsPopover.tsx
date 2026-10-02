/**
 * DiagnosticsPopover: the graph-wide list of problems (spec S05, amendment
 * A3). The toolbar chip and the status-bar segment open it.
 *
 * Rows are grouped by node (the node's name; `Graph` for problems with no
 * node). A row click on a problem about a wire (`port` set, or
 * `dangling_wire`) selects that wire (focusDiagnosticWire, W2); any other row
 * hands the node id to `onSelectNode`, which selects it, and then frames the
 * node when it is not fully on screen (F behavior, W3 3.D), and opens the
 * Inspector with Diagnostics expanded and the param flashed (S05, A3);
 * graph-level rows do nothing. Arrow keys move between rows; Esc closes (the shell).
 */

import { useMemo, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { Popover } from './ui/Popover'
import { useNodeBuilderStore } from './store'
import { focusDiagnosticWire, useDiagnostics } from './useDiagnostics'
import { frameNode } from './viewOps'
import { openInspectorDiagnostics } from './inspector/state'
import type { Diagnostic } from '../../api/nodebuilderValidate'
import './diagnostics.css'

export interface DiagnosticsPopoverProps {
  open: boolean
  anchorEl: HTMLElement | null
  onClose: () => void
  onSelectNode: (nodeId: string) => void
}

/** `2 errors · 1 warning`, or `No problems` when there are neither. */
export function diagnosticsSummary(errorCount: number, warningCount: number): string {
  const parts: string[] = []
  if (errorCount) parts.push(`${errorCount} ${errorCount === 1 ? 'error' : 'errors'}`)
  if (warningCount) parts.push(`${warningCount} ${warningCount === 1 ? 'warning' : 'warnings'}`)
  return parts.length ? parts.join(' · ') : 'No problems'
}

interface Group {
  key: string
  nodeId: string | null
  rows: { d: Diagnostic; index: number }[]
}

/** Group by node in order of first appearance; index is the flat row index. */
function groupDiagnostics(list: readonly Diagnostic[]): Group[] {
  const groups: Group[] = []
  const byKey = new Map<string, Group>()
  // Flat order follows the groups, so row indexes read top to bottom.
  for (const d of list) {
    const key = d.node_id ?? '\u0000graph'
    let g = byKey.get(key)
    if (!g) {
      g = { key, nodeId: d.node_id, rows: [] }
      byKey.set(key, g)
      groups.push(g)
    }
    g.rows.push({ d, index: -1 })
  }
  let i = 0
  for (const g of groups) for (const r of g.rows) r.index = i++
  return groups
}

export function DiagnosticsPopover({ open, anchorEl, onClose, onSelectNode }: DiagnosticsPopoverProps) {
  const { diagnostics, errorCount, warningCount } = useDiagnostics()
  const nodes = useNodeBuilderStore(s => s.graph?.nodes)
  const listRef = useRef<HTMLDivElement>(null)
  const groups = useMemo(() => groupDiagnostics(diagnostics), [diagnostics])

  if (!open) return null

  const groupTitle = (g: Group) => {
    if (!g.nodeId) return 'Graph'
    const first = g.rows[0]?.d
    return nodes?.[g.nodeId]?.name ?? first?.path?.split('/').pop() ?? g.nodeId
  }

  // Up and Down move focus between rows (wrapping), Home and End jump.
  // From outside the rows (the ✕), Down goes to the first row, Up to the last.
  const onListKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    const rows = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('[data-nb-diag-row]') ?? [])
    if (rows.length === 0) return
    e.preventDefault()
    const at = rows.indexOf(document.activeElement as HTMLButtonElement)
    let next = 0
    if (e.key === 'ArrowDown') next = at < 0 ? 0 : (at + 1) % rows.length
    else if (e.key === 'ArrowUp') next = at < 0 ? rows.length - 1 : (at - 1 + rows.length) % rows.length
    else if (e.key === 'End') next = rows.length - 1
    rows[next].focus()
  }

  const bounds = (anchorEl?.closest('.nodebuilder-root') as HTMLElement | null) ?? null

  return (
    <Popover
      anchor={anchorEl}
      bounds={bounds}
      onClose={() => onClose()}
      role="dialog"
      ariaLabel="Diagnostics"
      width={380}
      maxHeight={360}
      autoFocus
      data-testid="nb-diag-popover"
    >
      {/* Keys on the whole popover: first focus is the header ✕, and
          ArrowDown from there must reach the first row. */}
      <div className="nb-diag-pop" onKeyDown={onListKeyDown}>
        <div className="nb-diag-pop__header">
          <span>{diagnosticsSummary(errorCount, warningCount)}</span>
          <button type="button" className="nb-diag-pop__close" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <div className="nb-diag-pop__list" ref={listRef}>
          {diagnostics.length === 0 && (
            <div className="nb-diag-pop__empty">No problems in this graph.</div>
          )}
          {groups.map(g => (
            <div key={g.key} role="group" aria-label={groupTitle(g)}>
              <div className="nb-diag-pop__group">{groupTitle(g)}</div>
              {g.rows.map(({ d, index }) => (
                <button
                  key={index}
                  type="button"
                  data-nb-diag-row=""
                  data-testid={`nb-diag-row-${index}`}
                  className={g.nodeId ? 'nb-diag-pop__row' : 'nb-diag-pop__row nb-diag-pop__row--static'}
                  onClick={() => {
                    if (focusDiagnosticWire(d) || !g.nodeId) return
                    onSelectNode(g.nodeId)
                    frameNode(g.nodeId, { onlyIfOffscreen: true })
                    openInspectorDiagnostics(g.nodeId, d.param)
                  }}
                >
                  <span className={`nb-diag-pop__dot nb-diag-pop__dot--${d.severity}`} aria-hidden="true" />
                  <span className="nb-diag-pop__msg">{d.message}</span>
                  <span className="nb-diag-pop__code">{d.code}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
        {errorCount > 0 && (
          <div className="nb-diag-pop__footer">Run is disabled until the errors are fixed.</div>
        )}
      </div>
    </Popover>
  )
}

export default DiagnosticsPopover
