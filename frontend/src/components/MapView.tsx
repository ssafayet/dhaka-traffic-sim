import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre 6 ships its worker as an ES module; let Vite bundle it.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { IconLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import type {
  Area,
  Closure,
  FeatureKind,
  JunctionInfo,
  LayoutEdits,
  RoadFeature,
  Selection,
  SignalInfo,
  SignalPlan,
  RoadFeatures,
  Tool,
  Topology,
  VehicleType,
} from '../lib/types'
import { markerAtlas, type MarkerIcon } from '../lib/featureIcons'
import { DEPTH, FEATURE_LABEL, STAND_LABEL, flooded } from '../lib/features'
import { VehicleStore } from '../lib/vehicleStore'
import { buildIconAtlas, type IconAtlas } from '../lib/vehicleIcons'
import {
  CONGESTION,
  EDIT_RGBA,
  MAP_STYLE,
  ROAD_IDLE,
  congestionIndex,
  congestionRGBA,
  vehicleRGBA,
  type Theme,
} from '../lib/palette'
import { approachLight, defaultPlan, isUTurnNode, signalName } from '../lib/scenario'

export type ColorMode = 'type' | 'speed'

export interface EdgeLevels {
  levels: Record<string, number>
  version: number
}

/** Current phase of each signal, from the latest frame. */
export interface SignalPhases {
  phases: Record<string, number>
  version: number
}

/** Live state of road features, from the latest frame. */
export interface LiveMarks {
  crossing: Set<string>
  broken: [number, number][]
  version: number
}

interface Props {
  theme: Theme
  area: Area | null
  /** A bounding box to zoom to inside the area; `n` changes on every request. */
  focus: { bbox: Area['bbox']; n: number } | null
  rickshawsOnMainRoads: boolean
  roads: RoadFeature[]
  types: VehicleType[]
  store: VehicleStore
  edges: EdgeLevels
  colorMode: ColorMode
  showCongestion: boolean
  /** Road editor: roads, junctions and signals are clickable. */
  editMode: boolean
  closures: Closure[]
  /** The draft layout; U-turns not in the applied one show as pending. */
  layout: LayoutEdits
  appliedLayout: LayoutEdits
  topology: Topology | null
  signalPhases: SignalPhases
  signalPlans: Record<string, SignalPlan>
  selection: Selection | null
  onPick: (s: Selection | null) => void
  features: RoadFeatures
  /** In edit mode: what a click on the map places. */
  tool: Tool
  onPlace: (tool: Exclude<Tool, 'select'>, lon: number, lat: number) => void
  live: LiveMarks
}

maplibregl.setWorkerUrl(maplibreWorkerUrl)

// Below this zoom vehicles are dots; above it, to-scale silhouettes.
const DETAIL_ZOOM = 16.3
// Junctions are only clickable once individual streets are distinguishable.
const JUNCTION_ZOOM = 15
const SIGNAL_ZOOM = 13.5
const FEATURE_ZOOM = 14

interface PointMarker {
  kind: FeatureKind
  id: string
  position: Point
  icon: MarkerIcon
  label: string
}

interface ZoneMarker {
  kind: 'hot_zone' | 'water'
  id: string
  position: Point
  radius: number
  active: boolean
  label: string
}

const WATER: [number, number, number] = [47, 143, 209]
const HOT: [number, number, number] = [235, 104, 52]
const OFF: [number, number, number] = [137, 135, 129]

function featureLayers(f: RoadFeatures) {
  const points: PointMarker[] = [
    ...f.bus_stops.map((s) => ({ kind: 'bus_stop' as const, id: s.id, position: [s.lon, s.lat] as Point, icon: 'bus_stop' as const, label: s.name ? `Bus stop · ${s.name}` : 'Bus stop' })),
    ...f.stands.map((s) => ({ kind: 'stand' as const, id: s.id, position: [s.lon, s.lat] as Point, icon: `stand_${s.kind}` as MarkerIcon, label: `${STAND_LABEL[s.kind]} · ${s.parked} parked` })),
    ...f.crossings.map((c) => ({ kind: 'crossing' as const, id: c.id, position: [c.lon, c.lat] as Point, icon: 'crossing' as const, label: `People cross about every ${c.every} s` })),
  ]
  const wet = flooded(f)
  const zones: ZoneMarker[] = [
    ...f.water.map((w) => ({
      kind: 'water' as const, id: w.id, position: [w.lon, w.lat] as Point, radius: w.radius, active: w.enabled && wet,
      label: `${w.name || 'Waterlogging'} · ${DEPTH[w.depth].label.toLowerCase()}${w.enabled ? (wet ? ' · flooded now' : ' · floods with heavy rain') : ' · switched off'}`,
    })),
    ...f.hot_zones.map((z) => ({
      kind: 'hot_zone' as const, id: z.id, position: [z.lon, z.lat] as Point, radius: z.radius, active: true,
      label: `${z.name || 'Hot zone'} · ${z.trips}× trips`,
    })),
  ]
  return { points, zones, enabledWater: new Set(f.water.filter((w) => w.enabled).map((w) => w.id)) }
}

type Point = [number, number]

interface Marker {
  position: Point
  kind: 'closed' | 'lanes' | 'uturn' | 'uturn-pending'
  index?: number
}

function midpoint(coords: Point[]): Point {
  if (coords.length === 1) return coords[0]
  const i = Math.floor((coords.length - 1) / 2)
  const [a, b] = [coords[i], coords[i + 1]]
  return coords.length % 2 ? coords[i] : [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
}

/** One signal head per approach, at its stop line. */
interface SignalHead {
  signal: SignalInfo
  /** Link indices of the approach's movements. */
  links: number[]
  label: string
  position: [number, number]
}

const HEAD_SETBACK = 6 // m back from the stop line, over the approach road

function signalHeads(signals: SignalInfo[], byId: Map<string, RoadFeature>): SignalHead[] {
  const heads: SignalHead[] = []
  for (const s of signals) {
    const approaches = new Map<string, number[]>()
    s.links.forEach((l, i) => {
      if (l) approaches.set(l.from, [...(approaches.get(l.from) ?? []), i])
    })
    for (const [from, links] of approaches) {
      const link = s.links[links[0]]!
      const coords = byId.get(from)?.geometry.coordinates
      let position: [number, number] = [s.lon, s.lat]
      if (coords && coords.length >= 2) {
        const [x1, y1] = coords[coords.length - 2]
        const [x2, y2] = coords[coords.length - 1]
        const mx = (x1 - x2) * 111_320 * Math.cos((y2 * Math.PI) / 180)
        const my = (y1 - y2) * 111_320
        const t = Math.min(1, HEAD_SETBACK / (Math.hypot(mx, my) || 1))
        position = [x2 + (x1 - x2) * t, y2 + (y1 - y2) * t]
      }
      heads.push({ signal: s, links, label: `${link.name || 'Unnamed road'} from the ${link.approach}`, position })
    }
  }
  return heads
}

/** Map data derived from the edits, recomputed only when they change. */
function useEditLayers(p: Props) {
  const byId = useMemo(() => new Map(p.roads.map((r) => [r.properties.id, r])), [p.roads])
  const edits = useMemo(() => {
    const closed: RoadFeature[] = []
    const markers: Marker[] = []
    for (const c of p.closures) {
      const road = byId.get(c.edge)
      if (!road) continue
      if (!c.lanes) closed.push(road)
      markers.push({ position: midpoint(road.geometry.coordinates), kind: c.lanes ? 'lanes' : 'closed' })
    }
    for (const r of p.roads) {
      if (r.properties.uturn && r.properties.id.endsWith('.gap')) {
        markers.push({ position: midpoint(r.geometry.coordinates), kind: 'uturn' })
      }
    }
    const built = new Set(p.appliedLayout.uturns.map((u) => `${u.edge}|${u.lon}|${u.lat}`))
    p.layout.uturns.forEach((u, index) => {
      if (!built.has(`${u.edge}|${u.lon}|${u.lat}`)) markers.push({ position: [u.lon, u.lat], kind: 'uturn-pending', index })
    })
    const all = p.topology?.junctions ?? []
    const junctions = all.filter((j) => !j.signal && !isUTurnNode(j.id))
    // Turn rules cover a whole crossing; mark every junction of it.
    const ruled = new Set(p.layout.junction_turns.map((t) => t.junction))
    const pendingJunctions = new Set([
      ...p.layout.signals,
      ...p.layout.junction_uturns.map((j) => j.junction),
      ...all.filter((j) => j.group.some((g) => ruled.has(g))).map((j) => j.id),
    ])
    const selectedJunction = p.selection?.kind === 'junction' ? all.find((j) => j.id === (p.selection as { id: string }).id) : undefined
    const selectedGroup = new Set(selectedJunction ? all.filter((j) => j.group.some((g) => selectedJunction.group.includes(g))).map((j) => j.id) : [])
    const selected = p.selection?.kind === 'road' ? byId.get(p.selection.id) : undefined
    return { closed, markers, junctions, pendingJunctions, selectedGroup, selected: selected ? [selected] : [] }
  }, [byId, p.closures, p.roads, p.layout, p.appliedLayout, p.topology, p.selection])
  const heads = useMemo(() => signalHeads(p.topology?.signals ?? [], byId), [p.topology, byId])
  return { ...edits, heads }
}

export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const edits = useEditLayers(props)
  const feats = useMemo(() => featureLayers(props.features), [props.features])
  const latest = useRef({ ...props, edits, feats })
  useLayoutEffect(() => {
    latest.current = { ...props, edits, feats }
  })

  // Map + render loop, created once.
  useEffect(() => {
    const p = latest.current
    const map = new maplibregl.Map({
      container: container.current!,
      style: MAP_STYLE[p.theme],
      center: p.area?.center ?? [90.391, 23.75],
      zoom: 15,
      attributionControl: { compact: true },
    })
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __map: maplibregl.Map }).__map = map

    const overlay = new MapboxOverlay({
      interleaved: false,
      layers: [],
      // Up close roads are a few pixels wide; don't make people hit them exactly.
      pickingRadius: 5,
      getTooltip: (info: PickingInfo) => tooltip(preferred(info)),
      onClick: (info: PickingInfo) => {
        const cur = latest.current
        if (!cur.editMode) return
        if (cur.tool !== 'select') {
          if (info.coordinate) cur.onPlace(cur.tool, info.coordinate[0], info.coordinate[1])
          return
        }
        cur.onPick(pick(preferred(info)))
      },
      getCursor: ({ isDragging, isHovering }: { isDragging: boolean; isHovering: boolean }) => {
        const cur = latest.current
        if (isDragging) return 'grabbing'
        if (cur.editMode && cur.tool !== 'select') return 'crosshair'
        return isHovering && cur.editMode ? 'pointer' : 'grab'
      },
    })
    map.addControl(overlay)

    // Zones are big and sit under everything; the cursor is always "on" one.
    // Anything else near the cursor comes first.
    const ABOVE_ZONES = ['feature-points', 'edit-markers', 'broken', 'signals', 'junctions', 'roads']
    function preferred(info: PickingInfo): PickingInfo {
      if (info.layer?.id !== 'zones') return info
      return overlay.pickObject({ x: info.x, y: info.y, radius: 5, layerIds: ABOVE_ZONES }) ?? info
    }

    function pick(info: PickingInfo): Selection | null {
      const id = info.layer?.id
      if (id === 'roads' && info.object && info.coordinate) {
        const r = (info.object as RoadFeature).properties
        return { kind: 'road', id: r.id, lon: info.coordinate[0], lat: info.coordinate[1] }
      }
      if (id === 'junctions' && info.object) return { kind: 'junction', id: (info.object as JunctionInfo).id }
      if (id === 'signals' && info.object) return { kind: 'signal', id: (info.object as SignalHead).signal.id }
      if (id === 'edit-markers' && info.object) {
        const m = info.object as Marker
        if (m.kind === 'uturn-pending' && m.index !== undefined) return { kind: 'uturn', index: m.index }
      }
      if ((id === 'feature-points' || id === 'zones') && info.object) {
        const m = info.object as PointMarker | ZoneMarker
        return { kind: m.kind, id: m.id }
      }
      return null
    }

    let atlas: IconAtlas | null = null
    let atlasFor = ''
    let colors = new Uint8Array(0)
    let sizes = new Float32Array(0)
    let deckAngles = new Float32Array(0)
    let colorsKey = ''
    let raf = 0

    function tooltip(info: PickingInfo) {
      const cur = latest.current
      if (info.layer?.id === 'roads' && info.object) {
        const r = (info.object as RoadFeature).properties
        const level = cur.edges.levels[r.id]
        const state = level === undefined ? 'No traffic' : CONGESTION[congestionIndex(level)].label
        const rule = r.main ? `<br>Main road · rickshaws ${cur.rickshawsOnMainRoads ? 'allowed' : 'banned'}` : ''
        return {
          html: `<strong>${escapeHtml(r.name || 'Unnamed road')}</strong><br>${r.lanes} lane${r.lanes > 1 ? 's' : ''} · limit ${Math.round(r.speed * 3.6)} km/h${rule}<br>${state}${level === undefined ? '' : ` (${Math.round(level * 100)}% of free speed)`}`,
          className: 'map-tooltip',
        }
      }
      if ((info.layer?.id === 'feature-points' || info.layer?.id === 'zones') && info.object) {
        const m = info.object as PointMarker | ZoneMarker
        return { html: `<strong>${escapeHtml(FEATURE_LABEL[m.kind])}</strong><br>${escapeHtml(m.label)}`, className: 'map-tooltip' }
      }
      if (info.layer?.id === 'broken') return { html: 'Broken-down vehicle', className: 'map-tooltip' }
      if (info.layer?.id === 'signals' && info.object) {
        const head = info.object as SignalHead
        const s = head.signal
        const plan = cur.signalPlans[s.id]
        const mode = (plan ?? defaultPlan(s)).mode
        const phase = cur.signalPhases.phases[s.id]
        const off = plan ? 'switched off' : 'off, directed by traffic police'
        const status = mode === 'off' ? off : `${mode === 'fixed' ? 'fixed time' : 'actuated'}${phase === undefined ? '' : ` · phase ${phase + 1} of ${s.phases.length}`}`
        const light = mode !== 'off' && phase !== undefined ? `: ${approachLight(s.phases[phase]?.state ?? '', head.links)}` : ''
        return {
          html: `<strong>${escapeHtml(signalName(s))}</strong><br>Traffic signal · ${status}<br>${escapeHtml(head.label)}${light}`,
          className: 'map-tooltip',
        }
      }
      if (info.layer?.id === 'junctions' && info.object) {
        const j = info.object as JunctionInfo
        return {
          html: `<strong>${escapeHtml(j.names.join(' × ') || 'Junction')}</strong><br>No signal · U-turns on ${j.uturns} of ${j.approaches} approaches`,
          className: 'map-tooltip',
        }
      }
      if (info.layer?.id === 'edit-markers' && info.object) {
        const m = info.object as Marker
        const text = {
          closed: 'Road closed',
          lanes: 'Lanes closed',
          uturn: 'U-turn (median gap)',
          'uturn-pending': 'New U-turn · applies when you apply layout changes',
        }[m.kind]
        return { html: text, className: 'map-tooltip' }
      }
      if (info.layer?.id.startsWith('vehicles') && info.index >= 0 && info.index < cur.store.n) {
        const t = cur.types[cur.store.kind[info.index]]
        return {
          html: `<strong>${escapeHtml(t?.label ?? 'Vehicle')}</strong><br>${Math.round(cur.store.speed[info.index] * 3.6)} km/h`,
          className: 'map-tooltip',
        }
      }
      return null
    }

    const frame = () => {
      raf = requestAnimationFrame(frame)
      const cur = latest.current
      const { store, types, theme, colorMode } = cur
      if (!types.length) return
      store.sample(performance.now())
      const n = store.n

      const typesKey = types.map((t) => t.id).join()
      if (atlasFor !== typesKey) {
        atlas = buildIconAtlas(types)
        atlasFor = typesKey
      }
      // Per-vehicle colour and size only change when a new frame lands.
      const key = `${store.version}|${theme}|${colorMode}`
      if (key !== colorsKey) {
        colorsKey = key
        colors = new Uint8Array(4 * n)
        sizes = new Float32Array(n)
        const typeRGBA = types.map((t) => vehicleRGBA(theme, t.id))
        const vmax = types.map((t) => t.max_speed_kmh / 3.6)
        for (let i = 0; i < n; i++) {
          const k = store.kind[i]
          const c = colorMode === 'type' ? typeRGBA[k] : congestionRGBA(store.speed[i] / vmax[k])
          colors.set(c, 4 * i)
          colors[4 * i + 3] = 255
          sizes[i] = types[k].length
        }
      }
      if (deckAngles.length !== n) deckAngles = new Float32Array(n)
      // SUMO: degrees clockwise from north. deck.gl: counter-clockwise.
      for (let i = 0; i < n; i++) deckAngles[i] = -store.angles[i]

      const zoom = map.getZoom()
      const detailed = zoom >= DETAIL_ZOOM
      const { edits, editMode } = cur
      const accent = EDIT_RGBA.accent[theme]
      const binary = { length: n, attributes: { getPosition: { value: store.positions, size: 2 } } }
      const selectedJunction = edits.selectedGroup
      const selectedSignal = cur.selection?.kind === 'signal' ? cur.selection.id : null
      const sel = cur.selection && 'id' in cur.selection ? cur.selection.id : null
      const { feats, live } = cur
      const layers = [
        // Under the roads, so congestion colours still show through.
        new ScatterplotLayer<ZoneMarker>({
          id: 'zones',
          data: feats.zones,
          visible: true,
          getPosition: (z) => z.position,
          getRadius: (z) => z.radius,
          radiusUnits: 'meters',
          filled: true,
          stroked: true,
          getFillColor: (z) => {
            if (z.kind === 'hot_zone') return [...HOT, 45]
            if (!feats.enabledWater.has(z.id)) return [...OFF, editMode ? 20 : 0]
            return [...WATER, z.active ? 90 : 30]
          },
          getLineColor: (z) => {
            if (z.id === sel) return accent
            if (z.kind === 'hot_zone') return [...HOT, 200]
            if (!feats.enabledWater.has(z.id)) return [...OFF, editMode ? 160 : 0]
            return [...WATER, z.active ? 230 : 140]
          },
          lineWidthUnits: 'pixels',
          getLineWidth: (z) => (z.id === sel ? 3 : 1.5),
          pickable: editMode,
          updateTriggers: { getFillColor: [feats, editMode], getLineColor: [feats, editMode, sel, theme], getLineWidth: sel },
        }),
        new PathLayer<RoadFeature>({
          id: 'roads',
          data: cur.roads,
          visible: cur.showCongestion || editMode,
          getPath: (f) => f.geometry.coordinates,
          getColor: (f) => {
            const level = cur.edges.levels[f.properties.id]
            return level === undefined ? ROAD_IDLE[theme] : congestionRGBA(level)
          },
          // Up close, draw roads as a thin band so the vehicles stay visible.
          getWidth: (f) => f.properties.lanes * 3.2,
          widthScale: detailed ? 0.35 : 1,
          widthUnits: 'meters',
          widthMinPixels: 2,
          opacity: detailed ? 0.6 : 1,
          jointRounded: true,
          pickable: true,
          autoHighlight: true,
          highlightColor: [255, 255, 255, 60],
          updateTriggers: { getColor: [cur.edges.version, theme] },
        }),
        new PathLayer<RoadFeature>({
          id: 'closed-roads',
          data: edits.closed,
          getPath: (f) => f.geometry.coordinates,
          getColor: EDIT_RGBA.closed[theme],
          getWidth: (f) => f.properties.lanes * 3.2,
          widthScale: detailed ? 0.5 : 1,
          widthUnits: 'meters',
          widthMinPixels: 3,
          jointRounded: true,
          updateTriggers: { getColor: theme },
        }),
        new PathLayer<RoadFeature>({
          id: 'selected-road',
          data: edits.selected,
          visible: editMode,
          getPath: (f) => f.geometry.coordinates,
          getColor: accent,
          getWidth: (f) => f.properties.lanes * 3.2 + 4,
          widthUnits: 'meters',
          widthMinPixels: 5,
          jointRounded: true,
          updateTriggers: { getColor: theme },
        }),
        new ScatterplotLayer({
          id: 'vehicles-dots',
          visible: !detailed,
          data: { ...binary, attributes: { ...binary.attributes, getFillColor: { value: colors, size: 4, normalized: true } } },
          getRadius: 2.5,
          radiusUnits: 'meters',
          radiusMinPixels: 1.6,
          radiusMaxPixels: 5,
          pickable: !editMode,
        }),
        new IconLayer({
          id: 'vehicles-icons',
          visible: detailed,
          data: {
            ...binary,
            attributes: {
              ...binary.attributes,
              getColor: { value: colors, size: 4, normalized: true },
              getAngle: { value: deckAngles, size: 1 },
              getSize: { value: sizes, size: 1 },
            },
          },
          iconAtlas: atlas!.atlas as unknown as string,
          iconMapping: atlas!.mapping,
          getIcon: (_: unknown, { index }: { index: number }) => types[store.kind[index]]?.id ?? types[0].id,
          sizeUnits: 'meters',
          sizeMinPixels: 3,
          billboard: false,
          pickable: !editMode,
          updateTriggers: { getIcon: store.version },
        }),
        new ScatterplotLayer<JunctionInfo>({
          id: 'junctions',
          data: edits.junctions,
          visible: editMode && zoom >= JUNCTION_ZOOM,
          getPosition: (j) => [j.lon, j.lat],
          getRadius: (j) => (selectedJunction.has(j.id) ? 9 : 6),
          radiusUnits: 'pixels',
          getFillColor: (j) => (edits.pendingJunctions.has(j.id) || selectedJunction.has(j.id) ? accent : EDIT_RGBA.junction[theme]),
          getLineColor: (j) => (selectedJunction.has(j.id) ? EDIT_RGBA.markerStroke[theme] : EDIT_RGBA.junctionStroke[theme]),
          stroked: true,
          lineWidthUnits: 'pixels',
          getLineWidth: 1.5,
          pickable: true,
          updateTriggers: {
            getFillColor: [theme, edits.pendingJunctions, selectedJunction],
            getLineColor: [theme, selectedJunction],
            getRadius: selectedJunction,
          },
        }),
        // Rings behind the selected signal's heads.
        new ScatterplotLayer<SignalHead>({
          id: 'signal-selected',
          data: edits.heads.filter((h) => h.signal.id === selectedSignal),
          visible: zoom >= SIGNAL_ZOOM,
          getPosition: (h) => h.position,
          getRadius: 9,
          radiusUnits: 'meters',
          radiusMinPixels: 11,
          radiusMaxPixels: 22,
          filled: false,
          stroked: true,
          getLineColor: accent,
          lineWidthUnits: 'pixels',
          getLineWidth: 3,
        }),
        // Each approach shows the light its drivers see.
        new IconLayer<SignalHead>({
          id: 'signals',
          data: edits.heads,
          visible: zoom >= SIGNAL_ZOOM,
          iconAtlas: markerAtlas().atlas as unknown as string,
          iconMapping: markerAtlas().mapping,
          getIcon: ({ signal: s, links }): MarkerIcon => {
            if ((cur.signalPlans[s.id] ?? defaultPlan(s)).mode === 'off') return 'signal_off'
            const phase = cur.signalPhases.phases[s.id]
            return phase === undefined ? 'signal_off' : `signal_${approachLight(s.phases[phase]?.state ?? '', links)}`
          },
          getPosition: (h) => h.position,
          getSize: 14,
          sizeUnits: 'meters',
          sizeMinPixels: 16,
          sizeMaxPixels: 36,
          pickable: true,
          updateTriggers: { getIcon: [cur.signalPhases.version, cur.signalPlans] },
        }),
        new IconLayer<PointMarker>({
          id: 'feature-points',
          data: feats.points,
          visible: zoom >= FEATURE_ZOOM,
          iconAtlas: markerAtlas().atlas as unknown as string,
          iconMapping: markerAtlas().mapping,
          getIcon: (m) => (m.kind === 'crossing' && live.crossing.has(m.id) ? 'crossing_active' : m.icon),
          getPosition: (m) => m.position,
          getSize: (m) => (m.id === sel ? 30 : 22),
          sizeUnits: 'pixels',
          pickable: editMode,
          updateTriggers: { getIcon: live.version, getSize: sel },
        }),
        new IconLayer<[number, number]>({
          id: 'broken',
          data: live.broken,
          iconAtlas: markerAtlas().atlas as unknown as string,
          iconMapping: markerAtlas().mapping,
          getIcon: () => 'broken',
          getPosition: (p) => p,
          getSize: 22,
          sizeUnits: 'pixels',
          pickable: true,
          updateTriggers: { getPosition: live.version },
        }),
        new ScatterplotLayer<Marker>({
          id: 'edit-markers',
          data: edits.markers,
          getPosition: (m) => m.position,
          getRadius: (m) => (m.kind.startsWith('uturn') ? 6 : 7),
          radiusUnits: 'pixels',
          getFillColor: (m) =>
            m.kind === 'closed'
              ? EDIT_RGBA.closedMarker
              : m.kind === 'lanes'
                ? EDIT_RGBA.laneMarker
                : m.kind === 'uturn'
                  ? accent
                  : EDIT_RGBA.markerStroke[theme],
          getLineColor: (m) => (m.kind === 'uturn-pending' ? accent : EDIT_RGBA.markerStroke[theme]),
          stroked: true,
          lineWidthUnits: 'pixels',
          getLineWidth: (m) => (m.kind === 'uturn-pending' ? 3 : 2),
          pickable: true,
          updateTriggers: { getFillColor: theme, getLineColor: theme },
        }),
      ]
      overlay.setProps({ layers })
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Base map follows light/dark theme.
  useEffect(() => {
    mapRef.current?.setStyle(MAP_STYLE[props.theme])
  }, [props.theme])

  // Frame the selected area.
  useEffect(() => {
    const a = props.area
    if (!a || !mapRef.current) return
    const [w, s, e, n] = a.bbox
    mapRef.current.fitBounds([[w, s], [e, n]], { padding: 24, duration: 800 })
  }, [props.area])

  useEffect(() => {
    const f = props.focus
    if (!f || !mapRef.current) return
    const [w, s, e, n] = f.bbox
    mapRef.current.fitBounds([[w, s], [e, n]], { padding: 24, duration: 800 })
  }, [props.focus])

  return <div ref={container} className="map" aria-label="Traffic simulation map" />
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}
