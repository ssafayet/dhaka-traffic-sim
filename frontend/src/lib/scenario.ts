/** Road edits: closures (live), signal timings (live) and layout edits (rebuild). */

import type { Closure, LayoutEdits, RoadFeature, SignalInfo, SignalPlan, Topology, TurnRule } from './types'

export const EMPTY_LAYOUT: LayoutEdits = { uturns: [], junction_uturns: [], junction_turns: [], signals: [] }

export const TURN_RULES: Record<TurnRule, { label: string; hint: string }> = {
  straight_left: { label: 'Straight and left only', hint: 'No right turns or U-turns: drivers go ahead or turn left.' },
  left: { label: 'Left only', hint: 'Every approach can only turn left.' },
}

export function sameLayout(a: LayoutEdits, b: LayoutEdits) {
  const key = (l: LayoutEdits) =>
    JSON.stringify([
      l.uturns.map((u) => [u.edge, u.lon, u.lat, u.both_directions]),
      [...l.junction_uturns].sort((x, y) => x.junction.localeCompare(y.junction)),
      [...l.junction_turns].sort((x, y) => x.junction.localeCompare(y.junction)),
      [...l.signals].sort(),
    ])
  return key(a) === key(b)
}

export function layoutCount(l: LayoutEdits) {
  return l.uturns.length + l.junction_uturns.length + l.junction_turns.length + l.signals.length
}

export function baseOf(road: RoadFeature['properties']) {
  return road.base ?? road.id
}

/** Closed lanes of a road: 'all', some lane indexes, or none. */
export function closureOf(closures: Closure[], edge: string): 'all' | number[] | null {
  const c = closures.find((x) => x.edge === edge)
  if (!c) return null
  return c.lanes ?? 'all'
}

export function setClosure(
  closures: Closure[],
  road: RoadFeature['properties'],
  lanes: 'all' | number[] | null,
): Closure[] {
  const rest = closures.filter((c) => c.edge !== road.id)
  if (lanes === null || (Array.isArray(lanes) && lanes.length === 0)) return rest
  const all = lanes === 'all' || lanes.length >= road.lanes
  const old = closures.find((c) => c.edge === road.id)
  return [
    ...rest,
    { ...timing(old), edge: road.id, base: baseOf(road), ...(all ? {} : { lanes: [...lanes].sort((a, b) => a - b) }) },
  ]
}

/** A closure's time window and reason, to carry over when its lanes or road change. */
function timing(c: Closure | undefined): Partial<Closure> {
  if (!c) return {}
  return { from: c.from, to: c.to, reason: c.reason }
}

function piecesByBase(roads: RoadFeature[]) {
  const byBase = new Map<string, RoadFeature['properties'][]>()
  for (const r of roads) {
    const b = baseOf(r.properties)
    byBase.set(b, [...(byBase.get(b) ?? []), r.properties])
  }
  return byBase
}

/**
 * Follow closures from one layout to the next. A road closed whole stays
 * closed on every piece when a U-turn splits it; a closed piece stays on its
 * piece while it exists, and becomes the whole road when the road is un-split.
 */
export function remapClosures(closures: Closure[], from: RoadFeature[], to: RoadFeature[]): Closure[] {
  const ids = new Map(to.map((r) => [r.properties.id, r.properties]))
  const before = piecesByBase(from)
  const byBase = piecesByBase(to)
  const out = new Map<string, Closure>()
  for (const c of closures) {
    const wasWhole = (before.get(c.base)?.length ?? 1) <= 1
    const targets = !wasWhole && ids.has(c.edge) ? [ids.get(c.edge)!] : (byBase.get(c.base) ?? [])
    for (const t of targets) {
      const lanes = c.lanes?.filter((i) => i < t.lanes)
      out.set(t.id, { ...timing(c), edge: t.id, base: baseOf(t), ...(lanes && lanes.length < t.lanes ? { lanes } : {}) })
    }
  }
  return [...out.values()]
}

/** Keep signal plans that still fit their signal in the new layout. */
export function pruneSignalPlans(plans: Record<string, SignalPlan>, topology: Topology) {
  const byId = new Map(topology.signals.map((s) => [s.id, s]))
  return Object.fromEntries(
    Object.entries(plans).filter(([id, p]) => {
      const s = byId.get(id)
      return s && (p.mode === 'off' || p.phases.length === s.phases.length)
    }),
  )
}

/** How the signal runs unless the user changes it: police-directed ones start off. */
export function defaultPlan(s: SignalInfo): SignalPlan {
  return { mode: s.automated ? s.mode : 'off', phases: s.phases.map(({ duration, min, max }) => ({ duration, min, max })) }
}

export function samePlan(a: SignalPlan, b: SignalPlan) {
  if (a.mode !== b.mode) return false
  if (a.mode === 'off') return true
  return a.phases.every((p, i) => {
    const q = b.phases[i]
    return p.duration === q.duration && (a.mode === 'fixed' || (p.min === q.min && p.max === q.max))
  })
}

export type PhaseKind = 'green' | 'amber' | 'red'

export function phaseKind(state: string): PhaseKind {
  if (/[Gg]/.test(state)) return 'green'
  if (/y/.test(state)) return 'amber'
  return 'red'
}

/** What drivers on one approach see: green if any of its movements has green. */
export function approachLight(state: string, links: number[]): PhaseKind {
  const chars = links.map((i) => state[i])
  if (chars.some((ch) => ch === 'G' || ch === 'g')) return 'green'
  if (chars.some((ch) => ch === 'y' || ch === 'Y')) return 'amber'
  return 'red'
}

const TURN: Record<string, string> = { s: 'straight', l: 'left', L: 'left', r: 'right', R: 'right', t: 'U-turn', T: 'U-turn' }

/** "Green for Kazi Nazrul Islam Ave from the north (straight, left)" per approach. */
export function greenApproaches(signal: SignalInfo, state: string): string[] {
  const moves = new Map<string, Set<string>>()
  state.split('').forEach((ch, i) => {
    const link = signal.links[i]
    if (!link || (ch !== 'G' && ch !== 'g')) return
    const key = `${link.name || 'Unnamed road'} from the ${link.approach}`
    moves.set(key, (moves.get(key) ?? new Set()).add(TURN[link.dir] ?? 'straight'))
  })
  return [...moves].map(([k, m]) => `${k} (${[...m].join(', ')})`)
}

export function signalName(signal: SignalInfo) {
  const names = [...new Set(signal.links.map((l) => l?.name).filter(Boolean))]
  return names.length ? names.slice(0, 2).join(' × ') : 'Traffic signal'
}

export function cycleSeconds(plan: SignalPlan): [number, number] {
  if (plan.mode === 'off') return [0, 0]
  if (plan.mode === 'fixed') {
    const t = plan.phases.reduce((a, p) => a + p.duration, 0)
    return [t, t]
  }
  return [plan.phases.reduce((a, p) => a + p.min, 0), plan.phases.reduce((a, p) => a + p.max, 0)]
}

/** Split nodes the editor made for U-turns; not junctions to edit. */
export function isUTurnNode(id: string) {
  return /^ut\d+[ab]?$/.test(id)
}
