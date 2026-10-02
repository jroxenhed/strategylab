/**
 * Save as asset (spec S41) and "Promote to palette" (the same dialog with
 * the palette section on).
 *
 * Saves a subnet to the library as a new asset version: its contents
 * (positions relative to the subnet), its promoted params as stored, the
 * interface it reads and writes, and an optional Tab-menu entry under
 * Rules. With "Replace this node with a locked instance" on, the subnet
 * then becomes a locked instance of the saved version (its children leave
 * the graph; they come from the library) in one undo step.
 *
 * Mounted in the `dialogs` slot by plugins/assets.ts; opened through
 * assetUi.openSaveAsset.
 */

import { useEffect, useMemo, useState } from 'react'
import {
  ASSET_NAME_RE,
  assetErrorText,
  getAsset,
  saveAsset,
  type AssetFile,
} from '../../api/graphLibrary'
import type { Graph, GraphNode } from '../../api/nodebuilder'
import {
  closeSaveAsset,
  ensureLibrary,
  openAssetManager,
  refreshLibrary,
  showAssetToast,
  useAssetLibrary,
  useAssetUi,
} from './assetUi'
import { toggleInspector } from './inspector/state'
import {
  buildSaveBody,
  clampGlyph,
  fixErrorsText,
  SAVE_ASSET_TEXT,
  savedToastText,
  savesAsVersionText,
} from './assetText'
import { descendantsOf } from './networkOps'
import {
  assetNetworkOf,
  assetPromotedOf,
  defaultPaletteLabel,
  deriveInterface,
  replaceWithLockedInstance,
} from './operations/assets'
import { useNodeBuilderStore } from './store'
import { Dialog } from './ui/Dialog'
import { getStreams, useDiagnostics } from './useDiagnostics'
import './assets.css'

export function SaveAssetDialogHost() {
  const req = useAssetUi(s => s.saveAsset)
  if (!req) return null
  return <SaveAssetDialog key={req.subnetId} subnetId={req.subnetId} paletteOn={req.palette} />
}

function hasErrorsInside(graph: Graph, subnetId: string, byNode: Record<string, { severity: string }[]>): boolean {
  const ids = [subnetId, ...descendantsOf(graph.nodes, subnetId)]
  return ids.some(id => (byNode[id] ?? []).some(d => d.severity === 'error'))
}

function firstLine(text: unknown): string {
  return typeof text === 'string' ? text.split('\n')[0].slice(0, 200) : ''
}

function SaveAssetDialog({ subnetId, paletteOn }: { subnetId: string; paletteOn: boolean }) {
  const graph = useNodeBuilderStore(s => s.graph)
  const node: GraphNode | undefined = graph?.nodes[subnetId]
  const locked = !!node?.locked && !!node?.asset_ref
  const ref = node?.asset_ref ?? null

  const [name, setName] = useState(() => (paletteOn && ref ? ref.name : ref?.name ?? node?.name ?? ''))
  const [description, setDescription] = useState(() => firstLine(node?.meta?.note))
  const [palette, setPalette] = useState(paletteOn)
  const [label, setLabel] = useState(() => defaultPaletteLabel(ref?.name ?? node?.name ?? ''))
  const [glyph, setGlyph] = useState('R')
  const [replace, setReplace] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A locked instance's contents come from its asset file.
  const [source, setSource] = useState<AssetFile | null>(null)

  const library = useAssetLibrary(s => s.items)
  const libraryStatus = useAssetLibrary(s => s.status)
  const { byNode } = useDiagnostics()

  useEffect(() => { void ensureLibrary() }, [])
  useEffect(() => {
    if (!locked || !ref) return
    let live = true
    getAsset(ref.name, ref.version)
      .then(file => { if (live) setSource(file) })
      .catch(e => { if (live) setError(assetErrorText(e)) })
    return () => { live = false }
  }, [locked, ref])

  const content = useMemo(() => {
    if (!graph || !node) return null
    if (locked) {
      if (!source) return null
      return { network: source.network, iface: source.interface, promoted: source.promoted }
    }
    return {
      network: assetNetworkOf(graph, subnetId),
      iface: deriveInterface(graph, subnetId, getStreams()),
      promoted: assetPromotedOf(node),
    }
  }, [graph, node, locked, source, subnetId])

  if (!graph || !node) return null

  const existing = library.find(a => a.name === name)
  const nextVersion = existing ? existing.latest + 1 : 1
  const nameValid = ASSET_NAME_RE.test(name)
  const noOutput = !locked && !descendantsOf(graph.nodes, subnetId).some(id => graph.nodes[id]?.type === 'subnet_output')
  const errorsInside = !locked && hasErrorsInside(graph, subnetId, byNode)
  const disabledReason = !nameValid
    ? SAVE_ASSET_TEXT.nameInvalid
    : errorsInside
      ? fixErrorsText(node.name)
      : !content
        ? 'Loading the asset…'
        : null

  let line: { text: string; kind: 'muted' | 'exists' | 'error' }
  if (!nameValid) line = { text: SAVE_ASSET_TEXT.nameInvalid, kind: 'error' }
  else if (libraryStatus === 'error') line = { text: SAVE_ASSET_TEXT.libraryUnreachable, kind: 'muted' }
  else if (existing) line = { text: savesAsVersionText(nextVersion, name), kind: 'exists' }
  else line = { text: SAVE_ASSET_TEXT.newAsset, kind: 'muted' }

  const save = async () => {
    if (disabledReason || !content || saving) return
    setSaving(true)
    setError(null)
    try {
      const body = buildSaveBody({
        name,
        description,
        network: content.network,
        promoted: content.promoted,
        iface: content.iface,
        palette: palette ? { label: label.trim() || defaultPaletteLabel(name), glyph: glyph || 'R' } : null,
      })
      const saved = await saveAsset(body)
      const version = saved?.version ?? nextVersion
      if (replace) {
        const s = useNodeBuilderStore.getState()
        if (s.graph?.nodes[subnetId]) {
          s.commit('save as asset', g => replaceWithLockedInstance(g, subnetId, { name, version }))
        }
      }
      closeSaveAsset()
      showAssetToast(savedToastText(name, version), { label: 'Open Asset Manager', run: () => openAssetManager({ select: name }) })
      void refreshLibrary()
    } catch (e) {
      setError(assetErrorText(e))
    } finally {
      setSaving(false)
    }
  }

  const primaryLabel = saving ? 'Saving…' : existing ? `Save version ${nextVersion}` : 'Save asset'

  return (
    <Dialog
      title={`Save ${node.name} as an asset`}
      onCancel={closeSaveAsset}
      width={560}
      primaryLabel={primaryLabel}
      onPrimary={() => { void save() }}
      primaryDisabled={!!disabledReason || saving}
      primaryDisabledReason={disabledReason ?? undefined}
      submitKey="mod-enter"
      data-testid="nb-save-asset-dialog"
    >
      {locked && ref && (
        <div className="nb-asset-line" style={{ marginBottom: 10 }}>
          {`Saving creates version ${nextVersion}. Existing graphs keep v${ref.version}.`}
        </div>
      )}
      {noOutput && <div className="nb-asset-bar nb-asset-bar--warn">{SAVE_ASSET_TEXT.noOutput}</div>}
      {errorsInside && (
        <div className="nb-asset-bar nb-asset-bar--error">
          <span>{fixErrorsText(node.name)}</span>
          <button
            type="button"
            className="nb-promoted-link"
            onClick={() => {
              closeSaveAsset()
              useNodeBuilderStore.getState().setSelection({ nodeIds: [subnetId], primary: subnetId })
              toggleInspector(true)
            }}
          >
            Show diagnostics
          </button>
        </div>
      )}

      <div className="nb-asset-field">
        <label htmlFor="nb-save-asset-name">Name</label>
        <input
          id="nb-save-asset-name"
          className="nb-asset-input nb-asset-input--mono"
          value={name}
          spellCheck={false}
          aria-invalid={!nameValid ? true : undefined}
          aria-describedby="nb-save-asset-line"
          onChange={e => setName(e.target.value)}
        />
        <span
          id="nb-save-asset-line"
          data-testid="nb-save-asset-line"
          className={`nb-asset-line${line.kind === 'exists' ? ' nb-asset-line--exists' : line.kind === 'error' ? ' nb-asset-line--error' : ''}`}
        >
          {line.text}
        </span>
      </div>

      <div className="nb-asset-field">
        <label htmlFor="nb-save-asset-desc">Description</label>
        <input
          id="nb-save-asset-desc"
          className="nb-asset-input"
          value={description}
          maxLength={200}
          onChange={e => setDescription(e.target.value)}
        />
      </div>

      <div className="nb-asset-field">
        <span className="nb-asset-section__title">Interface</span>
        <InterfaceChips iface={content?.iface ?? null} />
      </div>

      <div className="nb-asset-field">
        <span className="nb-asset-section__title">Promoted parameters</span>
        <PromotedTable list={content?.promoted ?? []} />
      </div>

      <div className="nb-asset-field">
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={palette} onChange={e => setPalette(e.target.checked)} />
          {SAVE_ASSET_TEXT.paletteToggle}
        </label>
        {palette && (
          <>
            <div style={{ display: 'flex', gap: 8 }}>
              <div className="nb-asset-field" style={{ flex: 1 }}>
                <label htmlFor="nb-save-asset-label">Label</label>
                <input id="nb-save-asset-label" className="nb-asset-input" value={label} onChange={e => setLabel(e.target.value)} />
              </div>
              <div className="nb-asset-field" style={{ width: 80 }}>
                <label htmlFor="nb-save-asset-glyph">Glyph</label>
                <input
                  id="nb-save-asset-glyph"
                  data-testid="nb-save-asset-glyph"
                  className="nb-asset-input nb-asset-input--mono"
                  value={glyph}
                  onChange={e => setGlyph(clampGlyph(e.target.value))}
                />
              </div>
            </div>
            <div className="nb-asset-preview-row" aria-hidden="true">
              <span className="nb-asset-glyph nb-asset-glyph--rules">{glyph || 'R'}</span>
              <span>{label || defaultPaletteLabel(name)}</span>
              <span className="nb-asset-line" style={{ flex: 1 }}>{description}</span>
              <span className="nb-asset-tag">RULES</span>
            </div>
          </>
        )}
      </div>

      <div className="nb-asset-field">
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} />
          {SAVE_ASSET_TEXT.replace}
        </label>
        <span className="nb-asset-help">{SAVE_ASSET_TEXT.replaceHelp}</span>
      </div>

      {error && <div className="nb-asset-bar nb-asset-bar--error" role="alert">{error}</div>}
    </Dialog>
  )
}

/** The interface as two chip rows (reads, then writes) with dtype tags. */
export function InterfaceChips({ iface, match }: { iface: AssetFile['interface'] | null; match?: string | null }) {
  if (!iface) return <span className="nb-asset-help">…</span>
  const chip = (a: { name: string; dtype: string }, kind: 'read' | 'write') => (
    <li
      key={`${kind}${a.name}`}
      className={`nb-asset-chip${kind === 'write' ? ' nb-asset-chip--write' : ''}${match && a.name === match ? ' nb-asset-chip--match' : ''}`}
    >
      {a.name}
      <span className="nb-asset-chip__dtype">{a.dtype}</span>
    </li>
  )
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span className="nb-asset-help" style={{ width: 40 }}>reads</span>
        <ul className="nb-asset-chips" aria-label="Attributes this asset reads">
          {iface.reads.length ? iface.reads.map(a => chip(a, 'read')) : <li className="nb-asset-help">none</li>}
        </ul>
      </div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span className="nb-asset-help" style={{ width: 40 }}>writes</span>
        <ul className="nb-asset-chips" aria-label="Attributes this asset writes">
          {iface.writes.length ? iface.writes.map(a => chip(a, 'write')) : <li className="nb-asset-help">none</li>}
        </ul>
      </div>
    </div>
  )
}

/** The promoted params as a read-only table (label, name, type, default). */
export function PromotedTable({ list }: { list: readonly { label: string; name: string; type: string; default: unknown }[] }) {
  if (list.length === 0) return <span className="nb-asset-help">none</span>
  return (
    <table className="nb-asset-table">
      <thead>
        <tr><th>Label</th><th>Name</th><th>Type</th><th>Default</th></tr>
      </thead>
      <tbody>
        {list.map(p => (
          <tr key={p.name}>
            <td style={{ fontFamily: 'var(--nb-font-sans)' }}>{p.label}</td>
            <td>{p.name}</td>
            <td>{p.type}</td>
            <td>{p.default === null || p.default === undefined ? '' : String(p.default)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
