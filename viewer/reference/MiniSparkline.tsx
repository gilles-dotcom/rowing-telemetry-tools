'use client'

import { useRef, useEffect } from 'react'

interface Props {
  data: number[]
  color: string
  height?: number
}

export default function MiniSparkline({ data, color, height = 28 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || data.length < 2) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const w = canvas.offsetWidth
    const h = canvas.offsetHeight
    canvas.width = w * dpr
    canvas.height = h * dpr
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    const valid = data.filter(v => v != null && v > 0)
    if (valid.length < 2) return
    const min = Math.min(...valid) * 0.92
    const max = Math.max(...valid) * 1.08
    const range = max - min || 1

    const grad = ctx.createLinearGradient(0, 0, 0, h)
    grad.addColorStop(0, color + '28')
    grad.addColorStop(1, 'transparent')

    ctx.beginPath()
    ctx.moveTo(0, h)
    for (let i = 0; i < data.length; i++) {
      const x = (i / (data.length - 1)) * w
      const y = h - ((Math.max(data[i] || 0, 0) - min) / range) * h
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.lineTo(w, h)
    ctx.lineTo(0, h)
    ctx.fillStyle = grad
    ctx.fill()

    ctx.beginPath()
    for (let i = 0; i < data.length; i++) {
      const x = (i / (data.length - 1)) * w
      const y = h - ((Math.max(data[i] || 0, 0) - min) / range) * h
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    ctx.stroke()
  }, [data, color])

  return <canvas ref={canvasRef} className="w-full" style={{ height }} />
}
