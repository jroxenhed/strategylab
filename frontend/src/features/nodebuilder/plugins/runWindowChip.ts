/**
 * The toolbar's run window chip (F453, UX-2): "SPY · 5m", after the graph
 * name, the dot and the rev (left slot, order 60).
 */

import { registerSlot } from '../slots'
import { RunWindowChip } from '../RunWindowChip'

registerSlot('toolbarLeft', 'runWindow', RunWindowChip, 60)
