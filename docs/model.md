# How the simulation works

What the simulator models, which choices are specific to Dhaka, and how to read its
numbers. A plain-language version for people using the app is built in: open *How it
works and how to use it* in the side panel, or go to `/#docs`. For how the code is put
together, see [development.md](development.md).

The engine is [SUMO](https://eclipse.dev/sumo/) on OpenStreetMap roads. Trips are
generated live: through traffic enters and leaves at the area border, and local trips
start and end on streets weighted by lane-km. So the demand sliders take effect while
the simulation runs.

## Editing roads

Turn on **Edit roads on the map** in the side panel, then click a road, junction or
signal. There are two kinds of change:

- **Live**, applied to the running simulation over TraCI:
  - Closing a road, one direction of a two-way street, or single lanes (lane 1 is
    the kerb side). Vehicles heading for a closed road are rerouted. Vehicles
    with no other way to their destination queue at the closure, and the map
    tells you how many.
  - Traffic signal timings: *actuated* (a green stretches between a minimum and
    a maximum while traffic keeps arriving), *fixed time* (set durations), or
    *off* (drivers fall back to the junction's right of way). Which movements
    get green in each phase comes from the network. Which signals start on is
    the region's choice (see below); any signal can be switched on or off.
- **Road features and weather**, also live, placed with the tools above the map:
  - Bus stops (buses stop in the kerb lane), rickshaw and CNG stands (vehicles
    parked at the kerb plus pickups), pedestrian crossing spots (people hold up
    every lane now and then), hot zones (more trips, kerbside stops, vendors,
    crowds) and waterlogging zones (ankle-, knee- or waist-deep).
  - Rain (slower, bigger gaps), flooding, random breakdowns, drivers
    re-planning around traffic (a restart option), and fewer trips starting when
    delays grow.
  - Timed closures (between two times of day) for events, VIP movements and
    accidents.

  Dhaka's default waterlogging zones are 52 places reported flooded in 2024–26 news
  reports (`backend/src/traffic_sim/regions/dhaka/waterlogging.json`, with sources).
  Bus stops start from OpenStreetMap. See `features.py`.
- **Layout**, which needs a rebuilt network and restarts the simulation:
  - Mid-block U-turns. Dhaka's main roads are mapped as two one-way
    carriageways, so a U-turn opens a gap in the median between them. On an
    undivided street, vehicles turn around at the chosen point.
  - Allowing or banning U-turns at a junction.
  - Turn rules at a junction: *straight and left only* (no right turns or
    U-turns) or *left only*. A crossing of divided roads is mapped as several
    junctions a few metres apart; the rule merges them into one junction and
    covers the whole crossing.
  - Adding a traffic signal. It starts with an actuated plan that you can tune
    once the layout is applied.

  The server applies layout edits to the area's prepared network with netconvert
  (under a second for a neighbourhood), under `backend/data/variants/`. Edits are
  always relative to the unedited area, and identical edits reuse the same
  network. Closures and signal timings carry over to the new layout.

## Choices specific to Dhaka

These live in the Dhaka region pack, `backend/src/traffic_sim/regions/dhaka/`; another
city makes its own (see [regions.md](regions.md)).

- **Left-hand traffic** (`driving_side = "left"`, which adds `netconvert --lefthand`).
- **Lane-free movement.** SUMO's sublane model lets motorcycles, CNGs and rickshaws
  filter through gaps. It can be switched off in the UI to compare with lane-based
  traffic.
- **Battery rickshaws ("Tesla").** Since 2025 they outnumber pedal rickshaws (0.5–1.2
  million in Dhaka, depending on the source). They run at 20–30 km/h (up to 40) against
  10–12 km/h for a pedal rickshaw. Pedal rickshaws remain as a small share (~3%).
- **Rickshaws on main roads is a toggle, on by default.** Officially, battery and pedal
  rickshaws are banned from main roads (DNCC/DMP, April 2025; the minister said so again
  in August 2026). In practice they are everywhere. When the toggle is off they cannot
  use trunk or primary roads and detour through side streets. It works by switching the
  rickshaws' SUMO vClass (`moped` = allowed, `bicycle` = banned; see
  `regions/dhaka/roads.typ.xml`), so both rules share one network.
- **Whole city = main roads only.** The city network keeps motorway to tertiary roads
  (~1,500 km, 5,000 segments). Every residential lane in Dhaka would be ~100k segments,
  too many to simulate live. Neighbourhood areas include every street. Preset volumes
  are tuned on Farmgate and scaled to each area by its main-road lane-km.
- **Most signals are off; traffic police direct them.** Automatic signals run only on
  the Shahbag – Bangla Motor – Karwan Bazar – Farmgate – Bijoy Sarani – PMO – Jahangir
  Gate – Mohakhali railgate corridor, at Gulshan 1 and 2 circles, and in Dhaka
  Cantonment. Preparing an area adds signals there where OpenStreetMap has none
  (roundabouts, such as the SAARC circle at Karwan Bazar, keep their give-way rules).
  Every other mapped signal starts switched off, which is how police control is
  approximated. The zones are in `regions/dhaka/signals.toml`. A signal you add
  runs automatically.
- **Trucks** make up about 2% of daytime traffic and 19% in the night preset,
  matching the city's truck entry hours after 10 pm.

## Reading the numbers

The vehicle mix starts from the mid-2023 RSTP road survey (motorcycles 27%,
non-motorised 22%, cars 20%, three-wheelers 14%), shifted towards battery rickshaws for
2024–26 news reports. The volumes and driver behaviour are still **uncalibrated
guesses**, tuned only so the pattern looks right (free-ish midday, gridlock at rush
hour). Use the results to compare scenarios, e.g. "Evening rush is ~2× the delay of
midday". Don't treat them as forecasts. Calibrating against real vehicle counts is
Phase 4.

- *Delay so far*: average time lost compared with free flow, for vehicles still on the
  road. This is the best gridlock indicator, because stuck vehicles never finish a trip
  and so never show up in *Trip time*.
- *Removed as stuck*: SUMO takes a vehicle off the road after it has been stuck for the
  configured time. Set this to "never" to see a true gridlock.
- The first 5 simulated minutes run at maximum speed to fill the empty roads.

The same stats are available without the browser: `uv run traffic-sim-run <area>` in
`backend/`.
