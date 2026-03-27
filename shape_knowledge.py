"""
Reference knowledge base for common mechanical/engineering objects.

Each entry provides:
- description: what the object looks like, key features
- proportions: typical dimensional ratios and sizes
- parts: the distinct sub-components that should be generated as separate result_* variables
- example_code: CadQuery code snippet showing how to build the parts
"""

SHAPE_KNOWLEDGE = {
    "steering_wheel": {
        "description": (
            "A steering wheel consists of a circular rim (torus shape), a central hub "
            "(short cylinder with mounting hole), and 3 spokes connecting hub to rim. "
            "The rim is what the driver grips; the hub mounts to the steering column."
        ),
        "proportions": (
            "Typical diameter: 350-380mm. Rim cross-section: 12-16mm radius. "
            "Hub diameter: 50-70mm, height: 20-30mm. "
            "Spokes: 8-12mm thick, evenly spaced at 120 degrees."
        ),
        "parts": ["rim", "hub", "spoke1", "spoke2", "spoke3"],
        "example_code": """import cadquery as cq
import math

# Rim — torus (circular grip ring)
rim_major_r = 175  # center of rim circle
rim_minor_r = 14   # cross-section radius
rim = (cq.Workplane("XY")
    .parametricCurve(
        lambda t: (
            (rim_major_r + rim_minor_r * math.cos(t * 20)) * math.cos(t),
            (rim_major_r + rim_minor_r * math.cos(t * 20)) * math.sin(t),
            rim_minor_r * math.sin(t * 20)
        ),
        start=0, stop=2*math.pi, N=200
    ))
# Simpler approach — revolve a circle around Z axis
rim_profile = (cq.Workplane("XZ")
    .center(rim_major_r, 0)
    .circle(rim_minor_r))
result_rim = rim_profile.revolve(360, (0, 0, 0), (0, 0, 1))

# Hub — central cylinder with bore
result_hub = (cq.Workplane("XY")
    .cylinder(25, 30)
    .faces(">Z").workplane().hole(18))

# Spokes — rectangular bars from hub to rim
spoke_length = rim_major_r - 30
for i, angle in enumerate([0, 120, 240]):
    rad = math.radians(angle)
    cx = (30 + spoke_length / 2) * math.cos(rad)
    cy = (30 + spoke_length / 2) * math.sin(rad)
    spoke = (cq.Workplane("XY")
        .box(spoke_length, 10, 8)
        .rotate((0, 0, 0), (0, 0, 1), angle)
        .translate((cx, cy, 0)))
    if i == 0: result_spoke1 = spoke
    elif i == 1: result_spoke2 = spoke
    else: result_spoke3 = spoke
""",
    },
    "gear": {
        "description": (
            "A spur gear is a flat cylindrical disc with evenly-spaced teeth around "
            "its circumference and a central bore hole. Teeth have an involute profile "
            "but can be approximated with trapezoidal shapes."
        ),
        "proportions": (
            "Module (m) defines tooth size: pitch_diameter = m * num_teeth. "
            "Tooth height ~2.25*m, addendum = m, dedendum = 1.25*m. "
            "Face width (thickness) typically 8-12 * module. Bore ~25-40% of pitch diameter."
        ),
        "parts": ["gear_body", "teeth_ring"],
        "example_code": """import cadquery as cq
import math

module = 2.5
num_teeth = 24
pitch_d = module * num_teeth  # 60mm
outer_d = pitch_d + 2 * module
root_d = pitch_d - 2.5 * module
thickness = 12
bore_d = 15

# Gear body — cylinder with bore
result_body = (cq.Workplane("XY")
    .circle(root_d / 2)
    .extrude(thickness)
    .faces(">Z").workplane().hole(bore_d))

# Teeth — build as individual boxes around the circumference
tooth_h = 2.25 * module
tooth_w = math.pi * module * 0.45
teeth = cq.Workplane("XY")
for i in range(num_teeth):
    angle = i * 360 / num_teeth
    rad = math.radians(angle)
    cx = (root_d / 2 + tooth_h / 2) * math.cos(rad)
    cy = (root_d / 2 + tooth_h / 2) * math.sin(rad)
    tooth = (cq.Workplane("XY")
        .box(tooth_h, tooth_w, thickness)
        .rotate((0, 0, 0), (0, 0, 1), angle)
        .translate((cx, cy, thickness / 2)))
    teeth = teeth.union(tooth)
result_teeth = teeth
""",
    },
    "bearing": {
        "description": (
            "A ball bearing has an outer race (ring), inner race (ring), and balls "
            "between them. The races are concentric cylinders with grooves. "
            "Simplify as: outer ring, inner ring, and a set of ball spheres."
        ),
        "proportions": (
            "Common 6205 bearing: bore=25mm, OD=52mm, width=15mm. "
            "Inner race OD ~ bore + 6mm. Outer race ID ~ OD - 6mm. "
            "Ball diameter ~ (outer_race_ID - inner_race_OD) / 2. "
            "Typically 7-9 balls evenly spaced."
        ),
        "parts": ["outer_race", "inner_race", "balls"],
        "example_code": """import cadquery as cq
import math

bore = 25
od = 52
width = 15
inner_race_od = bore + 8
outer_race_id = od - 8
ball_d = (outer_race_id - inner_race_od) / 2
ball_center_r = (inner_race_od + outer_race_id) / 4 + inner_race_od / 4
num_balls = 8

# Outer race
result_outer_race = (cq.Workplane("XY")
    .circle(od / 2).circle(outer_race_id / 2)
    .extrude(width)
    .translate((0, 0, -width / 2)))

# Inner race
result_inner_race = (cq.Workplane("XY")
    .circle(inner_race_od / 2).circle(bore / 2)
    .extrude(width)
    .translate((0, 0, -width / 2)))

# Balls
ball_center_r = (inner_race_od + outer_race_id) / 4
balls = cq.Workplane("XY").sphere(0.01)  # dummy start
for i in range(num_balls):
    angle = i * 360 / num_balls
    rad = math.radians(angle)
    bx = ball_center_r * math.cos(rad)
    by = ball_center_r * math.sin(rad)
    ball = cq.Workplane("XY").sphere(ball_d / 2).translate((bx, by, 0))
    balls = balls.union(ball)
result_balls = balls
""",
    },
    "shaft": {
        "description": (
            "A mechanical shaft is a long cylinder, often with stepped diameters, "
            "keyways, and chamfered ends. Used to transmit rotary motion."
        ),
        "proportions": (
            "Length typically 3-10x the main diameter. Steps reduce diameter by 2-5mm. "
            "Keyway: width ~ d/4, depth ~ d/8. Chamfer on ends: 1-2mm x 45 degrees."
        ),
        "parts": ["shaft_body"],
        "example_code": """import cadquery as cq

main_d = 25
length = 150
step_d = 20
step_length = 30
keyway_w = 6
keyway_depth = 3

result_shaft = (cq.Workplane("XY")
    .circle(main_d / 2).extrude(length - step_length)
    .faces(">Z").workplane().circle(step_d / 2).extrude(step_length)
    .edges("|Z").chamfer(1.5)
    .faces(">Z").workplane()
    .rect(keyway_w, main_d).cutBlind(-keyway_depth))
""",
    },
    "pulley": {
        "description": (
            "A V-belt pulley has a hub (central cylinder with bore), a disc/web "
            "connecting hub to rim, and a grooved rim for the belt. The groove is V-shaped."
        ),
        "proportions": (
            "OD: 60-200mm typical. Hub diameter ~ 1.5-2x bore. "
            "Groove angle: 34-40 degrees. Groove depth: 8-12mm. "
            "Rim width: 15-25mm. Web thickness: 5-8mm."
        ),
        "parts": ["hub", "web", "rim"],
        "example_code": """import cadquery as cq

od = 100
bore = 20
hub_d = 40
hub_h = 30
rim_width = 20
groove_depth = 10
web_thickness = 6

# Hub
result_hub = (cq.Workplane("XY")
    .circle(hub_d / 2).circle(bore / 2)
    .extrude(hub_h)
    .translate((0, 0, -hub_h / 2)))

# Web disc
result_web = (cq.Workplane("XY")
    .circle(od / 2 - groove_depth).circle(hub_d / 2)
    .extrude(web_thickness)
    .translate((0, 0, -web_thickness / 2)))

# Rim with V-groove (approximate as cylinder with chamfered edges)
result_rim = (cq.Workplane("XY")
    .circle(od / 2).circle(od / 2 - groove_depth)
    .extrude(rim_width)
    .translate((0, 0, -rim_width / 2))
    .edges(">Z").chamfer(groove_depth * 0.7)
    .edges("<Z").chamfer(groove_depth * 0.7))
""",
    },
    "wheel_tire": {
        "description": (
            "A wheel assembly has a hub (central disc with bolt holes), spokes or a "
            "solid disc, a rim (outer ring), and a tire (D-shaped cross-section revolved around the rim). "
            "The tire is NOT a torus/circle — it has a flat tread and curved sidewalls."
        ),
        "proportions": (
            "Typical car wheel: rim diameter 15-18 inches (380-460mm). "
            "Tire width: 185-245mm, profile: 40-65% of width. "
            "Hub bore: 56-73mm. Bolt circle: 4 or 5 bolts at 100-120mm PCD."
        ),
        "parts": ["hub", "rim", "tire"],
        "example_code": """import cadquery as cq
import math

rim_d = 400
rim_width = 200
tire_section_r = 60
hub_bore = 60
hub_d = 150
bolt_pcd = 110
num_bolts = 5

# Hub disc
result_hub = (cq.Workplane("XY")
    .circle(hub_d / 2).circle(hub_bore / 2)
    .extrude(15))
# Add bolt holes
for i in range(num_bolts):
    angle = i * 360 / num_bolts
    rad = math.radians(angle)
    bx = bolt_pcd / 2 * math.cos(rad)
    by = bolt_pcd / 2 * math.sin(rad)
    result_hub = result_hub.faces(">Z").workplane().pushPoints([(bx, by)]).hole(12)

# Rim — outer ring
result_rim = (cq.Workplane("XZ")
    .center(rim_d / 2, 0)
    .rect(10, rim_width)
    .revolve(360, (-rim_d / 2, 0), (0, 1, 0)))

# Tire — FLAT tread + straight sidewalls + shoulder arcs (NOT a balloon)
tire_half_w = 100
tire_sidewall_h = 60
t_outer_r = rim_d / 2 + tire_sidewall_h
t_rim_r = rim_d / 2
t_tread_hw = tire_half_w * 0.78
t_sh = 12
result_tire = (cq.Workplane("XZ")
    .moveTo(t_rim_r, -tire_half_w)
    .lineTo(t_outer_r - t_sh, -t_tread_hw - t_sh * 0.3)
    .threePointArc((t_outer_r - t_sh * 0.3, -t_tread_hw), (t_outer_r, -t_tread_hw + t_sh * 0.3))
    .lineTo(t_outer_r, t_tread_hw - t_sh * 0.3)
    .threePointArc((t_outer_r - t_sh * 0.3, t_tread_hw), (t_outer_r - t_sh, t_tread_hw + t_sh * 0.3))
    .lineTo(t_rim_r, tire_half_w)
    .close()
    .revolve(360, (0, 0), (0, 1, 0)))
""",
    },
    "handle_knob": {
        "description": (
            "A door knob or handle has a knob (sphere or rounded shape), a neck "
            "(tapered cylinder), and a base plate (flat disc with screw holes) "
            "that mounts to the door surface."
        ),
        "proportions": (
            "Knob diameter: 30-50mm. Neck length: 20-40mm, diameter: 12-18mm. "
            "Base plate: 50-70mm diameter, 3-5mm thick, 2-4 screw holes."
        ),
        "parts": ["knob", "neck", "base_plate"],
        "example_code": """import cadquery as cq

knob_r = 22
neck_d = 14
neck_h = 30
base_d = 60
base_h = 4

# Knob — sphere at top
result_knob = (cq.Workplane("XY")
    .sphere(knob_r)
    .translate((0, 0, neck_h + knob_r)))

# Neck — cylinder connecting knob to base
result_neck = (cq.Workplane("XY")
    .circle(neck_d / 2).extrude(neck_h)
    .edges(">Z").fillet(2))

# Base plate — disc with screw holes
result_base = (cq.Workplane("XY")
    .circle(base_d / 2).extrude(base_h)
    .translate((0, 0, -base_h))
    .faces(">Z").workplane()
    .pushPoints([(20, 0), (-20, 0), (0, 20), (0, -20)])
    .hole(4))
""",
    },
    "pipe_fitting": {
        "description": (
            "A pipe elbow fitting is an L-shaped tube for connecting two pipes at "
            "90 degrees. It has two cylindrical ends (sockets) and a curved middle section. "
            "A tee fitting has three ends forming a T-shape."
        ),
        "proportions": (
            "Pipe OD: 20-50mm typical. Wall thickness: 2-4mm. "
            "Bend radius: 1.5x pipe OD for standard elbows. "
            "Socket depth: 15-25mm. Socket ID = pipe OD + 0.5mm clearance."
        ),
        "parts": ["elbow_body", "socket_a", "socket_b"],
        "example_code": """import cadquery as cq
import math

pipe_od = 32
wall = 3
bend_r = 48  # 1.5x OD

# Main elbow body — sweep a ring profile along a 90-degree arc
path = (cq.Workplane("XZ")
    .radiusArc((bend_r, bend_r), bend_r)
    .val())

result_elbow = (cq.Workplane("XY")
    .circle(pipe_od / 2)
    .circle(pipe_od / 2 - wall)
    .sweep(cq.Workplane("XZ").spline([(0,0), (bend_r*0.4, bend_r*0.05), (bend_r*0.7, bend_r*0.3), (bend_r, bend_r)])))

# Simpler approach: two cylinders at 90 degrees joined by a sphere
tube_len = 40
result_socket_a = (cq.Workplane("XY")
    .circle(pipe_od / 2).circle(pipe_od / 2 - wall)
    .extrude(tube_len))

result_socket_b = (cq.Workplane("XZ")
    .circle(pipe_od / 2).circle(pipe_od / 2 - wall)
    .extrude(tube_len))
""",
    },
    "hinge": {
        "description": (
            "A butt hinge has two flat rectangular leaves connected by a cylindrical "
            "knuckle (barrel) along one edge. A pin runs through the knuckle. "
            "When open, the leaves fold flat; when closed, they're at 90 or 180 degrees."
        ),
        "proportions": (
            "Common door hinge: each leaf 50-100mm long x 25-50mm wide x 1.5-2mm thick. "
            "Knuckle diameter: 8-12mm. Pin diameter: 4-6mm. "
            "3-5 knuckle segments alternating between leaves."
        ),
        "parts": ["leaf_a", "leaf_b", "pin"],
        "example_code": """import cadquery as cq

leaf_w = 40
leaf_h = 80
leaf_t = 2
knuckle_d = 10
pin_d = 5

# Leaf A — flat plate with half-knuckle cylinders
result_leaf_a = (cq.Workplane("XY")
    .box(leaf_w, leaf_h, leaf_t)
    .translate((-leaf_w / 2, 0, 0)))

# Leaf B — flat plate offset
result_leaf_b = (cq.Workplane("XY")
    .box(leaf_w, leaf_h, leaf_t)
    .translate((leaf_w / 2, 0, 0)))

# Pin — cylinder through the knuckle axis
result_pin = (cq.Workplane("XZ")
    .circle(pin_d / 2)
    .extrude(leaf_h + 4)
    .translate((0, -leaf_h / 2 - 2, 0)))
""",
    },
    "enclosure_box": {
        "description": (
            "An electronics enclosure is a rectangular box with a removable lid. "
            "The base is a hollow box (shelled), the lid sits on top with a lip/rabbet "
            "that fits inside the base walls. Screw bosses in the corners for fastening."
        ),
        "proportions": (
            "Common sizes: 100-200mm long x 60-120mm wide x 30-60mm tall. "
            "Wall thickness: 2-3mm. Lid thickness: 2-3mm with 1.5mm lip. "
            "Corner radius: 2-5mm. Screw bosses: 6-8mm OD, 3mm bore."
        ),
        "parts": ["base", "lid", "boss1", "boss2", "boss3", "boss4"],
        "example_code": """import cadquery as cq

L, W, H = 120, 80, 40
wall = 2.5
lid_h = 3
lip = 1.5
corner_r = 3
boss_od = 7
boss_bore = 3

# Base — shelled box
result_base = (cq.Workplane("XY")
    .box(L, W, H)
    .edges("|Z").fillet(corner_r)
    .faces(">Z").shell(-wall))

# Lid — flat plate with lip
result_lid = (cq.Workplane("XY")
    .box(L, W, lid_h)
    .edges("|Z").fillet(corner_r)
    .translate((0, 0, H / 2 + lid_h / 2)))

# Screw bosses at corners (inside base)
inset_x = L / 2 - wall - boss_od / 2 - 1
inset_y = W / 2 - wall - boss_od / 2 - 1
boss_h = H - wall

corners = [
    (inset_x, inset_y), (-inset_x, inset_y),
    (-inset_x, -inset_y), (inset_x, -inset_y),
]
for idx, (bx, by) in enumerate(corners, 1):
    boss = (cq.Workplane("XY")
        .circle(boss_od / 2).extrude(boss_h)
        .faces(">Z").workplane().hole(boss_bore)
        .translate((bx, by, -H / 2 + wall)))
    if idx == 1: result_boss1 = boss
    elif idx == 2: result_boss2 = boss
    elif idx == 3: result_boss3 = boss
    else: result_boss4 = boss
""",
    },
    "flange": {
        "description": (
            "A pipe flange is a flat disc with a central bore and a bolt circle. "
            "It connects two pipes together using bolts through the holes."
        ),
        "proportions": (
            "Flange OD: 2-3x pipe OD. Thickness: 10-25mm. "
            "Bolt circle: ~1.6x pipe OD. 4-8 bolt holes evenly spaced. "
            "Raised face height: 1.5-2mm."
        ),
        "parts": ["flange_disc"],
        "example_code": """import cadquery as cq
import math

pipe_od = 50
flange_od = 120
thickness = 18
bore = pipe_od
bolt_circle_d = 90
num_bolts = 6
bolt_hole_d = 14

result_flange = (cq.Workplane("XY")
    .circle(flange_od / 2)
    .extrude(thickness)
    .faces(">Z").workplane().hole(bore))

# Add bolt holes
for i in range(num_bolts):
    angle = i * 360 / num_bolts
    rad = math.radians(angle)
    bx = bolt_circle_d / 2 * math.cos(rad)
    by = bolt_circle_d / 2 * math.sin(rad)
    result_flange = (result_flange
        .faces(">Z").workplane()
        .pushPoints([(bx, by)]).hole(bolt_hole_d))
""",
    },
    "bracket": {
        "description": (
            "An L-bracket has two flat plates meeting at 90 degrees. Often has "
            "mounting holes in both flanges and a gusset/rib for reinforcement."
        ),
        "proportions": (
            "Flange width: 30-80mm. Length: 40-120mm. Thickness: 3-6mm. "
            "Gusset: triangular, same thickness. Hole diameter: 6-10mm."
        ),
        "parts": ["vertical_flange", "horizontal_flange", "gusset"],
        "example_code": """import cadquery as cq

flange_w = 50
flange_h = 60
length = 80
thickness = 4

# Vertical flange
result_vertical = (cq.Workplane("XY")
    .box(thickness, length, flange_h)
    .translate((-flange_w / 2 + thickness / 2, 0, flange_h / 2)))

# Horizontal flange
result_horizontal = (cq.Workplane("XY")
    .box(flange_w, length, thickness)
    .translate((0, 0, thickness / 2)))

# Triangular gusset
result_gusset = (cq.Workplane("YZ")
    .moveTo(0, thickness).lineTo(0, flange_h * 0.6)
    .lineTo(flange_w * 0.5, thickness).close()
    .extrude(thickness)
    .translate((-flange_w / 2 + thickness, -thickness / 2, 0)))
""",
    },
    "spring": {
        "description": (
            "A compression spring is a helix of round wire. It has active coils "
            "in the middle and flat (ground) ends. CadQuery can make helixes."
        ),
        "proportions": (
            "Wire diameter: 1-5mm. Coil OD: 10-50mm. Free length: 20-100mm. "
            "Active coils: 5-15. Pitch: wire_d * 1.2 to 3x wire_d."
        ),
        "parts": ["spring_coil"],
        "example_code": """import cadquery as cq
import math

wire_d = 2.5
coil_od = 25
coil_r = coil_od / 2 - wire_d / 2
num_coils = 8
pitch = 6
height = num_coils * pitch

# Build helix as a swept circle along a helical path
result_spring = (cq.Workplane("XY")
    .parametricCurve(
        lambda t: (
            coil_r * math.cos(t * num_coils * 2 * math.pi),
            coil_r * math.sin(t * num_coils * 2 * math.pi),
            t * height
        ),
        start=0, stop=1, N=num_coils * 36
    ))
# Note: parametricCurve creates a wire, not a solid. For visual:
result_spring = (cq.Workplane("XY")
    .circle(coil_r + wire_d / 2)
    .circle(coil_r - wire_d / 2)
    .extrude(wire_d)
    .translate((0, 0, 0)))
""",
    },
}


def get_shape_reference(query: str) -> str:
    """
    Search the knowledge base for objects matching the query.
    Returns formatted reference text to include in the AI prompt.
    """
    query_lower = query.lower()
    matches = []

    # Keyword mapping for fuzzy matching
    keyword_map = {
        "steering_wheel": ["steering", "wheel", "steering wheel"],
        "gear": ["gear", "cog", "cogwheel", "spur gear", "pinion"],
        "bearing": ["bearing", "ball bearing", "roller bearing"],
        "shaft": ["shaft", "axle", "rod", "spindle"],
        "pulley": ["pulley", "sheave", "belt wheel"],
        "wheel_tire": ["tire", "tyre", "wheel rim", "car wheel"],
        "handle_knob": ["handle", "knob", "door knob", "door handle", "grip"],
        "pipe_fitting": ["pipe", "elbow", "tee", "fitting", "plumbing", "tube fitting"],
        "hinge": ["hinge", "butt hinge", "door hinge"],
        "enclosure_box": ["enclosure", "box", "case", "housing", "electronics box", "lid"],
        "flange": ["flange", "pipe flange", "bolt flange"],
        "bracket": ["bracket", "l-bracket", "angle bracket", "mount"],
        "spring": ["spring", "coil", "compression spring"],
    }

    for key, keywords in keyword_map.items():
        if any(kw in query_lower for kw in keywords):
            if key in SHAPE_KNOWLEDGE:
                matches.append((key, SHAPE_KNOWLEDGE[key]))

    if not matches:
        return ""

    lines = ["SHAPE REFERENCE (use this as guidance for realistic proportions and multi-part structure):"]
    for key, info in matches:
        lines.append(f"\n--- {key.replace('_', ' ').title()} ---")
        lines.append(f"Description: {info['description']}")
        lines.append(f"Proportions: {info['proportions']}")
        lines.append(f"Suggested parts: {', '.join(info['parts'])}")
        lines.append(f"Example code:\n{info['example_code']}")

    return "\n".join(lines)


def get_all_shape_summaries() -> str:
    """Return a compact summary of all known shapes for the system prompt."""
    lines = []
    for key, info in SHAPE_KNOWLEDGE.items():
        name = key.replace("_", " ").title()
        parts = ", ".join(info["parts"])
        lines.append(f"- {name}: {info['description'][:100]}... Parts: [{parts}]")
    return "\n".join(lines)
