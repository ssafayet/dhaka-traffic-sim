/**
 * Map markers for road features, drawn once into a canvas atlas. Full colour
 * (not tinted), with a white rim so they read on light and dark maps.
 * Colours follow the vehicle palette: bus orange, battery rickshaw amber,
 * CNG green, pedal rickshaw purple; status red for breakdowns.
 */

export type MarkerIcon =
  | 'bus_stop'
  | 'stand_e_rickshaw'
  | 'stand_rickshaw'
  | 'stand_cng'
  | 'crossing'
  | 'crossing_active'
  | 'broken'

const SIZE = 48 // px in the atlas; drawn at about half that on the map

export interface MarkerAtlas {
  atlas: HTMLCanvasElement
  mapping: Record<MarkerIcon, { x: number; y: number; width: number; height: number; anchorX: number; anchorY: number; mask: boolean }>
}

let cached: MarkerAtlas | null = null

export function markerAtlas(): MarkerAtlas {
  if (cached) return cached
  const icons: MarkerIcon[] = ['bus_stop', 'stand_e_rickshaw', 'stand_rickshaw', 'stand_cng', 'crossing', 'crossing_active', 'broken']
  const canvas = document.createElement('canvas')
  canvas.width = SIZE * icons.length
  canvas.height = SIZE
  const ctx = canvas.getContext('2d')!
  const mapping = {} as MarkerAtlas['mapping']
  icons.forEach((icon, i) => {
    ctx.save()
    ctx.translate(i * SIZE, 0)
    draw(ctx, icon)
    ctx.restore()
    mapping[icon] = { x: i * SIZE, y: 0, width: SIZE, height: SIZE, anchorX: SIZE / 2, anchorY: SIZE / 2, mask: false }
  })
  cached = { atlas: canvas, mapping }
  return cached
}

function rim(ctx: CanvasRenderingContext2D, path: () => void, fill: string) {
  path()
  ctx.lineWidth = 6
  ctx.strokeStyle = '#ffffff'
  ctx.stroke()
  ctx.fillStyle = fill
  ctx.fill()
}

function letter(ctx: CanvasRenderingContext2D, text: string) {
  ctx.fillStyle = '#ffffff'
  ctx.font = 'bold 24px system-ui, -apple-system, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, SIZE / 2, SIZE / 2 + 1)
}

function draw(ctx: CanvasRenderingContext2D, icon: MarkerIcon) {
  const c = SIZE / 2
  switch (icon) {
    case 'bus_stop': {
      rim(ctx, () => {
        ctx.beginPath()
        ctx.roundRect(6, 6, SIZE - 12, SIZE - 12, 8)
      }, '#eb6834')
      // A bus from the front: body, windscreen, wheels.
      ctx.fillStyle = '#ffffff'
      ctx.beginPath()
      ctx.roundRect(15, 13, 18, 19, 3)
      ctx.fill()
      ctx.fillStyle = '#eb6834'
      ctx.fillRect(18, 16, 12, 7)
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(16, 32, 4, 4)
      ctx.fillRect(28, 32, 4, 4)
      break
    }
    case 'stand_e_rickshaw':
    case 'stand_rickshaw':
    case 'stand_cng': {
      const color = { stand_e_rickshaw: '#eda100', stand_rickshaw: '#4a3aa7', stand_cng: '#1baf7a' }[icon]
      rim(ctx, () => {
        ctx.beginPath()
        ctx.arc(c, c, c - 6, 0, Math.PI * 2)
      }, color)
      letter(ctx, icon === 'stand_cng' ? 'C' : 'R')
      break
    }
    case 'crossing':
    case 'crossing_active': {
      rim(ctx, () => {
        ctx.beginPath()
        ctx.roundRect(6, 6, SIZE - 12, SIZE - 12, 8)
      }, icon === 'crossing_active' ? '#fab219' : '#2b2b2b')
      // Zebra stripes.
      ctx.fillStyle = icon === 'crossing_active' ? '#2b2b2b' : '#ffffff'
      for (let k = 0; k < 4; k++) ctx.fillRect(13 + k * 6.5, 14, 3.5, 20)
      break
    }
    case 'broken': {
      rim(ctx, () => {
        ctx.beginPath()
        ctx.moveTo(c, 7)
        ctx.lineTo(SIZE - 6, SIZE - 9)
        ctx.lineTo(6, SIZE - 9)
        ctx.closePath()
      }, '#d03b3b')
      letter(ctx, '!')
      break
    }
  }
}
