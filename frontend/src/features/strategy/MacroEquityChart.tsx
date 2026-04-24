import { useEffect, useRef } from 'react'
import { createChart, BaselineSeries, LineSeries, HistogramSeries, ColorType, LineType } from 'lightweight-charts'
import type { MacroCurvePoint } from '../../shared/types'
import { normaliseToPercent, applyLog } from '../../shared/utils/chartScale'

interface Props {
  macroCurve: MacroCurvePoint[]
  initialCapital: number
  showBaseline: boolean
  logScale: boolean
  baselineCurve?: { time: string | number; value: number | null }[]
}

export default function MacroEquityChart({ macroCurve, initialCapital, showBaseline, logScale, baselineCurve }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || macroCurve.length === 0) return

    const chart = createChart(containerRef.current, {
      height: containerRef.current.clientHeight || 250,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: {
        borderColor: '#30363d',
        mode: logScale && !showBaseline ? 1 : 0, // 1 = logarithmic (native), 0 = normal
      },
      crosshair: { mode: 0 },
    })

    // Prepare close data
    let closeData: { time: any; value: number; dollar?: number }[] = macroCurve.map(b => ({ time: b.time as any, value: b.close }))
    let baseValue = initialCapital

    let macroBaselineData: { time: any; value: number; dollar?: number }[] | null = null

    if (showBaseline) {
      closeData = normaliseToPercent(closeData as { time: any; value: number }[])
      baseValue = 0

      if (baselineCurve && baselineCurve.length > 0) {
        const baselineMap = new Map<string, number>()
        const isIntraday = typeof baselineCurve[0]?.time === 'number'
        for (const d of baselineCurve) {
          if (d.value === null) continue
          if (isIntraday) {
            const dt = new Date((d.time as number) * 1000)
            const key = `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`
            baselineMap.set(key, d.value)
          } else {
            baselineMap.set(String(d.time), d.value)
          }
        }
        const macroBaselineRaw: { time: any; value: number }[] = []
        let lastBaselineValue = initialCapital
        for (const b of macroCurve) {
          const v = baselineMap.get(b.time) ?? lastBaselineValue
          lastBaselineValue = v
          macroBaselineRaw.push({ time: b.time as any, value: v })
        }
        macroBaselineData = normaliseToPercent(macroBaselineRaw)
      }

      // For B&H + log: manually transform percentages (native log can't handle negatives)
      if (logScale) {
        closeData = applyLog(closeData, true)
        if (macroBaselineData) macroBaselineData = applyLog(macroBaselineData, true)
        baseValue = Math.log10(100)
      }
    }
    // Non-B&H log: native logarithmic price scale handles it (no data transform needed)

    const priceFormat = showBaseline
      ? logScale
        ? {
            type: 'custom' as const,
            formatter: (price: number) => {
              const pct = Math.pow(10, price) - 100
              return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
            },
          }
        : {
            type: 'custom' as const,
            formatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(1)}%`,
          }
      : undefined

    // 1. Close line — BaselineSeries
    const closeSeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: baseValue },
      topLineColor: '#26a641',
      bottomLineColor: '#f85149',
      topFillColor1: 'rgba(38, 166, 65, 0.1)',
      topFillColor2: 'rgba(38, 166, 65, 0)',
      bottomFillColor1: 'rgba(248, 81, 73, 0)',
      bottomFillColor2: 'rgba(248, 81, 73, 0.1)',
      lineWidth: 2,
      priceScaleId: 'right',
      ...(priceFormat ? { priceFormat } : {}),
    })
    closeSeries.setData(closeData.map(d => ({ time: d.time, value: d.value })))

    // Baseline line (only when B&H is on)
    if (showBaseline && macroBaselineData) {
      const baselineSeries = chart.addSeries(LineSeries, {
        color: '#8b949e',
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId: 'right',
        ...(priceFormat ? { priceFormat } : {}),
      })
      baselineSeries.setData(macroBaselineData.map(d => ({ time: d.time, value: d.value })))
    }

    // 2. High/low stepped lines (skip in normalised mode — OHLC doesn't apply to % view)
    if (!showBaseline) {
      const ddColor = (pct: number): string => {
        const severity = Math.min(1, Math.abs(pct) / 20)
        const r = Math.round(88 + (248 - 88) * severity)
        const g = Math.round(166 + (81 - 166) * severity)
        const bVal = Math.round(255 + (73 - 255) * severity)
        return `rgba(${r}, ${g}, ${bVal}, 0.5)`
      }

      const highData = macroCurve.map(b => ({ time: b.time as any, value: b.high, color: ddColor(b.drawdown_pct) }))
      const lowData = macroCurve.map(b => ({ time: b.time as any, value: b.low, color: ddColor(b.drawdown_pct) }))
      // Native logarithmic price scale handles log transform for high/low automatically

      const highSeries = chart.addSeries(LineSeries, {
        lineWidth: 1,
        lineType: LineType.WithSteps,
        priceScaleId: 'right',
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      highSeries.setData(highData)

      const lowSeries = chart.addSeries(LineSeries, {
        lineWidth: 1,
        lineType: LineType.WithSteps,
        priceScaleId: 'right',
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      lowSeries.setData(lowData)
    }

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
  }, [macroCurve, initialCapital, showBaseline, logScale, baselineCurve])

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
