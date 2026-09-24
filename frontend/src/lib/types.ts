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
  /** Seconds between drivers re-planning on current traffic; 0 = never (restart needed). */
  reroute_every: number
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
  /** Share of the set demand actually starting (travellers avoiding delay). */
  demand_factor?: number
  broken_down?: number
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
  /** Current phase index of each traffic signal. */
  signals?: Record<string, number>
  /** Crossing spots where people are crossing now. */
  crossing?: string[]
  /** Where vehicles have broken down. */
  broken?: [number, number][]
  warming_up: boolean
  warmup_progress?: number
}

export type SimState = 'idle' | 'connecting' | 'starting' | 'running' | 'paused' | 'stopped' | 'error'

export interface RoadFeature {
  type: 'Feature'
  properties: {
    id: string
    name: string
    lanes: number
    speed: number
    type: string
    length: number
    main?: boolean
    /** In an edited layout: the unedited road this piece was split from. */
    base?: string | null
    /** A median gap added as a U-turn. */
    uturn?: boolean
  }
  geometry: { type: 'LineString'; coordinates: [number, number][] }
}

// --- road edits ---------------------------------------------------------------

/** A closed road (every lane) or some of its lanes (0 = kerb side). Applies live. */
export interface Closure {
  edge: string
  lanes?: number[]
  /** The unedited road it belongs to, to follow it across layout changes. */
  base: string
  /** Seconds since midnight, for a closure that only applies part of the day. */
  from?: number
  to?: number
  reason?: ClosureReason
}

export type ClosureReason = 'construction' | 'event' | 'vip' | 'accident' | 'other'

// --- road features and weather (live) ---------------------------------------------

export interface NewsSource {
  outlet: string
  date: string
  title: string
  url: string
}

export interface BusStopF {
  id: string
  lon: number
  lat: number
  /** Average seconds a bus stands in the kerb lane. */
  dwell: number
  name?: string
  /** From OpenStreetMap rather than placed by the user. */
  osm?: boolean
}

export type StandKind = 'e_rickshaw' | 'rickshaw' | 'cng'

export interface StandF {
  id: string
  lon: number
  lat: number
  kind: StandKind
  /** Vehicles parked at the kerb. */
  parked: number
  /** 0..1: share of passing vehicles of this kind that stop for a fare. */
  pickup: number
  dwell: number
}

export interface CrossingF {
  id: string
  lon: number
  lat: number
  /** Average seconds between groups of people crossing. */
  every: number
  /** Seconds they hold up the road. */
  duration: number
}

export interface HotZoneF {
  id: string
  name: string
  lon: number
  lat: number
  radius: number
  /** Multiplier on trips starting and ending here. */
  trips: number
  /** 0..1: chance a bus, CNG or rickshaw stops at the kerb once in the zone. */
  kerb_stops: number
  vendors: boolean
  crowds: boolean
}

export type WaterDepth = 'shallow' | 'knee' | 'deep'

export interface WaterF {
  id: string
  name: string
  lon: number
  lat: number
  radius: number
  depth: WaterDepth
  enabled: boolean
  /** From the news-reported defaults. */
  preset?: boolean
  sources?: NewsSource[]
}

export interface Weather {
  rain: 'none' | 'light' | 'heavy'
  /** auto: flooded when rain is heavy. */
  flooding: 'auto' | 'on' | 'off'
}

export interface RoadFeatures {
  bus_stops: BusStopF[]
  stands: StandF[]
  crossings: CrossingF[]
  hot_zones: HotZoneF[]
  water: WaterF[]
  weather: Weather
  /** Breakdowns per hour per 100 km of road. */
  breakdowns: number
  /** Share of trips not made per 10 minutes of average delay. */
  elasticity: number
}

export interface FeatureDefaults {
  bus_stops: { id: string; lon: number; lat: number; name: string }[]
  water: (Omit<WaterF, 'enabled' | 'preset'> & { sources: NewsSource[] })[]
  water_about: string
}

export type FeatureKind = 'bus_stop' | 'stand' | 'crossing' | 'hot_zone' | 'water'
export type Tool = 'select' | FeatureKind

export type SignalMode = 'actuated' | 'fixed' | 'off'

export interface PhaseTiming {
  duration: number
  min: number
  max: number
}

/** A user's timings for a signal. Which movements are green comes from the network. */
export interface SignalPlan {
  mode: SignalMode
  phases: PhaseTiming[]
}

export interface SignalLink {
  from: string
  name: string
  /** Compass direction the approach comes from. */
  approach: string
  to_name: string
  /** SUMO link direction: s straight, l/L left, r/R right, t/T U-turn. */
  dir: string
}

export interface SignalInfo {
  id: string
  lon: number
  lat: number
  program_id: string
  mode: 'actuated' | 'fixed'
  /** Runs automatically by default (Dhaka's automatic corridors, or a signal the
   * user added); otherwise it starts switched off, as traffic police direct it. */
  automated: boolean
  /** state: one character per link — G/g green, y amber, r red. */
  phases: (PhaseTiming & { state: string })[]
  links: (SignalLink | null)[]
}

export interface JunctionInfo {
  id: string
  lon: number
  lat: number
  signal: string | null
  approaches: number
  /** Approaches that can make a U-turn here. */
  uturns: number
  names: string[]
  /**
   * Every junction of the crossing this one belongs to (a divided road's
   * crossing is mapped as several). Turn rules apply to the whole crossing.
   */
  group: string[]
}

/** Movements kept at a junction; left is the kerb-side turn. */
export type TurnRule = 'straight_left' | 'left'


export interface Topology {
  junctions: JunctionInfo[]
  signals: SignalInfo[]
  /** Road → the other direction of the same undivided street. */
  twins: Record<string, string>
}

export interface UTurnEdit {
  edge: string
  lon: number
  lat: number
  both_directions: boolean
  /** For the list of changes. */
  name: string
}

/** Structural changes; applied by rebuilding the network and restarting. */
export interface LayoutEdits {
  uturns: UTurnEdit[]
  junction_uturns: { junction: string; allow: boolean }[]
  /** Keep only these movements (no right turns or U-turns). */
  junction_turns: { junction: string; allow: TurnRule }[]
  /** Junctions to add a traffic signal to. */
  signals: string[]
}

export interface LayoutError {
  kind: 'uturn' | 'junction_uturn' | 'junction_turn' | 'signal' | 'other'
  index: number
  message: string
}

export type Selection =
  | { kind: 'road'; id: string; lon: number; lat: number }
  | { kind: 'junction'; id: string }
  | { kind: 'signal'; id: string }
  | { kind: 'uturn'; index: number }
  | { kind: FeatureKind; id: string }
