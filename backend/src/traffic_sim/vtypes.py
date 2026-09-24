"""Vehicle types, shared by every region (each region's presets choose the mix).

Each type maps to a SUMO vType. Parameters are first-guess values for Dhaka
traffic (low speeds, small gaps, aggressive lateral filtering) and are meant to
be calibrated later. All types use the sublane model so smaller vehicles can
squeeze past larger ones instead of queueing in strict lanes.

Rickshaws (battery and pedal) get their SUMO vClass per run: "moped" when they
are allowed on main roads, "bicycle" when they are not. The road network bans
bicycles from trunk and primary roads (see regions/dhaka/roads.typ.xml), so
switching the class switches the rule without rebuilding the network.
"""

from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

RICKSHAW_CLASS_ALLOWED = "moped"
RICKSHAW_CLASS_BANNED = "bicycle"


@dataclass(frozen=True)
class VehicleType:
    id: str
    label: str
    vclass: str  # "rickshaw" = decided per run, see vclass_for()
    length: float  # m
    width: float  # m
    max_speed: float  # m/s
    accel: float
    decel: float
    min_gap: float  # m, longitudinal
    min_gap_lat: float  # m, lateral
    sigma: float  # driver imperfection 0..1
    default_share: float  # % of vehicles
    color: str  # hex, used by the frontend
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_rickshaw(self) -> bool:
        return self.vclass == "rickshaw"


VEHICLE_TYPES: list[VehicleType] = [
    VehicleType(
        "car", "Car / microbus", "passenger", 4.3, 1.8, 16.7, 2.6, 4.5, 1.5, 0.4, 0.6, 22,
        "#2a78d6",
    ),
    VehicleType(
        "bus", "Bus", "bus", 11.0, 2.5, 13.9, 1.2, 4.0, 1.5, 0.5, 0.7, 5,
        "#eb6834", {"lcAssertive": "3", "lcPushy": "0.6"},
    ),
    VehicleType(
        "cng", "CNG auto-rickshaw", "taxi", 2.9, 1.4, 13.9, 1.8, 4.0, 1.0, 0.3, 0.7, 11,
        "#1baf7a", {"lcAssertive": "2", "lcPushy": "0.4"},
    ),
    # Battery-run rickshaw, jokingly "Tesla": a rickshaw body on a hub motor.
    # 20–30 km/h typical, up to 40 with bigger motors (TBS, 2026-08); BUET's
    # standard design is 3.2 × 1.5 m. Quick off the line, weak U-brakes,
    # weaves constantly. Outnumbers pedal rickshaws since 2025 (Daily Star, 2025-10).
    VehicleType(
        "e_rickshaw", "Battery rickshaw (“Tesla”)", "rickshaw", 3.0, 1.3, 8.3, 1.4, 3.0,
        0.7, 0.25, 0.8, 30,
        "#eda100", {"lcAssertive": "2.5", "lcPushy": "0.6", "lcSublane": "2", "lcImpatience": "0.6"},
    ),
    VehicleType(
        "motorcycle", "Motorcycle", "motorcycle", 2.0, 0.8, 16.7, 3.0, 6.0, 0.5, 0.2, 0.7, 26,
        "#e87ba4", {"lcAssertive": "3", "lcPushy": "0.8", "lcSublane": "3"},
    ),
    VehicleType(
        "truck", "Truck", "truck", 8.0, 2.4, 11.1, 1.0, 3.5, 2.0, 0.5, 0.5, 3,
        "#008300",
    ),
    # 10–12 km/h; now a small minority, pushed out by battery rickshaws.
    VehicleType(
        "rickshaw", "Pedal rickshaw", "rickshaw", 2.8, 1.2, 3.3, 0.6, 3.0, 0.8, 0.25, 0.8, 3,
        "#4a3aa7",
    ),
]

VEHICLE_TYPES_BY_ID = {vt.id: vt for vt in VEHICLE_TYPES}


def vclass_for(vt: VehicleType, rickshaws_on_main_roads: bool) -> str:
    if not vt.is_rickshaw:
        return vt.vclass
    return RICKSHAW_CLASS_ALLOWED if rickshaws_on_main_roads else RICKSHAW_CLASS_BANNED


def vtypes_xml(rickshaws_on_main_roads: bool = True, kerb_side: str = "left") -> str:
    lines = ["<additional>"]
    for vt in VEHICLE_TYPES:
        attrs = {
            "id": vt.id,
            "vClass": vclass_for(vt, rickshaws_on_main_roads),
            "length": vt.length,
            "width": vt.width,
            "maxSpeed": vt.max_speed,
            "accel": vt.accel,
            "decel": vt.decel,
            "minGap": vt.min_gap,
            "minGapLat": vt.min_gap_lat,
            "sigma": vt.sigma,
            "latAlignment": "arbitrary",
            "speedDev": 0.15,
            "impatience": 0.5,
            "jmIgnoreKeepClearTime": 5,
            # Dhaka junctions run on nerve, not right of way: drivers nose into
            # gaps and force slow cross traffic to stop for them. Without this,
            # SUMO's polite yielding deadlocks busy unsignalled junctions.
            "jmIgnoreFoeProb": 0.2,  # chance per second of ignoring a slow foe
            "jmIgnoreFoeSpeed": 2.0,  # m/s; foes slower than this can be ignored
            "jmTimegapMinor": 0.5,  # s gap accepted from the side road (default 1)
            "jmDriveAfterRedTime": 2,  # s after the light turns red still driven through
            "color": vt.color,
            **vt.extra,
        }
        attr_str = " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items())
        lines.append(f"    <vType {attr_str}/>")
    lines += special_vtypes(kerb_side)
    lines.append("</additional>")
    return "\n".join(lines) + "\n"


# Not traffic: stationary vehicles standing in for parked rickshaws and CNGs,
# and for people crossing the road (see features.py). They use a vehicle class
# no real vehicle has, so lane closures still apply to them and nothing else
# changes. Parked ones keep to the kerb: the driving side ("left" in Dhaka).
PARKED_PREFIX = "parked_"
CROWD_TYPE = "crowd"
SPECIAL_CLASS = "custom1"


def special_vtypes(kerb_side: str = "left") -> list[str]:
    out = []
    for vt in VEHICLE_TYPES:
        if vt.id in ("e_rickshaw", "rickshaw", "cng"):
            out.append(
                f'    <vType id="{PARKED_PREFIX}{vt.id}" vClass="{SPECIAL_CLASS}" length="{vt.length}" '
                f'width="{vt.width}" minGap="0.3" maxSpeed="1" latAlignment="{kerb_side}" color="{vt.color}"/>'
            )
    # As wide as a lane, so nothing slips past while people cross.
    out.append(f'    <vType id="{CROWD_TYPE}" vClass="{SPECIAL_CLASS}" length="2" width="3.2" minGap="0" maxSpeed="1" color="#ffffff"/>')
    return out
