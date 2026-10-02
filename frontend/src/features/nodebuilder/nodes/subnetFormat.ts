/**
 * Text and constants for the network card and boundary cards (S38), kept
 * out of the components so they stay plain and testable.
 */

import type { CardInfo } from '../rfMapping'

/** Header glyph per network type. */
export const CARD_GLYPH: Record<string, { glyph: string; color: string }> = {
  subnet: { glyph: 'N', color: 'var(--nb-cat-network)' },
  output_group: { glyph: 'O', color: 'var(--nb-cat-output)' },
  regime_net: { glyph: 'R', color: 'var(--nb-cat-network)' },
}

/** The word for a network type in the type slot. */
function typeWord(type: string): string {
  if (type === 'output_group') return 'group'
  if (type === 'regime_net') return 'regime'
  return 'subnet'
}

/**
 * The card's type slot (S38 copy): `subnet · 7 nodes`, `subnet · empty`,
 * `regime_filter @ v3` (locked asset), `regime_filter v3 · local copy`
 * (unlocked), `regime_filter @ v3 · missing`. `noOutput`: add `· no output`
 * (a subnet with no `subnet_output`; a group never has one).
 */
export function cardTypeText(opts: { type: string; card: CardInfo; missing?: boolean }): { text: string; noOutput: boolean } {
  const { type, card, missing } = opts
  const noOutput = type !== 'output_group' && card.outputId === null && !card.asset?.locked
  const a = card.asset
  if (a) {
    if (missing) return { text: `${a.name} @ v${a.version} · missing`, noOutput: false }
    if (a.locked) return { text: `${a.name} @ v${a.version}`, noOutput: false }
    return { text: `${a.name} v${a.version} · local copy`, noOutput }
  }
  const word = typeWord(type)
  if (card.childCount === 0) return { text: `${word} · empty`, noOutput }
  return { text: `${word} · ${card.childCount} ${card.childCount === 1 ? 'node' : 'nodes'}`, noOutput }
}

/** The card's aria-label (S38). */
export function cardAriaLabel(opts: { name: string; card: CardInfo }): string {
  const { name, card } = opts
  if (card.asset) return `Asset ${card.asset.name} version ${card.asset.version}${card.asset.locked ? ', locked' : ''}`
  const ins = card.inputs.length
  const outs = card.outputId ? 1 : 0
  return `Subnet ${name}, ${card.childCount} ${card.childCount === 1 ? 'node' : 'nodes'}, ${ins} ${ins === 1 ? 'input' : 'inputs'}, ${outs} ${outs === 1 ? 'output' : 'outputs'}`
}

/** A boundary card's type slot (S38): `input · 5 attrs`, `output · 7 attrs`, or the bare word before /validate answers. */
export function boundaryTypeText(kind: 'input' | 'output', attrs: number | null): string {
  if (attrs === null) return kind
  return `${kind} · ${attrs} ${attrs === 1 ? 'attr' : 'attrs'}`
}
