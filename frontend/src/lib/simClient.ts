import type { Closure, Demand, Frame, RoadFeatures, SignalPlan, SimOptions, SimState } from './types'
import { toServer } from './features'

export type SimEvent =
  | { type: 'closures'; closed: number; rerouted: number; stranded: number }
  | { type: 'signal'; id: string }
  | { type: 'features'; rerouted: number; stranded: number }
  | { type: 'warning'; message: string }

type ServerMsg =
  | Frame
  | SimEvent
  | { type: 'status'; state: 'starting' | 'running' | 'paused' | 'stopped' }
  | { type: 'error'; message: string }

export interface SimClientHandlers {
  onFrame: (frame: Frame) => void
  onState: (state: SimState, message?: string) => void
  /** Replies to live road edits. */
  onEvent: (event: SimEvent) => void
}

export interface StartParams {
  area: string
  /** Edited road layout (see api.buildLayout); null = the area as mapped. */
  variant: string | null
  demand: Demand
  options: SimOptions
  closures: Closure[]
  signals: Record<string, SignalPlan>
  features: RoadFeatures
  speed: number
  warmup: number
}

function closuresMsg(closures: Closure[]) {
  return closures.map(({ edge, lanes, from, to }) => ({
    edge,
    ...(lanes ? { lanes } : {}),
    ...(from !== undefined && to !== undefined ? { from, to } : {}),
  }))
}

/** WebSocket connection to the backend; one SUMO process per connection. */
export class SimClient {
  private ws: WebSocket | null = null
  private queue: object[] = []
  private handlers: SimClientHandlers
  private closedByUs = false

  constructor(handlers: SimClientHandlers) {
    this.handlers = handlers
  }

  private connect() {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return
    this.closedByUs = false
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/sim`)
    this.ws = ws
    this.handlers.onState('connecting')
    ws.onopen = () => {
      for (const m of this.queue) ws.send(JSON.stringify(m))
      this.queue = []
    }
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as ServerMsg
      if (msg.type === 'frame') this.handlers.onFrame(msg)
      else if (msg.type === 'status') this.handlers.onState(msg.state)
      else if (msg.type === 'error') this.handlers.onState('error', msg.message)
      else this.handlers.onEvent(msg)
    }
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null
      if (!this.closedByUs) this.handlers.onState('error', 'Lost connection to the simulation server.')
    }
  }

  private send(msg: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
    else {
      this.queue.push(msg)
      this.connect()
    }
  }

  start(p: StartParams) {
    this.send({ type: 'start', ...p, closures: closuresMsg(p.closures), features: toServer(p.features) })
  }

  /** Replace the set of closed roads and lanes. */
  setClosures(closures: Closure[]) {
    this.send({ type: 'closures', closures: closuresMsg(closures) })
  }

  /** Replace the road features and weather. */
  setFeatures(features: RoadFeatures) {
    this.send({ type: 'features', features: toServer(features) })
  }

  /** Run these timings on a signal; null = back to its default program. */
  setSignal(id: string, plan: SignalPlan | null) {
    this.send({ type: 'signal', id, plan })
  }

  setDemand(demand: Demand) {
    this.send({ type: 'demand', ...demand })
  }

  setSpeed(value: number) {
    this.send({ type: 'speed', value })
  }

  pause() {
    this.send({ type: 'pause' })
  }

  play() {
    this.send({ type: 'play' })
  }

  close() {
    this.closedByUs = true
    this.ws?.close()
    this.ws = null
  }
}
