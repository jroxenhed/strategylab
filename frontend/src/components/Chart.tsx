import { useEffect, useRef } from 'react'
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
} from 'lightweight-charts'
import type { IChartApi, LogicalRange } from 'lightweight-charts'
import type { OHLCVBar, IndicatorData, IndicatorKey, TimeValue } from '../types'

interface ChartProps {
  data: OHLCVBar[]
  spyData?: OHLCVBar[]
  qqqData?: OHLCVBar[]
  showSpy: boolean
  showQqq: boolean
  indicatorData: IndicatorData
  activeIndicators: IndicatorKey[]
  trades?: Array<{ type: 'buy' | 'sell'; date: string; price: number }>
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

function toLineData(arr: TimeValue[]) {
  return arr.filter(d => d.value !== null).map(d => ({ time: d.time as any, value: d.value as number }))
}

function buildMarkers(trades: Array<{ type: 'buy' | 'sell'; date: string; price: number }>) {
  return trades.map(t => ({
    time: t.date as any,
    position: t.type === 'buy' ? 'belowBar' as const : 'aboveBar' as const,
    color: t.type === 'buy' ? UP : DOWN,
    shape: t.type === 'buy' ? 'arrowUp' as const : 'arrowDown' as const,
    text: t.type === 'buy' ? `B $${t.price}` : `S $${t.price}`,
  }))
}

export default function Chart({ data, spyData, qqqData, showSpy, showQqq, indicatorData, activeIndicators, trades }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const macdChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const macdContainerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)

  const showMacd = activeIndicators.includes('macd')
  const showRsi = activeIndicators.includes('rsi')

  const chartOptions = {
    layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
    grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
    crosshair: { mode: 1 },
    timeScale: { borderColor: GRID, timeVisible: true },
    rightPriceScale: { borderColor: GRID },
  }

  // Main chart
  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

    const chart = createChart(containerRef.current, { ...chartOptions, height: containerRef.current.clientHeight })
    chartRef.current = chart

    // Always render candlesticks
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
    })
    candleSeries.setData(data.map(d => ({ ...d, time: d.time as any })))

    // SPY/QQQ as overlay lines — hidden secondary scale so no second price axis clutters the chart
    if (showSpy && spyData && spyData.length > 0) {
      chart.addSeries(LineSeries, {
        color: '#f0883e', lineWidth: 1, title: 'SPY', priceScaleId: 'overlay',
      }).setData(spyData.map(d => ({ time: d.time as any, value: d.close })))
    }
    if (showQqq && qqqData && qqqData.length > 0) {
      chart.addSeries(LineSeries, {
        color: '#a371f7', lineWidth: 1, title: 'QQQ', priceScaleId: 'overlay',
      }).setData(qqqData.map(d => ({ time: d.time as any, value: d.close })))
    }
    if (showSpy || showQqq) {
      chart.priceScale('overlay').applyOptions({ visible: false })
    }

    // Volume overlay — semi-transparent bars at bottom 25% of chart
    if (activeIndicators.includes('volume')) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      })
      volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } })
      volSeries.setData(data.map(d => ({
        time: d.time as any,
        value: d.volume,
        color: d.close >= d.open ? '#26a64166' : '#f8514966',
      })))
    }

    // EMA overlays on main chart
    if (activeIndicators.includes('ema') && indicatorData.ema) {
      const { ema20, ema50, ema200 } = indicatorData.ema
      chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'EMA20' }).setData(toLineData(ema20))
      chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'EMA50' }).setData(toLineData(ema50))
      chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'EMA200' }).setData(toLineData(ema200))
    }

    // Bollinger Bands on main chart
    if (activeIndicators.includes('bb') && indicatorData.bb) {
      const { upper, middle, lower } = indicatorData.bb
      chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Upper' }).setData(toLineData(upper))
      chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'BB Mid' }).setData(toLineData(middle))
      chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Lower' }).setData(toLineData(lower))
    }

    // Trade markers
    if (trades && trades.length > 0) createSeriesMarkers(candleSeries, buildMarkers(trades))

    chart.timeScale().fitContent()

    const syncHandler = (range: LogicalRange | null) => {
      if (!range) return
      const mainW = chart.priceScale('right').width()
      const macdW = macdChartRef.current?.priceScale('right').width() ?? 0
      const rsiW = rsiChartRef.current?.priceScale('right').width() ?? 0
      const maxW = Math.max(mainW, macdW, rsiW)
      if (maxW > 0) {
        chart.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
        macdChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
        rsiChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
      }
      if (macdChartRef.current) macdChartRef.current.timeScale().setVisibleLogicalRange(range)
      if (rsiChartRef.current) rsiChartRef.current.timeScale().setVisibleLogicalRange(range)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler)

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    })
    ro.observe(containerRef.current)

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(syncHandler)
      chart.remove()
      ro.disconnect()
    }
  }, [data, spyData, qqqData, showSpy, showQqq, activeIndicators, indicatorData, trades])

  // MACD chart
  useEffect(() => {
    if (!macdContainerRef.current || !showMacd || !indicatorData.macd) return

    const chart = createChart(macdContainerRef.current, { ...chartOptions, height: macdContainerRef.current.clientHeight })
    macdChartRef.current = chart

    const { macd, signal, histogram } = indicatorData.macd
    chart.addSeries(HistogramSeries, {
      color: UP,
      priceFormat: { type: 'price', precision: 4 },
    }).setData(histogram.filter(d => d.value !== null).map(d => ({
      time: d.time as any,
      value: d.value as number,
      color: (d.value ?? 0) >= 0 ? UP : DOWN,
    })))
    chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'MACD' }).setData(toLineData(macd))
    chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'Signal' }).setData(toLineData(signal))
    chart.timeScale().fitContent()

    requestAnimationFrame(() => {
      if (!chartRef.current) return
      const mainW = chartRef.current.priceScale('right').width()
      const macdW = chart.priceScale('right').width()
      const rsiW = rsiChartRef.current?.priceScale('right').width() ?? 0
      const maxW = Math.max(mainW, macdW, rsiW)
      chartRef.current.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
      chart.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
      rsiChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
    })

    const ro = new ResizeObserver(() => {
      if (macdContainerRef.current) chart.applyOptions({ width: macdContainerRef.current.clientWidth })
    })
    ro.observe(macdContainerRef.current)
    return () => { chart.remove(); macdChartRef.current = null; ro.disconnect() }
  }, [showMacd, indicatorData.macd])

  // RSI chart
  useEffect(() => {
    if (!rsiContainerRef.current || !showRsi || !indicatorData.rsi) return

    const chart = createChart(rsiContainerRef.current, { ...chartOptions, height: rsiContainerRef.current.clientHeight })
    rsiChartRef.current = chart

    chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'RSI' }).setData(toLineData(indicatorData.rsi))
    // Overbought/oversold lines
    const len = indicatorData.rsi.length
    if (len > 0) {
      const first = indicatorData.rsi[0].time
      const last = indicatorData.rsi[len - 1].time
      chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 }).setData([{ time: first as any, value: 70 }, { time: last as any, value: 70 }])
      chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 }).setData([{ time: first as any, value: 30 }, { time: last as any, value: 30 }])
    }
    chart.timeScale().fitContent()

    requestAnimationFrame(() => {
      if (!chartRef.current) return
      const mainW = chartRef.current.priceScale('right').width()
      const macdW = macdChartRef.current?.priceScale('right').width() ?? 0
      const rsiW = chart.priceScale('right').width()
      const maxW = Math.max(mainW, macdW, rsiW)
      chartRef.current.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
      macdChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
      chart.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
    })

    const ro = new ResizeObserver(() => {
      if (rsiContainerRef.current) chart.applyOptions({ width: rsiContainerRef.current.clientWidth })
    })
    ro.observe(rsiContainerRef.current)
    return () => { chart.remove(); rsiChartRef.current = null; ro.disconnect() }
  }, [showRsi, indicatorData.rsi])

  const indicatorPaneCount = (showMacd ? 1 : 0) + (showRsi ? 1 : 0)
  const mainHeightPct = indicatorPaneCount === 0 ? 100 : indicatorPaneCount === 1 ? 65 : 50
  const subHeightPct = indicatorPaneCount === 0 ? 0 : indicatorPaneCount === 1 ? 35 : 25

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%' }}>
      <div ref={containerRef} style={{ height: `${mainHeightPct}%`, width: '100%' }} />
      {showMacd && (
        <div style={{ height: `${subHeightPct}%`, borderTop: '1px solid #1c2128', position: 'relative' }}>
          <span style={{ position: 'absolute', top: 4, left: 8, fontSize: 10, color: '#8b949e', zIndex: 1 }}>MACD</span>
          <div ref={macdContainerRef} style={{ height: '100%', width: '100%' }} />
        </div>
      )}
      {showRsi && (
        <div style={{ height: `${subHeightPct}%`, borderTop: '1px solid #1c2128', position: 'relative' }}>
          <span style={{ position: 'absolute', top: 4, left: 8, fontSize: 10, color: '#8b949e', zIndex: 1 }}>RSI</span>
          <div ref={rsiContainerRef} style={{ height: '100%', width: '100%' }} />
        </div>
      )}
    </div>
  )
}
