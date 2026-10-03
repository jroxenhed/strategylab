/**
 * NoticeStack — the banners under the graph toolbar (surface S07).
 *
 * Newest on top, at most 3 shown; more collapse into a `+N more notices`
 * line. ok and info banners close after 6 s unless hovered, focused or
 * sticky; warn and error stay until dismissed or resolved. `extra` holds
 * banners derived from state (regime removed, unsupported nodes) that come
 * and go with that state instead of being pushed.
 */

import { useEffect, useRef, useState } from 'react'
import { Button } from './ui/Button'
import { dismissNotice, useNoticeStore, type Notice } from './notices'

const AUTO_DISMISS_MS = 6000
const MAX_VISIBLE = 3
/** An expanded stack collapses again after this long without pointer contact (S07). */
export const COLLAPSE_AFTER_MS = 10_000

function Banner({ notice }: { notice: Notice }) {
  const [busy, setBusy] = useState(false)
  const holdRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const autoMs =
    notice.timeoutMs ??
    ((notice.severity === 'ok' || notice.severity === 'info') && !notice.sticky ? AUTO_DISMISS_MS : null)

  const clearTimer = () => {
    if (timerRef.current != null) clearTimeout(timerRef.current)
    timerRef.current = null
  }
  const startTimer = () => {
    clearTimer()
    if (autoMs == null || holdRef.current) return
    timerRef.current = setTimeout(() => {
      notice.onDismiss?.()
      dismissNotice(notice.key)
    }, autoMs)
  }

  useEffect(() => {
    startTimer()
    return clearTimer
    // Restart when the banner is replaced (seq) or its timeout changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notice.seq, autoMs])

  const hold = () => {
    holdRef.current = true
    clearTimer()
  }
  const release = () => {
    holdRef.current = false
    startTimer()
  }

  const runAction = async (run: () => void | Promise<unknown>) => {
    setBusy(true)
    try {
      await run()
    } finally {
      setBusy(false)
    }
  }

  const alert = notice.severity === 'error' || notice.severity === 'warn'
  return (
    <div
      className={`nb-banner nb-banner--${notice.severity}`}
      role={notice.role ?? (alert ? 'alert' : 'status')}
      data-testid={`nb-banner-${notice.key}`}
      onMouseEnter={hold}
      onMouseLeave={release}
      onFocus={hold}
      onBlur={release}
    >
      {busy ? <span className="nb-spinner" aria-hidden="true" /> : <span className="nb-banner__dot" aria-hidden="true" />}
      <span className="nb-banner__text">{notice.text}</span>
      {notice.actions && notice.actions.length > 0 && (
        <span className="nb-banner__actions">
          {notice.actions.slice(0, 3).map(a => (
            <Button
              key={a.label}
              kind="text"
              title={a.title}
              data-testid={a.testId}
              disabled={busy}
              onClick={() => runAction(a.run)}
              onKeyDown={e => {
                if (e.key === 'Delete') {
                  notice.onDismiss?.()
                  dismissNotice(notice.key)
                }
              }}
            >
              {a.label}
            </Button>
          ))}
        </span>
      )}
      <Button
        kind="icon"
        className="nb-banner__dismiss"
        title={notice.dismissTitle ?? 'Dismiss'}
        aria-label={notice.dismissTitle ?? 'Dismiss'}
        data-testid="nb-banner-dismiss"
        onClick={() => {
          notice.onDismiss?.()
          dismissNotice(notice.key)
        }}
      >
        ✕
      </Button>
    </div>
  )
}

export default function NoticeStack({ extra = [] }: { extra?: Notice[] }) {
  const pushed = useNoticeStore(s => s.notices)
  const [expanded, setExpanded] = useState(false)
  const collapseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const stopCollapse = () => {
    if (collapseTimer.current != null) clearTimeout(collapseTimer.current)
    collapseTimer.current = null
  }
  const startCollapse = () => {
    stopCollapse()
    collapseTimer.current = setTimeout(() => {
      collapseTimer.current = null
      setExpanded(false)
    }, COLLAPSE_AFTER_MS)
  }
  useEffect(() => stopCollapse, [])
  // Pushed banners are newest first; derived ones sit below them.
  const all = [...pushed, ...extra.filter(e => !pushed.some(p => p.key === e.key))]
  if (all.length === 0) return null
  const shown = expanded ? all : all.slice(0, MAX_VISIBLE)
  const hidden = all.length - shown.length
  return (
    <div
      className="nb-notices"
      role="region"
      aria-label="Notices"
      data-testid="nb-notices"
      // While expanded: pointer contact holds the stack open; leaving restarts the 10 s.
      onPointerEnter={() => { if (expanded) stopCollapse() }}
      onPointerLeave={() => { if (expanded) startCollapse() }}
    >
      {shown.map(n => (
        <Banner key={n.key} notice={n} />
      ))}
      {hidden > 0 && (
        <button
          type="button"
          className="nb-notices__more"
          onClick={e => {
            setExpanded(true)
            // A mouse click leaves the pointer on the stack: the 10 s start
            // when it leaves. A keyboard press has no pointer: start now.
            if (e.detail === 0) startCollapse()
          }}
        >
          +{hidden} more notice{hidden === 1 ? '' : 's'}
        </button>
      )}
    </div>
  )
}
