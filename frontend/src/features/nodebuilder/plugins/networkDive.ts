/**
 * Network cards and dive (W6 item 6.C, specs S37, S38).
 *
 * Loads the renderers, so the `nbSubnet` (network card) and `nbBoundary`
 * (boundary card inside a dived network) node types exist before the
 * canvas first draws. The keys live in commands/network.ts; the canvas
 * itself handles the frame-tab double-click and draws only the network on
 * screen (rfMapping.ts).
 */

import type { CanvasPlugin } from '../canvasPlugins'
import '../nodes/SubnetNode'
import '../nodes/BoundaryNode'

export const plugin: CanvasPlugin = { id: 'networkDive' }
