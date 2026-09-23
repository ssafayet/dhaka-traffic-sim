import type { Demand, Frame, SimOptions, SimState } from './types'

type ServerMsg =
  | Frame
  | { type: 'status'; state: 'starting' | 'running' | 'paused' | 'stopped' }
  | { type: 'error'; message: string }

export interface SimClientHandlers {
  onFrame: (frame: Frame) => void
  onState: (state: SimState, message?: string) => void
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

  start(area: string, demand: Demand, options: SimOptions, speed: number, warmup: number) {
    this.send({ type: 'start', area, demand, options, speed, warmup })
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
