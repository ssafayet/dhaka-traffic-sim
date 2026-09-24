import { useState } from 'react'
import type {
  Closure,
  ClosureReason,
  JunctionInfo,
  LayoutEdits,
  PhaseTiming,
  RoadFeature,
  RoadFeatures,
  Selection,
  SignalInfo,
  SignalMode,
  SignalPlan,
  TurnRule,
} from '../lib/types'
import {
  closureOf,
  cycleSeconds,
  defaultPlan,
  greenApproaches,
  phaseKind,
  samePlan,
  setClosure,
  signalName,
  TURN_RULES,
} from '../lib/scenario'
import { FeatureEditor } from './FeatureEditor'
import { featureTitle } from '../lib/features'

const MAX_PHASE_S = 300

interface Props {
  selection: Selection
  roadsById: Map<string, RoadFeature>
  twins: Record<string, string>
  junctions: Map<string, JunctionInfo>
  signals: Map<string, SignalInfo>
  closures: Closure[]
  onClosures: (c: Closure[]) => void
  layout: LayoutEdits
  /** The layout the running network was built with. */
  appliedLayout: LayoutEdits
  onLayout: (l: LayoutEdits) => void
  signalPlans: Record<string, SignalPlan>
  onSignalPlan: (id: string, plan: SignalPlan | null) => void
  signalPhase: (id: string) => number | undefined
  onSelect: (s: Selection | null) => void
  features: RoadFeatures
  onFeatures: (f: RoadFeatures) => void
}

export function Inspector(p: Props) {
  const s = p.selection
  let body: React.ReactNode = null
  let title = ''
  if (s.kind === 'road') {
    const road = p.roadsById.get(s.id)
    if (road) {
      title = road.properties.uturn ? 'U-turn' : road.properties.name || 'Unnamed road'
      body = <RoadEditor {...p} road={road} at={[s.lon, s.lat]} />
    }
  } else if (s.kind === 'junction') {
    const j = p.junctions.get(s.id)
    if (j) {
      title = j.names.join(' × ') || 'Junction'
      body = <JunctionEditor {...p} junction={j} />
    }
  } else if (s.kind === 'signal') {
    const sig = p.signals.get(s.id)
    if (sig) {
      title = signalName(sig)
      body = <SignalEditor key={sig.id} {...p} signal={sig} />
    }
  } else if (s.kind === 'uturn') {
    const u = p.layout.uturns[s.index]
    if (u) {
      title = 'New U-turn'
      body = <PendingUTurn {...p} index={s.index} />
    }
  } else {
    title = featureTitle(p.features, s.kind, s.id)
    body = <FeatureEditor key={s.id} kind={s.kind} id={s.id} features={p.features} onFeatures={p.onFeatures} onClose={() => p.onSelect(null)} />
  }
  if (!body) return null
  return (
    <aside className="inspector" aria-label="Edit selected road feature">
      <header>
        <h2>{title}</h2>
        <button className="icon-btn" aria-label="Close" onClick={() => p.onSelect(null)}>
          ×
        </button>
      </header>
      {body}
    </aside>
  )
}

const LAYOUT_NOTE = 'Takes effect when you apply layout changes (the simulation restarts).'

function RoadEditor(p: Props & { road: RoadFeature; at: [number, number] }) {
  const r = p.road.properties
  const [both, setBoth] = useState(true)
  const twinId = p.twins[r.id]
  const twin = twinId ? p.roadsById.get(twinId) : undefined
  const mine = closureOf(p.closures, r.id)
  const twinClosed = twin ? closureOf(p.closures, twin.properties.id) === 'all' : false
  const state = mine === 'all' ? (twinClosed ? 'both' : 'this') : 'open'

  const setState = (next: 'open' | 'this' | 'both') => {
    let c = setClosure(p.closures, r, next === 'open' ? null : 'all')
    if (twin) c = setClosure(c, twin.properties, next === 'both' ? 'all' : null)
    p.onClosures(c)
  }
  const toggleLane = (i: number) => {
    const lanes = Array.isArray(mine) ? mine : []
    p.onClosures(setClosure(p.closures, r, lanes.includes(i) ? lanes.filter((x) => x !== i) : [...lanes, i]))
  }

  if (r.uturn) {
    // A median gap in the running layout: removable from the draft layout.
    const tag = /^ut(\d+)\.gapr?$/.exec(r.id)
    const applied = tag ? p.appliedLayout.uturns[Number(tag[1])] : undefined
    const draftIndex = applied
      ? p.layout.uturns.findIndex((u) => u.edge === applied.edge && u.lon === applied.lon && u.lat === applied.lat)
      : -1
    return (
      <>
        <p className="muted">A gap in the median where vehicles cross to turn back.</p>
        {draftIndex >= 0 ? (
          <button onClick={() => p.onLayout({ ...p.layout, uturns: p.layout.uturns.filter((_, i) => i !== draftIndex) })}>
            Remove this U-turn
          </button>
        ) : (
          <p className="notice small">Removed; apply layout changes to take it out.</p>
        )}
      </>
    )
  }

  return (
    <>
      <p className="muted small">
        {r.lanes} lane{r.lanes > 1 ? 's' : ''} · {Math.round(r.speed * 3.6)} km/h ·{' '}
        {twin ? 'two-way street' : 'one direction only'}
      </p>

      <h3>Closure</h3>
      <div className="seg wide" role="radiogroup" aria-label="Close this road">
        {(
          [
            ['open', 'Open'],
            ['this', twin ? 'This direction' : 'Closed'],
            ...(twin ? [['both', 'Both directions']] : []),
          ] as ['open' | 'this' | 'both', string][]
        ).map(([v, label]) => (
          <button key={v} role="radio" aria-checked={state === v} className={state === v ? 'on' : ''} onClick={() => setState(v)}>
            {label}
          </button>
        ))}
      </div>
      {r.lanes > 1 && mine !== 'all' && (
        <fieldset className="lanes">
          <legend className="small muted">Or close single lanes (construction, parked vehicles)</legend>
          {Array.from({ length: r.lanes }, (_, i) => (
            <label key={i} className="check tight">
              <input type="checkbox" checked={Array.isArray(mine) && mine.includes(i)} onChange={() => toggleLane(i)} />
              <span>
                Lane {i + 1}
                <span className="muted"> · {i === 0 ? 'kerb side' : i === r.lanes - 1 ? (twin ? 'centre line' : 'median side') : 'middle'}</span>
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {mine !== null && <ClosureTiming {...p} edges={[r.id, ...(state === 'both' && twin ? [twin.properties.id] : [])]} />}
      <p className="muted small">Applies at once. Vehicles heading here are rerouted.</p>

      <h3>U-turn</h3>
      <label className="check tight">
        <input type="checkbox" checked={both} onChange={(e) => setBoth(e.target.checked)} />
        <span>For both directions</span>
      </label>
      <button
        onClick={() => {
          const uturns = [...p.layout.uturns, { edge: r.base ?? r.id, lon: p.at[0], lat: p.at[1], both_directions: both, name: r.name }]
          p.onLayout({ ...p.layout, uturns })
          p.onSelect({ kind: 'uturn', index: uturns.length - 1 })
        }}
      >
        Add a U-turn here
      </button>
      <p className="muted small">
        {twin ? 'Vehicles turn around mid-street at the point you clicked.' : 'Opens a gap in the median to the other carriageway at the point you clicked.'}{' '}
        {LAYOUT_NOTE}
      </p>
    </>
  )
}

const REASONS: [ClosureReason, string][] = [
  ['construction', 'Construction'],
  ['event', 'Event or rally'],
  ['vip', 'VIP movement'],
  ['accident', 'Accident'],
  ['other', 'Other'],
]

const hhmm = (s: number) => `${String(Math.floor(s / 3600) % 24).padStart(2, '0')}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}`
const timeOfDay = (t: string) => {
  const [h, m] = t.split(':').map(Number)
  return (h || 0) * 3600 + (m || 0) * 60
}

/** When a closure applies (all day, or a time window) and why. */
function ClosureTiming(p: Props & { edges: string[] }) {
  const mine = p.closures.filter((c) => p.edges.includes(c.edge))
  const first = mine[0]
  if (!first) return null
  const timed = first.from !== undefined && first.to !== undefined
  const set = (change: Partial<Closure>) =>
    p.onClosures(p.closures.map((c) => (p.edges.includes(c.edge) ? { ...c, ...change } : c)))
  return (
    <div className="timing">
      <label className="field">
        <span className="field-label">When</span>
        <select
          value={timed ? 'window' : 'always'}
          onChange={(e) => set(e.target.value === 'window' ? { from: 17 * 3600, to: 19 * 3600 } : { from: undefined, to: undefined })}
        >
          <option value="always">All the time</option>
          <option value="window">Between two times</option>
        </select>
      </label>
      {timed && (
        <div className="time-range">
          <input type="time" aria-label="Closed from" value={hhmm(first.from!)} onChange={(e) => set({ from: timeOfDay(e.target.value) })} />
          <span className="muted">to</span>
          <input type="time" aria-label="Closed until" value={hhmm(first.to!)} onChange={(e) => set({ to: timeOfDay(e.target.value) })} />
        </div>
      )}
      <label className="field">
        <span className="field-label">Reason</span>
        <select value={first.reason ?? 'construction'} onChange={(e) => set({ reason: e.target.value as ClosureReason })}>
          {REASONS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {timed && <p className="muted small">Follows the simulation clock; windows can run past midnight.</p>}
    </div>
  )
}

function PendingUTurn(p: Props & { index: number }) {
  const u = p.layout.uturns[p.index]
  const update = (uturns: LayoutEdits['uturns']) => p.onLayout({ ...p.layout, uturns })
  return (
    <>
      <p className="muted">On {u.name || 'an unnamed road'}. {LAYOUT_NOTE}</p>
      <label className="check tight">
        <input
          type="checkbox"
          checked={u.both_directions}
          onChange={(e) => update(p.layout.uturns.map((x, i) => (i === p.index ? { ...x, both_directions: e.target.checked } : x)))}
        />
        <span>For both directions</span>
      </label>
      <button
        onClick={() => {
          update(p.layout.uturns.filter((_, i) => i !== p.index))
          p.onSelect(null)
        }}
      >
        Remove this U-turn
      </button>
    </>
  )
}

function JunctionTurns(p: Props & { junction: JunctionInfo }) {
  const j = p.junction
  const edit = p.layout.junction_turns.find((x) => j.group.includes(x.junction))
  const rule: TurnRule | 'mapped' = edit?.allow ?? 'mapped'
  const setRule = (v: TurnRule | 'mapped') => {
    const rest = p.layout.junction_turns.filter((x) => !j.group.includes(x.junction))
    p.onLayout({
      ...p.layout,
      junction_turns: v === 'mapped' ? rest : [...rest, { junction: j.group[0], allow: v }],
      // The rule already bans U-turns; a separate U-turn setting would conflict.
      junction_uturns: v === 'mapped' ? p.layout.junction_uturns : p.layout.junction_uturns.filter((x) => !j.group.includes(x.junction)),
    })
  }
  return (
    <>
      <h3>Turns allowed</h3>
      <div className="seg wide" role="radiogroup" aria-label="Turns allowed at this junction">
        {(
          [
            ['mapped', 'As mapped'],
            ['straight_left', 'Straight + left'],
            ['left', 'Left only'],
          ] as const
        ).map(([v, label]) => (
          <button key={v} role="radio" aria-checked={rule === v} className={rule === v ? 'on' : ''} onClick={() => setRule(v)}>
            {label}
          </button>
        ))}
      </div>
      <p className="muted small">
        {rule === 'mapped' ? 'Every movement the map allows.' : TURN_RULES[rule].hint}
        {j.group.length > 1 && ` This crossing is mapped as ${j.group.length} junctions (a divided road); the rule covers all of them.`}{' '}
        {LAYOUT_NOTE}
      </p>
    </>
  )
}

function JunctionUTurns(p: Props & { junction: JunctionInfo }) {
  const j = p.junction
  if (p.layout.junction_turns.some((x) => j.group.includes(x.junction))) return null
  const edit = p.layout.junction_uturns.find((x) => x.junction === j.id)
  const uturn = edit ? (edit.allow ? 'allow' : 'ban') : 'keep'
  const setUTurn = (v: 'keep' | 'allow' | 'ban') => {
    const rest = p.layout.junction_uturns.filter((x) => x.junction !== j.id)
    p.onLayout({ ...p.layout, junction_uturns: v === 'keep' ? rest : [...rest, { junction: j.id, allow: v === 'allow' }] })
  }
  return (
    <>
      <h3>U-turns at this junction</h3>
      <div className="seg wide" role="radiogroup" aria-label="U-turns at this junction">
        {(
          [
            ['keep', 'As mapped'],
            ['allow', 'Allow'],
            ['ban', 'Ban'],
          ] as const
        ).map(([v, label]) => (
          <button key={v} role="radio" aria-checked={uturn === v} className={uturn === v ? 'on' : ''} onClick={() => setUTurn(v)}>
            {label}
          </button>
        ))}
      </div>
      <p className="muted small">
        As mapped: U-turns possible from {j.uturns} of {j.approaches} approaches. {LAYOUT_NOTE}
      </p>
    </>
  )
}

function JunctionEditor(p: Props & { junction: JunctionInfo }) {
  const j = p.junction
  const addsSignal = p.layout.signals.includes(j.id)
  return (
    <>
      <p className="muted small">
        {j.approaches} approaches · no signal
      </p>
      <JunctionTurns {...p} />
      <JunctionUTurns {...p} />
      <h3>Traffic signal</h3>
      <label className="check">
        <input
          type="checkbox"
          checked={addsSignal}
          onChange={(e) =>
            p.onLayout({
              ...p.layout,
              signals: e.target.checked ? [...p.layout.signals, j.id] : p.layout.signals.filter((x) => x !== j.id),
            })
          }
        />
        <span>
          Add a traffic signal
          <span className="muted small block">Starts with an actuated plan that adapts to traffic; tune it once applied.</span>
        </span>
      </label>
    </>
  )
}

const MODES: { value: SignalMode; label: string; hint: string }[] = [
  { value: 'actuated', label: 'Actuated', hint: 'Greens stretch between a minimum and maximum while traffic keeps arriving.' },
  { value: 'fixed', label: 'Fixed time', hint: 'Every phase runs for a set time, whatever the traffic.' },
  { value: 'off', label: 'Off', hint: 'Signal dark: drivers fall back to the junction’s right of way, as when traffic police wave them through.' },
]

function label(kind: string, greenNumber: number) {
  if (kind === 'green') return `Green phase ${greenNumber}`
  return greenNumber ? `Amber after green phase ${greenNumber}` : 'Amber'
}

function seconds(v: string, fallback: number) {
  const n = Math.round(Number(v))
  return Number.isFinite(n) ? Math.min(MAX_PHASE_S, Math.max(1, n)) : fallback
}

function SignalEditor(p: Props & { signal: SignalInfo }) {
  const sig = p.signal
  const base = defaultPlan(sig)
  const plan = p.signalPlans[sig.id] ?? base
  // Switching off keeps the timings, so switching back on restores them.
  const timings = plan.phases.length === base.phases.length ? plan.phases : base.phases
  const current = p.signalPhase(sig.id)

  const commit = (next: SignalPlan) => p.onSignalPlan(sig.id, samePlan(next, base) ? null : next)
  const setMode = (mode: SignalMode) => commit({ mode, phases: timings })
  const setPhase = (i: number, change: Partial<PhaseTiming>) => {
    const phases = timings.map((ph, k) => {
      if (k !== i) return ph
      const next = { ...ph, ...change }
      if (plan.mode === 'fixed') return { duration: next.duration, min: next.duration, max: next.duration }
      if (change.min !== undefined && next.max < next.min) next.max = next.min
      if (change.max !== undefined && next.min > next.max) next.min = next.max
      next.duration = Math.min(next.max, Math.max(next.min, sig.phases[k].duration))
      return next
    })
    commit({ mode: plan.mode, phases })
  }
  const [lo, hi] = cycleSeconds(plan)

  return (
    <>
      <p className="muted small">
        {sig.automated
          ? 'Runs automatically by default, like the signals on Dhaka’s automatic corridors.'
          : 'Off by default: outside Dhaka’s automatic corridors, traffic police direct this junction. Switch it on to run it automatically.'}
      </p>
      <div className="seg wide" role="radiogroup" aria-label="Signal control">
        {MODES.map((m) => (
          <button key={m.value} role="radio" aria-checked={plan.mode === m.value} className={plan.mode === m.value ? 'on' : ''} onClick={() => setMode(m.value)}>
            {m.label}
          </button>
        ))}
      </div>
      <p className="muted small">{MODES.find((m) => m.value === plan.mode)?.hint}</p>

      {plan.mode !== 'off' && (
        <>
          <ol className="phases">
            {sig.phases.map((ph, i) => {
              const kind = phaseKind(ph.state)
              const greens = kind === 'green' ? greenApproaches(sig, ph.state) : []
              const t = timings[i]
              // Number the green phases; amber and all-red run between them.
              const n = sig.phases.slice(0, i + 1).filter((x) => phaseKind(x.state) === 'green').length
              return (
                <li key={i} className={current === i ? 'current' : ''} aria-current={current === i ? 'step' : undefined}>
                  <span className={`phase-chip ${kind}`} aria-hidden />
                  <div className="phase-text">
                    <strong>{kind === 'green' ? `Green phase ${n}` : kind === 'amber' ? 'Amber' : 'All red'}</strong>
                    {greens.map((g) => (
                      <span key={g} className="small muted block">
                        {g}
                      </span>
                    ))}
                  </div>
                  <div className="phase-times">
                    {plan.mode === 'fixed' ? (
                      <NumberField label={`${label(kind, n)} duration`} value={t.duration} onChange={(v) => setPhase(i, { duration: v })} />
                    ) : (
                      <>
                        <NumberField label={`${label(kind, n)} minimum`} value={t.min} onChange={(v) => setPhase(i, { min: v })} />
                        <span className="muted">–</span>
                        <NumberField label={`${label(kind, n)} maximum`} value={t.max} onChange={(v) => setPhase(i, { max: v })} />
                      </>
                    )}
                    <span className="muted small">s</span>
                  </div>
                </li>
              )
            })}
          </ol>
          <p className="small">
            Cycle: <strong>{lo === hi ? `${lo} s` : `${lo}–${hi} s`}</strong>
          </p>
        </>
      )}
      <button disabled={!p.signalPlans[sig.id]} onClick={() => p.onSignalPlan(sig.id, null)}>
        {sig.automated ? 'Reset to the network’s plan' : 'Reset to police control (off)'}
      </button>
      <p className="muted small">Timing changes apply at once.</p>
      {p.junctions.has(sig.id) && (
        <>
          <JunctionTurns {...p} junction={p.junctions.get(sig.id)!} />
          <JunctionUTurns {...p} junction={p.junctions.get(sig.id)!} />
        </>
      )}
    </>
  )
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  // Local text while typing; committed (and clamped) on blur or Enter.
  const [text, setText] = useState<string | null>(null)
  const commit = () => {
    if (text !== null) onChange(seconds(text, value))
    setText(null)
  }
  return (
    <input
      className="num"
      type="number"
      inputMode="numeric"
      min={1}
      max={MAX_PHASE_S}
      aria-label={label}
      value={text ?? value}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
    />
  )
}
