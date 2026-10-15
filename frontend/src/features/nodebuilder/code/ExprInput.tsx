/**
 * Level 1 code: a param that holds one Python expression (F435 W7, spec S44).
 *
 * `ExprParamRow` is a param row in code mode. On a node it is 36px: the
 * label, then `=` and a one-line `ExprInput` (a plain text input, never
 * Monaco, never type="number"). In the Inspector the field is a one-line
 * Monaco editor (its stand-in is the same plain input until Monaco loads)
 * with a status line under it.
 *
 * - Typing parses 300 ms after the last key (`parse_code`, context `expr`,
 *   `expected` = the param's type); a new key cancels the request in
 *   flight. Typing never waits on it.
 * - Enter or blur commits `{ expr }` through `commit`, even when the parse
 *   found a problem (validation reports it). Empty text leaves code mode and
 *   restores the last literal. Esc reverts to the last committed text; when
 *   the row had only just entered code mode, Esc leaves it.
 * - The status dot: none while a parse is pending, green after an ok parse,
 *   red after an error, amber for a warning only.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ParamSpec } from '../catalog'
import { diagnosticText } from '../../../api/nodebuilderCode'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { exprStatus } from './codeUi'
import { useNodeBuilderStore } from '../store'
import { useStreams } from '../useDiagnostics'
import { setParamExpr, setParamLiteral, expectedTypeOf, literalFor } from './codeOps'
import { attrsForNode, chPathItems } from './completion'
import {
  EXPR_PARSE_MS,
  clearParse,
  exprSlot,
  scheduleParse,
  runParse,
  setLiveDraft,
  useCodeDiagnostics,
  useCodeStore,
  useParse,
  useParsePending,
} from './codeStore'
import MonacoEditor, { type MonacoEditorHandle } from './MonacoEditor'
import type { ModelContext } from './monacoProviders'
import './code.css'

// Visually hidden, still read by screen readers.
/** One empty list, so the editor's marker effect does not rerun on every render. */
const NO_DIAGNOSTICS: readonly Diagnostic[] = []

const srOnly: React.CSSProperties = {
  position: 'absolute', width: 1, height: 1, padding: 0, margin: -1, overflow: 'hidden',
  clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0,
}

export interface ExprInputProps {
  value: string
  onChange(text: string): void
  onCommit(text: string): void
  onEscape(): void
  label: string
  invalid: boolean
  describedBy?: string
  readOnly?: boolean
  /** Focus and select all on mount (the row has just entered code mode). */
  autoSelect?: boolean
  dim?: boolean
}

/**
 * The one-line expression field: always `type="text"` (F278), no spell
 * check or autocorrect, `data-testid="expr-input"`.
 */
export function ExprInput({ value, onChange, onCommit, onEscape, label, invalid, describedBy, readOnly, autoSelect, dim }: ExprInputProps) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!autoSelect) return
    const el = ref.current
    el?.focus()
    el?.select()
  }, [autoSelect])
  return (
    <input
      ref={ref}
      type="text"
      inputMode="text"
      spellCheck={false}
      autoComplete="off"
      autoCorrect="off"
      autoCapitalize="off"
      data-testid="expr-input"
      className={`nb-expr-input${dim ? ' nb-expr-input--off' : ''}`}
      aria-label={`Expression for ${label}`}
      aria-invalid={invalid ? true : undefined}
      aria-describedby={describedBy}
      readOnly={readOnly}
      value={value}
      title={value}
      onChange={e => onChange(e.target.value)}
      onBlur={e => onCommit(e.target.value)}
      onKeyDown={e => {
        if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          onCommit(e.currentTarget.value)
          e.currentTarget.blur()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          onEscape()
        }
      }}
      onPointerDown={e => e.stopPropagation()}
    />
  )
}

export interface ExprParamRowProps {
  nodeId: string
  paramKey: string
  /** The committed expression, or null while the row is only entering code mode. */
  expr: string | null
  /** The literal the row had before code mode (the starting text when entering). */
  literal: unknown
  spec: ParamSpec | undefined
  label: string
  variant: 'node' | 'inspector'
  /** True when the row has just entered code mode and nothing is committed yet. */
  entering: boolean
  /** Called when the row leaves code mode without a commit (Esc, empty text). */
  onLeave(): void
  /** Called once the expression is committed (the row's entering state ends). */
  onCommitted(): void
  /** Code is off on the server: read-only, dim (S49). */
  disabled?: boolean
  /** The `=` toggle drawn by the row, in code mode. */
  toggle?: React.ReactNode
  initialText: string
}

/** A param row in code mode (node or Inspector). */
export function ExprParamRow(props: ExprParamRowProps) {
  const { nodeId, paramKey, expr, spec, label, variant, entering, onLeave, onCommitted, disabled, toggle, initialText } = props
  const commitGraph = useNodeBuilderStore(s => s.commit)
  const [draft, setDraft] = useState(expr ?? initialText)
  const [typing, setTyping] = useState(false)
  const committedRef = useRef(expr)
  // Esc: the typed text the field's blur is about to commit, which must not be (FE-5).
  const discardRef = useRef<string | null>(null)
  const editorRef = useRef<MonacoEditorHandle>(null)
  const slot = exprSlot(paramKey)
  const expected = useMemo(() => ({ type: expectedTypeOf(spec) }), [spec])
  const parse = useParse(nodeId, slot)
  const pending = useParsePending(nodeId, slot)
  const statusId = `${useId()}-status`

  // Follow the store when it changes from outside (undo) while not typing:
  // adjusted during render (React's pattern for state that follows a prop).
  const [prevExpr, setPrevExpr] = useState(expr)
  if (expr !== prevExpr) {
    setPrevExpr(expr)
    if (!typing && expr !== null) setDraft(expr)
  }
  useEffect(() => {
    if (!typing) committedRef.current = expr
  }, [expr, typing])

  // A committed expression with no parse yet (a graph just opened) is checked once.
  useEffect(() => {
    if (expr === null || disabled) return
    const st = useCodeStore.getState()
    if (st.parses[`${nodeId}|${slot}`] || st.pending[`${nodeId}|${slot}`]) return
    void runParse({ nodeId, slot, code: expr, context: 'expr', expected })
  }, [expr, nodeId, slot, expected, disabled])

  // The parse answer's problems, plus the server's (ch_cycle, a cook error)
  // while the field shows the committed expression (UX-4).
  const diagnostics = useCodeDiagnostics(nodeId, slot, parse?.res.diagnostics ?? null, expr !== null && draft === expr)
  const status = exprStatus(diagnostics, pending)
  const firstError = diagnostics?.find(d => d.severity === 'error') ?? diagnostics?.[0] ?? null
  const invalid = status === 'error'

  const change = (text: string) => {
    discardRef.current = null
    setTyping(true)
    setDraft(text)
    setLiveDraft(nodeId, slot, text)
    scheduleParse({ nodeId, slot, code: text, context: 'expr', expected }, EXPR_PARSE_MS)
  }

  const commit = (text: string) => {
    const discard = discardRef.current
    discardRef.current = null
    // The blur that follows Esc: the reverted text stays, the typed one is dropped.
    if (discard !== null && text === discard) return
    setTyping(false)
    setLiveDraft(nodeId, slot, null)
    if (text.trim() === '') {
      // Empty: leave code mode and put the last literal back.
      const g = useNodeBuilderStore.getState().graph
      const node = g?.nodes[nodeId]
      clearParse(nodeId, slot)
      if (node && committedRef.current !== null) {
        const lit = literalFor(node, paramKey, spec?.default)
        commitGraph(`edit ${paramKey}`, gr => setParamLiteral(gr, nodeId, paramKey, lit))
        committedRef.current = null
      }
      onLeave()
      return
    }
    if (text === committedRef.current) return
    committedRef.current = text
    commitGraph(`edit ${paramKey}`, gr => setParamExpr(gr, nodeId, paramKey, text))
    onCommitted()
  }

  const escape = () => {
    setTyping(false)
    setLiveDraft(nodeId, slot, null)
    if (entering && committedRef.current === null) {
      clearParse(nodeId, slot)
      onLeave()
      return
    }
    // S44: Esc reverts to the last committed text and blurs. The blur
    // commits what the field holds, so put the committed text there first
    // (Monaco, the stand-in) and drop a blur that still carries the typed
    // text (a controlled input that has not re-rendered yet).
    const committed = committedRef.current ?? ''
    discardRef.current = draft === committed ? null : draft
    editorRef.current?.revert(committed)
    setDraft(committed)
    ;(document.activeElement as HTMLElement | null)?.blur?.()
    // The discarded draft's parse problems go; the committed text is checked again.
    if (committedRef.current !== null && (pending || parse?.code !== committed)) {
      void runParse({ nodeId, slot, code: committed, context: 'expr', expected })
    }
  }

  // S44: a result type only when the server sends one (W7 parse_code never
  // runs code, so it sends none); the green dot already says "parsed ok".
  const resultType = parse?.res.result_type ?? null
  const statusText = firstError
    ? diagnosticText(firstError)
    : status === 'ok' && resultType ? `→ ${resultType}` : ''

  // Inspector: Monaco single line, with the stand-in input until it loads.
  const streams = useStreams()
  const context = useMemo<ModelContext>(() => ({
    attrs: () => attrsForNode(useNodeBuilderStore.getState().graph, nodeId, streams),
    detailOnly: true,
    paths: () => chPathItems(useNodeBuilderStore.getState().graph, nodeId, streams),
    functions: () => useCodeStore.getState().caps?.functions ?? [],
  }), [nodeId, streams])

  const dot = status !== 'none'
    ? <span className={`nb-expr-dot nb-expr-dot--${status}`} data-testid="expr-status-dot" data-status={status} aria-hidden="true" />
    : null

  if (variant === 'inspector') {
    return (
      <div className="nb-expr-row nb-expr-row--inspector" data-testid={`nb-param-inspector-${nodeId}-${paramKey}`} data-code-mode="true">
        {toggle}
        <span>{label}</span>
        <div className="nb-expr-row__field">
          <MonacoEditor
            ref={editorRef}
            singleLine
            value={draft}
            readOnly={disabled}
            ariaLabel={`Expression for ${label}`}
            onChange={change}
            onCommit={commit}
            onEscape={escape}
            markers={diagnostics ?? NO_DIAGNOSTICS}
            markersSource={parse?.code ?? null}
            context={context}
            testId={`nb-expr-editor-${paramKey}`}
            plainTestId="expr-input"
            plainInvalid={invalid}
            plainDescribedBy={statusId}
            autoSelect={entering}
          />
          {dot}
        </div>
        <div
          id={statusId}
          className={`nb-expr-status${firstError ? ' nb-expr-status--error' : ''}`}
          data-testid={`nb-expr-status-${paramKey}`}
        >
          {statusText}
        </div>
      </div>
    )
  }

  return (
    <div className="nb-expr-row" data-testid={`nb-param-${nodeId}-${paramKey}`} data-code-mode="true">
      {toggle}
      <span>{label}</span>
      <div className="nb-expr-row__field">
        <span className="nb-expr-row__eq" aria-hidden="true">=</span>
        <ExprInput
          value={draft}
          label={label}
          invalid={invalid}
          describedBy={statusId}
          readOnly={disabled}
          dim={disabled}
          autoSelect={entering}
          onChange={change}
          onCommit={commit}
          onEscape={escape}
        />
        {dot}
      </div>
      <span id={statusId} style={srOnly} data-testid={`nb-expr-status-${paramKey}`}>{statusText}</span>
    </div>
  )
}
