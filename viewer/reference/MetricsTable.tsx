'use client'

import { useMemo, useState } from 'react'
import type { PeriodicSnapshot, PieceRower } from '@/lib/types'
import { SEAT_COLORS } from '@/lib/types'

type MetricDef = {
  key: keyof PeriodicSnapshot
  sd?: keyof PeriodicSnapshot
  label: string
  unit: string
  decimals: number
}

type MetricGroup = {
  label: string
  metrics: MetricDef[]
}

const POWER: MetricDef = { key: 'avg_power_w', sd: 'sd_power_w', label: 'Power', unit: 'W', decimals: 0 }
const PEAK_FORCE: MetricDef = { key: 'avg_peak_force_n', sd: 'sd_peak_force_n', label: 'Peak F', unit: 'N', decimals: 0 }
const WORK: MetricDef = { key: 'avg_work_j', sd: 'sd_work_j', label: 'Work', unit: 'J', decimals: 0 }
const CATCH: MetricDef = { key: 'avg_catch_angle_deg', sd: 'sd_catch_angle_deg', label: 'Catch', unit: 'deg', decimals: 1 }
const FINISH: MetricDef = { key: 'avg_finish_angle_deg', sd: 'sd_finish_angle_deg', label: 'Finish', unit: 'deg', decimals: 1 }
const ARC: MetricDef = { key: 'avg_arc_deg', sd: 'sd_arc_deg', label: 'Arc', unit: 'deg', decimals: 1 }
const EFFECTIVE_ARC: MetricDef = { key: 'avg_effective_arc_deg', sd: 'sd_effective_arc_deg', label: 'Eff Arc', unit: 'deg', decimals: 1 }
const SLIP: MetricDef = { key: 'avg_slip_deg', sd: 'sd_slip_deg', label: 'Slip', unit: 'deg', decimals: 1 }
const WASH: MetricDef = { key: 'avg_wash_deg', sd: 'sd_wash_deg', label: 'Wash', unit: 'deg', decimals: 1 }
const MAX_FORCE_ANGLE: MetricDef = { key: 'avg_angle_max_force_deg', sd: 'sd_angle_max_force_deg', label: 'Max F @', unit: 'deg', decimals: 1 }
const FORCE_70_ANGLE: MetricDef = { key: 'avg_angle_70pct_force_deg', sd: 'sd_angle_70pct_force_deg', label: '70% F @', unit: 'deg', decimals: 1 }
const DRIVE: MetricDef = { key: 'avg_drive_time_ms', sd: 'sd_drive_time_ms', label: 'Drive', unit: 'ms', decimals: 0 }
const RECOVERY: MetricDef = { key: 'avg_recovery_time_ms', sd: 'sd_recovery_time_ms', label: 'Recovery', unit: 'ms', decimals: 0 }
const RATIO: MetricDef = { key: 'avg_ratio', sd: 'sd_ratio', label: 'Dr:Rec', unit: '', decimals: 2 }
const Q1: MetricDef = { key: 'avg_work_q1_pct', label: 'Q1', unit: '%', decimals: 0 }
const Q2: MetricDef = { key: 'avg_work_q2_pct', label: 'Q2', unit: '%', decimals: 0 }
const Q3: MetricDef = { key: 'avg_work_q3_pct', label: 'Q3', unit: '%', decimals: 0 }
const Q4: MetricDef = { key: 'avg_work_q4_pct', label: 'Q4', unit: '%', decimals: 0 }

const METRIC_GROUPS: MetricGroup[] = [
  {
    label: 'Overview',
    metrics: [POWER, PEAK_FORCE, WORK, CATCH, FINISH, ARC, SLIP, WASH],
  },
  {
    label: 'Power',
    metrics: [POWER, PEAK_FORCE, WORK],
  },
  {
    label: 'Angles',
    metrics: [CATCH, FINISH, ARC, EFFECTIVE_ARC, SLIP, WASH, MAX_FORCE_ANGLE, FORCE_70_ANGLE],
  },
  {
    label: 'Timing',
    metrics: [DRIVE, RECOVERY, RATIO],
  },
  {
    label: 'Work',
    metrics: [Q1, Q2, Q3, Q4],
  },
]

interface MetricsTableProps {
  snapshots: PeriodicSnapshot[]
  rowers: PieceRower[]
  showSd?: boolean
}

function getMetricValue(snapshot: PeriodicSnapshot, key: keyof PeriodicSnapshot) {
  const value = snapshot[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatMetric(value: number, metric: MetricDef) {
  return value.toFixed(metric.decimals)
}

function seatLabel(seatNumber: number, crewSize: number) {
  if (crewSize > 2 && seatNumber === 1) return 'B'
  if (crewSize > 2 && seatNumber === crewSize) return 'S'
  return String(seatNumber)
}

function firstName(name: string | undefined) {
  return name?.trim().split(/\s+/)[0] ?? ''
}

export default function MetricsTable({ snapshots, rowers, showSd = false }: MetricsTableProps) {
  const sorted = useMemo(
    () => [...snapshots].sort((a, b) => a.seat_number - b.seat_number),
    [snapshots]
  )
  const rowerMap = useMemo(
    () => Object.fromEntries(rowers.map(r => [r.seat_number, r.name_label])),
    [rowers]
  )

  const availableGroups = useMemo(() => (
    METRIC_GROUPS
      .map(group => ({
        ...group,
        metrics: group.metrics.filter(metric =>
          sorted.some(snapshot => getMetricValue(snapshot, metric.key) != null)
        ),
      }))
      .filter(group => group.metrics.length > 0)
  ), [sorted])

  const [activeLabel, setActiveLabel] = useState('Overview')
  const activeGroup = availableGroups.find(group => group.label === activeLabel) ?? availableGroups[0]

  if (!activeGroup) return null

  const crewSize = sorted.length
  const gridClass = crewSize <= 1 ? 'grid-cols-1' : crewSize <= 2 ? 'grid-cols-2' : 'grid-cols-4'
  const panelWidth = crewSize <= 1 ? 172 : crewSize <= 2 ? 224 : 270

  return (
    <div className="text-xs" style={{ width: panelWidth }}>
      <div className="sticky top-0 z-[1] flex gap-1 overflow-x-auto border-b border-[var(--edge)] bg-[var(--surface-0)] px-2 py-1.5">
        {availableGroups.map(group => (
          <button
            key={group.label}
            type="button"
            onClick={() => setActiveLabel(group.label)}
            className={`shrink-0 rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide transition ${
              group.label === activeGroup.label
                ? 'bg-teal/10 text-teal'
                : 'text-muted/55 hover:bg-[var(--surface-1)] hover:text-muted'
            }`}
          >
            {group.label}
          </button>
        ))}
      </div>

      <div className="divide-y divide-[var(--edge)]">
        {activeGroup.metrics.map(metric => {
          const values = sorted
            .map(snapshot => getMetricValue(snapshot, metric.key))
            .filter((value): value is number => value != null)
          const average = values.length > 0
            ? values.reduce((sum, value) => sum + value, 0) / values.length
            : null

          return (
            <section key={String(metric.key)} className="px-2 py-1.5">
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-[10px] font-bold uppercase tracking-wide text-[var(--ink-secondary)]">
                    {metric.label}
                  </span>
                  {metric.unit && (
                    <span className="ml-1 text-[9px] font-medium text-muted/50">{metric.unit}</span>
                  )}
                </div>
                {average != null && (
                  <span className="shrink-0 text-[10px] tabular-nums text-muted/60">
                    avg {formatMetric(average, metric)}
                  </span>
                )}
              </div>

              <div className={`grid ${gridClass} gap-1`}>
                {sorted.map(snapshot => {
                  const value = getMetricValue(snapshot, metric.key)
                  const sd = metric.sd ? getMetricValue(snapshot, metric.sd) : null
                  const name = rowerMap[snapshot.seat_number]
                  const color = SEAT_COLORS[snapshot.seat_number] || '#888'
                  const delta = value != null && average != null && values.length > 1
                    ? value - average
                    : null
                  const title = [
                    name ? `Seat ${snapshot.seat_number}: ${name}` : `Seat ${snapshot.seat_number}`,
                    value != null ? `${metric.label}: ${formatMetric(value, metric)} ${metric.unit}`.trim() : 'No value',
                    sd != null ? `SD: ${formatMetric(sd, metric)} ${metric.unit}`.trim() : null,
                    delta != null ? `Delta vs avg: ${delta >= 0 ? '+' : ''}${formatMetric(delta, metric)} ${metric.unit}`.trim() : null,
                  ].filter(Boolean).join('\n')

                  return (
                    <div
                      key={snapshot.seat_number}
                      title={title}
                      className="min-w-0 rounded-md border border-[var(--edge)] bg-[var(--surface-1)] px-1.5 py-1"
                      style={{ borderTopColor: color }}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="shrink-0 text-[9px] font-bold tabular-nums" style={{ color }}>
                          {seatLabel(snapshot.seat_number, crewSize)}
                        </span>
                        <span className="min-w-0 truncate text-right text-[11px] font-semibold tabular-nums text-[var(--ink-primary)]">
                          {value == null ? '-' : formatMetric(value, metric)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex min-h-[10px] items-center justify-between gap-1 text-[8.5px] leading-none text-muted/45">
                        <span className="min-w-0 truncate">{firstName(name)}</span>
                        {showSd && sd != null ? (
                          <span className="shrink-0 tabular-nums">+/-{formatMetric(sd, metric)}</span>
                        ) : delta != null && Math.abs(delta) >= Math.pow(10, -metric.decimals) ? (
                          <span className="shrink-0 tabular-nums">
                            {delta > 0 ? '+' : ''}{formatMetric(delta, metric)}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          )
        })}
      </div>
    </div>
  )
}
