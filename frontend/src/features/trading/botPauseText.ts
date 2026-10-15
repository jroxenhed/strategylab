/**
 * Sentences the bot card shows for code pauses and code failures (F435 W7,
 * S49). Kept out of the component files so they export only components.
 */

/** The S49 sentence for a bot paused because code is off on the server. */
export const CODE_DISABLED_PAUSE_TEXT = 'Paused: code nodes are disabled on this server. Turn SL_CODE_NODES back on and restart the bot.'

/**
 * A bot whose code failed while it held a position (John's open position
 * rule): no entries and no signal exits, only the price exits (stop,
 * trailing stop, time stop) until the position is flat, then it pauses.
 */
export const CODE_EXITS_ONLY_TEXT = 'Code failed: managing exits until flat'

/** The longer explanation, shown as the row's tooltip. */
export const CODE_EXITS_ONLY_TITLE = 'The bot places no entries and no signal exits. Its stop, trailing stop and time stop keep running until the position closes, then the bot pauses.'
