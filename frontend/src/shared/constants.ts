/**
 * Shared constants used across multiple feature modules.
 * Keep framework-agnostic (no React imports here).
 */

/**
 * Intraday intervals that can be deployed as a live bot.
 * Source of truth for both StrategyBuilder (deploy warning) and AddBotBar (interval picker).
 * Add new intervals here; both files update automatically.
 */
export const BOT_DEPLOYABLE_INTERVALS = ['1m', '5m', '15m', '30m', '1h'] as const
export type BotDeployableInterval = typeof BOT_DEPLOYABLE_INTERVALS[number]
