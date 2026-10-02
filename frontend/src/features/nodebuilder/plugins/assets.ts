/**
 * Puts asset authoring in the builder (W6 item 6.D, specs S39 to S43):
 * - the Promote popover, Save as asset and the Asset Manager in the
 *   `dialogs` slot;
 * - the asset toast in the `overlays` slot;
 * - the Inspector's Parameters section for network nodes (the Promoted
 *   list), registered under the built-in id `parameters`, which it
 *   overrides; other nodes still get the built-in section.
 *
 * - the library as the source of locked instances' children, fetched when
 *   a locked instance is on screen (assetNetworks.ts).
 *
 * This lives in plugins/ only because canvasPlugins.ts loads every module
 * here before the builder first renders. It adds no canvas hooks. The
 * commands (Shift+C, Promote, Save as asset, Cmd+Shift+A) are in
 * commands/collapse.ts and commands/assets.ts.
 */

import { AssetManagerHost } from '../AssetManager'
import { installAssetNetworks } from '../assetNetworks'
import { AssetToastHost } from '../AssetToast'
import { registerInspectorSection } from '../inspector/sections'
import { ParametersWithPromoted, ParametersWithPromotedCount } from '../PromotedSection'
import { PromoteDialogHost } from '../PromoteDialog'
import { SaveAssetDialogHost } from '../SaveAssetDialog'
import { registerSlot } from '../slots'
// The built-in sections must be registered first, so this one overrides.
import '../inspector/NodeSections'

registerSlot('dialogs', 'promoteParam', PromoteDialogHost, 60)
registerSlot('dialogs', 'saveAsset', SaveAssetDialogHost, 61)
registerSlot('dialogs', 'assetManager', AssetManagerHost, 62)
registerSlot('overlays', 'assetToast', AssetToastHost, 70)

const removeSection = registerInspectorSection({
  id: 'parameters',
  title: 'Parameters',
  order: 10,
  Component: ParametersWithPromoted,
  Count: ParametersWithPromotedCount,
})
if (import.meta.hot) import.meta.hot.dispose(removeSection)

const uninstallAssetNetworks = installAssetNetworks()
if (import.meta.hot) import.meta.hot.dispose(uninstallAssetNetworks)
