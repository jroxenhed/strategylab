/**
 * `graph.spawnBots`: open the Spawn bots dialog (S34). No key; the toolbar
 * button and menus run it by id. Only an editable graph can spawn (the
 * dialog itself says when the graph must be saved first).
 * Loaded by the commands/ auto-registry.
 */

import { openSpawnDialog } from '../spawnUi'
import type { Command } from './index'

export const commands: Command[] = [
  {
    id: 'graph.spawnBots',
    label: 'Spawn bots…',
    scope: 'global',
    when: s => s.graph != null && !s.graph.readOnly,
    disabledReason: s => (s.graph != null && !s.graph.readOnly ? null : 'Open a graph to edit first'),
    run: () => { openSpawnDialog() },
  },
]
