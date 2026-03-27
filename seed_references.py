"""
Seed engineering_references table with automotive and aerospace component data.

Usage:
    cd backend && python seed_references.py
"""

from dotenv import load_dotenv

load_dotenv()

import json

from db import init_db, get_connection, put_connection, close_db

SEED_DATA = [
    # ── AUTOMOTIVE 1: Steering Wheel ─────────────────────────────────────
    {
        "name": "Steering Wheel",
        "category": "automotive",
        "description": (
            "Three-spoke steering wheel with polyurethane-wrapped rim, "
            "aluminum hub with spline bore, and magnesium alloy spokes. "
            "Designed for passenger vehicles with airbag-ready center cap."
        ),
        "sub_components": [
            {"name": "Rim", "type": "torus", "od_mm": 370, "grip_dia_mm": 32},
            {"name": "Hub", "type": "cylinder", "dia_mm": 50, "height_mm": 30, "spline_bore_mm": 18},
            {"name": "Spoke_1", "type": "beam", "cross_section_mm": [18, 12], "angle_deg": 0},
            {"name": "Spoke_2", "type": "beam", "cross_section_mm": [18, 12], "angle_deg": 120},
            {"name": "Spoke_3", "type": "beam", "cross_section_mm": [18, 12], "angle_deg": 240},
            {"name": "CenterCap", "type": "disc", "dia_mm": 60, "thickness_mm": 5},
        ],
        "dimensions": {
            "overall_dia_mm": 370,
            "grip_diameter_mm": 32,
            "hub_dia_mm": 50,
            "hub_height_mm": 30,
            "spline_bore_mm": 18,
            "spoke_width_mm": 18,
            "spoke_depth_mm": 12,
            "spoke_count": 3,
        },
        "materials": {
            "rim": "Polyurethane over steel armature",
            "hub": "Aluminum 6061-T6",
            "spokes": "Magnesium AZ91D",
            "center_cap": "ABS plastic",
        },
        "connections": (
            "Spokes are die-cast into the hub. Rim torus is welded to spoke ends "
            "at 120-degree intervals. Center cap press-fits onto hub face. "
            "Hub spline bore mates to steering column shaft."
        ),
        "assembly_order": (
            "1. Cast hub with integrated spoke roots. "
            "2. Weld rim torus to spoke tips. "
            "3. Over-mold polyurethane onto rim. "
            "4. Press-fit center cap onto hub."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

rim_od = 370
grip_r = 16
rim_center_r = (rim_od / 2) - grip_r

rim = (
    cq.Workplane("XY")
    .center(rim_center_r, 0)
    .circle(grip_r)
    .revolve(360, (-rim_center_r, 0), (-rim_center_r, 1))
)
parts["Rim"] = rim

hub = (
    cq.Workplane("XY")
    .circle(25)
    .extrude(30)
    .faces(">Z")
    .circle(9)
    .cutThruAll()
)
parts["Hub"] = hub

for i in range(3):
    angle = math.radians(i * 120)
    spoke_len = rim_center_r - 25
    cx = (25 + spoke_len / 2) * math.cos(angle)
    cy = (25 + spoke_len / 2) * math.sin(angle)
    spoke = (
        cq.Workplane("XY")
        .transformed(offset=(cx, cy, 15), rotate=(0, 0, math.degrees(angle)))
        .box(spoke_len, 18, 12)
    )
    parts[f"Spoke_{i+1}"] = spoke

center_cap = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 30))
    .circle(30)
    .extrude(5)
)
parts["CenterCap"] = center_cap
""",
        "tags": ["automotive", "steering", "interior", "driver-interface", "multi-part"],
    },

    # ── AUTOMOTIVE 2: Car Wheel (Alloy Rim) ──────────────────────────────
    {
        "name": "Car Wheel (Alloy Rim)",
        "category": "automotive",
        "description": (
            "17-inch five-spoke cast aluminum alloy wheel with 5-lug hub face, "
            "valve stem provision, and rim barrel for tubeless tire mounting."
        ),
        "sub_components": [
            {"name": "RimBarrel", "type": "cylinder", "od_mm": 432, "width_mm": 215},
            {"name": "HubFace", "type": "disc", "od_mm": 300, "center_bore_mm": 67.1, "lug_count": 5, "pcd_mm": 114.3},
            {"name": "Spoke_1", "type": "radial_beam", "angle_deg": 0},
            {"name": "Spoke_2", "type": "radial_beam", "angle_deg": 72},
            {"name": "Spoke_3", "type": "radial_beam", "angle_deg": 144},
            {"name": "Spoke_4", "type": "radial_beam", "angle_deg": 216},
            {"name": "Spoke_5", "type": "radial_beam", "angle_deg": 288},
            {"name": "ValveStemHole", "type": "hole", "dia_mm": 11.3},
        ],
        "dimensions": {
            "rim_dia_mm": 432,
            "rim_width_mm": 215,
            "hub_face_dia_mm": 300,
            "center_bore_mm": 67.1,
            "pcd_mm": 114.3,
            "lug_hole_dia_mm": 14,
            "lug_count": 5,
            "spoke_count": 5,
            "offset_mm": 45,
        },
        "materials": {
            "wheel": "Cast aluminum A356-T6",
            "finish": "Machine-faced with clear coat",
        },
        "connections": (
            "Hub face bolts to brake rotor hat via 5 lug bolts on 114.3mm PCD. "
            "Tire bead seats on rim barrel flanges. Valve stem presses into "
            "barrel hole. Spokes are integral cast connecting hub to barrel."
        ),
        "assembly_order": (
            "1. Low-pressure die-cast wheel as single piece. "
            "2. Machine hub face, lug holes, center bore. "
            "3. Machine rim barrel bead seats. "
            "4. Drill valve stem hole. "
            "5. Clear coat finish."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

# ── PARAMETERS ────────────────────────────────────────────────────────────
RIM_DIAMETER_MM    = 432.0    # 17-inch nominal
RIM_RADIUS         = 216.0    # bead-seat outer radius
BARREL_WIDTH       = 190.0    # overall barrel width along Z
BARREL_WALL        = 5.0      # barrel wall thickness
LIP_HEIGHT         = 12.0     # flange lip height
OUTER_FLANGE_R     = 222.0    # outermost lip radius

HUB_RADIUS         = 80.0     # hub disc outer radius
HUB_THICKNESS      = 18.0     # hub face thickness
CENTER_BORE_R      = 33.5     # center bore radius (67.1mm dia)

BOLT_PCD_R         = 57.15    # bolt PCD radius (114.3 / 2)
BOLT_HOLE_D        = 14.0     # bolt hole diameter
CBORE_D            = 22.0     # counterbore diameter
CBORE_DEPTH        = 8.0      # counterbore depth
BOLT_COUNT         = 5

SPOKE_COUNT        = 5
SPOKE_WIDTH_HUB    = 28.0     # spoke width at hub end
SPOKE_WIDTH_TIP    = 36.0     # spoke width at barrel end
SPOKE_THICKNESS    = 16.0     # spoke extrusion thickness along Z
SPOKE_OFFSET_DEG   = 36.0     # half-bolt-spacing offset (360/5/2)

DROP_CENTER_R      = 180.0    # radius at bottom of drop center well
DROP_CENTER_DEPTH  = 15.0     # how deep the well drops inward

parts = {}
bw2 = BARREL_WIDTH / 2.0

# ── 1. BARREL (revolve drop-center profile around Z-axis) ────────────────
# Profile in XZ plane: (radius, z_position)
profile_pts = [
    # Front lip
    (OUTER_FLANGE_R,    bw2),
    (OUTER_FLANGE_R,    bw2 - LIP_HEIGHT),
    (RIM_RADIUS,        bw2 - LIP_HEIGHT),
    # Taper into drop center
    (RIM_RADIUS,        bw2 - LIP_HEIGHT - 8),
    (DROP_CENTER_R + 5, 20.0),
    (DROP_CENTER_R,     15.0),
    # Drop center flat
    (DROP_CENTER_R,    -15.0),
    # Taper out of drop center
    (DROP_CENTER_R + 5,-20.0),
    (RIM_RADIUS,       -(bw2 - LIP_HEIGHT - 8)),
    (RIM_RADIUS,       -(bw2 - LIP_HEIGHT)),
    # Rear lip
    (OUTER_FLANGE_R,   -(bw2 - LIP_HEIGHT)),
    (OUTER_FLANGE_R,   -bw2),
    # Inner wall (closing the profile inward)
    (RIM_RADIUS - BARREL_WALL, -bw2),
    (RIM_RADIUS - BARREL_WALL, -(bw2 - LIP_HEIGHT - 5)),
    (DROP_CENTER_R - BARREL_WALL, -12.0),
    (DROP_CENTER_R - BARREL_WALL,  12.0),
    (RIM_RADIUS - BARREL_WALL,  bw2 - LIP_HEIGHT - 5),
    (RIM_RADIUS - BARREL_WALL,  bw2),
]
barrel = (
    cq.Workplane("XZ")
    .polyline(profile_pts)
    .close()
    .revolve(360, (0, 0, 0), (0, 1, 0))
)
parts["Barrel"] = barrel

# ── 2. HUB (disc with center bore + bolt holes cut in) ───────────────────
hub = (
    cq.Workplane("XY")
    .cylinder(HUB_THICKNESS, HUB_RADIUS)
)
# Cut center bore
bore_tool = cq.Workplane("XY").cylinder(HUB_THICKNESS + 4, CENTER_BORE_R)
hub = hub.cut(bore_tool)
# Cut bolt holes with counterbores
for i in range(BOLT_COUNT):
    ang = math.radians(i * 360.0 / BOLT_COUNT)
    bx = BOLT_PCD_R * math.cos(ang)
    by = BOLT_PCD_R * math.sin(ang)
    bolt_cyl = cq.Workplane("XY").cylinder(HUB_THICKNESS + 4, BOLT_HOLE_D / 2.0).translate((bx, by, 0))
    hub = hub.cut(bolt_cyl)
    cbore_cyl = cq.Workplane("XY").cylinder(CBORE_DEPTH, CBORE_D / 2.0).translate((bx, by, HUB_THICKNESS / 2.0 - CBORE_DEPTH / 2.0))
    hub = hub.cut(cbore_cyl)
parts["Hub"] = hub

# ── 3. SPOKES (extruded trapezoids, rotated around Z) ────────────────────
spoke_inner_r = HUB_RADIUS * 0.6
spoke_outer_r = RIM_RADIUS - BARREL_WALL - 2.0

for i in range(SPOKE_COUNT):
    ang_deg = i * (360.0 / SPOKE_COUNT) + SPOKE_OFFSET_DEG
    ang_rad = math.radians(ang_deg)
    cos_a = math.cos(ang_rad)
    sin_a = math.sin(ang_rad)
    cos_a90 = math.cos(ang_rad + math.pi / 2)
    sin_a90 = math.sin(ang_rad + math.pi / 2)
    hw_inner = SPOKE_WIDTH_HUB / 2.0
    hw_outer = SPOKE_WIDTH_TIP / 2.0
    spoke = (
        cq.Workplane("XY")
        .transformed(offset=(0, 0, -SPOKE_THICKNESS / 2.0))
        .moveTo(
            spoke_inner_r * cos_a + hw_inner * cos_a90,
            spoke_inner_r * sin_a + hw_inner * sin_a90,
        )
        .lineTo(
            spoke_outer_r * cos_a + hw_outer * cos_a90,
            spoke_outer_r * sin_a + hw_outer * sin_a90,
        )
        .lineTo(
            spoke_outer_r * cos_a - hw_outer * cos_a90,
            spoke_outer_r * sin_a - hw_outer * sin_a90,
        )
        .lineTo(
            spoke_inner_r * cos_a - hw_inner * cos_a90,
            spoke_inner_r * sin_a - hw_inner * sin_a90,
        )
        .close()
        .extrude(SPOKE_THICKNESS)
    )
    parts[f"Spoke_{i+1}"] = spoke
""",
        "tags": ["automotive", "wheel", "rim", "chassis", "multi-part"],
    },

    # ── AUTOMOTIVE 3: Tire Cross-Section ─────────────────────────────────
    {
        "name": "Tire Cross-Section",
        "category": "automotive",
        "description": (
            "225/45 R17 passenger tire cross-section showing tread, sidewall, "
            "bead, inner liner, and belt plies. Width 225mm, aspect ratio 45%, "
            "fits 432mm (17-inch) rim."
        ),
        "sub_components": [
            {"name": "Tread", "type": "arc_shell", "thickness_mm": 8, "width_mm": 225},
            {"name": "Sidewall_Inner", "type": "curved_shell", "thickness_mm": 5},
            {"name": "Sidewall_Outer", "type": "curved_shell", "thickness_mm": 5},
            {"name": "Bead_Inner", "type": "torus", "wire_dia_mm": 4, "seat_dia_mm": 432},
            {"name": "Bead_Outer", "type": "torus", "wire_dia_mm": 4, "seat_dia_mm": 432},
            {"name": "InnerLiner", "type": "arc_shell", "thickness_mm": 2},
            {"name": "BeltPly", "type": "arc_shell", "thickness_mm": 1.5, "count": 2},
        ],
        "dimensions": {
            "section_width_mm": 225,
            "aspect_ratio_pct": 45,
            "sidewall_height_mm": 101.25,
            "rim_dia_mm": 432,
            "overall_dia_mm": 634.5,
            "tread_depth_mm": 8,
            "bead_wire_dia_mm": 4,
        },
        "materials": {
            "tread": "Synthetic rubber (SBR + BR blend)",
            "sidewall": "Natural rubber + SBR",
            "bead": "High-carbon steel wire (1060 grade)",
            "inner_liner": "Halobutyl rubber (air barrier)",
            "belt_plies": "Steel cord reinforced rubber",
        },
        "connections": (
            "Bead wires anchor tire to rim bead seat. Belt plies sit between "
            "tread and carcass, bonded with rubber cement during vulcanization. "
            "Inner liner is calendered onto inner surface. Sidewall rubber wraps "
            "around bead bundles."
        ),
        "assembly_order": (
            "1. Build inner liner on drum. "
            "2. Apply carcass plies. "
            "3. Set bead wires. "
            "4. Fold carcass over beads. "
            "5. Apply belt plies. "
            "6. Apply tread strip. "
            "7. Vulcanize in mold."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

section_w = 225
sidewall_h = 101.25
rim_r = 216
tread_t = 8
outer_r = rim_r + sidewall_h

tread = (
    cq.Workplane("XY")
    .cylinder(section_w, outer_r, centered=(True, True, True))
    .cut(
        cq.Workplane("XY")
        .cylinder(section_w, outer_r - tread_t, centered=(True, True, True))
    )
)
parts["Tread"] = tread

sw_inner = (
    cq.Workplane("XY")
    .cylinder(5, outer_r - tread_t, centered=(True, True, True))
    .cut(
        cq.Workplane("XY")
        .cylinder(5, rim_r, centered=(True, True, True))
    )
    .translate((0, 0, section_w / 2 - 2.5))
)
parts["Sidewall_Inner"] = sw_inner

sw_outer = (
    cq.Workplane("XY")
    .cylinder(5, outer_r - tread_t, centered=(True, True, True))
    .cut(
        cq.Workplane("XY")
        .cylinder(5, rim_r, centered=(True, True, True))
    )
    .translate((0, 0, -(section_w / 2 - 2.5)))
)
parts["Sidewall_Outer"] = sw_outer

bead_center_r = rim_r + 2
for idx, z_off in enumerate([section_w / 2 - 4, -(section_w / 2 - 4)]):
    bead = (
        cq.Workplane("XZ")
        .center(bead_center_r, 0)
        .circle(2)
        .revolve(360, (-bead_center_r, 0), (-bead_center_r, 1))
        .translate((0, 0, z_off))
    )
    label = "Bead_Inner" if idx == 0 else "Bead_Outer"
    parts[label] = bead

liner = (
    cq.Workplane("XY")
    .cylinder(section_w - 10, rim_r + 5, centered=(True, True, True))
    .cut(
        cq.Workplane("XY")
        .cylinder(section_w - 10, rim_r + 3, centered=(True, True, True))
    )
)
parts["InnerLiner"] = liner

for b in range(2):
    belt = (
        cq.Workplane("XY")
        .cylinder(section_w - 40, outer_r - tread_t - (b * 2), centered=(True, True, True))
        .cut(
            cq.Workplane("XY")
            .cylinder(section_w - 40, outer_r - tread_t - 1.5 - (b * 2), centered=(True, True, True))
        )
    )
    parts[f"BeltPly_{b+1}"] = belt
""",
        "tags": ["automotive", "tire", "rubber", "chassis", "cross-section"],
    },

    # ── AUTOMOTIVE 4: Brake Disc ─────────────────────────────────────────
    {
        "name": "Brake Disc",
        "category": "automotive",
        "description": (
            "Ventilated brake disc (rotor) with 330mm outer diameter, 30mm total "
            "thickness, internal cooling vanes, and 5-bolt hub mounting ring."
        ),
        "sub_components": [
            {"name": "OuterPlate", "type": "annular_disc", "od_mm": 330, "id_mm": 200, "thickness_mm": 8},
            {"name": "InnerPlate", "type": "annular_disc", "od_mm": 330, "id_mm": 200, "thickness_mm": 8},
            {"name": "VentVanes", "type": "radial_fins", "count": 36, "height_mm": 14},
            {"name": "HubRing", "type": "annular_disc", "od_mm": 200, "id_mm": 67.1, "thickness_mm": 12},
            {"name": "LugHoles", "type": "holes", "count": 5, "dia_mm": 14, "pcd_mm": 114.3},
        ],
        "dimensions": {
            "outer_dia_mm": 330,
            "total_thickness_mm": 30,
            "plate_thickness_mm": 8,
            "vent_gap_mm": 14,
            "hub_od_mm": 200,
            "center_bore_mm": 67.1,
            "pcd_mm": 114.3,
            "lug_hole_dia_mm": 14,
            "lug_count": 5,
            "vane_count": 36,
        },
        "materials": {
            "disc": "Grey cast iron GG25 (FC250)",
            "surface_treatment": "Anti-corrosion coating on non-friction surfaces",
        },
        "connections": (
            "Hub ring bolts to wheel hub via 5 lug studs on 114.3mm PCD. "
            "Center bore locates on hub pilot (67.1mm). Brake pads clamp "
            "against outer and inner friction plates. Ventilation vanes are "
            "cast integral between plates."
        ),
        "assembly_order": (
            "1. Cast complete disc as single piece (plates + vanes + hub). "
            "2. Machine friction surfaces to flatness. "
            "3. Drill lug holes and center bore. "
            "4. Balance disc. "
            "5. Apply anti-corrosion coating on hat and vane surfaces."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

outer_r = 165
inner_r = 100
hub_r = 100
bore_r = 67.1 / 2
plate_t = 8
vent_gap = 14

outer_plate = (
    cq.Workplane("XY")
    .circle(outer_r)
    .circle(inner_r)
    .extrude(plate_t)
    .translate((0, 0, plate_t + vent_gap))
)
parts["OuterPlate"] = outer_plate

inner_plate = (
    cq.Workplane("XY")
    .circle(outer_r)
    .circle(inner_r)
    .extrude(plate_t)
)
parts["InnerPlate"] = inner_plate

vane_assembly = cq.Workplane("XY").box(1, 1, 1).translate((0, 0, -1000))
for i in range(36):
    angle = math.radians(i * 10)
    mid_r = (outer_r + inner_r) / 2
    vx = mid_r * math.cos(angle)
    vy = mid_r * math.sin(angle)
    vane = (
        cq.Workplane("XY")
        .transformed(
            offset=(vx, vy, plate_t + vent_gap / 2),
            rotate=(0, 0, math.degrees(angle)),
        )
        .box(outer_r - inner_r - 10, 3, vent_gap)
    )
    vane_assembly = vane_assembly.union(vane)
parts["VentVanes"] = vane_assembly

hub_ring = (
    cq.Workplane("XY")
    .circle(hub_r)
    .circle(bore_r)
    .extrude(plate_t + vent_gap + plate_t)
)
pcd_r = 114.3 / 2
for i in range(5):
    a = math.radians(i * 72)
    hole = (
        cq.Workplane("XY")
        .transformed(offset=(pcd_r * math.cos(a), pcd_r * math.sin(a), 0))
        .circle(7)
        .extrude(plate_t + vent_gap + plate_t)
    )
    hub_ring = hub_ring.cut(hole)
parts["HubRing"] = hub_ring
""",
        "tags": ["automotive", "brake", "rotor", "disc", "chassis", "ventilated"],
    },

    # ── AUTOMOTIVE 5: Brake Caliper ──────────────────────────────────────
    {
        "name": "Brake Caliper",
        "category": "automotive",
        "description": (
            "Opposed-piston fixed brake caliper with 4 pistons (2 per side), "
            "aluminum body, brake pad slots, bleed nipple boss, and bolt-on "
            "mounting ears for knuckle attachment."
        ),
        "sub_components": [
            {"name": "CaliperBodyOuter", "type": "block", "dims_mm": [160, 80, 50]},
            {"name": "CaliperBodyInner", "type": "block", "dims_mm": [160, 80, 50]},
            {"name": "PistonBore_1", "type": "cylinder_bore", "dia_mm": 38},
            {"name": "PistonBore_2", "type": "cylinder_bore", "dia_mm": 38},
            {"name": "PistonBore_3", "type": "cylinder_bore", "dia_mm": 42},
            {"name": "PistonBore_4", "type": "cylinder_bore", "dia_mm": 42},
            {"name": "MountingEar_L", "type": "lug", "bolt_dia_mm": 12},
            {"name": "MountingEar_R", "type": "lug", "bolt_dia_mm": 12},
            {"name": "BleedNippleBoss", "type": "boss", "dia_mm": 14},
            {"name": "PadSlot", "type": "slot", "width_mm": 12},
        ],
        "dimensions": {
            "body_length_mm": 160,
            "body_width_mm": 80,
            "body_height_mm": 100,
            "disc_gap_mm": 34,
            "piston_dia_front_mm": 38,
            "piston_dia_rear_mm": 42,
            "piston_count": 4,
            "mounting_bolt_dia_mm": 12,
            "pad_thickness_mm": 12,
        },
        "materials": {
            "body": "Aluminum 6061-T6 (anodized)",
            "pistons": "Phenolic composite or stainless steel",
            "seals": "EPDM rubber",
            "bleed_nipple": "Steel",
        },
        "connections": (
            "Mounting ears bolt to steering knuckle with M12 bolts (2). "
            "Caliper straddles brake disc with pad slots on each side. "
            "Hydraulic fluid enters via banjo fitting, pressurizes pistons. "
            "Bleed nipple at highest point for air evacuation."
        ),
        "assembly_order": (
            "1. Machine caliper body halves (or monoblock). "
            "2. Bore piston cylinders. "
            "3. Install seals and pistons. "
            "4. Install bleed nipple. "
            "5. Mount caliper on knuckle. "
            "6. Insert brake pads."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

body_l = 160
body_w = 80
body_h = 50
disc_gap = 34

outer_half = (
    cq.Workplane("XY")
    .box(body_l, body_w, body_h)
    .translate((0, 0, disc_gap / 2 + body_h / 2))
)
for offset_x, dia in [(-30, 38), (30, 42)]:
    bore = (
        cq.Workplane("XY")
        .transformed(offset=(offset_x, 0, disc_gap / 2))
        .circle(dia / 2)
        .extrude(body_h + 1)
    )
    outer_half = outer_half.cut(bore)
parts["CaliperBodyOuter"] = outer_half

inner_half = (
    cq.Workplane("XY")
    .box(body_l, body_w, body_h)
    .translate((0, 0, -(disc_gap / 2 + body_h / 2)))
)
for offset_x, dia in [(-30, 38), (30, 42)]:
    bore = (
        cq.Workplane("XY")
        .transformed(offset=(offset_x, 0, -(disc_gap / 2 + body_h + 1)))
        .circle(dia / 2)
        .extrude(body_h + 1)
    )
    inner_half = inner_half.cut(bore)
parts["CaliperBodyInner"] = inner_half

bridge = (
    cq.Workplane("XY")
    .box(body_l, 30, disc_gap + body_h * 2)
    .translate((0, body_w / 2 - 15, 0))
)
parts["Bridge"] = bridge

for side, x_off in [("L", -body_l / 2 - 15), ("R", body_l / 2 + 15)]:
    ear = (
        cq.Workplane("XY")
        .box(30, 40, 20)
        .translate((x_off, body_w / 2 - 20, 0))
    )
    bolt = (
        cq.Workplane("XY")
        .transformed(offset=(x_off, body_w / 2 - 20, -11))
        .circle(6)
        .extrude(22)
    )
    ear = ear.cut(bolt)
    parts[f"MountingEar_{side}"] = ear

bleed_boss = (
    cq.Workplane("XY")
    .transformed(offset=(50, 0, disc_gap / 2 + body_h))
    .circle(7)
    .extrude(12)
)
bleed_hole = (
    cq.Workplane("XY")
    .transformed(offset=(50, 0, disc_gap / 2 + body_h))
    .circle(3)
    .extrude(15)
)
bleed_boss = bleed_boss.cut(bleed_hole)
parts["BleedNippleBoss"] = bleed_boss
""",
        "tags": ["automotive", "brake", "caliper", "hydraulic", "chassis"],
    },

    # ── AUTOMOTIVE 6: Piston ─────────────────────────────────────────────
    {
        "name": "Piston",
        "category": "automotive",
        "description": (
            "Forged aluminum automotive piston with flat crown, three ring "
            "grooves (two compression, one oil control), skirt with anti-friction "
            "coating, and wrist pin bore."
        ),
        "sub_components": [
            {"name": "Crown", "type": "disc", "dia_mm": 86, "thickness_mm": 12, "profile": "flat"},
            {"name": "RingLand_1", "type": "groove", "width_mm": 1.2, "depth_mm": 3.5, "position": "top_compression"},
            {"name": "RingLand_2", "type": "groove", "width_mm": 1.5, "depth_mm": 3.5, "position": "second_compression"},
            {"name": "OilRingGroove", "type": "groove", "width_mm": 3.0, "depth_mm": 3.5, "has_drain_holes": True},
            {"name": "Skirt", "type": "cylinder_shell", "dia_mm": 86, "height_mm": 40, "wall_mm": 3},
            {"name": "WristPinBore", "type": "through_hole", "dia_mm": 22},
        ],
        "dimensions": {
            "bore_dia_mm": 86,
            "overall_height_mm": 70,
            "crown_thickness_mm": 12,
            "compression_height_mm": 35,
            "skirt_length_mm": 40,
            "wrist_pin_dia_mm": 22,
            "ring_groove_1_width_mm": 1.2,
            "ring_groove_2_width_mm": 1.5,
            "oil_ring_groove_width_mm": 3.0,
            "ring_groove_depth_mm": 3.5,
        },
        "materials": {
            "piston": "Forged aluminum 2618-T61 or 4032-T6",
            "ring_1": "Ductile iron with CrN PVD coating",
            "ring_2": "Grey cast iron with phosphate coating",
            "oil_ring": "Steel rail with chrome coating",
            "wrist_pin": "Case-hardened 8620 steel",
            "skirt_coating": "Graphite-filled polymer (Moly or Grafal)",
        },
        "connections": (
            "Wrist pin press-fit or floating in piston bores, retained by circlips. "
            "Piston rings sit in grooves, seal against cylinder wall. "
            "Wrist pin connects to connecting rod small end."
        ),
        "assembly_order": (
            "1. Forge and machine piston blank. "
            "2. Machine ring grooves on OD. "
            "3. Bore wrist pin holes. "
            "4. Apply skirt coating. "
            "5. Install rings (oil ring first, then compression rings). "
            "6. Insert wrist pin and circlips."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

bore_r = 43
crown_t = 12
total_h = 70
pin_dia = 22

crown = (
    cq.Workplane("XY")
    .circle(bore_r)
    .extrude(crown_t)
    .translate((0, 0, total_h - crown_t))
)
parts["Crown"] = crown

ring_body = (
    cq.Workplane("XY")
    .circle(bore_r)
    .circle(bore_r - 5)
    .extrude(20)
    .translate((0, 0, total_h - crown_t - 20))
)
groove_specs = [
    ("RingGroove_1", 1.2, 0),
    ("RingGroove_2", 1.5, 5),
    ("OilRingGroove", 3.0, 12),
]
for gname, gw, g_offset in groove_specs:
    groove = (
        cq.Workplane("XY")
        .circle(bore_r + 0.1)
        .circle(bore_r - 3.5)
        .extrude(gw)
        .translate((0, 0, total_h - crown_t - 2 - g_offset))
    )
    ring_body = ring_body.cut(groove)
parts["RingLands"] = ring_body

skirt = (
    cq.Workplane("XY")
    .circle(bore_r)
    .circle(bore_r - 3)
    .extrude(40)
)
pin_bore_l = (
    cq.Workplane("YZ")
    .transformed(offset=(0, 20, 0))
    .circle(pin_dia / 2)
    .extrude(bore_r * 2 + 2)
    .translate((-bore_r - 1, 0, 0))
)
skirt = skirt.cut(pin_bore_l)
parts["Skirt"] = skirt

pin_boss_l = (
    cq.Workplane("YZ")
    .transformed(offset=(0, 20, 0))
    .circle(pin_dia / 2 + 5)
    .circle(pin_dia / 2)
    .extrude(12)
    .translate((-pin_dia / 2 - 5 - 6, 0, 0))
)
pin_boss_r = (
    cq.Workplane("YZ")
    .transformed(offset=(0, 20, 0))
    .circle(pin_dia / 2 + 5)
    .circle(pin_dia / 2)
    .extrude(12)
    .translate((pin_dia / 2 + 5 - 6, 0, 0))
)
parts["PinBoss_L"] = pin_boss_l
parts["PinBoss_R"] = pin_boss_r
""",
        "tags": ["automotive", "engine", "piston", "reciprocating", "internal-combustion"],
    },

    # ── AUTOMOTIVE 7: Connecting Rod ─────────────────────────────────────
    {
        "name": "Connecting Rod",
        "category": "automotive",
        "description": (
            "Forged steel I-beam connecting rod with split big end (crank journal), "
            "small end (wrist pin), and two cap bolts. Center-to-center 143mm."
        ),
        "sub_components": [
            {"name": "BigEnd", "type": "split_bearing_housing", "bore_mm": 52, "width_mm": 22},
            {"name": "BigEndCap", "type": "semi_circle", "bore_mm": 52},
            {"name": "IBeamShaft", "type": "i_beam", "length_mm": 95, "web_mm": 8, "flange_mm": [18, 6]},
            {"name": "SmallEnd", "type": "bearing_eye", "bore_mm": 22, "width_mm": 20},
            {"name": "CapBolt_1", "type": "bolt", "size": "M8x1.0"},
            {"name": "CapBolt_2", "type": "bolt", "size": "M8x1.0"},
        ],
        "dimensions": {
            "center_to_center_mm": 143,
            "big_end_bore_mm": 52,
            "small_end_bore_mm": 22,
            "big_end_width_mm": 22,
            "small_end_width_mm": 20,
            "beam_web_thickness_mm": 8,
            "beam_flange_width_mm": 18,
            "beam_flange_thickness_mm": 6,
            "cap_bolt_size": "M8x1.0",
            "weight_g": 520,
        },
        "materials": {
            "rod": "Forged steel 4340 or powder metal C-70",
            "cap_bolts": "Grade 12.9 alloy steel",
            "big_end_bearing": "Tri-metal (steel back, copper, babbitt overlay)",
            "small_end_bushing": "Phosphor bronze",
        },
        "connections": (
            "Big end wraps crankshaft journal with split bearing insert; cap "
            "secured by two M8 bolts torqued to 35 Nm. Small end receives "
            "floating wrist pin connecting to piston. I-beam transfers "
            "reciprocating load between both ends."
        ),
        "assembly_order": (
            "1. Forge rod blank (big end + shaft + small end). "
            "2. Fracture-split big end to create cap. "
            "3. Machine bearing bores. "
            "4. Install bearing shells. "
            "5. Install small end bushing. "
            "6. Assemble rod on crankshaft with cap bolts."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

ctc = 143
big_bore = 52
small_bore = 22
big_w = 22
small_w = 20
beam_len = ctc - big_bore / 2 - small_bore / 2

big_end_outer_r = big_bore / 2 + 10
big_end = (
    cq.Workplane("XY")
    .circle(big_end_outer_r)
    .circle(big_bore / 2)
    .extrude(big_w)
)
parts["BigEnd"] = big_end

cap = (
    cq.Workplane("XY")
    .circle(big_end_outer_r)
    .circle(big_bore / 2)
    .extrude(big_w)
    .cut(
        cq.Workplane("XY")
        .box(big_end_outer_r * 3, big_end_outer_r * 3, big_w + 2)
        .translate((0, big_end_outer_r * 1.5, big_w / 2))
    )
)
parts["BigEndCap"] = cap

shaft_base = ctc - big_end_outer_r - small_bore / 2 - 5
shaft = (
    cq.Workplane("XZ")
    .transformed(offset=(0, big_w / 2, 0))
    .rect(8, big_w)
    .extrude(shaft_base)
    .translate((0, -big_w / 2, 0))
    .translate((0, big_end_outer_r, 0))
)
flange_top = (
    cq.Workplane("XZ")
    .transformed(offset=(0, big_w / 2, 0))
    .rect(18, 6)
    .extrude(shaft_base)
    .translate((0, -big_w / 2, 0))
    .translate((0, big_end_outer_r, big_w / 2 - 3))
)
flange_bot = (
    cq.Workplane("XZ")
    .transformed(offset=(0, big_w / 2, 0))
    .rect(18, 6)
    .extrude(shaft_base)
    .translate((0, -big_w / 2, 0))
    .translate((0, big_end_outer_r, -(big_w / 2 - 3)))
)
parts["IBeamShaft"] = shaft
parts["Flange_Top"] = flange_top
parts["Flange_Bottom"] = flange_bot

small_outer_r = small_bore / 2 + 5
small_end = (
    cq.Workplane("XY")
    .circle(small_outer_r)
    .circle(small_bore / 2)
    .extrude(small_w)
    .translate((0, ctc, (big_w - small_w) / 2))
)
parts["SmallEnd"] = small_end

for side in [-1, 1]:
    bolt = (
        cq.Workplane("XY")
        .transformed(offset=(side * (big_bore / 2 + 5), 0, -5))
        .circle(4)
        .extrude(big_w + 10)
    )
    parts[f"CapBolt_{'L' if side == -1 else 'R'}"] = bolt
""",
        "tags": ["automotive", "engine", "connecting-rod", "reciprocating", "forged"],
    },

    # ── AUTOMOTIVE 8: Engine Valve ───────────────────────────────────────
    {
        "name": "Engine Valve",
        "category": "automotive",
        "description": (
            "Poppet-type engine valve with disc head, long stem, keeper groove, "
            "and tip. Intake variant: 35mm head, steel. Exhaust variant: 30mm "
            "head, Inconel. Stem diameter 6mm, overall length ~106mm."
        ),
        "sub_components": [
            {"name": "Head", "type": "disc", "dia_mm": 35, "thickness_mm": 2, "seat_angle_deg": 45},
            {"name": "Fillet", "type": "transition", "from_dia_mm": 35, "to_dia_mm": 6},
            {"name": "Stem", "type": "cylinder", "dia_mm": 6, "length_mm": 100},
            {"name": "KeeperGroove", "type": "groove", "dia_mm": 5.5, "width_mm": 2},
            {"name": "Tip", "type": "cylinder", "dia_mm": 6, "length_mm": 4},
        ],
        "dimensions": {
            "head_dia_intake_mm": 35,
            "head_dia_exhaust_mm": 30,
            "stem_dia_mm": 6,
            "stem_length_mm": 100,
            "overall_length_mm": 106,
            "seat_angle_deg": 45,
            "keeper_groove_dia_mm": 5.5,
            "keeper_groove_width_mm": 2,
            "tip_height_mm": 4,
        },
        "materials": {
            "intake_valve": "Martensitic stainless steel (21-4N / SUH35)",
            "exhaust_valve": "Inconel 751 or Nimonic 80A",
            "stem_tip": "Stellite 6 hardfacing",
            "seat_face": "Stellite or Tribaloy hardfacing",
        },
        "connections": (
            "Valve stem slides in valve guide (pressed into cylinder head). "
            "Keeper groove retains valve spring retainer via split collets. "
            "Valve head seats against valve seat insert (45-deg angle) in "
            "cylinder head. Tip contacts rocker arm or cam follower."
        ),
        "assembly_order": (
            "1. Hot forge valve head and stem in one piece. "
            "2. Machine stem to tolerance (h7). "
            "3. Grind seat face at 45-deg. "
            "4. Machine keeper groove. "
            "5. Harden and stellite-tip face. "
            "6. Insert through guide, fit spring + retainer + collets."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

head_r = 17.5
stem_r = 3
stem_len = 100

head = (
    cq.Workplane("XY")
    .circle(head_r)
    .extrude(2)
)
margin = (
    cq.Workplane("XY")
    .circle(head_r)
    .circle(head_r - 1.5)
    .extrude(2)
)
parts["Head"] = head

fillet_cone = (
    cq.Workplane("XY")
    .circle(head_r - 1)
    .extrude(0.1)
    .translate((0, 0, 2))
    .union(
        cq.Workplane("XY")
        .circle(stem_r + 1)
        .extrude(0.1)
        .translate((0, 0, 8))
    )
)
fillet_approx = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 2))
    .circle(head_r - 1)
    .extrude(6)
    .cut(
        cq.Workplane("XY")
        .transformed(offset=(0, 0, 2))
        .box(head_r * 3, head_r * 3, 6)
        .cut(
            cq.Workplane("XY")
            .transformed(offset=(0, 0, 2))
            .circle(stem_r + 1)
            .extrude(6)
        )
    )
)
parts["Fillet"] = fillet_approx

stem = (
    cq.Workplane("XY")
    .circle(stem_r)
    .extrude(stem_len)
    .translate((0, 0, 2))
)
parts["Stem"] = stem

keeper_groove_cut = (
    cq.Workplane("XY")
    .circle(stem_r + 0.1)
    .circle(stem_r - 0.25)
    .extrude(2)
    .translate((0, 0, stem_len - 4))
)
parts["KeeperGroove"] = keeper_groove_cut

tip = (
    cq.Workplane("XY")
    .circle(stem_r)
    .extrude(4)
    .translate((0, 0, stem_len + 2))
)
parts["Tip"] = tip
""",
        "tags": ["automotive", "engine", "valve", "poppet", "intake", "exhaust"],
    },

    # ── AUTOMOTIVE 9: Car Side Mirror Housing ────────────────────────────
    {
        "name": "Car Side Mirror Housing",
        "category": "automotive",
        "description": (
            "Aerodynamic side mirror housing with teardrop outer shell, "
            "internal mirror mount plate, pivot ball socket for adjustment, "
            "and wire routing channel for heated mirror and indicators."
        ),
        "sub_components": [
            {"name": "OuterShell", "type": "shell", "dims_mm": [170, 120, 80], "wall_mm": 3},
            {"name": "MirrorMountPlate", "type": "plate", "dims_mm": [110, 90, 3]},
            {"name": "PivotBallSocket", "type": "sphere_socket", "ball_dia_mm": 30},
            {"name": "WireChannel", "type": "channel", "dia_mm": 12},
            {"name": "MountingBase", "type": "plate", "dims_mm": [60, 40, 5]},
        ],
        "dimensions": {
            "length_mm": 170,
            "height_mm": 120,
            "depth_mm": 80,
            "shell_wall_mm": 3,
            "mirror_face_mm": [110, 90],
            "pivot_ball_dia_mm": 30,
            "wire_channel_dia_mm": 12,
            "mounting_bolt_spacing_mm": 45,
        },
        "materials": {
            "shell": "ABS plastic (ASA blend for UV resistance)",
            "mount_plate": "Glass-filled nylon PA66-GF30",
            "pivot": "POM (acetal) ball and socket",
            "mirror": "Heated glass with anti-glare coating",
            "finish": "Textured black or body-color painted",
        },
        "connections": (
            "Mounting base bolts to door panel via 3 studs from inside. "
            "Pivot ball socket snaps onto mirror mount plate ball. "
            "Wire channel routes from door through mounting base to "
            "mirror heater and turn signal LED. Shell snaps onto base."
        ),
        "assembly_order": (
            "1. Injection-mold outer shell and base separately. "
            "2. Install pivot ball mechanism in base. "
            "3. Attach mirror glass to mount plate. "
            "4. Route wiring harness through channel. "
            "5. Snap shell onto base. "
            "6. Bolt assembly to door."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

shell_l = 170
shell_h = 120
shell_d = 80
wall = 3

outer_shell = (
    cq.Workplane("XY")
    .box(shell_l, shell_d, shell_h)
    .edges("|Z")
    .fillet(25)
    .cut(
        cq.Workplane("XY")
        .box(shell_l - wall * 2, shell_d - wall * 2, shell_h - wall)
        .translate((0, 0, -wall / 2))
    )
)
parts["OuterShell"] = outer_shell

mount_plate = (
    cq.Workplane("XY")
    .box(110, 3, 90)
    .translate((0, -shell_d / 2 + wall + 1.5, 0))
)
parts["MirrorMountPlate"] = mount_plate

ball_socket_outer = (
    cq.Workplane("XY")
    .sphere(18)
    .translate((0, 0, 0))
)
ball_socket_cut = (
    cq.Workplane("XY")
    .sphere(15)
    .translate((0, 0, 0))
)
ball_socket = ball_socket_outer.cut(ball_socket_cut)
ball_cut_half = (
    cq.Workplane("XY")
    .box(40, 40, 20)
    .translate((0, 0, 10))
)
ball_socket = ball_socket.cut(ball_cut_half)
parts["PivotBallSocket"] = ball_socket

wire_channel = (
    cq.Workplane("XZ")
    .circle(6)
    .extrude(shell_d)
    .translate((30, -shell_d / 2, -30))
)
parts["WireChannel"] = wire_channel

mount_base = (
    cq.Workplane("XY")
    .box(60, 40, 5)
    .translate((40, shell_d / 2 - 20, -shell_h / 2 + 2.5))
)
for bx in [-15, 0, 15]:
    bolt_hole = (
        cq.Workplane("XY")
        .transformed(offset=(40 + bx, shell_d / 2 - 20, -shell_h / 2))
        .circle(3)
        .extrude(10)
    )
    mount_base = mount_base.cut(bolt_hole)
parts["MountingBase"] = mount_base
""",
        "tags": ["automotive", "mirror", "exterior", "body", "aerodynamic"],
    },

    # ── AUTOMOTIVE 10: Car Door Handle ───────────────────────────────────
    {
        "name": "Car Door Handle",
        "category": "automotive",
        "description": (
            "Exterior car door pull handle with zinc alloy body, chrome plating, "
            "mounting plate with gasket, pivot pin, and return spring cavity."
        ),
        "sub_components": [
            {"name": "PullHandle", "type": "bar", "dims_mm": [120, 25, 15], "profile": "rounded"},
            {"name": "MountingPlate", "type": "plate", "dims_mm": [140, 40, 3]},
            {"name": "PivotPin", "type": "cylinder", "dia_mm": 6, "length_mm": 30},
            {"name": "SpringCavity", "type": "pocket", "dims_mm": [20, 10, 12]},
            {"name": "LatchRod_Connector", "type": "hook", "wire_dia_mm": 3},
        ],
        "dimensions": {
            "handle_length_mm": 120,
            "handle_width_mm": 25,
            "handle_depth_mm": 15,
            "plate_length_mm": 140,
            "plate_width_mm": 40,
            "plate_thickness_mm": 3,
            "pivot_dia_mm": 6,
            "pivot_length_mm": 30,
            "spring_pocket_mm": [20, 10, 12],
            "pull_clearance_mm": 20,
        },
        "materials": {
            "handle": "Zinc alloy (Zamak 3) die-cast",
            "finish": "Decorative chrome plating (Cu-Ni-Cr)",
            "mounting_plate": "Steel, e-coat",
            "pivot_pin": "Stainless steel 303",
            "spring": "Music wire (ASTM A228)",
            "gasket": "EPDM foam",
        },
        "connections": (
            "Mounting plate screws to door inner panel from behind (2 screws). "
            "Handle pivots on pin pressed into mounting plate. Return spring "
            "biases handle to closed position. Latch rod connector links handle "
            "motion to door latch mechanism via bellcrank."
        ),
        "assembly_order": (
            "1. Die-cast handle body. "
            "2. Plate, polish, chrome-plate handle. "
            "3. Stamp mounting plate. "
            "4. Assemble handle on plate via pivot pin. "
            "5. Install return spring. "
            "6. Attach latch rod connector. "
            "7. Install assembly in door with gasket."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

handle = (
    cq.Workplane("XY")
    .box(120, 25, 15)
    .edges("|Z")
    .fillet(7)
    .edges("|X")
    .fillet(5)
)
parts["PullHandle"] = handle

mount_plate = (
    cq.Workplane("XY")
    .box(140, 40, 3)
    .translate((0, 0, -20))
)
for sx in [-50, 50]:
    screw = (
        cq.Workplane("XY")
        .transformed(offset=(sx, 0, -22))
        .circle(2.5)
        .extrude(6)
    )
    mount_plate = mount_plate.cut(screw)
parts["MountingPlate"] = mount_plate

pivot_pin = (
    cq.Workplane("XY")
    .transformed(offset=(-55, 0, -10))
    .circle(3)
    .extrude(30)
)
parts["PivotPin"] = pivot_pin

spring_cavity = (
    cq.Workplane("XY")
    .box(20, 10, 12)
    .translate((-40, 0, -14))
)
parts["SpringCavity"] = spring_cavity

connector = (
    cq.Workplane("XY")
    .box(10, 8, 25)
    .translate((55, 0, -12))
)
rod_hole = (
    cq.Workplane("XZ")
    .transformed(offset=(0, 0, 0))
    .circle(1.5)
    .extrude(12)
    .translate((55, -6, -20))
)
connector = connector.cut(rod_hole)
parts["LatchRodConnector"] = connector
""",
        "tags": ["automotive", "door", "handle", "exterior", "body", "chrome"],
    },

    # ── AEROSPACE 11: Turbine Blade ──────────────────────────────────────
    {
        "name": "Turbine Blade",
        "category": "aerospace",
        "description": (
            "High-pressure turbine blade with twisted airfoil, fir-tree root "
            "for dovetail retention in turbine disc, integral platform, and "
            "internal cooling holes. Single-crystal nickel superalloy."
        ),
        "sub_components": [
            {"name": "Airfoil", "type": "twisted_airfoil", "chord_mm": 60, "span_mm": 80, "twist_deg": 30},
            {"name": "Platform", "type": "plate", "dims_mm": [65, 30, 3]},
            {"name": "Root_FirTree", "type": "fir_tree", "width_mm": 25, "depth_mm": 20, "lobes": 3},
            {"name": "CoolingHole_LE", "type": "hole", "dia_mm": 2, "count": 5},
            {"name": "CoolingHole_TE", "type": "slot", "width_mm": 1, "count": 3},
            {"name": "Tip_Shroud", "type": "plate", "dims_mm": [62, 8, 2]},
        ],
        "dimensions": {
            "airfoil_chord_mm": 60,
            "airfoil_span_mm": 80,
            "airfoil_max_thickness_mm": 12,
            "twist_deg": 30,
            "platform_dims_mm": [65, 30, 3],
            "root_width_mm": 25,
            "root_depth_mm": 20,
            "root_lobes": 3,
            "cooling_hole_dia_mm": 2,
            "overall_height_mm": 103,
        },
        "materials": {
            "blade": "Single-crystal nickel superalloy (CMSX-4 or Rene N5)",
            "coating": "Thermal barrier coating (TBC): yttria-stabilized zirconia",
            "bond_coat": "MCrAlY overlay",
        },
        "connections": (
            "Fir-tree root slides axially into matching slots in turbine disc rim. "
            "Platform forms part of gas path annulus, sealing blade-to-blade gap. "
            "Cooling air feeds from disc bore through root passages into airfoil "
            "internal channels, exhausting through film cooling holes."
        ),
        "assembly_order": (
            "1. Investment-cast blade in single-crystal mold. "
            "2. EDM cooling holes. "
            "3. Grind root fir-tree to tolerance. "
            "4. Apply bond coat and TBC. "
            "5. Slide root into disc slot. "
            "6. Install locking plates."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

chord = 60
span = 80
max_t = 12

airfoil = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 23))
    .rect(chord, max_t)
    .extrude(span)
)
airfoil = airfoil.edges("|Z").fillet(5)
parts["Airfoil"] = airfoil

platform = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 20))
    .box(65, 30, 3)
)
parts["Platform"] = platform

root_base = (
    cq.Workplane("XY")
    .box(25, 20, 20)
)
for i in range(3):
    z_pos = 3 + i * 6
    notch_w = 25 + 4
    notch = (
        cq.Workplane("XY")
        .box(notch_w, 4, 2)
        .translate((0, 0, z_pos))
    )
    root_base = root_base.cut(notch)
parts["Root_FirTree"] = root_base

for i in range(5):
    hole = (
        cq.Workplane("XY")
        .transformed(offset=(-chord / 2 + 5, 0, 23 + 10 + i * 14))
        .circle(1)
        .extrude(max_t + 2)
    )
    parts[f"CoolingHole_LE_{i+1}"] = hole

tip_shroud = (
    cq.Workplane("XY")
    .box(62, 8, 2)
    .translate((0, 0, 23 + span + 1))
)
parts["TipShroud"] = tip_shroud
""",
        "tags": ["aerospace", "turbine", "blade", "gas-turbine", "high-temperature", "single-crystal"],
    },

    # ── AEROSPACE 12: Turbine Disc ───────────────────────────────────────
    {
        "name": "Turbine Disc",
        "category": "aerospace",
        "description": (
            "High-pressure turbine disc with fir-tree blade slots around the "
            "rim, central bore for shaft, bolt circle for stage coupling, and "
            "labyrinth seal teeth. Inconel 718 forging."
        ),
        "sub_components": [
            {"name": "DiscBody", "type": "disc", "od_mm": 400, "thickness_mm": 60},
            {"name": "FirTreeSlots", "type": "slots", "count": 50, "slot_depth_mm": 20},
            {"name": "CentralBore", "type": "hole", "dia_mm": 100},
            {"name": "BoltCircle", "type": "holes", "count": 24, "dia_mm": 10, "pcd_mm": 140},
            {"name": "BalanceRing", "type": "annular", "od_mm": 130, "width_mm": 8},
            {"name": "SealTeeth", "type": "annular_ridges", "count": 4},
        ],
        "dimensions": {
            "outer_dia_mm": 400,
            "rim_thickness_mm": 60,
            "web_thickness_mm": 20,
            "bore_dia_mm": 100,
            "bolt_circle_dia_mm": 140,
            "bolt_count": 24,
            "bolt_hole_dia_mm": 10,
            "blade_slot_count": 50,
            "blade_slot_depth_mm": 20,
            "overall_width_mm": 60,
        },
        "materials": {
            "disc": "Inconel 718 (forged + heat-treated)",
            "bolt_circle_inserts": "Waspaloy",
            "seal_teeth": "Integral (same as disc)",
        },
        "connections": (
            "Fir-tree slots receive turbine blade roots around circumference. "
            "Central bore fits on shaft with interference fit or spline. "
            "Bolt circle couples disc to adjacent turbine stage or shaft flange. "
            "Labyrinth seal teeth interface with stator seal lands."
        ),
        "assembly_order": (
            "1. Triple-melt and forge disc blank. "
            "2. Heat treat (solution + age). "
            "3. Machine bore and bolt holes. "
            "4. Broach fir-tree slots. "
            "5. Machine seal teeth. "
            "6. Balance. "
            "7. Install blades and locking hardware."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

od_r = 200
bore_r = 50
disc_t = 60
web_t = 20

disc_body = (
    cq.Workplane("XY")
    .circle(od_r)
    .circle(bore_r)
    .extrude(disc_t)
)
web_cut_outer = (
    cq.Workplane("XY")
    .circle(od_r - 25)
    .circle(bore_r + 30)
    .extrude(disc_t)
    .cut(
        cq.Workplane("XY")
        .circle(od_r - 25)
        .circle(bore_r + 30)
        .extrude(web_t)
        .translate((0, 0, (disc_t - web_t) / 2))
    )
)
disc_body = disc_body.cut(web_cut_outer)
parts["DiscBody"] = disc_body

slot_assembly = cq.Workplane("XY").box(1, 1, 1).translate((0, 0, -1000))
for i in range(50):
    angle = math.radians(i * 360 / 50)
    sx = od_r * math.cos(angle)
    sy = od_r * math.sin(angle)
    slot = (
        cq.Workplane("XY")
        .transformed(
            offset=(sx, sy, disc_t / 2),
            rotate=(0, 0, math.degrees(angle)),
        )
        .box(20, 8, disc_t + 1)
    )
    slot_assembly = slot_assembly.union(slot)
parts["FirTreeSlots"] = slot_assembly

pcd_r = 70
for i in range(24):
    angle = math.radians(i * 15)
    hx = pcd_r * math.cos(angle)
    hy = pcd_r * math.sin(angle)
    bolt_hole = (
        cq.Workplane("XY")
        .transformed(offset=(hx, hy, 0))
        .circle(5)
        .extrude(disc_t)
    )
    parts[f"BoltHole_{i+1}"] = bolt_hole

for t in range(4):
    tooth = (
        cq.Workplane("XY")
        .circle(bore_r + 5)
        .circle(bore_r + 3)
        .extrude(2)
        .translate((0, 0, 10 + t * 12))
    )
    parts[f"SealTooth_{t+1}"] = tooth
""",
        "tags": ["aerospace", "turbine", "disc", "gas-turbine", "high-temperature", "forged"],
    },

    # ── AEROSPACE 13: Wing Rib ───────────────────────────────────────────
    {
        "name": "Wing Rib",
        "category": "aerospace",
        "description": (
            "Aluminum wing rib with vertical web (lightening holes for weight "
            "reduction), upper and lower cap flanges for skin attachment, and "
            "stiffener beads. Chord 500mm, height 80mm."
        ),
        "sub_components": [
            {"name": "Web", "type": "plate", "dims_mm": [500, 80, 2], "lightening_holes": 5},
            {"name": "UpperFlange", "type": "angle", "length_mm": 500, "leg_mm": [20, 2]},
            {"name": "LowerFlange", "type": "angle", "length_mm": 500, "leg_mm": [20, 2]},
            {"name": "Stiffener_1", "type": "bead", "height_mm": 5, "length_mm": 70},
            {"name": "Stiffener_2", "type": "bead", "height_mm": 5, "length_mm": 70},
            {"name": "SparCutout_Front", "type": "notch", "width_mm": 40, "depth_mm": 15},
            {"name": "SparCutout_Rear", "type": "notch", "width_mm": 40, "depth_mm": 15},
        ],
        "dimensions": {
            "chord_mm": 500,
            "height_mm": 80,
            "web_thickness_mm": 2,
            "flange_width_mm": 20,
            "flange_thickness_mm": 2,
            "lightening_hole_dia_mm": 40,
            "lightening_hole_count": 5,
            "stiffener_height_mm": 5,
            "spar_cutout_width_mm": 40,
        },
        "materials": {
            "rib": "Aluminum 7075-T6 (machined from billet or sheet)",
            "fasteners": "Hi-Lok or Cherry rivets, aluminum or titanium",
        },
        "connections": (
            "Upper and lower flanges rivet to wing skin panels. Web sits between "
            "front and rear spars with cutouts for spar caps. Stiffeners prevent "
            "web buckling. Ribs maintain airfoil cross-section shape and transfer "
            "aerodynamic loads to spars."
        ),
        "assembly_order": (
            "1. CNC-machine rib from 7075-T6 plate. "
            "2. Deburr and shot-peen. "
            "3. Anodize (chromic or tartaric-sulfuric). "
            "4. Locate rib between spars. "
            "5. Drill and rivet flanges to skin. "
            "6. Install spar clip angles."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

chord = 500
height = 80
web_t = 2
flange_w = 20
flange_t = 2

web = (
    cq.Workplane("XZ")
    .rect(chord, height)
    .extrude(web_t)
    .translate((0, -web_t / 2, 0))
)
hole_spacing = chord / 6
for i in range(5):
    hx = -chord / 2 + hole_spacing * (i + 1)
    lightening = (
        cq.Workplane("XZ")
        .transformed(offset=(hx, 0, 0))
        .circle(20)
        .extrude(web_t + 2)
        .translate((0, -web_t / 2 - 1, 0))
    )
    web = web.cut(lightening)

for cutout_x in [-chord / 2 + 60, chord / 2 - 60]:
    spar_cutout = (
        cq.Workplane("XZ")
        .rect(40, 15)
        .extrude(web_t + 2)
        .translate((cutout_x, -web_t / 2 - 1, -height / 2 + 7.5))
    )
    web = web.cut(spar_cutout)
    spar_cutout_top = (
        cq.Workplane("XZ")
        .rect(40, 15)
        .extrude(web_t + 2)
        .translate((cutout_x, -web_t / 2 - 1, height / 2 - 7.5))
    )
    web = web.cut(spar_cutout_top)
parts["Web"] = web

upper_flange = (
    cq.Workplane("XY")
    .rect(chord, flange_w)
    .extrude(flange_t)
    .translate((0, 0, height / 2))
)
parts["UpperFlange"] = upper_flange

lower_flange = (
    cq.Workplane("XY")
    .rect(chord, flange_w)
    .extrude(flange_t)
    .translate((0, 0, -height / 2 - flange_t))
)
parts["LowerFlange"] = lower_flange

for s, sx in enumerate([-80, 80]):
    stiffener = (
        cq.Workplane("XZ")
        .rect(70, 5)
        .extrude(web_t)
        .translate((sx, web_t / 2, 0))
    )
    parts[f"Stiffener_{s+1}"] = stiffener
""",
        "tags": ["aerospace", "wing", "rib", "structure", "airframe", "aluminum"],
    },

    # ── AEROSPACE 14: Landing Gear Strut ─────────────────────────────────
    {
        "name": "Landing Gear Strut",
        "category": "aerospace",
        "description": (
            "Oleo-pneumatic main landing gear strut with outer cylinder, "
            "telescoping piston, scissor torque links, and axle mounting lug. "
            "300M ultra-high-strength steel construction."
        ),
        "sub_components": [
            {"name": "OuterCylinder", "type": "tube", "od_mm": 80, "id_mm": 64, "length_mm": 600},
            {"name": "Piston", "type": "tube", "od_mm": 60, "id_mm": 48, "length_mm": 500},
            {"name": "TorqueLink_Upper", "type": "link", "length_mm": 120, "width_mm": 30, "thickness_mm": 10},
            {"name": "TorqueLink_Lower", "type": "link", "length_mm": 120, "width_mm": 30, "thickness_mm": 10},
            {"name": "TorqueLinkPivot", "type": "pin", "dia_mm": 14},
            {"name": "AxleLug", "type": "clevis", "bore_mm": 40, "width_mm": 50},
            {"name": "UpperTrunnion", "type": "pin", "dia_mm": 50},
        ],
        "dimensions": {
            "outer_cylinder_od_mm": 80,
            "outer_cylinder_id_mm": 64,
            "outer_cylinder_length_mm": 600,
            "piston_od_mm": 60,
            "piston_length_mm": 500,
            "stroke_mm": 200,
            "torque_link_length_mm": 120,
            "axle_bore_mm": 40,
            "trunnion_dia_mm": 50,
            "compressed_length_mm": 700,
            "extended_length_mm": 900,
        },
        "materials": {
            "cylinder_and_piston": "300M steel (vacuum arc remelted)",
            "torque_links": "4340 steel, cadmium plated",
            "seals": "Polyurethane and PTFE",
            "fluid": "MIL-PRF-5606 hydraulic fluid + nitrogen gas",
        },
        "connections": (
            "Upper trunnion pins into airframe attachment fitting. Piston "
            "telescopes inside outer cylinder on bronze bushings. Scissor "
            "torque links prevent piston rotation, pivoting at center. Axle "
            "lug at piston bottom receives wheel axle. Oleo fluid and nitrogen "
            "gas provide shock absorption."
        ),
        "assembly_order": (
            "1. Forge and machine outer cylinder. "
            "2. Forge and chrome-plate piston ID/OD. "
            "3. Install seals and metering pin in piston. "
            "4. Insert piston into cylinder. "
            "5. Attach torque links with pivot pin. "
            "6. Service with hydraulic fluid and nitrogen."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

cyl_od = 80
cyl_id = 64
cyl_len = 600
piston_od = 60
piston_id = 48
piston_len = 500
stroke = 200

outer_cyl = (
    cq.Workplane("XY")
    .circle(cyl_od / 2)
    .circle(cyl_id / 2)
    .extrude(cyl_len)
)
parts["OuterCylinder"] = outer_cyl

piston = (
    cq.Workplane("XY")
    .circle(piston_od / 2)
    .circle(piston_id / 2)
    .extrude(piston_len)
    .translate((0, 0, -stroke))
)
parts["Piston"] = piston

upper_trunnion = (
    cq.Workplane("YZ")
    .circle(25)
    .extrude(cyl_od + 40)
    .translate((-cyl_od / 2 - 20, 0, cyl_len - 30))
)
parts["UpperTrunnion"] = upper_trunnion

link_offset_y = cyl_od / 2 + 10
upper_link = (
    cq.Workplane("XZ")
    .rect(30, 120)
    .extrude(10)
    .translate((0, link_offset_y, cyl_len / 2 + 30))
)
pin_hole_top = (
    cq.Workplane("XZ")
    .transformed(offset=(0, 0, cyl_len / 2 + 90))
    .circle(7)
    .extrude(12)
    .translate((0, link_offset_y - 1, 0))
)
pin_hole_mid = (
    cq.Workplane("XZ")
    .transformed(offset=(0, 0, cyl_len / 2 - 30))
    .circle(7)
    .extrude(12)
    .translate((0, link_offset_y - 1, 0))
)
upper_link = upper_link.cut(pin_hole_top).cut(pin_hole_mid)
parts["TorqueLink_Upper"] = upper_link

lower_link = (
    cq.Workplane("XZ")
    .rect(30, 120)
    .extrude(10)
    .translate((0, link_offset_y, cyl_len / 2 - 90))
)
parts["TorqueLink_Lower"] = lower_link

pivot_pin = (
    cq.Workplane("YZ")
    .circle(7)
    .extrude(14)
    .translate((0, link_offset_y - 2, cyl_len / 2 - 30))
)
parts["TorqueLinkPivot"] = pivot_pin

axle_lug = (
    cq.Workplane("XY")
    .box(50, 50, 40)
    .translate((0, 0, -stroke - 20))
)
axle_bore = (
    cq.Workplane("YZ")
    .circle(20)
    .extrude(60)
    .translate((-30, 0, -stroke - 20))
)
axle_lug = axle_lug.cut(axle_bore)
parts["AxleLug"] = axle_lug
""",
        "tags": ["aerospace", "landing-gear", "strut", "oleo", "structure", "steel"],
    },

    # ── AEROSPACE 15: Engine Nacelle Cross-Section ───────────────────────
    {
        "name": "Engine Nacelle Cross-Section",
        "category": "aerospace",
        "description": (
            "Turbofan engine nacelle cross-section showing outer cowl (inlet "
            "ring), inner barrel acoustic liner, thrust reverser cascade area, "
            "and pylon mount flanges. Approximately 1800mm inlet diameter."
        ),
        "sub_components": [
            {"name": "OuterCowl", "type": "ring", "od_mm": 1800, "wall_mm": 8},
            {"name": "InnerBarrel", "type": "ring", "od_mm": 1400, "wall_mm": 6},
            {"name": "AcousticLiner", "type": "ring", "od_mm": 1412, "wall_mm": 12},
            {"name": "ThrustReverserZone", "type": "annular", "id_mm": 1430, "od_mm": 1780},
            {"name": "PylonMountFlange_L", "type": "flange", "width_mm": 100, "thickness_mm": 15},
            {"name": "PylonMountFlange_R", "type": "flange", "width_mm": 100, "thickness_mm": 15},
        ],
        "dimensions": {
            "inlet_dia_mm": 1800,
            "cowl_wall_mm": 8,
            "inner_barrel_dia_mm": 1400,
            "inner_barrel_wall_mm": 6,
            "nacelle_length_mm": 4000,
            "section_length_mm": 200,
            "acoustic_liner_thickness_mm": 12,
            "pylon_flange_width_mm": 100,
        },
        "materials": {
            "outer_cowl": "Carbon fiber / epoxy composite",
            "inner_barrel": "Aluminum honeycomb sandwich (acoustic)",
            "inlet_lip": "Titanium (anti-icing provision)",
            "thrust_reverser": "Composite cascades with titanium frame",
            "pylon_flanges": "Titanium Ti-6Al-4V",
        },
        "connections": (
            "Outer cowl sections hinge open for engine access. Inner barrel "
            "bolts to engine fan case. Pylon mount flanges bolt to wing pylon. "
            "Thrust reverser cascades sit between inner and outer structure, "
            "deployed via hydraulic actuators. Acoustic liner bonded to inner barrel."
        ),
        "assembly_order": (
            "1. Layup and cure composite outer cowl halves. "
            "2. Fabricate inner barrel with acoustic liner. "
            "3. Install thrust reverser cascade panels. "
            "4. Attach pylon mount flanges. "
            "5. Mate nacelle halves around engine. "
            "6. Connect hydraulic and anti-ice systems."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

cowl_r = 900
cowl_wall = 8
inner_r = 700
inner_wall = 6
section_len = 200

outer_cowl = (
    cq.Workplane("XY")
    .circle(cowl_r)
    .circle(cowl_r - cowl_wall)
    .extrude(section_len)
)
parts["OuterCowl"] = outer_cowl

inner_barrel = (
    cq.Workplane("XY")
    .circle(inner_r)
    .circle(inner_r - inner_wall)
    .extrude(section_len)
)
parts["InnerBarrel"] = inner_barrel

acoustic_liner = (
    cq.Workplane("XY")
    .circle(inner_r + 12)
    .circle(inner_r)
    .extrude(section_len)
)
parts["AcousticLiner"] = acoustic_liner

reverser_zone = (
    cq.Workplane("XY")
    .circle(cowl_r - cowl_wall - 5)
    .circle(inner_r + 20)
    .extrude(section_len / 2)
    .translate((0, 0, section_len / 4))
)
parts["ThrustReverserZone"] = reverser_zone

for side, angle in [("L", 90), ("R", -90)]:
    flange_x = cowl_r * math.cos(math.radians(angle))
    flange_y = cowl_r * math.sin(math.radians(angle))
    flange = (
        cq.Workplane("XY")
        .box(100, 15, section_len)
        .translate((flange_x, flange_y, section_len / 2))
    )
    for bz in [section_len * 0.25, section_len * 0.75]:
        bolt = (
            cq.Workplane("XY")
            .transformed(offset=(flange_x, flange_y, bz))
            .circle(8)
            .extrude(20)
        )
        flange = flange.cut(bolt)
    parts[f"PylonMountFlange_{side}"] = flange
""",
        "tags": ["aerospace", "nacelle", "engine", "cowl", "composite", "cross-section"],
    },

    # ── AEROSPACE 16: Propeller Blade ────────────────────────────────────
    {
        "name": "Propeller Blade",
        "category": "aerospace",
        "description": (
            "General aviation propeller blade with tapered airfoil planform, "
            "cylindrical root shank for hub retention, and optional counterweight. "
            "800mm span, root chord 120mm tapering to 60mm tip chord."
        ),
        "sub_components": [
            {"name": "BladeBody", "type": "tapered_airfoil", "span_mm": 800, "root_chord_mm": 120, "tip_chord_mm": 60},
            {"name": "RootShank", "type": "cylinder", "dia_mm": 40, "length_mm": 60},
            {"name": "RetentionCollar", "type": "annular", "od_mm": 55, "id_mm": 40, "width_mm": 15},
            {"name": "Counterweight", "type": "block", "dims_mm": [30, 20, 50]},
            {"name": "LeadingEdge", "type": "strip", "width_mm": 3, "span_mm": 750},
        ],
        "dimensions": {
            "blade_span_mm": 800,
            "root_chord_mm": 120,
            "tip_chord_mm": 60,
            "max_thickness_mm": 18,
            "root_shank_dia_mm": 40,
            "root_shank_length_mm": 60,
            "pitch_angle_root_deg": 35,
            "pitch_angle_tip_deg": 15,
            "total_length_mm": 860,
        },
        "materials": {
            "blade": "Aluminum 2024-T3 or carbon fiber composite",
            "root_shank": "Steel 4340 (or integral aluminum)",
            "leading_edge": "Nickel erosion strip (bonded)",
            "counterweight": "Lead or tungsten",
            "finish": "Polyurethane rain erosion coating",
        },
        "connections": (
            "Root shank inserts into propeller hub bore with retention collar "
            "and lock nut. Blade pitch is adjusted by rotating shank in hub. "
            "Counterweight attaches to root for centrifugal pitch regulation. "
            "Leading edge nickel strip bonded with adhesive."
        ),
        "assembly_order": (
            "1. Forge or layup blade blank. "
            "2. Machine airfoil contour. "
            "3. Machine root shank to tolerance. "
            "4. Bond nickel leading edge strip. "
            "5. Balance blade. "
            "6. Install in hub with retention hardware."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

span = 800
root_chord = 120
tip_chord = 60
max_t = 18

blade_body = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 60))
    .rect(root_chord, max_t)
    .extrude(span)
)
taper_cut = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 60 + span))
    .box(root_chord * 2, max_t * 2, span)
    .cut(
        cq.Workplane("XY")
        .transformed(offset=(0, 0, 60))
        .rect(tip_chord, max_t * 0.6)
        .extrude(span)
    )
)
blade_approx = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, 60))
    .rect(root_chord, max_t)
    .extrude(span)
)
blade_approx = blade_approx.edges("|Z").fillet(8)
parts["BladeBody"] = blade_approx

root_shank = (
    cq.Workplane("XY")
    .circle(20)
    .extrude(60)
)
parts["RootShank"] = root_shank

collar = (
    cq.Workplane("XY")
    .circle(27.5)
    .circle(20)
    .extrude(15)
    .translate((0, 0, 45))
)
parts["RetentionCollar"] = collar

counterweight = (
    cq.Workplane("XY")
    .box(30, 20, 50)
    .translate((0, -max_t / 2 - 10, 30))
)
parts["Counterweight"] = counterweight

le_strip = (
    cq.Workplane("XY")
    .box(3, max_t + 2, 750)
    .translate((-root_chord / 2 + 1.5, 0, 60 + span / 2))
)
parts["LeadingEdgeStrip"] = le_strip
""",
        "tags": ["aerospace", "propeller", "blade", "general-aviation", "rotating"],
    },

    # ── AEROSPACE 17: Aircraft Control Yoke ──────────────────────────────
    {
        "name": "Aircraft Control Yoke",
        "category": "aerospace",
        "description": (
            "Dual-grip aircraft control yoke (W-shaped) with center column tube, "
            "two grip handles spaced 300mm apart, mounting flange for instrument "
            "panel shaft, and cutouts for PTT and trim buttons."
        ),
        "sub_components": [
            {"name": "GripHandle_L", "type": "cylinder", "dia_mm": 28, "length_mm": 100},
            {"name": "GripHandle_R", "type": "cylinder", "dia_mm": 28, "length_mm": 100},
            {"name": "CrossBar", "type": "tube", "od_mm": 25, "length_mm": 300},
            {"name": "CenterColumn", "type": "tube", "od_mm": 30, "id_mm": 24, "length_mm": 250},
            {"name": "MountingFlange", "type": "disc", "od_mm": 60, "bolt_count": 4},
            {"name": "ButtonPanel_L", "type": "cutout", "dims_mm": [25, 15]},
            {"name": "ButtonPanel_R", "type": "cutout", "dims_mm": [25, 15]},
        ],
        "dimensions": {
            "grip_spacing_mm": 300,
            "grip_dia_mm": 28,
            "grip_length_mm": 100,
            "column_od_mm": 30,
            "column_id_mm": 24,
            "column_length_mm": 250,
            "crossbar_length_mm": 300,
            "flange_od_mm": 60,
            "flange_bolt_count": 4,
            "overall_width_mm": 356,
        },
        "materials": {
            "column_and_crossbar": "Aluminum 6061-T6 tube",
            "grips": "Rubber over-mold on aluminum core",
            "flange": "Steel 4130",
            "buttons": "Sealed micro-switches (IP67)",
        },
        "connections": (
            "Center column inserts into instrument panel shaft via mounting "
            "flange with 4 bolts. Crossbar welds to column top. Grip handles "
            "weld to crossbar ends and angle forward. Internal wiring for PTT "
            "and trim switches routes through hollow column."
        ),
        "assembly_order": (
            "1. Cut and bend aluminum tubes. "
            "2. TIG-weld crossbar to column. "
            "3. TIG-weld grip cores to crossbar ends. "
            "4. Weld mounting flange to column base. "
            "5. Route wiring through column. "
            "6. Over-mold rubber grips."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

grip_spacing = 300
grip_dia = 28
grip_len = 100
col_od = 30
col_id = 24
col_len = 250

center_column = (
    cq.Workplane("XY")
    .circle(col_od / 2)
    .circle(col_id / 2)
    .extrude(col_len)
)
parts["CenterColumn"] = center_column

crossbar = (
    cq.Workplane("YZ")
    .circle(col_od / 2 - 1)
    .extrude(grip_spacing)
    .translate((0, -grip_spacing / 2, col_len))
)
parts["CrossBar"] = crossbar

for side, y_off in [("L", -grip_spacing / 2), ("R", grip_spacing / 2)]:
    grip = (
        cq.Workplane("XY")
        .transformed(offset=(0, y_off, col_len))
        .circle(grip_dia / 2)
        .extrude(grip_len)
    )
    btn_cutout = (
        cq.Workplane("XY")
        .box(25, 15, 5)
        .translate((grip_dia / 2 - 3, y_off, col_len + 40))
    )
    grip = grip.cut(btn_cutout)
    parts[f"GripHandle_{side}"] = grip

flange = (
    cq.Workplane("XY")
    .circle(30)
    .circle(col_od / 2)
    .extrude(8)
    .translate((0, 0, -8))
)
for i in range(4):
    angle = math.radians(i * 90 + 45)
    bx = 22 * math.cos(angle)
    by = 22 * math.sin(angle)
    bolt = (
        cq.Workplane("XY")
        .transformed(offset=(bx, by, -10))
        .circle(3)
        .extrude(12)
    )
    flange = flange.cut(bolt)
parts["MountingFlange"] = flange
""",
        "tags": ["aerospace", "controls", "yoke", "cockpit", "flight-controls"],
    },

    # ── AEROSPACE 18: Hydraulic Actuator ─────────────────────────────────
    {
        "name": "Hydraulic Actuator",
        "category": "aerospace",
        "description": (
            "Double-acting hydraulic linear actuator with 50mm bore, 200mm stroke, "
            "25mm piston rod, end caps with hydraulic port fittings, and rod-end "
            "eye for clevis attachment. Used for flight control surfaces."
        ),
        "sub_components": [
            {"name": "CylinderBody", "type": "tube", "bore_mm": 50, "od_mm": 60, "length_mm": 250},
            {"name": "Piston", "type": "disc", "dia_mm": 49.5, "thickness_mm": 25, "seal_grooves": 2},
            {"name": "PistonRod", "type": "cylinder", "dia_mm": 25, "length_mm": 260},
            {"name": "HeadEndCap", "type": "disc", "od_mm": 60, "thickness_mm": 20, "port_dia_mm": 8},
            {"name": "RodEndCap", "type": "disc", "od_mm": 60, "thickness_mm": 20, "rod_seal_bore_mm": 25.5},
            {"name": "RodEndEye", "type": "clevis", "bore_mm": 16, "width_mm": 20},
            {"name": "HeadMountEye", "type": "clevis", "bore_mm": 16, "width_mm": 20},
        ],
        "dimensions": {
            "bore_mm": 50,
            "rod_dia_mm": 25,
            "stroke_mm": 200,
            "cylinder_od_mm": 60,
            "cylinder_wall_mm": 5,
            "cylinder_length_mm": 250,
            "piston_thickness_mm": 25,
            "end_cap_thickness_mm": 20,
            "port_dia_mm": 8,
            "rod_end_eye_bore_mm": 16,
            "overall_retracted_mm": 350,
            "overall_extended_mm": 550,
        },
        "materials": {
            "cylinder": "Steel 4130 (hard chrome bore)",
            "piston": "Ductile iron or aluminum bronze",
            "rod": "Chrome-plated 17-4PH stainless steel",
            "end_caps": "Steel 4130",
            "seals": "Polyurethane U-cups + PTFE guide rings",
            "rod_end": "Steel with PTFE-lined spherical bearing",
        },
        "connections": (
            "Head-end mount eye pins to airframe bracket (clevis). Rod-end eye "
            "pins to control surface horn. Hydraulic ports in end caps connect "
            "to hydraulic system via AN fittings. Extend port on head-end cap, "
            "retract port on rod-end cap."
        ),
        "assembly_order": (
            "1. Hone cylinder bore to finish (Ra 0.2 um). "
            "2. Install piston seals on piston. "
            "3. Thread piston onto rod. "
            "4. Install rod seal in rod-end cap. "
            "5. Insert piston+rod assembly into cylinder. "
            "6. Install end caps (threaded or bolted). "
            "7. Install port fittings. "
            "8. Pressure test."
        ),
        "cadquery_example": """\
import cadquery as cq
import math

parts = {}

bore = 50
rod_dia = 25
stroke = 200
cyl_od = 60
cyl_wall = 5
cyl_len = 250
cap_t = 20
piston_t = 25

cylinder_body = (
    cq.Workplane("XY")
    .circle(cyl_od / 2)
    .circle(bore / 2)
    .extrude(cyl_len)
    .translate((0, 0, cap_t))
)
parts["CylinderBody"] = cylinder_body

piston = (
    cq.Workplane("XY")
    .circle(bore / 2 - 0.25)
    .circle(rod_dia / 2)
    .extrude(piston_t)
    .translate((0, 0, cap_t + cyl_len - piston_t - 10))
)
for g in range(2):
    groove = (
        cq.Workplane("XY")
        .circle(bore / 2)
        .circle(bore / 2 - 3)
        .extrude(3)
        .translate((0, 0, cap_t + cyl_len - piston_t - 10 + 5 + g * 10))
    )
    piston = piston.cut(groove)
parts["Piston"] = piston

rod = (
    cq.Workplane("XY")
    .circle(rod_dia / 2)
    .extrude(cyl_len + stroke + cap_t)
    .translate((0, 0, cap_t))
)
parts["PistonRod"] = rod

head_cap = (
    cq.Workplane("XY")
    .circle(cyl_od / 2)
    .extrude(cap_t)
)
port_head = (
    cq.Workplane("XY")
    .transformed(offset=(cyl_od / 2 - 5, 0, cap_t / 2))
    .circle(4)
    .extrude(15)
)
head_cap = head_cap.cut(port_head)
parts["HeadEndCap"] = head_cap

head_eye = (
    cq.Workplane("XY")
    .box(20, 30, 20)
    .translate((0, 0, -10))
)
eye_bore = (
    cq.Workplane("YZ")
    .circle(8)
    .extrude(22)
    .translate((-11, 0, -10))
)
head_eye = head_eye.cut(eye_bore)
parts["HeadMountEye"] = head_eye

rod_cap = (
    cq.Workplane("XY")
    .circle(cyl_od / 2)
    .extrude(cap_t)
    .translate((0, 0, cap_t + cyl_len))
)
rod_bore = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, cap_t + cyl_len))
    .circle(rod_dia / 2 + 0.25)
    .extrude(cap_t)
)
rod_cap = rod_cap.cut(rod_bore)
port_rod = (
    cq.Workplane("XY")
    .transformed(offset=(cyl_od / 2 - 5, 0, cap_t + cyl_len + cap_t / 2))
    .circle(4)
    .extrude(15)
)
rod_cap = rod_cap.cut(port_rod)
parts["RodEndCap"] = rod_cap

rod_end_z = cap_t + cyl_len + stroke + cap_t
rod_eye = (
    cq.Workplane("XY")
    .box(20, 30, 20)
    .translate((0, 0, rod_end_z + 10))
)
rod_eye_bore = (
    cq.Workplane("YZ")
    .circle(8)
    .extrude(22)
    .translate((-11, 0, rod_end_z + 10))
)
rod_eye = rod_eye.cut(rod_eye_bore)
parts["RodEndEye"] = rod_eye
""",
        "tags": ["aerospace", "hydraulic", "actuator", "flight-controls", "linear"],
    },
]


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS engineering_references (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    sub_components  JSONB NOT NULL,
    dimensions      JSONB NOT NULL,
    materials       JSONB NOT NULL,
    connections     TEXT NOT NULL,
    assembly_order  TEXT NOT NULL,
    cadquery_example TEXT NOT NULL,
    tags            JSONB NOT NULL,
    created_by  TEXT NOT NULL DEFAULT 'seed',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ref_search ON engineering_references
    USING gin(to_tsvector('english', name || ' ' || description || ' ' || category));
"""

INSERT_SQL = """
INSERT INTO engineering_references
    (name, category, description, sub_components, dimensions, materials,
     connections, assembly_order, cadquery_example, tags)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (name) DO NOTHING;
"""


def seed():
    init_db()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            for entry in SEED_DATA:
                cur.execute(INSERT_SQL, (
                    entry["name"],
                    entry["category"],
                    entry["description"],
                    json.dumps(entry["sub_components"]),
                    json.dumps(entry["dimensions"]),
                    json.dumps(entry["materials"]),
                    entry["connections"],
                    entry["assembly_order"],
                    entry["cadquery_example"],
                    json.dumps(entry["tags"]),
                ))
        conn.commit()
        print(f"Seeded {len(SEED_DATA)} engineering references.")
    finally:
        put_connection(conn)
        close_db()


if __name__ == "__main__":
    seed()
