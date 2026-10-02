/**
 * Small dialogs used by the graph toolbar and the Graph Browser (S01):
 * - NameDialog: Save as / Rename graph / New graph, one Name field.
 * - SaveChangesDialog: "Save changes to <name>?" before leaving a dirty graph.
 * - DeleteGraphDialog: "Delete <name>?" confirm.
 * - RelativeTimeText: a refreshing relative time with a full-time title.
 *
 * All use the shared Dialog shell (ui/Dialog.tsx).
 */

import { useId, useState } from 'react'
import { Dialog, PRIMARY_ATTR } from './ui/Dialog'
import { Button } from './ui/Button'
import { nameError } from './persistence'
import { formatFullTimestamp, useRelativeTime } from './ui/relativeTime'

/** A relative time (`5 min ago`) that refreshes, with the full timestamp as its title. */
/**
 * A relative time with the full timestamp as its title. `datePrefix` goes in
 * front only when the text is a date (`YYYY-MM-DD`), so a sentence can read
 * "saved on 2026-09-12" but "saved 5 min ago".
 */
export function RelativeTimeText({ iso, className, datePrefix }: { iso: string; className?: string; datePrefix?: string }) {
  const rel = useRelativeTime(iso)
  const isDate = /^\d{4}-\d{2}-\d{2}$/.test(rel)
  return (
    <span className={className} title={formatFullTimestamp(iso)}>
      {isDate && datePrefix ? `${datePrefix}${rel}` : rel}
    </span>
  )
}

export interface NameDialogProps {
  title: string
  primaryLabel: string
  initialName: string
  /** Save the name. Resolve with an error sentence to keep the dialog open, or null when done. */
  onSubmit: (name: string) => Promise<string | null>
  onCancel: () => void
  /** An error to show from the start (a name clash from an earlier try). */
  initialError?: string | null
}

export function NameDialog({ title, primaryLabel, initialName, onSubmit, onCancel, initialError }: NameDialogProps) {
  const [name, setName] = useState(initialName)
  const [error, setError] = useState<string | null>(initialError ?? null)
  const [busy, setBusy] = useState(false)
  const fieldId = useId()

  const submit = async () => {
    if (busy) return
    const local = nameError(name)
    if (local) {
      setError(local)
      return
    }
    setBusy(true)
    try {
      const err = await onSubmit(name.trim())
      if (err) setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={title}
      width={360}
      onCancel={onCancel}
      primaryLabel={busy ? 'Saving…' : primaryLabel}
      onPrimary={submit}
      primaryDisabled={busy}
      data-testid="nb-name-dialog"
    >
      <label className="nb-field-label" htmlFor={fieldId}>Name</label>
      <input
        id={fieldId}
        className="nb-input nb-input--mono"
        value={name}
        maxLength={200}
        aria-invalid={error ? 'true' : undefined}
        aria-describedby={`${fieldId}-msg`}
        data-testid="nb-name-input"
        onChange={e => {
          setName(e.target.value)
          if (error) setError(null)
        }}
        onFocus={e => e.currentTarget.select()}
      />
      {error ? (
        <div id={`${fieldId}-msg`} className="nb-field-error" role="alert">{error}</div>
      ) : (
        <div id={`${fieldId}-msg`} className="nb-field-help">1 to 80 characters. Names are unique.</div>
      )}
    </Dialog>
  )
}

export interface SaveChangesDialogProps {
  name: string
  onSave: () => void
  onDiscard: () => void
  onCancel: () => void
}

export function SaveChangesDialog({ name, onSave, onDiscard, onCancel }: SaveChangesDialogProps) {
  return (
    <Dialog
      title={`Save changes to ${name}?`}
      width={360}
      onCancel={onCancel}
      data-testid="nb-save-changes-dialog"
      footerLeft={
        <Button kind="danger" onClick={onDiscard} data-testid="nb-save-changes-discard">
          Discard
        </Button>
      }
      footer={
        <>
          <Button onClick={onCancel} data-testid="nb-dialog-cancel">Cancel</Button>
          <Button kind="primary" onClick={onSave} data-testid="nb-save-changes-save" {...{ [PRIMARY_ATTR]: '' }}>
            Save
          </Button>
        </>
      }
    >
      Your unsaved edits will be lost if you discard them.
    </Dialog>
  )
}

export interface DeleteGraphDialogProps {
  name: string
  onDelete: () => void
  onCancel: () => void
}

export function DeleteGraphDialog({ name, onDelete, onCancel }: DeleteGraphDialogProps) {
  return (
    <Dialog
      title={`Delete ${name}?`}
      width={400}
      onCancel={onCancel}
      primaryLabel="Delete"
      onPrimary={onDelete}
      danger
      data-testid="nb-delete-dialog"
    >
      This removes the graph from the server for everyone. Bots already spawned keep their own copy of the graph.
    </Dialog>
  )
}
