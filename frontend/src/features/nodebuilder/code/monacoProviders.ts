/**
 * The one completion provider and the one hover provider of `nb-python`
 * (F435 W7, spec S47). They are registered once with the language; each
 * editor tells them about its node through `setModelContext(model uri)`,
 * so providers never duplicate per editor.
 *
 * The logic is in completion.ts (pure, tested); this file only converts
 * to Monaco's types. Monaco types are imported as types only.
 */

import type * as MonacoNs from 'monaco-editor'
import type { CodeFunctionInfo } from '../../../api/nodebuilderCode'
import { completionsFor, hoverFor, type CompletionAttr, type CompletionKind } from './completion'

type Monaco = typeof MonacoNs

/** What the providers need to know about the node an editor belongs to. */
export interface ModelContext {
  attrs(): CompletionAttr[]
  /** An expression reads detail attributes only. */
  detailOnly: boolean
  paths(): string[]
  functions(): CodeFunctionInfo[]
}

const contexts = new Map<string, ModelContext>()

/** Set (or remove, with null) the context of one editor model. */
export function setModelContext(uri: string, ctx: ModelContext | null): void {
  if (ctx) contexts.set(uri, ctx)
  else contexts.delete(uri)
}

function kindOf(monaco: Monaco, k: CompletionKind): MonacoNs.languages.CompletionItemKind {
  const K = monaco.languages.CompletionItemKind
  switch (k) {
    case 'Function': return K.Function
    case 'Module': return K.Module
    case 'Snippet': return K.Snippet
    default: return K.Variable
  }
}

export function completionProvider(monaco: Monaco): MonacoNs.languages.CompletionItemProvider {
  return {
    triggerCharacters: ['@', '(', '"', '.'],
    provideCompletionItems(model, position) {
      const ctx = contexts.get(model.uri.toString())
      if (!ctx) return { suggestions: [] }
      const textBefore = model.getLineContent(position.lineNumber).slice(0, position.column - 1)
      const items = completionsFor({
        textBefore,
        code: model.getValue(),
        attrs: ctx.attrs(),
        detailOnly: ctx.detailOnly,
        functions: ctx.functions(),
        paths: ctx.paths(),
      })
      return {
        suggestions: items.map(it => ({
          label: it.label,
          detail: it.detail,
          documentation: it.documentation,
          kind: kindOf(monaco, it.kind),
          insertText: it.insertText,
          insertTextRules: it.snippet ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet : undefined,
          sortText: it.sortText,
          range: {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: Math.max(1, position.column - it.replace),
            endColumn: position.column,
          },
        })),
      }
    },
  }
}

export function hoverProvider(monaco: Monaco): MonacoNs.languages.HoverProvider {
  void monaco
  return {
    provideHover(model, position) {
      const ctx = contexts.get(model.uri.toString())
      if (!ctx) return null
      const line = model.getLineContent(position.lineNumber)
      // The word under the pointer, with `sl.` and `@` kept on it.
      const re = /(?:sl\.[a-z_][a-z0-9_]*|\x40[a-z_][a-z0-9_]*)/g
      for (const m of line.matchAll(re)) {
        const start = (m.index ?? 0) + 1
        const end = start + m[0].length
        if (position.column < start || position.column > end) continue
        const text = hoverFor(m[0], ctx.functions(), ctx.attrs())
        if (!text) return null
        return {
          range: { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber, startColumn: start, endColumn: end },
          contents: [{ value: text }],
        }
      }
      return null
    },
  }
}
