import type { RoadFeatures, VehicleType } from '../lib/types'
import { CONGESTION, vehicleColor, type Theme } from '../lib/palette'
import type { ColorMode } from './MapView'

interface Props {
  theme: Theme
  types: VehicleType[]
  colorMode: ColorMode
  onColorMode: (m: ColorMode) => void
  showCongestion: boolean
  onShowCongestion: (v: boolean) => void
  features: RoadFeatures
}

const FEATURE_KEYS = [
  { key: 'bus_stops', label: 'Bus stop', cls: 'fk-bus' },
  { key: 'stands', label: 'Rickshaw / CNG stand', cls: 'fk-stand' },
  { key: 'crossings', label: 'Crossing (yellow: people crossing)', cls: 'fk-crossing' },
  { key: 'hot_zones', label: 'Hot zone', cls: 'fk-hot' },
  { key: 'water', label: 'Waterlogging (filled: flooded)', cls: 'fk-water' },
] as const

export function Legend(p: Props) {
  return (
    <div className="legend">
      <div className="legend-group">
        <label className="check tight">
          <input type="checkbox" checked={p.showCongestion} onChange={(e) => p.onShowCongestion(e.target.checked)} />
          <span className="legend-title">Road congestion</span>
        </label>
        {p.showCongestion && (
          <ul>
            {CONGESTION.map((c) => (
              <li key={c.label}>
                <span className="line-key" style={{ background: c.color }} aria-hidden />
                {c.label}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="legend-group">
        <div className="legend-title">
          Vehicles by{' '}
          <span className="seg" role="radiogroup" aria-label="Colour vehicles by">
            {(['type', 'speed'] as const).map((m) => (
              <button key={m} role="radio" aria-checked={p.colorMode === m} className={p.colorMode === m ? 'on' : ''} onClick={() => p.onColorMode(m)}>
                {m}
              </button>
            ))}
          </span>
        </div>
        <ul>
          {p.colorMode === 'type'
            ? p.types.map((t) => (
                <li key={t.id}>
                  <span className="dot-key" style={{ background: vehicleColor(p.theme, t.id) }} aria-hidden />
                  {t.label}
                </li>
              ))
            : CONGESTION.map((c) => (
                <li key={c.label}>
                  <span className="dot-key" style={{ background: c.color }} aria-hidden />
                  {c.label}
                </li>
              ))}
        </ul>
      </div>
      {FEATURE_KEYS.some((k) => p.features[k.key].length) && (
        <div className="legend-group">
          <div className="legend-title">On the map</div>
          <ul>
            {FEATURE_KEYS.filter((k) => p.features[k.key].length).map((k) => (
              <li key={k.key}>
                <span className={`feature-key ${k.cls}`} aria-hidden />
                {k.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
