/**
 * Links between the Trading view and the node builder (S34, S35).
 *
 * The bot card and the spawn toast live in different tabs from the graph
 * they point at, and App owns the tabs. So they ask by a window event and
 * App does the switch:
 *
 * - `strategylab-open-graph` {graphId, group, spawn}: show the node builder
 *   with that graph open (the normal "Save changes?" prompt first when other
 *   work is unsaved). With `spawn: true` the Spawn bots dialog opens once
 *   that graph is loaded (SpawnBotsDialog listens for this part itself).
 * - `strategylab-open-trading` {botId}: show the Trading tab and scroll to
 *   that bot's card.
 * - `strategylab-show-graph-view`: the graph session sends it once the
 *   open-graph request got past the "Save changes?" prompt; App then shows
 *   the chart tab in graph view. A cancelled prompt sends nothing, so the
 *   view stays as it was (FE-08).
 *
 * Nothing here starts a bot.
 */

export const OPEN_GRAPH_EVENT = 'strategylab-open-graph'
export const OPEN_TRADING_EVENT = 'strategylab-open-trading'
export const SHOW_GRAPH_VIEW_EVENT = 'strategylab-show-graph-view'

export interface OpenGraphDetail {
  graphId: string
  /** The group frame to select, when known. */
  group: string | null
  /** Open the Spawn bots dialog after the graph loads. */
  spawn: boolean
}

export interface OpenTradingDetail {
  /** The bot card to scroll to, or null for just the tab. */
  botId: string | null
}

export function requestOpenGraph(detail: OpenGraphDetail): void {
  window.dispatchEvent(new CustomEvent<OpenGraphDetail>(OPEN_GRAPH_EVENT, { detail }))
}

export function requestOpenTrading(botId: string | null): void {
  window.dispatchEvent(new CustomEvent<OpenTradingDetail>(OPEN_TRADING_EVENT, { detail: { botId } }))
}

export function requestShowGraphView(): void {
  window.dispatchEvent(new Event(SHOW_GRAPH_VIEW_EVENT))
}
