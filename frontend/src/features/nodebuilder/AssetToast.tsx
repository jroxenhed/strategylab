/**
 * The asset toast (shared W5-W7 rules): 36px, bottom centre of the canvas
 * column, hides after 4 s, one action link at most. Shows the collapse
 * result (S39) and "Saved … to the library" (S41). Mounted in the
 * `overlays` slot by plugins/assets.ts.
 */

import { useEffect } from 'react'
import { hideAssetToast, useAssetUi } from './assetUi'
import './assets.css'

/** How long the toast stays. */
export const ASSET_TOAST_MS = 4000

export function AssetToastHost() {
  const toast = useAssetUi(s => s.toast)
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => hideAssetToast(toast.seq), ASSET_TOAST_MS)
    return () => clearTimeout(t)
  }, [toast])
  if (!toast) return null
  return (
    <div className="nb-asset-toast" role="status" data-testid="nb-asset-toast">
      <span>{toast.text}</span>
      {toast.action && (
        <button
          type="button"
          onClick={() => {
            hideAssetToast(toast.seq)
            toast.action!.run()
          }}
        >
          {toast.action.label}
        </button>
      )}
    </div>
  )
}
