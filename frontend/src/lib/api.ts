import type { Area, Preset, RoadFeature, VehicleType } from './types'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  areas: () => get<{ default: string; areas: Area[] }>('/api/areas'),
  network: (areaId: string) =>
    get<{ features: RoadFeature[] }>(`/api/areas/${encodeURIComponent(areaId)}/network`).then((d) => d.features),
  vehicleTypes: () => get<VehicleType[]>('/api/vehicle-types'),
  presets: () => get<{ default: string; presets: Preset[] }>('/api/presets'),
}
