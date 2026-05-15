import { useEffect, useMemo, useRef } from 'react'
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  HistogramSeries,
  ColorType,
} from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import type { IndicatorInstance } from '../../shared/types'
import { toLineData } from './chartUtils'

export type PaneRegistryEntry = { chart: IChartApi; series: ISeriesApi<any> }
export type PaneRegistry = Map<string, PaneRegistryEntry>

interface SubPaneProps {
  paneKey: string
  instances: IndicatorInstance[]
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  mainChartRef: React.RefObject<IChartApi | null>
  mainSeriesRef: React.RefObject<ISeriesApi<any> | null>
  paneRegistryRef: React.RefObject<PaneRegistry>
  syncWidthsRef: React.RefObject<() => void>
  markers?: any[]
  toET: (time: string | number) => any
  label: string
  tzMode?: string
  loading?: boolean
  error?: boolean
  errorMessage?: string | null
  onRetry?: () => void
}

const CHART_BG = '#0d1117'
const CHART_BG_SCRIM = 'rgba(13, 17, 23, 0.6)'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

const SUB_COLORS = ['#a371f7', '#58a6ff', '#f0883e', '#e8ab6a', '#56d4c4', '#f85149']

const chartOptions = {
  autoSize: true,
  layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
  grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
  crosshair: { mode: 1 as const },
  timeScale: { borderColor: GRID, timeVisible: true },
  rightPriceScale: { borderColor: GRID },
  leftPriceScale: { visible: false, borderColor: GRID },
}

export default function SubPane({
  paneKey, instances, instanceData, mainChartRef, mainSeriesRef,
  paneRegistryRef, syncWidthsRef,
  markers, toET, label, tzMode, loading, error, errorMessage, onRetry,
}: SubPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const primarySeriesRef = useRef<ISeriesApi<any> | null>(null)
  const seriesMapRef = useRef<Map<string, ISeriesApi<any>> | null>(null)
  const markersPluginRef = useRef<any>(null)

  const indicatorType = instances[0]?.type

  const instancesKey = useMemo(
    () => JSON.stringify(instances.map(i => ({ id: i.id, type: i.type, params: i.params, color: i.color }))),
    [instances],
  )

  const subData = useMemo(() => {
    const result: typeof instanceData = {}
    for (const inst of instances) {
      if (instanceData[inst.id]) result[inst.id] = instanceData[inst.id]
    }
    return result
  }, [instances, instanceData])

  useEffect(() => {
    if (!containerRef.current || instances.length === 0) return

    const chart = createChart(containerRef.current, chartOptions)
    chartRef.current = chart
    const seriesMap = new Map<string, ISeriesApi<any>>()
    let firstSeries: ISeriesApi<any> | null = null

    if (indicatorType === 'macd') {
      const inst = instances[0]
      const histSeries = chart.addSeries(HistogramSeries, {
        color: UP,
        priceFormat: { type: 'price', precision: 4 },
      })
      seriesMap.set(`${inst.id}:histogram`, histSeries)
      firstSeries = histSeries

      const macdLine = chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'MACD' })
      seriesMap.set(`${inst.id}:macd`, macdLine)
      const signalLine = chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'Signal' })
      seriesMap.set(`${inst.id}:signal`, signalLine)
    } else if (indicatorType === 'stochastic') {
      const inst = instances[0]
      const kLine = chart.addSeries(LineSeries, { color: '#2962FF', lineWidth: 1, title: '%K' })
      seriesMap.set(`${inst.id}:k`, kLine)
      firstSeries = kLine
      const dLine = chart.addSeries(LineSeries, { color: '#FF6D00', lineWidth: 1, title: '%D' })
      seriesMap.set(`${inst.id}:d`, dLine)
      // 80/20 reference lines
      seriesMap.set('__ref80', chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 }))
      seriesMap.set('__ref20', chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 }))
    } else if (indicatorType === 'adx') {
      const inst = instances[0]
      const adxLine = chart.addSeries(LineSeries, { color: '#2962FF', lineWidth: 1, title: 'ADX' })
      seriesMap.set(`${inst.id}:adx`, adxLine)
      firstSeries = adxLine
      const plusDI = chart.addSeries(LineSeries, { color: '#26a69a', lineWidth: 1, title: '+DI' })
      seriesMap.set(`${inst.id}:plus_di`, plusDI)
      const minusDI = chart.addSeries(LineSeries, { color: '#ef5350', lineWidth: 1, title: '-DI' })
      seriesMap.set(`${inst.id}:minus_di`, minusDI)
      // 25 reference line (above = trending)
      seriesMap.set('__ref25', chart.addSeries(LineSeries, { color: '#8b949e', lineWidth: 1, lineStyle: 2 }))
    } else {
      instances.forEach((inst, idx) => {
        const color = inst.color ?? SUB_COLORS[idx % SUB_COLORS.length]
        const paramStr = Object.values(inst.params).join(',')
        const series = chart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          title: `${inst.type.toUpperCase()}(${paramStr})`,
        })
        seriesMap.set(inst.id, series)
        if (!firstSeries) firstSeries = series
      })

      if (indicatorType === 'rsi') {
        seriesMap.set('__ref70', chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 }))
        seriesMap.set('__ref30', chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 }))
      }
    }

    primarySeriesRef.current = firstSeries
    seriesMapRef.current = seriesMap

    if (firstSeries) {
      paneRegistryRef.current.set(paneKey, { chart, series: firstSeries })
    }

    chart.timeScale().fitContent()
    syncWidthsRef.current()

    const crosshairHandler = (param: any) => {
      try {
        if (!param.time) {
          mainChartRef.current?.clearCrosshairPosition()
          for (const [key, entry] of paneRegistryRef.current) {
            if (key !== paneKey) entry.chart.clearCrosshairPosition()
          }
          return
        }
        if (mainChartRef.current && mainSeriesRef.current)
          mainChartRef.current.setCrosshairPosition(NaN, param.time, mainSeriesRef.current)
        for (const [key, entry] of paneRegistryRef.current) {
          if (key !== paneKey) {
            try { entry.chart.setCrosshairPosition(NaN, param.time, entry.series) } catch {}
          }
        }
      } catch {}
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    return () => {
      paneRegistryRef.current.delete(paneKey)
      chartRef.current = null
      primarySeriesRef.current = null
      seriesMapRef.current = null
      if (markersPluginRef.current) {
        try { markersPluginRef.current.detach() } catch {}
        markersPluginRef.current = null
      }
      try { chart.unsubscribeCrosshairMove(crosshairHandler) } catch {}
      try { chart.remove() } catch {}
      syncWidthsRef.current()
    }
  }, [paneKey, instancesKey, indicatorType, toET])
  // mainChartRef, mainSeriesRef, paneRegistryRef, syncWidthsRef are stable refs — excluded from deps

  useEffect(() => {
    const sMap = seriesMapRef.current
    if (!sMap || !chartRef.current) return

    if (indicatorType === 'macd') {
      const inst = instances[0]
      const data = subData[inst.id]
      if (!data) return
      const histSeries = sMap.get(`${inst.id}:histogram`)
      if (histSeries) {
        histSeries.setData((data.histogram ?? []).map(d => d.value !== null
          ? { time: toET(d.time as any) as any, value: d.value as number, color: (d.value as number) >= 0 ? UP : DOWN }
          : { time: toET(d.time as any) as any }
        ))
      }
      sMap.get(`${inst.id}:macd`)?.setData(toLineData(data.macd ?? [], toET))
      sMap.get(`${inst.id}:signal`)?.setData(toLineData(data.signal ?? [], toET))
    } else if (indicatorType === 'stochastic') {
      const inst = instances[0]
      const data = subData[inst.id]
      if (!data) return
      sMap.get(`${inst.id}:k`)?.setData(toLineData(data.k ?? [], toET))
      sMap.get(`${inst.id}:d`)?.setData(toLineData(data.d ?? [], toET))
      // 80/20 reference lines
      const kArr = data.k ?? []
      if (kArr.length > 0) {
        const first = kArr[0].time, last = kArr[kArr.length - 1].time
        sMap.get('__ref80')?.setData([{ time: toET(first as any) as any, value: 80 }, { time: toET(last as any) as any, value: 80 }])
        sMap.get('__ref20')?.setData([{ time: toET(first as any) as any, value: 20 }, { time: toET(last as any) as any, value: 20 }])
      }
    } else if (indicatorType === 'adx') {
      const inst = instances[0]
      const data = subData[inst.id]
      if (!data) return
      sMap.get(`${inst.id}:adx`)?.setData(toLineData(data.adx ?? [], toET))
      sMap.get(`${inst.id}:plus_di`)?.setData(toLineData(data.plus_di ?? [], toET))
      sMap.get(`${inst.id}:minus_di`)?.setData(toLineData(data.minus_di ?? [], toET))
      // 25 reference line
      const adxArr = data.adx ?? []
      if (adxArr.length > 0) {
        const first = adxArr[0].time, last = adxArr[adxArr.length - 1].time
        sMap.get('__ref25')?.setData([{ time: toET(first as any) as any, value: 25 }, { time: toET(last as any) as any, value: 25 }])
      }
    } else {
      for (const inst of instances) {
        const data = subData[inst.id]
        if (!data) continue
        const seriesKey = Object.keys(data)[0]
        if (!seriesKey) continue
        sMap.get(inst.id)?.setData(toLineData(data[seriesKey], toET))
      }

      if (indicatorType === 'rsi' && instances.length > 0) {
        const firstData = subData[instances[0].id]
        const seriesKey = firstData ? Object.keys(firstData)[0] : null
        const arr = seriesKey ? firstData[seriesKey] : []
        if (arr.length > 0) {
          const first = arr[0].time, last = arr[arr.length - 1].time
          sMap.get('__ref70')?.setData([{ time: toET(first as any) as any, value: 70 }, { time: toET(last as any) as any, value: 70 }])
          sMap.get('__ref30')?.setData([{ time: toET(first as any) as any, value: 30 }, { time: toET(last as any) as any, value: 30 }])
        }
      }
    }

    // Re-sync range after data is applied — the initial setVisibleLogicalRange
    // in Effect 1 fires on an empty chart and may no-op.
    if (mainChartRef.current) {
      const mainRange = mainChartRef.current.timeScale().getVisibleLogicalRange()
      if (mainRange) {
        try { chartRef.current.timeScale().setVisibleLogicalRange(mainRange) } catch {}
      }
    }
    syncWidthsRef.current()
  }, [subData, instances, indicatorType, toET, tzMode])

  useEffect(() => {
    const series = primarySeriesRef.current
    if (!series) return
    const m = markers ?? []
    if (!markersPluginRef.current) {
      markersPluginRef.current = createSeriesMarkers(series, m)
    } else {
      markersPluginRef.current.setMarkers(m)
    }
  }, [markers, instancesKey, subData])

  return (
    <div style={{ height: '100%', borderTop: '1px solid #1c2128', position: 'relative' }}>
      <span style={{ position: 'absolute', top: 4, left: 8, fontSize: 10, color: '#8b949e', zIndex: 1 }}>{label}</span>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
      {loading && (
        <div style={{
          position: 'absolute', inset: 0, background: CHART_BG_SCRIM,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 2, pointerEvents: 'none',
        }}>
          <span style={{
            fontSize: 11, color: TEXT,
            animation: 'chart-skeleton-pulse 1.6s ease-in-out infinite',
          }}>Loading…</span>
        </div>
      )}
      {/* Error overlay — same positioning/z-index as loading; pane-level granularity matches
          pane-level instanceLoading. Per-instance error granularity deferred to A14d. */}
      {!loading && error && (
        <div style={{
          position: 'absolute', inset: 0, background: CHART_BG_SCRIM,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: 6, zIndex: 2,
        }}>
          <span style={{ fontSize: 11, color: DOWN }}>
            Failed to load: {errorMessage ?? 'indicator error'}
          </span>
          {onRetry && (
            <button
              onClick={onRetry}
              style={{
                fontSize: 11, color: TEXT, background: 'transparent',
                border: `1px solid ${GRID}`, borderRadius: 3,
                padding: '2px 8px', cursor: 'pointer',
              }}
            >
              Retry
            </button>
          )}
        </div>
      )}
    </div>
  )
}
