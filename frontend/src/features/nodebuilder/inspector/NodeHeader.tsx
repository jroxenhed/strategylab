/**
 * The Inspector header for one node (S14): glyph chip, name (click, F2 or
 * Enter to rename), type, path, and the display and bypass flag dots.
 *
 * Rename goes through `commitRename` (operations/rename.ts), one undo step
 * labelled `rename node`; the path line follows on the same render. A name
 * that is not allowed shows the helper text and is not committed.
 *
 * The flag dots call `clickDisplayFlag` / `clickBypassFlag` (commands/flags.ts,
 * S16) on this node, the same rule the D and B keys use (one display node
 * per network, no bypass on Tickers or terminals). A dot is hidden when
 * `flagProblemForType` rules the flag out; the bypass dot stays while a
 * stray bypass flag is set, so it can be turned off.
 */

import { useContext, useEffect, useRef, useState } from 'react'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import { clickBypassFlag, clickDisplayFlag } from '../commands/flags'
import { flagProblemForType } from '../operations'
import { commitRename, renameProblem } from '../operations/rename'
import { nodePath } from '../paths'
import { useNodeBuilderStore } from '../store'
import { useInspectorUi } from './state'
import { catVars, categoryOf, focusCanvas, glyphOf, inspectorGraphNow, InspectorSourceContext, useInspectorSelect } from './util'

function safePath(graph: Graph, nodeId: string): string {
  try {
    return nodePath(graph, nodeId)
  } catch {
    return ''
  }
}

export function NodeHeader({
  nodeId,
  node,
  editable,
}: {
  nodeId: string
  node: GraphNode
  /** False for a read-only graph or an unsupported node (S13): no rename, no flags. */
  editable: boolean
}) {
  const cat = categoryOf(node.type)
  // The path changes with this node's or a parent's name only.
  const path = useInspectorSelect(g => (g ? safePath(g, nodeId) : ''))
  const source = useContext(InspectorSourceContext)
  // Sibling names matter only while editing; read the graph then.
  const nameProblem = (name: string): string | null => {
    const graph = inspectorGraphNow(source)
    return graph ? renameProblem(graph, nodeId, name) : null
  }
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(node.name)
  const inputRef = useRef<HTMLInputElement>(null)
  // Set when Enter or Esc already finished the edit, so the blur after it does nothing.
  const doneRef = useRef(false)

  const start = () => {
    if (!editable) return
    doneRef.current = false
    setDraft(node.name)
    setEditing(true)
  }

  // F2 (edit.rename) asks for a rename of this node. The request is used
  // up here, so it does not fire again when this header mounts later.
  const renameRequest = useInspectorUi(s => s.renameRequest)
  useEffect(() => {
    if (!renameRequest) return
    useInspectorUi.setState({ renameRequest: null })
    if (renameRequest.nodeId === nodeId) start()
    // start only reads props; running it again on their change is not wanted
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renameRequest, nodeId])

  // Selecting another node ends an edit in progress without committing.
  const shownId = useRef(nodeId)
  useEffect(() => {
    if (shownId.current === nodeId) return
    shownId.current = nodeId
    setEditing(false)
  }, [nodeId])

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  const problem = editing ? nameProblem(draft) : null

  const finish = (commit: boolean) => {
    if (commit) {
      // A bad name stays in the field with the helper under it.
      if (nameProblem(draft) !== null) return
      commitRename(useNodeBuilderStore, nodeId, draft)
    }
    doneRef.current = true
    setEditing(false)
    focusCanvas()
  }

  const canDisplay = flagProblemForType(node.type, 'display') === null
  const canBypass = node.bypass || flagProblemForType(node.type, 'bypass') === null
  const invalid = problem !== null

  return (
    <div className="nb-insp-head" style={catVars(cat)}>
      <span className="nb-insp-head__glyph" aria-hidden="true">{glyphOf(cat)}</span>
      <div className="nb-insp-head__text">
        <div className="nb-insp-head__line1">
          {editing ? (
            <input
              ref={inputRef}
              className={`nb-insp-head__input${invalid ? ' nb-insp-head__input--invalid' : ''}`}
              value={draft}
              data-testid="nb-inspector-name-input"
              aria-label="Node name"
              aria-invalid={invalid ? true : undefined}
              aria-describedby={invalid ? 'nb-inspector-name-help' : undefined}
              spellCheck={false}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); finish(true) }
                else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false) }
              }}
              onBlur={() => {
                if (doneRef.current) return
                // Blur keeps a good name and drops a bad one.
                if (nameProblem(draft) === null) commitRename(useNodeBuilderStore, nodeId, draft)
                doneRef.current = true
                setEditing(false)
              }}
            />
          ) : (
            <button
              type="button"
              className="nb-insp-head__name"
              data-testid="nb-inspector-name"
              title={editable ? 'Rename (F2)' : undefined}
              disabled={!editable}
              onClick={start}
              onKeyDown={e => { if (e.key === 'F2') { e.preventDefault(); start() } }}
            >
              {node.name}
            </button>
          )}
          <span className="nb-insp-head__type">{node.type}</span>
        </div>
        <div className="nb-insp-head__path" data-testid="nb-inspector-path">{path}</div>
        {invalid && (
          <div id="nb-inspector-name-help" className="nb-insp-head__help" data-testid="nb-inspector-name-help">
            {problem}
          </div>
        )}
      </div>
      {editable && (canDisplay || canBypass) && (
        <div className="nb-insp-head__flags">
          {canDisplay && (
            <button
              type="button"
              className="nb-insp-flag nb-insp-flag--display"
              data-testid="nb-inspector-flag-display"
              aria-pressed={node.display}
              aria-label="Display flag"
              title={node.display ? 'Display (D) · already shown' : 'Display (D)'}
              onClick={() => { clickDisplayFlag(nodeId) }}
            />
          )}
          {canBypass && (
            <button
              type="button"
              className="nb-insp-flag nb-insp-flag--bypass"
              data-testid="nb-inspector-flag-bypass"
              aria-pressed={node.bypass}
              aria-label="Bypass flag"
              title="Bypass (B)"
              onClick={() => { clickBypassFlag(nodeId) }}
            />
          )}
        </div>
      )}
    </div>
  )
}
