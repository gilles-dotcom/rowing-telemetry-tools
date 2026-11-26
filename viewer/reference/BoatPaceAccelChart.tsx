'use client'

import { useCallback, useMemo, useRef } from 'react'
import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import zoomPlugin from 'chartjs-plugin-zoom'
import type { CurveRow } from '@/lib/types'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, zoomPlugin)

interface BoatPaceAccelChartProps {
  curves: CurveRow[]
  title?: string
  height?: number
  overlays?: { curves: CurveRow[]; label: string; borderDash?: number[] }[]
}

function findBoatRow(curves: CurveRow[], channel: 'speed' | 'accel'): CurveRow | undefined {
  return curves.find(
    row =>
      row.seat === 0 &&
      row.side === 'boat' &&
      row.channel === channel &&
      row.curve_type === 'time_based' &&
      row.mean.length > 0,
  )
}

function speedToPaceSeconds(speed: number): number | null {
  if (!Number.isFinite(speed) || speed <= 0.2) return null
  return 500 / speed
}

function formatPace(seconds: number): string {
  if (!Number.isFinite(seconds)) return '-'
  const rounded = Math.round(seconds)
  const minutes = Math.floor(rounded / 60)
  const remainder = rounded % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

function normalizeAcceleration(values: number[]): Array<number | null> {
  const valid = values.filter(Number.isFinite)
  if (valid.length === 0) return values.map(() => null)

  const max = Math.max(...valid)
  const min = Math.min(...valid)
  const sign = Math.abs(max) > Math.abs(min) ? -1 : 1
  return values.map(value => (Number.isFinite(value) ? value * sign : null))
}

export default function BoatPaceAccelChart({
  curves,
  title = 'Pace + Acceleration',
  height = 280,
  overlays,
}: BoatPaceAccelChartProps) {
  const chartRef = useRef<any>(null)
  const zoomBy = useCallback((factor: number) => {
    chartRef.current?.zoom?.({ x: factor, y: factor }, 'none')
  }, [])
  const resetZoom = useCallback(() => {
    chartRef.current?.resetZoom?.('none')
  }, [])

  const chartData = useMemo(() => {
    const datasets: any[] = []

    function addPiece(pieceCurves: CurveRow[], labelPrefix: string, dashed = false, borderDash?: number[]) {
      const speed = findBoatRow(pieceCurves, 'speed')
      const accel = findBoatRow(pieceCurves, 'accel')
      const dash = dashed ? borderDash || [7, 4] : undefined

      if (speed) {
        datasets.push({
          label: `${labelPrefix} pace`,
          data: speed.mean.map(speedToPaceSeconds),
          borderColor: '#00b4d8',
          backgroundColor: '#00b4d8',
          borderWidth: 2,
          borderDash: dash,
          pointRadius: 0,
          tension: 0.3,
          cubicInterpolationMode: 'monotone',
          spanGaps: false,
          yAxisID: 'pace',
        })
      }

      if (accel) {
        datasets.push({
          label: `${labelPrefix} accel`,
        data: normalizeAcceleration(accel.mean),
          borderColor: '#ff922b',
          backgroundColor: '#ff922b',
          borderWidth: 2,
          borderDash: dash,
          pointRadius: 0,
          tension: 0.3,
          cubicInterpolationMode: 'monotone',
          spanGaps: false,
          yAxisID: 'accel',
        })
      }
    }

    addPiece(curves, 'Current')
    overlays?.forEach(overlay => addPiece(overlay.curves, overlay.label, true, overlay.borderDash))

    return {
      labels: Array.from({ length: 200 }, (_, i) => Math.round(i * 0.5)),
      datasets,
    }
  }, [curves, overlays])

  if (chartData.datasets.length === 0) return null

  return (
    <div className="rounded-xl border border-border bg-[var(--surface-0)] p-4">
      <div className="mb-3 flex items-center justify-between">
<h4 className="text-sm font-medium text-muted">{title}</h4>
<div className="flex flex-wrap items-center justify-end gap-2 text-[11px] font-semibold">
<div className="flex overflow-hidden rounded border border-border bg-[var(--surface-0)]" title="Wheel zoom, drag after zoom to pan">
<button type="button" onClick={() => zoomBy(1.5)} className="px-1.5 py-0.5 text-[10px] font-semibold text-muted/70 transition hover:bg-[var(--surface-3)] hover:text-teal" title="Zoom in">+</button>
<button type="button" onClick={() => zoomBy(1 / 1.5)} className="border-l border-border px-1.5 py-0.5 text-[10px] font-semibold text-muted/70 transition hover:bg-[var(--surface-3)] hover:text-teal" title="Zoom out">-</button>
<button type="button" onClick={resetZoom} className="border-l border-border px-1.5 py-0.5 text-[10px] font-semibold text-muted/70 transition hover:bg-[var(--surface-3)] hover:text-teal" title="Reset zoom">1:1</button>
</div>
<span className="inline-flex items-center gap-1.5 text-teal">
<span className="h-0 w-7 rounded-full border-t-[3px] border-teal" />
pace/500m
          </span>
          <span className="inline-flex items-center gap-1.5 text-[#ff922b]">
            <span className="h-0 w-7 rounded-full border-t-[3px] border-[#ff922b]" />
            acceleration
          </span>
        </div>
      </div>
      <div style={{ height }}>
<Line
ref={chartRef}
data={chartData}
options={{
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
              legend: { display: false },
tooltip: {
                mode: 'index',
                intersect: false,
                backgroundColor: '#172438',
                borderColor: '#253d56',
                borderWidth: 1,
                titleColor: '#e2e8f0',
                bodyColor: '#e2e8f0',
                callbacks: {
                  label: ctx => {
                    const label = ctx.dataset.label || ''
                    const value = Number(ctx.parsed.y)
                    if (ctx.dataset.yAxisID === 'pace') return `${label}: ${formatPace(value)}/500m`
                    return `${label}: ${value.toFixed(2)} m/s²`
                  },
                },
},
zoom: {
limits: {
x: { min: 'original', max: 'original' },
pace: { min: 'original', max: 'original' },
accel: { min: 'original', max: 'original' },
},
pan: {
enabled: true,
mode: 'xy',
threshold: 8,
onPanStart: ({ chart }: any) => chart.isZoomedOrPanned?.() === true,
},
zoom: {
wheel: { enabled: true },
pinch: { enabled: true },
drag: { enabled: true, modifierKey: 'shift' },
mode: 'xy',
},
},
},
scales: {
              x: {
                title: { display: true, text: 'Stroke %', color: '#7a8ba0', font: { size: 11 } },
                ticks: { color: '#7a8ba0', font: { size: 10 }, maxTicksLimit: 10 },
                grid: { color: '#253d5640' },
              },
              pace: {
                type: 'linear',
                position: 'left',
                reverse: true,
                title: { display: true, text: 'Pace /500m', color: '#7a8ba0', font: { size: 11 } },
                ticks: {
                  color: '#7a8ba0',
                  font: { size: 10 },
                  callback: value => formatPace(Number(value)),
                },
                grid: { color: '#253d5640' },
              },
              accel: {
                type: 'linear',
                position: 'right',
                title: { display: true, text: 'Acceleration (m/s²)', color: '#7a8ba0', font: { size: 11 } },
                ticks: { color: '#7a8ba0', font: { size: 10 } },
                grid: { drawOnChartArea: false },
              },
            },
          }}
        />
      </div>
    </div>
  )
}
