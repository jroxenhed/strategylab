/**
 * Puts the W4 workspace controls in the graph toolbar (S27, S25): the
 * `Auto cook` switch (order 20, after Reset view, A1) and the Data Sheet
 * toggle (order 91, beside the Inspector toggle at 90: panel toggles last).
 *
 * This lives in plugins/ only because canvasPlugins.ts loads every module
 * here before the builder first renders. It adds no canvas hooks. The keys
 * (A, S, Shift+V) are in commands/graphWorkspace.ts.
 */

import AutoCookToggle from '../AutoCookToggle'
import { SheetToggle } from '../GraphChartSplit'
import { registerSlot } from '../slots'

registerSlot('toolbarRight', 'autocook', AutoCookToggle, 20)
registerSlot('toolbarRight', 'sheetToggle', SheetToggle, 91)
