'use client'

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { shortRowerName, type CurveRow, type PeriodicSnapshot, type SessionPiece } from '@/lib/types'
import { parseCurvesParquet } from '@/lib/parse-curves'
import { pieceCurvesUrl } from '@/lib/session-cache'
import { pieceColorById } from '@/lib/piece-colors'
import CurveChart from './CurveChart'
import BoatPaceAccelChart from './BoatPaceAccelChart'
import { CurveHoverProvider } from './CurveHoverContext'

type MetricKey =
  | 'avg_power_w'
  | 'avg_peak_force_n'
  | 'avg_catch_angle_deg'
  | 'avg_finish_angle_deg'
  | 'avg_arc_deg'
  | 'avg_slip_deg'
  | 'avg_wash_deg'
  | 'avg_drive_time_ms'
  | 'avg_recovery_time_ms'
  | 'avg_ratio'

const METRICS: { key: MetricKey; label: string; unit: string; decimals: number }[] = [
  { key: 'avg_power_w', label: 'Power', unit: 'W', decimals: 0 },
  { key: 'avg_peak_force_n', label: 'Peak force', unit: 'N', decimals: 0 },
  { key: 'avg_catch_angle_deg', label: 'Catch', unit: 'deg', decimals: 1 },
  { key: 'avg_finish_angle_deg', label: 'Finish', unit: 'deg', decimals: 1 },
  { key: 'avg_arc_deg', label: 'Arc', unit: 'deg', decimals: 1 },
  { key: 'avg_slip_deg', label: 'Slip', unit: 'deg', decimals: 1 },
  { key: 'avg_wash_deg', label: 'Wash', unit: 'deg', decimals: 1 },
  { key: 'avg_drive_time_ms', label: 'Drive', unit: 'ms', decimals: 0 },
  { key: 'avg_recovery_time_ms', label: 'Recovery', unit: 'ms', decimals: 0 },
  { key: 'avg_ratio', label: 'Ratio', unit: '', decimals: 2 },
]

type CompareChart = {
  id: string
  label: string
  channel: string
  curveType: 'time_based' | 'parametric_raw'
  xChannel?: string
  yLabel: string
  scope: 'rower' | 'boat' | 'boat_pace_accel'
  height?: number
  visible: boolean
}

const COMPARE_CHARTS: CompareChart[] = [
  { id: 'force_angle', label: 'Force vs Angle', channel: 'forcex_raw', curveType: 'parametric_raw', xChannel: 'angle_raw', yLabel: 'Force (N)', scope: 'rower', height: 280, visible: true },
  { id: 'force_time', label: 'Force vs Time', channel: 'forcex', curveType: 'time_based', yLabel: 'Force (N)', scope: 'rower', visible: true },
  { id: 'anglevel', label: 'Angle Velocity', channel: 'anglevel', curveType: 'time_based', yLabel: 'deg/s', scope: 'rower', visible: true },
  { id: 'pace_accel', label: 'Pace + Acceleration', channel: 'speed', curveType: 'time_based', yLabel: 'Pace /500m', scope: 'boat_pace_accel', visible: true },
  { id: 'angle_time', label: 'Angle vs Time', channel: 'angle', curveType: 'time_based', yLabel: 'Angle (deg)', scope: 'rower', visible: false },
  { id: 'forcey_time', label: 'Force Y', channel: 'forcey', curveType: 'time_based', yLabel: 'Force (N)', scope: 'rower', visible: false },
  { id: 'seatpos_time', label: 'Seat Position', channel: 'seatpos', curveType: 'time_based', yLabel: 'Seat Position (mm)', scope: 'rower', visible: false },
  { id: 'seatpos_angle', label: 'Seat Position vs Angle', channel: 'seatpos_raw', curveType: 'parametric_raw', xChannel: 'angle_raw', yLabel: 'Seat Position (mm)', scope: 'rower', height: 280, visible: false },
  { id: 'seatposvel_time', label: 'Seat Position Velocity', channel: 'seatposvel', curveType: 'time_based', yLabel: 'Seat Position Velocity (mm/s)', scope: 'rower', visible: false },
  { id: 'stretcherforcex_time', label: 'Stretcher Force X', channel: 'stretcherforcex', curveType: 'time_based', yLabel: 'Stretcher Force X', scope: 'rower', visible: false },
  { id: 'stretcherforcex_angle', label: 'Stretcher Force X vs Angle', channel: 'stretcherforcex_raw', curveType: 'parametric_raw', xChannel: 'angle_raw', yLabel: 'Stretcher Force X', scope: 'rower', height: 280, visible: false },
  { id: 'stretcherz_time', label: 'Stretcher Z', channel: 'stretcherz', curveType: 'time_based', yLabel: 'Stretcher Z', scope: 'rower', visible: false },
  { id: 'stretcherrl_time', label: 'Stretcher RL', channel: 'stretcherrl', curveType: 'time_based', yLabel: 'Stretcher RL', scope: 'rower', visible: false },
  { id: 'stretchertb_time', label: 'Stretcher TB', channel: 'stretchertb', curveType: 'time_based', yLabel: 'Stretcher TB', scope: 'rower', visible: false },
  { id: 'roll', label: 'Roll', channel: 'roll', curveType: 'time_based', yLabel: 'deg', scope: 'boat', height: 200, visible: false },
  { id: 'pitch', label: 'Pitch', channel: 'pitch', curveType: 'time_based', yLabel: 'deg', scope: 'boat', height: 200, visible: false },
  { id: 'yaw', label: 'Yaw', channel: 'yaw', curveType: 'time_based', yLabel: 'deg', scope: 'boat', height: 200, visible: false },
]

function formatValue(value: number | null | undefined, metric: typeof METRICS[number]): string {
  if (value == null || !Number.isFinite(value)) return '-'
  const formatted = value.toFixed(metric.decimals)
  return metric.unit ? `${formatted}${metric.unit}` : formatted
}

function formatDelta(value: number | null | undefined, ref: number | null | undefined, metric: typeof METRICS[number]): string {
  if (value == null || ref == null || !Number.isFinite(value) || !Number.isFinite(ref)) return ''
  const delta = value - ref
  if (Math.abs(delta) < 0.0001) return '0'
  const formatted = Math.abs(delta).toFixed(metric.decimals)
  return `${delta > 0 ? '+' : '-'}${formatted}${metric.unit}`
}

function pieceSplit(piece: SessionPiece): string {
  return piece.split_500m || (piece.avg_speed_ms ? `${Math.round(500 / piece.avg_speed_ms)}s` : '-')
}

function pieceDuration(piece: SessionPiece): string {
  const seconds = piece.duration_s
  if (seconds == null || !Number.isFinite(seconds)) return '-'
  const rounded = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(rounded / 60)
  const remainder = rounded % 60
  return minutes > 0 ? `${minutes}:${String(remainder).padStart(2, '0')}` : `${remainder}s`
}

function externalLabel(piece: SessionPiece): string | null {
  if (!piece._external) return null
  const parts = [piece._external.session_date, piece._external.boat_class].filter(Boolean)
  return parts.length > 0 ? `↗ ${parts.join(' · ')}` : '↗ external'
}

function snapshotFor(piece: SessionPiece, seat: number): PeriodicSnapshot | undefined {
  return piece.periodic_snapshots.find(s => s.seat_number === seat)
}

function hasCurve(rows: CurveRow[], channel: string, curveType: string, side?: string | string[]): boolean {
  return rows.some(row =>
    row.channel === channel &&
    row.curve_type === curveType &&
    (!side ||
      (Array.isArray(side) ? side.includes(row.side) : row.side === side) ||
      (side === 'port' && row.side === 'sweep'))
  )
}

export default function SessionPieceComparePanel({
  pieces,
  allPieces,
  isSculling,
  referencePieceId,
  onSetReference,
  onRemovePiece,
  preloadedCurves,
}: {
  pieces: SessionPiece[]
  allPieces: SessionPiece[]
  isSculling: boolean
  referencePieceId?: string
  onSetReference?: (id: string) => void
  onRemovePiece?: (id: string) => void
  preloadedCurves?: Record<string, CurveRow[]>
}) {
  const [activeTab, setActiveTab] = useState<'charts' | 'metrics'>('charts')
  const [metricKey, setMetricKey] = useState<MetricKey>('avg_power_w')
  const [visibleChartIds, setVisibleChartIds] = useState<Set<string>>(
    () => new Set(COMPARE_CHARTS.filter(chart => chart.visible).map(chart => chart.id)),
  )
  const [scullingView, setScullingView] = useState<'both' | 'port' | 'stbd'>('both')
  const [curvesByPiece, setCurvesByPiece] = useState<Record<string, CurveRow[]>>({})

  const comparePieces = useMemo(() => pieces.slice(0, 2), [pieces])
  const reference = comparePieces.find(p => p.id === referencePieceId) || comparePieces[0]
  const overlayPiece = comparePieces.find(piece => piece.id !== reference?.id) || comparePieces[1]
  const metric = METRICS.find(m => m.key === metricKey) || METRICS[0]
  const seats = useMemo(() => {
    const set = new Set<number>()
    comparePieces.forEach(piece => piece.rowers.forEach(rower => set.add(rower.seat_number)))
    return Array.from(set).sort((a, b) => a - b)
  }, [comparePieces])
  const seatsKey = seats.join('|')

  const [visibleSeatIds, setVisibleSeatIds] = useState<Set<number>>(() => new Set(seats))

  useEffect(() => {
    setVisibleSeatIds(prev => {
      const allowed = new Set(seats)
      const next = new Set([...prev].filter(seat => allowed.has(seat)))
      if (next.size === 0) seats.forEach(seat => next.add(seat))
      if (next.size === prev.size && [...next].every(seat => prev.has(seat))) return prev
      return next
    })
  }, [seatsKey])

  useEffect(() => {
    let cancelled = false
    const missing = comparePieces.filter(piece => (
      piece.curves_path && !preloadedCurves?.[piece.id] && !curvesByPiece[piece.id]
    ))
    if (missing.length === 0) return

    Promise.all(missing.map(async (piece: SessionPiece): Promise<readonly [string, CurveRow[]]> => {
      const res = await fetch(pieceCurvesUrl(piece.id, piece.force_offsets_updated_at, piece.created_at))
      if (!res.ok) return [piece.id, []]
      const buffer = await res.arrayBuffer()
      return [piece.id, await parseCurvesParquet(buffer)]
    })).then(entries => {
      if (cancelled) return
      setCurvesByPiece(prev => {
        const next = { ...prev }
        entries.forEach(([pieceId, rows]) => { next[pieceId] = rows })
        return next
      })
    }).catch(() => {})

    return () => { cancelled = true }
  }, [comparePieces, curvesByPiece, preloadedCurves])

  if (comparePieces.length < 2 || !reference || !overlayPiece) return null

  const referenceCurves = preloadedCurves?.[reference.id] ?? curvesByPiece[reference.id] ?? []
  const overlayCurves = preloadedCurves?.[overlayPiece.id] ?? curvesByPiece[overlayPiece.id] ?? []
  const hasStbdData = isSculling && (
    referenceCurves.some(row => row.side === 'starboard') ||
    overlayCurves.some(row => row.side === 'starboard')
  )
  const side = isSculling
    ? scullingView === 'both' ? ['port', 'starboard'] : scullingView === 'stbd' ? 'starboard' : 'port'
    : 'sweep'
  const showSideLabels = isSculling && scullingView === 'both'

  const chartEmpty = referenceCurves.length === 0 || overlayCurves.length === 0
  const rowerNames = Object.fromEntries(reference.rowers.map(rower => [rower.seat_number, rower.name_label]))
  const rowerLabels = useMemo(
    () => Object.fromEntries(
      seats.map(seat => {
        const label = pieces.flatMap(piece => piece.rowers).find(r => r.seat_number === seat)?.name_label || `Seat ${seat}`
        return [seat, shortRowerName(label, comparePieces.flatMap(piece => piece.rowers.map(rower => rower.name_label)))]
      }),
    ),
    [comparePieces, pieces, seats],
  )
  const visibleSeats = seats.filter(seat => visibleSeatIds.has(seat))
  const compareOverlay = overlayCurves.length > 0 ? [{
    curves: overlayCurves,
    label: overlayPiece.label || 'Comparison',
    colorIndex: 0,
    useSeatColor: true,
    borderDash: visibleSeats.length === 1 ? [14, 8] : [10, 6],
    borderWidth: visibleSeats.length === 1 ? 3.25 : 2.75,
    underlayColor: 'rgba(248, 250, 252, 0.28)',
    underlayWidth: visibleSeats.length === 1 ? 6 : 5,
  }] : []

  function toggleVisibleSeat(seat: number) {
    setVisibleSeatIds(prev => {
      const next = new Set(prev)
      if (next.has(seat) && next.size > 1) next.delete(seat)
      else next.add(seat)
      return next
    })
  }

  function showAllSeats() {
    setVisibleSeatIds(new Set(seats))
  }

  function toggleChart(chartId: string) {
    setVisibleChartIds(prev => {
      const next = new Set(prev)
      next.has(chartId) ? next.delete(chartId) : next.add(chartId)
      return next
    })
  }

  // Use full sculling side for availability so switching port/stbd view doesn't hide toggles.
  const availabilitySide: string | string[] = isSculling ? ['port', 'starboard'] : 'sweep'

  function chartAvailable(chart: CompareChart): boolean {
    if (chart.scope === 'boat_pace_accel') {
      return (
        hasCurve(referenceCurves, 'speed', 'time_based', 'boat') ||
        hasCurve(referenceCurves, 'accel', 'time_based', 'boat')
      )
    }
    const chartSide = chart.scope === 'boat' ? 'boat' : availabilitySide
    return hasCurve(referenceCurves, chart.channel, chart.curveType, chartSide)
  }

  function renderChart(chart: CompareChart): ReactNode {
    if (chart.scope === 'boat_pace_accel') {
      return (
        <BoatPaceAccelChart
          curves={referenceCurves}
          overlays={overlayCurves.length > 0 ? [{ curves: overlayCurves, label: overlayPiece.label || 'Comparison', borderDash: [7, 4] }] : undefined}
        />
      )
    }

    const chartSide = chart.scope === 'boat' ? 'boat' : side
    return (
      <CurveChart
        curves={referenceCurves}
        overlays={compareOverlay}
        channel={chart.channel}
        curveType={chart.curveType}
        xChannel={chart.xChannel}
        side={chartSide}
        seats={chart.scope === 'boat' ? [0] : visibleSeats}
        title={chart.label}
        yLabel={chart.yLabel}
        showStd={false}
        lineWidth={visibleSeats.length === 1 ? 3 : 2.5}
        showSideLabels={chart.scope === 'rower' ? showSideLabels : false}
        rowerNames={rowerNames}
        height={chart.height}
      />
    )
  }

  const availableCharts = COMPARE_CHARTS.filter(chart => chartAvailable(chart))
  const activeCharts = availableCharts.filter(chart => visibleChartIds.has(chart.id))

  function renderCompareChart(
    key: string,
    element: ReactNode,
    available: boolean,
  ) {
    return available ? <div key={key}>{element}</div> : null
  }

  function renderSummaryStat(label: string, referenceValue: string, overlayValue: string) {
    return (
      <div className="rounded-lg border border-border bg-[var(--surface-1)] px-3 py-2">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted/60">{label}</div>
        <div className="flex min-w-0 items-baseline gap-2 text-sm text-[var(--ink-primary)]">
          <span className="truncate font-semibold">{referenceValue}</span>
          <span className="shrink-0 text-xs text-muted">vs</span>
          <span className="truncate font-semibold">{overlayValue}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-[var(--surface-0)] p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-[var(--ink-primary)]">Compare saved sections</h3>
            <p className="text-xs text-muted">
Solid = {reference.label}. Dashed = {overlayPiece.label}. Seat colors stay tied to rowers; focus isolates one seat when the stack gets noisy.
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {comparePieces.map(piece => {
              const color = pieceColorById(allPieces, piece.id)
              const isReference = piece.id === reference.id
              return (
                <div key={piece.id} className="flex items-center gap-1 rounded-lg border border-border bg-[var(--surface-1)] px-2 py-1">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />
                  <span className="text-xs font-medium text-[var(--ink-primary)]">{piece.label}</span>
                  {externalLabel(piece) && (
                    <span className="rounded border border-border bg-[var(--surface-0)] px-1.5 py-0.5 text-[9px] font-medium text-muted">
                      {externalLabel(piece)}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => onSetReference?.(piece.id)}
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold transition ${
                      isReference ? 'bg-teal/20 text-teal' : 'text-muted hover:text-teal'
                    }`}
                  >
                    Ref
                  </button>
                  {onRemovePiece && (
                    <button type="button" onClick={() => onRemovePiece(piece.id)} className="rounded px-1 text-muted/50 hover:text-red-500">
                      ×
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        <div className="mt-3 grid gap-2 md:grid-cols-4">
          {renderSummaryStat('Strokes', `${reference.stroke_count ?? '-'}`, `${overlayPiece.stroke_count ?? '-'}`)}
          {renderSummaryStat('Duration', pieceDuration(reference), pieceDuration(overlayPiece))}
          {renderSummaryStat('Rate', `${reference.avg_stroke_rate?.toFixed(1) ?? '-'} spm`, `${overlayPiece.avg_stroke_rate?.toFixed(1) ?? '-'} spm`)}
          {renderSummaryStat('Pace', pieceSplit(reference), pieceSplit(overlayPiece))}
        </div>

        <div className="mt-3 flex border-t border-border pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex overflow-hidden rounded-lg border border-border bg-[var(--surface-0)]">
              {(['charts', 'metrics'] as const).map(tab => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-1.5 text-sm font-semibold transition ${
                    activeTab === tab
                      ? 'bg-[var(--surface-1)] text-teal'
                      : 'text-muted hover:text-[var(--ink-primary)]'
                  }`}
                >
                  {tab === 'charts' ? 'Charts' : 'Metrics'}
                </button>
              ))}
            </div>
            {hasStbdData && activeTab === 'charts' && (
              <div className="flex overflow-hidden rounded-lg border border-border bg-[var(--surface-0)]">
                {(['both', 'port', 'stbd'] as const).map(view => (
                  <button
                    key={view}
                    type="button"
                    onClick={() => setScullingView(view)}
                    className={`px-3 py-1.5 text-xs font-semibold transition ${
                      scullingView === view
                        ? 'bg-teal/20 text-teal'
                        : 'text-muted hover:text-[var(--ink-primary)]'
                    }`}
                  >
                    {view === 'both' ? 'P/S' : view === 'port' ? 'P' : 'S'}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {activeTab === 'charts' && seats.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted/70">Rowers</span>
            {visibleSeats.length < seats.length && (
              <button
                type="button"
                onClick={showAllSeats}
                className="rounded-full border border-border px-2.5 py-1 text-[11px] font-medium text-muted transition hover:text-teal"
              >
                Show all
              </button>
            )}
            {seats.map(seat => {
              const on = visibleSeatIds.has(seat)
              return (
                <button
                  key={seat}
                  type="button"
                  onClick={() => toggleVisibleSeat(seat)}
                  className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                    on
                      ? 'border-teal/30 bg-teal/15 text-teal'
                      : 'border-border text-muted/45 hover:text-[var(--ink-primary)]'
                  }`}
                  title={on && visibleSeats.length === 1 ? 'At least one rower stays visible' : 'Hide/show this curve'}
                >
                  {seat}. {rowerLabels[seat] || `Seat ${seat}`}
                </button>
              )
            })}
          </div>
        )}

        {activeTab === 'charts' && availableCharts.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted/70">Graphs</span>
            {availableCharts.map(chart => {
              const on = visibleChartIds.has(chart.id)
              return (
                <button
                  key={chart.id}
                  type="button"
                  onClick={() => toggleChart(chart.id)}
                  className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                    on
                      ? 'border-teal/30 bg-teal/15 text-teal'
                      : 'border-border text-muted/55 hover:text-[var(--ink-primary)]'
                  }`}
                >
                  {chart.label}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {activeTab === 'metrics' && (
        <div className="rounded-xl border border-border bg-[var(--surface-0)] p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-[var(--ink-primary)]">Rower deltas</h4>
            <select
              value={metricKey}
              onChange={event => setMetricKey(event.target.value as MetricKey)}
              className="rounded-lg border border-border bg-[var(--surface-1)] px-2 py-1 text-xs text-[var(--ink-primary)] focus:border-teal focus:outline-none"
            >
              {METRICS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-xs">
              <thead>
                <tr className="border-b border-border text-[10px] uppercase tracking-wide text-muted/60">
                  <th className="py-2 pr-3">Rower</th>
                  {comparePieces.map(piece => (
                    <th key={piece.id} className="px-3 py-2">
                      <span className="inline-flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: pieceColorById(allPieces, piece.id) }} />
                        {piece.label}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {seats.map(seat => {
                  const refSnap = snapshotFor(reference, seat)
                  const refValue = refSnap?.[metric.key] as number | null | undefined
                  const rowerName = pieces.flatMap(piece => piece.rowers).find(r => r.seat_number === seat)?.name_label || `Seat ${seat}`
                  return (
                    <tr key={seat} className="border-b border-border/70">
                      <td className="py-2 pr-3 font-medium text-[var(--ink-primary)]">{seat}. {rowerName}</td>
                      {comparePieces.map(piece => {
                        const snap = snapshotFor(piece, seat)
                        const value = snap?.[metric.key] as number | null | undefined
                        const delta = piece.id === reference.id ? '' : formatDelta(value, refValue, metric)
                        return (
                          <td key={piece.id} className="px-3 py-2 tabular-nums">
                            <span className="font-semibold text-[var(--ink-primary)]">{formatValue(value, metric)}</span>
                            {delta && <span className="ml-2 text-muted">{delta}</span>}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === 'charts' && (
        <div>
          {chartEmpty ? (
            <div className="rounded-xl border border-dashed border-border bg-[var(--surface-0)] px-4 py-8 text-center text-sm text-muted">
              Graph comparison is available once curve data exists for both selected pieces.
            </div>
          ) : (
            <CurveHoverProvider>
              <div className="space-y-4">
                {activeCharts.length > 0 ? (
                  activeCharts.map(chart => renderCompareChart(chart.id, renderChart(chart), true))
                ) : (
                  <div className="rounded-xl border border-dashed border-border bg-[var(--surface-0)] px-4 py-8 text-center text-sm text-muted">
                    Select at least one graph to compare.
                  </div>
                )}

            </div>
          </CurveHoverProvider>
        )}
      </div>
      )}
    </div>
  )
}
