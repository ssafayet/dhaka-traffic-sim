/** Road features (bus stops, stands, crossings, zones) and weather. */

import type { FeatureDefaults, FeatureKind, RoadFeatures, StandKind, WaterDepth } from './types'

export const EMPTY_FEATURES: RoadFeatures = {
  bus_stops: [],
  stands: [],
  crossings: [],
  hot_zones: [],
  water: [],
  weather: { rain: 'none', flooding: 'auto' },
  breakdowns: 0,
  elasticity: 0,
}

/** The area's defaults: OpenStreetMap bus stops and news-reported waterlogging spots. */
export function defaultFeatures(d: FeatureDefaults | null): RoadFeatures {
  if (!d) return EMPTY_FEATURES
  return {
    ...EMPTY_FEATURES,
    bus_stops: d.bus_stops.map((s) => ({ id: s.id, lon: s.lon, lat: s.lat, name: s.name, dwell: 30, osm: true })),
    water: d.water.map((w) => ({ ...w, enabled: true, preset: true })),
  }
}

let counter = 0
export function newId(prefix: string) {
  counter += 1
  return `${prefix}${Date.now().toString(36)}${counter}`
}

export const LIST_KEY: Record<FeatureKind, 'bus_stops' | 'stands' | 'crossings' | 'hot_zones' | 'water'> = {
  bus_stop: 'bus_stops',
  stand: 'stands',
  crossing: 'crossings',
  hot_zone: 'hot_zones',
  water: 'water',
}

/** A new feature of this kind at a point, with sensible starting values. */
export function create(f: RoadFeatures, kind: FeatureKind, lon: number, lat: number): [RoadFeatures, string] {
  const id = newId(kind.slice(0, 2))
  switch (kind) {
    case 'bus_stop':
      return [{ ...f, bus_stops: [...f.bus_stops, { id, lon, lat, dwell: 30 }] }, id]
    case 'stand':
      return [{ ...f, stands: [...f.stands, { id, lon, lat, kind: 'e_rickshaw', parked: 3, pickup: 0.3, dwell: 20 }] }, id]
    case 'crossing':
      return [{ ...f, crossings: [...f.crossings, { id, lon, lat, every: 60, duration: 10 }] }, id]
    case 'hot_zone':
      return [
        {
          ...f,
          hot_zones: [
            ...f.hot_zones,
            { id, name: '', lon, lat, radius: 200, trips: 3, kerb_stops: 0.3, vendors: false, crowds: false },
          ],
        },
        id,
      ]
    case 'water':
      return [{ ...f, water: [...f.water, { id, name: '', lon, lat, radius: 200, depth: 'knee', enabled: true }] }, id]
  }
}

export function update<K extends FeatureKind>(f: RoadFeatures, kind: K, id: string, change: object): RoadFeatures {
  const key = LIST_KEY[kind]
  return { ...f, [key]: (f[key] as { id: string }[]).map((x) => (x.id === id ? { ...x, ...change } : x)) }
}

export function remove(f: RoadFeatures, kind: FeatureKind, id: string): RoadFeatures {
  const key = LIST_KEY[kind]
  return { ...f, [key]: (f[key] as { id: string }[]).filter((x) => x.id !== id) }
}

export function find(f: RoadFeatures, kind: FeatureKind, id: string) {
  return (f[LIST_KEY[kind]] as { id: string; lon: number; lat: number }[]).find((x) => x.id === id)
}

/** What the server needs: no names, sources or switched-off zones. */
export function toServer(f: RoadFeatures) {
  return {
    bus_stops: f.bus_stops.map(({ id, lon, lat, dwell }) => ({ id, lon, lat, dwell })),
    stands: f.stands,
    crossings: f.crossings,
    hot_zones: f.hot_zones.map(({ name: _name, ...z }) => z),
    water: f.water.filter((w) => w.enabled).map(({ id, lon, lat, radius, depth }) => ({ id, lon, lat, radius, depth })),
    weather: f.weather,
    breakdowns: f.breakdowns,
    elasticity: f.elasticity,
  }
}

export function flooded(f: RoadFeatures) {
  return f.weather.flooding === 'on' || (f.weather.flooding === 'auto' && f.weather.rain === 'heavy')
}

export const STAND_LABEL: Record<StandKind, string> = {
  e_rickshaw: 'Battery rickshaw stand',
  rickshaw: 'Pedal rickshaw stand',
  cng: 'CNG stand',
}

export const DEPTH: Record<WaterDepth, { label: string; effect: string }> = {
  shallow: { label: 'Ankle-deep', effect: 'Everyone slows to 20 km/h.' },
  knee: { label: 'Knee-deep', effect: 'Up to 10 km/h; motorcycles and CNGs stall and can’t get through.' },
  deep: { label: 'Waist-deep', effect: 'Up to 5 km/h; only buses, trucks and rickshaws get through.' },
}

export const FEATURE_LABEL: Record<FeatureKind, string> = {
  bus_stop: 'Bus stop',
  stand: 'Rickshaw / CNG stand',
  crossing: 'Pedestrian crossing',
  hot_zone: 'Hot zone',
  water: 'Waterlogging',
}

export function featureTitle(f: RoadFeatures, kind: FeatureKind, id: string) {
  const item = find(f, kind, id) as { name?: string; kind?: StandKind } | undefined
  if (!item) return FEATURE_LABEL[kind]
  if (kind === 'stand' && item.kind) return STAND_LABEL[item.kind]
  return item.name || FEATURE_LABEL[kind]
}
