export function normaliseToPercent(
  data: { time: any; value: number }[],
): { time: any; value: number; dollar: number }[] {
  if (data.length === 0) return []
  const first = data[0].value
  if (first === 0) return data.map(d => ({ time: d.time, value: 0, dollar: d.value }))
  return data.map(d => ({
    time: d.time,
    value: ((d.value - first) / first) * 100,
    dollar: d.value,
  }))
}

// Offsets percent series by 100 before log to avoid log(negative).
export function applyLog(
  data: { time: any; value: number; dollar?: number }[],
  isNormalised: boolean,
): { time: any; value: number; dollar?: number }[] {
  return data.map(d => ({
    ...d,
    value: isNormalised
      ? Math.log10(Math.max(100 + d.value, 0.01))
      : Math.log10(Math.max(d.value, 0.01)),
  }))
}
