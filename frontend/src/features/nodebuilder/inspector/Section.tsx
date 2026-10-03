/**
 * A collapsible Inspector section (S14, S15): a 28px header button with
 * `▾`/`▸`, a caps title and an optional count on the right, then the body.
 * Expanded state is kept per id in `nb.inspector.sections`.
 */

import { useId, type ReactNode } from 'react'
import { isSectionOpen, setSectionOpen, useInspectorUi } from './state'

export function InspectorSectionShell({
  id,
  title,
  count,
  countKind,
  forceOpen = false,
  stateKey,
  defaultOpen,
  children,
}: {
  id: string
  title: string
  count?: ReactNode
  /** `error` / `warn` colors the count. */
  countKind?: 'error' | 'warn'
  /** Show the body even if the user collapsed it (the empty graph's Keys). */
  forceOpen?: boolean
  /** The key the open state is kept under (default: `id`). */
  stateKey?: string
  /** Open state until the user toggles it (default: the built-in default). */
  defaultOpen?: boolean
  children: ReactNode
}) {
  const key = stateKey ?? id
  const open = useInspectorUi(s => (
    key in s.sections || defaultOpen === undefined ? isSectionOpen(s.sections, key) : defaultOpen
  )) || forceOpen
  const bodyId = `nb-insp-sec-${useId()}`
  return (
    <section className="nb-insp-sec" data-testid={`nb-inspector-section-${id}`}>
      <button
        type="button"
        className="nb-insp-sec__head"
        aria-expanded={open}
        // The body is not rendered while collapsed, so point at it only when it is there (UX-21).
        aria-controls={open ? bodyId : undefined}
        // A forced-open section ignores the click, and its stored state with it.
        onClick={() => { if (!forceOpen) setSectionOpen(key, !open) }}
      >
        <span className="nb-insp-sec__caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="nb-insp-sec__title">{title}</span>
        {count != null && count !== '' && (
          <span className={`nb-insp-sec__count${countKind ? ` nb-insp-sec__count--${countKind}` : ''}`}>{count}</span>
        )}
      </button>
      {open && (
        <div id={bodyId} className="nb-insp-sec__body">
          {children}
        </div>
      )}
    </section>
  )
}
