import type { Trade } from '../../shared/types'

export interface StreakStats {
  maxConsecWins: number
  maxConsecLosses: number
  avgWinStreak: number
  avgLossStreak: number
  winStreaks: number[]      // lengths of each win streak
  lossStreaks: number[]     // lengths of each loss streak
}

export function computeStreakStats(trades: Trade[]): StreakStats {
  const sells = trades.filter(t => t.type === 'sell' || t.type === 'cover')

  const winStreaks: number[] = []
  const lossStreaks: number[] = []
  let curWin = 0
  let curLoss = 0

  for (const trade of sells) {
    const isWin = (trade.pnl ?? 0) > 0
    if (isWin) {
      if (curLoss > 0) { lossStreaks.push(curLoss); curLoss = 0 }
      curWin++
    } else {
      if (curWin > 0) { winStreaks.push(curWin); curWin = 0 }
      curLoss++
    }
  }
  if (curWin > 0) winStreaks.push(curWin)
  if (curLoss > 0) lossStreaks.push(curLoss)

  const avg = (arr: number[]) => arr.length === 0 ? 0 : arr.reduce((a, b) => a + b, 0) / arr.length

  return {
    maxConsecWins: winStreaks.length === 0 ? 0 : Math.max(...winStreaks),
    maxConsecLosses: lossStreaks.length === 0 ? 0 : Math.max(...lossStreaks),
    avgWinStreak: avg(winStreaks),
    avgLossStreak: avg(lossStreaks),
    winStreaks,
    lossStreaks,
  }
}
