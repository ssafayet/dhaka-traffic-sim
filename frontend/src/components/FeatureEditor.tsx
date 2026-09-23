import type { FeatureKind, RoadFeatures, StandKind, WaterDepth } from '../lib/types'
import { DEPTH, find, flooded, remove, update } from '../lib/features'

interface Props {
  kind: FeatureKind
  id: string
  features: RoadFeatures
  onFeatures: (f: RoadFeatures) => void
  onClose: () => void
}

const LIVE = 'Applies at once.'

/** Settings for one bus stop, stand, crossing, hot zone or waterlogging zone. */
export function FeatureEditor(p: Props) {
  const item = find(p.features, p.kind, p.id)
  if (!item) return null
  const set = (change: object) => p.onFeatures(update(p.features, p.kind, p.id, change))
  const drop = () => {
    p.onFeatures(remove(p.features, p.kind, p.id))
    p.onClose()
  }

  switch (p.kind) {
    case 'bus_stop': {
      const s = p.features.bus_stops.find((x) => x.id === p.id)!
      return (
        <>
          <p className="muted small">
            Buses whose route passes here stop in the kerb lane, holding up traffic behind them.
            {s.osm && ' From OpenStreetMap.'}
          </p>
          <Slider label="Average stop" unit="s" min={5} max={180} step={5} value={s.dwell} onChange={(v) => set({ dwell: v })} />
          <p className="muted small">{LIVE} New buses stop here from now on.</p>
          <button onClick={drop}>Remove this bus stop</button>
        </>
      )
    }
    case 'stand': {
      const s = p.features.stands.find((x) => x.id === p.id)!
      return (
        <>
          <div className="seg wide" role="radiogroup" aria-label="Vehicles at this stand">
            {(
              [
                ['e_rickshaw', 'Battery rickshaw'],
                ['rickshaw', 'Pedal rickshaw'],
                ['cng', 'CNG'],
              ] as [StandKind, string][]
            ).map(([v, label]) => (
              <button key={v} role="radio" aria-checked={s.kind === v} className={s.kind === v ? 'on' : ''} onClick={() => set({ kind: v })}>
                {label}
              </button>
            ))}
          </div>
          <Slider label="Parked at the kerb" unit="" min={0} max={12} step={1} value={s.parked} onChange={(v) => set({ parked: v })} />
          <Slider
            label="Passing ones that stop for a fare"
            unit="%"
            min={0}
            max={100}
            step={5}
            value={Math.round(s.pickup * 100)}
            onChange={(v) => set({ pickup: v / 100 })}
          />
          <Slider label="Average stop" unit="s" min={5} max={180} step={5} value={s.dwell} onChange={(v) => set({ dwell: v })} />
          <p className="muted small">
            Parked vehicles sit at the kerb. On a one-lane street they leave room only for motorcycles and rickshaws; cars and
            buses queue behind them. {LIVE}
          </p>
          <button onClick={drop}>Remove this stand</button>
        </>
      )
    }
    case 'crossing': {
      const c = p.features.crossings.find((x) => x.id === p.id)!
      return (
        <>
          <p className="muted small">
            People cross here now and then, holding up every lane in both directions until they're across. Use it for crossing
            mid-block, outside a school or at a bus stop.
          </p>
          <Slider label="A group crosses about every" unit="s" min={10} max={600} step={5} value={c.every} onChange={(v) => set({ every: v })} />
          <Slider label="Road held up for" unit="s" min={2} max={60} step={1} value={c.duration} onChange={(v) => set({ duration: v })} />
          <p className="muted small">{LIVE} The marker turns yellow while people are crossing.</p>
          <button onClick={drop}>Remove this crossing</button>
        </>
      )
    }
    case 'hot_zone': {
      const z = p.features.hot_zones.find((x) => x.id === p.id)!
      return (
        <>
          <label className="field">
            <span className="field-label">Name</span>
            <input className="text" value={z.name} placeholder="e.g. New Market" maxLength={60} onChange={(e) => set({ name: e.target.value })} />
          </label>
          <Slider label="Radius" unit="m" min={50} max={1000} step={25} value={z.radius} onChange={(v) => set({ radius: v })} />
          <Slider label="Trips starting and ending here" unit="×" min={1} max={10} step={0.5} value={z.trips} onChange={(v) => set({ trips: v })} />
          <Slider
            label="Buses, CNGs and rickshaws that stop at the kerb here"
            unit="%"
            min={0}
            max={100}
            step={5}
            value={Math.round(z.kerb_stops * 100)}
            onChange={(v) => set({ kerb_stops: v / 100 })}
          />
          <label className="check">
            <input type="checkbox" checked={z.vendors} onChange={(e) => set({ vendors: e.target.checked })} />
            <span>
              Vendors take the kerb lane
              <span className="muted small block">The kerb lane is closed; on one-lane streets traffic crawls at 15 km/h.</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={z.crowds} onChange={(e) => set({ crowds: e.target.checked })} />
            <span>
              Crowds on the road
              <span className="muted small block">People walking in the road slow traffic to 60% of the speed limit.</span>
            </span>
          </label>
          <p className="muted small">For markets, schools, hospitals and terminals. {LIVE}</p>
          <button onClick={drop}>Remove this hot zone</button>
        </>
      )
    }
    case 'water': {
      const w = p.features.water.find((x) => x.id === p.id)!
      const wet = flooded(p.features)
      return (
        <>
          {!w.preset && (
            <label className="field">
              <span className="field-label">Name</span>
              <input className="text" value={w.name} placeholder="e.g. Green Road" maxLength={60} onChange={(e) => set({ name: e.target.value })} />
            </label>
          )}
          <label className="check tight">
            <input type="checkbox" checked={w.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
            <span>Floods in this simulation</span>
          </label>
          <h3>Depth</h3>
          <div className="seg wide" role="radiogroup" aria-label="Water depth">
            {(Object.keys(DEPTH) as WaterDepth[]).map((d) => (
              <button key={d} role="radio" aria-checked={w.depth === d} className={w.depth === d ? 'on' : ''} onClick={() => set({ depth: d })}>
                {DEPTH[d].label}
              </button>
            ))}
          </div>
          <p className="muted small">{DEPTH[w.depth].effect}</p>
          <Slider label="Radius" unit="m" min={50} max={1000} step={25} value={w.radius} onChange={(v) => set({ radius: v })} />
          <p className={wet ? 'small' : 'notice small'}>
            {wet ? 'Flooded now.' : 'Dry now. It floods with heavy rain, or when you choose “Flooded now” under Weather and incidents.'}
          </p>
          {w.sources && w.sources.length > 0 && (
            <>
              <h3>Reported in</h3>
              <ul className="sources">
                {w.sources.map((s) => (
                  <li key={s.url}>
                    <a href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.outlet}, {s.date}
                    </a>
                  </li>
                ))}
              </ul>
              <p className="muted small">Position and depth are estimates from these reports. Adjust them if you know better.</p>
            </>
          )}
          {!w.preset && <button onClick={drop}>Remove this zone</button>}
        </>
      )
    }
  }
}

function Slider(p: { label: string; unit: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }) {
  return (
    <label className="field">
      <span className="field-label">
        {p.label}
        <output>
          {p.value}
          {p.unit && (p.unit === '%' || p.unit === '×' ? p.unit : ` ${p.unit}`)}
        </output>
      </span>
      <input type="range" min={p.min} max={p.max} step={p.step} value={p.value} onChange={(e) => p.onChange(Number(e.target.value))} />
    </label>
  )
}
