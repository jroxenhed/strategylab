/**
 * The Monaco the code UI loads (F435 W7 FE-8): the editor API, the editor
 * contributions the node builder's editors use, and the python basic
 * language. Imported only by monacoLoader.ts, dynamically, so it is its own
 * chunk. The package's main entry would also bring every basic language and
 * the TypeScript, CSS, HTML and JSON language services with their workers
 * (about 9 MB in dist that no editor here ever fetches).
 *
 * The contribution list follows monaco-editor 0.57's `editor/editor.main`,
 * keeping what a small Python editor needs: cursor and word commands,
 * clipboard, find, hover (markers), suggest with snippets, parameter hints,
 * comment toggling, line operations, multi-cursor, marker navigation and
 * the read-only message.
 *
 * The file name has no "monaco" in it on purpose: the loader in the main
 * chunk names this chunk, and the plan's bundle gate greps index-*.js for
 * that word.
 */

import 'monaco-editor/editor/browser/coreCommands'
import 'monaco-editor/editor/browser/widget/codeEditor/codeEditorWidget'
import 'monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching'
import 'monaco-editor/editor/contrib/caretOperations/browser/caretOperations'
import 'monaco-editor/editor/contrib/caretOperations/browser/transpose'
import 'monaco-editor/editor/contrib/clipboard/browser/clipboard'
import 'monaco-editor/editor/contrib/comment/browser/comment'
import 'monaco-editor/editor/contrib/cursorUndo/browser/cursorUndo'
import 'monaco-editor/features/find/register'
import 'monaco-editor/editor/contrib/find/browser/findController'
import 'monaco-editor/editor/contrib/gotoError/browser/gotoError'
import 'monaco-editor/editor/contrib/gotoError/browser/markerSelectionStatus'
import 'monaco-editor/editor/contrib/hover/browser/hoverContribution'
import 'monaco-editor/editor/contrib/indentation/browser/indentation'
import 'monaco-editor/editor/contrib/lineSelection/browser/lineSelection'
import 'monaco-editor/editor/contrib/linesOperations/browser/linesOperations'
import 'monaco-editor/editor/contrib/multicursor/browser/multicursor'
import 'monaco-editor/editor/contrib/parameterHints/browser/parameterHints'
import 'monaco-editor/editor/contrib/readOnlyMessage/browser/contribution'
import 'monaco-editor/editor/contrib/smartSelect/browser/smartSelect'
import 'monaco-editor/editor/contrib/snippet/browser/snippetController2'
import 'monaco-editor/editor/contrib/suggest/browser/suggestController'
import 'monaco-editor/editor/contrib/toggleTabFocusMode/browser/toggleTabFocusMode'
import 'monaco-editor/editor/contrib/tokenization/browser/tokenization'
import 'monaco-editor/editor/contrib/unicodeHighlighter/browser/unicodeHighlighter'
import 'monaco-editor/editor/contrib/wordHighlighter/browser/wordHighlighter'
import 'monaco-editor/editor/contrib/wordOperations/browser/wordOperations'
import 'monaco-editor/editor/contrib/wordPartOperations/browser/wordPartOperations'
import 'monaco-editor/editor/common/standaloneStrings'
// Registers `python` with its lazy Monarch loader (pythonLanguage.ts starts from it).
import 'monaco-editor/languages/definitions/python/register'
// The codicon font (suggest kinds, find widget buttons). The package's
// exports map only reaches .js files, so the CSS is imported by path.
import '../../../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css'
import '../../../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon-modifiers.css'

export * from 'monaco-editor/editor/editor.api'
