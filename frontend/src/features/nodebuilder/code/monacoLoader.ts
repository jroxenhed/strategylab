/**
 * Loads Monaco on first use (F435 W7, spec S47 "Loading").
 *
 * Monaco is bundled locally (the `monaco-editor` package plus a Vite
 * `?worker` for its editor worker), never from a CDN, and only through the
 * dynamic imports below, so it lands in its own chunks and never in
 * `index-*.js` (the plan's bundle gate). Nothing in the node builder imports
 * `monaco-editor` at module top level.
 *
 * The first load also registers the `nb-python` language, the theme and the
 * completion and hover providers, once (pythonLanguage.ts guards it).
 *
 * Only what the code UI uses is loaded (FE-8, editorCore.ts): the editor
 * API with the editor features it needs, the python basic language and the
 * editor worker, not the package's main entry with every language service.
 *
 * In unit tests (Vite mode `test`) Monaco is not loaded: the editors stay on
 * their plain text stand-in, which has the same props and behavior.
 */

import type * as MonacoNs from 'monaco-editor'
import { registerNbPython, type MonacoLanguagesApi, type MonarchLike } from './pythonLanguage'
import { completionProvider, hoverProvider } from './monacoProviders'

export type Monaco = typeof MonacoNs

let loading: Promise<Monaco | null> | null = null

async function load(): Promise<Monaco | null> {
  if (import.meta.env.MODE === 'test') return null
  const [monaco, workerMod] = await Promise.all([
    import('./editorCore') as Promise<Monaco>,
    import('monaco-editor/editor/editor.worker?worker'),
  ])
  const python = await builtinPython(monaco)
  const EditorWorker = workerMod.default
  // Every model is nb-python, so the plain editor worker serves every label.
  ;(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
    getWorker: () => new EditorWorker(),
  }
  const base = python && python.language
    ? { language: python.language as unknown as MonarchLike, conf: python.conf }
    : null
  registerNbPython(monaco as unknown as MonacoLanguagesApi, base, {
    completion: completionProvider(monaco),
    hover: hoverProvider(monaco),
  })
  return monaco
}

/**
 * The built-in python Monarch definition (S47 starts from it). Monaco
 * registers each basic language with a lazy `loader`; that is the only
 * public way to reach it (the file is not in the package's exports). Null
 * when it is missing: the language then uses FALLBACK_PYTHON.
 */
async function builtinPython(monaco: Monaco): Promise<{ language?: unknown; conf?: unknown } | null> {
  try {
    const def = monaco.languages.getLanguages().find(l => l.id === 'python') as
      { loader?: () => Promise<{ language?: unknown; conf?: unknown }> } | undefined
    return def?.loader ? await def.loader() : null
  } catch {
    return null
  }
}

/** Monaco, loaded once. Resolves to null in unit tests; rejects when the chunk fails to load. */
export function loadMonaco(): Promise<Monaco | null> {
  if (!loading) {
    loading = load().catch(e => {
      // Let a later open try again (a flaky network), but report this one.
      loading = null
      throw e
    })
  }
  return loading
}
