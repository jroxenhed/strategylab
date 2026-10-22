/**
 * Y + drag cuts wires (F457, Houdini). The gesture needs a key and a press
 * that React Flow must never see, which the plugin hooks do not give, so
 * this module puts WireCut.tsx in the `overlays` slot; it listens on the
 * window and draws the cut line there.
 *
 * Loaded by the plugins/ auto-loader (canvasPlugins.ts).
 */

import type { CanvasPlugin } from '../canvasPlugins'
import { registerSlot } from '../slots'
import WireCut from '../WireCut'

registerSlot('overlays', 'wireCut', WireCut, 20)

export const plugin: CanvasPlugin = { id: 'wireCut' }
