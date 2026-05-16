import { useEffect, useRef, useCallback } from 'react'
import { createChart, BaselineSeries, type IChartApi, type ISeriesApi } from 'lightweight-charts'

interface Props {
  equityData: { time: string; value: number }[]
  alignedRange?: { from: number; to: number }
  height?: number
}

/** ET-aware date/time formatter. Shows time if data spans < 3 days, date-only otherwise. */
function formatTooltipTime(unixSec: number, showTime: boolean): string {
  const d = new Date(unixSec * 1000)
  if (showTime) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
    }).format(d)
  }
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    month: 'short', day: 'numeric',
  }).format(d)
}

const THREE_DAYS_SEC = 3 * 24 * 60 * 60

export default function MiniSparkline({ equityData, alignedRange, height = 60 }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const lastSigRef = useRef<string>('')
  const tooltipRef = useRef<HTMLDivElement>(null)
  /** Tracks whether data spans < 3 days so tooltip can show time vs date-only */
  const showTimeRef = useRef<boolean>(false)

  const applyRange = () => {
    const chart = chartRef.current
    if (!chart) return
    try { chart.timeScale().fitContent() } catch {}
  }

  const handleCrosshairMove = useCallback((param: any) => {
    const tooltip = tooltipRef.current
    const container = ref.current
    if (!tooltip || !container) return

    if (!param || !param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
      tooltip.style.display = 'none'
      return
    }

    const series = seriesRef.current
    if (!series) { tooltip.style.display = 'none'; return }

    const data = param.seriesData?.get(series)
    if (!data || data.value == null) { tooltip.style.display = 'none'; return }

    const timeStr = formatTooltipTime(param.time as number, showTimeRef.current)
    const valStr = '$' + data.value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    tooltip.textContent = `${timeStr}  ${valStr}`
    tooltip.style.display = 'block'

    // Position: above cursor, horizontally clamped within container
    const cw = container.clientWidth
    const tw = tooltip.offsetWidth
    let left = param.point.x - tw / 2
    if (left < 2) left = 2
    if (left + tw > cw - 2) left = cw - tw - 2
    tooltip.style.left = `${left}px`
    tooltip.style.top = `${Math.max(0, param.point.y - 28)}px`
  }, [])

  // Mount once: create chart + series. autoSize observes the container — never
  // pair with an external ResizeObserver + applyOptions (F218 trap).
  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#aaa' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false },
      timeScale: { visible: false, timeVisible: true, secondsVisible: true },
      crosshair: {
        horzLine: { visible: false },
        vertLine: {
          visible: true,
          color: 'rgba(255,255,255,0.3)',
          style: 3, // Dashed
          width: 1,
          labelVisible: false,
        },
      },
      handleScroll: false,
      handleScale: false,
    })
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#26a69a',
      topFillColor1: 'rgba(38,166,154,0.2)',
      topFillColor2: 'rgba(38,166,154,0.02)',
      bottomLineColor: '#ef5350',
      bottomFillColor1: 'rgba(239,83,80,0.02)',
      bottomFillColor2: 'rgba(239,83,80,0.2)',
      lineWidth: 1,
      priceScaleId: 'right',
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 3,
    })
    chartRef.current = chart
    seriesRef.current = series
    lastSigRef.current = ''

    chart.subscribeCrosshairMove(handleCrosshairMove)

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      lastSigRef.current = ''
    }
  }, [])

  // Data updates: skip setData when the series hasn't meaningfully changed.
  useEffect(() => {
    const series = seriesRef.current
    if (!series || equityData.length < 2) return
    const mapped = equityData
      .map(d => ({ time: Math.floor(new Date(d.time).getTime() / 1000), value: d.value }))
      .sort((a, b) => a.time - b.time)
      .filter((d, i, arr) => i === 0 || d.time > arr[i - 1].time)
    if (mapped.length < 2) return
    const first = mapped[0]
    const last = mapped[mapped.length - 1]

    // Track whether data spans < 3 days for tooltip formatting
    showTimeRef.current = (last.time - first.time) < THREE_DAYS_SEC

    // Pad with whitespace entries at the aligned union boundaries so every
    // bot's time axis spans the same range. Using whitespace + fitContent
    // avoids setVisibleRange's ensureNotNull throw when the requested range
    // extends beyond the series' real data.
    const padded: { time: number; value?: number }[] = [...mapped]
    if (alignedRange && alignedRange.to > alignedRange.from) {
      if (alignedRange.from < first.time) padded.unshift({ time: alignedRange.from })
      if (alignedRange.to > last.time) padded.push({ time: alignedRange.to })
    }
    const sig = `${padded.length}|${padded[0].time}|${padded[padded.length - 1].time}|${last.value}`
    if (sig === lastSigRef.current) return
    lastSigRef.current = sig
    series.setData(padded as any)
    applyRange()
  }, [equityData, alignedRange?.from, alignedRange?.to])

  return (
    <div ref={ref} style={{ width: '100%', height, position: 'relative' }}>
      <div
        ref={tooltipRef}
        style={{
          display: 'none',
          position: 'absolute',
          zIndex: 10,
          pointerEvents: 'none',
          background: 'rgba(0,0,0,0.85)',
          color: '#fff',
          fontSize: '11px',
          lineHeight: '16px',
          padding: '3px 6px',
          borderRadius: '4px',
          whiteSpace: 'nowrap',
        }}
      />
    </div>
  )
}
