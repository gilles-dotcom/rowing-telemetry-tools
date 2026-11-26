'use client'

import { useMemo, useEffect, useState } from 'react'
import { SEAT_COLORS, getClubColor } from '@/lib/types'
import type { SessionPiece } from '@/lib/types'
import { viewerTooltipPosition, registerTooltipPositioner } from '@/lib/chartTooltip'

interface SessionTimelineProps {
  pieces: SessionPiece[]
  selectedPieceId: string | null
  onSelectPiece: (id: string) => void
  metric: 'power' | 'force' | 'stroke_rate'
}

export default function SessionTimeline({
  pieces, selectedPieceId, onSelectPiece, metric,
}: SessionTimelineProps) {
  const [chart, setChart] = useState<{ Line: any } | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const { Chart: ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend } = await import('chart.js')
      const annotationPlugin = (await import('chartjs-plugin-annotation')).default
      const { Line } = await import('react-chartjs-2')
      ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend, annotationPlugin)
      registerTooltipPositioner()
      if (!cancelled) setChart({ Line })
    })()
    return () => { cancelled = true }
  }, [])

  // Build timeline: one data point per piece, showing seat-level averages
  const { chartData, options } = useMemo(() => {
    // Collect all seat numbers across pieces
    const allSeats = new Set<number>()
    for (const p of pieces) {
      for (const snap of p.periodic_snapshots) {
        allSeats.add(snap.seat_number)
      }
    }
    const seats = Array.from(allSeats).sort()

    const labels = pieces.map(p => p.label)

    const datasets: any[] = []

    if (metric === 'stroke_rate') {
      // Single line: avg stroke rate per piece
      datasets.push({
        label: 'Stroke Rate (spm)',
        data: pieces.map(p => p.avg_stroke_rate),
        borderColor: getClubColor(),
        backgroundColor: getClubColor() + '20',
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 8,
        pointBackgroundColor: pieces.map(p =>
          p.id === selectedPieceId ? getClubColor() : '#172438'
        ),
        pointBorderColor: getClubColor(),
        pointBorderWidth: 2,
        tension: 0.3,
        fill: true,
      })
    } else {
      // Per-seat lines
      for (const seat of seats) {
        const color = SEAT_COLORS[seat] || '#888'
        const rowerName = pieces[0]?.rowers.find(r => r.seat_number === seat)?.name_label || `Seat ${seat}`

        const values = pieces.map(p => {
          const snap = p.periodic_snapshots.find(s => s.seat_number === seat)
          if (!snap) return null
          if (metric === 'power') return snap.avg_power_w
          if (metric === 'force') return snap.avg_peak_force_n
          return null
        })

        datasets.push({
          label: rowerName,
          data: values,
          borderColor: color,
          borderWidth: 2,
          pointRadius: 5,
          pointHoverRadius: 7,
          pointBackgroundColor: pieces.map(p =>
            p.id === selectedPieceId ? color : '#172438'
          ),
          pointBorderColor: color,
          pointBorderWidth: 2,
          tension: 0.3,
        })
      }
    }

    const metricLabels: Record<string, string> = {
      power: 'Power (W)',
      force: 'Peak Force (N)',
      stroke_rate: 'Stroke Rate (spm)',
    }

    const opts: any = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      onClick: (_event: any, elements: any[]) => {
        if (elements.length > 0) {
          const idx = elements[0].index
          if (pieces[idx]) onSelectPiece(pieces[idx].id)
        }
      },
      plugins: {
        legend: {
          display: true,
          position: 'top' as const,
          labels: {
            color: '#7a8ba0',
            font: { size: 11 },
            boxWidth: 12,
            padding: 8,
          },
        },
        tooltip: {
          ...viewerTooltipPosition(),
          mode: 'index' as const,
          intersect: false,
          backgroundColor: '#172438',
          borderColor: '#253d56',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#7a8ba0',
          callbacks: {
            afterTitle: (items: any[]) => {
              const idx = items[0]?.dataIndex
              if (idx == null || !pieces[idx]) return ''
              const p = pieces[idx]
              return `${p.stroke_count} strokes · ${p.duration_s?.toFixed(0)}s · ${p.avg_stroke_rate?.toFixed(0)} spm`
            },
          },
        },
        annotation: {
          annotations: selectedPieceId ? {
            selectedBox: {
              type: 'box' as const,
              xMin: pieces.findIndex(p => p.id === selectedPieceId) - 0.4,
              xMax: pieces.findIndex(p => p.id === selectedPieceId) + 0.4,
              backgroundColor: getClubColor() + '10',
              borderColor: getClubColor() + '40',
              borderWidth: 1,
            },
          } : {},
        },
      },
      scales: {
        x: {
          ticks: { color: '#7a8ba0', font: { size: 11 } },
          grid: { color: '#253d5640' },
        },
        y: {
          title: {
            display: true,
            text: metricLabels[metric] || '',
            color: '#7a8ba0',
            font: { size: 11 },
          },
          ticks: { color: '#7a8ba0', font: { size: 10 } },
          grid: { color: '#253d5640' },
        },
      },
    }

    return { chartData: { labels, datasets }, options: opts }
  }, [pieces, selectedPieceId, metric, onSelectPiece])

  if (!chart) {
    return <div className="rounded-xl border border-border bg-[var(--surface-0)] p-4" style={{ height: 300 }} />
  }

  const { Line } = chart

  return (
    <div className="rounded-xl border border-border bg-[var(--surface-0)] p-4">
      <div style={{ height: 300 }}>
        <Line data={chartData} options={options} />
      </div>
    </div>
  )
}
