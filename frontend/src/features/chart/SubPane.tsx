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
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

const SUB_COLORS = ['#a371f7', '#58a6ff', '#f0883e', '#e8ab6a', '#56d4c4', '#f85149']

const chartOptions = {
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
  markers, toET, label,
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

  // Effect 1: Chart lifecycle
  useEffect(() => {
    if (!containerRef.current || instances.length === 0) return

    const chart = createChart(containerRef.current, {
      ...chartOptions,
      height: containerRef.current.clientHeight,
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

    const ro = new ResizeObserver(() => {
      if (containerRef.current)
        chart.applyOptions({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight })
    })
    ro.observe(containerRef.current)

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
      ro.disconnect()
      syncWidthsRef.current()
    }
  }, [paneKey, instancesKey, indicatorType, toET])
  // mainChartRef, mainSeriesRef, paneRegistryRef, syncWidthsRef are stable refs — excluded from deps

  // Effect 2: Data application
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
  }, [subData, instances, indicatorType, toET])

  // Effect 3: Markers
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
    </div>
  )
}
