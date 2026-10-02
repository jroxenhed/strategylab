/**
 * View commands (F435 W3 item 3.D): F frames the selection, H and Home
 * frame everything, G toggles snap-to-grid, and Space+drag pans.
 *
 * G follows the keyboard map (ui-ux-spec 6.2) and the pane menu (S19,
 * `Snap to grid G`), not the plan row's "H and G frame all".
 *
 * The work lives in viewOps.ts; this module only describes the commands.
 * It is loaded by commands/index.ts and imports that file for types only.
 * Nothing else may import this module (import viewOps.ts instead).
 */

import type { Command } from './index'
import { frameAll, frameSelection, hasNodesToFrame } from '../viewOps'

export const commands: Command[] = [
  {
    id: 'view.frameSelection',
    readOnlyOk: true,
    label: 'Frame selection',
    keys: ['f'],
    menu: 'node',
    when: hasNodesToFrame,
    disabledReason: s => (hasNodesToFrame(s) ? null : 'Nothing to frame'),
    run({ canvas }) {
      if (!canvas) return false
      frameSelection(canvas)
    },
  },
  {
    id: 'view.frameAll',
    readOnlyOk: true,
    label: 'Frame all',
    keys: ['h', 'home'],
    menu: 'pane',
    when: hasNodesToFrame,
    disabledReason: s => (hasNodesToFrame(s) ? null : 'Nothing to frame'),
    run({ canvas }) {
      if (!canvas) return false
      frameAll(canvas)
    },
  },
  {
    id: 'view.toggleSnap',
    readOnlyOk: true,
    label: 'Snap to grid',
    keys: ['g'],
    menu: 'pane',
    checked: s => s.snapToGrid,
    run({ store }) {
      const s = store.getState()
      s.toggleSnapToGrid()
      s.showFlash(store.getState().snapToGrid ? 'Snap to grid on' : 'Snap to grid off')
    },
  },
  {
    // Listed for the `?` overlay and the key check. React Flow pans by
    // itself while Space is held (panActivationKeyCode), so this never
    // handles the key: ' ' is what a Space press gives, 'space' is for
    // display.
    id: 'view.pan',
    label: 'Pan (hold and drag)',
    keys: ['space', ' '],
    run: () => false,
  },
]
