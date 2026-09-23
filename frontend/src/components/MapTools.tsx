import type { Tool } from '../lib/types'

const TOOLS: { tool: Tool; label: string; hint: string }[] = [
  { tool: 'select', label: 'Select', hint: 'Click a road, junction, signal or feature to change it' },
  { tool: 'bus_stop', label: 'Bus stop', hint: 'Click on a road to add a bus stop' },
  { tool: 'stand', label: 'Stand', hint: 'Click on a road to add a rickshaw or CNG stand' },
  { tool: 'crossing', label: 'Crossing', hint: 'Click on a road where people cross' },
  { tool: 'hot_zone', label: 'Hot zone', hint: 'Click the centre of a market, school or terminal' },
  { tool: 'water', label: 'Waterlogging', hint: 'Click the centre of an area that floods' },
]

/** What a click on the map does in edit mode. */
export function MapTools({ tool, onTool }: { tool: Tool; onTool: (t: Tool) => void }) {
  const hint = TOOLS.find((t) => t.tool === tool)?.hint
  return (
    <div className="map-tools">
      <div className="seg" role="radiogroup" aria-label="Map tool">
        {TOOLS.map((t) => (
          <button key={t.tool} role="radio" aria-checked={tool === t.tool} className={tool === t.tool ? 'on' : ''} onClick={() => onTool(t.tool)}>
            {t.label}
          </button>
        ))}
      </div>
      <p className="map-tools-hint" role="status">
        {hint}
        {tool !== 'select' && <span className="muted"> · Esc to stop</span>}
      </p>
    </div>
  )
}
