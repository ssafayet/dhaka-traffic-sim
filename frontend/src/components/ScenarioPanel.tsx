import type {
  Closure,
  JunctionInfo,
  LayoutEdits,
  LayoutError,
  RoadFeature,
  RoadFeatures,
  Selection,
  SignalInfo,
  SignalPlan,
} from '../lib/types'
import { cycleSeconds, layoutCount, signalName, TURN_RULES } from '../lib/scenario'
import { DEPTH, STAND_LABEL, remove } from '../lib/features'

interface Props {
  editMode: boolean
  onEditMode: (on: boolean) => void
  roadsById: Map<string, RoadFeature>
  junctions: Map<string, JunctionInfo>
  signals: Map<string, SignalInfo>
  closures: Closure[]
  onClosures: (c: Closure[]) => void
  signalPlans: Record<string, SignalPlan>
  onSignalPlan: (id: string, plan: SignalPlan | null) => void
  layout: LayoutEdits
  onLayout: (l: LayoutEdits) => void
  layoutDirty: boolean
  building: boolean
  layoutErrors: LayoutError[]
  onApplyLayout: () => void
  onSelect: (s: Selection) => void
  onClearAll: () => void
  features: RoadFeatures
  featureDefaults: RoadFeatures
  onFeatures: (f: RoadFeatures) => void
}

const REASON: Record<string, string> = { construction: 'construction', event: 'event', vip: 'VIP movement', accident: 'accident', other: '' }
const hhmm = (s: number) => `${String(Math.floor(s / 3600) % 24).padStart(2, '0')}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}`

interface Item {
  key: string
  label: string
  detail?: string
  error?: string
  select?: Selection
  remove: () => void
}

export function ScenarioPanel(p: Props) {
  const roadName = (id: string) => p.roadsById.get(id)?.properties.name || 'Unnamed road'
  // Edits name junctions of the unedited area; a merged crossing answers to its old ids.
  const junction = (id: string) => p.junctions.get(id) ?? [...p.junctions.values()].find((j) => j.group.includes(id))
  const junctionName = (id: string) => junction(id)?.names.join(' × ') || 'a junction'
  const junctionSelect = (id: string): Selection | undefined => {
    const j = junction(id)
    if (!j) return undefined
    return j.signal ? { kind: 'signal', id: j.signal } : { kind: 'junction', id: j.id }
  }
  const errorFor = (kind: LayoutError['kind'], index: number) =>
    p.layoutErrors.find((e) => e.kind === kind && e.index === index)?.message

  const live: Item[] = [
    ...p.closures.map((c) => {
      const road = p.roadsById.get(c.edge)
      return {
        key: `c:${c.edge}`,
        label: roadName(c.edge),
        detail: [
          c.lanes ? `lane${c.lanes.length > 1 ? 's' : ''} ${c.lanes.map((i) => i + 1).join(', ')} closed` : 'closed',
          c.from !== undefined && c.to !== undefined ? `${hhmm(c.from)}–${hhmm(c.to)}` : '',
          c.reason ? REASON[c.reason] : '',
        ].filter(Boolean).join(' · '),
        select: road ? { kind: 'road' as const, id: c.edge, ...midpoint(road) } : undefined,
        remove: () => p.onClosures(p.closures.filter((x) => x.edge !== c.edge)),
      }
    }),
    ...Object.entries(p.signalPlans).map(([id, plan]) => {
      const s = p.signals.get(id)
      const [lo, hi] = cycleSeconds(plan)
      return {
        key: `s:${id}`,
        label: s ? signalName(s) : 'Traffic signal',
        detail: plan.mode === 'off' ? 'signal off' : `${plan.mode === 'fixed' ? 'fixed time' : 'actuated'}, ${lo === hi ? lo : `${lo}–${hi}`} s cycle`,
        select: { kind: 'signal' as const, id },
        remove: () => p.onSignalPlan(id, null),
      }
    }),
  ]
  const layout: Item[] = [
    ...p.layout.uturns.map((u, i) => ({
      key: `u:${i}`,
      label: `U-turn on ${u.name || 'an unnamed road'}`,
      detail: u.both_directions ? 'both directions' : 'one direction',
      error: errorFor('uturn', i),
      select: { kind: 'uturn' as const, index: i },
      remove: () => p.onLayout({ ...p.layout, uturns: p.layout.uturns.filter((_, k) => k !== i) }),
    })),
    ...p.layout.junction_uturns.map((j, i) => ({
      key: `j:${j.junction}`,
      label: `U-turns ${j.allow ? 'allowed' : 'banned'}`,
      detail: `at ${junctionName(j.junction)}`,
      error: errorFor('junction_uturn', i),
      select: junctionSelect(j.junction),
      remove: () => p.onLayout({ ...p.layout, junction_uturns: p.layout.junction_uturns.filter((_, k) => k !== i) }),
    })),
    ...p.layout.junction_turns.map((t, i) => ({
      key: `r:${t.junction}`,
      label: TURN_RULES[t.allow].label,
      detail: `at ${junctionName(t.junction)}`,
      error: errorFor('junction_turn', i),
      select: junctionSelect(t.junction),
      remove: () => p.onLayout({ ...p.layout, junction_turns: p.layout.junction_turns.filter((_, k) => k !== i) }),
    })),
    ...p.layout.signals.map((id, i) => ({
      key: `t:${id}`,
      label: 'New traffic signal',
      detail: `at ${junctionName(id)}`,
      error: errorFor('signal', i),
      select: junctionSelect(id),
      remove: () => p.onLayout({ ...p.layout, signals: p.layout.signals.filter((x) => x !== id) }),
    })),
  ]
  const f = p.features
  const rm = (kind: Parameters<typeof remove>[1], id: string) => () => p.onFeatures(remove(f, kind, id))
  const placed: Item[] = [
    ...f.bus_stops.filter((s) => !s.osm).map((s) => ({ key: `b:${s.id}`, label: 'Bus stop', detail: `${s.dwell} s stops`, select: { kind: 'bus_stop' as const, id: s.id }, remove: rm('bus_stop', s.id) })),
    ...f.stands.map((s) => ({ key: `s:${s.id}`, label: STAND_LABEL[s.kind], detail: `${s.parked} parked`, select: { kind: 'stand' as const, id: s.id }, remove: rm('stand', s.id) })),
    ...f.crossings.map((c) => ({ key: `x:${c.id}`, label: 'Pedestrian crossing', detail: `every ~${c.every} s`, select: { kind: 'crossing' as const, id: c.id }, remove: rm('crossing', c.id) })),
    ...f.hot_zones.map((z) => ({ key: `h:${z.id}`, label: z.name || 'Hot zone', detail: `${z.radius} m · ${z.trips}× trips`, select: { kind: 'hot_zone' as const, id: z.id }, remove: rm('hot_zone', z.id) })),
    ...f.water.filter((w) => !w.preset).map((w) => ({ key: `w:${w.id}`, label: w.name || 'Waterlogging', detail: DEPTH[w.depth].label.toLowerCase(), select: { kind: 'water' as const, id: w.id }, remove: rm('water', w.id) })),
  ]
  const d = p.featureDefaults
  const osmStops = f.bus_stops.filter((s) => s.osm).length
  const presetWater = f.water.filter((w) => w.preset)
  const presetOff = presetWater.filter((w) => !w.enabled).length
  const defaultsChanged =
    JSON.stringify([f.bus_stops.filter((s) => s.osm), presetWater]) !== JSON.stringify([d.bus_stops, d.water])
  const restoreDefaults = () =>
    p.onFeatures({ ...f, bus_stops: [...d.bus_stops, ...f.bus_stops.filter((s) => !s.osm)], water: [...d.water, ...f.water.filter((w) => !w.preset)] })

  const general = p.layoutErrors.filter((e) => e.kind === 'other')
  const empty = !live.length && !layout.length && !placed.length

  return (
    <section aria-label="Road changes">
      <h2>Road changes</h2>
      <button className={`edit-toggle ${p.editMode ? 'on' : ''}`} aria-pressed={p.editMode} onClick={() => p.onEditMode(!p.editMode)}>
        {p.editMode ? 'Done editing' : 'Edit roads on the map'}
      </button>
      {p.editMode && empty && (
        <p className="muted small">Use the tools above the map to add bus stops, stands, crossings, hot zones and waterlogging. Or click a road to close it or add a U-turn, a junction to restrict its turns or add a signal, or a signal to change its timing.</p>
      )}

      {live.length > 0 && (
        <>
          <h3>Live</h3>
          <ChangeList items={live} onSelect={p.onSelect} />
        </>
      )}
      {placed.length > 0 && (
        <>
          <h3>On the map</h3>
          <ChangeList items={placed} onSelect={p.onSelect} />
        </>
      )}
      <p className="muted small">
        Included: {osmStops} bus stop{osmStops === 1 ? '' : 's'} from OpenStreetMap, {presetWater.length} flood-prone spot
        {presetWater.length === 1 ? '' : 's'} from news reports{presetOff ? ` (${presetOff} switched off)` : ''}.
        {defaultsChanged && (
          <>
            {' '}
            <button className="link inline" onClick={restoreDefaults}>
              Restore these
            </button>
          </>
        )}
      </p>
      {(layout.length > 0 || p.layoutDirty) && (
        <>
          <h3>Layout</h3>
          {layout.length > 0 ? (
            <ChangeList items={layout} onSelect={p.onSelect} />
          ) : (
            <p className="muted small">All layout changes removed.</p>
          )}
          {general.map((e) => (
            <p key={e.message} className="error small" role="alert">
              {e.message}
            </p>
          ))}
          {p.layoutDirty && (
            <button className="primary" disabled={p.building} onClick={p.onApplyLayout}>
              {p.building ? 'Building the road network…' : `Apply layout changes${layoutCount(p.layout) ? ` (${layoutCount(p.layout)})` : ''} & restart`}
            </button>
          )}
        </>
      )}
      {!empty && (
        <button className="link" onClick={p.onClearAll}>
          Clear all road changes
        </button>
      )}
    </section>
  )
}

function ChangeList({ items, onSelect }: { items: Item[]; onSelect: (s: Selection) => void }) {
  return (
    <ul className="changes">
      {items.map((it) => (
        <li key={it.key} className={it.error ? 'has-error' : ''}>
          <button className="change" disabled={!it.select} onClick={() => it.select && onSelect(it.select)}>
            <span className="change-label">{it.label}</span>
            {it.detail && <span className="muted small"> · {it.detail}</span>}
            {it.error && <span className="error small block">{it.error}</span>}
          </button>
          <button className="icon-btn" aria-label={`Remove: ${it.label}`} onClick={it.remove}>
            ×
          </button>
        </li>
      ))}
    </ul>
  )
}

function midpoint(road: RoadFeature) {
  const c = road.geometry.coordinates
  const [lon, lat] = c[Math.floor(c.length / 2)]
  return { lon, lat }
}
