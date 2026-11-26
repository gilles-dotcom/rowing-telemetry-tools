'use client'

import { useMemo, useState, useRef, useEffect } from 'react'
import { getClubColor, SEAT_COLORS, shortRowerName } from '@/lib/types'
import type { SessionPiece } from '@/lib/types'
import { pieceColor } from '@/lib/piece-colors'
import type { PeriodicData } from '@/lib/parse-periodic'

interface RawTimelineProps {
  overview: {
    time_s: number[]
    boat_power: number[]
    boat_speed: number[]
    boat_rate: number[]
    total_strokes: number
  }
  onAnalyze: (startMs: number, endMs: number) => void
  onSelectionChange?: (startMs: number | null, endMs: number | null) => void
  periodicData?: PeriodicData | null
  rowerNames?: Record<string, string>
  savedPieces?: SessionPiece[]
  selectedSavedPieceId?: string | null
  comparePieceIds?: string[]
  onSelectSavedPiece?: (pieceId: string) => void
}

const SMOOTHING_OPTIONS = [
  { value: 1, label: 'Off' },
  { value: 3, label: '3 str' },
  { value: 5, label: '5 str' },
  { value: 10, label: '10 str' },
  { value: 20, label: '20 str' },
] as const

const ACTIVE_POWER_THRESHOLD_W = 10
const ACTIVE_WINDOW_PADDING_S = 90

function smooth(arr: number[], window: number): number[] {
  if (window <= 1) return arr
  const half = Math.floor(window / 2)
  return arr.map((_, i) => {
    const start = Math.max(0, i - half)
    const end = Math.min(arr.length, i + half + 1)
    let sum = 0, count = 0
    for (let j = start; j < end; j++) {
      if (arr[j] != null && !isNaN(arr[j])) { sum += arr[j]; count++ }
    }
    return count > 0 ? sum / count : 0
  })
}

function speedToPace(speed: number): number | null {
  if (!speed || speed <= 0) return null
  return 500 / speed
}

function formatPace(paceSeconds: number | null): string {
  if (paceSeconds == null || paceSeconds > 600) return '-'
  const m = Math.floor(paceSeconds / 60)
  const s = Math.floor(paceSeconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatTime(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60)
  const s = Math.floor(totalSeconds % 60)
  return m > 0 ? `${m}:${s.toString().padStart(2, '0')}` : `${s}s`
}

function formatDistance(meters: number | null): string | null {
  if (meters == null || !Number.isFinite(meters) || meters <= 0) return null
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)}m`
}

function formatWatts(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  return Math.round(value).toString()
}

function formatRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  return Math.round(value).toString()
}

// Pure helper, no component closure needed
function findNearestIdx(timeS: number[], t: number): number {
  let best = 0
  for (let i = 1; i < timeS.length; i++) {
    if (Math.abs(timeS[i] - t) < Math.abs(timeS[best] - t)) best = i
  }
  return best
}

export default function RawTimeline({
  overview,
  onAnalyze,
  onSelectionChange,
  periodicData,
  rowerNames,
  savedPieces = [],
  selectedSavedPieceId = null,
  comparePieceIds = [],
  onSelectSavedPiece,
}: RawTimelineProps) {
  const [chart, setChart] = useState<{ Line: any } | null>(null)
  const [startIdx, setStartIdx] = useState<number | null>(null)
  const [endIdx, setEndIdx] = useState<number | null>(null)
  const [smoothWindow, setSmoothWindow] = useState(5)
  const [xZoomMin, setXZoomMin] = useState<number | undefined>(undefined)
  const [xZoomMax, setXZoomMax] = useState<number | undefined>(undefined)
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const chartRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const isDraggingRef = useRef(false)
  const dragStartIdxRef = useRef<number | null>(null)
  const mouseDownPointRef = useRef<{ x: number; y: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const {
        Chart: ChartJS, CategoryScale, LinearScale,
        PointElement, LineElement, Filler, Tooltip, Legend,
      } = await import('chart.js')
      const annotationPlugin = (await import('chartjs-plugin-annotation')).default
      const { Line } = await import('react-chartjs-2')
      ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend, annotationPlugin)
      if (!cancelled) setChart({ Line })
    })()
    return () => { cancelled = true }
  }, [])

  const [showPace, setShowPace] = useState(true)
  const [showRate, setShowRate] = useState(true)
  const [showRowerPower, setShowRowerPower] = useState(false)
  const [showBoatPower, setShowBoatPower] = useState(true)

  useEffect(() => {
    setShowPace(localStorage.getItem('viewer-show_pace') !== 'false')
    setShowRate(localStorage.getItem('viewer-show_rate') !== 'false')
    setShowRowerPower(localStorage.getItem('viewer-show_rower_power') === 'true')
    setShowBoatPower(localStorage.getItem('viewer-show_boat_power') !== 'false')
    const savedSmooth = localStorage.getItem('viewer-smoothing')
    if (savedSmooth) {
      const n = parseInt(savedSmooth, 10)
      if (SMOOTHING_OPTIONS.some(o => o.value === n)) setSmoothWindow(n)
    }
  }, [])

  const timeS = overview.time_s
  const N = timeS.length

  const smoothedPower = useMemo(() => smooth(overview.boat_power, smoothWindow), [overview.boat_power, smoothWindow])
  const smoothedSpeed = useMemo(() => smooth(overview.boat_speed, smoothWindow), [overview.boat_speed, smoothWindow])
  const smoothedRate = useMemo(() => smooth(overview.boat_rate, smoothWindow), [overview.boat_rate, smoothWindow])
  const smoothedPace = useMemo(() => smoothedSpeed.map(speedToPace), [smoothedSpeed])

  const seatPowerSmoothed = useMemo(() => {
    if (!periodicData?.seatPowerW) return new Map<number, number[]>()
    const result = new Map<number, number[]>()
    for (const [seat, arr] of periodicData.seatPowerW) {
      result.set(seat, smooth(arr, smoothWindow))
    }
    return result
  }, [periodicData, smoothWindow])

  const GAP_THRESHOLD = 15

  const toPoints = (arr: (number | null)[]) => {
    const pts: { x: number; y: number | null }[] = []
    for (let i = 0; i < arr.length; i++) {
      if (i > 0 && timeS[i] - timeS[i - 1] > GAP_THRESHOLD) {
        pts.push({ x: timeS[i - 1] + 0.1, y: null })
      }
      pts.push({ x: timeS[i], y: arr[i] })
    }
    return pts
  }

  const isZoomed = xZoomMin !== undefined || xZoomMax !== undefined
  const activeDomain = useMemo(() => {
    if (!N) return { min: 0, max: 0 }

    const activeIndices = overview.boat_power
      .map((power, idx) => ({ power, idx }))
      .filter(({ power, idx }) =>
        Number.isFinite(timeS[idx]) &&
        Number.isFinite(power) &&
        power > ACTIVE_POWER_THRESHOLD_W
      )

    const firstIdx = activeIndices[0]?.idx ?? 0
    const lastIdx = activeIndices[activeIndices.length - 1]?.idx ?? (N - 1)
    const min = Math.max(0, (timeS[firstIdx] ?? 0) - ACTIVE_WINDOW_PADDING_S)
    const max = Math.max(min + 60, (timeS[lastIdx] ?? min) + ACTIVE_WINDOW_PADDING_S)

    return { min, max }
  }, [N, overview.boat_power, timeS])
  const tMin = activeDomain.min
  const tMax = activeDomain.max
  const visibleDurationS = (xZoomMax ?? tMax) - (xZoomMin ?? tMin)
  const xTickStepS = visibleDurationS > 2 * 60 * 60 ? 20 * 60 : 10 * 60

  // ── Zoom controls ──────────────────────────────────────────────────────────
  function zoomIn() {
    // If there's a selection, zoom the chart to it and clear the box
    if (startIdx != null && endIdx != null) {
      setXZoomMin(timeS[startIdx])
      setXZoomMax(timeS[endIdx])
      setStartIdx(null)
      setEndIdx(null)
      onSelectionChange?.(null, null)
      return
    }
    // Otherwise shrink 25%
    const lo = xZoomMin ?? tMin, hi = xZoomMax ?? tMax
    const range = hi - lo
    const shrink = range * 0.25
    const newLo = lo + shrink, newHi = hi - shrink
    if (newHi - newLo < 10) return
    setXZoomMin(newLo); setXZoomMax(newHi)
  }
  function zoomOut() {
    if (!isZoomed) return
    const lo = xZoomMin ?? tMin, hi = xZoomMax ?? tMax
    const range = hi - lo
    const grow = range * 0.35
    const newLo = Math.max(tMin, lo - grow), newHi = Math.min(tMax, hi + grow)
    if (newLo <= tMin && newHi >= tMax) { setXZoomMin(undefined); setXZoomMax(undefined) }
    else { setXZoomMin(newLo); setXZoomMax(newHi) }
  }
  function resetZoom() { setXZoomMin(undefined); setXZoomMax(undefined) }

  // ── Drag-to-select ────────────────────────────────────────────────────────
  function handleMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    const c = chartRef.current
    const container = containerRef.current
    if (!c?.chartArea || !container) return
    const rect = container.getBoundingClientRect()
    const relX = e.clientX - rect.left
    const relY = e.clientY - rect.top
    // Only react inside the chart plot area
    if (relX < c.chartArea.left || relX > c.chartArea.right) return
    if (relY < c.chartArea.top || relY > c.chartArea.bottom) return

    e.preventDefault()
    isDraggingRef.current = true
    mouseDownPointRef.current = { x: e.clientX, y: e.clientY }

    const t = c.scales.x.getValueForPixel(relX) ?? (xZoomMin ?? tMin)
    const idx = findNearestIdx(timeS, t)

    dragStartIdxRef.current = idx
    setStartIdx(idx)
    setEndIdx(null)
    onSelectionChange?.(timeS[idx] * 1000, null)
  }

  useEffect(() => {
    function clientXToIdx(clientX: number): number {
      const c = chartRef.current
      const container = containerRef.current
      if (!c?.chartArea || !c.scales?.x || !container) return 0
      const rect = container.getBoundingClientRect()
      const canvasX = clientX - rect.left
      const t = c.scales.x.getValueForPixel(canvasX) ?? (xZoomMin ?? tMin)
      return findNearestIdx(timeS, t)
    }

    function onMove(e: MouseEvent) {
      if (!isDraggingRef.current) return
      const startI = dragStartIdxRef.current
      if (startI == null) return
      const cur = clientXToIdx(e.clientX)
      const [s, en] = startI <= cur ? [startI, cur] : [cur, startI]
      setStartIdx(s)
      setEndIdx(en)
      onSelectionChange?.(timeS[s] * 1000, timeS[en] * 1000)
    }

    function onUp(e: MouseEvent) {
      if (!isDraggingRef.current) return
      isDraggingRef.current = false
      const startI = dragStartIdxRef.current
      if (startI == null) return
      const cur = clientXToIdx(e.clientX)
      const [s, en] = startI <= cur ? [startI, cur] : [cur, startI]
      const mouseDownPoint = mouseDownPointRef.current
      mouseDownPointRef.current = null
      const pointerTravel = mouseDownPoint
        ? Math.hypot(e.clientX - mouseDownPoint.x, e.clientY - mouseDownPoint.y)
        : Infinity
      if (pointerTravel < 5 && savedPieces.length > 0 && onSelectSavedPiece) {
        const tMs = timeS[cur] * 1000
        const clickedPiece = savedPieces.find(piece => tMs >= piece.start_ms && tMs <= piece.end_ms)
        if (clickedPiece) {
          setStartIdx(null)
          setEndIdx(null)
          onSelectionChange?.(null, null)
          onSelectSavedPiece(clickedPiece.id)
          return
        }
      }
      // Discard accidental clicks (< 10s selection)
      if (timeS[en] - timeS[s] < 10) {
        setStartIdx(null); setEndIdx(null)
        onSelectionChange?.(null, null)
        return
      }
      setStartIdx(s)
      setEndIdx(en)
      onSelectionChange?.(timeS[s] * 1000, timeS[en] * 1000)
      onAnalyze(timeS[s] * 1000, timeS[en] * 1000)
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [N, xZoomMin, xZoomMax, timeS, tMin, tMax, onAnalyze, onSelectionChange, savedPieces, onSelectSavedPiece])

  // ── Selection label ───────────────────────────────────────────────────────
  const hasSelection = startIdx != null && endIdx != null
  const selectionLabel = useMemo(() => {
    if (startIdx == null) return 'Drag to select a segment'
    if (endIdx == null) return `Selecting: ${formatTime(timeS[startIdx])}...`
    const dur = timeS[endIdx] - timeS[startIdx]
    let distance = 0
    for (let i = startIdx + 1; i <= endIdx; i++) {
      const dt = Math.max(0, timeS[i] - timeS[i - 1])
      const v0 = overview.boat_speed[i - 1] || 0
      const v1 = overview.boat_speed[i] || 0
      distance += 0.5 * (v0 + v1) * dt
    }
    const distanceLabel = formatDistance(distance)
    return [
      `Selected: ${formatTime(timeS[startIdx])}-${formatTime(timeS[endIdx])}`,
      formatTime(dur),
      distanceLabel,
    ].filter(Boolean).join(' / ')
  }, [startIdx, endIdx, timeS, overview.boat_speed])

  const rosterFullNames = showRowerPower
    ? Array.from(seatPowerSmoothed.keys()).map(s => rowerNames?.[s] ?? `Seat ${s}`)
    : []
  const readoutIdx = hoverIdx != null && hoverIdx >= 0 && hoverIdx < N ? hoverIdx : null
  const timelineLegend = [
    showBoatPower ? { label: 'Power', value: readoutIdx == null ? null : formatWatts(smoothedPower[readoutIdx]), color: getClubColor() } : null,
    showPace ? { label: 'Pace', value: readoutIdx == null ? null : formatPace(smoothedPace[readoutIdx] ?? null), color: '#51cf66' } : null,
    showRate ? { label: 'Rate', value: readoutIdx == null ? null : formatRate(smoothedRate[readoutIdx]), color: '#f59f00' } : null,
    ...(showRowerPower
      ? Array.from(seatPowerSmoothed.entries()).map(([seat, arr]) => {
          const fullName = rowerNames?.[seat] || `Seat ${seat}`
          return {
            label: shortRowerName(fullName, rosterFullNames),
            value: readoutIdx == null ? null : formatWatts(arr[readoutIdx]),
            color: SEAT_COLORS[seat] || '#888',
          }
        })
      : []),
  ].filter(Boolean) as { label: string; value: string | null; color: string }[]

  if (!chart) {
    return <div className="rounded-xl border border-border bg-[var(--surface-0)] p-4" style={{ height: 280 }} />
  }

  const { Line } = chart

  const showStrokeDots = smoothWindow === 1
  const boatPowerPointStyle = {
    pointRadius: showStrokeDots ? 2.5 : 0,
    pointHoverRadius: 4,
    pointHitRadius: 8,
    pointBorderWidth: showStrokeDots ? 1 : 1.5,
    pointBackgroundColor: getClubColor(),
    pointBorderColor: '#0b1825',
    tension: showStrokeDots ? 0 : 0.3,
  }
  const rowerPowerPointStyle = (color: string) => ({
    pointRadius: showStrokeDots ? 2 : 0,
    pointHoverRadius: 3.5,
    pointHitRadius: 8,
    pointBorderWidth: showStrokeDots ? 1 : 1.5,
    pointBackgroundColor: color,
    pointBorderColor: '#0b1825',
    tension: showStrokeDots ? 0 : 0.3,
  })

  const datasets: any[] = []
  if (showBoatPower) {
    datasets.push({
      label: 'Power (W)',
      data: toPoints(smoothedPower),
      borderColor: getClubColor(),
      borderWidth: 2,
      ...boatPowerPointStyle,
      yAxisID: 'y',
      spanGaps: false,
    })
  }
  if (showPace) datasets.push({
    label: 'Pace',
    data: toPoints(smoothedPace),
    borderColor: '#51cf66',
    borderWidth: 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHitRadius: 8,
    pointBorderWidth: 1.5,
    pointBackgroundColor: '#51cf66',
    pointBorderColor: '#0b1825',
    tension: 0.3,
    yAxisID: 'y1',
    spanGaps: false,
  })
  if (showRate) datasets.push({
    label: 'Rate',
    data: toPoints(smoothedRate),
    borderColor: '#f59f00',
    borderWidth: 1.75,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHitRadius: 8,
    pointBorderWidth: 1.5,
    pointBackgroundColor: '#f59f00',
    pointBorderColor: '#0b1825',
    tension: 0.3,
    yAxisID: 'y2',
    spanGaps: false,
  })

  if (showRowerPower) {
    for (const [seat, arr] of seatPowerSmoothed) {
      const color = SEAT_COLORS[seat] || '#888'
      const fullName = rowerNames?.[seat] || `Seat ${seat}`
      datasets.push({
        label: shortRowerName(fullName, rosterFullNames),
        data: toPoints(arr),
        borderColor: color,
        borderWidth: 1.5,
        ...rowerPowerPointStyle(color),
        yAxisID: 'y',
        spanGaps: false,
      })
    }
  }

  const validPaces = smoothedPace.filter((p): p is number => p != null && p < 300)
  const minPace = validPaces.length > 0 ? Math.floor(Math.min(...validPaces) / 10) * 10 : 90
  const maxPace = validPaces.length > 0 ? Math.ceil(Math.max(...validPaces) / 10) * 10 : 180
  const compareSet = new Set(comparePieceIds)
  const yMaxForBands = Math.max(
    1,
    ...smoothedPower.filter(Number.isFinite),
    ...Array.from(seatPowerSmoothed.values()).flat().filter(Number.isFinite),
  )
  const savedPieceAnnotations = Object.fromEntries(
    savedPieces.map((piece, index) => {
      const color = pieceColor(index)
      const selected = piece.id === selectedSavedPieceId
      const compared = compareSet.has(piece.id)
      return [
        `saved-piece-${piece.id}`,
        {
          type: 'box' as const,
          xMin: piece.start_ms / 1000,
          xMax: piece.end_ms / 1000,
          yMin: 0,
          yMax: yMaxForBands * 1.08,
          backgroundColor: `${color}${selected ? '30' : compared ? '24' : '16'}`,
          borderColor: selected || compared ? color : `${color}88`,
          borderWidth: selected ? 2 : compared ? 1.5 : 1,
          borderDash: compared && !selected ? [5, 4] : undefined,
          label: {
            display: selected || compared,
            content: piece.label || `Piece ${index + 1}`,
            position: 'start' as const,
            backgroundColor: color,
            color: '#0f1c2e',
            font: { size: 10, weight: '600' as const },
            padding: 4,
            yAdjust: 8,
          },
        },
      ]
    }),
  )

  const options: any = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'nearest', intersect: false },
    layout: { padding: { top: 26 } },
    onHover: (event: any, _elements: any[], chartInstance: any) => {
      const x = event?.x
      const y = event?.y
      const chartArea = chartInstance?.chartArea
      const xScale = chartInstance?.scales?.x
      if (
        x == null ||
        y == null ||
        !chartArea ||
        !xScale ||
        x < chartArea.left ||
        x > chartArea.right ||
        y < chartArea.top ||
        y > chartArea.bottom
      ) {
        setHoverIdx(null)
        return
      }
      const t = xScale.getValueForPixel(x)
      setHoverIdx(Number.isFinite(t) ? findNearestIdx(timeS, t) : null)
    },
    plugins: {
      legend: { display: false },
      tooltip: { enabled: false },
      annotation: {
        annotations: savedPieceAnnotations,
      },
    },
    scales: {
      x: {
        type: 'linear' as const,
        min: xZoomMin ?? tMin,
        max: xZoomMax ?? tMax,
        title: { display: true, text: 'Time', color: '#7a8ba0', font: { size: 11 } },
        ticks: {
          color: '#7a8ba0',
          font: { size: 10 },
          stepSize: xTickStepS,
          callback: (val: number) => formatTime(Math.round(val)),
        },
        grid: { color: 'rgba(37,61,86,0.3)' },
      },
      y: {
        position: 'left', min: 0,
        title: { display: true, text: 'Avg Power (W)', color: getClubColor(), font: { size: 11 } },
        ticks: { color: getClubColor(), font: { size: 10 } },
        grid: { color: 'rgba(37,61,86,0.3)' },
      },
      y1: {
        display: showPace, position: 'right', reverse: true, min: minPace, max: maxPace,
        title: { display: true, text: 'Pace /500m', color: '#51cf66', font: { size: 11 } },
        ticks: { color: '#51cf66', font: { size: 10 }, callback: (val: number) => formatPace(val) },
        grid: { display: false },
      },
      y2: {
        display: showRate, position: 'right',
        title: { display: true, text: 'Rate (spm)', color: '#f59f00', font: { size: 11 } },
        ticks: { color: '#f59f00', font: { size: 10 } },
        grid: { display: false },
      },
    },
  }

  // Selection box overlay, computed at render time from current chart scales
  const c = chartRef.current
  const selBox = (startIdx != null && endIdx != null && c?.chartArea && c.scales?.x)
    ? (() => {
        const s = c.scales.x.getPixelForValue(timeS[startIdx])
        const e = c.scales.x.getPixelForValue(timeS[endIdx])
        return {
          left: s,
          width: Math.max(2, e - s),
          top: c.chartArea.top,
          height: c.chartArea.bottom - c.chartArea.top,
        }
      })()
    : null

  return (
    <div>
      {/* Controls */}
      <div className="mb-2 flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">Smoothing:</span>
          <select
            value={smoothWindow}
            onChange={e => {
              const n = parseInt(e.target.value, 10)
              setSmoothWindow(n)
              localStorage.setItem('viewer-smoothing', String(n))
            }}
            className="rounded-lg border border-border bg-[var(--surface-1)] px-2 py-0.5 text-xs text-[var(--ink-primary)] focus:border-teal focus:outline-none"
          >
            {SMOOTHING_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        <div className="flex items-center gap-2">
          <button onClick={() => { setShowBoatPower(v => { const n = !v; localStorage.setItem('viewer-show_boat_power', String(n)); return n }) }}
            className={`rounded-lg px-2 py-0.5 text-xs font-medium transition ${showBoatPower ? 'bg-teal/20 text-teal border border-teal/30' : 'text-muted hover:text-[var(--ink-primary)] border border-transparent'}`}>Boat</button>
          <button onClick={() => { setShowPace(p => { const v = !p; localStorage.setItem('viewer-show_pace', String(v)); return v }) }}
            className={`rounded-lg px-2 py-0.5 text-xs font-medium transition ${showPace ? 'bg-[#51cf66]/20 text-[#51cf66] border border-[#51cf66]/30' : 'text-muted hover:text-[var(--ink-primary)] border border-transparent'}`}>Pace</button>
          <button onClick={() => { setShowRate(r => { const v = !r; localStorage.setItem('viewer-show_rate', String(v)); return v }) }}
            className={`rounded-lg px-2 py-0.5 text-xs font-medium transition ${showRate ? 'bg-[#f59f00]/20 text-[#f59f00] border border-[#f59f00]/30' : 'text-muted hover:text-[var(--ink-primary)] border border-transparent'}`}>Rate</button>
          {seatPowerSmoothed.size > 0 && (
            <button onClick={() => { setShowRowerPower(v => { const n = !v; localStorage.setItem('viewer-show_rower_power', String(n)); return n }) }}
              className={`rounded-lg px-2 py-0.5 text-xs font-medium transition ${showRowerPower ? 'bg-teal/20 text-teal border border-teal/30' : 'text-muted hover:text-[var(--ink-primary)] border border-transparent'}`}>Rowers</button>
          )}
        </div>

        <div
          className={`flex min-h-7 items-center rounded-full border px-2.5 text-[11px] font-medium ${
            hasSelection
              ? 'border-teal/30 bg-teal/10 text-teal'
              : 'border-border bg-[var(--surface-1)] text-muted/70'
          }`}
        >
          {selectionLabel}
        </div>

        {/* Zoom buttons */}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={resetZoom}
            disabled={!isZoomed}
            className="flex h-7 w-7 items-center justify-center rounded border border-border text-xs text-muted hover:text-[var(--ink-primary)] transition disabled:opacity-20"
            title="Reset zoom"
          >↺</button>
          <button
            onClick={zoomOut}
            disabled={!isZoomed}
            className="flex h-7 w-7 items-center justify-center rounded border border-border text-xs text-muted hover:text-[var(--ink-primary)] hover:border-teal/40 transition font-mono disabled:opacity-30"
            title="Zoom out"
          >−</button>
          <button
            onClick={zoomIn}
            className={`flex h-7 w-7 items-center justify-center rounded border text-xs font-mono transition ${
              hasSelection
                ? 'border-teal/50 text-teal hover:bg-teal/10'
                : 'border-border text-muted hover:text-[var(--ink-primary)] hover:border-teal/40'
            }`}
            title={hasSelection ? 'Zoom to selection' : 'Zoom in'}
          >+</button>
        </div>
      </div>

      {/* Chart + selection overlay */}
      <div
        ref={containerRef}
        className="relative"
        style={{ height: 200, cursor: 'crosshair' }}
        onMouseDown={handleMouseDown}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <Line ref={chartRef} data={{ datasets }} options={options} />

        <div className="pointer-events-none absolute left-16 right-16 top-0 z-10 flex min-h-6 items-center justify-center gap-4 overflow-hidden text-[11px] leading-5 tabular-nums">
          <span className="w-20 shrink-0 text-right text-[13px] font-normal leading-5 text-[var(--ink-primary)]">
            {readoutIdx == null ? '\u00A0' : formatTime(Math.round(timeS[readoutIdx]))}
          </span>
          <div className="flex min-w-0 flex-wrap items-center justify-center gap-x-3 gap-y-1">
            {timelineLegend.map(item => (
              <span key={item.label} className="inline-grid grid-cols-[0.75rem_auto_2.75rem] items-center gap-1 whitespace-nowrap leading-5">
                <span className="h-3 w-3 border-2 bg-transparent" style={{ borderColor: item.color }} />
                <span className="font-semibold text-[var(--ink-primary)]">{item.label}</span>
                <span className="text-left text-[13px] font-normal leading-5 text-[var(--ink-primary)]">
                  {item.value ?? '\u00A0'}
                </span>
              </span>
            ))}
          </div>
        </div>

        {selBox && (
          <div
            className="absolute pointer-events-none"
            style={{
              left: selBox.left,
              top: selBox.top,
              width: selBox.width,
              height: selBox.height,
              background: 'rgba(0,180,216,0.12)',
              border: '1.5px solid #00b4d8',
              borderRadius: 2,
            }}
          />
        )}
      </div>

      <div className="mt-2 h-3" />
    </div>
  )
}
