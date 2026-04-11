interface Props {
  values: number[]
  width?: number
  height?: number
}

export default function PnlHistogram({ values, width = 220, height = 60 }: Props) {
  if (values.length === 0) {
    return <div style={{ width, height, color: '#484f58', fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>no trades</div>
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const bucketCount = Math.min(20, Math.max(5, Math.floor(Math.sqrt(values.length))))
  const bucketSize = range / bucketCount
  const buckets = new Array(bucketCount).fill(0)
  for (const v of values) {
    const idx = Math.min(bucketCount - 1, Math.floor((v - min) / bucketSize))
    buckets[idx]++
  }
  const tallest = Math.max(...buckets) || 1
  const barWidth = width / bucketCount
  const zeroX = min < 0 && max > 0 ? ((0 - min) / range) * width : null

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {buckets.map((count, i) => {
        const bucketStart = min + i * bucketSize
        const bucketEnd = bucketStart + bucketSize
        const isLoss = bucketEnd <= 0
        const isGain = bucketStart >= 0
        const color = isLoss ? '#f85149' : isGain ? '#26a641' : '#8b949e'
        const h = (count / tallest) * (height - 4)
        return (
          <rect
            key={i}
            x={i * barWidth + 0.5}
            y={height - h}
            width={Math.max(1, barWidth - 1)}
            height={h}
            fill={color}
            opacity={0.85}
          />
        )
      })}
      {zeroX != null && (
        <line x1={zeroX} y1={0} x2={zeroX} y2={height} stroke="#30363d" strokeWidth={1} strokeDasharray="2,2" />
      )}
    </svg>
  )
}
