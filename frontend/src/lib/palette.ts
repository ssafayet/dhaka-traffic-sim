/** Colours for map layers (deck.gl needs RGBA arrays, not CSS variables). */

export type RGBA = [number, number, number, number]
export type Theme = 'light' | 'dark'

function hex(h: string, a = 255): RGBA {
  const n = parseInt(h.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, a]
}

// Categorical slots 1–7 in fixed order (the validated reference palette).
// Order matches the backend's VEHICLE_TYPES list. Battery rickshaws took over
// the rickshaw's slot 4; the now-rare pedal rickshaw is slot 7.
const VEHICLE_HEX: Record<Theme, Record<string, string>> = {
  light: {
    car: '#2a78d6', bus: '#eb6834', cng: '#1baf7a', e_rickshaw: '#eda100',
    motorcycle: '#e87ba4', truck: '#008300', rickshaw: '#4a3aa7',
  },
  dark: {
    car: '#3987e5', bus: '#d95926', cng: '#199e70', e_rickshaw: '#c98500',
    motorcycle: '#d55181', truck: '#008300', rickshaw: '#9085e9',
  },
}

export function vehicleColor(theme: Theme, typeId: string): string {
  return VEHICLE_HEX[theme][typeId] ?? '#898781'
}

export function vehicleRGBA(theme: Theme, typeId: string): RGBA {
  return hex(vehicleColor(theme, typeId))
}

// Congestion levels use the reserved status palette; always shown with labels.
export const CONGESTION = [
  { min: 0.75, label: 'Free flow', color: '#0ca30c' },
  { min: 0.5, label: 'Slow', color: '#fab219' },
  { min: 0.25, label: 'Heavy', color: '#ec835a' },
  { min: -1, label: 'Jammed', color: '#d03b3b' },
] as const

const CONGESTION_RGBA = CONGESTION.map((c) => hex(c.color, 230))

export function congestionIndex(level: number): number {
  return CONGESTION.findIndex((c) => level >= c.min)
}

export function congestionRGBA(level: number): RGBA {
  return CONGESTION_RGBA[congestionIndex(level)]
}

export const ROAD_IDLE: Record<Theme, RGBA> = {
  light: hex('#898781', 90),
  dark: hex('#898781', 110),
}

export const MAP_STYLE: Record<Theme, string> = {
  light: 'https://tiles.openfreemap.org/styles/positron',
  dark: 'https://tiles.openfreemap.org/styles/dark',
}

// Road editor. Closed roads are drawn in the ink colour (black / white) so they
// never read as a congestion level; markers use the status reds and ambers.
export const EDIT_RGBA = {
  accent: { light: hex('#2a78d6'), dark: hex('#3987e5') } as Record<Theme, RGBA>,
  closed: { light: hex('#0b0b0b', 230), dark: hex('#ffffff', 230) } as Record<Theme, RGBA>,
  closedMarker: hex('#d03b3b'),
  laneMarker: hex('#fab219'),
  markerStroke: { light: hex('#ffffff'), dark: hex('#0d0d0d') } as Record<Theme, RGBA>,
  junction: { light: hex('#fcfcfb', 230), dark: hex('#1a1a19', 230) } as Record<Theme, RGBA>,
  junctionStroke: { light: hex('#52514e'), dark: hex('#c3c2b7') } as Record<Theme, RGBA>,
}

export const SIGNAL_RGBA: Record<'green' | 'amber' | 'red' | 'off', RGBA> = {
  green: hex('#0ca30c'),
  amber: hex('#fab219'),
  red: hex('#d03b3b'),
  off: hex('#898781'),
}
