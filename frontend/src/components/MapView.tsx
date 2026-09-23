import { useEffect, useLayoutEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre 6 ships its worker as an ES module; let Vite bundle it.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { IconLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import type { Area, RoadFeature, VehicleType } from '../lib/types'
import { VehicleStore } from '../lib/vehicleStore'
import { buildIconAtlas, type IconAtlas } from '../lib/vehicleIcons'
import { CONGESTION, MAP_STYLE, ROAD_IDLE, congestionIndex, congestionRGBA, vehicleRGBA, type Theme } from '../lib/palette'

export type ColorMode = 'type' | 'speed'

export interface EdgeLevels {
  levels: Record<string, number>
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
}

maplibregl.setWorkerUrl(maplibreWorkerUrl)

// Below this zoom vehicles are dots; above it, to-scale silhouettes.
const DETAIL_ZOOM = 16.3

export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const latest = useRef(props)
  useLayoutEffect(() => {
    latest.current = props
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

    const overlay = new MapboxOverlay({ interleaved: false, layers: [], getTooltip: tooltip })
    map.addControl(overlay)

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

      const detailed = map.getZoom() >= DETAIL_ZOOM
      const binary = { length: n, attributes: { getPosition: { value: store.positions, size: 2 } } }
      const layers = [
        new PathLayer<RoadFeature>({
          id: 'roads',
          data: cur.roads,
          visible: cur.showCongestion,
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
        new ScatterplotLayer({
          id: 'vehicles-dots',
          visible: !detailed,
          data: { ...binary, attributes: { ...binary.attributes, getFillColor: { value: colors, size: 4, normalized: true } } },
          getRadius: 2.5,
          radiusUnits: 'meters',
          radiusMinPixels: 1.6,
          radiusMaxPixels: 5,
          pickable: true,
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
          pickable: true,
          updateTriggers: { getIcon: store.version },
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
