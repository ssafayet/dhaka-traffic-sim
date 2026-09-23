import type { Stats } from '../lib/types'
import { LineChart } from './LineChart'
import { formatClock } from '../lib/format'

export interface HistoryPoint {
  t: number // clock seconds
  running: number
  speed: number
}

function minutes(v: number | null | undefined) {
  if (v === null || v === undefined) return '—'
  return v < 1 ? `${Math.round(v * 60)} s` : `${v.toFixed(1)} min`
}

export function StatsPanel({ stats, history }: { stats: Stats | null; history: HistoryPoint[] }) {
  const s = stats
  const stoppedPct = s && s.running ? Math.round((s.stopped / s.running) * 100) : 0
  const tiles: { label: string; value: string; hint: string }[] = [
    { label: 'On the road', value: s ? s.running.toLocaleString() : '—', hint: 'Vehicles currently driving in the area' },
    { label: 'Average speed', value: s ? `${s.avg_speed_kmh} km/h` : '—', hint: 'Mean speed of all vehicles on the road' },
    { label: 'Standing still', value: s ? `${stoppedPct}%` : '—', hint: 'Share of vehicles moving slower than 2 km/h' },
    { label: 'Delay so far', value: minutes(s?.on_road_delay_min), hint: 'Average time lost vs. free flow, for vehicles still on the road' },
    { label: 'Trip time', value: minutes(s?.avg_trip_min), hint: 'Average duration of trips finished in the last 10 minutes' },
    { label: 'Throughput', value: s ? `${s.throughput_per_hour.toLocaleString()}/h` : '—', hint: 'Trips completed per hour (last 10 minutes)' },
    { label: 'Waiting to enter', value: s ? s.waiting.toLocaleString() : '—', hint: 'Vehicles that cannot get onto the network because the entry road is full' },
    { label: 'Removed as stuck', value: s ? s.teleports.toLocaleString() : '—', hint: 'Vehicles stuck longer than the limit and taken off the road (gridlock indicator)' },
  ]
  return (
    <section className="stats" aria-label="Simulation results">
      <div className="tiles">
        {tiles.map((t) => (
          <div className="tile" key={t.label} title={t.hint}>
            <div className="tile-label">{t.label}</div>
            <div className="tile-value">{t.value}</div>
          </div>
        ))}
      </div>
      <div className="charts">
        <LineChart
          title="Vehicles on the road"
          unit=""
          points={history.map((p) => ({ t: p.t, v: p.running }))}
          formatX={formatClock}
          color="var(--series-1)"
        />
        <LineChart
          title="Average speed"
          unit="km/h"
          points={history.map((p) => ({ t: p.t, v: p.speed }))}
          formatX={formatClock}
          color="var(--series-1)"
        />
      </div>
    </section>
  )
}
