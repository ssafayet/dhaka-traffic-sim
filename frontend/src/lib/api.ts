import type { Area, FeatureDefaults, LayoutEdits, LayoutError, Region, RoadFeature, Topology, VehicleType } from './types'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json() as Promise<T>
}

/** Layout edits the server refused, with a reason per edit. */
export class LayoutEditError extends Error {
  errors: LayoutError[]
  constructor(errors: LayoutError[]) {
    super(errors.map((e) => e.message).join('; '))
    this.errors = errors
  }
}

function withVariant(path: string, variant: string | null) {
  return variant ? `${path}?variant=${encodeURIComponent(variant)}` : path
}

export const api = {
  areas: () => get<{ default: string; areas: Area[] }>('/api/areas'),
  network: (areaId: string, variant: string | null = null) =>
    get<{ features: RoadFeature[] }>(withVariant(`/api/areas/${encodeURIComponent(areaId)}/network`, variant)).then(
      (d) => d.features,
    ),
  topology: (areaId: string, variant: string | null = null) =>
    get<Topology>(withVariant(`/api/areas/${encodeURIComponent(areaId)}/topology`, variant)),
  /** OpenStreetMap bus stops and news-reported waterlogging spots in the area. */
  featureDefaults: (areaId: string) => get<FeatureDefaults>(`/api/areas/${encodeURIComponent(areaId)}/features`),
  vehicleTypes: () => get<VehicleType[]>('/api/vehicle-types'),
  /** Region packs, each with its presets. */
  regions: () => get<{ default: string; regions: Region[] }>('/api/regions'),
  /** Build the network with these layout edits; variant null = no edits. */
  async buildLayout(areaId: string, edits: LayoutEdits): Promise<{ variant: string | null }> {
    const body = {
      uturns: edits.uturns.map(({ edge, lon, lat, both_directions }) => ({ edge, lon, lat, both_directions })),
      junction_uturns: edits.junction_uturns,
      junction_turns: edits.junction_turns,
      signals: edits.signals,
    }
    const res = await fetch(`/api/areas/${encodeURIComponent(areaId)}/variants`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (res.ok) return data as { variant: string | null }
    if (res.status === 422 && Array.isArray(data.errors)) throw new LayoutEditError(data.errors)
    const message = typeof data.detail === 'string' ? data.detail : `Could not build the road layout (${res.status}).`
    throw new LayoutEditError([{ kind: 'other', index: -1, message }])
  },
}
