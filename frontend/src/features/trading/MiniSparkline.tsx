import { useEffect, useRef } from 'react'
import { createChart, BaselineSeries, type IChartApi, type ISeriesApi } from 'lightweight-charts'

interface Props {
  equityData: { time: string; value: number }[]
  alignedRange?: { from: number; to: number }
}

export default function MiniSparkline({ equityData, alignedRange }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const lastSigRef = useRef<string>('')

  const applyRange = () => {
    const chart = chartRef.current
    if (!chart) return
    try { chart.timeScale().fitContent() } catch {}
  }

  // Mount once: create chart + series + ResizeObserver.
  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: 60,
      layout: { background: { color: 'transparent' }, textColor: '#aaa' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false },
      timeScale: { visible: false, timeVisible: true, secondsVisible: true },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
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
    })
    chartRef.current = chart
    seriesRef.current = series
    lastSigRef.current = ''

    const ro = new ResizeObserver(() => {
      const el = ref.current
      const c = chartRef.current
      if (!el || !c) return
      c.applyOptions({ width: el.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
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

  return <div ref={ref} style={{ width: '100%', height: 60 }} />
}
