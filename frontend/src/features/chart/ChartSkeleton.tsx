const SKELETON_HEIGHTS = [35, 55, 45, 70, 50, 40, 65, 80, 60, 45, 55, 70, 40, 50, 65, 75, 55, 45, 60, 50]

export default function ChartSkeleton({ ticker }: { ticker: string }) {
  return (
    <div className="chart-skeleton">
      <div className="chart-skeleton-bars">
        {SKELETON_HEIGHTS.map((h, i) => (
          <div key={i} className="chart-skeleton-bar" style={{ height: `${h}%` }} />
        ))}
      </div>
      <div className="chart-skeleton-axis" />
      <div className="chart-skeleton-label">Loading {ticker}…</div>
    </div>
  )
}
