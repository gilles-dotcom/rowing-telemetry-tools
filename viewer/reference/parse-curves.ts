import { parquetMetadata, parquetRead } from 'hyparquet'
import type { CurveRow } from './types'

/**
 * Parse a curves.parquet ArrayBuffer into CurveRow[].
 *
 * Parquet schema (written by the original app's uploader):
 *   seat: int8, side: string, channel: string, curve_type: string,
 *   mean: list<float32>[200], std: list<float32>[200]
 */
export async function parseCurvesParquet(buffer: ArrayBuffer): Promise<CurveRow[]> {
  const metadata = parquetMetadata(buffer)
  const columnNames = metadata.schema.slice(1).map((s: any) => s.name)

  const rows: CurveRow[] = []
  await parquetRead({
    file: buffer,
    metadata,
    onComplete: (data: any[][]) => {
      for (const row of data) {
        const obj: any = {}
        columnNames.forEach((name: string, i: number) => {
          obj[name] = row[i]
        })
        rows.push({
          seat: Number(obj.seat),
          side: String(obj.side),
          channel: String(obj.channel),
          curve_type: String(obj.curve_type),
          mean: Array.isArray(obj.mean) ? obj.mean.map(Number) : [],
          std: obj.std ? (Array.isArray(obj.std) ? obj.std.map(Number) : null) : null,
        })
      }
    },
  })

  return rows
}
