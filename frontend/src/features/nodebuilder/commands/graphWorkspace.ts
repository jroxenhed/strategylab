/**
 * Workspace keys for W4 (S25, S27, S28):
 * - `A` toggles auto cook (`cook.toggleAuto`, from AutoCookToggle.tsx).
 * - `S` opens and closes the Data Sheet (`panels.toggleSheet`).
 * - `Shift+V` opens and closes the chart panel (`panels.toggleChart`).
 *
 * The panel toggles also work on the read-only strategy view: they change
 * what is shown, never the graph. Loaded by the commands/ auto-registry.
 */

import { autoCookCommands } from '../AutoCookToggle'
import { toggleSheet, useSheetUi } from '../datasheet/sheetUi'
import { isChartOpen, toggleChartPanel } from '../graphSplitState'
import type { Command } from './index'

export const commands: Command[] = [
  ...autoCookCommands,
  {
    id: 'panels.toggleSheet',
    label: 'Data sheet',
    keys: ['s'],
    readOnlyOk: true,
    checked: () => useSheetUi.getState().open,
    run: () => { toggleSheet() },
  },
  {
    id: 'panels.toggleChart',
    label: 'Chart',
    keys: ['shift+v'],
    readOnlyOk: true,
    checked: () => isChartOpen(),
    run: () => toggleChartPanel(),
  },
]
