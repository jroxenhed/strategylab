/**
 * The toolbar's Inspector toggle (`▥`, foundation 3.1, A1: last-but-one in
 * the right cluster). Pressed while the panel is open. `P` does the same
 * (commands/inspector.ts).
 */

import { Button } from '../ui/Button'
import { toggleInspector, useInspectorShown } from './state'

export default function InspectorToggle() {
  const open = useInspectorShown()
  return (
    <Button
      kind="icon"
      pressed={open}
      aria-label="Inspector"
      title="Inspector (P)"
      data-testid="nb-btn-inspector"
      data-nb-inspector-toggle=""
      onClick={() => toggleInspector()}
    >
      ▥
    </Button>
  )
}
