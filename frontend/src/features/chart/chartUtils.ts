export function toLineData(arr: { time: string; value: number | null }[], toET: (t: any) => any) {
  return arr.map(d => d.value !== null
    ? { time: toET(d.time as any) as any, value: d.value as number }
    : { time: toET(d.time as any) as any }
  )
}
