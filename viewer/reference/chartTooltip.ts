import { Tooltip } from 'chart.js'

let registered = false

export function registerTooltipPositioner() {
  if (registered) return
  registered = true

  ;(Tooltip.positioners as any).viewerFixedTopLeft = function () {
    const chart = (this as any).chart
    const area = chart?.chartArea
    const pad = 10

    if (area) {
      return {
        x: area.left + pad,
        y: area.top + pad,
        xAlign: 'left' as const,
        yAlign: 'top' as const,
      }
    }

    return { x: pad, y: pad, xAlign: 'left' as const, yAlign: 'top' as const }
  }
}

export function viewerTooltipPosition() {
  return {
    position: 'viewerFixedTopLeft' as const,
    caretSize: 0,
  }
}
