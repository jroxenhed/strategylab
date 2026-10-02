/**
 * AutoCookToggle: the toolbar's `Auto cook` switch (spec S27, S01).
 *
 * On: every edit refreshes the node sparklines and the Data Sheet through
 * the preview cook (`useAutoCook`). It never runs the backtest (A4). The
 * value lives in the store's status slice and is kept in `nb.autocook`
 * (A5), default on.
 *
 * Item 4.D mounts it in the `toolbarRight` slot at order 20. Read-only
 * graphs do not show it (S01). `toggleAutoCook` is the shared action for
 * the `A` key (`cook.toggleAuto`, exported below as `autoCookCommands`) and
 * the status-bar segment.
 */

import type { Command } from './commands'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import { Switch } from './ui/Switch'

/** Flip auto cook and say so in the status bar (S27). */
export function toggleAutoCook(): void {
  const s = useNodeBuilderStore.getState()
  const on = !s.autoCook
  s.setAutoCook(on)
  s.showFlash(on ? 'auto cook on' : 'auto cook off')
}

/** The `A` key (S27). A commands module re-exports this list to register it. */
export const autoCookCommands: Command[] = [{
  id: 'cook.toggleAuto',
  label: 'Auto cook',
  keys: ['a'],
  when: (s: NodeBuilderState) => s.graph != null && !s.graph.readOnly,
  checked: (s: NodeBuilderState) => s.autoCook,
  run() { toggleAutoCook() },
}]

const selectShown = (s: NodeBuilderState) => s.graph != null && !s.graph.readOnly
const selectAutoCook = (s: NodeBuilderState) => s.autoCook

export function AutoCookToggle() {
  const shown = useNodeBuilderStore(selectShown)
  const on = useNodeBuilderStore(selectAutoCook)
  if (!shown) return null
  return (
    <span className="nb-autocook" style={{ display: 'inline-flex', alignItems: 'center', height: 26 }}>
      <Switch
        checked={on}
        onChange={next => {
          const s = useNodeBuilderStore.getState()
          if (next !== s.autoCook) toggleAutoCook()
        }}
        label="Auto cook"
        title="Cook node data after each edit (A)"
        data-testid="nb-autocook"
      />
    </span>
  )
}

export default AutoCookToggle
