/**
 * MonacoEditor: the one code editor of the node builder (F435 W7, spec S47).
 * Used by the Inspector only (S44 single line, S45 and S46 blocks); node
 * cards never mount it (S45 must-not).
 *
 * - Monaco loads on first mount (monacoLoader.ts, its own chunks). Until it
 *   is there, or when it fails, a plain `<textarea>` (or `<input>` when
 *   `singleLine`) with the same font, colors, label, `onChange` and
 *   `onCommit` stands in; the text and caret move over when Monaco arrives.
 * - Commit: blur, Cmd+Enter, and (single line) Enter. Tab and Shift+Tab
 *   leave a single-line field like an ordinary input.
 * - Keys are editor actions scoped to this editor (a context key of its
 *   own) and disposed with it: Monaco's keybindings are shared by every
 *   editor on the page, so an unscoped binding would run in another editor.
 * - `onChange` reports the user's edits only, never a value the caller set.
 * - Markers come from the caller (the last parse answer), so they change
 *   only when a parse answers, never per keystroke. A gutter dot per line
 *   with a marker (`nb-code-glyph-error` / `-warning`).
 * - `automaticLayout` is off; the caller calls `layout()` after a resize.
 * - Esc with no Monaco widget open calls `onEscape` (leave the editor).
 */

import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react'
import type * as MonacoNs from 'monaco-editor'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { toMonacoRange } from '../../../api/nodebuilderCode'
import { loadMonaco, type Monaco } from './monacoLoader'
import { NB_PYTHON_ID, NB_THEME_ID } from './pythonLanguage'
import { setModelContext, type ModelContext } from './monacoProviders'
import { focusNeighbour } from './focusNav'
import './code.css'

export interface MonacoEditorHandle {
  /** Re-measure after a resize (automaticLayout is off). */
  layout(): void
  /** Focus the editor, optionally at a 1-based line and 0-based column. */
  focus(line?: number, col?: number): void
  /**
   * Put this text in the editor now, even while it has focus (Esc reverts a
   * draft before the field blurs). Not reported through `onChange`.
   */
  revert(value: string): void
}

export type EditorLoadState = 'loading' | 'loaded' | 'failed' | 'plain'

export interface MonacoEditorProps {
  value: string
  onChange?(value: string): void
  /** Blur, Cmd+Enter, and Enter on a single line. */
  onCommit?(value: string): void
  /** Esc with no widget open. */
  onEscape?(): void
  markers?: readonly Diagnostic[]
  singleLine?: boolean
  readOnly?: boolean
  ariaLabel: string
  /** Height in px (block editors). */
  height?: number
  placeholder?: string
  /** Completion and hover context for this editor's node. */
  context?: ModelContext
  onLoadState?(state: EditorLoadState): void
  testId?: string
  /** Test id, invalid state and description of the plain stand-in field. */
  plainTestId?: string
  plainInvalid?: boolean
  /**
   * The text `markers` were computed for (the parse request's code). When
   * given and the editor now holds other text (the user typed on while the
   * parse ran), the answer's positions would land in the wrong place, so
   * the markers already shown stay (Monaco moves them with the edits) until
   * an answer for the current text arrives.
   */
  markersSource?: string | null
  plainDescribedBy?: string
  /** Focus and select all on mount (a row that has just entered code mode). */
  autoSelect?: boolean
}

const FONT = 'Geist Mono, ui-monospace, SF Mono, Menlo, Consolas, monospace'

/** Write a value the caller set into the model without reporting it as an edit. */
function setQuietly(flag: { current: boolean }, model: MonacoNs.editor.ITextModel, value: string): void {
  flag.current = true
  try { model.setValue(value) } finally { flag.current = false }
}

/** Numbers the editors, for each one's own context key and action ids. */
let editorSeq = 0

/** Monaco severity for a diagnostic severity. */
function severityOf(monaco: Monaco, s: Diagnostic['severity']): MonacoNs.MarkerSeverity {
  if (s === 'warning') return monaco.MarkerSeverity.Warning
  if (s === 'info') return monaco.MarkerSeverity.Info
  return monaco.MarkerSeverity.Error
}

/** Markers for a model. A diagnostic with no line marks the whole first line (S47). */
function markersFor(
  monaco: Monaco,
  model: MonacoNs.editor.ITextModel,
  list: readonly Diagnostic[],
): MonacoNs.editor.IMarkerData[] {
  return list.map(d => {
    const r = d.line == null
      ? { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: Math.max(2, model.getLineMaxColumn(1)) }
      : toMonacoRange(d)
    return { ...r, severity: severityOf(monaco, d.severity), message: `${d.message} (${d.code})` }
  })
}

const MonacoEditor = forwardRef<MonacoEditorHandle, MonacoEditorProps>(function MonacoEditor(props, ref) {
  const {
    value, onChange, onCommit, onEscape, markers, singleLine = false, readOnly = false,
    ariaLabel, height, placeholder, context, onLoadState, testId,
    plainTestId, plainInvalid, plainDescribedBy, autoSelect, markersSource,
  } = props
  const hostRef = useRef<HTMLDivElement>(null)
  const plainRef = useRef<HTMLTextAreaElement & HTMLInputElement>(null)
  const editorRef = useRef<MonacoNs.editor.IStandaloneCodeEditor | null>(null)
  const monacoRef = useRef<Monaco | null>(null)
  const decoRef = useRef<MonacoNs.editor.IEditorDecorationsCollection | null>(null)
  const [state, setState] = useState<EditorLoadState>('loading')
  // True while the caller's value is being written into the model: that
  // change is not the user's, so it is not reported through onChange.
  const flushing = useRef(false)
  // Focus and selection to move over from the stand-in once the editor shows.
  const pendingFocus = useRef<{ start: number; end: number } | null>(null)
  // The latest props, read by Monaco's callbacks (set up once).
  const live = useRef(props)
  useEffect(() => { live.current = props })

  useEffect(() => { onLoadState?.(state) }, [state, onLoadState])

  // A row that has just entered code mode: focus the field with its text selected.
  useEffect(() => {
    if (!autoSelect) return
    const plain = plainRef.current
    plain?.focus()
    plain?.select()
    // Only on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Load Monaco and build the editor once; tear it down on unmount.
  useEffect(() => {
    let disposed = false
    const disposables: { dispose(): void }[] = []
    loadMonaco().then(monaco => {
      if (disposed) return
      if (!monaco) { setState('plain'); return }
      const host = hostRef.current
      if (!host) return
      monacoRef.current = monaco
      // Carry the stand-in's text and caret over.
      const plain = plainRef.current
      const text = plain ? plain.value : live.current.value
      const caret = plain?.selectionStart ?? text.length
      const model = monaco.editor.createModel(text, NB_PYTHON_ID)
      const editor = monaco.editor.create(host, {
        model,
        theme: NB_THEME_ID,
        ariaLabel: live.current.ariaLabel,
        readOnly: live.current.readOnly,
        fontFamily: FONT,
        fontSize: 12,
        lineHeight: 18,
        minimap: { enabled: false },
        lineNumbers: singleLine ? 'off' : 'on',
        lineNumbersMinChars: 3,
        glyphMargin: !singleLine,
        folding: false,
        wordWrap: singleLine ? 'off' : 'on',
        renderLineHighlight: singleLine ? 'none' : 'line',
        scrollBeyondLastLine: false,
        tabSize: 4,
        insertSpaces: true,
        quickSuggestions: { other: true, comments: false, strings: false },
        suggestOnTriggerCharacters: true,
        bracketPairColorization: { enabled: false },
        guides: { indentation: false },
        overviewRulerBorder: false,
        overviewRulerLanes: singleLine ? 0 : undefined,
        hideCursorInOverviewRuler: true,
        automaticLayout: false,
        contextmenu: false,
        fixedOverflowWidgets: true,
        accessibilitySupport: 'auto',
        // A real <textarea> takes the keys, so the canvas sees a typing target (FE-4).
        editContext: false,
        scrollbar: singleLine ? { vertical: 'hidden', horizontal: 'hidden', alwaysConsumeMouseWheel: false } : undefined,
      })
      editorRef.current = editor
      decoRef.current = editor.createDecorationsCollection([])
      editor.setPosition(model.getPositionAt(caret))
      if (plain && document.activeElement === plain) {
        // The host is still display:none here, so focus() would do nothing;
        // the layout effect below focuses once the editor is shown.
        pendingFocus.current = { start: caret, end: plain.selectionEnd ?? caret }
      }
      if (live.current.context) setModelContext(model.uri.toString(), live.current.context)
      disposables.push(model.onDidChangeContent(() => {
        if (flushing.current) return
        let v = model.getValue()
        if (singleLine && /[\r\n]/.test(v)) {
          // A pasted newline in a one-line field: keep one line.
          v = v.replace(/[\r\n]+/g, ' ')
          model.setValue(v)
          return
        }
        live.current.onChange?.(v)
      }))
      disposables.push(editor.onDidBlurEditorText(() => live.current.onCommit?.(model.getValue())))
      // Every key binding is an action of this editor only: its own context
      // key is set in this editor's scope, so the binding never runs while
      // another editor has focus, and dispose() removes it (FE-1).
      const uid = ++editorSeq
      const own = `nbEditor${uid}`
      editor.createContextKey(own, true)
      const bind = (id: string, label: string, keybindings: number[], when: string | null, run: () => void) => {
        disposables.push(editor.addAction({
          id: `nb.${id}.${uid}`,
          label,
          keybindings,
          keybindingContext: when ? `${own} && ${when}` : own,
          run,
        }))
      }
      bind('commit', 'Commit code', [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter], null,
        () => live.current.onCommit?.(model.getValue()))
      if (singleLine) {
        bind('commitLine', 'Commit expression', [monaco.KeyCode.Enter], '!suggestWidgetVisible',
          () => live.current.onCommit?.(model.getValue()))
        // A one-line field: Tab leaves it like an ordinary input (S44).
        const out = (backwards: boolean) => () => {
          const box = host.parentElement ?? host
          focusNeighbour(box, backwards)
        }
        bind('tabNext', 'Next field', [monaco.KeyCode.Tab], '!suggestWidgetVisible', out(false))
        bind('tabPrev', 'Previous field', [monaco.KeyMod.Shift | monaco.KeyCode.Tab], '!suggestWidgetVisible', out(true))
      }
      bind('escape', 'Leave editor', [monaco.KeyCode.Escape], '!suggestWidgetVisible && !findWidgetVisible && !parameterHintsVisible',
        () => live.current.onEscape?.())
      setState('loaded')
      disposables.push({ dispose: () => setModelContext(model.uri.toString(), null) })
      disposables.push(editor, model)
    }).catch(() => {
      if (!disposed) setState('failed')
    })
    return () => {
      disposed = true
      editorRef.current = null
      decoRef.current = null
      for (const d of disposables.reverse()) {
        try { d.dispose() } catch { /* already gone */ }
      }
    }
    // The editor is built once; later prop changes are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Outside changes (undo, another view) reach the editor while it is not focused.
  useEffect(() => {
    const editor = editorRef.current
    const model = editor?.getModel()
    if (!editor || !model) return
    if (model.getValue() !== value && !editor.hasTextFocus()) setQuietly(flushing, model, value)
  }, [value, state])

  // The editor shows: move the stand-in's focus and selection over (FE-3).
  useLayoutEffect(() => {
    const editor = editorRef.current
    const monaco = monacoRef.current
    const want = pendingFocus.current
    if (state !== 'loaded' || !editor || !monaco || !want) return
    pendingFocus.current = null
    const model = editor.getModel()
    editor.layout()
    if (model) {
      editor.setSelection(monaco.Range.fromPositions(model.getPositionAt(want.start), model.getPositionAt(want.end)))
    }
    editor.focus()
  }, [state])

  useEffect(() => {
    editorRef.current?.updateOptions({ ariaLabel })
  }, [ariaLabel, state])

  useEffect(() => {
    editorRef.current?.updateOptions({ readOnly })
  }, [readOnly, state])

  useEffect(() => {
    const editor = editorRef.current
    const model = editor?.getModel()
    if (context && model) setModelContext(model.uri.toString(), context)
  }, [context, state])

  // Markers and gutter dots from the caller's diagnostics.
  useEffect(() => {
    const monaco = monacoRef.current
    const editor = editorRef.current
    const model = editor?.getModel()
    if (!monaco || !editor || !model) return
    if (typeof markersSource === 'string' && model.getValue() !== markersSource) return
    const list = markers ?? []
    monaco.editor.setModelMarkers(model, 'nb-code', markersFor(monaco, model, list))
    if (!singleLine && decoRef.current) {
      const lines = new Map<number, Diagnostic['severity']>()
      for (const d of list) {
        const line = d.line ?? 1
        if (lines.get(line) !== 'error') lines.set(line, d.severity)
      }
      decoRef.current.set([...lines].map(([line, sev]) => ({
        range: { startLineNumber: line, startColumn: 1, endLineNumber: line, endColumn: 1 },
        options: { glyphMarginClassName: sev === 'error' ? 'nb-code-glyph-error' : 'nb-code-glyph-warning' },
      })))
    }
  }, [markers, markersSource, state, singleLine])

  useEffect(() => {
    editorRef.current?.layout()
  }, [height, state])

  useImperativeHandle(ref, () => ({
    layout() { editorRef.current?.layout() },
    revert(next: string) {
      const model = editorRef.current?.getModel()
      if (model && model.getValue() !== next) setQuietly(flushing, model, next)
      const plain = plainRef.current
      // A controlled stand-in: write the DOM value too, so its blur reads it.
      if (plain && plain.value !== next) plain.value = next
    },
    focus(line?: number, col?: number) {
      const editor = editorRef.current
      if (editor) {
        if (line != null) editor.setPosition({ lineNumber: line, column: (col ?? 0) + 1 })
        editor.focus()
        return
      }
      const plain = plainRef.current
      if (!plain) return
      plain.focus()
      if (line != null) {
        const lines = plain.value.split('\n')
        let off = 0
        for (let i = 0; i < Math.min(line - 1, lines.length); i++) off += lines[i].length + 1
        off += Math.min(col ?? 0, lines[line - 1]?.length ?? 0)
        try { plain.setSelectionRange(off, off) } catch { /* jsdom */ }
      }
    },
  }), [])

  const showMonaco = state === 'loaded'
  const style: React.CSSProperties = singleLine ? { height: 28 } : { height: height ?? 96 }

  // The plain stand-in: same font, colors, label and events as the editor.
  const plainProps = {
    ref: plainRef,
    className: `nb-code-plain${singleLine ? ' nb-code-plain--line' : ''}`,
    value,
    readOnly,
    'aria-label': ariaLabel,
    spellCheck: false,
    autoComplete: 'off',
    autoCorrect: 'off',
    autoCapitalize: 'off',
    placeholder,
    'data-testid': plainTestId ?? (testId ? `${testId}-plain` : undefined),
    'aria-invalid': plainInvalid ? true : undefined,
    'aria-describedby': plainDescribedBy,
    onChange: (e: React.ChangeEvent<HTMLTextAreaElement & HTMLInputElement>) => onChange?.(e.target.value),
    onBlur: (e: React.FocusEvent<HTMLTextAreaElement & HTMLInputElement>) => onCommit?.(e.target.value),
    onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement & HTMLInputElement>) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey || singleLine)) {
        e.preventDefault()
        e.stopPropagation()
        onCommit?.(e.currentTarget.value)
      } else if (e.key === 'Escape') {
        e.stopPropagation()
        onEscape?.()
      }
    },
    onPointerDown: (e: React.PointerEvent) => e.stopPropagation(),
  }

  return (
    <div
      className="nb-code-editor nodrag nopan"
      style={style}
      data-testid={testId}
      data-editor-state={state}
      // Cmd+Enter in the editor commits the code; it must not also run a backtest.
      onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) e.stopPropagation() }}
    >
      <div ref={hostRef} className="nb-code-editor__host" style={{ display: showMonaco ? 'block' : 'none', height: '100%' }} />
      {!showMonaco && (singleLine
        ? <input type="text" inputMode="text" {...plainProps} />
        : <textarea {...plainProps} style={{ height: '100%' }} />)}
      {showMonaco && placeholder && value === '' && (
        <div className="nb-code-editor__placeholder" aria-hidden="true">{placeholder}</div>
      )}
    </div>
  )
})

export default MonacoEditor
