'use client'

import { useEffect, useRef } from 'react'
import 'leaflet/dist/leaflet.css'

interface GpsMapProps {
  gpsTrack?: { lat: number; lng: number; timestamp_ms: number }[]
  selectedStartMs?: number
  selectedEndMs?: number
}

function smoothTrack(track: GpsMapProps['gpsTrack'], window = 3): GpsMapProps['gpsTrack'] {
  if (!track || track.length < window) return track
  const half = Math.floor(window / 2)
  return track.map((p, i) => {
    const start = Math.max(0, i - half)
    const end = Math.min(track.length, i + half + 1)
    let lat = 0, lng = 0, count = 0
    for (let j = start; j < end; j++) {
      lat += track[j].lat; lng += track[j].lng; count++
    }
    return { lat: lat / count, lng: lng / count, timestamp_ms: p.timestamp_ms }
  })
}

export default function GpsMap({ gpsTrack, selectedStartMs, selectedEndMs }: GpsMapProps) {
  const mapRef = useRef<HTMLDivElement>(null)
  const mapInstanceRef = useRef<any>(null)

  useEffect(() => {
    const container = mapRef.current
    if (!container || !gpsTrack || gpsTrack.length === 0) return

    // Import leaflet only on client-side render
    import('leaflet').then(({ default: L }) => {

      // Clean up previous map
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove()
        mapInstanceRef.current = null
      }

      const smoothed = smoothTrack(gpsTrack, 9)!
      const points: [number, number][] = smoothed.map(p => [p.lat, p.lng])
      const map = L.map(container, {
        zoomControl: true,
      })
      mapInstanceRef.current = map

      // OpenStreetMap's standard tiles do not require an API key.
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors',
      }).addTo(map)

      // Full track polyline
      L.polyline(points, {
        color: '#7a8ba0',
        weight: 2,
        opacity: 0.6,
      }).addTo(map)

      // Highlight selected segment
      if (selectedStartMs != null && selectedEndMs != null) {
        const segmentPoints = smoothed
          .filter(p => p.timestamp_ms >= selectedStartMs && p.timestamp_ms <= selectedEndMs)
          .map(p => [p.lat, p.lng] as [number, number])

        if (segmentPoints.length > 1) {
          L.polyline(segmentPoints, {
            color: getComputedStyle(document.documentElement).getPropertyValue('--club').trim() || '#00b4d8',
            weight: 4,
            opacity: 0.9,
          }).addTo(map)
        }
      }

      // Start/end markers
      if (points.length > 0) {
        L.circleMarker(points[0], {
          radius: 5, color: '#51cf66', fillColor: '#51cf66', fillOpacity: 1, weight: 0,
        }).addTo(map).bindTooltip('Start', { permanent: false })

        L.circleMarker(points[points.length - 1], {
          radius: 5, color: '#ff6b6b', fillColor: '#ff6b6b', fillOpacity: 1, weight: 0,
        }).addTo(map).bindTooltip('End', { permanent: false })
      }

      // Fit bounds
      const bounds = L.latLngBounds(points)
      map.fitBounds(bounds, { padding: [20, 20] })
    }).catch(err => console.error('Failed to load leaflet:', err))

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove()
        mapInstanceRef.current = null
      }
    }
  }, [gpsTrack, selectedStartMs, selectedEndMs])

  if (!gpsTrack || gpsTrack.length === 0) {
    return (
      <div className="flex h-full min-h-[200px] items-center justify-center rounded-xl border border-border bg-[var(--surface-0)]">
        <div className="text-center">
          <svg className="mx-auto mb-2 h-8 w-8 text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1 1 15 0Z" />
          </svg>
          <p className="text-sm text-muted">No GPS data in this session</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={mapRef}
      className="relative h-full min-h-[200px] rounded-xl border border-border overflow-hidden"
      style={{ background: '#0f1c2e', zIndex: 0 }}
    />
  )
}
