import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { CONGESTION, vehicleColor, type Theme } from '../lib/palette'
import { SOURCE_URL } from '../lib/project'
import type { Area, Preset, VehicleType } from '../lib/types'

/** Plain-language guide for planners and the public, at #docs. */

// The reports behind the default waterlogging spots (backend waterlogging.json).
const NEWS = [
  ['The Daily Star', '12 July 2024', "Parts of Dhaka, including thoroughfares, waterlogged after 'very heavy' morning rain", 'https://www.thedailystar.net/environment/weather/news/parts-dhaka-including-thoroughfares-waterlogged-after-very-heavy-morning-rain-3655066'],
  ['Dhaka Tribune', '22 September 2025', 'Overnight downpour floods Dhaka, commuters struggle through gridlocks', 'https://www.dhakatribune.com/bangladesh/dhaka/392098/persistent-rainfall-submerges-dhaka-streets'],
  ['Prothom Alo', '22 September 2025', 'Heavy rain since morning leads to waterlogging in Dhaka streets', 'https://en.prothomalo.com/bangladesh/city/uclhk1b76t'],
  ['Prothom Alo', '1 May 2026', 'Heavy downpour at dawn leaves roads waterlogged across Dhaka', 'https://en.prothomalo.com/bangladesh/city/iwjax7z6hw'],
  ['The Daily Star', '10 May 2026', '141 Dhaka spots at risk of waterlogging', 'https://www.thedailystar.net/news/bangladesh/news/141-dhaka-spots-risk-waterlogging-4171926'],
  ['Dhaka Tribune', '14 July 2026', 'Why Dhaka keeps flooding after every heavy rain', 'https://www.dhakatribune.com/bangladesh/dhaka/415068/why-dhaka-keeps-flooding-after-every-heavy-rain'],
  ['The Business Standard', '20 September 2026', 'Dhaka streets waterlogged after downpour, commuters suffer', 'https://www.tbsnews.net/bangladesh/heavy-rain-floods-parts-capital-causing-widespread-suffering-1547816'],
] as const

const SECTIONS = [
  ['unique', 'What makes it different'],
  ['features', 'Everything it can do'],
  ['what', 'What this is'],
  ['use', 'How to use it'],
  ['results', 'Reading the results'],
  ['edits', 'Changing the roads'],
  ['method', 'How it works'],
  ['limits', 'Limitations'],
  ['sources', 'Sources and credits'],
] as const

function useTheme(): Theme {
  const q = '(prefers-color-scheme: dark)'
  const [theme, setTheme] = useState<Theme>(() => (matchMedia(q).matches ? 'dark' : 'light'))
  useEffect(() => {
    const m = matchMedia(q)
    const on = () => setTheme(m.matches ? 'dark' : 'light')
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return theme
}

/** Sections are addressed as #docs/<id>, so the page route survives. */
function useSectionScroll() {
  useEffect(() => {
    const go = () => {
      const id = location.hash.replace(/^#docs\/?/, '')
      if (id) document.getElementById(id)?.scrollIntoView({ block: 'start' })
    }
    go()
    window.addEventListener('hashchange', go)
    return () => window.removeEventListener('hashchange', go)
  }, [])
}

export default function Docs() {
  const theme = useTheme()
  const [types, setTypes] = useState<VehicleType[]>([])
  const [presets, setPresets] = useState<Preset[]>([])
  const [areas, setAreas] = useState<Area[]>([])
  useSectionScroll()
  useEffect(() => {
    document.title = 'How it works · Dhaka Traffic Sim'
    api.vehicleTypes().then(setTypes).catch(() => setTypes([]))
    // The guide describes the default region (Dhaka).
    api
      .regions()
      .then((r) => setPresets(r.regions.find((x) => x.id === r.default)?.presets ?? []))
      .catch(() => setPresets([]))
    api.areas().then((a) => setAreas(a.areas)).catch(() => setAreas([]))
  }, [])
  const neighbourhoods = areas.filter((a) => a.kind !== 'city')

  return (
    <div className="docs">
      <header className="docs-bar">
        <a className="docs-brand" href="/">
          Dhaka Traffic Sim
        </a>
        <a className="docs-open" href="/">
          Open the simulator
        </a>
      </header>

      <div className="docs-body">
        <nav className="docs-toc" aria-label="On this page">
          <p className="docs-toc-title">On this page</p>
          <ol>
            {SECTIONS.map(([id, label]) => (
              <li key={id}>
                <a href={`#docs/${id}`}>{label}</a>
              </li>
            ))}
          </ol>
        </nav>

        <article className="docs-article">
          <img
            className="docs-banner"
            src="/banner.png"
            alt="Dhaka Traffic Sim: a dark map of central Dhaka with roads coloured by congestion"
            width={2169}
            height={725}
          />
          <h1>Dhaka Traffic Sim: how it works and how to use it</h1>
          <p className="docs-lead">
            A what-if tool for Dhaka's streets. It simulates individual cars, buses, CNGs, rickshaws and motorcycles on the city's real
            road map, so you can see what happens when a road closes, a signal is retimed or a U-turn is added, and compare it with how
            things are now.
          </p>
          <ul className="docs-stats" aria-label="At a glance">
            <li>
              <strong>{neighbourhoods.length || 15}</strong> neighbourhoods, every street, plus the whole city
            </li>
            <li>
              <strong>{types.length || 7}</strong> vehicle types, each driven individually
            </li>
            <li>
              <strong>52</strong> real waterlogging spots from the news
            </li>
            <li>
              <strong>12</strong> kinds of road change, most applied live
            </li>
          </ul>

          <section id="unique">
            <h2>What makes it different</h2>
            <p>
              Most traffic tools either colour roads from phone data, which shows today but can't answer "what if", or are professional
              packages built for European and American traffic. This one is a what-if simulator made for Dhaka.
            </p>
            <div className="docs-highlights">
              <div className="docs-highlight">
                <h3>Drives like Dhaka</h3>
                <p>
                  Motorcycles, CNGs and rickshaws weave through gaps instead of keeping to lanes, drivers nose into small gaps at
                  junctions, and rickshaws use main roads despite the ban. Switch each of these off to see how much it matters.
                </p>
                <a href="#docs/method">Dhaka-style driving</a>
              </div>
              <div className="docs-highlight">
                <h3>Battery rickshaws included</h3>
                <p>
                  The vehicle mix starts from the 2023 RSTP road survey and counts battery rickshaws, which now outnumber pedal
                  rickshaws, as a vehicle type of their own with their own speed and size.
                </p>
                <a href="#docs/method">Vehicles</a>
              </div>
              <div className="docs-highlight">
                <h3>Flooding taken from the news</h3>
                <p>
                  52 places reported waterlogged in the 2024–26 rainy seasons are already on the map, each linked to its news report.
                  Knee-deep water turns back motorcycles and CNGs; waist-deep water turns back cars too.
                </p>
                <a href="#docs/weather">Weather and waterlogging</a>
              </div>
              <div className="docs-highlight">
                <h3>Congestion that builds up by itself</h3>
                <p>
                  Nobody paints a road red. Every car, bus and rickshaw has its own trip and reacts to the vehicles around it, so jams
                  form, spread and clear the way they do on real streets.
                </p>
                <a href="#docs/what">What this is</a>
              </div>
              <div className="docs-highlight">
                <h3>Change roads while it runs</h3>
                <p>
                  Close a lane, retime a signal or flood a junction and watch traffic react within seconds. Bigger changes, such as a new
                  U-turn in the median or a no-right-turn rule, restart on the rebuilt road network in about a second.
                </p>
                <a href="#docs/edits">Changing the roads</a>
              </div>
              <div className="docs-highlight">
                <h3>Put what you know on the map</h3>
                <p>
                  Bus stops that block the kerb lane, rickshaw stands, mid-block crossings, markets and schools that draw crowds. The
                  things that make a Dhaka street slow can all be placed and tuned.
                </p>
                <a href="#docs/edits">Things you place on the map</a>
              </div>
              <div className="docs-highlight">
                <h3>Nothing to install</h3>
                <p>
                  It runs in the browser on free, open data: OpenStreetMap roads and SUMO, the open-source simulator used by researchers
                  and cities worldwide. Share the link and anyone can try a scenario.
                </p>
                <a href="#docs/sources">Sources and credits</a>
              </div>
              <div className="docs-highlight">
                <h3>Honest about what it can't do</h3>
                <p>
                  The volumes are informed estimates, not measurements, and this page says so. Use it to compare options side by side;
                  every rule it uses is written down below.
                </p>
                <a href="#docs/limits">Limitations</a>
              </div>
            </div>
          </section>

          <section id="features">
            <h2>Everything it can do</h2>
            <div className="docs-table-wrap">
              <table className="docs-table docs-features">
                <tbody>
                  <tr>
                    <th scope="row">
                      <a href="#docs/use">Areas</a>
                    </th>
                    <td>
                      The whole city (main roads, about 1,500 km) or a 3 × 3 km neighbourhood with every street
                      {neighbourhoods.length > 0 && <>: {neighbourhoods.map((a) => a.name.split(' – ')[0]).join(', ')}</>}. Left-hand traffic throughout.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/method">Time of day</a>
                    </th>
                    <td>
                      {presets.length > 0 ? `${presets.length} presets` : 'Presets'} from morning rush to night-time trucks, each with its
                      own traffic volume, vehicle mix and share of through traffic. Fine-tune volume and mix with sliders while it runs.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/method">Vehicles</a>
                    </th>
                    <td>
                      Cars, buses, CNGs, battery rickshaws, pedal rickshaws, motorcycles and trucks, each with its own size, speed and
                      driving style, drawn to scale on the map.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/method">Driving behaviour</a>
                    </th>
                    <td>
                      Lane-free weaving, Dhaka-style gap-taking, rickshaws on main roads, drivers re-planning around traffic every 2 or 5
                      minutes, and how long a stuck vehicle waits before it is removed.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/edits">Live road changes</a>
                    </th>
                    <td>
                      Close a road, one direction or single lanes, all day or between two times. Retime signals as actuated, fixed time
                      or off.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/edits">Layout changes</a>
                    </th>
                    <td>
                      Add mid-block U-turns, restrict turns at a junction, allow or ban U-turns, and add new traffic signals. Closures and
                      timings carry over to the new layout.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/edits">On the map</a>
                    </th>
                    <td>Bus stops, rickshaw and CNG stands, pedestrian crossings, hot zones and waterlogging zones.</td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/weather">Weather and incidents</a>
                    </th>
                    <td>
                      Light or heavy rain, flooding at three depths, random breakdowns, and fewer trips starting when roads are slow.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/results">Results</a>
                    </th>
                    <td>
                      Roads coloured by congestion, eight live numbers from average speed to delay and throughput, charts over time, and
                      details for any road, vehicle or signal on hover.
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <a href="#docs/use">Speed</a>
                    </th>
                    <td>Pause, or run at 1×, 2×, 5×, 10× or as fast as the computer allows.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section id="what">
            <h2>What this is</h2>
            <p>
              The simulator draws every vehicle on a live map and moves it second by second. Each one sets off from somewhere, heads for a
              destination, and has to share the road with everyone else: it waits at signals, queues behind buses and squeezes through
              gaps. Congestion isn't painted on; it builds up from thousands of vehicles getting in each other's way.
            </p>
            <p>You can use it to explore questions such as:</p>
            <ul>
              <li>How much worse does the evening rush get if this road is closed for construction?</li>
              <li>Does opening a U-turn in the median here help, or does it just move the queue?</li>
              <li>What happens if this junction only allows straight on and left turns?</li>
              <li>Would a longer green on the main road clear the queue, or block the side streets?</li>
              <li>How do battery rickshaws on main roads change travel times?</li>
              <li>How much does heavy rain slow the area, and which flooded spots hurt most?</li>
              <li>Does moving a bus stop or a rickshaw stand away from a junction free it up?</li>
            </ul>
            <div className="docs-callout">
              <strong>Use it to compare, not to predict.</strong> The traffic volumes and driver behaviour are informed estimates, not
              measurements (see <a href="#docs/limits">Limitations</a>). The simulator is good at telling you that one option is clearly
              better or worse than another. It can't tell you exactly how many minutes a trip will take on a given day.
            </div>
          </section>

          <section id="use">
            <h2>How to use it</h2>
            <ol className="docs-steps">
              <li>
                <strong>Pick an area.</strong> Neighbourhoods such as Farmgate, Motijheel or Uttara cover about 3 × 3 km and include every
                street. <em>Whole city</em> covers all of Dhaka North and South, but only the main roads.
              </li>
              <li>
                <strong>Pick a time of day.</strong> Each preset sets how many vehicles enter per hour and the mix of vehicle types. You
                can fine-tune both with the sliders, and changes apply while the simulation runs.
              </li>
              <li>
                <strong>Start the simulation.</strong> The first five simulated minutes run at top speed to fill the empty roads, shown as
                “warming up”. After that the clock runs at the speed you choose (1× is real time).
              </li>
              <li>
                <strong>Watch and read the numbers.</strong> Roads are coloured by how congested they are, and the panel under the map
                shows speeds, delays and throughput. Hover over a road or vehicle for details.
              </li>
              <li>
                <strong>Set the weather.</strong> Under <em>Weather and incidents</em>, choose rain, flooding, breakdowns and how much
                people avoid delay.
              </li>
              <li>
                <strong>Change the roads.</strong> Press <em>Edit roads on the map</em>, then use the tools above the map or click a road,
                junction or signal. See <a href="#docs/edits">Changing the roads</a>.
              </li>
              <li>
                <strong>Compare.</strong> Note the numbers before and after your change, or restart with and without it.
              </li>
            </ol>
            <h3>Tips for a fair comparison</h3>
            <ul>
              <li>
                Run the unchanged roads first as your baseline, and let it settle for 15–20 simulated minutes before reading numbers.
              </li>
              <li>Change one thing at a time, and keep the same preset, area and settings for both runs.</li>
              <li>
                Look at trends in the charts rather than a single moment; traffic fluctuates. Differences of a few percent are within the
                noise.
              </li>
              <li>
                <em>Delay so far</em> is the most reliable sign of gridlock: vehicles stuck in a jam never finish their trip, so they never
                show up in <em>Trip time</em>.
              </li>
            </ul>
          </section>

          <section id="results">
            <h2>Reading the results</h2>
            <h3>Road colours</h3>
            <p>Each road is coloured by how fast vehicles are moving compared with how fast they could go there:</p>
            <ul className="docs-keys">
              {CONGESTION.map((c, i) => (
                <li key={c.label}>
                  <span className="line-key" style={{ background: c.color }} aria-hidden />
                  <strong>{c.label}</strong>
                  <span className="muted">
                    {' '}
                    ·{' '}
                    {i === 0
                      ? 'at least 75% of free speed'
                      : i === CONGESTION.length - 1
                        ? 'below 25% of free speed'
                        : `${Math.round(c.min * 100)}–${Math.round(CONGESTION[i - 1].min * 100)}% of free speed`}
                  </span>
                </li>
              ))}
            </ul>
            <p>
              Speeds are compared with each vehicle's own top speed, so a road full of free-flowing pedal rickshaws isn't shown as a jam.
              Grey roads have no traffic at the moment.
            </p>
            <h3>The numbers</h3>
            <dl className="docs-defs">
              <dt>On the road</dt>
              <dd>Vehicles currently driving in the area.</dd>
              <dt>Average speed</dt>
              <dd>Mean speed of all vehicles on the road, including those standing still.</dd>
              <dt>Standing still</dt>
              <dd>Share of vehicles barely moving (under 2 km/h).</dd>
              <dt>Delay so far</dt>
              <dd>
                Average time lost compared with an empty road, for vehicles still on the road. The best single indicator of how bad
                congestion is.
              </dd>
              <dt>Trip time</dt>
              <dd>Average duration of trips completed in the last 10 minutes.</dd>
              <dt>Throughput</dt>
              <dd>Trips completed per hour, over the last 10 minutes.</dd>
              <dt>Waiting to enter</dt>
              <dd>Vehicles that can't get onto the network because the road they start on is full. A rising number means gridlock.</dd>
              <dt>Removed as stuck</dt>
              <dd>
                Vehicles stuck for longer than the limit you set (5 minutes by default) are taken off the road, to stop one blockage
                freezing the whole area. Choose <em>never</em> under Driving behaviour to see a true gridlock.
              </dd>
            </dl>
          </section>

          <section id="edits">
            <h2>Changing the roads</h2>
            <p>
              Press <em>Edit roads on the map</em> in the side panel. Junctions appear as small circles once you zoom in, and signals as
              coloured dots that follow the live signal phase. Click anything to open its settings. Every change is listed under{' '}
              <em>Road changes</em>, where you can jump to it or undo it.
            </p>
            <p>
              You decide how true to life the setup is. The tools let you put on the map what you know about a place: where buses
              stop, where rickshaws wait, where people cross, where markets draw crowds and where water collects.
            </p>
            <h3>Things you place on the map</h3>
            <p>
              Pick a tool above the map, then click where it goes. Points snap to the nearest road. Click anything you've placed to adjust
              or remove it. All of these apply at once.
            </p>
            <ul>
              <li>
                <strong>Bus stop.</strong> Buses whose route passes it stop in the kerb lane for the time you set, and traffic behind
                them has to wait or go round. The bus stops recorded in OpenStreetMap are included to start with (there are few).
              </li>
              <li>
                <strong>Rickshaw or CNG stand.</strong> A number of battery rickshaws, pedal rickshaws or CNGs parked at the kerb, plus a
                share of passing ones stopping there for a fare. On a one-lane street, parked vehicles leave room only for motorcycles and
                rickshaws.
              </li>
              <li>
                <strong>Pedestrian crossing.</strong> Every so often a group of people crosses and holds up every lane in both
                directions for a few seconds. Use it for people crossing mid-block, outside schools or at bus stops.
              </li>
              <li>
                <strong>Hot zone</strong>, a circle around a market, school, hospital or terminal. More trips start and end inside it;
                buses, CNGs and rickshaws stop at the kerb more often; and you can have vendors take the kerb lane and crowds slow
                traffic down.
              </li>
              <li>
                <strong>Waterlogging</strong>, a circle that floods: ankle-, knee- or waist-deep. See{' '}
                <a href="#docs/weather">Weather and waterlogging</a>.
              </li>
            </ul>
            <h3>Changes to roads and signals that apply immediately</h3>
            <ul>
              <li>
                <strong>Close a road</strong>: one direction, both directions of a two-way street, or single lanes, for example for
                construction or parked vehicles. Lane 1 is the kerb side. A closure can apply all the time or between two times of day
                (an event, a VIP movement, an accident), and you can note why. Vehicles heading for the closure pick another route; any
                that have no other way to their destination queue at the closure, and the map tells you how many.
              </li>
              <li>
                <strong>Retime a signal</strong>: choose <em>actuated</em> (each green stretches between a minimum and a maximum while
                traffic keeps arriving; the default), <em>fixed time</em> (every phase runs for a set time), or <em>off</em> (drivers
                fall back to the junction's normal right of way). Each phase lists which approaches get green. Signals outside
                Dhaka's automatic corridors start off, as traffic police direct them; switch one on to run it automatically.
              </li>
            </ul>
            <h3>Changes to the road layout</h3>
            <p>
              These change the shape of the network, so they are collected as a list and applied together with{' '}
              <em>Apply layout changes &amp; restart</em>. The simulation then restarts on the new layout, and your closures and signal
              timings carry over.
            </p>
            <ul>
              <li>
                <strong>Add a U-turn</strong> at the point you click. On a divided road this opens a gap in the median to the other side;
                on an ordinary two-way street vehicles turn around where you clicked. It can serve one direction or both.
              </li>
              <li>
                <strong>Restrict turns at a junction</strong>: <em>straight and left only</em> (no right turns or U-turns) or{' '}
                <em>left only</em>. A crossing of divided roads is often mapped as two to four junctions a few metres apart; the rule
                treats them as one crossing.
              </li>
              <li>
                <strong>Allow or ban U-turns</strong> at a junction.
              </li>
              <li>
                <strong>Add a traffic signal</strong> to a junction that has none. It starts with an actuated plan that you can tune once
                applied.
              </li>
            </ul>
            <p className="muted">
              Edits belong to your browser tab only; other people using the simulator don't see them. Reloading the page clears them.
            </p>
          </section>

          <section id="method">
            <h2>How it works</h2>
            <ol className="docs-flow" aria-label="From map data to results">
              <li>OpenStreetMap roads</li>
              <li>Road network with lanes, junctions and signals</li>
              <li>Vehicles and trips</li>
              <li>Second-by-second simulation</li>
              <li>Map and numbers</li>
            </ol>

            <h3>The road network</h3>
            <p>
              Roads come from <a href="https://www.openstreetmap.org/">OpenStreetMap</a>, the free map made by volunteers. They are
              converted into a network of lanes and junctions by{' '}
              <a href="https://eclipse.dev/sumo/">SUMO</a>, the open-source traffic simulator developed by the German Aerospace Center
              (DLR) and used by researchers and cities worldwide. Vehicles drive on the left. Footpaths, parking aisles and driveways are
              left out.
            </p>
            <p>
              Neighbourhood areas include every street. The whole-city network keeps only main roads (motorways down to tertiary roads,
              about 1,500 km), because all of Dhaka's lanes and alleys would be far too many to simulate live.
            </p>
            <p>
              Traffic signals are placed where OpenStreetMap marks them, merged when a junction has several. In practice traffic
              police direct most of Dhaka's junctions, so only signals on the automatic corridors run by default: Shahbag – Bangla
              Motor – Karwan Bazar – Farmgate – Bijoy Sarani – PMO – Jahangir Gate – Mohakhali railgate, Gulshan 1 and 2 circles,
              and the junctions in Dhaka Cantonment. Where OpenStreetMap has no signal on those, the simulator adds one. Every other
              signal starts switched off, and you can switch any signal on or off.
            </p>

            <h3>Vehicles</h3>
            <p>
              Seven vehicle types, each with its own size, top speed, acceleration and driving style. The shares below are the default
              mix; presets adjust them by time of day.
            </p>
            {types.length > 0 && (
              <div className="docs-table-wrap">
                <table className="docs-table">
                  <thead>
                    <tr>
                      <th scope="col">Type</th>
                      <th scope="col">Default share</th>
                      <th scope="col">Length</th>
                      <th scope="col">Top speed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {types.map((t) => (
                      <tr key={t.id}>
                        <th scope="row">
                          <span className="dot-key" style={{ background: vehicleColor(theme, t.id) }} aria-hidden /> {t.label}
                        </th>
                        <td>{t.default_share}%</td>
                        <td>{t.length.toFixed(1)} m</td>
                        <td>{t.max_speed_kmh} km/h</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p>
              The mix starts from the 2023 Revised Strategic Transport Plan (RSTP) road survey and is shifted towards battery rickshaws,
              which have outnumbered pedal rickshaws since 2025.
            </p>

            <h3>Dhaka-style driving</h3>
            <ul>
              <li>
                <strong>Lane-free movement.</strong> Motorcycles, CNGs and rickshaws weave into gaps instead of keeping to lanes. You can
                switch this off to compare with lane-based traffic; it is also several times faster to simulate.
              </li>
              <li>
                <strong>Gap-taking at junctions.</strong> Drivers accept smaller gaps than in European traffic, nose into slow cross
                traffic and go through just after the light turns red. Without this, polite yielding deadlocks busy junctions that in
                Dhaka keep moving.
              </li>
              <li>
                <strong>Rickshaws on main roads.</strong> Officially banned from main roads such as Mirpur Road and Progoti Sarani, but in
                practice they are everywhere, so they are allowed by default. Turn this off and they detour through side streets.
              </li>
            </ul>

            <h3>Traffic demand</h3>
            <p>
              Vehicles are added continuously at the rate you set, in vehicles per hour. Some are <em>through traffic</em>: they enter
              where a road crosses the edge of the area and leave at another edge, which is typical for a neighbourhood that main roads
              pass through. The rest start or end on streets inside the area, with bigger roads getting more trips.
            </p>
            {presets.length > 0 && (
              <div className="docs-table-wrap">
                <table className="docs-table">
                  <thead>
                    <tr>
                      <th scope="col">Preset</th>
                      <th scope="col">Starts</th>
                      <th scope="col">Vehicles per hour (Farmgate)</th>
                      <th scope="col">Through traffic</th>
                    </tr>
                  </thead>
                  <tbody>
                    {presets.map((p) => (
                      <tr key={p.id}>
                        <th scope="row">
                          {p.label}
                          <span className="muted small block">{p.description}</span>
                        </th>
                        <td>{String(Math.floor(p.start_hour)).padStart(2, '0')}:00</td>
                        <td>{p.volume.toLocaleString()}</td>
                        <td>{Math.round(p.through_share * 100)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p>
              Volumes were tuned on Farmgate and are scaled to other areas by how much main road they have. The whole city also has much
              less through traffic, since most trips start and end inside it.
            </p>

            <h3>Routes</h3>
            <p>
              Each vehicle picks the quickest route to its destination when it sets off. It is sent another way if a road ahead closes or
              floods too deep for it. With <em>Drivers re-plan around traffic</em> switched on, every driver also checks for a quicker
              route every 2 or 5 minutes, using how fast traffic is moving at that moment.
            </p>

            <h3 id="weather">Weather and waterlogging</h3>
            <p>
              Rain makes drivers go slower and leave longer gaps: about 10% slower in light rain and 25% slower in heavy rain, with gaps
              25% and 60% longer. Flooded roads then slow traffic further, or keep some vehicles out, depending on the depth:
            </p>
            <div className="docs-table-wrap">
              <table className="docs-table">
                <thead>
                  <tr>
                    <th scope="col">Depth</th>
                    <th scope="col">Top speed</th>
                    <th scope="col">Can't get through</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Ankle-deep</th>
                    <td>20 km/h</td>
                    <td>—</td>
                  </tr>
                  <tr>
                    <th scope="row">Knee-deep</th>
                    <td>10 km/h</td>
                    <td>Motorcycles, CNGs</td>
                  </tr>
                  <tr>
                    <th scope="row">Waist-deep</th>
                    <td>5 km/h</td>
                    <td>Motorcycles, CNGs, cars</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>
              Vehicles that can't get through are rerouted; those with no other way wait at the water's edge. By default the zones flood
              when rain is heavy; you can also keep them flooded after the rain stops, or switch flooding off.
            </p>
            <p>
              Each area starts with the places reported waterlogged in Dhaka's 2024, 2025 and 2026 rainy seasons (52 across the city),
              from the news reports listed under <a href="#docs/sources">Sources</a>. Each zone takes the worst depth reported there. The
              positions are approximate, at the level of a neighbourhood or an intersection. Click a zone to see its sources, change its
              depth or size, or switch it off, and add your own where you know water collects.
            </p>

            <h3>Incidents and behaviour</h3>
            <ul>
              <li>
                <strong>Breakdowns.</strong> Vehicles break down at random at the rate you choose and block their lane for 10–30
                minutes. A red warning marker shows where.
              </li>
              <li>
                <strong>Fewer trips when roads are slow.</strong> With this on, fewer trips start as average delays grow (people put
                trips off, go later or go another way). <em>A little</em> means 1% fewer trips for every minute of average delay;{' '}
                <em>a lot</em> means 3%. The panel shows how many fewer are starting right now.
              </li>
            </ul>

            <h3>Statistics</h3>
            <p>
              Trip time and throughput use trips completed in the last 10 minutes. Road colours are smoothed over a few seconds. The
              first 5 simulated minutes are a warm-up that fills the roads.
            </p>
          </section>

          <section id="limits">
            <h2>Limitations</h2>
            <p>Keep these in mind when you draw conclusions.</p>
            <h3>The numbers are not calibrated</h3>
            <ul>
              <li>
                Traffic volumes and driver behaviour are informed estimates, tuned so that familiar patterns appear (fairly free at midday,
                gridlock at rush hour). They have not been checked against real vehicle counts or travel times. Treat results as
                comparisons between scenarios, not forecasts.
              </li>
              <li>
                Where trips start and end is spread across the area in proportion to road size, not real origin–destination data. Hot
                zones let you add the places you know draw traffic.
              </li>
              <li>
                The effects of rain, water depth, stands, crossings and hot zones are simple, stated rules (see{' '}
                <a href="#docs/method">How it works</a>), not measurements. The default waterlogging spots come from news reports, with
                approximate positions and the worst reported depth.
              </li>
            </ul>
            <h3>The map is only as good as OpenStreetMap</h3>
            <ul>
              <li>Missing or outdated roads, wrong lane counts or one-way errors in OpenStreetMap carry straight into the simulation.</li>
              <li>
                Turn restrictions recorded in OpenStreetMap (such as “no right turn”) are not imported yet; only the road layout decides
                which turns are possible. Use the junction turn rules to add restrictions you know about.
              </li>
              <li>
                Police-directed junctions are simulated as signals switched off (drivers follow the right of way), not the way an
                officer holds and releases each approach. The automatic-corridor zones are approximate.
              </li>
            </ul>
            <h3>Simplified, or not simulated</h3>
            <ul>
              <li>
                Pedestrians aren't simulated one by one. Crossing spots hold up the road for a set time, and crowds in hot zones slow
                traffic by a set amount.
              </li>
              <li>Buses don't follow real bus routes; each makes a random trip and stops at the bus stops along its way.</li>
              <li>
                In deep water, battery and pedal rickshaws are treated alike. Both get through, although battery rickshaws often stall
                in practice.
              </li>
              <li>
                People don't switch between modes of travel, for example from CNG to bus. <em>Fewer trips when roads are slow</em>{' '}
                only reduces the number of trips.
              </li>
              <li>Accidents only happen as random breakdowns or as closures you place; there is no crash model.</li>
            </ul>
            <h3>Area and scale</h3>
            <ul>
              <li>
                Each area is simulated on its own, as an island. Queues that would spill in from neighbouring areas, or back out of this
                one, are cut off at the edge.
              </li>
              <li>
                The whole-city network has main roads only. On a typical computer it runs in real time up to about 30,000 vehicles on
                the road at once; beyond that the clock slows down. A real Dhaka peak hour, with a few hundred thousand vehicles at once,
                is out of reach for now.
              </li>
              <li>
                Each browser tab runs its own simulation, and the server allows only a few at a time. If it is busy you'll see a
                message; try again later.
              </li>
            </ul>
          </section>

          <section id="sources">
            <h2>Sources and credits</h2>
            <ul>
              <li>
                Dhaka Traffic Sim by SSafayet. Open source under the GNU AGPL-3.0:{' '}
                <a href={SOURCE_URL}>source code on GitHub</a>.
              </li>
              <li>
                Road data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>, available under the Open
                Database Licence.
              </li>
              <li>
                Simulation: <a href="https://eclipse.dev/sumo/">Eclipse SUMO</a> (Simulation of Urban MObility).
              </li>
              <li>
                Base map: <a href="https://openfreemap.org/">OpenFreeMap</a>, with <a href="https://openmaptiles.org/">OpenMapTiles</a>.
              </li>
              <li>
                Vehicle mix: 2023 RSTP road survey for Dhaka, adjusted with 2024–26 news reports on battery rickshaws.
              </li>
              <li>
                Waterlogging spots:
                <ul>
                  {NEWS.map(([outlet, date, title, url]) => (
                    <li key={url}>
                      {outlet}, {date}: <a href={url}>{title}</a>
                    </li>
                  ))}
                </ul>
              </li>
            </ul>
          </section>
        </article>
      </div>
    </div>
  )
}
