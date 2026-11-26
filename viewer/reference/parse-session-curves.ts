import { parquetMetadata, parquetRead } from 'hyparquet'
import type { CurveRow } from './types'

export interface StrokeCurveRow {
  stroke_index: number
  seat: number
  side: string
  channel: string
  curve_type: string
  values: number[]
}

/**
 * Parse session curves.parquet into StrokeCurveRow[].
 *
 * Values are stored as int16 with a per-row `scale` factor.
 * Real value = int16_value * scale
 */
export async function parseSessionCurvesParquet(
  buffer: ArrayBuffer,
): Promise<StrokeCurveRow[]> {
  const metadata = parquetMetadata(buffer)
  // Filter out nested list schema entries (e.g. 'list', 'element' from list<int16>)
  // Only keep top-level columns that have a defined type or num_children
  const columnNames = metadata.schema
    .slice(1)
    .filter((s: any) => s.type !== undefined || s.num_children !== undefined)
    .filter((s: any) => s.name !== 'list' && s.name !== 'element')
    .map((s: any) => s.name)
  const hasScale = columnNames.includes('scale')

  const rows: StrokeCurveRow[] = []
  await parquetRead({
    file: buffer,
    metadata,
    onComplete: (data: any[][]) => {
      for (const row of data) {
        const obj: any = {}
        columnNames.forEach((name: string, i: number) => {
          obj[name] = row[i]
        })

        const scale = hasScale ? Number(obj.scale) : 1
        const rawValues = Array.isArray(obj.values) ? obj.values : []
        const values = scale !== 1
          ? rawValues.map((v: number) => v * scale)
          : rawValues.map(Number)

        rows.push({
          stroke_index: Number(obj.stroke_index),
          seat: Number(obj.seat),
          side: String(obj.side),
          channel: String(obj.channel),
          curve_type: String(obj.curve_type),
          values,
        })
      }
    },
  })

  return rows
}

/**
 * Compute piece curves from session curves in a time range.
 *
 * Filters curves to the stroke index range, groups by (seat, side, channel, curve_type),
 * and computes element-wise mean and std across strokes.
 *
 * Returns CurveRow[] format compatible with existing CurveChart components.
 */
export function computePieceCurves(
  allCurves: StrokeCurveRow[],
  startStrokeIdx: number,
  endStrokeIdx: number,
): CurveRow[] {
  // Filter to stroke range
  const filtered = allCurves.filter(
    (c) => c.stroke_index >= startStrokeIdx && c.stroke_index <= endStrokeIdx,
  )

  if (filtered.length === 0) {
    return []
  }

  // Group by (seat, side, channel, curve_type)
  const groups = new Map<
    string,
    { curves: number[][]; row: StrokeCurveRow }
  >()

  for (const row of filtered) {
    const key = `${row.seat}|${row.side}|${row.channel}|${row.curve_type}`
    if (!groups.has(key)) {
      groups.set(key, { curves: [], row })
    }
    groups.get(key)!.curves.push(row.values)
  }

  // Compute mean/std for each group
  const result: CurveRow[] = []

  for (const { curves, row } of groups.values()) {
    if (curves.length === 0) continue

    const n = curves[0].length
    const mean = new Float32Array(n)
    const std = new Float32Array(n)

    // Element-wise mean
    for (let i = 0; i < n; i++) {
      let sum = 0
      for (const curve of curves) {
        sum += curve[i]
      }
      mean[i] = sum / curves.length
    }

    // Element-wise std
    for (let i = 0; i < n; i++) {
      let sumSq = 0
      for (const curve of curves) {
        sumSq += (curve[i] - mean[i]) ** 2
      }
      std[i] = Math.sqrt(sumSq / curves.length)
    }

    result.push({
      seat: row.seat,
      side: row.side,
      channel: row.channel,
      curve_type: row.curve_type,
      mean: Array.from(mean),
      std: Array.from(std),
    })
  }

  return result
}
