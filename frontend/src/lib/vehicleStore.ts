import type { Frame } from './types'

/**
 * Holds the two most recent frames and interpolates between them so vehicles
 * glide at 60 fps even though the server sends 1–20 frames per second.
 */
export class VehicleStore {
  n = 0
  ids = new Int32Array(0)
  kind = new Uint8Array(0)
  speed = new Float32Array(0)
  // interpolation endpoints
  private fromLon = new Float64Array(0)
  private fromLat = new Float64Array(0)
  private fromAng = new Float32Array(0)
  private toLon = new Float64Array(0)
  private toLat = new Float64Array(0)
  private toAng = new Float32Array(0)
  // sampled output, [lon, lat] interleaved for deck.gl binary attributes
  positions = new Float64Array(0)
  angles = new Float32Array(0)

  private start = 0
  private duration = 1000
  private lastArrival = 0
  version = 0

  clear() {
    this.n = 0
    this.version++
  }

  ingest(frame: Frame, now: number) {
    // Frames arrive at an interval that depends on sim speed; interpolate
    // over a smoothed estimate of it.
    if (this.lastArrival) {
      const gap = now - this.lastArrival
      this.duration = Math.min(1500, Math.max(30, this.duration * 0.7 + gap * 0.3))
    }
    this.lastArrival = now

    // Where each vehicle is drawn right now becomes the new starting point.
    this.sample(now)
    const prevIndex = new Map<number, number>()
    for (let i = 0; i < this.n; i++) prevIndex.set(this.ids[i], i)
    const prevPos = this.positions
    const prevAng = this.angles

    const n = frame.ids.length
    const fromLon = new Float64Array(n)
    const fromLat = new Float64Array(n)
    const fromAng = new Float32Array(n)
    for (let i = 0; i < n; i++) {
      const j = prevIndex.get(frame.ids[i])
      const lon = frame.lon[i]
      const lat = frame.lat[i]
      // Snap new vehicles and big jumps (teleports) instead of sliding them.
      if (j !== undefined && Math.abs(prevPos[2 * j] - lon) + Math.abs(prevPos[2 * j + 1] - lat) < 0.002) {
        fromLon[i] = prevPos[2 * j]
        fromLat[i] = prevPos[2 * j + 1]
        fromAng[i] = prevAng[j]
      } else {
        fromLon[i] = lon
        fromLat[i] = lat
        fromAng[i] = frame.angle[i]
      }
    }
    this.n = n
    this.ids = Int32Array.from(frame.ids)
    this.kind = Uint8Array.from(frame.kind)
    this.speed = Float32Array.from(frame.speed)
    this.fromLon = fromLon
    this.fromLat = fromLat
    this.fromAng = fromAng
    this.toLon = Float64Array.from(frame.lon)
    this.toLat = Float64Array.from(frame.lat)
    this.toAng = Float32Array.from(frame.angle)
    this.positions = new Float64Array(2 * n)
    this.angles = new Float32Array(n)
    this.start = now
    this.version++
  }

  /** Fill `positions` / `angles` for time `now`. */
  sample(now: number) {
    const n = this.n
    if (this.positions.length !== 2 * n) {
      this.positions = new Float64Array(2 * n)
      this.angles = new Float32Array(n)
    }
    const t = Math.min(1, (now - this.start) / this.duration)
    for (let i = 0; i < n; i++) {
      this.positions[2 * i] = this.fromLon[i] + (this.toLon[i] - this.fromLon[i]) * t
      this.positions[2 * i + 1] = this.fromLat[i] + (this.toLat[i] - this.fromLat[i]) * t
      let d = this.toAng[i] - this.fromAng[i]
      if (d > 180) d -= 360
      else if (d < -180) d += 360
      this.angles[i] = this.fromAng[i] + d * t
    }
  }
}
