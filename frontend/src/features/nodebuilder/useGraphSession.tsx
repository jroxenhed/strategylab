/**
 * useGraphSession — what the graph toolbar's actions do (S01 to S04).
 *
 * Owns the dialogs (name, save changes, delete, conflict), the Graph
 * Browser, the hidden file input for Import JSON, the draft restore banner
 * and the "open the last graph on start" step. NodeBuilder mounts it once
 * and renders `element`.
 *
 * Rules kept here:
 * - Server saves are explicit. Drafts go to localStorage only.
 * - A draft is never restored without the user saying so.
 * - A 409 never overwrites: it opens the conflict dialog, and a later Save
 *   re-opens that dialog without a new PUT until it is resolved.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { emptyGraph, type Graph } from '../../api/nodebuilder'
import {
  createGraph,
  deleteGraph,
  errorDiagnostics,
  getGraph,
  graphErrorDetail,
  isGraphCorrupt,
  isNameTaken,
  isRevConflict,
  saveGraph,
  type GraphEnvelope,
  type GraphListItem,
} from '../../api/graphs'
import { hasEdits, useNodeBuilderStore, type GraphMeta } from './store'
import {
  clearDraft,
  copyName,
  downloadJson,
  draftPromptFor,
  exportFileName,
  exportPayload,
  getLastGraphId,
  importName,
  nameTakenText,
  parseImport,
  readDraft,
  saveDraft,
  setLastGraphId,
  useDraftAutosave,
  writeDraftNow,
  type Draft,
  type DraftVariant,
} from './persistence'
import { modKeyCap } from './graphText'
import { dismissNotice, pushNotice, resolveNotice } from './notices'
import { DeleteGraphDialog, NameDialog, RelativeTimeText, SaveChangesDialog } from './GraphDialogs'
import ConflictDialog from './ConflictDialog'
import GraphBrowser from './GraphBrowser'

/** Tries before giving up on finding a free "copy N" / "(imported N)" name. */
const NAME_TRIES = 50
/** How long the "Draft kept" line stays before the old draft is dropped. */
const DRAFT_KEPT_MS = 10_000

type DialogState =
  | {
      kind: 'name'
      title: string
      primaryLabel: string
      initialName: string
      initialError?: string | null
      onSubmit: (name: string) => Promise<string | null>
      onCancel: () => void
    }
  | { kind: 'saveChanges'; name: string; onSave: () => void; onDiscard: () => void; onCancel: () => void }
  | { kind: 'delete'; name: string; onDelete: () => void; onCancel: () => void }
  | { kind: 'conflict'; graphId: string; name: string; myRev: number; currentRev: number }

interface PendingConflict {
  graphId: string
  currentRev: number
  myRev: number
}

interface DraftPrompt {
  draft: Draft
  variant: DraftVariant | 'kept'
  graphId: string | null
  serverRev: number
  seqAtShow: number
}

const st = () => useNodeBuilderStore.getState()

function editable(g: Graph): Graph {
  return g.readOnly ? { ...g, readOnly: false } : g
}

function metaOf(env: GraphEnvelope): GraphMeta {
  return { id: env.id, rev: env.rev, name: env.name }
}

/** Record a save of `sent`. Edits made while the request was out stay dirty. */
function finishSave(meta: GraphMeta, sent: Graph) {
  const s = st()
  if (s.graph === sent) s.markSaved(meta)
  else useNodeBuilderStore.setState({ graphMeta: meta, savedGraph: sent, dirty: s.graph !== sent })
}

export interface GraphSession {
  busy: 'saving' | 'loading' | null
  save: () => void
  saveAs: () => void
  rename: () => void
  renameInline: (name: string) => Promise<string | null>
  newGraph: () => void
  openBrowser: () => void
  duplicate: () => void
  exportJson: () => void
  importJson: () => void
  deleteCurrent: () => void
  closeGraph: () => void
  /** Open `nb.lastGraph` when nothing is loaded yet (app start). */
  openLastGraph: () => void
  /** Dialogs, the browser and the hidden file input. Render once. */
  element: ReactNode
}

export function useGraphSession(): GraphSession {
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [browserOpen, setBrowserOpen] = useState(false)
  const [browserRefresh, setBrowserRefresh] = useState(0)
  const [busy, setBusyState] = useState<'saving' | 'loading' | null>(null)
  const busyRef = useRef<'saving' | 'loading' | null>(null)
  const conflictRef = useRef<PendingConflict | null>(null)
  const draftRef = useRef<DraftPrompt | null>(null)
  const draftKeptTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const lastTried = useRef(false)
  // Bumped by every openGraphById; an older load's late answer is dropped.
  const loadToken = useRef(0)

  const setBusy = (b: 'saving' | 'loading' | null) => {
    busyRef.current = b
    setBusyState(b)
  }
  const closeDialog = () => setDialog(null)
  const refreshBrowser = () => setBrowserRefresh(n => n + 1)

  useDraftAutosave()

  // ── Draft restore banner (S03) ──────────────────────────────────────────

  const clearKeptTimer = () => {
    if (draftKeptTimer.current != null) clearTimeout(draftKeptTimer.current)
    draftKeptTimer.current = null
  }

  const dropDraftPrompt = () => {
    clearKeptTimer()
    draftRef.current = null
    dismissNotice('draft_found')
  }

  const restoreDraft = () => {
    const p = draftRef.current
    if (!p) return
    const s = st()
    if ((s.graphMeta?.id ?? null) !== p.graphId || !s.graph || s.graph.readOnly) return
    clearKeptTimer()
    // Clear first: the restore is itself a commit and must not look like an edit.
    draftRef.current = null
    s.commit('restore draft', () => editable(p.draft.graph))
    if (p.variant === 'moved' && p.graphId) {
      // The server moved on: the next Save goes straight to the conflict dialog.
      conflictRef.current = { graphId: p.graphId, currentRev: p.serverRev, myRev: p.draft.rev }
    }
    resolveNotice('draft_found')
  }

  const discardDraft = () => {
    const p = draftRef.current
    if (p) clearDraft(p.graphId)
    dropDraftPrompt()
  }

  const showDraftPrompt = (p: DraftPrompt) => {
    const name = p.draft.name || 'untitled'
    const time = <RelativeTimeText iso={p.draft.savedAt} />
    const text =
      p.variant === 'moved' ? (
        <>
          An unsaved draft of {name} from {time} was found, but the server has a newer version (rev {p.serverRev}).
          Restoring it will need Save as copy.
        </>
      ) : (
        <>
          A newer unsaved draft of {name} from {time} was found in this browser.
        </>
      )
    pushNotice({
      key: 'draft_found',
      severity: 'info',
      sticky: true,
      text,
      dismissTitle: 'Discard draft',
      onDismiss: discardDraft,
      actions: [
        { label: 'Restore draft', run: restoreDraft, testId: 'nb-draft-restore' },
        { label: 'Discard', run: discardDraft, testId: 'nb-draft-discard' },
      ],
    })
  }

  const checkDraft = (graphId: string | null, serverRev: number) => {
    dropDraftPrompt()
    const s = st()
    if (!s.graph || s.graph.readOnly) return
    const draft = readDraft(graphId)
    const variant = draftPromptFor(draft, s.graph, serverRev)
    if (!draft || !variant) return
    const p: DraftPrompt = { draft, variant, graphId, serverRev, seqAtShow: s.commitSeq }
    draftRef.current = p
    showDraftPrompt(p)
  }

  // An edit while the prompt is up means "keep the server version": the
  // banner turns into "Draft kept · Restore" for 10 s, then the old draft
  // is dropped. Loading another graph closes the prompt.
  useEffect(() => {
    return useNodeBuilderStore.subscribe((s, prev) => {
      const p = draftRef.current
      if (!p) return
      if (s.layoutEpoch !== prev.layoutEpoch || s.graph == null) {
        dropDraftPrompt()
        return
      }
      if (p.variant !== 'kept' && s.commitSeq !== p.seqAtShow) {
        const kept: DraftPrompt = { ...p, variant: 'kept' }
        draftRef.current = kept
        const savedAt = p.draft.savedAt
        const dropOld = () => {
          // Only drop the draft we offered; the autosave may have written a newer one.
          if (readDraft(p.graphId)?.savedAt === savedAt) clearDraft(p.graphId)
          if (draftRef.current === kept) draftRef.current = null
        }
        clearKeptTimer()
        draftKeptTimer.current = setTimeout(() => {
          draftKeptTimer.current = null
          dropOld()
          dismissNotice('draft_found')
        }, DRAFT_KEPT_MS)
        pushNotice({
          key: 'draft_found',
          severity: 'info',
          sticky: true,
          text: 'Draft kept',
          dismissTitle: 'Discard draft',
          onDismiss: () => {
            clearKeptTimer()
            dropOld()
          },
          actions: [{ label: 'Restore', run: restoreDraft, testId: 'nb-draft-restore' }],
        })
      }
    })
    // Handlers read refs and the store; subscribe once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => clearKeptTimer, [])

  // ── Loading ─────────────────────────────────────────────────────────────

  const afterLoad = (env: GraphEnvelope) => {
    setLastGraphId(env.id)
    conflictRef.current = null
    resolveNotice('rev_conflict_pending')
    checkDraft(env.id, env.rev)
  }

  /**
   * Load a server graph into the editor. Resolves false when the answer came
   * too late: a newer load started, or the store moved on meanwhile (Edit
   * this graph, New, an import, an edit). The late graph never replaces
   * what the user is working on.
   */
  const openGraphById = async (id: string): Promise<boolean> => {
    const token = ++loadToken.current
    const seqAtStart = st().commitSeq
    setBusy('loading')
    let env: GraphEnvelope
    try {
      env = await getGraph(id)
    } finally {
      if (token === loadToken.current) setBusy(null)
    }
    if (token !== loadToken.current || st().commitSeq !== seqAtStart) return false
    st().openGraph(editable(env.graph), metaOf(env))
    afterLoad(env)
    return true
  }

  const openLastGraph = useCallback(() => {
    if (lastTried.current) return
    lastTried.current = true
    if (st().graph != null) return
    const id = getLastGraphId()
    if (!id) return
    openGraphById(id).catch(e => {
      // Gone from the server: forget it. Anything else: try again next start.
      if ((e as { response?: { status?: number } })?.response?.status === 404) setLastGraphId(null)
      else console.warn('[nodebuilder] could not reopen the last graph', e)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Name dialog, save and leave ─────────────────────────────────────────

  const askName = (
    opts: { title: string; primaryLabel: string; initialName: string; initialError?: string | null },
    submit: (name: string) => Promise<string | null>,
  ): Promise<boolean> =>
    new Promise(resolve => {
      setDialog({
        kind: 'name',
        ...opts,
        onSubmit: async name => {
          const err = await submit(name)
          if (!err) {
            closeDialog()
            resolve(true)
          }
          return err
        },
        onCancel: () => {
          closeDialog()
          resolve(false)
        },
      })
    })

  /** Create a new server graph holding the current graph; the editor switches to it. */
  const createFromCurrent = async (name: string): Promise<string | null> => {
    const s = st()
    const sent = s.graph
    if (!sent) return null
    const oldId = s.graphMeta?.id ?? null
    let env: GraphEnvelope
    try {
      env = await createGraph({ name, graph: sent })
    } catch (e) {
      return isNameTaken(e) ? nameTakenText(name) : graphErrorDetail(e)
    }
    finishSave(metaOf(env), sent)
    // The edits now live in the new graph; the old draft would only prompt again.
    clearDraft(oldId)
    setLastGraphId(env.id)
    conflictRef.current = null
    resolveNotice('rev_conflict_pending')
    refreshBrowser()
    return null
  }

  const openConflict = () => {
    const c = conflictRef.current
    const meta = st().graphMeta
    if (!c || !meta?.id || c.graphId !== meta.id) return
    setDialog({ kind: 'conflict', graphId: c.graphId, name: meta.name, myRev: c.myRev, currentRev: c.currentRev })
  }

  /**
   * After a save of `sent`: drop the draft, unless edits were made while the
   * request was out. Those are still unsaved, so the draft is rewritten to
   * hold them instead.
   */
  const keepDraftInStep = (graphId: string, sent: Graph) => {
    if (st().graph === sent) clearDraft(graphId)
    else writeDraftNow()
  }

  /** Save the graph. Resolves true when it is saved (or there was nothing to save). */
  const saveFlow = async (): Promise<boolean> => {
    const s = st()
    if (!s.graph || s.graph.readOnly || busyRef.current) return false
    const meta = s.graphMeta
    if (!meta?.id) {
      return askName({ title: 'Save as', primaryLabel: 'Save', initialName: '' }, createFromCurrent)
    }
    if (conflictRef.current && conflictRef.current.graphId === meta.id) {
      openConflict()
      return false
    }
    // Nothing to save (Cmd+S on a clean graph, as the disabled Save button):
    // no PUT, so no rev bump for other windows and no draft deleted under an
    // undecided "draft found" prompt.
    if (!s.dirty) return true
    const sent = s.graph
    setBusy('saving')
    try {
      const env = await saveGraph(meta.id, { rev: meta.rev, graph: sent })
      finishSave(metaOf(env), sent)
      keepDraftInStep(meta.id, sent)
      setLastGraphId(env.id)
      resolveNotice('server_error')
      refreshBrowser()
      return true
    } catch (e) {
      if (isRevConflict(e)) {
        conflictRef.current = { graphId: meta.id, currentRev: e.current_rev, myRev: meta.rev }
        openConflict()
      } else if (isGraphCorrupt(e)) {
        // The server's copy cannot be read: not a conflict, and a retry
        // would fail the same way. The edits stay here, unsaved.
        pushNotice({
          key: 'server_error',
          severity: 'error',
          text: `Could not save: ${graphErrorDetail(e)} Save as… keeps your edits in a new graph.`,
        })
      } else {
        pushNotice({
          key: 'server_error',
          severity: 'error',
          text: `Could not save: ${graphErrorDetail(e)}`,
          actions: [{ label: 'Retry', run: () => saveFlow() }],
        })
      }
      return false
    } finally {
      setBusy(null)
    }
  }

  /** Before replacing the graph on screen: ask to save a dirty graph. Resolves true to go on. */
  const confirmLeave = (): Promise<boolean> => {
    const s = st()
    if (!hasEdits(s)) return Promise.resolve(true)
    return new Promise(resolve => {
      setDialog({
        kind: 'saveChanges',
        name: s.graphMeta?.name ?? 'untitled',
        onDiscard: () => {
          closeDialog()
          const now = st()
          clearDraft(now.graphMeta?.id ?? null)
          // Throw the edits away now (back to the saved version, one undo
          // step), so a cancelled or failed follow-up (name dialog, file
          // picker, getGraph) leaves a clean graph, and the draft autosave
          // cannot write the discarded edits back meanwhile.
          const saved = now.savedGraph
          if (saved) now.commit('discard edits', () => saved)
          resolve(true)
        },
        onCancel: () => {
          closeDialog()
          resolve(false)
        },
        onSave: () => {
          closeDialog()
          saveFlow().then(resolve, () => resolve(false))
        },
      })
    })
  }

  const reportError = (prefix: string) => (e: unknown) => {
    pushNotice({ key: 'server_error', severity: 'error', text: `${prefix}${graphErrorDetail(e)}` })
  }

  // ── Actions ─────────────────────────────────────────────────────────────

  const save = () => {
    void saveFlow()
  }

  const saveAs = () => {
    const s = st()
    if (!s.graph || s.graph.readOnly) return
    const current = s.graphMeta?.id ? s.graphMeta.name : null
    void askName(
      { title: 'Save as', primaryLabel: 'Save', initialName: current ? copyName(current) : '' },
      async name => {
        const err = await createFromCurrent(name)
        if (!err && current) pushNotice({ key: 'saved_copy', severity: 'ok', text: `Saved as "${name}".` })
        return err
      },
    )
  }

  /** Rename the open graph on the server. Edits stay unsaved; only the name changes. */
  const renameOpen = async (name: string): Promise<string | null> => {
    const s = st()
    const meta = s.graphMeta
    if (!meta?.id) return createFromCurrent(name)
    const base = s.savedGraph ?? s.graph
    if (!base) return null
    try {
      const env = await saveGraph(meta.id, { rev: meta.rev, name, graph: base })
      const now = st()
      useNodeBuilderStore.setState({ graphMeta: metaOf(env), dirty: now.graph !== now.savedGraph })
      // Keep a draft in step, or the reload prompt would say the server moved on.
      const d = readDraft(meta.id)
      if (d) saveDraft({ ...d, rev: env.rev, name: env.name })
      setLastGraphId(env.id)
      refreshBrowser()
      return null
    } catch (e) {
      if (isNameTaken(e)) return nameTakenText(name)
      if (isRevConflict(e)) return 'The graph changed on the server. Reload and try again.'
      return graphErrorDetail(e)
    }
  }

  const rename = () => {
    const meta = st().graphMeta
    if (!meta?.id) {
      saveAs()
      return
    }
    void askName({ title: 'Rename graph', primaryLabel: 'Rename', initialName: meta.name }, renameOpen)
  }

  const renameItem = (item: GraphListItem) => {
    if (item.id === st().graphMeta?.id) {
      rename()
      return
    }
    void askName({ title: 'Rename graph', primaryLabel: 'Rename', initialName: item.name }, async name => {
      try {
        const env = await getGraph(item.id)
        await saveGraph(item.id, { rev: env.rev, name, graph: env.graph })
        refreshBrowser()
        return null
      } catch (e) {
        if (isNameTaken(e)) return nameTakenText(name)
        return graphErrorDetail(e)
      }
    })
  }

  const newGraph = () => {
    void confirmLeave().then(ok => {
      if (!ok) return
      st().newGraph()
      setLastGraphId(null)
      conflictRef.current = null
      resolveNotice('rev_conflict_pending')
      checkDraft(null, 0)
    })
  }

  /** New from the Graph Browser: a named, empty graph created on the server. */
  const newNamedGraph = () => {
    void confirmLeave().then(ok => {
      if (!ok) return
      void askName({ title: 'New graph', primaryLabel: 'Create', initialName: '' }, async name => {
        try {
          const env = await createGraph({ name, graph: emptyGraph() })
          st().openGraph(editable(env.graph), metaOf(env))
          afterLoad(env)
          setBrowserOpen(false)
          return null
        } catch (e) {
          return isNameTaken(e) ? nameTakenText(name) : graphErrorDetail(e)
        }
      })
    })
  }

  const openItem = async (item: GraphListItem) => {
    if (!(await confirmLeave())) return
    await openGraphById(item.id)
    setBrowserOpen(false)
  }

  /** Duplicate a graph (the saved version) and open the copy. Throws on failure. */
  const duplicateGraph = async (target: { id: string; name: string }) => {
    if (!(await confirmLeave())) return
    let env: GraphEnvelope | null = null
    for (let attempt = 0; attempt < NAME_TRIES && !env; attempt++) {
      try {
        env = await createGraph({ name: copyName(target.name, attempt), duplicate_of: target.id })
      } catch (e) {
        if (!isNameTaken(e)) throw e
      }
    }
    if (!env) throw new Error('No free name for the copy.')
    st().openGraph(editable(env.graph), metaOf(env))
    afterLoad(env)
    refreshBrowser()
    setBrowserOpen(false)
  }

  const duplicate = () => {
    const meta = st().graphMeta
    if (!meta?.id) return
    duplicateGraph({ id: meta.id, name: meta.name }).catch(reportError('Could not duplicate: '))
  }

  const exportJson = () => {
    const s = st()
    if (!s.graph) return
    const meta = s.graphMeta
    const name = meta?.name ?? 'untitled'
    const graph = s.graph
    if (!meta?.id) {
      downloadJson(exportFileName(name), exportPayload(graph, name, null))
      return
    }
    getGraph(meta.id)
      .then(env => downloadJson(exportFileName(name), exportPayload(graph, name, { ...env, name })))
      .catch(() => {
        // Offline: export what we know, so the user still gets their graph.
        downloadJson(exportFileName(name), { id: meta.id, rev: meta.rev, name, graph })
      })
  }

  const importJson = () => {
    void confirmLeave().then(ok => {
      if (ok) fileRef.current?.click()
    })
  }

  const importFile = async (file: File) => {
    let entries: { name: string; graph: Graph }[]
    try {
      entries = parseImport(await file.text(), file.name)
    } catch (e) {
      pushNotice({ key: 'server_error', severity: 'error', text: `Import failed: ${(e as Error).message}` })
      return
    }
    const created: GraphEnvelope[] = []
    for (const entry of entries) {
      let env: GraphEnvelope | null = null
      for (let attempt = 0; attempt < NAME_TRIES && !env; attempt++) {
        try {
          env = await createGraph({ name: importName(entry.name, attempt), graph: entry.graph })
        } catch (e) {
          if (isNameTaken(e)) continue
          const n = errorDiagnostics(e)?.length ?? 0
          const problems = n > 0 ? ` (${n} problem${n === 1 ? '' : 's'})` : ''
          pushNotice({
            key: 'server_error',
            severity: 'error',
            text: `Import failed: ${graphErrorDetail(e)}${problems}`,
          })
          break
        }
      }
      if (env) created.push(env)
    }
    if (created.length === 0) return
    const first = created[0]
    st().openGraph(editable(first.graph), metaOf(first))
    afterLoad(first)
    refreshBrowser()
    setBrowserOpen(false)
    const more = created.length > 1 ? ` and ${created.length - 1} more` : ''
    pushNotice({ key: 'import_ok', severity: 'ok', text: `Imported "${first.name}"${more}.` })
  }

  const deleteCurrent = () => {
    const meta = st().graphMeta
    if (!meta?.id) return
    const { id, rev, name } = meta
    setDialog({
      kind: 'delete',
      name,
      onCancel: closeDialog,
      onDelete: () => {
        closeDialog()
        deleteGraph(id, rev)
          .then(() => {
            clearDraft(id)
            setLastGraphId(null)
            if (st().graphMeta?.id === id) st().discardEdits()
            refreshBrowser()
          })
          .catch(e => {
            pushNotice({
              key: 'server_error',
              severity: 'error',
              text: isRevConflict(e)
                ? 'Could not delete: the graph changed on the server. Reload and try again.'
                : `Could not delete: ${graphErrorDetail(e)}`,
            })
          })
      },
    })
  }

  const onBrowserDeleted = (item: GraphListItem) => {
    clearDraft(item.id)
    if (getLastGraphId() === item.id) setLastGraphId(null)
    if (st().graphMeta?.id === item.id) st().discardEdits()
  }

  const closeGraph = () => {
    void confirmLeave().then(ok => {
      if (!ok) return
      st().discardEdits()
      setLastGraphId(null)
      conflictRef.current = null
      resolveNotice('rev_conflict_pending')
    })
  }

  // ── Conflict dialog (S04) ───────────────────────────────────────────────

  const conflictCancel = () => {
    closeDialog()
    pushNotice({
      key: 'rev_conflict_pending',
      severity: 'warn',
      text: `This graph has a save conflict. Save (${modKeyCap('S')}) to resolve it.`,
      actions: [{ label: 'Resolve', run: openConflict }],
    })
  }

  const conflictReload = (env: GraphEnvelope) => {
    const s = st()
    if (s.graphMeta?.id !== env.id) return
    // One history step, so Cmd+Z brings the local version back.
    s.commit('reload from server', () => editable(env.graph))
    st().markSaved(metaOf(env))
    clearDraft(env.id)
    conflictRef.current = null
    resolveNotice('rev_conflict_pending')
    closeDialog()
  }

  const conflictOverwrite = async (rev: number) => {
    const s = st()
    const meta = s.graphMeta
    if (!meta?.id || !s.graph) return
    const sent = s.graph
    const env = await saveGraph(meta.id, { rev, graph: sent })
    finishSave(metaOf(env), sent)
    keepDraftInStep(meta.id, sent)
    conflictRef.current = null
    resolveNotice('rev_conflict_pending')
    refreshBrowser()
    closeDialog()
  }

  const conflictSaveAsCopy = () => {
    const meta = st().graphMeta
    const name = meta?.name ?? 'untitled'
    void askName({ title: 'Save as', primaryLabel: 'Save', initialName: copyName(name) }, async newName => {
      const err = await createFromCurrent(newName)
      if (!err) pushNotice({ key: 'saved_copy', severity: 'ok', text: `Saved as "${newName}".` })
      return err
    }).then(saved => {
      // Cancelled: the conflict is still there.
      if (!saved && conflictRef.current) conflictCancel()
    })
  }

  // ── Render ──────────────────────────────────────────────────────────────

  let dialogEl: ReactNode = null
  if (dialog?.kind === 'name') {
    dialogEl = (
      <NameDialog
        key={dialog.title + dialog.initialName}
        title={dialog.title}
        primaryLabel={dialog.primaryLabel}
        initialName={dialog.initialName}
        initialError={dialog.initialError}
        onSubmit={dialog.onSubmit}
        onCancel={dialog.onCancel}
      />
    )
  } else if (dialog?.kind === 'saveChanges') {
    dialogEl = (
      <SaveChangesDialog name={dialog.name} onSave={dialog.onSave} onDiscard={dialog.onDiscard} onCancel={dialog.onCancel} />
    )
  } else if (dialog?.kind === 'delete') {
    dialogEl = <DeleteGraphDialog name={dialog.name} onDelete={dialog.onDelete} onCancel={dialog.onCancel} />
  } else if (dialog?.kind === 'conflict') {
    const graph = st().graph
    if (graph) {
      dialogEl = (
        <ConflictDialog
          graphId={dialog.graphId}
          name={dialog.name}
          myRev={dialog.myRev}
          currentRev={dialog.currentRev}
          localGraph={graph}
          onSaveAsCopy={conflictSaveAsCopy}
          onReloadTheirs={conflictReload}
          onOverwrite={conflictOverwrite}
          onCancel={conflictCancel}
        />
      )
    }
  }

  const element = (
    <>
      {browserOpen && (
        <GraphBrowser
          openGraphId={st().graphMeta?.id ?? null}
          refreshKey={browserRefresh}
          onClose={() => setBrowserOpen(false)}
          onOpen={openItem}
          onDuplicate={item => duplicateGraph({ id: item.id, name: item.name })}
          onRename={renameItem}
          onNew={newNamedGraph}
          onImport={importJson}
          onDeleted={onBrowserDeleted}
        />
      )}
      {dialogEl}
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        style={{ display: 'none' }}
        data-testid="nb-import-input"
        onChange={e => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (file) void importFile(file)
        }}
      />
    </>
  )

  return {
    busy,
    save,
    saveAs,
    rename,
    renameInline: renameOpen,
    newGraph,
    openBrowser: () => setBrowserOpen(true),
    duplicate,
    exportJson,
    importJson,
    deleteCurrent,
    closeGraph,
    openLastGraph,
    element,
  }
}
