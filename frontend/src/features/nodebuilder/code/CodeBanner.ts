/**
 * Code-off mode for the node builder (F435 W7, spec S49).
 *
 * `useCodeModeController()` is mounted once by NodeBuilder. It fetches the
 * code capabilities once (never polled) and keeps the S07 banner in step:
 * shown while code is off on the server and the graph has code (or the user
 * tried to add code), until Dismiss (per session). `Show N code nodes`
 * selects those nodes and frames them. The count prefers the server's
 * `code_disabled` diagnostics; the store scan is only the fallback.
 */

import { useEffect, useMemo } from 'react'
import { dismissNotice, pushNotice } from '../notices'
import { useNodeBuilderStore } from '../store'
import { useDiagnostics } from '../useDiagnostics'
import { runCommand } from '../commands'
import { codeNodeIds, hasCode } from './codeOps'
import { followValidateAnswers, loadCodeCapabilities, pruneCodeState, useCodeStore } from './codeStore'

export const CODE_BANNER_KEY = 'code_disabled'
export const CODE_BANNER_TEXT = 'Code nodes are disabled on this server (SL_CODE_NODES=0). Graphs with expressions, code blocks or Wrangles cannot be validated, cooked or spawned.'
export const CODE_OFF_TOOLTIP = 'Code nodes are disabled on this server'

/** `Show 3 code nodes` */
export function showCodeNodesText(n: number): string {
  return `Show ${n} code nodes`
}

export function useCodeModeController(): void {
  useEffect(() => { void loadCodeCapabilities() }, [])
  // Drop parse results of snippets that are gone (a stale error must not block Run).
  useEffect(() => useNodeBuilderStore.subscribe((s, prev) => {
    if (s.graph !== prev.graph) pruneCodeState(s.graph)
  }), [])
  useEffect(() => {
    // A test that stubs the diagnostics module may leave this out.
    try { return followValidateAnswers() } catch { return undefined }
  }, [])
  const enabled = useCodeStore(s => s.enabled)
  const dismissed = useCodeStore(s => s.bannerDismissed)
  const wanted = useCodeStore(s => s.bannerWanted)
  const graph = useNodeBuilderStore(s => s.graph)
  const all = useDiagnostics().diagnostics
  const ids = useMemo(() => {
    const fromServer = [...new Set((all ?? []).filter(d => d.code === 'code_disabled' && d.node_id).map(d => d.node_id as string))]
    return fromServer.length ? fromServer : codeNodeIds(graph)
  }, [all, graph])
  const key = ids.join('|')

  useEffect(() => {
    const show = !enabled && !dismissed && (ids.length > 0 || wanted)
    if (!show) { dismissNotice(CODE_BANNER_KEY); return }
    const dismiss = () => useCodeStore.setState({ bannerDismissed: true, bannerWanted: false })
    pushNotice({
      key: CODE_BANNER_KEY,
      severity: 'warn',
      role: 'status',
      sticky: true,
      text: CODE_BANNER_TEXT,
      onDismiss: dismiss,
      actions: [
        ...(ids.length > 0 ? [{
          label: showCodeNodesText(ids.length),
          testId: 'nb-code-banner-show',
          run: () => {
            const s = useNodeBuilderStore.getState()
            const present = ids.filter(id => s.graph && id in s.graph.nodes)
            if (present.length === 0) return
            s.setSelection({ nodeIds: present, primary: present[0] })
            runCommand('view.frameSelection')
          },
        }] : []),
        { label: 'Dismiss', run: () => { dismiss(); dismissNotice(CODE_BANNER_KEY) }, testId: 'nb-code-banner-dismiss' },
      ],
    })
    // `key` stands for `ids`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, dismissed, wanted, key])

  useEffect(() => () => dismissNotice(CODE_BANNER_KEY), [])
}

/** True while code is off and this graph has code: Run is disabled with the tooltip (S49). */
export function useCodeBlocksRun(): boolean {
  const enabled = useCodeStore(s => s.enabled)
  const has = useNodeBuilderStore(s => !enabled && hasCode(s.graph))
  return has
}
