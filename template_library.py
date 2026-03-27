"""Template Library — seed data and template generators for the Aleksma AI template marketplace.

This module:
1. Registers 15+ parametric templates into TEMPLATE_REGISTRY
2. Provides seed_templates() to insert category + template metadata into PostgreSQL
3. Each template generates valid CadQuery scripts with parametric variables
"""

from parametric_templates import register_template, TEMPLATE_REGISTRY
import json

# ═══════════════════════════════════════════════════════════════════════════
# Category definitions
# ═══════════════════════════════════════════════════════════════════════════

CATEGORIES = [
    {"name": "Brackets & Mounts", "slug": "brackets-mounts", "description": "Structural brackets, wall mounts, and mounting hardware", "icon": "Wrench", "sort_order": 1},
    {"name": "Enclosures & Cases", "slug": "enclosures-cases", "description": "Boxes, cases, and enclosures for electronics and projects", "icon": "Box", "sort_order": 2},
    {"name": "Gears & Mechanisms", "slug": "gears-mechanisms", "description": "Gears, pulleys, and mechanical drive components", "icon": "Cog", "sort_order": 3},
    {"name": "Structural", "slug": "structural", "description": "Beams, extrusions, and structural profiles", "icon": "Columns3", "sort_order": 4},
    {"name": "Fasteners & Hardware", "slug": "fasteners-hardware", "description": "Bolts, washers, standoffs, and common hardware", "icon": "CircleDot", "sort_order": 5},
    {"name": "Automotive", "slug": "automotive", "description": "Wheels, brake components, and automotive parts", "icon": "Car", "sort_order": 6},
]

# Map template slug → category slug for seeding
TEMPLATE_CATEGORIES = {
    "l-bracket": "brackets-mounts",
    "u-bracket": "brackets-mounts",
    "wall-mount-plate": "brackets-mounts",
    "din-rail-mount": "brackets-mounts",
    "rectangular-enclosure": "enclosures-cases",
    "round-electronics-case": "enclosures-cases",
    "spur-gear": "gears-mechanisms",
    "pulley": "gears-mechanisms",
    "t-slot-extrusion": "structural",
    "i-beam": "structural",
    "angle-iron": "structural",
    "hex-bolt": "fasteners-hardware",
    "flat-washer": "fasteners-hardware",
    "standoff-spacer": "fasteners-hardware",
    "alloy_rim": "automotive",
    "brake_disc": "automotive",
}

TEMPLATE_DESCRIPTIONS = {
    "l-bracket": "Standard L-shaped bracket for mounting and structural connections. Configurable dimensions and mounting holes.",
    "u-bracket": "U-shaped channel bracket for clamping and securing round or rectangular components.",
    "wall-mount-plate": "Flat mounting plate with configurable hole pattern. Perfect for wall mounting electronics or equipment.",
    "din-rail-mount": "DIN rail compatible mounting clip for industrial enclosures and electrical components.",
    "rectangular-enclosure": "Rectangular box with removable lid. Ideal for electronics enclosures, project boxes, and 3D printing.",
    "round-electronics-case": "Cylindrical case with snap-fit lid. Great for sensors, batteries, and small electronics.",
    "spur-gear": "Involute spur gear with configurable module, tooth count, and bore. Ready for meshing.",
    "pulley": "V-belt or flat pulley with configurable groove profile and hub dimensions.",
    "t-slot-extrusion": "Standard T-slot aluminum extrusion profile (20-series or 40-series compatible).",
    "i-beam": "Standard I-beam / H-beam structural profile with configurable flanges and web.",
    "angle-iron": "L-shaped angle profile for structural framing and support.",
    "hex-bolt": "Standard hex head bolt with configurable thread diameter and length.",
    "flat-washer": "Standard flat washer with configurable inner/outer diameter and thickness.",
    "standoff-spacer": "Threaded standoff or spacer for PCB mounting and panel spacing.",
    "alloy_rim": "Parametric automotive alloy wheel with configurable spokes, bolt pattern, and dimensions.",
    "brake_disc": "Ventilated brake disc with configurable hat, bolt pattern, and cooling slots.",
}

TEMPLATE_TAGS = {
    "l-bracket": ["bracket", "mount", "structural", "angle", "3d-print"],
    "u-bracket": ["bracket", "channel", "clamp", "structural", "3d-print"],
    "wall-mount-plate": ["plate", "mount", "wall", "holes", "3d-print"],
    "din-rail-mount": ["din-rail", "industrial", "mount", "clip", "3d-print"],
    "rectangular-enclosure": ["box", "case", "enclosure", "electronics", "3d-print", "lid"],
    "round-electronics-case": ["case", "cylinder", "electronics", "snap-fit", "3d-print"],
    "spur-gear": ["gear", "involute", "mechanism", "drive", "3d-print"],
    "pulley": ["pulley", "belt", "drive", "mechanism"],
    "t-slot-extrusion": ["extrusion", "t-slot", "aluminum", "frame", "2020", "4040"],
    "i-beam": ["beam", "structural", "steel", "profile"],
    "angle-iron": ["angle", "structural", "steel", "profile", "framing"],
    "hex-bolt": ["bolt", "hex", "fastener", "thread", "M-series"],
    "flat-washer": ["washer", "flat", "fastener", "spacer"],
    "standoff-spacer": ["standoff", "spacer", "PCB", "mount", "3d-print"],
    "alloy_rim": ["wheel", "rim", "automotive", "alloy", "spokes"],
    "brake_disc": ["brake", "disc", "rotor", "automotive", "ventilated"],
}

FEATURED_TEMPLATES = {"l-bracket", "rectangular-enclosure", "spur-gear", "hex-bolt", "t-slot-extrusion", "alloy_rim"}


# ═══════════════════════════════════════════════════════════════════════════
# Template generators
# ═══════════════════════════════════════════════════════════════════════════

# ── L-Bracket ─────────────────────────────────────────────────────────────

@register_template("l-bracket", "L-Bracket", {
    "width": {"label": "Width", "type": "float", "default": 40, "min": 10, "max": 200, "step": 1, "unit": "mm", "group": "Dimensions"},
    "height": {"label": "Height", "type": "float", "default": 50, "min": 10, "max": 200, "step": 1, "unit": "mm", "group": "Dimensions"},
    "depth": {"label": "Depth", "type": "float", "default": 40, "min": 10, "max": 200, "step": 1, "unit": "mm", "group": "Dimensions"},
    "thickness": {"label": "Thickness", "type": "float", "default": 3, "min": 1, "max": 20, "step": 0.5, "unit": "mm", "group": "Dimensions"},
    "hole_diameter": {"label": "Hole Diameter", "type": "float", "default": 5, "min": 2, "max": 20, "step": 0.5, "unit": "mm", "group": "Holes"},
    "fillet_radius": {"label": "Fillet Radius", "type": "float", "default": 3, "min": 0, "max": 15, "step": 0.5, "unit": "mm", "group": "Details"},
})
def gen_l_bracket(p):
    return f"""import cadquery as cq

WIDTH = {p['width']}
HEIGHT = {p['height']}
DEPTH = {p['depth']}
THICKNESS = {p['thickness']}
HOLE_D = {p['hole_diameter']}
FILLET_R = {p['fillet_radius']}

# Base plate
base = (
    cq.Workplane("XY")
    .box(WIDTH, DEPTH, THICKNESS)
    .translate((0, 0, THICKNESS / 2))
)

# Vertical plate
wall = (
    cq.Workplane("XY")
    .box(WIDTH, THICKNESS, HEIGHT)
    .translate((0, -DEPTH / 2 + THICKNESS / 2, HEIGHT / 2))
)

result = base.union(wall)

# Mounting holes in base plate
result = (
    result.faces(">Z").workplane()
    .pushPoints([(WIDTH / 4, 0), (-WIDTH / 4, 0)])
    .hole(HOLE_D)
)

# Mounting holes in vertical plate
result = (
    result.faces("<Y").workplane()
    .pushPoints([(WIDTH / 4, HEIGHT / 3), (-WIDTH / 4, HEIGHT / 3)])
    .hole(HOLE_D)
)

# Inner fillet
if FILLET_R > 0:
    try:
        result = result.edges("|X").edges("not(<Z or >Z or <Y or >Y)").fillet(FILLET_R)
    except Exception:
        pass
"""


# ── U-Bracket ─────────────────────────────────────────────────────────────

@register_template("u-bracket", "U-Bracket", {
    "width": {"label": "Width", "type": "float", "default": 50, "min": 15, "max": 200, "step": 1, "unit": "mm", "group": "Dimensions"},
    "height": {"label": "Height", "type": "float", "default": 40, "min": 10, "max": 150, "step": 1, "unit": "mm", "group": "Dimensions"},
    "depth": {"label": "Depth", "type": "float", "default": 30, "min": 10, "max": 150, "step": 1, "unit": "mm", "group": "Dimensions"},
    "thickness": {"label": "Thickness", "type": "float", "default": 3, "min": 1, "max": 15, "step": 0.5, "unit": "mm", "group": "Dimensions"},
    "hole_diameter": {"label": "Hole Diameter", "type": "float", "default": 5, "min": 2, "max": 16, "step": 0.5, "unit": "mm", "group": "Holes"},
})
def gen_u_bracket(p):
    return f"""import cadquery as cq

WIDTH = {p['width']}
HEIGHT = {p['height']}
DEPTH = {p['depth']}
T = {p['thickness']}
HOLE_D = {p['hole_diameter']}

# U-shape: base + two side walls
base = cq.Workplane("XY").box(WIDTH, DEPTH, T).translate((0, 0, T / 2))
left_wall = cq.Workplane("XY").box(T, DEPTH, HEIGHT).translate((-WIDTH / 2 + T / 2, 0, HEIGHT / 2))
right_wall = cq.Workplane("XY").box(T, DEPTH, HEIGHT).translate((WIDTH / 2 - T / 2, 0, HEIGHT / 2))

result = base.union(left_wall).union(right_wall)

# Mounting holes on each side wall
for x_sign in [-1, 1]:
    hole = (
        cq.Workplane("XZ")
        .center(x_sign * WIDTH / 2, HEIGHT / 2)
        .circle(HOLE_D / 2)
        .extrude(-x_sign * T * 2)
    )
    result = result.cut(hole)

# Holes in base
result = (
    result.faces(">Z").workplane(offset=-HEIGHT + T)
    .pushPoints([(WIDTH / 4, 0), (-WIDTH / 4, 0)])
    .hole(HOLE_D)
)
"""


# ── Wall Mount Plate ──────────────────────────────────────────────────────

@register_template("wall-mount-plate", "Wall Mount Plate", {
    "width": {"label": "Width", "type": "float", "default": 80, "min": 20, "max": 300, "step": 5, "unit": "mm", "group": "Dimensions"},
    "height": {"label": "Height", "type": "float", "default": 60, "min": 20, "max": 300, "step": 5, "unit": "mm", "group": "Dimensions"},
    "thickness": {"label": "Thickness", "type": "float", "default": 3, "min": 1, "max": 10, "step": 0.5, "unit": "mm", "group": "Dimensions"},
    "corner_radius": {"label": "Corner Radius", "type": "float", "default": 5, "min": 0, "max": 30, "step": 1, "unit": "mm", "group": "Details"},
    "hole_diameter": {"label": "Hole Diameter", "type": "float", "default": 5, "min": 2, "max": 12, "step": 0.5, "unit": "mm", "group": "Holes"},
    "hole_margin": {"label": "Hole Margin", "type": "float", "default": 8, "min": 4, "max": 30, "step": 1, "unit": "mm", "group": "Holes"},
})
def gen_wall_mount_plate(p):
    cr = min(p['corner_radius'], p['width'] / 2 - 1, p['height'] / 2 - 1)
    return f"""import cadquery as cq

WIDTH = {p['width']}
HEIGHT = {p['height']}
THICKNESS = {p['thickness']}
CORNER_R = {cr}
HOLE_D = {p['hole_diameter']}
MARGIN = {p['hole_margin']}

result = (
    cq.Workplane("XY")
    .rect(WIDTH, HEIGHT)
    .extrude(THICKNESS)
)

if CORNER_R > 0:
    result = result.edges("|Z").fillet(CORNER_R)

# Four corner mounting holes
hx = WIDTH / 2 - MARGIN
hy = HEIGHT / 2 - MARGIN
result = (
    result.faces(">Z").workplane()
    .pushPoints([(hx, hy), (-hx, hy), (-hx, -hy), (hx, -hy)])
    .hole(HOLE_D)
)
"""


# ── DIN Rail Mount ────────────────────────────────────────────────────────

@register_template("din-rail-mount", "DIN Rail Mount", {
    "width": {"label": "Width", "type": "float", "default": 45, "min": 20, "max": 100, "step": 1, "unit": "mm", "group": "Dimensions"},
    "height": {"label": "Height", "type": "float", "default": 50, "min": 30, "max": 100, "step": 1, "unit": "mm", "group": "Dimensions"},
    "thickness": {"label": "Wall Thickness", "type": "float", "default": 2.5, "min": 1.5, "max": 6, "step": 0.5, "unit": "mm", "group": "Dimensions"},
    "rail_width": {"label": "Rail Width", "type": "float", "default": 35, "min": 35, "max": 35, "step": 0, "unit": "mm", "group": "Rail"},
    "clip_depth": {"label": "Clip Depth", "type": "float", "default": 5, "min": 3, "max": 10, "step": 0.5, "unit": "mm", "group": "Rail"},
})
def gen_din_rail_mount(p):
    return f"""import cadquery as cq

WIDTH = {p['width']}
HEIGHT = {p['height']}
T = {p['thickness']}
RAIL_W = {p['rail_width']}
CLIP_D = {p['clip_depth']}

# Main body plate
body = (
    cq.Workplane("XY")
    .rect(WIDTH, HEIGHT)
    .extrude(T)
)

# DIN rail clips (top and bottom hooks)
clip_w = RAIL_W + 2  # slightly wider than rail
clip_h = 8

top_clip = (
    cq.Workplane("XY")
    .rect(clip_w, clip_h)
    .extrude(CLIP_D + T)
    .translate((0, HEIGHT / 2 - clip_h / 2, 0))
)

# Hook overhang at top
hook = (
    cq.Workplane("XY")
    .rect(clip_w, 2)
    .extrude(2)
    .translate((0, HEIGHT / 2 - clip_h - 1, CLIP_D + T - 2))
)

bottom_clip = (
    cq.Workplane("XY")
    .rect(clip_w, clip_h)
    .extrude(CLIP_D + T)
    .translate((0, -HEIGHT / 2 + clip_h / 2, 0))
)

bottom_hook = (
    cq.Workplane("XY")
    .rect(clip_w, 2)
    .extrude(2)
    .translate((0, -HEIGHT / 2 + clip_h + 1, CLIP_D + T - 2))
)

result = body.union(top_clip).union(hook).union(bottom_clip).union(bottom_hook)
"""


# ── Rectangular Enclosure ─────────────────────────────────────────────────

@register_template("rectangular-enclosure", "Rectangular Enclosure", {
    "length": {"label": "Length", "type": "float", "default": 100, "min": 30, "max": 300, "step": 5, "unit": "mm", "group": "Outer"},
    "width": {"label": "Width", "type": "float", "default": 60, "min": 20, "max": 200, "step": 5, "unit": "mm", "group": "Outer"},
    "height": {"label": "Height", "type": "float", "default": 35, "min": 15, "max": 150, "step": 5, "unit": "mm", "group": "Outer"},
    "wall_thickness": {"label": "Wall Thickness", "type": "float", "default": 2, "min": 1, "max": 6, "step": 0.5, "unit": "mm", "group": "Walls"},
    "corner_radius": {"label": "Corner Radius", "type": "float", "default": 3, "min": 0, "max": 15, "step": 1, "unit": "mm", "group": "Details"},
    "screw_diameter": {"label": "Screw Hole Dia", "type": "float", "default": 3, "min": 2, "max": 6, "step": 0.5, "unit": "mm", "group": "Assembly"},
    "boss_diameter": {"label": "Boss Diameter", "type": "float", "default": 8, "min": 5, "max": 15, "step": 1, "unit": "mm", "group": "Assembly"},
})
def gen_rectangular_enclosure(p):
    cr = min(p['corner_radius'], p['width'] / 2 - 1, p['length'] / 2 - 1)
    return f"""import cadquery as cq

LENGTH = {p['length']}
WIDTH = {p['width']}
HEIGHT = {p['height']}
WALL = {p['wall_thickness']}
CORNER_R = {cr}
SCREW_D = {p['screw_diameter']}
BOSS_D = {p['boss_diameter']}

# Box body (shelled)
body = (
    cq.Workplane("XY")
    .rect(LENGTH, WIDTH)
    .extrude(HEIGHT)
)
if CORNER_R > 0:
    body = body.edges("|Z").fillet(CORNER_R)

body = body.faces(">Z").shell(-WALL)

# Screw bosses in corners (inside the box)
boss_x = LENGTH / 2 - WALL - BOSS_D / 2 - 0.5
boss_y = WIDTH / 2 - WALL - BOSS_D / 2 - 0.5
boss_h = HEIGHT - WALL

for bx, by in [(boss_x, boss_y), (-boss_x, boss_y), (-boss_x, -boss_y), (boss_x, -boss_y)]:
    boss = (
        cq.Workplane("XY")
        .center(bx, by)
        .circle(BOSS_D / 2)
        .extrude(boss_h)
    )
    body = body.union(boss)

# Screw holes through bosses
result = (
    body.faces(">Z").workplane()
    .pushPoints([(boss_x, boss_y), (-boss_x, boss_y), (-boss_x, -boss_y), (boss_x, -boss_y)])
    .hole(SCREW_D, boss_h)
)
"""


# ── Round Electronics Case ────────────────────────────────────────────────

@register_template("round-electronics-case", "Round Electronics Case", {
    "outer_diameter": {"label": "Outer Diameter", "type": "float", "default": 60, "min": 25, "max": 150, "step": 5, "unit": "mm", "group": "Outer"},
    "height": {"label": "Height", "type": "float", "default": 30, "min": 10, "max": 100, "step": 5, "unit": "mm", "group": "Outer"},
    "wall_thickness": {"label": "Wall Thickness", "type": "float", "default": 2, "min": 1, "max": 5, "step": 0.5, "unit": "mm", "group": "Walls"},
    "lid_height": {"label": "Lid Height", "type": "float", "default": 5, "min": 3, "max": 20, "step": 1, "unit": "mm", "group": "Lid"},
})
def gen_round_case(p):
    return f"""import cadquery as cq

OD = {p['outer_diameter']}
HEIGHT = {p['height']}
WALL = {p['wall_thickness']}
LID_H = {p['lid_height']}

r = OD / 2

# Main body cylinder (shelled)
body = (
    cq.Workplane("XY")
    .circle(r)
    .extrude(HEIGHT)
    .faces(">Z")
    .shell(-WALL)
)

# Lid ring (sits on top of body inner wall)
lid_r_outer = r
lid_r_inner = r - WALL
lip_r = r - WALL - 0.3  # 0.3mm clearance for fit

lid = (
    cq.Workplane("XY")
    .circle(lid_r_outer)
    .extrude(WALL)
    .translate((0, 0, HEIGHT + 2))
)

# Lid inner lip that sits inside the body
lip = (
    cq.Workplane("XY")
    .circle(lip_r)
    .circle(lip_r - WALL)
    .extrude(LID_H - WALL)
    .translate((0, 0, HEIGHT + 2 - LID_H + WALL))
)

lid = lid.union(lip)

result = body.union(lid)
"""


# ── Spur Gear ─────────────────────────────────────────────────────────────

@register_template("spur-gear", "Spur Gear", {
    "module": {"label": "Module", "type": "float", "default": 2, "min": 0.5, "max": 10, "step": 0.5, "unit": "mm", "group": "Gear"},
    "teeth": {"label": "Number of Teeth", "type": "int", "default": 24, "min": 8, "max": 100, "step": 1, "unit": "", "group": "Gear"},
    "thickness": {"label": "Face Width", "type": "float", "default": 10, "min": 3, "max": 50, "step": 1, "unit": "mm", "group": "Gear"},
    "bore_diameter": {"label": "Bore Diameter", "type": "float", "default": 8, "min": 2, "max": 50, "step": 0.5, "unit": "mm", "group": "Hub"},
    "hub_diameter": {"label": "Hub Diameter", "type": "float", "default": 16, "min": 5, "max": 60, "step": 1, "unit": "mm", "group": "Hub"},
    "hub_length": {"label": "Hub Length", "type": "float", "default": 15, "min": 5, "max": 40, "step": 1, "unit": "mm", "group": "Hub"},
    "pressure_angle": {"label": "Pressure Angle", "type": "float", "default": 20, "min": 14.5, "max": 25, "step": 0.5, "unit": "deg", "group": "Gear"},
})
def gen_spur_gear(p):
    return f"""import cadquery as cq
import math

MODULE = {p['module']}
N_TEETH = {int(p['teeth'])}
FACE_WIDTH = {p['thickness']}
BORE_D = {p['bore_diameter']}
HUB_D = {p['hub_diameter']}
HUB_L = {p['hub_length']}
PRESSURE_ANGLE = {p['pressure_angle']}

# Gear geometry
pitch_r = MODULE * N_TEETH / 2
addendum = MODULE
dedendum = 1.25 * MODULE
outer_r = pitch_r + addendum
root_r = pitch_r - dedendum

# Build gear profile as polygon (3 points per tooth: root-left, tip-left, tip-right)
# The root-right of one tooth is the root-left of the next
pts = []
angle_step = 2 * math.pi / N_TEETH
for i in range(N_TEETH):
    center_angle = i * angle_step
    tooth_half = angle_step * 0.2
    gap_half = angle_step * 0.5 - tooth_half

    # Root point (gap center before this tooth)
    a_root = center_angle - tooth_half - gap_half * 0.5
    pts.append((root_r * math.cos(a_root), root_r * math.sin(a_root)))
    # Tip leading edge
    a_tip1 = center_angle - tooth_half
    pts.append((outer_r * math.cos(a_tip1), outer_r * math.sin(a_tip1)))
    # Tip trailing edge
    a_tip2 = center_angle + tooth_half
    pts.append((outer_r * math.cos(a_tip2), outer_r * math.sin(a_tip2)))

# Build the gear profile
wp = cq.Workplane("XY").moveTo(pts[0][0], pts[0][1])
for pt in pts[1:]:
    wp = wp.lineTo(pt[0], pt[1])
wp = wp.close()

gear = wp.extrude(FACE_WIDTH)

# Center bore
gear = (
    gear.faces(">Z").workplane()
    .circle(BORE_D / 2)
    .cutThruAll()
)

# Hub
hub = (
    cq.Workplane("XY")
    .circle(HUB_D / 2)
    .circle(BORE_D / 2)
    .extrude(HUB_L)
)

result = gear.union(hub)
"""


# ── Pulley ────────────────────────────────────────────────────────────────

@register_template("pulley", "Pulley", {
    "outer_diameter": {"label": "Outer Diameter", "type": "float", "default": 60, "min": 20, "max": 200, "step": 5, "unit": "mm", "group": "Overall"},
    "width": {"label": "Width", "type": "float", "default": 15, "min": 5, "max": 50, "step": 1, "unit": "mm", "group": "Overall"},
    "bore_diameter": {"label": "Bore Diameter", "type": "float", "default": 10, "min": 3, "max": 50, "step": 0.5, "unit": "mm", "group": "Hub"},
    "groove_depth": {"label": "Groove Depth", "type": "float", "default": 5, "min": 2, "max": 15, "step": 0.5, "unit": "mm", "group": "Groove"},
    "groove_angle": {"label": "Groove Angle", "type": "float", "default": 38, "min": 30, "max": 45, "step": 1, "unit": "deg", "group": "Groove"},
    "flange_height": {"label": "Flange Height", "type": "float", "default": 3, "min": 1, "max": 10, "step": 0.5, "unit": "mm", "group": "Groove"},
})
def gen_pulley(p):
    return f"""import cadquery as cq
import math

OD = {p['outer_diameter']}
WIDTH = {p['width']}
BORE_D = {p['bore_diameter']}
GROOVE_DEPTH = {p['groove_depth']}
GROOVE_ANGLE = {p['groove_angle']}
FLANGE_H = {p['flange_height']}

r = OD / 2

# Main pulley body (revolved profile)
# Profile: flange → groove → flange
groove_r = r - GROOVE_DEPTH
half_w = WIDTH / 2
groove_half_w = half_w - FLANGE_H

# Build as solid cylinder, then cut the groove
body = (
    cq.Workplane("XY")
    .circle(r)
    .circle(BORE_D / 2)
    .extrude(WIDTH)
    .translate((0, 0, -WIDTH / 2))
)

# V-groove cut (using a cone-like shape)
tan_a = math.tan(math.radians(GROOVE_ANGLE / 2))
groove_top_r = r + 1  # slightly oversize for clean cut
groove_bot_r = groove_r

# Create groove as a ring that we cut
groove = (
    cq.Workplane("XZ")
    .moveTo(groove_r, -groove_half_w)
    .lineTo(groove_top_r, -half_w - 1)
    .lineTo(groove_top_r, half_w + 1)
    .lineTo(groove_r, groove_half_w)
    .lineTo(groove_r, -groove_half_w)
    .close()
    .revolve(360, (0, 0, 0), (0, 1, 0))
)

result = body.cut(groove)
"""


# ── T-Slot Extrusion ─────────────────────────────────────────────────────

@register_template("t-slot-extrusion", "T-Slot Extrusion", {
    "size": {"label": "Profile Size", "type": "float", "default": 20, "min": 15, "max": 80, "step": 5, "unit": "mm", "group": "Profile"},
    "length": {"label": "Length", "type": "float", "default": 200, "min": 50, "max": 2000, "step": 10, "unit": "mm", "group": "Dimensions"},
    "wall_thickness": {"label": "Wall Thickness", "type": "float", "default": 2, "min": 1, "max": 5, "step": 0.5, "unit": "mm", "group": "Profile"},
    "slot_width": {"label": "Slot Width", "type": "float", "default": 6, "min": 4, "max": 12, "step": 0.5, "unit": "mm", "group": "Slot"},
})
def gen_t_slot(p):
    s = p['size']
    t = p['wall_thickness']
    sw = p['slot_width']
    return f"""import cadquery as cq

SIZE = {s}
LENGTH = {p['length']}
T = {t}
SLOT_W = {sw}

half = SIZE / 2

# Solid square profile
profile = (
    cq.Workplane("XY")
    .rect(SIZE, SIZE)
)

# Cut center void (leaving walls)
inner = SIZE - 2 * T
profile = (
    profile.extrude(LENGTH)
    .faces(">Z").workplane()
    .rect(inner, inner)
    .cutThruAll()
)

# Cut T-slots on all four sides
for angle in [0, 90, 180, 270]:
    slot_cut = (
        cq.Workplane("XY")
        .transformed(rotate=(0, 0, angle))
        .center(0, half - T / 2)
        .rect(SLOT_W, T + 0.1)
        .extrude(LENGTH)
    )
    profile = profile.cut(slot_cut)

result = profile
"""


# ── I-Beam ────────────────────────────────────────────────────────────────

@register_template("i-beam", "I-Beam", {
    "height": {"label": "Beam Height", "type": "float", "default": 100, "min": 30, "max": 500, "step": 10, "unit": "mm", "group": "Profile"},
    "flange_width": {"label": "Flange Width", "type": "float", "default": 50, "min": 20, "max": 300, "step": 5, "unit": "mm", "group": "Profile"},
    "web_thickness": {"label": "Web Thickness", "type": "float", "default": 5, "min": 2, "max": 20, "step": 1, "unit": "mm", "group": "Profile"},
    "flange_thickness": {"label": "Flange Thickness", "type": "float", "default": 8, "min": 3, "max": 25, "step": 1, "unit": "mm", "group": "Profile"},
    "length": {"label": "Length", "type": "float", "default": 300, "min": 50, "max": 3000, "step": 50, "unit": "mm", "group": "Dimensions"},
})
def gen_i_beam(p):
    return f"""import cadquery as cq

H = {p['height']}
FW = {p['flange_width']}
TW = {p['web_thickness']}
TF = {p['flange_thickness']}
LENGTH = {p['length']}

# I-beam profile built as three rectangles
# Top flange
top_flange = cq.Workplane("XY").rect(FW, TF).extrude(LENGTH).translate((0, H / 2 - TF / 2, 0))
# Bottom flange
bot_flange = cq.Workplane("XY").rect(FW, TF).extrude(LENGTH).translate((0, -H / 2 + TF / 2, 0))
# Web
web = cq.Workplane("XY").rect(TW, H - 2 * TF).extrude(LENGTH)

result = top_flange.union(web).union(bot_flange)
"""


# ── Angle Iron ────────────────────────────────────────────────────────────

@register_template("angle-iron", "Angle Iron", {
    "leg_a": {"label": "Leg A", "type": "float", "default": 40, "min": 15, "max": 150, "step": 5, "unit": "mm", "group": "Profile"},
    "leg_b": {"label": "Leg B", "type": "float", "default": 40, "min": 15, "max": 150, "step": 5, "unit": "mm", "group": "Profile"},
    "thickness": {"label": "Thickness", "type": "float", "default": 4, "min": 2, "max": 15, "step": 0.5, "unit": "mm", "group": "Profile"},
    "length": {"label": "Length", "type": "float", "default": 200, "min": 50, "max": 2000, "step": 50, "unit": "mm", "group": "Dimensions"},
})
def gen_angle_iron(p):
    return f"""import cadquery as cq

LEG_A = {p['leg_a']}
LEG_B = {p['leg_b']}
T = {p['thickness']}
LENGTH = {p['length']}

# Vertical leg
leg_v = cq.Workplane("XY").rect(T, LEG_A).extrude(LENGTH).translate((-LEG_B / 2 + T / 2, LEG_A / 2 - T / 2, 0))
# Horizontal leg
leg_h = cq.Workplane("XY").rect(LEG_B, T).extrude(LENGTH)

result = leg_v.union(leg_h)
"""


# ── Hex Bolt ──────────────────────────────────────────────────────────────

@register_template("hex-bolt", "Hex Bolt", {
    "diameter": {"label": "Thread Diameter (M)", "type": "float", "default": 8, "min": 3, "max": 30, "step": 1, "unit": "mm", "group": "Thread"},
    "length": {"label": "Bolt Length", "type": "float", "default": 30, "min": 8, "max": 200, "step": 5, "unit": "mm", "group": "Dimensions"},
    "head_height": {"label": "Head Height", "type": "float", "default": 5.3, "min": 2, "max": 20, "step": 0.1, "unit": "mm", "group": "Head"},
    "head_width_af": {"label": "Head Width (AF)", "type": "float", "default": 13, "min": 5, "max": 46, "step": 0.5, "unit": "mm", "group": "Head"},
})
def gen_hex_bolt(p):
    return f"""import cadquery as cq
import math

THREAD_D = {p['diameter']}
BOLT_L = {p['length']}
HEAD_H = {p['head_height']}
HEAD_AF = {p['head_width_af']}  # across flats

# Hex head (regular hexagon)
head_r = HEAD_AF / math.sqrt(3)  # circumscribed radius
head = (
    cq.Workplane("XY")
    .polygon(6, head_r * 2)
    .extrude(HEAD_H)
)

# Chamfer the top edges of the head
try:
    head = head.edges(">Z").chamfer(HEAD_H * 0.15)
except Exception:
    pass

# Shaft
shaft = (
    cq.Workplane("XY")
    .circle(THREAD_D / 2)
    .extrude(-BOLT_L)
)

# Chamfer the tip
try:
    shaft = shaft.edges("<Z").chamfer(THREAD_D * 0.15)
except Exception:
    pass

result = head.union(shaft)
"""


# ── Flat Washer ───────────────────────────────────────────────────────────

@register_template("flat-washer", "Flat Washer", {
    "inner_diameter": {"label": "Inner Diameter", "type": "float", "default": 8.4, "min": 3, "max": 40, "step": 0.1, "unit": "mm", "group": "Dimensions"},
    "outer_diameter": {"label": "Outer Diameter", "type": "float", "default": 16, "min": 6, "max": 60, "step": 0.5, "unit": "mm", "group": "Dimensions"},
    "thickness": {"label": "Thickness", "type": "float", "default": 1.6, "min": 0.5, "max": 6, "step": 0.1, "unit": "mm", "group": "Dimensions"},
})
def gen_flat_washer(p):
    return f"""import cadquery as cq

ID = {p['inner_diameter']}
OD = {p['outer_diameter']}
T = {p['thickness']}

result = (
    cq.Workplane("XY")
    .circle(OD / 2)
    .circle(ID / 2)
    .extrude(T)
)
"""


# ── Standoff / Spacer ────────────────────────────────────────────────────

@register_template("standoff-spacer", "Standoff / Spacer", {
    "outer_diameter": {"label": "Outer Diameter", "type": "float", "default": 6, "min": 3, "max": 20, "step": 0.5, "unit": "mm", "group": "Dimensions"},
    "bore_diameter": {"label": "Bore Diameter", "type": "float", "default": 3.2, "min": 1.5, "max": 12, "step": 0.1, "unit": "mm", "group": "Dimensions"},
    "height": {"label": "Height", "type": "float", "default": 10, "min": 3, "max": 50, "step": 1, "unit": "mm", "group": "Dimensions"},
    "hex": {"label": "Hex Shape", "type": "int", "default": 1, "min": 0, "max": 1, "step": 1, "unit": "", "group": "Shape"},
})
def gen_standoff(p):
    return f"""import cadquery as cq
import math

OD = {p['outer_diameter']}
BORE_D = {p['bore_diameter']}
HEIGHT = {p['height']}
HEX = {int(p['hex'])}

if HEX:
    # Hex standoff
    hex_r = OD / math.sqrt(3)
    body = cq.Workplane("XY").polygon(6, hex_r * 2).extrude(HEIGHT)
else:
    # Round standoff
    body = cq.Workplane("XY").circle(OD / 2).extrude(HEIGHT)

# Through bore
result = (
    body.faces(">Z").workplane()
    .circle(BORE_D / 2)
    .cutThruAll()
)
"""


# ═══════════════════════════════════════════════════════════════════════════
# Database seeding
# ═══════════════════════════════════════════════════════════════════════════

def seed_templates(conn):
    """Insert template categories and template metadata into the database.
    Safe to call multiple times (uses ON CONFLICT).
    """
    cur = conn.cursor()

    # Seed categories
    for cat in CATEGORIES:
        cur.execute("""
            INSERT INTO template_categories (name, slug, description, icon, sort_order)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                icon = EXCLUDED.icon,
                sort_order = EXCLUDED.sort_order
        """, (cat["name"], cat["slug"], cat["description"], cat["icon"], cat["sort_order"]))

    # Get category id map
    cur.execute("SELECT slug, id FROM template_categories")
    cat_map = dict(cur.fetchall())

    # Seed templates from TEMPLATE_REGISTRY
    for tpl_name, tpl in TEMPLATE_REGISTRY.items():
        slug = tpl_name.replace("_", "-")
        cat_slug = TEMPLATE_CATEGORIES.get(slug) or TEMPLATE_CATEGORIES.get(tpl_name)
        cat_id = cat_map.get(cat_slug) if cat_slug else None
        desc = TEMPLATE_DESCRIPTIONS.get(slug, TEMPLATE_DESCRIPTIONS.get(tpl_name, ""))
        tags = TEMPLATE_TAGS.get(slug, TEMPLATE_TAGS.get(tpl_name, []))
        is_featured = slug in FEATURED_TEMPLATES or tpl_name in FEATURED_TEMPLATES

        cur.execute("""
            INSERT INTO templates (slug, name, description, category_id, tags, param_schema, generator_key, is_featured, is_published)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                category_id = EXCLUDED.category_id,
                tags = EXCLUDED.tags,
                param_schema = EXCLUDED.param_schema,
                generator_key = EXCLUDED.generator_key,
                is_featured = EXCLUDED.is_featured
        """, (
            slug,
            tpl["label"],
            desc,
            cat_id,
            tags,
            json.dumps(tpl["param_schema"]),
            tpl_name,
            is_featured,
        ))

    conn.commit()
    print(f"Seeded {len(CATEGORIES)} categories and {len(TEMPLATE_REGISTRY)} templates.")
