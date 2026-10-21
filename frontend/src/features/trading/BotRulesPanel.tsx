import { useEffect, useState } from 'react'
import { describeBot, describeErrorText, isDescribeAbort, type DescribeResponse } from '../../api/describe'
import { btnStyle } from './ui'

type Load =
  | { phase: 'loading' }
  | { phase: 'ok'; data: DescribeResponse }
  | { phase: 'error'; text: string }

/**
 * The bot's strategy as plain text (F454): what makes it buy and sell, its
 * stops and its size. It loads once each time it opens, from
 * GET /api/bots/{id}/describe, so it always shows the bot's current rules.
 * Closing it (or the card going away) cancels a load still on its way.
 */
export default function BotRulesPanel({ botId, onClose, id }: { botId: string; onClose: () => void; id?: string }) {
  const [load, setLoad] = useState<Load>({ phase: 'loading' })

  useEffect(() => {
    const ctrl = new AbortController()
    setLoad({ phase: 'loading' })
    describeBot(botId, ctrl.signal)
      .then(data => { if (!ctrl.signal.aborted) setLoad({ phase: 'ok', data }) })
      .catch(e => {
        if (ctrl.signal.aborted || isDescribeAbort(e)) return
        setLoad({ phase: 'error', text: describeErrorText(e, 'bot') })
      })
    return () => ctrl.abort()
  }, [botId])

  return (
    <div
      id={id}
      role="region"
      aria-label="Bot rules"
      data-testid="botcard-rules"
      onClick={e => e.stopPropagation()}
      style={{
        background: 'var(--gh-bg-deep)', border: '1px solid var(--gh-bg-alt)', borderRadius: 4,
        padding: '6px 8px', fontSize: 12, display: 'flex', flexDirection: 'column', gap: 3,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, textTransform: 'uppercase', color: 'var(--gh-text-faint)' }}>
          Rules
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close rules"
          title="Close"
          style={{ ...btnStyle('var(--gh-bg-alt)'), marginLeft: 'auto', height: 20, padding: '0 6px', fontSize: 11 }}
        >×</button>
      </div>
      {load.phase === 'loading' && (
        <span style={{ color: 'var(--gh-text-placeholder)' }}>Loading the rules…</span>
      )}
      {load.phase === 'error' && (
        <span role="alert" data-testid="botcard-rules-error" style={{ color: 'var(--gh-red-alt)' }}>
          Could not show the rules. {load.text}
        </span>
      )}
      {load.phase === 'ok' && (
        <>
          {load.data.lines.length === 0 && (
            <span style={{ color: 'var(--gh-text-placeholder)' }}>No rules to show.</span>
          )}
          {load.data.lines.map((line, i) => (
            <div key={i} data-testid="botcard-rules-line" style={{ color: 'var(--gh-text-light)' }}>{line}</div>
          ))}
          {load.data.warnings.map((w, i) => (
            <div key={`w${i}`} data-testid="botcard-rules-warning" style={{ color: 'var(--gh-yellow-warm)', fontSize: 11 }}>
              {w}
            </div>
          ))}
        </>
      )}
    </div>
  )
}
