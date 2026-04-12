import { useEffect, useRef } from 'react'
import { createChart, BaselineSeries, LineSeries, HistogramSeries, ColorType, LineType } from 'lightweight-charts'
import type { MacroCurvePoint } from '../../shared/types'

interface Props {
  macroCurve: MacroCurvePoint[]
  initialCapital: number
}

export default function MacroEquityChart({ macroCurve, initialCapital }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || macroCurve.length === 0) return

    const chart = createChart(containerRef.current, {
      height: containerRef.current.clientHeight || 250,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
      crosshair: { mode: 0 },
    })

    // 1. Close line — BaselineSeries (green above initial capital, red below)
    const closeSeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: initialCapital },
      topLineColor: '#26a641',
      bottomLineColor: '#f85149',
      topFillColor1: 'rgba(38, 166, 65, 0.1)',
      topFillColor2: 'rgba(38, 166, 65, 0)',
      bottomFillColor1: 'rgba(248, 81, 73, 0)',
      bottomFillColor2: 'rgba(248, 81, 73, 0.1)',
      lineWidth: 2,
      priceScaleId: 'right',
    })
    closeSeries.setData(
      macroCurve.map(b => ({ time: b.time as any, value: b.close }))
    )

    // 2. High/low stepped lines with drawdown-based coloring
    const ddColor = (pct: number): string => {
      const severity = Math.min(1, Math.abs(pct) / 20)
      const r = Math.round(88 + (248 - 88) * severity)
      const g = Math.round(166 + (81 - 166) * severity)
      const bVal = Math.round(255 + (73 - 255) * severity)
      return `rgba(${r}, ${g}, ${bVal}, 0.5)`
    }

    const highSeries = chart.addSeries(LineSeries, {
      lineWidth: 1,
      lineType: LineType.WithSteps,
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    highSeries.setData(
      macroCurve.map(b => ({ time: b.time as any, value: b.high, color: ddColor(b.drawdown_pct) }))
    )

    const lowSeries = chart.addSeries(LineSeries, {
      lineWidth: 1,
      lineType: LineType.WithSteps,
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    lowSeries.setData(
      macroCurve.map(b => ({ time: b.time as any, value: b.low, color: ddColor(b.drawdown_pct) }))
    )

    // 3. Trade density ticks — histogram at chart bottom
    const allPnls = macroCurve.flatMap(b => b.trades.map(t => t.pnl))
    const maxPnl = Math.max(...allPnls.map(Math.abs), 1)

    const tickData = macroCurve
      .filter(b => b.trades.length > 0)
      .map(b => {
        const netPnl = b.trades.reduce((sum, t) => sum + t.pnl, 0)
        const intensity = 0.3 + 0.7 * Math.min(1, Math.abs(netPnl) / maxPnl)
        return {
          time: b.time as any,
          value: b.trades.length,
          color: netPnl >= 0
            ? `rgba(38, 166, 65, ${intensity})`
            : `rgba(248, 81, 73, ${intensity})`,
        }
      })

    if (tickData.length > 0) {
      const tickSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'trade-ticks',
        base: 0,
        lastValueVisible: false,
        priceLineVisible: false,
      })
      chart.priceScale('trade-ticks').applyOptions({
        visible: false,
        scaleMargins: { top: 0.9, bottom: 0 },
      })
      tickSeries.setData(tickData)
    }

    // Fit full range — no sync with main chart
    chart.timeScale().fitContent()

    // 4. Crosshair tooltip
    const tooltip = tooltipRef.current
    chart.subscribeCrosshairMove(param => {
      if (!tooltip) return
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = 'none'
        return
      }
      const bucket = macroCurve.find(b => b.time === param.time)
      if (!bucket) {
        tooltip.style.display = 'none'
        return
      }
      const returnPct = bucket.open !== 0
        ? ((bucket.close - bucket.open) / bucket.open * 100).toFixed(2)
        : '0.00'
      const returnColor = Number(returnPct) >= 0 ? '#26a641' : '#f85149'

      tooltip.style.display = 'block'
      tooltip.style.left = `${param.point.x + 16}px`
      tooltip.style.top = `${param.point.y - 10}px`
      tooltip.innerHTML = `
        <div style="font-weight:600;margin-bottom:4px">${bucket.time}</div>
        <div>Open: $${bucket.open.toLocaleString()}</div>
        <div>Close: $${bucket.close.toLocaleString()}</div>
        <div>High: $${bucket.high.toLocaleString()}</div>
        <div>Low: $${bucket.low.toLocaleString()}</div>
        <div style="color:${returnColor}">Return: ${Number(returnPct) > 0 ? '+' : ''}${returnPct}%</div>
        <div>DD: ${bucket.drawdown_pct.toFixed(2)}%</div>
        <div>Trades: ${bucket.trades.length}</div>
      `
    })

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      chart.remove()
      ro.disconnect()
    }
  }, [macroCurve, initialCapital])

  return (
    <div style={{ position: 'relative', width: '100%', height: 250, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div
        ref={tooltipRef}
        style={{
          display: 'none',
          position: 'absolute',
          top: 0,
          left: 0,
          padding: '8px 12px',
          background: 'rgba(22, 27, 34, 0.95)',
          border: '1px solid #30363d',
          borderRadius: 6,
          color: '#e6edf3',
          fontSize: 11,
          lineHeight: 1.5,
          pointerEvents: 'none',
          zIndex: 10,
          whiteSpace: 'nowrap',
        }}
      />
    </div>
  )
}
