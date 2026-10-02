/**
 * The Inspector rows for an asset instance's lifecycle (S38, UX-04):
 * - a locked instance with a newer version: `v4 available · Update`;
 * - an unlocked copy: `Re-lock to v3`, enabled only while the copy still
 *   equals that version (else disabled, saying why);
 * - a deleted version (`asset_missing`): the S38 sentence.
 * The buttons show only on an editable graph.
 */
import { useEffect, useState } from 'react'
import type { GraphNode } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { cachedAsset, getAsset, type AssetFile } from '../../../api/graphLibrary'
import { useAssetLibrary } from '../assetUi'
import { LIFECYCLE_TEXT, newerVersionOf, relockAssetInstance, relockProblem, updateAssetInstance } from '../assetLifecycle'
import { Button } from '../ui/Button'
import { useInspectorSelect } from './util'

export function AssetLifecycleRows({
  nodeId,
  node,
  editable,
  diagnostics,
}: {
  nodeId: string
  node: GraphNode
  editable: boolean
  diagnostics: readonly Diagnostic[]
}) {
  const ref = node.asset_ref ?? null
  const newer = useAssetLibrary(s => newerVersionOf(node, s.items))
  const missing = diagnostics.some(d => d.code === 'asset_missing')
  const unlocked = !!ref && node.locked !== true

  // An unlocked copy needs its version's file to tell whether it changed.
  const [file, setFile] = useState<AssetFile | null>(() => (ref ? cachedAsset(ref.name, ref.version) : null))
  const fileKey = ref ? `${ref.name}@${ref.version}` : null
  useEffect(() => {
    if (!unlocked || !ref) return
    let live = true
    getAsset(ref.name, ref.version).then(f => { if (live) setFile(f) }, () => { if (live) setFile(null) })
    return () => { live = false }
    // ref is read through fileKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unlocked, fileKey])
  const shownFile = file && ref && file.name === ref.name && file.version === ref.version ? file : null
  const relockWhy = useInspectorSelect(g => (unlocked && editable ? relockProblem(g, nodeId, shownFile) : null))

  if (!ref) return null
  const showUpdate = node.locked === true && newer !== null
  if (!missing && !showUpdate && !(unlocked && editable)) return null
  return (
    <div className="nb-insp-desc" data-testid="nb-inspector-asset-lifecycle">
      {missing && (
        <div style={{ color: 'var(--nb-error)' }} data-testid="nb-inspector-asset-missing">{LIFECYCLE_TEXT.missing}</div>
      )}
      {showUpdate && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} data-testid="nb-inspector-asset-newer">
          <span>{LIFECYCLE_TEXT.available(newer!)}</span>
          {editable && (
            <>
              <span aria-hidden="true">·</span>
              <Button kind="text" onClick={() => { void updateAssetInstance(nodeId, newer!) }} data-testid="nb-inspector-asset-update">
                {LIFECYCLE_TEXT.update}
              </Button>
            </>
          )}
        </div>
      )}
      {unlocked && editable && (
        <Button
          onClick={() => { void relockAssetInstance(nodeId) }}
          disabled={relockWhy !== null}
          disabledReason={relockWhy ?? undefined}
          data-testid="nb-inspector-asset-relock"
        >
          {LIFECYCLE_TEXT.relockTo(ref.version)}
        </Button>
      )}
    </div>
  )
}
