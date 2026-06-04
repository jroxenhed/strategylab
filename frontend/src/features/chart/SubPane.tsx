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
import { toLineData, aggregateLineSeries } from './chartUtils'

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
  /** Render-layer bucket size in seconds (0/undefined = no aggregation).
   *  Must use the same bucket floor as the main pane to keep bar counts aligned. */
  bucketSecs?: number
  loading?: boolean
  error?: boolean
  errorMessage?: string | null
  onRetry?: () => void
  showTimeAxis?: boolean
}

const CHART_BG = '#0d1117'
const CHART_BG_SCRIM = 'rgba(13, 17, 23, 0.6)'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

const SUB_COLORS = ['#a371f7', '#58a6ff', '#f0883e', '#e8ab6a', '#56d4c4', '#f85149']

export default function SubPane({
  paneKey, instances, instanceData, mainChartRef, mainSeriesRef,
  paneRegistryRef, syncWidthsRef,
  markers, toET, label, tzMode, bucketSecs, loading, error, errorMessage, onRetry,
  showTimeAxis = true,
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

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
      grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      crosshair: { mode: 1 as const },
      // minBarSpacing must match the main chart's (Chart.tsx) — the default 0.5px
      // clamp would silently refuse the wide logical ranges synced from the main
      // pane at deep zoom-out, desyncing the panes (A8 auto-downsample).
      timeScale: { borderColor: GRID, timeVisible: true, visible: showTimeAxis, minBarSpacing: 0.01 },
      rightPriceScale: { borderColor: GRID },
      leftPriceScale: { visible: false, borderColor: GRID },
    })
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
  }, [paneKey, instancesKey, indicatorType, toET, showTimeAxis])
  // mainChartRef, mainSeriesRef, paneRegistryRef, syncWidthsRef are stable refs — excluded from deps

  // Dynamic time-axis visibility: when showTimeAxis flips (sub-pane count changes)
  // update the already-mounted chart without tearing it down.
  useEffect(() => {
    try { chartRef.current?.timeScale().applyOptions({ visible: showTimeAxis }) } catch {}
  }, [showTimeAxis])

  useEffect(() => {
    const sMap = seriesMapRef.current
    if (!sMap || !chartRef.current) return

    // Render-layer aggregation helper: applies bucket-sampling if bucketSecs is set.
    const agg = (raw: ReturnType<typeof toLineData>) =>
      bucketSecs && bucketSecs > 0 ? aggregateLineSeries(raw, bucketSecs) : raw

    if (indicatorType === 'macd') {
      const inst = instances[0]
      const data = subData[inst.id]
      if (!data) return
      const histSeries = sMap.get(`${inst.id}:histogram`)
      if (histSeries) {
        // Aggregate the {time, value} projection of the histogram, then zip color back.
        // This avoids passing extra fields through aggregateLineSeries (which only
        // knows about {time, value}) without resorting to `as any` casts (TS-02).
        const rawHistBase = toLineData(data.histogram ?? [], toET)
        const aggHistBase = agg(rawHistBase)
        const aggHist = aggHistBase.map(d => ({
          time: d.time,
          ...(d.value !== undefined ? { value: d.value, color: d.value >= 0 ? UP : DOWN } : {}),
        }))
        histSeries.setData(aggHist as any)
      }
      sMap.get(`${inst.id}:macd`)?.setData(agg(toLineData(data.macd ?? [], toET)))
      sMap.get(`${inst.id}:signal`)?.setData(agg(toLineData(data.signal ?? [], toET)))
    } else if (indicatorType === 'stochastic') {
      const inst = instances[0]
      const data = subData[inst.id]
      if (!data) return
      // Hoist aggregated k result to reuse for setData and reference-line endpoints (COR-02/TS-05).
      const kAgg = agg(toLineData(data.k ?? [], toET))
      sMap.get(`${inst.id}:k`)?.setData(kAgg)
      sMap.get(`${inst.id}:d`)?.setData(agg(toLineData(data.d ?? [], toET)))
      // 80/20 reference lines — endpoints from the already-aggregated k series
      if (kAgg.length > 0) {
        const first = kAgg[0].time, last = kAgg[kAgg.length - 1].time
        sMap.get('__ref80')?.setData([{ time: first, value: 80 }, { time: last, value: 80 }])
        sMap.get('__ref20')?.setData([{ time: first, value: 20 }, { time: last, value: 20 }])
      }
    } else if (indicatorType === 'adx') {
      const inst = instances[0]
      const data = subData[inst.id]
      if (!data) return
      // Hoist aggregated adx result to reuse for setData and reference-line endpoints (TS-05).
      const adxAgg = agg(toLineData(data.adx ?? [], toET))
      sMap.get(`${inst.id}:adx`)?.setData(adxAgg)
      sMap.get(`${inst.id}:plus_di`)?.setData(agg(toLineData(data.plus_di ?? [], toET)))
      sMap.get(`${inst.id}:minus_di`)?.setData(agg(toLineData(data.minus_di ?? [], toET)))
      // 25 reference line — endpoints from the already-aggregated adx series
      if (adxAgg.length > 0) {
        const first = adxAgg[0].time, last = adxAgg[adxAgg.length - 1].time
        sMap.get('__ref25')?.setData([{ time: first, value: 25 }, { time: last, value: 25 }])
      }
    } else {
      for (const inst of instances) {
        const data = subData[inst.id]
        if (!data) continue
        const seriesKey = Object.keys(data)[0]
        if (!seriesKey) continue
        sMap.get(inst.id)?.setData(agg(toLineData(data[seriesKey], toET)))
      }

      if (indicatorType === 'rsi' && instances.length > 0) {
        const firstData = subData[instances[0].id]
        const seriesKey = firstData ? Object.keys(firstData)[0] : null
        const rawArr = seriesKey ? firstData[seriesKey] : []
        const aggArr = agg(toLineData(rawArr, toET))
        if (aggArr.length > 0) {
          const first = aggArr[0].time, last = aggArr[aggArr.length - 1].time
          sMap.get('__ref70')?.setData([{ time: first, value: 70 }, { time: last, value: 70 }])
          sMap.get('__ref30')?.setData([{ time: first, value: 30 }, { time: last, value: 30 }])
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
  }, [subData, instances, indicatorType, toET, tzMode, bucketSecs])

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
