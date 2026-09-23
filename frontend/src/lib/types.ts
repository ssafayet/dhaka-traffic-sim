export interface Area {
  id: string
  name: string
  city: string
  kind?: 'city' | 'area'
  detail?: 'full' | 'arterial'
  bbox: [number, number, number, number]
  center: [number, number]
  edges: number
  road_km: number
  /** Multiplier for preset volumes, which are tuned on Farmgate. */
  demand_scale?: number
  /** Multiplier for preset through-traffic shares. */
  through_scale?: number
}

export interface VehicleType {
  id: string
  label: string
  color: string
  length: number
  width: number
  max_speed_kmh: number
  default_share: number
}

export type Mix = Record<string, number>

export interface Preset {
  id: string
  label: string
  description: string
  start_hour: number
  volume: number
  through_share: number
  mix: Mix
}

export interface Demand {
  volume: number
  mix: Mix
  through_share: number
}

export interface SimOptions {
  sublane: boolean
  teleport_after: number
  start_hour: number
  rickshaws_on_main_roads: boolean
}

export interface Stats {
  time: number
  clock: number
  running: number
  waiting: number
  departed: number
  arrived: number
  teleports: number
  failed_routes: number
  avg_speed_kmh: number
  stopped: number
  avg_trip_min: number | null
  avg_delay_min: number | null
  on_road_delay_min: number
  throughput_per_hour: number
}

export interface Frame {
  type: 'frame'
  ids: number[]
  lon: number[]
  lat: number[]
  angle: number[]
  speed: number[]
  kind: number[]
  stats: Stats
  edges?: Record<string, number>
  warming_up: boolean
  warmup_progress?: number
}

export type SimState = 'idle' | 'connecting' | 'starting' | 'running' | 'paused' | 'stopped' | 'error'

export interface RoadFeature {
  type: 'Feature'
  properties: { id: string; name: string; lanes: number; speed: number; type: string; length: number; main?: boolean }
  geometry: { type: 'LineString'; coordinates: [number, number][] }
}
