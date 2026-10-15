/**
 * Tab and Shift+Tab out of a one-line code field (F435 W7, S44): the field
 * behaves like an ordinary input, so Tab moves to the next focusable element
 * in document order instead of writing spaces.
 */

const TABBABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',')

/**
 * Focus the next (or previous) focusable element outside `from`. Returns
 * false when there is none (focus stays where it is).
 */
export function focusNeighbour(from: HTMLElement, backwards: boolean): boolean {
  const doc = from.ownerDocument
  // A browser gives hidden elements no client rects; jsdom gives none to any
  // element, so the check only runs where there is layout.
  const hasLayout = doc.body.getClientRects().length > 0
  const all = Array.from(doc.querySelectorAll<HTMLElement>(TABBABLE))
    .filter(el => !from.contains(el) && !el.contains(from) && el.tabIndex >= 0 && (!hasLayout || el.getClientRects().length > 0))
  const follows = (el: HTMLElement) => (from.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
  const target = backwards
    ? [...all].reverse().find(el => !follows(el))
    : all.find(follows)
  if (!target) return false
  target.focus()
  return true
}
