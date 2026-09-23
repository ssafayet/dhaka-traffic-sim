import type { ReactNode } from 'react'
import type { Area, Demand, Preset, SimOptions, SimState, VehicleType } from '../lib/types'
import { vehicleColor, type Theme } from '../lib/palette'
import { SOURCE_URL } from '../lib/project'

interface Props {
  theme: Theme
  areas: Area[]
  areaId: string
  onArea: (id: string) => void
  onJump: (bbox: Area['bbox']) => void
  presets: Preset[]
  presetId: string | null
  onPreset: (p: Preset) => void
  types: VehicleType[]
  demand: Demand
  onDemand: (d: Demand) => void
  options: SimOptions
  onOptions: (o: SimOptions) => void
  needsRestart: boolean
  state: SimState
  warmup: number | null
  speed: number
  onSpeed: (s: number) => void
  onStart: () => void
  onPauseToggle: () => void
  /** Extra sections, shown above the transport controls. */
  children?: ReactNode
}

const SPEEDS = [
  { value: 1, label: '1×' },
  { value: 2, label: '2×' },
  { value: 5, label: '5×' },
  { value: 10, label: '10×' },
  { value: 0, label: 'Max' },
]

const REROUTE = [
  { value: 0, label: 'never (keep the first route)' },
  { value: 120, label: 'every 2 minutes' },
  { value: 300, label: 'every 5 minutes' },
]

const TELEPORT = [
  { value: 120, label: 'after 2 min' },
  { value: 300, label: 'after 5 min' },
  { value: -1, label: 'never (true gridlock)' },
]

// Volume slider range for Farmgate; scaled by each area's demand_scale.
const VOLUME_MAX = 20000
const VOLUME_STEP = 250

/** Round to 1, 2 or 5 × a power of ten, so scaled slider steps stay readable. */
function niceStep(v: number) {
  const p = 10 ** Math.floor(Math.log10(v))
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= v) ?? v
}

export function ControlPanel(p: Props) {
  const totalMix = Object.values(p.demand.mix).reduce((a, b) => a + Math.max(0, b), 0) || 1
  const running = p.state === 'running' || p.state === 'paused' || p.state === 'starting'
  const area = p.areas.find((a) => a.id === p.areaId)
  const cities = p.areas.filter((a) => a.kind === 'city')
  const neighbourhoods = p.areas.filter((a) => a.kind !== 'city')
  const isCity = area?.kind === 'city'
  const volumeStep = niceStep(VOLUME_STEP * (area?.demand_scale ?? 1))
  const volumeMax = Math.ceil((VOLUME_MAX * (area?.demand_scale ?? 1)) / volumeStep) * volumeStep

  return (
    <aside className="panel" aria-label="Simulation controls">
      <header className="brand">
        <h1>Dhaka Traffic Sim</h1>
        <p>Simulated with SUMO on OpenStreetMap roads.</p>
        {/* A new tab, so a running simulation and its road edits survive. */}
        <a className="docs-link" href="#docs" target="_blank" rel="noopener">
          How it works and how to use it
        </a>
        {/* Author credit: keep it, per the notice in NOTICE (AGPL-3.0 section 7(b)). */}
        <p className="credit">
          By SSafayet · <a href={SOURCE_URL}>Source code</a> (AGPL-3.0)
        </p>
      </header>

      <section>
        <label className="field">
          <span className="field-label">Area</span>
          <select value={p.areaId} onChange={(e) => p.onArea(e.target.value)}>
            {cities.length > 0 && (
              <optgroup label="Whole city">
                {cities.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="Neighbourhoods (every street)">
              {neighbourhoods.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        {area && (
          <p className="muted small">
            {area.road_km.toLocaleString()} km of road · {area.edges.toLocaleString()} road segments
            {isCity && ' · main roads only'}
          </p>
        )}
        {isCity && neighbourhoods.length > 0 && (
          <label className="field">
            <span className="field-label">Zoom to</span>
            <select
              value=""
              onChange={(e) => {
                const target = p.areas.find((a) => a.id === e.target.value)
                if (target) p.onJump(target.bbox)
              }}
            >
              <option value="">Whole city</option>
              {neighbourhoods.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      <section>
        <h2>Scenario</h2>
        <div className="chips" role="radiogroup" aria-label="Scenario preset">
          {p.presets.map((pr) => (
            <button
              key={pr.id}
              role="radio"
              aria-checked={p.presetId === pr.id}
              className={`chip ${p.presetId === pr.id ? 'on' : ''}`}
              onClick={() => p.onPreset(pr)}
              title={pr.description}
            >
              {pr.label}
            </button>
          ))}
        </div>
        {p.presetId && <p className="muted small">{p.presets.find((x) => x.id === p.presetId)?.description}</p>}
      </section>

      <section>
        <h2>Traffic demand</h2>
        <label className="field">
          <span className="field-label">
            Vehicles entering per hour <output>{p.demand.volume.toLocaleString()}</output>
          </span>
          <input
            type="range"
            min={0}
            max={volumeMax}
            step={volumeStep}
            value={p.demand.volume}
            onChange={(e) => p.onDemand({ ...p.demand, volume: Number(e.target.value) })}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Just passing through <output>{Math.round(p.demand.through_share * 100)}%</output>
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={p.demand.through_share}
            onChange={(e) => p.onDemand({ ...p.demand, through_share: Number(e.target.value) })}
          />
        </label>
        <p className="muted small">Changes apply live while the simulation runs.</p>
      </section>

      <section>
        <h2>Vehicle mix</h2>
        {p.types.map((t) => {
          const w = p.demand.mix[t.id] ?? 0
          return (
            <label className="field mix" key={t.id}>
              <span className="field-label">
                <span className="swatch" style={{ background: vehicleColor(p.theme, t.id) }} aria-hidden />
                {t.label}
                <output>{Math.round((Math.max(0, w) / totalMix) * 100)}%</output>
              </span>
              <input
                type="range"
                min={0}
                max={60}
                step={1}
                value={w}
                onChange={(e) => p.onDemand({ ...p.demand, mix: { ...p.demand.mix, [t.id]: Number(e.target.value) } })}
              />
            </label>
          )
        })}
      </section>

      <section>
        <h2>Driving behaviour</h2>
        <label className="check">
          <input
            type="checkbox"
            checked={p.options.sublane}
            onChange={(e) => p.onOptions({ ...p.options, sublane: e.target.checked })}
          />
          <span>
            Lane-free movement
            <span className="muted small block">Bikes, CNGs and rickshaws squeeze into gaps instead of keeping to lanes.</span>
            {isCity && p.options.sublane && (
              <span className="notice small block">Slow at whole-city scale; turn off for a faster run.</span>
            )}
          </span>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={p.options.rickshaws_on_main_roads}
            onChange={(e) => p.onOptions({ ...p.options, rickshaws_on_main_roads: e.target.checked })}
          />
          <span>
            Rickshaws allowed on main roads
            <span className="muted small block">
              Battery and pedal rickshaws on trunk and primary roads such as Mirpur Road and Progoti Sarani. Officially
              banned there, but in practice they are everywhere. Off: they detour through side streets.
            </span>
          </span>
        </label>
        <label className="field">
          <span className="field-label">Drivers re-plan around traffic</span>
          <select value={p.options.reroute_every} onChange={(e) => p.onOptions({ ...p.options, reroute_every: Number(e.target.value) })}>
            {REROUTE.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {p.options.reroute_every > 0 && isCity && <span className="notice small">Slower to simulate at whole-city scale.</span>}
        </label>
        <label className="field">
          <span className="field-label">Remove stuck vehicles</span>
          <select
            value={p.options.teleport_after}
            onChange={(e) => p.onOptions({ ...p.options, teleport_after: Number(e.target.value) })}
          >
            {TELEPORT.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </section>

      {p.children}

      <section className="transport">
        <div className="speed" role="radiogroup" aria-label="Simulation speed">
          {SPEEDS.map((s) => (
            <button
              key={s.value}
              role="radio"
              aria-checked={p.speed === s.value}
              className={p.speed === s.value ? 'on' : ''}
              onClick={() => p.onSpeed(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="buttons">
          <button className="primary" onClick={p.onStart}>
            {running ? 'Restart' : 'Start simulation'}
          </button>
          <button onClick={p.onPauseToggle} disabled={!(p.state === 'running' || p.state === 'paused')}>
            {p.state === 'paused' ? 'Resume' : 'Pause'}
          </button>
        </div>
        {p.needsRestart && running && <p className="notice small">Restart to apply the area, driving-behaviour or rickshaw-rule change.</p>}
        {p.warmup !== null && (
          <div className="warmup" role="status">
            <span>Filling the roads… {Math.round(p.warmup * 100)}%</span>
            <progress max={1} value={p.warmup} />
          </div>
        )}
      </section>
    </aside>
  )
}
