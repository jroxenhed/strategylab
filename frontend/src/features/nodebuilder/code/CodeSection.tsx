/**
 * The Inspector's Code section (F435 W7, specs S45, S46, S48, S49).
 *
 * Registered twice in the section registry: `code` (order 20, under
 * Parameters) for built-in nodes, and `code-wrangle` (order 5, above
 * Parameters, open by default at 240px) for a Wrangle, whose code is the
 * node. Open state: `code` while the node has code, `code.off` while it has
 * none (collapsed by default, S45), `code.wrangle` for a Wrangle.
 *
 * Inside: the spare params (S48; shown here while the section is open, in
 * Parameters otherwise), the Monaco block editor (S47), the diagnostics
 * list, and the footer `Checked 0.3 s ago · 4 of 8 KB`.
 *
 * Edits commit to `Node.code` on blur, on Cmd+Enter, and 800 ms after the
 * last key, as one coalesced undo step per burst. `parse_code` runs 400 ms
 * after the last key and on commit; a good answer writes the spare params.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { GraphNode } from '../../../api/nodebuilder'
import { diagnosticText } from '../../../api/nodebuilderCode'
import { useNodeBuilderStore } from '../store'
import { useStreams } from '../useDiagnostics'
import { registerInspectorSection, type InspectorSectionProps } from '../inspector/sections'
import { ActionMenu } from '../ui/ActionMenu'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'
import { byteLength, NODE_CODE_PLACEHOLDER, setNodeCode, WRANGLE_TYPE } from './codeOps'
import { canHaveCode, checkedAgoText, codeSectionKey, codeTagText, formatCode, sizeText } from './codeUi'
import {
  CODE_PARSE_MS,
  CODE_SLOT,
  runParse,
  scheduleParse,
  setLiveDraft,
  useCodeDiagnostics,
  useCodeEnabled,
  useCodeStore,
  useParse,
  useParsePending,
} from './codeStore'
import { attrsForNode, chPathItems } from './completion'
import MonacoEditor, { type EditorLoadState, type MonacoEditorHandle } from './MonacoEditor'
import type { ModelContext } from './monacoProviders'
import { SpareParamRows } from './SpareParams'
import { useSpareSpecs } from './codeUi'
import './code.css'

/** Commit a typing burst this long after the last key (S45). */
export const CODE_COMMIT_MS = 800
/** The stored editor height (S45). */
export const CODE_HEIGHT_KEY = 'nb.inspector.code.h'
const MIN_H = 96
const MAX_H = 320
const WRANGLE_H = 240

function readHeight(): number | null {
  try {
    const v = Number(localStorage.getItem(CODE_HEIGHT_KEY))
    return Number.isFinite(v) && v >= MIN_H ? Math.min(v, 800) : null
  } catch {
    return null
  }
}

/** Height that fits the code, between 96 and 320 px (18px lines). */
function fitHeight(code: string): number {
  const lines = code.split('\n').length
  return Math.max(MIN_H, Math.min(MAX_H, lines * 18 + 12))
}

function contextOf(node: GraphNode): 'wrangle' | 'node_code' {
  return node.type === WRANGLE_TYPE ? 'wrangle' : 'node_code'
}

export function CodeCount({ nodeId, node }: InspectorSectionProps) {
  const parse = useParse(nodeId, CODE_SLOT)
  const pending = useParsePending(nodeId, CODE_SLOT)
  const code = node.code ?? ''
  const diags = useCodeDiagnostics(nodeId, CODE_SLOT, parse && parse.code === code ? parse.res.diagnostics : null, true)
  const tag = codeTagText(
    code,
    diags,
    parse?.res.reads.map(r => r.name) ?? [],
    parse?.res.writes.map(w => w.name) ?? [],
  )
  return (
    <span className={`nb-code-tag nb-code-tag--${tag.kind}`} data-testid="nb-code-tag">
      {tag.text}
      {pending && <span className="nb-code-spinner" aria-hidden="true" />}
    </span>
  )
}

export function CodeSection({ nodeId, node, editable }: InspectorSectionProps) {
  const commitGraph = useNodeBuilderStore(s => s.commit)
  const codeOn = useCodeEnabled()
  const caps = useCodeStore(s => s.caps)
  const parse = useParse(nodeId, CODE_SLOT)
  const streams = useStreams()
  const spares = useSpareSpecs(nodeId, node)
  const wrangle = node.type === WRANGLE_TYPE
  const context = contextOf(node)
  const stored = node.code ?? ''
  const [draft, setDraft] = useState(stored)
  const typingRef = useRef(false)
  const [typing, setTyping] = useState(false)
  const commitTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const editorRef = useRef<MonacoEditorHandle>(null)
  const [loadState, setLoadState] = useState<EditorLoadState>('loading')
  const [height, setHeight] = useState<number>(() => readHeight() ?? (wrangle ? WRANGLE_H : fitHeight(stored)))
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const [refAnchor, setRefAnchor] = useState<HTMLElement | null>(null)
  const [refQuery, setRefQuery] = useState('')
  const [confirmClear, setConfirmClear] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const readOnly = !editable || !codeOn

  // Follow the store (undo, another view) while the user is not typing:
  // adjusted during render (React's pattern for state that follows a prop).
  const [prevStored, setPrevStored] = useState(stored)
  if (stored !== prevStored) {
    setPrevStored(stored)
    if (!typing) setDraft(stored)
  }

  // Code that has no parse yet (a graph just opened) is checked once.
  useEffect(() => {
    if (!stored || !codeOn) return
    const st = useCodeStore.getState()
    const key = `${nodeId}|${CODE_SLOT}`
    if (st.parses[key] || st.pending[key]) return
    void runParse({ nodeId, slot: CODE_SLOT, code: stored, context, expected: null, applySpares: editable })
  }, [nodeId, stored, context, codeOn, editable])

  // The footer's "Checked 0.3 s ago" ticks while the section is open.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // A click on the node's code block asks for the editor (S45).
  const focusReq = useCodeStore(s => (s.focusRequest?.nodeId === nodeId ? s.focusRequest : null))
  useEffect(() => {
    if (!focusReq) return
    const t = setTimeout(() => editorRef.current?.focus(focusReq.line, 0), 0)
    return () => clearTimeout(t)
  }, [focusReq])

  const commit = useCallback((text: string) => {
    if (commitTimer.current) { clearTimeout(commitTimer.current); commitTimer.current = null }
    typingRef.current = false
    setTyping(false)
    setLiveDraft(nodeId, CODE_SLOT, null)
    const g = useNodeBuilderStore.getState().graph
    if (!g?.nodes[nodeId]) return
    const current = g.nodes[nodeId].code ?? ''
    if (text === current) return
    commitGraph('edit code', gr => setNodeCode(gr, nodeId, text), { coalesce: `code:${nodeId}` })
    void runParse({ nodeId, slot: CODE_SLOT, code: text, context, expected: null, applySpares: true })
  }, [commitGraph, nodeId, context])

  // Leaving the section (another node selected, the Inspector closed) with
  // a typing burst not yet committed: commit it now rather than drop it.
  const unmountFlush = useRef<() => void>(() => {})
  useEffect(() => {
    unmountFlush.current = () => {
      if (commitTimer.current) {
        clearTimeout(commitTimer.current)
        // Only onto the code this burst started from (not a graph loaded since).
        const cur = useNodeBuilderStore.getState().graph?.nodes[nodeId]?.code ?? ''
        if (cur === stored) commit(draft)
        commitTimer.current = null
      }
      setLiveDraft(nodeId, CODE_SLOT, null)
    }
  })
  useEffect(() => () => unmountFlush.current(), [])

  const change = (text: string) => {
    typingRef.current = true
    setTyping(true)
    setDraft(text)
    setLiveDraft(nodeId, CODE_SLOT, text)
    scheduleParse({ nodeId, slot: CODE_SLOT, code: text, context, expected: null, applySpares: true }, CODE_PARSE_MS)
    if (commitTimer.current) clearTimeout(commitTimer.current)
    commitTimer.current = setTimeout(() => {
      commitTimer.current = null
      commit(text)
    }, CODE_COMMIT_MS)
  }

  const completion = useMemo<ModelContext>(() => ({
    attrs: () => attrsForNode(useNodeBuilderStore.getState().graph, nodeId, streams),
    detailOnly: false,
    paths: () => chPathItems(useNodeBuilderStore.getState().graph, nodeId, streams),
    functions: () => useCodeStore.getState().caps?.functions ?? [],
  }), [nodeId, streams])

  // Spare params sit here while the section is open (S48: one place only).
  // The server's problems (attr_missing, ch_cycle, cook errors) join while
  // the editor holds the committed code they were found in (UX-4).
  const diagnostics = useCodeDiagnostics(nodeId, CODE_SLOT, parse?.res.diagnostics ?? null, draft === stored) ?? []
  const maxBytes = caps?.limits.max_source_bytes ?? 8192
  const bytes = byteLength(draft)
  const sizeKind = bytes > maxBytes ? 'over' : bytes > maxBytes * 0.9 ? 'warn' : ''

  // Resize from the 6px bottom handle; the height is kept per browser.
  const dragRef = useRef<{ y: number; h: number } | null>(null)
  const onResizeDown = (e: React.PointerEvent<HTMLDivElement>) => {
    dragRef.current = { y: e.clientY, h: height }
    try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* jsdom */ }
  }
  const onResizeMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current
    if (!d) return
    setHeight(Math.max(MIN_H, Math.min(800, d.h + (e.clientY - d.y))))
  }
  const onResizeUp = () => {
    if (!dragRef.current) return
    dragRef.current = null
    try { localStorage.setItem(CODE_HEIGHT_KEY, String(height)) } catch { /* storage off */ }
    editorRef.current?.layout()
  }

  const name = node.name || nodeId
  const roReason = !codeOn
    ? 'Code nodes are disabled on this server.'
    : node.locked ? 'This asset is locked. Make a local copy to edit it.'
      : !editable ? 'This graph is read-only.' : null

  const menuItems = [
    { id: 'format', label: 'Format (indent)', disabled: readOnly, onSelect: () => { const f = formatCode(draft); setDraft(f); commit(f) } },
    { id: 'copy', label: 'Copy code', onSelect: () => { void navigator.clipboard?.writeText(draft).catch(() => {}) } },
    { id: 'clear', label: 'Clear code…', disabled: readOnly || !draft, danger: true, onSelect: () => setConfirmClear(true), testId: 'nb-code-clear' },
    { id: 'ref', label: 'Open reference', onSelect: () => setRefAnchor(menuAnchor) },
  ]

  const fns = (caps?.functions ?? []).filter(f => !refQuery || `${f.name} ${f.doc}`.toLowerCase().includes(refQuery.toLowerCase()))

  return (
    <div className="nb-code-sec" data-testid={`nb-code-section-${nodeId}`}>
      {roReason && <div className="nb-code-sec__bar" data-testid="nb-code-readonly-bar">{roReason}</div>}
      {spares.length > 0 && (
        <>
          <div className="nb-code-sec__sub" data-testid="nb-spare-subtitle">Spare parameters</div>
          <SpareParamRows nodeId={nodeId} node={node} variant="inspector" editable={editable} />
        </>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="button"
          className="nb-btn nb-btn--text"
          aria-label="Code actions"
          data-testid="nb-code-menu"
          onClick={e => setMenuAnchor(e.currentTarget)}
        >
          ⋯
        </button>
      </div>
      <MonacoEditor
        ref={editorRef}
        value={draft}
        onChange={change}
        onCommit={commit}
        readOnly={readOnly}
        ariaLabel={`Code block for ${name}`}
        height={height}
        placeholder={wrangle ? undefined : NODE_CODE_PLACEHOLDER}
        markers={diagnostics}
        markersSource={parse?.code ?? null}
        context={completion}
        onLoadState={setLoadState}
        testId={`nb-code-editor-${nodeId}`}
        onEscape={() => {
          const sec = document.querySelector<HTMLElement>(`[data-testid="nb-inspector-section-${wrangle ? 'code-wrangle' : 'code'}"] > button`)
          sec?.focus()
        }}
      />
      <div
        className="nb-code-sec__resize"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the code editor"
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeUp}
        onPointerCancel={onResizeUp}
      />
      {loadState === 'failed' && (
        <div className="nb-code-sec__fail">Editor could not load. Plain text editing still works.</div>
      )}
      {diagnostics.length > 0 && (
        <div className="nb-code-sec__diags" role="list" data-testid="nb-code-diags">
          {diagnostics.map((d, i) => (
            <div role="listitem" key={i}>
              <button
                type="button"
                className="nb-code-diag"
                aria-label={`${d.severity} at line ${d.line ?? 1} column ${d.col ?? 0}: ${d.message}`}
                title={diagnosticText(d)}
                onClick={() => editorRef.current?.focus(d.line ?? 1, d.col ?? 0)}
              >
                <span className={`nb-code-dot nb-code-dot--${d.severity}`} aria-hidden="true" />
                <span className="nb-code-diag__pos">{d.line != null ? `${d.line}:${d.col ?? 0}` : '—'}</span>
                <span className="nb-code-diag__msg">{d.message}</span>
                <span className="nb-code-diag__code">{d.code}</span>
              </button>
            </div>
          ))}
        </div>
      )}
      <div className={`nb-code-sec__foot${sizeKind ? ` nb-code-sec__foot--${sizeKind}` : ''}`} data-testid="nb-code-footer">
        <span>
          {parse ? `${checkedAgoText(now - parse.at)} · ` : ''}
          <span className="nb-code-size">{sizeText(bytes, maxBytes)}</span>
        </span>
      </div>
      {menuAnchor && (
        <ActionMenu anchor={menuAnchor} items={menuItems} onClose={() => setMenuAnchor(null)} ariaLabel="Code actions" />
      )}
      {refAnchor && (
        <Popover anchor={refAnchor} onClose={() => setRefAnchor(null)} role="dialog" ariaLabel="Code reference" width={320}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: 8, maxHeight: 400, overflowY: 'auto' }}>
            <input
              type="text"
              placeholder="Search helpers"
              value={refQuery}
              onChange={e => setRefQuery(e.target.value)}
              style={{ background: 'var(--nb-bg-input)', border: '1px solid var(--nb-border)', color: 'var(--nb-text)', borderRadius: 4, padding: '4px 6px', fontSize: 12 }}
            />
            {fns.length === 0 && <div className="nb-insp-dim">No helpers listed.</div>}
            {fns.map(f => (
              <div key={f.name} style={{ fontSize: 11 }}>
                <div style={{ fontFamily: 'var(--nb-font-mono)', color: 'var(--nb-code-func)' }}>{f.signature} → {f.returns}</div>
                <div style={{ color: 'var(--nb-text-muted)' }}>{f.doc}</div>
              </div>
            ))}
          </div>
        </Popover>
      )}
      {confirmClear && (
        <Dialog
          title="Clear code"
          onCancel={() => setConfirmClear(false)}
          primaryLabel="Remove"
          danger
          onPrimary={() => {
            setConfirmClear(false)
            setDraft('')
            if (commitTimer.current) { clearTimeout(commitTimer.current); commitTimer.current = null }
            typingRef.current = false
            setTyping(false)
            setLiveDraft(nodeId, CODE_SLOT, null)
            // Spare param values stay in params (S45, S48).
            commitGraph('clear code', g => setNodeCode(g, nodeId, null))
          }}
          data-testid="nb-code-clear-dialog"
        >
          {`Remove the code block from ${name}? Spare parameters and their values are kept until you save.`}
        </Dialog>
      )}
    </div>
  )
}

/** The section's state key and default for a node (see the file comment). */
function stateKeyOf({ node }: InspectorSectionProps): string {
  return codeSectionKey(!!(node.code && node.code.trim()), node.type === WRANGLE_TYPE)
}

/**
 * Shown for nodes that can carry code. On a read-only graph only when the
 * node has code. A node with no code shows it too (collapsed, tag `off`),
 * since that is where a block is added (S45 "no code" state).
 */
function showFor(props: InspectorSectionProps): boolean {
  const { node, editable } = props
  if (!canHaveCode(node)) return false
  if (!editable && !(node.code && node.code.trim())) return false
  return true
}

registerInspectorSection({
  id: 'code',
  title: 'Code',
  order: 20,
  when: p => p.node.type !== WRANGLE_TYPE && showFor(p),
  stateKey: stateKeyOf,
  defaultOpen: p => !!(p.node.code && p.node.code.trim()) || !!useCodeStore.getState().addedCode[p.nodeId],
  Component: CodeSection,
  Count: CodeCount,
})

registerInspectorSection({
  id: 'code-wrangle',
  title: 'Code',
  order: 5,
  when: p => p.node.type === WRANGLE_TYPE,
  stateKey: stateKeyOf,
  defaultOpen: () => true,
  Component: CodeSection,
  Count: CodeCount,
})
