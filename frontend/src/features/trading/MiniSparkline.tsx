import { useEffect, useRef } from 'react'
import { createChart, BaselineSeries } from 'lightweight-charts'

interface Props {
  equityData: { time: string; value: number }[]
  alignedRange?: { from: number; to: number }
}

export default function MiniSparkline({ equityData, alignedRange }: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current || equityData.length < 2) return
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
    const mapped = equityData
      .map(d => ({ time: Math.floor(new Date(d.time).getTime() / 1000), value: d.value }))
      .sort((a, b) => a.time - b.time)
      .filter((d, i, arr) => i === 0 || d.time > arr[i - 1].time) as any
    series.setData(mapped)

    const applyRange = () => {
      if (alignedRange && alignedRange.to > alignedRange.from) {
        chart.timeScale().setVisibleRange({
          from: alignedRange.from as any,
          to: alignedRange.to as any,
        })
      } else {
        chart.timeScale().fitContent()
      }
    }
    applyRange()

    const ro = new ResizeObserver(() => {
      if (!ref.current) return
      chart.applyOptions({ width: ref.current.clientWidth })
      applyRange()
    })
    ro.observe(ref.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [equityData, alignedRange?.from, alignedRange?.to])

  if (equityData.length < 2) return null
  return <div ref={ref} style={{ width: '100%', height: 60 }} />
}
