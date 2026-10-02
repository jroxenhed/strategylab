/**
 * ConflictDialog — "Saved elsewhere" (surface S04).
 *
 * Opens when a save came back 409 rev_conflict. It fetches the server
 * version, shows which rev each side is on, and offers three ways out:
 * Save as copy, Reload theirs, or Overwrite. Overwrite stays disabled until
 * the user has opened Compare once. Nothing is retried on its own.
 */

import { useCallback, useEffect, useState } from 'react'
import type { Graph } from '../../api/nodebuilder'
import { getGraph, graphErrorDetail, isRevConflict, type GraphEnvelope } from '../../api/graphs'
import { Dialog } from './ui/Dialog'
import { Button } from './ui/Button'
import { RelativeTimeText } from './GraphDialogs'
import { compareGraphs, describeGraphDiff } from './persistence'

export interface ConflictDialogProps {
  graphId: string
  name: string
  /** The rev the local copy is based on. */
  myRev: number
  /** The rev the 409 reported. The fetched server version may be newer. */
  currentRev: number
  localGraph: Graph
  onSaveAsCopy: () => void
  /** Replace the local graph with the server version. */
  onReloadTheirs: (server: GraphEnvelope) => void
  /** Save the local graph over the server version at `rev`. Throws RevConflictError when it moved again. */
  onOverwrite: (rev: number) => Promise<void>
  onCancel: () => void
}

type Fetch =
  | { state: 'loading' }
  | { state: 'ready'; env: GraphEnvelope }
  | { state: 'failed'; detail: string }

export default function ConflictDialog(props: ConflictDialogProps) {
  const { graphId, name, myRev, localGraph, onSaveAsCopy, onReloadTheirs, onOverwrite, onCancel } = props
  const [currentRev, setCurrentRev] = useState(props.currentRev)
  const [fetchState, setFetchState] = useState<Fetch>({ state: 'loading' })
  const [compareOpen, setCompareOpen] = useState(false)
  const [compared, setCompared] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setFetchState({ state: 'loading' })
    try {
      const env = await getGraph(graphId)
      setFetchState({ state: 'ready', env })
      setCurrentRev(r => Math.max(r, env.rev))
    } catch (e) {
      setFetchState({ state: 'failed', detail: graphErrorDetail(e) })
    }
  }, [graphId])

  useEffect(() => {
    void load()
  }, [load])

  const ready = fetchState.state === 'ready' ? fetchState.env : null
  const serverRev = ready ? ready.rev : currentRev

  const overwrite = async () => {
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    try {
      await onOverwrite(serverRev)
    } catch (e) {
      if (isRevConflict(e)) {
        // Someone saved again: show the new numbers and ask for a new look.
        setCurrentRev(e.current_rev)
        setCompared(false)
        setCompareOpen(false)
        await load()
      } else {
        setError(graphErrorDetail(e))
      }
    } finally {
      setBusy(false)
    }
  }

  const diffLines = ready
    ? describeGraphDiff(compareGraphs(localGraph, ready.graph, { local: name, server: ready.name }))
    : []

  const footer = (
    <>
      <Button
        onClick={onSaveAsCopy}
        disabled={busy || fetchState.state === 'loading'}
        data-testid="nb-conflict-copy"
      >
        Save as copy
      </Button>
      {fetchState.state !== 'failed' && (
        <>
          <Button
            onClick={() => ready && onReloadTheirs(ready)}
            disabled={!ready || busy}
            data-testid="nb-conflict-reload"
          >
            Reload theirs
          </Button>
          <Button
            kind="danger"
            onClick={overwrite}
            disabled={!ready || !compared || busy}
            disabledReason={ready && !compared ? 'Open Compare first' : undefined}
            data-testid="nb-conflict-overwrite"
          >
            Overwrite
          </Button>
        </>
      )}
    </>
  )

  return (
    <Dialog title="Saved elsewhere" width={480} onCancel={onCancel} footer={footer} data-testid="nb-conflict-dialog">
      {fetchState.state === 'loading' && (
        <div className="nb-dialog-status">
          <span className="nb-spinner" aria-hidden="true" /> Checking the server version…
        </div>
      )}
      {fetchState.state === 'failed' && (
        <div className="nb-field-error" role="alert">Could not fetch the server version: {fetchState.detail}</div>
      )}
      {ready && (
        <>
          <p className="nb-conflict__text" data-testid="nb-conflict-text">
            {name} was saved from another window <RelativeTimeText iso={ready.updated_at} datePrefix="on " /> (rev {serverRev}). Your copy is
            based on rev {myRev}.
          </p>
          <Button
            kind="text"
            aria-expanded={compareOpen}
            data-testid="nb-conflict-compare"
            onClick={() => {
              setCompareOpen(o => !o)
              setCompared(true)
            }}
          >
            Compare
          </Button>
          {compareOpen && (
            <div className="nb-conflict__diff" role="region" aria-label="Differences" data-testid="nb-conflict-diff">
              <div className="nb-caps">What changed on the server</div>
              {diffLines.map(line => (
                <div key={line}>{line}</div>
              ))}
            </div>
          )}
          {error && <div className="nb-field-error" role="alert">{error}</div>}
        </>
      )}
    </Dialog>
  )
}
