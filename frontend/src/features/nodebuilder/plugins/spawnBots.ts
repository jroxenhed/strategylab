/**
 * Puts bot spawning in the builder (S34, plan W5 item 5.F):
 * - `Spawn bots…` in the toolbar's right cluster at order 50 (S01 order:
 *   after Run 40, before Save 60).
 * - The Spawn bots dialog in the `dialogs` slot.
 * - Its "Created N stopped bots" toast in the `overlays` slot.
 * - A window listener for the bot card's `Spawn…` link, so the dialog opens
 *   once App has opened that graph (graphLinks.ts).
 *
 * This lives in plugins/ only because canvasPlugins.ts loads every module
 * here before the builder first renders. It adds no canvas hooks. The
 * command form (`graph.spawnBots`) is in commands/spawn.ts.
 */

import { SpawnBotsButton, SpawnBotsDialogHost, SpawnToastHost } from '../SpawnBotsDialog'
import { listenForSpawnRequests } from '../spawnUi'
import { registerSlot } from '../slots'

registerSlot('toolbarRight', 'spawnBots', SpawnBotsButton, 50)
registerSlot('dialogs', 'spawnBots', SpawnBotsDialogHost, 50)
registerSlot('overlays', 'spawnToast', SpawnToastHost, 60)

// Once per page; a hot reload of this module replaces the old listener.
const remove = listenForSpawnRequests()
if (import.meta.hot) import.meta.hot.dispose(remove)
