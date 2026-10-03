/**
 * Code on a node card (F435 W7, specs S45 and S46). Static, highlighted
 * text only: no Monaco and no textarea on the canvas (S45 must-not).
 *
 * - `CodeBlock`: the `.codeblk` look, colored by `highlightPython()`, up to
 *   `maxLines` lines then `… +N lines`. A line with a diagnostic gets the
 *   error tint; a missing `@name` read is underlined.
 * - `NodeCodeDrawer`: the 20px `code · 3 lines` row under a built-in node's
 *   params, present when the node has code (or the user added a block this
 *   session). It opens to a 6-line block. Open state is per session.
 * - `openCodeInInspector`: select the node, open the Inspector and its Code
 *   section, and put the editor's cursor on a line.
 */

import { useMemo } from 'react'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { useNodeBuilderStore } from '../store'
import { highlightPython, spansByLine, TOKEN_CSS } from './pythonLanguage'
import { lineCount } from './codeOps'
import { CODE_SLOT, setDrawerOpen, useCodeDiagnostics, useCodeStore, useEnsureParsed, useParse } from './codeStore'
import { codeLinesText, moreLinesText, openCodeInInspector } from './codeUi'
import './code.css'

const NO_DIAGS: Diagnostic[] = []

export interface CodeBlockProps {
  code: string
  maxLines: number
  /** Diagnostics with a line: that line gets the error tint. */
  diagnostics?: readonly Diagnostic[]
  /** Text in --nb-text-dim (code off on the server, S49). */
  dim?: boolean
  error?: boolean
  ariaLabel: string
  onOpen?(): void
  testId?: string
}

export function CodeBlock({ code, maxLines, diagnostics = [], dim, error, ariaLabel, onOpen, testId }: CodeBlockProps) {
  const lines = useMemo(() => code.replace(/\n+$/, '').split('\n'), [code])
  const byLine = useMemo(() => spansByLine(highlightPython(code)), [code])
  const errorLines = new Set(diagnostics.filter(d => d.line != null && d.severity === 'error').map(d => d.line as number))
  // An `attr_missing` read: underline the token at its position.
  const missing = diagnostics.filter(d => d.code === 'attr_missing' && d.line != null)
  const shown = lines.slice(0, maxLines)
  const more = lines.length - shown.length
  const cls = `nb-codeblk${error ? ' nb-codeblk--error' : ''}${dim ? ' nb-codeblk--off' : ''}`
  return (
    <div
      className={`${cls} nodrag`}
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      data-testid={testId}
      onPointerDown={e => e.stopPropagation()}
      onClick={e => { e.stopPropagation(); onOpen?.() }}
      onDoubleClick={e => { e.stopPropagation(); onOpen?.() }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen?.() } }}
    >
      {shown.map((_, i) => {
        const lineNo = i + 1
        const spans = byLine.get(lineNo) ?? []
        return (
          <span
            key={lineNo}
            className={`nb-codeblk__line${errorLines.has(lineNo) ? ' nb-codeblk__line--error' : ''}`}
            data-line={lineNo}
          >
            {spans.length === 0 ? ' ' : spans.map((sp, j) => {
              const isMissing = missing.some(d => d.line === lineNo && d.col != null && d.col >= sp.start && d.col < sp.start + sp.text.length)
              return (
                <span
                  key={j}
                  data-token={sp.cls}
                  className={isMissing ? 'nb-codeblk__missing' : undefined}
                  style={TOKEN_CSS[sp.cls]}
                >
                  {sp.text}
                </span>
              )
            })}
          </span>
        )
      })}
      {more > 0 && <span className="nb-codeblk__line nb-codeblk__more">{moreLinesText(more)}</span>}
    </div>
  )
}

/**
 * The code drawer of a built-in node (S45). Renders nothing when the node
 * has no code and the user has not added a block this session.
 */
export function NodeCodeDrawer({ nodeId, code, codeOff }: { nodeId: string; code: string | null | undefined; codeOff: boolean }) {
  const open = useCodeStore(s => !!s.openDrawers[nodeId])
  const added = useCodeStore(s => !!s.addedCode[nodeId])
  const parse = useParse(nodeId, CODE_SLOT)
  const text = code ?? ''
  const editable = useNodeBuilderStore(s => !!s.graph && !s.graph.readOnly && nodeId in s.graph.nodes)
  useEnsureParsed(nodeId, text, 'node_code', editable)
  // Parse problems plus the server's (attr_missing, ch_cycle, cook errors), UX-4.
  const diagnostics = useCodeDiagnostics(nodeId, CODE_SLOT, parse?.code === text ? parse.res.diagnostics : null, true) ?? NO_DIAGS
  if (!text && !added) return null
  const n = lineCount(text)
  const hasError = diagnostics.some(d => d.severity === 'error')
  return (
    <div className="nb-code-drawer-wrap" data-testid={`nb-code-drawer-${nodeId}`}>
      <button
        type="button"
        className={`nb-code-drawer nodrag${hasError ? ' nb-code-drawer--error' : ''}`}
        aria-expanded={open}
        data-testid={`nb-code-drawer-row-${nodeId}`}
        onPointerDown={e => e.stopPropagation()}
        onClick={e => { e.stopPropagation(); setDrawerOpen(nodeId, !open) }}
      >
        <span className="nb-code-drawer__glyph" aria-hidden="true">{'{}'}</span>
        <span className="nb-code-drawer__text">{codeLinesText(n)}</span>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && text && (
        <CodeBlock
          code={text}
          maxLines={6}
          diagnostics={diagnostics}
          dim={codeOff}
          error={hasError}
          ariaLabel={`Open code for ${useNodeBuilderStore.getState().graph?.nodes[nodeId]?.name ?? nodeId}, ${n} line${n === 1 ? '' : 's'}`}
          onOpen={() => openCodeInInspector(nodeId, 1)}
          testId={`nb-codeblk-${nodeId}`}
        />
      )}
    </div>
  )
}
