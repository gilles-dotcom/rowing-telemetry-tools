import type { PeriodicData } from '@/lib/parse-periodic'

export const ALIGNMENT_OFFSET_LIMIT_MS = 86400000
export const ALIGNMENT_SCALE_LIMIT_PPM = 20000
export type TelemetryOffsetSource = 'auto' | 'manual'

export function clampAlignmentOffsetMs(offsetMs: number) {
  if (!Number.isFinite(offsetMs)) return 0
  return Math.max(-ALIGNMENT_OFFSET_LIMIT_MS, Math.min(ALIGNMENT_OFFSET_LIMIT_MS, Math.round(offsetMs)))
}

export function clampAlignmentScalePpm(scalePpm: number) {
  if (!Number.isFinite(scalePpm)) return 0
  return Math.max(-ALIGNMENT_SCALE_LIMIT_PPM, Math.min(ALIGNMENT_SCALE_LIMIT_PPM, Math.round(scalePpm)))
}

export function scalePpmToFactor(scalePpm: number | null | undefined) {
  return 1 + clampAlignmentScalePpm(Number(scalePpm)) / 1000000
}

export function computeAutoTelemetryOffsetMs(sessionStartUtc: string | null | undefined, recordingStartedAt: string | null | undefined) {
  if (!sessionStartUtc || !recordingStartedAt) return null
  const sessionStartMs = Date.parse(sessionStartUtc)
  const recordingStartMs = Date.parse(recordingStartedAt)
  if (!Number.isFinite(sessionStartMs) || !Number.isFinite(recordingStartMs)) return null
  return clampAlignmentOffsetMs(recordingStartMs - sessionStartMs)
}

export function resolveTelemetryOffsetMs({
  sessionStartUtc,
  recordingStartedAt,
  telemetryOffsetMs,
  telemetryOffsetSource,
}: {
  sessionStartUtc: string | null | undefined
  recordingStartedAt: string | null | undefined
  telemetryOffsetMs: number | null | undefined
  telemetryOffsetSource: TelemetryOffsetSource | null | undefined
}) {
  if (telemetryOffsetSource !== 'manual') {
    const autoOffset = computeAutoTelemetryOffsetMs(sessionStartUtc, recordingStartedAt)
    if (autoOffset != null) return autoOffset
  }

  const storedOffset = Number(telemetryOffsetMs)
  return Number.isFinite(storedOffset) ? clampAlignmentOffsetMs(storedOffset) : 0
}

export function videoTimeToSessionMsScaled(videoTimeMs: number, telemetryOffsetMs: number, telemetryScalePpm: number | null | undefined) {
  if (!Number.isFinite(videoTimeMs)) return null
  const scaledVideoMs = videoTimeMs * scalePpmToFactor(telemetryScalePpm)
  return Math.round(scaledVideoMs + clampAlignmentOffsetMs(telemetryOffsetMs))
}

export function activeStrokeIndexAt(timeMs: number[], sessionMs: number) {
  if (!timeMs.length) return null
  if (!Number.isFinite(sessionMs)) return null

  if (sessionMs < timeMs[0]) return null

  let lo = 0
  let hi = timeMs.length - 1
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2)
    if (timeMs[mid] <= sessionMs) lo = mid + 1
    else hi = mid - 1
  }

  return Math.max(0, Math.min(timeMs.length - 1, hi))
}

export function strokeProgressAt(
  timeMs: number[],
  strokeEndTimesMs: number[],
  strokeIndex: number | null,
  sessionMs: number,
) {
  if (strokeIndex == null || strokeIndex < 0 || strokeIndex >= timeMs.length) return null
  if (!Number.isFinite(sessionMs)) return null

  const strokeStart = timeMs[strokeIndex]
  const strokeEnd = strokeEndTimesMs[strokeIndex]
  if (!Number.isFinite(strokeStart) || !Number.isFinite(strokeEnd) || strokeEnd <= strokeStart) return null

  const progress = (sessionMs - strokeStart) / (strokeEnd - strokeStart)
  if (!Number.isFinite(progress)) return null
  return Math.max(0, Math.min(1, progress))
}

export function strokePhaseProgressAt(
  timeMs: number[],
  strokeEndTimesMs: number[],
  strokeIndex: number | null,
  sessionMs: number,
) {
  if (strokeIndex == null || strokeIndex < 0 || strokeIndex >= timeMs.length) return null
  if (!Number.isFinite(sessionMs)) return null

  const strokeStart = timeMs[strokeIndex]
  const strokeEnd = strokeEndTimesMs[strokeIndex]
  if (!Number.isFinite(strokeStart) || !Number.isFinite(strokeEnd) || strokeEnd <= strokeStart) return null

  const rawProgress = (sessionMs - strokeStart) / (strokeEnd - strokeStart)
  if (!Number.isFinite(rawProgress)) return null

  const wrapped = rawProgress % 1
  return wrapped < 0 ? wrapped + 1 : wrapped
}

export function displayStartTimesFromFinishes(catchTimesMs: number[], finishTimesMs: number[]) {
  if (!catchTimesMs.length) return []
  return catchTimesMs.map((catchMs, index) => {
    if (index === 0) return catchMs
    const previousFinish = finishTimesMs[index - 1]
    return Number.isFinite(previousFinish) && previousFinish > catchTimesMs[index - 1] && previousFinish < catchMs
      ? previousFinish
      : catchMs
  })
}

function average(values: Array<number | null | undefined>) {
  if (!values.length) return null
  const finite = values.filter((value): value is number => Number.isFinite(value))
  if (!finite.length) return null
  return finite.reduce((total, value) => total + value, 0) / finite.length
}

function averageStrokeTimingOffset(
  map: Map<number, number[]>,
  strokeIndex: number,
) {
  const values = Array.from(map.values())
    .map(row => row[strokeIndex])
    .filter((value): value is number => Number.isFinite(value) && value >= 0)
  return average(values)
}

export function buildStrokeEndTimesMs(data: PeriodicData) {
  const timeMs = data.timeMs
  const n = timeMs.length
  if (n === 0) return []

  const endTimes: number[] = new Array(n)

  for (let i = 0; i < n; i += 1) {
    const strokeStartMs = timeMs[i]
    const nextStrokeStartMs = timeMs[i + 1]
    if (Number.isFinite(nextStrokeStartMs) && nextStrokeStartMs > strokeStartMs) {
      endTimes[i] = nextStrokeStartMs
      continue
    }

    const durationMs = data.durationMs[i]
    endTimes[i] = Number.isFinite(durationMs) && durationMs > 0
      ? strokeStartMs + durationMs
      : strokeStartMs
  }

  return endTimes
}

export function buildStrokeFinishTimesMs(data: PeriodicData, strokeEndTimesMs: number[]) {
  return data.timeMs.map((catchMs, index) => {
    const durationMs = data.durationMs[index]
    const recoveryMs = averageStrokeTimingOffset(data.seatRecoveryTimeMs, index)
    const driveTimeMs = averageStrokeTimingOffset(data.seatDriveTimeMs, index)

    const finishByRecovery = Number.isFinite(durationMs) && durationMs > 0 && recoveryMs != null && recoveryMs >= 0
      ? catchMs + Math.max(0, durationMs - recoveryMs)
      : null
    const finishByDriveTime = driveTimeMs != null && driveTimeMs > 0
      ? catchMs + driveTimeMs
      : null
    const fallbackFinish = Number.isFinite(durationMs) && durationMs > 0
      ? catchMs + durationMs * 0.35
      : strokeEndTimesMs[index]

    const finishMs = finishByRecovery ?? finishByDriveTime ?? fallbackFinish
    return Number.isFinite(finishMs) && finishMs > catchMs
      ? Math.min(finishMs, strokeEndTimesMs[index])
      : catchMs
  })
}

export function buildStrokeDisplayStartTimesMs(data: PeriodicData) {
  const strokeEndTimesMs = buildStrokeEndTimesMs(data)
  const strokeFinishTimesMs = buildStrokeFinishTimesMs(data, strokeEndTimesMs)
  return displayStartTimesFromFinishes(data.timeMs, strokeFinishTimesMs)
}
