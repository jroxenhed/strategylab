/**
 * The toolbar's "Tidy" button (F453): order 11, right after Reset view (10)
 * in the right cluster. It runs the Tidy layout command (key L).
 */

import { registerSlot } from '../slots'
import { TidyButton } from '../HintBar'

registerSlot('toolbarRight', 'tidy', TidyButton, 11)
