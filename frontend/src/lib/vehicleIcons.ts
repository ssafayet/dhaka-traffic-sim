import type { VehicleType } from './types'

const PX_PER_M = 12

export interface IconAtlas {
  atlas: HTMLCanvasElement
  mapping: Record<string, { x: number; y: number; width: number; height: number; anchorX: number; anchorY: number; mask: boolean }>
}

/**
 * One top-down silhouette per vehicle type, to scale (length × width), nose
 * pointing up. Drawn white so deck.gl can tint it (mask mode). The anchor is
 * the front bumper, which is the point SUMO reports.
 */
export function buildIconAtlas(types: VehicleType[]): IconAtlas {
  const pad = 2
  const dims = types.map((t) => ({
    id: t.id,
    w: Math.round(t.width * PX_PER_M),
    h: Math.round(t.length * PX_PER_M),
  }))
  const canvas = document.createElement('canvas')
  canvas.width = dims.reduce((s, d) => s + d.w + pad * 2, 0)
  canvas.height = Math.max(...dims.map((d) => d.h)) + pad * 2
  const ctx = canvas.getContext('2d')!
  const mapping: IconAtlas['mapping'] = {}
  let x = 0
  for (const d of dims) {
    const ox = x + pad
    const oy = pad
    const r = Math.min(d.w, d.h) * 0.25
    ctx.fillStyle = '#fff'
    ctx.beginPath()
    ctx.roundRect(ox, oy, d.w, d.h, r)
    ctx.fill()
    // Windscreen: a translucent band near the nose shows direction of travel.
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = 'rgba(0,0,0,0.55)'
    const band = Math.max(2, d.h * 0.12)
    ctx.fillRect(ox + d.w * 0.15, oy + d.h * 0.14, d.w * 0.7, band)
    ctx.globalCompositeOperation = 'source-over'
    mapping[d.id] = { x: ox, y: oy, width: d.w, height: d.h, anchorX: d.w / 2, anchorY: 0, mask: true }
    x += d.w + pad * 2
  }
  return { atlas: canvas, mapping }
}
