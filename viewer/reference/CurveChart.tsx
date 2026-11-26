'use client'

import { useMemo, useRef, useEffect, useCallback, useState } from 'react'
import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, Legend,
} from 'chart.js'
import { Line, Scatter } from 'react-chartjs-2'
import zoomPlugin from 'chartjs-plugin-zoom'
import type { CurveRow } from '@/lib/types'
import { SEAT_COLORS, OVERLAY_COLORS, getClubColor } from '@/lib/types'
import { viewerTooltipPosition, registerTooltipPositioner } from '@/lib/chartTooltip'
import { useCurveHover } from './CurveHoverContext'

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, Legend, zoomPlugin,
)
registerTooltipPositioner()

interface AxisPrefs { xMin?: number; xMax?: number; yMin?: number; yMax?: number }
type CurveSideFilter = string | string[]
const STROKE_SEAM_RATIO = 0.9
const SHARED_STRETCHER_CHANNELS = new Set([
  'stretcherforcex',
  'stretcherforcex_raw',
  'stretcherz',
  'stretcherz_raw',
  'stretcherrl',
  'stretcherrl_raw',
  'stretchertb',
  'stretchertb_raw',
])

function seamIndex(length: number): number {
  return Math.max(0, Math.min(length - 1, Math.round((length - 1) * STROKE_SEAM_RATIO)))
}

function accelerationSignFromBounds(maxVal: number, minVal: number): 1 | -1 {
  if (!Number.isFinite(maxVal) || !Number.isFinite(minVal)) return 1
  return Math.abs(maxVal) > Math.abs(minVal) ? -1 : 1
}

function accelerationSignForMean(mean: number[]): 1 | -1 {
  let maxVal = -Infinity
  let minVal = Infinity
  for (const value of mean) {
    if (!Number.isFinite(value)) continue
    if (value > maxVal) maxVal = value
    if (value < minVal) minVal = value
  }
  return accelerationSignFromBounds(maxVal, minVal)
}

function displaySignForChannel(channel: string | undefined, rows: CurveRow[]): 1 | -1 {
  if (channel !== 'accel') return 1

  const boatRow = rows.find(row => row.seat === 0 && row.mean.length > 0)
  if (boatRow) return accelerationSignForMean(boatRow.mean)

  let maxVal = -Infinity
  let minVal = Infinity
  for (const row of rows) {
    for (const value of row.mean) {
      if (!Number.isFinite(value)) continue
      if (value > maxVal) maxVal = value
      if (value < minVal) minVal = value
    }
  }
  return accelerationSignFromBounds(maxVal, minVal)
}

function isBoatSensorMotionChannel(channel: string | undefined): boolean {
  return channel === 'accel' || channel === 'roll' || channel === 'pitch' || channel === 'yaw'
}

function boatMotionDisplaySign(channel: string | undefined, rows: CurveRow[], accelerationRows: CurveRow[]): 1 | -1 {
  if (channel === 'accel') return displaySignForChannel(channel, rows)
  if (!isBoatSensorMotionChannel(channel)) return 1
  const accelerationSign = displaySignForChannel('accel', accelerationRows)
  return accelerationSign
}

function displayMeanForChannel(channel: string | undefined, mean: number[], sign: 1 | -1): number[] {
  const invert =
    channel === 'stretcherforcex' ||
    channel === 'stretcherforcex_raw' ||
    (isBoatSensorMotionChannel(channel) && sign === -1)
  return invert ? mean.map(value => -value) : mean
}

function rollAtSeam<T>(arr: T[], idx: number): T[] {
  return [...arr.slice(idx), ...arr.slice(0, idx)]
}

function sourceIndicesForRolled(length: number, idx: number): number[] {
  return Array.from({ length }, (_, rolledIndex) => (idx + rolledIndex) % length)
}

function sourceIndexLookupForRolled(length: number, idx: number): Map<number, number> {
  const lookup = new Map<number, number>()
  for (let rolledIndex = 0; rolledIndex < length; rolledIndex++) {
    lookup.set((idx + rolledIndex) % length, rolledIndex)
  }
  return lookup
}

function firstNameOf(name: string): string {
  return name.trim().split(/\s+/)[0] || name.trim()
}

function lastInitialOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length < 2) return ''
  return parts[parts.length - 1][0]?.toUpperCase() || ''
}

function firstNameCounts(names?: Record<number, string>): Map<string, number> {
  const counts = new Map<string, number>()
  if (!names) return counts
  for (const name of Object.values(names)) {
    const first = firstNameOf(name)
    if (!first) continue
    counts.set(first, (counts.get(first) || 0) + 1)
  }
  return counts
}

function compactRowerName(name: string, counts: Map<string, number>): string {
  const first = firstNameOf(name)
  if (!first) return name
  if ((counts.get(first) || 0) <= 1) return first
  const initial = lastInitialOf(name)
  return initial ? `${first} ${initial}` : first
}

function highestSeatNumber(seats?: number[]): number | null {
  const numericSeats = (seats || []).filter(Number.isFinite)
  if (numericSeats.length === 0) return null
  return Math.max(...numericSeats)
}

function sideMatches(rowSide: string, side?: CurveSideFilter): boolean {
  if (!side) return true
  return Array.isArray(side) ? side.includes(rowSide) : rowSide === side
}

function channelRows(
  rows: CurveRow[],
  channel: string,
  curveType: string,
  side: CurveSideFilter | undefined,
  seats: number[] | undefined,
  xChannel?: string,
): CurveRow[] {
  const matching = rows.filter(row =>
    row.channel === channel &&
    row.curve_type === curveType &&
    (seats ? seats.includes(row.seat) : true)
  )
  if (!SHARED_STRETCHER_CHANNELS.has(channel)) {
    return matching.filter(row => sideMatches(row.side, side))
  }
  if (!xChannel) {
    // Stretcher sensors belong to the rower rather than either oar. They are
    // stored once on the port row and remain visible for every side selection.
    return matching
  }

  // For parametric plots, pair the one shared sensor curve with the selected
  // oar angle(s). Synthetic side values let the normal renderer draw P and S.
  const xRows = rows.filter(row =>
    row.channel === xChannel &&
    row.curve_type === curveType &&
    sideMatches(row.side, side) &&
    (seats ? seats.includes(row.seat) : true)
  )
  return xRows.flatMap(xRow => {
    const shared = matching.find(row => row.seat === xRow.seat)
    return shared ? [{ ...shared, side: xRow.side }] : []
  })
}

function sideSuffix(side: string): string {
  if (side === 'port') return 'P'
  if (side === 'starboard') return 'S'
  return side
}

function sideDash(side: string, baseDash?: number[]): number[] | undefined {
  if (side !== 'starboard') return baseDash
  return baseDash ? [...baseDash, 2, 4] : [2, 3]
}

function tooltipColorForDataset(ctx: any): string {
  return typeof ctx.dataset?.borderColor === 'string' ? ctx.dataset.borderColor : '#7a8ba0'
}

function formatCurveValue(value: number, channel: string, yLabel?: string): string {
  const label = yLabel?.toLowerCase() || ''
  const isBoatMotion =
    channel === 'accel' ||
    channel === 'roll' ||
    channel === 'pitch' ||
    channel === 'yaw' ||
    label.includes('accel') ||
    label.includes('roll') ||
    label.includes('pitch') ||
    label.includes('yaw')
  if (isBoatMotion) {
    const formatted = value.toFixed(2)
    if (channel === 'accel' || label.includes('accel')) return `${formatted} m/s²`
    if (label.includes('deg') || channel === 'roll' || channel === 'pitch' || channel === 'yaw') return `${formatted}°`
    return formatted
  }
  if (
    channel.includes('force') ||
    label.includes('force') ||
    label.includes('(n)')
  ) {
    return Math.round(value).toString()
  }
  if (
    channel.includes('angle') ||
    label.includes('angle') ||
    label.includes('deg/s') ||
    label.includes('deg')
  ) {
    const formatted = Math.round(value).toString()
    if (label.includes('deg/s')) return `${formatted} deg/s`
    return `${formatted}°`
  }
  return value.toFixed(1)
}

interface CurveChartProps {
  curves: CurveRow[]
  channel: string
  curveType?: 'time_based' | 'parametric_raw'
  xChannel?: string
  seats?: number[]
  side?: CurveSideFilter
  title?: string
  yLabel?: string
  showStd?: boolean
  height?: number
  lineWidth?: number
  /** If true, suppresses Chart.js' built-in legend. */
  hideLegend?: boolean
  /** If false, mouse/trackpad wheel input scrolls the page instead of zooming the chart. */
  wheelZoom?: boolean
  /** If true, the crosshair plugin still draws the vertical line but skips the per-dataset label list. */
  hideCrosshairLabels?: boolean
  /** Map of seat number → display name (e.g. {1: "Anna", 2: "Ben"}). Falls back to "Seat N". */
  rowerNames?: Record<number, string>
  /** Map of seat number → override color. Takes precedence over SEAT_COLORS. */
  seatColors?: Record<number, string>
  /** Adds P/S suffixes to rower labels when both sculling sides are shown in one chart. */
  showSideLabels?: boolean
  overlays?: {
    curves: CurveRow[]
    label: string
    colorIndex: number
    /** If true, overlay lines use SEAT_COLORS (preserves rower identity across pieces). */
    useSeatColor?: boolean
    /** Custom dash pattern; defaults to [6, 3]. */
 borderDash?: number[]
 borderWidth?: number
 /** Optional under-stroke to keep dashed overlays readable over stacked lines. */
 underlayColor?: string
 underlayWidth?: number
 /** Per-overlay seat → color override (for rower-identity coloring with different crews). */
    seatColors?: Record<number, string>
    /** Per-overlay seat → name (for labeling when crews differ across pieces). */
    rowerNames?: Record<number, string>
    /** If true, use only the overlay label for non-boat datasets. */
    hideRowerLabel?: boolean
  }[]
}

export default function CurveChart({
  curves, channel, curveType = 'time_based', xChannel,
  seats, side, title, yLabel, showStd = true, height = 280, lineWidth = 2,
  hideLegend, wheelZoom = true, hideCrosshairLabels,
  rowerNames, seatColors, showSideLabels, overlays,
}: CurveChartProps) {
  // Latest-value refs so the stable crosshair plugin can read current prop values.
  const hideCrosshairLabelsRef = useRef<boolean>(!!hideCrosshairLabels)
  useEffect(() => {
    hideCrosshairLabelsRef.current = !!hideCrosshairLabels
  }, [hideCrosshairLabels])
  const primaryFirstNameCounts = useMemo(() => firstNameCounts(rowerNames), [rowerNames])
  const seatLabelFor = (seat: number): string => {
    if (seat === 0) return 'Boat'
    const name = rowerNames?.[seat]
    return name ? compactRowerName(name, primaryFirstNameCounts) : `Seat ${seat}`
  }
  const seatColorFor = (seat: number): string => {
    // If the caller provided a seatColors map, trust it completely, fall back
    // to a neutral gray rather than the seat-number palette (which would
    // reintroduce, e.g., red at seat 2 for rowers that aren't in the map).
    if (seatColors) {
      return seatColors[seat] || '#7a8ba0'
    }
    return seat === 0 ? getClubColor() : (SEAT_COLORS[seat] || '#888')
  }
  const isParametric = !!xChannel
  const strokeSeat = useMemo(() => highestSeatNumber(seats), [seats])

  // ── Axis range prefs (persisted per chart type) ─────────────────────────
  const sideKey = Array.isArray(side) ? side.join('_') : (side || '')
  const lsKey = `viewer-axis_${channel}_${curveType}_${sideKey}`
  const [axisOpen, setAxisOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [axisPrefs, setAxisPrefs] = useState<AxisPrefs>({})

  // Close the enlarged view on Escape.
  useEffect(() => {
    if (!expanded) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [expanded])
  const [draftXMin, setDraftXMin] = useState('')
  const [draftXMax, setDraftXMax] = useState('')
  const [draftYMin, setDraftYMin] = useState('')
  const [draftYMax, setDraftYMax] = useState('')

  useEffect(() => {
    try {
      const stored = localStorage.getItem(lsKey)
      if (stored) {
        const p: AxisPrefs = JSON.parse(stored)
        setAxisPrefs(p)
        setDraftXMin(p.xMin != null ? String(p.xMin) : '')
        setDraftXMax(p.xMax != null ? String(p.xMax) : '')
        setDraftYMin(p.yMin != null ? String(p.yMin) : '')
        setDraftYMax(p.yMax != null ? String(p.yMax) : '')
      }
    } catch {}
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lsKey])

  function applyAxis() {
    const p: AxisPrefs = {
      xMin: draftXMin !== '' ? Number(draftXMin) : undefined,
      xMax: draftXMax !== '' ? Number(draftXMax) : undefined,
      yMin: draftYMin !== '' ? Number(draftYMin) : undefined,
      yMax: draftYMax !== '' ? Number(draftYMax) : undefined,
    }
    setAxisPrefs(p)
    try { localStorage.setItem(lsKey, JSON.stringify(p)) } catch {}
    setAxisOpen(false)
  }

  function clearAxis() {
    setAxisPrefs({})
    setDraftXMin(''); setDraftXMax(''); setDraftYMin(''); setDraftYMax('')
    try { localStorage.removeItem(lsKey) } catch {}
    setAxisOpen(false)
  }

  const hasAxisPrefs = Object.values(axisPrefs).some(v => v !== undefined)

  function paddedBounds(vals: number[], padRatio: number) {
    if (vals.length === 0) return { min: undefined, max: undefined }
    const min = Math.min(...vals)
    const max = Math.max(...vals)
    if (!isFinite(min) || !isFinite(max)) return { min: undefined, max: undefined }
    const span = max - min
    const pad = span > 0 ? span * padRatio : Math.max(Math.abs(max) * padRatio, 1)
    return { min: min - pad, max: max + pad }
  }

  // ── Cross-chart hover sync ──────────────────────────────────────────────
  const hoverCtx = useCurveHover()
  const chartRef = useRef<any>(null)
  const localHoverRef = useRef<number | null>(null)
  const zoomBy = useCallback((factor: number) => {
    chartRef.current?.zoom?.({ x: factor, y: factor }, 'none')
  }, [])
  const resetZoom = useCallback(() => {
    chartRef.current?.resetZoom?.('none')
  }, [])
  // Visible stroke sample index shared across time and parametric chart types.
  const crossHoverIndexRef = useRef<number | null>(null)

  useEffect(() => {
    if (!hoverCtx) return
    if (isParametric) {
      return hoverCtx.subscribe((idx) => {
        crossHoverIndexRef.current = idx
        chartRef.current?.update('none')
      })
    } else {
      return hoverCtx.subscribe((idx) => {
        localHoverRef.current = idx
        chartRef.current?.update('none')
      })
    }
  }, [hoverCtx, isParametric])

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!hoverCtx || !chartRef.current) return
    const chart = chartRef.current
    if (!chart.canvas || !chart.chartArea) return
    const rect = chart.canvas.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    if (x < chart.chartArea.left || x > chart.chartArea.right || y < chart.chartArea.top || y > chart.chartArea.bottom) return
    if (isParametric) {
      let minDist = Infinity
      let nearestIdx: number | null = null
      const hoverDatasets = (chart.data.datasets as any[]).filter(ds =>
        ds.isHoverReference && ds.label && ds.borderColor && ds.borderColor !== 'transparent' && Array.isArray(ds.data)
      )
      const searchableDatasets = hoverDatasets.length > 0 ? hoverDatasets : (chart.data.datasets as any[])
      for (const ds of searchableDatasets) {
        if (!ds.label || !ds.borderColor || ds.borderColor === 'transparent' || !Array.isArray(ds.data)) continue
        for (let i = 0; i < ds.data.length; i++) {
          const pt = ds.data[i]
          if (!pt || typeof pt.x !== 'number' || typeof pt.y !== 'number') continue
          const px = chart.scales.x?.getPixelForValue(pt.x)
          const py = chart.scales.y?.getPixelForValue(pt.y)
          if (!Number.isFinite(px) || !Number.isFinite(py)) continue
          const dx = px - x
          const dy = py - y
          const dist = dx * dx + dy * dy
          if (dist < minDist) {
            minDist = dist
            nearestIdx = ds.sourceIndices?.[i] ?? i
          }
        }
      }
      if (nearestIdx == null) return
      crossHoverIndexRef.current = nearestIdx
      hoverCtx.setHoverIndex(nearestIdx)
    } else {
      const raw = chart.scales.x?.getValueForPixel(x)
      if (raw == null) return
      const idx = Math.max(0, Math.min(199, Math.round(raw)))
      localHoverRef.current = idx
      hoverCtx.setHoverIndex(idx)
    }
    chart.update('none')
  }, [hoverCtx, isParametric])

  const handleMouseLeave = useCallback(() => {
    if (!hoverCtx) return
    if (isParametric) {
      crossHoverIndexRef.current = null
      hoverCtx.setHoverIndex(null)  // clear bridge on time-based charts
    } else {
      localHoverRef.current = null
      hoverCtx.setHoverIndex(null)
    }
    chartRef.current?.update('none')
  }, [hoverCtx, isParametric])

  // Crosshair plugin, stable object (reads hover refs at draw time)
  // isParametric is constant for this component instance, safe to close over
  const crosshairPlugin = useRef<any>({
    id: 'viewer-crosshair',
    afterDraw(chart: any) {
      if (!chart.chartArea) return
      const { ctx, chartArea, scales } = chart

      function drawDot(px: number, py: number, color: string, isReference = false) {
        if (py < chartArea.top - 8 || py > chartArea.bottom + 8) return
        if (isReference) {
          ctx.beginPath()
          ctx.arc(px, py, 7, 0, Math.PI * 2)
          ctx.strokeStyle = 'rgba(23, 36, 56, 0.45)'
          ctx.lineWidth = 2.5
          ctx.setLineDash([])
          ctx.stroke()
          ctx.beginPath()
          ctx.arc(px, py, 5.5, 0, Math.PI * 2)
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.72)'
          ctx.lineWidth = 1.25
          ctx.stroke()
        }
        ctx.beginPath()
        ctx.arc(px, py, isReference ? 4.5 : 4, 0, Math.PI * 2)
        ctx.fillStyle = color
        ctx.fill()
        ctx.strokeStyle = '#0f1c2e'
        ctx.lineWidth = isReference ? 1.75 : 1.5
        ctx.setLineDash([])
        ctx.stroke()
      }

      ctx.save()

      if (isParametric) {
        const crossIdx = crossHoverIndexRef.current
        if (crossIdx == null) { ctx.restore(); return }

        const primaryDs = (chart.data.datasets as any[]).find(
          (ds: any) => ds.isHoverReference && ds.label && ds.borderColor !== 'transparent'
        ) ?? (chart.data.datasets as any[]).find((ds: any) => ds.label && ds.borderColor !== 'transparent')
        const primaryIdx = primaryDs?.sourceIndexLookup?.get?.(crossIdx) ?? crossIdx
        const linePoint = primaryDs && Array.isArray(primaryDs.data) && primaryIdx < primaryDs.data.length
          ? primaryDs.data[primaryIdx]
          : null
        if (!linePoint || typeof linePoint.x !== 'number') { ctx.restore(); return }

        const linePx = scales.x?.getPixelForValue(linePoint.x) ?? null
        if (linePx == null || linePx < chartArea.left || linePx > chartArea.right) { ctx.restore(); return }

        // Crosshair line
        ctx.beginPath()
        ctx.moveTo(linePx, chartArea.top)
        ctx.lineTo(linePx, chartArea.bottom)
        ctx.strokeStyle = 'rgba(255,255,255,0.22)'
        ctx.lineWidth = 1
        ctx.setLineDash([3, 4])
        ctx.stroke()
        ctx.setLineDash([])

        const showLabels = !hideCrosshairLabelsRef.current
        let labelY = chartArea.top + 13
        if (showLabels) {
          ctx.font = '10px ui-monospace,monospace'
          ctx.textAlign = 'left'
        }

        for (const ds of chart.data.datasets) {
          if (!ds.label || !ds.borderColor || ds.borderColor === 'transparent') continue
          const color = typeof ds.borderColor === 'string' ? ds.borderColor : '#7a8ba0'
          let dotPt: { x: number; y: number } | null = null

          const sourceIdx = ds.sourceIndexLookup?.get?.(crossIdx) ?? crossIdx
          if (Array.isArray(ds.data) && sourceIdx < ds.data.length) {
            const pt = ds.data[sourceIdx] as { x: number; y: number }
            if (pt && typeof pt.x === 'number') dotPt = pt
          }

          if (!dotPt || !isFinite(dotPt.y)) continue

          const dotPx = scales.x?.getPixelForValue(dotPt.x) ?? null
          const dotPy = scales.y?.getPixelForValue(dotPt.y) ?? null
          const isReference = ds.isHoverReference === true
          if (dotPx != null && dotPy != null) drawDot(dotPx, dotPy, color, isReference)

          if (showLabels) {
            ctx.fillStyle = color
            ctx.beginPath()
            ctx.arc(chartArea.left + 7, labelY - 4, isReference ? 3.5 : 3, 0, Math.PI * 2)
            ctx.fill()
            ctx.fillStyle = isReference ? '#e2e8f0' : '#c8d6e5'
            const prefix = isReference ? 'Stroke seat ' : ''
            ctx.fillText(`${prefix}${ds.label}: ${formatCurveValue(Number(dotPt.y), channel, yLabel)}`, chartArea.left + 14, labelY)
            labelY += 14
          }
        }

      } else {
        // Time-based chart
        const idx = localHoverRef.current
        if (idx == null) { ctx.restore(); return }

        const linePx = scales.x?.getPixelForValue(idx) ?? null
        if (linePx == null || linePx < chartArea.left || linePx > chartArea.right) { ctx.restore(); return }

        // Crosshair line
        ctx.beginPath()
        ctx.moveTo(linePx, chartArea.top)
        ctx.lineTo(linePx, chartArea.bottom)
        ctx.strokeStyle = 'rgba(255,255,255,0.22)'
        ctx.lineWidth = 1
        ctx.setLineDash([3, 4])
        ctx.stroke()
        ctx.setLineDash([])

        const showLabels = !hideCrosshairLabelsRef.current
        let labelY = chartArea.top + 13
        if (showLabels) {
          ctx.font = '10px ui-monospace,monospace'
          ctx.textAlign = 'left'
        }

        for (const ds of chart.data.datasets) {
          if (!ds.label || !ds.borderColor || ds.borderColor === 'transparent') continue
          const raw = Array.isArray(ds.data) ? ds.data[idx] : null
          const val: number | null = raw != null && typeof raw === 'object' ? (raw as any).y : raw
          if (val == null || !isFinite(Number(val))) continue
          const color = typeof ds.borderColor === 'string' ? ds.borderColor : '#7a8ba0'

          const dotPy = scales.y?.getPixelForValue(val) ?? null
          if (dotPy != null) drawDot(linePx, dotPy, color)

          if (showLabels) {
            ctx.fillStyle = color
            ctx.beginPath()
            ctx.arc(chartArea.left + 7, labelY - 4, 3, 0, Math.PI * 2)
            ctx.fill()
            ctx.fillStyle = '#c8d6e5'
            ctx.fillText(`${ds.label}: ${formatCurveValue(Number(val), channel, yLabel)}`, chartArea.left + 14, labelY)
            labelY += 14
          }
        }
      }

      ctx.restore()
    },
  }).current

  // Stable plugins array, prevents react-chartjs-2 from seeing a new reference each render
  const pluginsArray = useRef([crosshairPlugin]).current

  // ── Chart data ─────────────────────────────────────────────────────────
  const chartData = useMemo(() => {
    const datasets: any[] = []
    const filtered = channelRows(curves, channel, curveType, side, seats, xChannel)
    const primaryAccelerationRows = curves.filter(r =>
      r.channel === 'accel' &&
      r.curve_type === curveType &&
      sideMatches(r.side, side) &&
      (seats ? seats.includes(r.seat) : true)
    )
    const primaryYSign = boatMotionDisplaySign(channel, filtered, primaryAccelerationRows)
    const primaryXRows = isParametric
      ? curves.filter(r =>
          r.channel === xChannel &&
          r.curve_type === curveType &&
          sideMatches(r.side, side) &&
          (seats ? seats.includes(r.seat) : true)
        )
      : []
    const primaryXSign = boatMotionDisplaySign(xChannel, primaryXRows, primaryAccelerationRows)

    for (const row of filtered) {
      const color = seatColorFor(row.seat)
      const showRowSide = showSideLabels && (!SHARED_STRETCHER_CHANNELS.has(channel) || isParametric)
      const seatLabel = `${seatLabelFor(row.seat)}${showRowSide && row.seat !== 0 ? ` ${sideSuffix(row.side)}` : ''}`
      const dash = showRowSide ? sideDash(row.side) : undefined

      if (isParametric) {
        const xRow = curves.find(r =>
          r.seat === row.seat && r.side === row.side &&
          r.channel === xChannel && r.curve_type === curveType
        )
        if (!xRow) continue
        const len = Math.min(row.mean.length, xRow.mean.length)
        const seam = seamIndex(len)
        const rowMean = displayMeanForChannel(channel, row.mean, primaryYSign)
        const xRowMean = displayMeanForChannel(xChannel, xRow.mean, primaryXSign)
        const yMean = rollAtSeam(rowMean.slice(0, len), seam)
        const xMean = rollAtSeam(xRowMean.slice(0, len), seam)
        const sourceIndices = sourceIndicesForRolled(len, seam)
        const sourceIndexLookup = sourceIndexLookupForRolled(len, seam)
        const points = yMean.map((y, i) => ({ x: xMean[i], y }))
        if (showStd && row.std) {
          const std = rollAtSeam(row.std.slice(0, len), seam)
          const upper = yMean.map((y, i) => ({ x: xMean[i], y: y + (std[i] ?? 0) }))
          const lower = yMean.map((y, i) => ({ x: xMean[i], y: y - (std[i] ?? 0) }))
          datasets.push({ data: upper, showLine: true, fill: '+1', backgroundColor: color + '18', borderColor: 'transparent', pointRadius: 0, tension: 0.3, order: 2 })
          datasets.push({ data: lower, showLine: true, fill: false, borderColor: 'transparent', pointRadius: 0, tension: 0.3, order: 2 })
        }
        datasets.push({ label: seatLabel, data: points, showLine: true, borderColor: color, borderWidth: lineWidth, borderDash: dash, pointRadius: 0, tension: 0.3, order: 1, seatNumber: row.seat, isHoverReference: row.seat === strokeSeat, sourceIndices, sourceIndexLookup })
      } else {
        const rowMean = displayMeanForChannel(channel, row.mean, primaryYSign)
        const wrapIdx = seamIndex(rowMean.length)
        const meanClean = [...rowMean] as (number | null)[]
        meanClean[wrapIdx] = null
        if (showStd && row.std) {
          const upper = rowMean.map((v, i) => i === wrapIdx ? null : v + (row.std?.[i] ?? 0))
          const lower = rowMean.map((v, i) => i === wrapIdx ? null : v - (row.std?.[i] ?? 0))
          datasets.push({ data: upper, fill: '+1', backgroundColor: color + '18', borderColor: 'transparent', pointRadius: 0, tension: 0.3, cubicInterpolationMode: 'monotone', order: 2, spanGaps: false })
          datasets.push({ data: lower, fill: false, borderColor: 'transparent', pointRadius: 0, tension: 0.3, cubicInterpolationMode: 'monotone', order: 2, spanGaps: false })
        }
        datasets.push({ label: seatLabel, data: meanClean, borderColor: color, borderWidth: lineWidth, borderDash: dash, pointRadius: 0, tension: 0.3, cubicInterpolationMode: 'monotone', order: 1, spanGaps: false })
      }
    }

    if (overlays) {
      for (const overlay of overlays) {
        const overlayFirstNameCounts = firstNameCounts(overlay.rowerNames || rowerNames)
        const oFiltered = channelRows(overlay.curves, channel, curveType, side, seats, xChannel)
        const overlayAccelerationRows = overlay.curves.filter(r =>
          r.channel === 'accel' &&
          r.curve_type === curveType &&
          sideMatches(r.side, side) &&
          (seats ? seats.includes(r.seat) : true)
        )
        const overlayYSign = boatMotionDisplaySign(channel, oFiltered, overlayAccelerationRows)
        const overlayXRows = isParametric
          ? overlay.curves.filter(r =>
              r.channel === xChannel &&
              r.curve_type === curveType &&
              sideMatches(r.side, side) &&
              (seats ? seats.includes(r.seat) : true)
            )
          : []
        const overlayXSign = boatMotionDisplaySign(xChannel, overlayXRows, overlayAccelerationRows)
        for (const row of oFiltered) {
          // Same rule as the primary: when the caller passes its own
          // seatColors map, never fall back to the seat-number palette.
          let defaultSeatColor: string
          if (overlay.seatColors) {
            defaultSeatColor = overlay.seatColors[row.seat] || '#7a8ba0'
          } else {
            defaultSeatColor = row.seat === 0 ? getClubColor() : (SEAT_COLORS[row.seat] || '#888')
          }
        const color = overlay.useSeatColor
          ? defaultSeatColor
          : OVERLAY_COLORS[overlay.colorIndex % OVERLAY_COLORS.length]
        const dash = overlay.borderDash ?? [6, 3]
 const borderWidth = overlay.borderWidth ?? lineWidth
 const underlayColor = overlay.underlayColor
 const underlayWidth = overlay.underlayWidth ?? (borderWidth + 2)
          const rowerLabel =
            row.seat === 0
              ? 'Boat'
              : (() => {
                  const name = overlay.rowerNames?.[row.seat] || rowerNames?.[row.seat]
                  return name ? compactRowerName(name, overlayFirstNameCounts) : `Seat ${row.seat}`
                })()
          const showRowSide = showSideLabels && (!SHARED_STRETCHER_CHANNELS.has(channel) || isParametric)
          const seatLabel = row.seat === 0 || overlay.hideRowerLabel
            ? overlay.label
            : `${overlay.label} - ${rowerLabel}${showRowSide ? ` ${sideSuffix(row.side)}` : ''}`
          const sideAwareDash = showRowSide ? sideDash(row.side, dash) : dash
          if (isParametric) {
            const xRow = overlay.curves.find(r =>
              r.seat === row.seat && r.side === row.side &&
              r.channel === xChannel && r.curve_type === curveType
            )
            if (!xRow) continue
            const len = Math.min(row.mean.length, xRow.mean.length)
            const seam = seamIndex(len)
            const rowMean = displayMeanForChannel(channel, row.mean, overlayYSign)
            const xRowMean = displayMeanForChannel(xChannel, xRow.mean, overlayXSign)
            const yMean = rollAtSeam(rowMean.slice(0, len), seam)
            const xMean = rollAtSeam(xRowMean.slice(0, len), seam)
            const sourceIndices = sourceIndicesForRolled(len, seam)
 const sourceIndexLookup = sourceIndexLookupForRolled(len, seam)
 const overlayPoints = yMean.map((y, i) => ({ x: xMean[i], y }))
 if (underlayColor && underlayWidth > borderWidth) {
 datasets.push({ data: overlayPoints, showLine: true, borderColor: underlayColor, borderWidth: underlayWidth, borderDash: sideAwareDash, pointRadius: 0, tension: 0.3, order: 0, seatNumber: row.seat, isHoverReference: false, sourceIndices, sourceIndexLookup })
 }
 datasets.push({ label: seatLabel, data: overlayPoints, showLine: true, borderColor: color, borderWidth, borderDash: sideAwareDash, pointRadius: 0, tension: 0.3, order: 1, seatNumber: row.seat, isHoverReference: false, sourceIndices, sourceIndexLookup })
          } else {
 const rowMean = displayMeanForChannel(channel, row.mean, overlayYSign)
 const wrapIdx = seamIndex(rowMean.length)
 const meanClean = [...rowMean] as (number | null)[]
 meanClean[wrapIdx] = null
 if (underlayColor && underlayWidth > borderWidth) {
 datasets.push({ data: meanClean, borderColor: underlayColor, borderWidth: underlayWidth, borderDash: sideAwareDash, pointRadius: 0, tension: 0.3, cubicInterpolationMode: 'monotone', order: 0, spanGaps: false })
 }
 datasets.push({ label: seatLabel, data: meanClean, borderColor: color, borderWidth, borderDash: sideAwareDash, pointRadius: 0, tension: 0.3, cubicInterpolationMode: 'monotone', order: 1, spanGaps: false })
          }
        }
      }
    }

    if (isParametric) return { datasets }
    return { labels: Array.from({ length: 200 }, (_, i) => Math.round(i * 0.5)), datasets }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curves, channel, curveType, xChannel, seats, side, showStd, showSideLabels, overlays, rowerNames, strokeSeat])

  const legendItems = useMemo(() => {
    const seen = new Set<string>()
    return chartData.datasets
      .filter((ds: any) => ds.label && ds.borderColor && ds.borderColor !== 'transparent')
      .map((ds: any) => ({
        label: String(ds.label),
        color: typeof ds.borderColor === 'string' ? ds.borderColor : '#7a8ba0',
        dash: Array.isArray(ds.borderDash) && ds.borderDash.length > 0,
      }))
      .filter(item => {
        const key = `${item.label}-${item.color}-${item.dash ? 'dash' : 'solid'}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
  }, [chartData])

  const autoBounds = useMemo(() => {
    const xVals: number[] = []
    const yVals: number[] = []

    for (const ds of chartData.datasets) {
      const data = ds.data as unknown[]
      for (const pt of data) {
        if (pt == null) continue
        if (typeof pt === 'number') {
          if (isFinite(pt)) yVals.push(pt)
          continue
        }
        if (typeof pt === 'object') {
          const maybePoint = pt as { x?: unknown; y?: unknown }
          const x = typeof maybePoint.x === 'number' ? maybePoint.x : Number(maybePoint.x)
          const y = typeof maybePoint.y === 'number' ? maybePoint.y : Number(maybePoint.y)
          if (isFinite(x)) xVals.push(x)
          if (isFinite(y)) yVals.push(y)
        }
      }
    }

    return {
      x: paddedBounds(xVals, 0.08),
      y: paddedBounds(yVals, 0.12),
    }
  }, [chartData])

  const options: any = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: {
        display: false,
        position: 'top' as const,
        labels: {
          color: '#e8edf2',
          font: { size: 12, weight: 600 },
          filter: (item: any) => !!item.text,
          boxWidth: 34,
          boxHeight: 4,
          padding: 14,
          generateLabels: (chart: any) => ChartJS.defaults.plugins.legend.labels.generateLabels(chart)
            .filter((item: any) => !!item.text)
            .map((item: any) => ({
              ...item,
              fillStyle: 'transparent',
              lineWidth: 3,
              strokeStyle: item.strokeStyle || item.fillStyle,
            })),
        },
      },
      tooltip: {
        ...viewerTooltipPosition(),
        enabled: !hideCrosshairLabels,
        mode: 'index' as const,
        intersect: false,
        backgroundColor: '#172438', borderColor: '#253d56', borderWidth: 1,
        titleColor: '#e2e8f0', bodyColor: '#7a8ba0',
        callbacks: {
          label: (ctx: any) => {
            const raw = ctx.raw
            const value = raw != null && typeof raw === 'object' ? raw.y : raw
            const label = ctx.dataset?.label || ''
            return `${label}: ${formatCurveValue(Number(value), channel, yLabel)}`
          },
          labelColor: (ctx: any) => {
            const color = tooltipColorForDataset(ctx)
            return { borderColor: color, backgroundColor: color }
          },
        },
      },
      zoom: {
        limits: {
          x: { min: 'original', max: 'original' },
          y: { min: 'original', max: 'original' },
        },
        pan: {
          enabled: true,
          mode: 'xy',
          threshold: 8,
          onPanStart: ({ chart }: any) => chart.isZoomedOrPanned?.() === true,
        },
        zoom: {
          wheel: { enabled: wheelZoom },
          pinch: { enabled: true },
          drag: { enabled: true, modifierKey: 'shift' },
          mode: 'xy',
        },
      },
    },
    scales: {
      x: {
        ...(axisPrefs.xMin !== undefined ? { min: axisPrefs.xMin } : isParametric && autoBounds.x.min != null ? { min: autoBounds.x.min } : {}),
        ...(axisPrefs.xMax !== undefined ? { max: axisPrefs.xMax } : isParametric && autoBounds.x.max != null ? { max: autoBounds.x.max } : {}),
        title: { display: true, text: isParametric ? 'Angle (deg)' : 'Stroke %', color: '#7a8ba0', font: { size: 11 } },
        ticks: { color: '#7a8ba0', font: { size: 10 }, maxTicksLimit: 10 },
        grid: { color: '#253d5640' },
      },
      y: {
        ...(axisPrefs.yMin !== undefined ? { min: axisPrefs.yMin } : autoBounds.y.min != null ? { min: autoBounds.y.min } : {}),
        ...(axisPrefs.yMax !== undefined ? { max: axisPrefs.yMax } : autoBounds.y.max != null ? { max: autoBounds.y.max } : {}),
        title: { display: !!yLabel, text: yLabel || '', color: '#7a8ba0', font: { size: 11 } },
        ticks: { color: '#7a8ba0', font: { size: 10 } },
        grid: { color: '#253d5640' },
      },
    },
  }

  return (
    <div
      className="rounded-xl border border-border bg-[var(--surface-0)] p-4"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
    >
      {/* Title row */}
      {title && (
        <div className="mb-3 flex items-center justify-between">
          <button
            onClick={() => setAxisOpen(v => !v)}
            className={`text-sm font-medium transition flex items-center gap-1.5 ${
              axisOpen || hasAxisPrefs ? 'text-teal' : 'text-muted hover:text-[var(--ink-primary)]'
            }`}
            title="Set axis range"
          >
            {title}
            {hasAxisPrefs && <span className="text-[9px] rounded-full bg-teal/20 border border-teal/30 px-1.5 py-0.5 text-teal">custom axis</span>}
            <svg className={`h-3 w-3 transition-transform ${axisOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
<div className="flex items-center gap-1">
<div className="flex overflow-hidden rounded border border-border bg-[var(--surface-0)]" title={wheelZoom ? 'Wheel zoom, drag after zoom to pan' : 'Use buttons to zoom, drag after zoom to pan'}>
<button type="button" onClick={e => { e.stopPropagation(); zoomBy(1.5) }} className="px-1.5 py-0.5 text-[10px] font-semibold text-muted/70 transition hover:bg-[var(--surface-3)] hover:text-teal" title="Zoom in">+</button>
<button type="button" onClick={e => { e.stopPropagation(); zoomBy(1 / 1.5) }} className="border-l border-border px-1.5 py-0.5 text-[10px] font-semibold text-muted/70 transition hover:bg-[var(--surface-3)] hover:text-teal" title="Zoom out">-</button>
<button type="button" onClick={e => { e.stopPropagation(); resetZoom() }} className="border-l border-border px-1.5 py-0.5 text-[10px] font-semibold text-muted/70 transition hover:bg-[var(--surface-3)] hover:text-teal" title="Reset zoom">1:1</button>
</div>
<button
type="button"
onClick={e => { e.stopPropagation(); setExpanded(true) }}
title="Expand chart"
aria-label="Expand chart"
className="text-muted hover:text-teal transition"
>
<svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
<path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" />
</svg>
</button>
</div>
        </div>
      )}

      {/* Axis range panel */}
      {axisOpen && (
        <div className="mb-3 rounded-lg border border-border/60 bg-[var(--surface-1)] px-3 py-2.5 space-y-2">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-1.5 text-[11px] text-muted">
              <span className="font-medium w-4">X:</span>
              <input type="number" placeholder="min" value={draftXMin} onChange={e => setDraftXMin(e.target.value)}
                className="w-16 rounded border border-border bg-transparent px-1.5 py-0.5 text-[11px] text-[var(--ink-primary)] outline-none focus:border-teal" />
              <span>-</span>
              <input type="number" placeholder="max" value={draftXMax} onChange={e => setDraftXMax(e.target.value)}
                className="w-16 rounded border border-border bg-transparent px-1.5 py-0.5 text-[11px] text-[var(--ink-primary)] outline-none focus:border-teal" />
            </div>
            <div className="flex items-center gap-1.5 text-[11px] text-muted">
              <span className="font-medium w-4">Y:</span>
              <input type="number" placeholder="min" value={draftYMin} onChange={e => setDraftYMin(e.target.value)}
                className="w-16 rounded border border-border bg-transparent px-1.5 py-0.5 text-[11px] text-[var(--ink-primary)] outline-none focus:border-teal" />
              <span>-</span>
              <input type="number" placeholder="max" value={draftYMax} onChange={e => setDraftYMax(e.target.value)}
                className="w-16 rounded border border-border bg-transparent px-1.5 py-0.5 text-[11px] text-[var(--ink-primary)] outline-none focus:border-teal" />
            </div>
            <div className="flex items-center gap-2 ml-auto">
              <button onClick={applyAxis}
                className="rounded-lg bg-teal px-3 py-1 text-[11px] font-semibold text-white hover:bg-teal/80 transition">Apply</button>
              {hasAxisPrefs && (
                <button onClick={clearAxis}
                  className="text-[11px] text-muted hover:text-red-400 transition">Reset</button>
              )}
            </div>
          </div>
          <p className="text-[10px] text-muted/50">Leave blank for auto. Saved per coach per chart type.</p>
        </div>
      )}

      {!hideLegend && legendItems.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs font-semibold text-[var(--ink-primary)]">
          {legendItems.map(item => (
            <div key={`${item.label}-${item.color}-${item.dash ? 'dash' : 'solid'}`} className="flex items-center gap-2">
              <span
                className="h-0 w-9 rounded-full"
                style={{ borderTop: `3px ${item.dash ? 'dashed' : 'solid'} ${item.color}` }}
              />
              <span>{item.label}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ height }}>
        {isParametric ? (
          <Scatter ref={chartRef} data={chartData} options={options} plugins={pluginsArray} />
        ) : (
          <Line
            ref={chartRef}
            data={chartData}
            options={options}
            plugins={pluginsArray}
          />
        )}
      </div>

      {/* Enlarged inspector modal */}
      {expanded && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-[#0b1626]/90 p-4 backdrop-blur-sm"
          onClick={() => setExpanded(false)}
          onMouseMove={e => e.stopPropagation()}
        >
          <div
            className="relative flex h-[92vh] w-full max-w-[1500px] flex-col overflow-hidden rounded-xl border border-[var(--edge-strong)] bg-[var(--surface-0)] p-6 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-[var(--ink-primary)]">
                {title || 'Chart'}
              </h2>
              <button
                type="button"
                onClick={() => setExpanded(false)}
                aria-label="Close"
                className="text-muted hover:text-[var(--ink-primary)] transition"
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="min-h-0 flex-1">
              {isParametric ? (
                <Scatter
                  data={chartData}
                  options={{
                    ...options,
                    maintainAspectRatio: false,
                    plugins: {
                      ...options.plugins,
                      legend: { ...options.plugins.legend, display: true },
                      tooltip: { ...options.plugins.tooltip, enabled: true },
                    },
                  }}
                />
              ) : (
                <Line
                  data={chartData}
                  options={{
                    ...options,
                    maintainAspectRatio: false,
                    plugins: {
                      ...options.plugins,
                      legend: { ...options.plugins.legend, display: true },
                      tooltip: { ...options.plugins.tooltip, enabled: true },
                    },
                  }}
                />
              )}
            </div>
            <p className="mt-2 text-[10px] text-muted/50">Press Esc or click outside to close.</p>
          </div>
        </div>
      )}
    </div>
  )
}
