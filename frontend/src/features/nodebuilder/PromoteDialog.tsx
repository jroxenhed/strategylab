/**
 * The Promote popover (spec S40): right-click a param inside a network and
 * choose "Promote to parent…". It asks for the promoted param's label and
 * name, then commits `promoteParam` as one undo step. Mounted in the
 * `dialogs` slot by plugins/assets.ts; opened through assetUi.openPromote.
 */

import { useMemo, useState } from 'react'
import { closePromote, useAssetUi } from './assetUi'
import { useNodeBuilderStore } from './store'
import { Dialog } from './ui/Dialog'
import {
  childParamSpec,
  defaultPromotedName,
  promoteHint,
  promoteParam,
  promoteTitle,
  promotedNameProblem,
} from './operations/promote'
import './assets.css'

export function PromoteDialogHost() {
  const req = useAssetUi(s => s.promote)
  if (!req) return null
  return <PromoteDialog key={`${req.nodeId}/${req.param}`} nodeId={req.nodeId} param={req.param} />
}

function PromoteDialog({ nodeId, param }: { nodeId: string; param: string }) {
  const graph = useNodeBuilderStore(s => s.graph)
  const node = graph?.nodes[nodeId]
  const net = node?.parent ? graph?.nodes[node.parent] : undefined
  const spec = useMemo(() => (graph ? childParamSpec(graph, nodeId, param) : null), [graph, nodeId, param])
  const [label, setLabel] = useState(() => spec?.label || param)
  const [name, setName] = useState(() => (graph ? defaultPromotedName(graph, nodeId, param) : param))
  const [error, setError] = useState<string | null>(null)

  if (!graph || !node || !net) return null
  const problem = promotedNameProblem(net, name)

  const promote = () => {
    if (problem) return
    try {
      useNodeBuilderStore.getState().commit('promote param', g => promoteParam(g, nodeId, param, { name, label }))
      closePromote()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Dialog
      title={promoteTitle(param, net.name)}
      onCancel={closePromote}
      width={260}
      primaryLabel="Promote"
      onPrimary={promote}
      primaryDisabled={!!problem}
      primaryDisabledReason={problem ?? undefined}
      data-testid="nb-promote-dialog"
    >
      <div className="nb-asset-field">
        <label htmlFor="nb-promote-label">Label</label>
        <input
          id="nb-promote-label"
          className="nb-asset-input"
          value={label}
          onChange={e => setLabel(e.target.value)}
        />
      </div>
      <div className="nb-asset-field">
        <label htmlFor="nb-promote-name">Name</label>
        <input
          id="nb-promote-name"
          className="nb-asset-input nb-asset-input--mono"
          value={name}
          spellCheck={false}
          aria-invalid={problem ? true : undefined}
          aria-describedby="nb-promote-hint"
          onChange={e => setName(e.target.value)}
        />
        <span id="nb-promote-hint" className={problem ? 'nb-asset-line nb-asset-line--error' : 'nb-asset-help'}>
          {problem ?? promoteHint(name)}
        </span>
      </div>
      {error && <div className="nb-asset-bar nb-asset-bar--error" role="alert">{error}</div>}
    </Dialog>
  )
}
