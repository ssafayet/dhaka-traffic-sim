import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, LayoutEditError } from './lib/api'
import { SimClient, type SimEvent } from './lib/simClient'
import { VehicleStore } from './lib/vehicleStore'
import type {
  Area,
  Closure,
  Demand,
  Frame,
  LayoutEdits,
  LayoutError,
  Preset,
  Region,
  RoadFeature,
  RoadFeatures,
  Selection,
  SignalPlan,
  SimOptions,
  SimState,
  Stats,
  Tool,
  Topology,
  VehicleType,
} from './lib/types'
import type { Theme } from './lib/palette'
import { MapView, type ColorMode, type EdgeLevels, type LiveMarks, type SignalPhases } from './components/MapView'
import { MapTools } from './components/MapTools'
import { WeatherPanel } from './components/WeatherPanel'
import { EMPTY_FEATURES, create, defaultFeatures, find } from './lib/features'
import { ControlPanel } from './components/ControlPanel'
import { StatsPanel, type HistoryPoint } from './components/StatsPanel'
import { formatClock } from './lib/format'
import { Legend } from './components/Legend'
import { Inspector } from './components/Inspector'
import { ScenarioPanel } from './components/ScenarioPanel'
import { EMPTY_LAYOUT, pruneSignalPlans, remapClosures, sameLayout } from './lib/scenario'

const WARMUP_SECONDS = 300
const HISTORY_EVERY = 10 // simulated seconds between chart samples
const HISTORY_MAX = 720
const TOAST_MS = 5000
const LOCATE_SPAN = 0.002 // degrees around an edit the map zooms to (~200 m)

/** Preset volumes and through shares are for the region's reference area; scale them to the area. */
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
  const [regions, setRegions] = useState<Region[]>([])
  const [presetId, setPresetId] = useState<string | null>(null)
  const [demand, setDemand] = useState<Demand>({ volume: 6000, mix: {}, through_share: 0.6 })
  const [options, setOptions] = useState<SimOptions>({
    sublane: true,
    teleport_after: 300,
    start_hour: 8,
    rickshaws_on_main_roads: true,
    reroute_every: 0,
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
  // Road edits. Closures and signal plans apply live; layout edits (U-turns,
  // new signals) need a rebuilt network ("applied") and a restart.
  const [topology, setTopology] = useState<Topology | null>(null)
  const [editMode, setEditMode] = useState(false)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [closures, setClosures] = useState<Closure[]>([])
  const [signalPlans, setSignalPlans] = useState<Record<string, SignalPlan>>({})
  const [layout, setLayout] = useState<LayoutEdits>(EMPTY_LAYOUT)
  const [applied, setApplied] = useState<{ edits: LayoutEdits; variant: string | null }>({ edits: EMPTY_LAYOUT, variant: null })
  const [building, setBuilding] = useState(false)
  const [layoutErrors, setLayoutErrors] = useState<LayoutError[]>([])
  const [toast, setToast] = useState<{ text: string; tone: 'info' | 'warn'; n: number } | null>(null)
  // Road features and weather (live), starting from the area's defaults.
  const [features, setFeatures] = useState<RoadFeatures>(EMPTY_FEATURES)
  const [featureDefaults, setFeatureDefaults] = useState<RoadFeatures>(EMPTY_FEATURES)
  const [waterAbout, setWaterAbout] = useState('')
  const [tool, setTool] = useState<Tool>('select')

  const store = useRef(new VehicleStore()).current
  const edges = useRef<EdgeLevels>({ levels: {}, version: 0 }).current
  const signalPhases = useRef<SignalPhases>({ phases: {}, version: 0 }).current
  const live = useRef<LiveMarks>({ crossing: new Set(), broken: [], version: 0 }).current
  const sentClosures = useRef('[]') // closures the running sim has
  const sentFeatures = useRef('') // features the running sim has
  const signalTimers = useRef<Record<string, number>>({})
  const areaRef = useRef(areaId)
  areaRef.current = areaId
  const latestFrame = useRef<Frame | null>(null)
  const historyRef = useRef<HistoryPoint[]>([])
  const client = useRef<SimClient | null>(null)

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
        if (f.signals) {
          signalPhases.phases = f.signals
          signalPhases.version++
        }
        const crossing = f.crossing ?? []
        if (crossing.length !== live.crossing.size || crossing.some((c) => !live.crossing.has(c)) || (f.broken?.length ?? 0) + live.broken.length) {
          live.crossing = new Set(crossing)
          live.broken = f.broken ?? []
          live.version++
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
      onEvent: (e: SimEvent) => {
        if (e.type === 'warning') setToast((t) => ({ text: e.message, tone: 'warn', n: (t?.n ?? 0) + 1 }))
        else if (e.type === 'closures' && (e.rerouted || e.stranded)) {
          const parts = [`${e.rerouted.toLocaleString()} vehicle${e.rerouted === 1 ? '' : 's'} rerouted`]
          if (e.stranded) parts.push(`${e.stranded.toLocaleString()} with no other way (they queue at the closure)`)
          setToast((t) => ({ text: parts.join(' · '), tone: e.stranded ? 'warn' : 'info', n: (t?.n ?? 0) + 1 }))
        } else if (e.type === 'features' && e.stranded) {
          const text = `${e.stranded.toLocaleString()} vehicle${e.stranded === 1 ? ' has' : 's have'} no way through the water (they wait at its edge)`
          setToast((t) => ({ text, tone: 'warn', n: (t?.n ?? 0) + 1 }))
        }
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
  }, [store, edges, signalPhases, live])

  // Initial data.
  useEffect(() => {
    Promise.all([api.areas(), api.vehicleTypes(), api.regions()])
      .then(([a, t, r]) => {
        setAreas(a.areas)
        const initial = a.areas.find((x) => x.id === a.default) ?? a.areas[0]
        setAreaId(initial?.id ?? '')
        setTypes(t)
        setRegions(r.regions)
        const region = r.regions.find((x) => x.id === initial?.region)
        const def = region?.presets.find((x) => x.id === region.default_preset) ?? region?.presets[0]
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

  // A new area starts with the roads as mapped.
  useEffect(() => {
    if (!areaId) return
    setRoads([])
    setTopology(null)
    api.network(areaId).then(setRoads).catch(() => setLoadError(`Could not load roads for ${areaId}.`))
    api.topology(areaId).then(setTopology).catch(() => setTopology(null))
    api
      .featureDefaults(areaId)
      .then((d) => {
        if (areaRef.current !== areaId) return
        const f = defaultFeatures(d)
        setFeatureDefaults(f)
        // Keep the weather and behaviour settings; the places belong to the area.
        setFeatures((cur) => ({ ...f, weather: cur.weather, breakdowns: cur.breakdowns, elasticity: cur.elasticity }))
        setWaterAbout(d.water_about)
      })
      .catch(() => setFeatureDefaults(EMPTY_FEATURES))
  }, [areaId])

  const start = useCallback(
    (over?: { variant: string | null; closures: Closure[]; signals: Record<string, SignalPlan> }) => {
      if (!client.current || !areaId) return
      store.clear()
      edges.levels = {}
      edges.version++
      signalPhases.phases = {}
      signalPhases.version++
      historyRef.current = []
      latestFrame.current = null
      setHistory([])
      setStats(null)
      setWarmup(0)
      setState('starting')
      setRunConfig(restartKey(areaId, options))
      live.crossing = new Set()
      live.broken = []
      live.version++
      const scenario = over ?? { variant: applied.variant, closures, signals: signalPlans }
      sentClosures.current = JSON.stringify(scenario.closures)
      sentFeatures.current = JSON.stringify(features)
      client.current.start({ area: areaId, demand, options, speed, warmup: WARMUP_SECONDS, features, ...scenario })
    },
    [areaId, demand, options, speed, store, edges, signalPhases, live, applied.variant, closures, signalPlans, features],
  )

  // Push demand changes to the running sim (debounced while dragging).
  useEffect(() => {
    if (state !== 'running' && state !== 'paused') return
    const t = setTimeout(() => client.current?.setDemand(demand), 120)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demand])

  const active = state === 'running' || state === 'paused' || state === 'starting'

  // Closures apply to the running simulation as they change.
  useEffect(() => {
    if (!active) return
    const json = JSON.stringify(closures)
    if (json === sentClosures.current) return
    const t = setTimeout(() => {
      sentClosures.current = json
      client.current?.setClosures(closures)
    }, 150)
    return () => clearTimeout(t)
  }, [closures, active])

  // So do road features and weather; sliders send once they settle.
  useEffect(() => {
    if (!active) return
    const json = JSON.stringify(features)
    if (json === sentFeatures.current) return
    const t = setTimeout(() => {
      sentFeatures.current = json
      client.current?.setFeatures(features)
    }, 300)
    return () => clearTimeout(t)
  }, [features, active])

  const onPlace = (kind: Exclude<Tool, 'select'>, lon: number, lat: number) => {
    const [next, id] = create(features, kind, lon, lat)
    setFeatures(next)
    setSelection({ kind, id })
    setTool('select')
  }

  const onSignalPlan = (id: string, plan: SignalPlan | null) => {
    setSignalPlans((all) => {
      const next = { ...all }
      if (plan) next[id] = plan
      else delete next[id]
      return next
    })
    if (!active) return
    // Typing a duration sends one change, not one per keystroke.
    clearTimeout(signalTimers.current[id])
    signalTimers.current[id] = window.setTimeout(() => client.current?.setSignal(id, plan), 250)
  }

  const applyLayout = async () => {
    const area = areaId
    const draft = layout
    setBuilding(true)
    setLayoutErrors([])
    try {
      const { variant } = await api.buildLayout(area, draft)
      const [nextRoads, topo] = await Promise.all([api.network(area, variant), api.topology(area, variant)])
      if (areaRef.current !== area) return
      const nextClosures = remapClosures(closures, roads, nextRoads)
      const nextPlans = pruneSignalPlans(signalPlans, topo)
      setRoads(nextRoads)
      setTopology(topo)
      setApplied({ edits: draft, variant })
      setClosures(nextClosures)
      setSignalPlans(nextPlans)
      setSelection(null)
      // Before the first start the new layout just waits for Start.
      if (active) start({ variant, closures: nextClosures, signals: nextPlans })
    } catch (e) {
      setLayoutErrors(
        e instanceof LayoutEditError ? e.errors : [{ kind: 'other', index: -1, message: 'Could not reach the server to build the road layout.' }],
      )
    } finally {
      setBuilding(false)
    }
  }

  const clearAll = () => {
    setFeatures((cur) => ({ ...featureDefaults, weather: cur.weather, breakdowns: cur.breakdowns, elasticity: cur.elasticity }))
    setClosures([])
    for (const id of Object.keys(signalPlans)) onSignalPlan(id, null)
    setLayout(EMPTY_LAYOUT)
    setLayoutErrors([])
    setSelection(null)
  }

  const resetScenario = () => {
    setClosures([])
    setSignalPlans({})
    setLayout(EMPTY_LAYOUT)
    setApplied({ edits: EMPTY_LAYOUT, variant: null })
    setLayoutErrors([])
    setSelection(null)
  }

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
    resetScenario()
    // Presets belong to a region; in another region, fall back to its default.
    const toRegion = regions.find((r) => r.id === to?.region)
    const sameRegion = to?.region === from?.region
    const preset = sameRegion
      ? toRegion?.presets.find((x) => x.id === presetId)
      : (toRegion?.presets.find((x) => x.id === toRegion.default_preset) ?? toRegion?.presets[0])
    if (preset) {
      setPresetId(preset.id)
      if (!sameRegion) setOptions((o) => ({ ...o, start_hour: preset.start_hour }))
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
  const region = regions.find((r) => r.id === area?.region)
  const presets = region?.presets ?? []
  const needsRestart = active && runConfig !== restartKey(areaId, options)

  const roadsById = useMemo(() => new Map(roads.map((r) => [r.properties.id, r])), [roads])
  const junctions = useMemo(() => new Map((topology?.junctions ?? []).map((j) => [j.id, j])), [topology])
  const signals = useMemo(() => new Map((topology?.signals ?? []).map((s) => [s.id, s])), [topology])
  const layoutDirty = !sameLayout(layout, applied.edits)

  const onEditMode = (on: boolean) => {
    setEditMode(on)
    setTool('select')
    if (!on) setSelection(null)
  }
  /** Select an edit from the list and bring it into view. */
  const locate = (sel: Selection) => {
    setEditMode(true)
    setSelection(sel)
    const at =
      sel.kind === 'road'
        ? [sel.lon, sel.lat]
        : sel.kind === 'uturn'
          ? [layout.uturns[sel.index]?.lon, layout.uturns[sel.index]?.lat]
          : sel.kind === 'signal'
            ? [signals.get(sel.id)?.lon, signals.get(sel.id)?.lat]
            : sel.kind === 'junction'
              ? [junctions.get(sel.id)?.lon, junctions.get(sel.id)?.lat]
              : [find(features, sel.kind, sel.id)?.lon, find(features, sel.kind, sel.id)?.lat]
    const [lon, lat] = at
    if (lon === undefined || lat === undefined) return
    const d = LOCATE_SPAN
    setFocus((f) => ({ bbox: [lon - d, lat - d, lon + d, lat + d], n: (f?.n ?? 0) + 1 }))
  }

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), TOAST_MS)
    return () => clearTimeout(t)
  }, [toast])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setSelection(null)
      setTool('select')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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
        onStart={() => start()}
        onPauseToggle={onPauseToggle}
      >
        <WeatherPanel
          area={area}
          features={features}
          onFeatures={setFeatures}
          stats={stats}
          waterAbout={waterAbout}
          onEdit={() => onEditMode(true)}
        />
        <ScenarioPanel
          editMode={editMode}
          onEditMode={onEditMode}
          roadsById={roadsById}
          junctions={junctions}
          signals={signals}
          closures={closures}
          onClosures={setClosures}
          signalPlans={signalPlans}
          onSignalPlan={onSignalPlan}
          layout={layout}
          onLayout={setLayout}
          layoutDirty={layoutDirty}
          building={building}
          layoutErrors={layoutErrors}
          onApplyLayout={applyLayout}
          running={active}
          onSelect={locate}
          onClearAll={clearAll}
          features={features}
          featureDefaults={featureDefaults}
          onFeatures={setFeatures}
        />
      </ControlPanel>
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
            editMode={editMode}
            closures={closures}
            layout={layout}
            appliedLayout={applied.edits}
            topology={topology}
            signalPhases={signalPhases}
            signalPlans={signalPlans}
            selection={selection}
            onPick={setSelection}
            features={features}
            tool={tool}
            onPlace={onPlace}
            live={live}
          />
          {editMode && <MapTools tool={tool} onTool={setTool} />}
          {editMode && selection && (
            <Inspector
              selection={selection}
              roadsById={roadsById}
              twins={topology?.twins ?? {}}
              junctions={junctions}
              signals={signals}
              closures={closures}
              onClosures={setClosures}
              layout={layout}
              appliedLayout={applied.edits}
              onLayout={setLayout}
              signalPlans={signalPlans}
              onSignalPlan={onSignalPlan}
              signalPhase={(id) => signalPhases.phases[id]}
              onSelect={setSelection}
              features={features}
              onFeatures={setFeatures}
              signalNotes={region?.signal_notes}
            />
          )}
          {toast && (
            <div key={toast.n} className={`toast ${toast.tone}`} role="status">
              {toast.text}
            </div>
          )}
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
            features={features}
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
  return JSON.stringify([areaId, o.sublane, o.teleport_after, o.rickshaws_on_main_roads, o.reroute_every])
}

function stateLabel(s: SimState, warmup: number | null) {
  if (warmup !== null && (s === 'running' || s === 'starting')) return 'warming up'
  return { idle: 'idle', connecting: 'connecting', starting: 'starting', running: 'running', paused: 'paused', stopped: 'stopped', error: 'error' }[s]
}
