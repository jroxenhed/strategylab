/**
 * Puts the Inspector (F272, S14/S15) in the builder: the panel in the
 * `rightPanel` slot and its toggle in `toolbarRight` (order 90: panel
 * toggles come last, A1).
 *
 * This lives in plugins/ only because canvasPlugins.ts loads every module
 * here before the builder first renders. It adds no canvas hooks. The
 * Inspector's keys (P, F2) are in commands/inspector.ts.
 */

import Inspector from '../Inspector'
import InspectorToggle from '../inspector/InspectorToggle'
import { registerSlot } from '../slots'

registerSlot('rightPanel', 'inspector', Inspector, 10)
registerSlot('toolbarRight', 'inspectorToggle', InspectorToggle, 90)
