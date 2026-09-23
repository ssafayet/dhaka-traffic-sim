import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './lib/api'
import { SimClient } from './lib/simClient'
import { VehicleStore } from './lib/vehicleStore'
import type { Area, Demand, Frame, Preset, RoadFeature, SimOptions, SimState, Stats, VehicleType } from './lib/types'
import type { Theme } from './lib/palette'
import { MapView, type ColorMode, type EdgeLevels } from './components/MapView'
import { ControlPanel } from './components/ControlPanel'
import { StatsPanel, type HistoryPoint } from './components/StatsPanel'
import { formatClock } from './lib/format'
import { Legend } from './components/Legend'

const WARMUP_SECONDS = 300
const HISTORY_EVERY = 10 // simulated seconds between chart samples
const HISTORY_MAX = 720

/** Preset volumes and through shares are for Farmgate; scale them to the area. */
function presetDemand(p: Preset, area: Area | undefined): Demand {
  const volume = p.volume * (area?.demand_scale ?? 1)
  const step = volume > 20000 ? 1000 : 50
  return {
    volume: Math.round(volume / step) * step,
    mix: p.mix,
    through_share: Math.min(1, Math.round(p.through_share * (area?.through_scale ?? 1) * 100) / 100),
  }
}

function useTheme(): Theme {
  const q = '(prefers-color-scheme: dark)'
  const [theme, setTheme] = useState<Theme>(() => (matchMedia(q).matches ? 'dark' : 'light'))
  useEffect(() => {
    const m = matchMedia(q)
    const on = () => setTheme(m.matches ? 'dark' : 'light')
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return theme
}

export default function App() {
  const theme = useTheme()
  const [areas, setAreas] = useState<Area[]>([])
  const [areaId, setAreaId] = useState('')
  const [roads, setRoads] = useState<RoadFeature[]>([])
  const [types, setTypes] = useState<VehicleType[]>([])
  const [presets, setPresets] = useState<Preset[]>([])
  const [presetId, setPresetId] = useState<string | null>(null)
  const [demand, setDemand] = useState<Demand>({ volume: 6000, mix: {}, through_share: 0.6 })
  const [options, setOptions] = useState<SimOptions>({
    sublane: true,
    teleport_after: 300,
    start_hour: 8,
    rickshaws_on_main_roads: true,
  })
  const [focus, setFocus] = useState<{ bbox: Area['bbox']; n: number } | null>(null)
  const [runConfig, setRunConfig] = useState<string>('') // options/area the running sim was started with
  const [speed, setSpeed] = useState(1)
  const [state, setState] = useState<SimState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [warmup, setWarmup] = useState<number | null>(null)
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const [colorMode, setColorMode] = useState<ColorMode>('type')
  const [showCongestion, setShowCongestion] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const store = useRef(new VehicleStore()).current
  const edges = useRef<EdgeLevels>({ levels: {}, version: 0 }).current
  const latestFrame = useRef<Frame | null>(null)
  const historyRef = useRef<HistoryPoint[]>([])
  const client = useRef<SimClient | null>(null)
  const autoStarted = useRef(false)

  // Frames arrive up to 20×/s; the map reads the store directly, React state
  // (stats, charts) refreshes at 4 Hz.
  useEffect(() => {
    const c = new SimClient({
      onFrame: (f) => {
        store.ingest(f, performance.now())
        if (f.edges) {
          edges.levels = f.edges
          edges.version++
        }
        latestFrame.current = f
        const h = historyRef.current
        const last = h[h.length - 1]
        if (!last || f.stats.clock - last.t >= HISTORY_EVERY) {
          h.push({ t: f.stats.clock, running: f.stats.running, speed: f.stats.avg_speed_kmh })
          if (h.length > HISTORY_MAX) h.shift()
        }
      },
      onState: (s, message) => {
        if (s === 'error') setError(message ?? 'Unknown error')
        else if (s === 'starting' || s === 'running') setError(null)
        setState((prev) => (s === 'stopped' && prev === 'starting' ? prev : s))
      },
    })
    client.current = c
    const timer = setInterval(() => {
      const f = latestFrame.current
      if (!f) return
      setStats(f.stats)
      setWarmup(f.warming_up ? (f.warmup_progress ?? 0) : null)
      setHistory([...historyRef.current])
      setState((prev) => (prev === 'starting' ? 'running' : prev))
    }, 250)
    return () => {
      clearInterval(timer)
      c.close()
    }
  }, [store, edges])

  // Initial data.
  useEffect(() => {
    Promise.all([api.areas(), api.vehicleTypes(), api.presets()])
      .then(([a, t, p]) => {
        setAreas(a.areas)
        const initial = a.areas.find((x) => x.id === a.default) ?? a.areas[0]
        setAreaId(initial?.id ?? '')
        setTypes(t)
        setPresets(p.presets)
        const def = p.presets.find((x) => x.id === p.default) ?? p.presets[0]
        if (def) {
          setPresetId(def.id)
          setDemand(presetDemand(def, initial))
          setOptions((o) => ({ ...o, start_hour: def.start_hour }))
        } else {
          setDemand((d) => ({ ...d, mix: Object.fromEntries(t.map((v) => [v.id, v.default_share])) }))
        }
        if (!a.areas.length) setLoadError('No prepared areas. Run `uv run traffic-sim-prepare farmgate` in backend/.')
      })
      .catch(() => setLoadError('Cannot reach the simulation server. Is the backend running on port 8000?'))
  }, [])

  useEffect(() => {
    if (!areaId) return
    setRoads([])
    api.network(areaId).then(setRoads).catch(() => setLoadError(`Could not load roads for ${areaId}.`))
  }, [areaId])

  const start = useCallback(() => {
    if (!client.current || !areaId) return
    store.clear()
    edges.levels = {}
    edges.version++
    historyRef.current = []
    latestFrame.current = null
    setHistory([])
    setStats(null)
    setWarmup(0)
    setState('starting')
    setRunConfig(restartKey(areaId, options))
    client.current.start(areaId, demand, options, speed, WARMUP_SECONDS)
  }, [areaId, demand, options, speed, store, edges])

  // Start automatically once everything has loaded.
  useEffect(() => {
    if (!autoStarted.current && areaId && types.length && roads.length && Object.keys(demand.mix).length) {
      autoStarted.current = true
      start()
    }
  }, [areaId, types, roads, demand, start])

  // Push demand changes to the running sim (debounced while dragging).
  useEffect(() => {
    if (state !== 'running' && state !== 'paused') return
    const t = setTimeout(() => client.current?.setDemand(demand), 120)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demand])

  const onDemand = (d: Demand) => {
    setDemand(d)
    setPresetId(null)
  }
  const onPreset = (p: Preset) => {
    setPresetId(p.id)
    setDemand(presetDemand(p, areas.find((a) => a.id === areaId)))
    setOptions((o) => ({ ...o, start_hour: p.start_hour }))
  }
  const onArea = (id: string) => {
    const from = areas.find((a) => a.id === areaId)
    const to = areas.find((a) => a.id === id)
    setAreaId(id)
    setFocus(null)
    const preset = presets.find((x) => x.id === presetId)
    if (preset) {
      setDemand(presetDemand(preset, to))
    } else {
      // Custom demand: keep the same intensity relative to the area's size.
      const ratio = (to?.demand_scale ?? 1) / (from?.demand_scale ?? 1)
      const through = (to?.through_scale ?? 1) / (from?.through_scale ?? 1)
      setDemand((d) => ({
        ...d,
        volume: Math.round((d.volume * ratio) / 50) * 50,
        through_share: Math.min(1, d.through_share * through),
      }))
    }
    // Lane-free movement is several times slower; off by default for the city.
    if (to?.kind === 'city' && from?.kind !== 'city') setOptions((o) => ({ ...o, sublane: false }))
  }
  const onSpeed = (s: number) => {
    setSpeed(s)
    client.current?.setSpeed(s)
  }
  const onPauseToggle = () => {
    if (state === 'paused') client.current?.play()
    else client.current?.pause()
  }

  const area = areas.find((a) => a.id === areaId) ?? null
  const active = state === 'running' || state === 'paused' || state === 'starting'
  const needsRestart = active && runConfig !== restartKey(areaId, options)

  return (
    <div className="app">
      <ControlPanel
        theme={theme}
        areas={areas}
        areaId={areaId}
        onArea={onArea}
        onJump={(bbox) => setFocus((f) => ({ bbox, n: (f?.n ?? 0) + 1 }))}
        presets={presets}
        presetId={presetId}
        onPreset={onPreset}
        types={types}
        demand={demand}
        onDemand={onDemand}
        options={options}
        onOptions={setOptions}
        needsRestart={needsRestart}
        state={state}
        warmup={warmup}
        speed={speed}
        onSpeed={onSpeed}
        onStart={start}
        onPauseToggle={onPauseToggle}
      />
      <main className="stage">
        <div className="map-wrap">
          <MapView
            theme={theme}
            area={area}
            focus={focus}
            rickshawsOnMainRoads={options.rickshaws_on_main_roads}
            roads={roads}
            types={types}
            store={store}
            edges={edges}
            colorMode={colorMode}
            showCongestion={showCongestion}
          />
          <div className="clock" aria-live="off">
            <span className={`state-dot ${state}`} aria-hidden />
            {stats ? formatClock(stats.clock) : '—'}
            <span className="muted"> · {stateLabel(state, warmup)}</span>
          </div>
          <Legend
            theme={theme}
            types={types}
            colorMode={colorMode}
            onColorMode={setColorMode}
            showCongestion={showCongestion}
            onShowCongestion={setShowCongestion}
          />
          {(loadError || error) && (
            <div className="banner" role="alert">
              {loadError ?? error}
            </div>
          )}
        </div>
        <StatsPanel stats={stats} history={history} />
      </main>
    </div>
  )
}

/** Options that only take effect when the simulation restarts. */
function restartKey(areaId: string, o: SimOptions) {
  return JSON.stringify([areaId, o.sublane, o.teleport_after, o.rickshaws_on_main_roads])
}

function stateLabel(s: SimState, warmup: number | null) {
  if (warmup !== null && (s === 'running' || s === 'starting')) return 'warming up'
  return { idle: 'idle', connecting: 'connecting', starting: 'starting', running: 'running', paused: 'paused', stopped: 'stopped', error: 'error' }[s]
}
