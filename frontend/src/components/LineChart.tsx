import { useLayoutEffect, useRef, useState } from 'react'

interface Props {
  title: string
  unit: string
  points: { t: number; v: number }[]
  formatX: (t: number) => string
  color: string
  height?: number
}

/** Single-series time chart with crosshair tooltip. */
export function LineChart({ title, unit, points, formatX, color, height = 96 }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(280)
  const [hover, setHover] = useState<number | null>(null)

  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(120, e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const padL = 34
  const padR = 8
  const padT = 8
  const padB = 18
  const w = width - padL - padR
  const h = height - padT - padB

  const t0 = points[0]?.t ?? 0
  const t1 = Math.max(t0 + 60, points[points.length - 1]?.t ?? 60)
  const vmax = niceMax(Math.max(1, ...points.map((p) => p.v)))
  const x = (t: number) => padL + ((t - t0) / (t1 - t0)) * w
  const y = (v: number) => padT + h - (v / vmax) * h

  const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('')
  const hp = hover !== null ? points[hover] : null
  const last = points[points.length - 1]

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!points.length) return
    const rect = e.currentTarget.getBoundingClientRect()
    const t = t0 + ((e.clientX - rect.left - padL) / w) * (t1 - t0)
    let best = 0
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i].t - t) < Math.abs(points[best].t - t)) best = i
    setHover(best)
  }

  return (
    <figure className="chart" ref={wrap}>
      <figcaption>
        <span>{title}</span>
        <span className="chart-now">{last ? `${fmt(last.v)} ${unit}` : '—'}</span>
      </figcaption>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={`${title} over simulated time`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="grid" x1={padL} x2={padL + w} y1={y(vmax * f)} y2={y(vmax * f)} />
            <text className="tick" x={padL - 5} y={y(vmax * f) + 3} textAnchor="end">
              {fmt(vmax * f)}
            </text>
          </g>
        ))}
        <text className="tick" x={padL} y={height - 4}>
          {formatX(t0)}
        </text>
        <text className="tick" x={padL + w} y={height - 4} textAnchor="end">
          {formatX(t1)}
        </text>
        {points.length > 1 && <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />}
        {hp && (
          <g>
            <line className="crosshair" x1={x(hp.t)} x2={x(hp.t)} y1={padT} y2={padT + h} />
            <circle cx={x(hp.t)} cy={y(hp.v)} r={4} fill={color} stroke="var(--surface-1)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {hp && (
        <div
          className="chart-tip"
          style={{ left: Math.min(width - 110, Math.max(0, x(hp.t) - 50)) }}
          role="status"
        >
          <span>{formatX(hp.t)}</span>
          <strong>
            {fmt(hp.v)} {unit}
          </strong>
        </div>
      )}
    </figure>
  )
}

function niceMax(v: number) {
  const p = 10 ** Math.floor(Math.log10(v))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p
  return 10 * p
}

function fmt(v: number) {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v >= 100 ? Math.round(v).toString() : (Math.round(v * 10) / 10).toString()
}
