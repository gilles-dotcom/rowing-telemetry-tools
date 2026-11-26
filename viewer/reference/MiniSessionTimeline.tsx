'use client'

import { useMemo, useState } from 'react'
import type { PeriodicSnapshot, SessionPiece } from '@/lib/types'

type MiniTimelineMetric = 'power' | 'force' | 'rate'
const MARKER_COLORS = ['var(--amber)', '#b45309', '#7e22ce', '#15803d', '#be185d', '#4b5563']

export function miniTimelineMarkerColor(pieces: { id: string }[], pieceId: string): string {
  const index = pieces.findIndex(piece => piece.id === pieceId)
  return MARKER_COLORS[Math.max(0, index) % MARKER_COLORS.length]
}

interface MiniSessionTimelineProps {
  session: {
    duration_s?: number | null
    power_profile?: { time_s?: number[] | null; power?: number[] | null } | null
  }
  pieces: SessionPiece[]
  excludeIds: string[]
  onPick: (pieceId: string) => void
}

function formatTime(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds))
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '-'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}:${s.toString().padStart(2, '0')}` : `${s}s`
}

function averageSnapshotMetric(snapshots: PeriodicSnapshot[], key: keyof PeriodicSnapshot): number | null {
  const values = snapshots
    .map(snapshot => snapshot[key])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (values.length === 0) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function averagePower(piece: SessionPiece): number | null {
  return averageSnapshotMetric(piece.periodic_snapshots, 'avg_power_w')
}

function averageForce(piece: SessionPiece): number | null {
  return averageSnapshotMetric(piece.periodic_snapshots, 'avg_peak_force_n')
}

function pieceMetric(piece: SessionPiece, metric: MiniTimelineMetric): number | null {
  if (metric === 'rate') return piece.avg_stroke_rate ?? null
  if (metric === 'force') return averageForce(piece)
  return averagePower(piece)
}

export default function MiniSessionTimeline({
  session,
  pieces,
  excludeIds,
  onPick,
}: MiniSessionTimelineProps) {
  const [metric, setMetric] = useState<MiniTimelineMetric>('power')
  const excludeSet = useMemo(() => new Set(excludeIds), [excludeIds])

  const durationS = useMemo(() => {
    const pieceEnd = pieces.reduce((max, piece) => Math.max(max, piece.end_ms / 1000), 0)
    const profileTime = session.power_profile?.time_s
    const profileEnd = profileTime && profileTime.length > 0 ? profileTime[profileTime.length - 1] ?? 0 : 0
    return Math.max(session.duration_s ?? 0, pieceEnd, profileEnd, 1)
  }, [pieces, session.duration_s, session.power_profile])

  const linePoints = useMemo(() => {
    const source =
      metric === 'power' && session.power_profile?.time_s?.length && session.power_profile?.power?.length
        ? session.power_profile.time_s.map((time, index) => ({
            x: time,
            y: session.power_profile?.power?.[index] ?? null,
          }))
        : pieces.map(piece => ({
            x: (piece.start_ms + piece.end_ms) / 2000,
            y: pieceMetric(piece, metric),
          }))

    const valid = source.filter((point): point is { x: number; y: number } => (
      typeof point.y === 'number' && Number.isFinite(point.x) && Number.isFinite(point.y)
    ))
    if (valid.length === 0) return ''

    let minY = valid[0].y
    let maxY = valid[0].y
    for (const point of valid) {
      if (point.y < minY) minY = point.y
      if (point.y > maxY) maxY = point.y
    }
    const rangeY = Math.max(maxY - minY, 1)

    return valid
      .map(point => {
        const x = Math.max(0, Math.min(100, (point.x / durationS) * 100))
        const y = 88 - ((point.y - minY) / rangeY) * 62
        return `${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(' ')
  }, [durationS, metric, pieces, session.power_profile])

  return (
    <div className="rounded-xl border border-border bg-[var(--surface-0)] p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold text-[var(--ink-primary)]">Session overview</h3>
          <p className="text-[10px] text-muted">{pieces.length} saved pieces · {formatTime(durationS)}</p>
        </div>
        <div className="inline-flex shrink-0 overflow-hidden rounded-lg border border-border bg-[var(--surface-1)] p-0.5">
          {([
            ['power', 'Power'],
            ['force', 'Force'],
            ['rate', 'Rate'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMetric(value)}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition ${
                metric === value
                  ? 'bg-[var(--amber-dim)] text-[var(--amber)]'
                  : 'text-muted hover:text-[var(--ink-primary)]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="relative h-40 overflow-hidden rounded-lg border border-border bg-[var(--surface-1)]">
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <pattern id="miniTimelineGrid" width="12.5" height="25" patternUnits="userSpaceOnUse">
              <path d="M 12.5 0 L 0 0 0 25" fill="none" stroke="var(--edge)" strokeWidth="0.45" />
            </pattern>
          </defs>
          <rect width="100" height="100" fill="url(#miniTimelineGrid)" />
          {linePoints && (
            <polyline
              points={linePoints}
              fill="none"
              stroke="var(--amber)"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>

        <div className="absolute inset-x-0 bottom-0 h-12 border-t border-dashed border-border bg-[var(--surface-0)]/80">
          {pieces.map(piece => {
            const already = excludeSet.has(piece.id)
            const color = miniTimelineMarkerColor(pieces, piece.id)
            const left = Math.max(0, Math.min(100, (piece.start_ms / 1000 / durationS) * 100))
            const right = Math.max(left + 1, Math.min(100, (piece.end_ms / 1000 / durationS) * 100))
            const width = Math.max(right - left, 1.5)
            return (
              <button
                key={piece.id}
                type="button"
                disabled={already}
                onClick={() => onPick(piece.id)}
                title={already ? 'Already in comparison' : `Add ${piece.label}`}
                className={`absolute bottom-1 top-1 overflow-hidden rounded-md border px-2 text-left transition ${
                  already
                    ? 'cursor-not-allowed opacity-45'
                    : 'hover:-translate-y-0.5 hover:bg-[var(--surface-0)]'
                }`}
                style={{
                  left: `${left}%`,
                  width: `${width}%`,
                  minWidth: '34px',
                  borderColor: already ? 'var(--edge)' : color,
                  backgroundColor: already ? 'var(--surface-2)' : `color-mix(in srgb, ${color} 12%, transparent)`,
                }}
              >
                <span className="block truncate text-[10px] font-semibold text-[var(--ink-primary)]">
                  {piece.label || 'Piece'}
                </span>
                <span className="block truncate text-[9px] text-muted">
                  {already ? 'In compare' : formatDuration(piece.duration_s)}
                </span>
              </button>
            )
          })}
        </div>

        <div className="absolute bottom-12 left-2 right-2 flex justify-between text-[9px] text-muted">
          <span>0:00</span>
          <span>{formatTime(durationS)}</span>
        </div>
      </div>
    </div>
  )
}
