import { requestOpenGraph } from '../nodebuilder/graphLinks'
import { CODE_DISABLED_PAUSE_TEXT, CODE_EXITS_ONLY_TEXT, CODE_EXITS_ONLY_TITLE } from './botPauseText'

const rowStyle: React.CSSProperties = {
  fontSize: 11, color: 'var(--gh-yellow-warm)', background: 'rgba(240,183,78,0.08)', padding: '3px 8px', borderRadius: 3,
}

/**
 * The pause-reason row. The server's text as sent, except the code-off
 * pause (`code_disabled`, F435 W7 S49), which reads as a sentence with an
 * `Open graph` link when the bot has a graph. The card never auto-starts
 * a bot when code comes back.
 */
export function PauseReasonRow({ reason, graphId, group }: { reason: string; graphId: string | null; group: string | null }) {
  const codeOff = reason.trim() === 'code_disabled'
  return (
    <div data-testid="bot-pause-reason" style={rowStyle}>
      {codeOff ? CODE_DISABLED_PAUSE_TEXT : reason}
      {codeOff && graphId && (
        <>
          {' '}
          <a
            href="#"
            onClick={e => { e.preventDefault(); requestOpenGraph({ graphId, group, spawn: false }) }}
            style={{ color: 'inherit', textDecoration: 'underline' }}
          >Open graph</a>
        </>
      )}
    </div>
  )
}

/**
 * The exits-only row (F435 W7, John's open position rule): the bot's code
 * failed while it held a position, so it manages only its price exits until
 * flat. Shown while the summary's `code_exits_only` is true.
 */
export function CodeExitsOnlyRow({ inline = false }: { inline?: boolean }) {
  if (inline) {
    return (
      <span
        data-testid="bot-code-exits-only"
        role="status"
        title={CODE_EXITS_ONLY_TITLE}
        style={{ marginLeft: 6, fontSize: 10, color: 'var(--gh-yellow-warm)' }}
      >
        {CODE_EXITS_ONLY_TEXT}
      </span>
    )
  }
  return (
    <div data-testid="bot-code-exits-only" role="status" title={CODE_EXITS_ONLY_TITLE} style={rowStyle}>
      {CODE_EXITS_ONLY_TEXT}
    </div>
  )
}
