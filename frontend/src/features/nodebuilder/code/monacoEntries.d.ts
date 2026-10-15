// Deep entries of monaco-editor that editorCore.ts loads instead of the
// main entry (FE-8). Typed here in case the package ships no typings for
// them; the API is the main entry's.
declare module 'monaco-editor/editor/editor.api' {
  export * from 'monaco-editor'
}
declare module 'monaco-editor/editor/browser/*'
declare module 'monaco-editor/editor/contrib/*'
declare module 'monaco-editor/editor/common/*'
declare module 'monaco-editor/features/*'
declare module 'monaco-editor/languages/*'
