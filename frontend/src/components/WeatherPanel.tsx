import type { Area, RoadFeatures, Stats } from '../lib/types'
import { flooded } from '../lib/features'

interface Props {
  area: Area | null
  features: RoadFeatures
  onFeatures: (f: RoadFeatures) => void
  stats: Stats | null
  waterAbout: string
  onEdit: () => void
}

const BREAKDOWNS = [
  { value: 0, label: 'None' },
  { value: 2, label: 'Occasional' },
  { value: 8, label: 'Frequent' },
]

const ELASTICITY = [
  { value: 0, label: 'No: the same trips whatever the traffic' },
  { value: 0.1, label: 'A little' },
  { value: 0.3, label: 'A lot' },
]

/** Rain, flooding, breakdowns and how much travellers avoid delay; all live. */
export function WeatherPanel(p: Props) {
  const f = p.features
  const set = (change: Partial<RoadFeatures>) => p.onFeatures({ ...f, ...change })
  const wet = flooded(f)
  const zones = f.water.filter((w) => w.enabled)
  const fromNews = zones.filter((w) => w.preset).length
  const perHour = f.breakdowns * ((p.area?.road_km ?? 100) / 100)
  const factor = p.stats?.demand_factor ?? 1

  return (
    <section aria-label="Weather and incidents">
      <h2>Weather and incidents</h2>
      <div className="field">
        <span className="field-label">Rain</span>
        <div className="seg wide" role="radiogroup" aria-label="Rain">
          {(['none', 'light', 'heavy'] as const).map((r) => (
            <button key={r} role="radio" aria-checked={f.weather.rain === r} className={f.weather.rain === r ? 'on' : ''} onClick={() => set({ weather: { ...f.weather, rain: r } })}>
              {{ none: 'Dry', light: 'Light rain', heavy: 'Heavy rain' }[r]}
            </button>
          ))}
        </div>
        <span className="muted small">
          {f.weather.rain === 'none' ? 'Normal driving.' : f.weather.rain === 'light' ? 'Drivers go about 10% slower and leave bigger gaps.' : 'Drivers go about 25% slower and leave much bigger gaps.'}
        </span>
      </div>
      <label className="field">
        <span className="field-label">Waterlogging</span>
        <select value={f.weather.flooding} onChange={(e) => set({ weather: { ...f.weather, flooding: e.target.value as RoadFeatures['weather']['flooding'] } })}>
          <option value="auto">Floods with heavy rain</option>
          <option value="on">Flooded now (after the rain)</option>
          <option value="off">No flooding</option>
        </select>
      </label>
      <p className="small">
        <strong>{zones.length}</strong> flood-prone spot{zones.length === 1 ? '' : 's'} in this area
        {fromNews > 0 && <span className="muted"> ({fromNews} from 2024–26 news reports)</span>}
        {' · '}
        <span className={wet ? 'notice' : 'muted'}>{wet ? 'flooded now' : 'dry'}</span>
      </p>
      {fromNews > 0 && (
        <p className="muted small" title={p.waterAbout}>
          Places and depths are estimates from the reports; click a zone on the map to see its sources or change it.
        </p>
      )}
      <button className="link" onClick={p.onEdit}>
        Edit waterlogging, bus stops, stands and hot zones on the map
      </button>

      <label className="field">
        <span className="field-label">
          Breakdowns
          {f.breakdowns > 0 && <output>~{perHour < 1 ? perHour.toFixed(1) : Math.round(perHour)} per hour here</output>}
        </span>
        <select value={f.breakdowns} onChange={(e) => set({ breakdowns: Number(e.target.value) })}>
          {BREAKDOWNS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
        <span className="muted small">A broken-down vehicle blocks its lane for 10–30 minutes.</span>
      </label>
      <label className="field">
        <span className="field-label">Fewer trips when roads are slow</span>
        <select value={f.elasticity} onChange={(e) => set({ elasticity: Number(e.target.value) })}>
          {ELASTICITY.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
        <span className="muted small">
          People put off trips, go later or go another way as delays grow.
          {f.elasticity > 0 && factor < 1 && <strong> Right now {Math.round((1 - factor) * 100)}% fewer trips are starting.</strong>}
        </span>
      </label>
    </section>
  )
}
