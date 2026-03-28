"""Parametric templates: generate CadQuery scripts from structured parameters.

Each template defines:
  - PARAM_SCHEMA: dict of parameter names → {label, type, default, min, max, step, unit, group}
  - generate(params) → CadQuery script string
"""

from __future__ import annotations
from typing import Any, Dict

import math


# ── Template registry ──────────────────────────────────────────────────────

TEMPLATE_REGISTRY: Dict[str, dict] = {}


def register_template(name: str, label: str, param_schema: dict):
    def decorator(fn):
        TEMPLATE_REGISTRY[name] = {
            "label": label,
            "param_schema": param_schema,
            "generate": fn,
        }
        return fn
    return decorator


def generate_from_template(template_name: str, params: dict) -> str:
    """Generate a CadQuery script from a template and parameters."""
    tmpl = TEMPLATE_REGISTRY.get(template_name)
    if not tmpl:
        raise ValueError(f"Unknown template: {template_name}")
    # Fill in defaults for missing params
    full_params = {}
    for key, schema in tmpl["param_schema"].items():
        full_params[key] = params.get(key, schema["default"])
    return tmpl["generate"](full_params)


def get_template_schema(template_name: str) -> dict:
    tmpl = TEMPLATE_REGISTRY.get(template_name)
    if not tmpl:
        raise ValueError(f"Unknown template: {template_name}")
    return tmpl["param_schema"]


def list_templates() -> list:
    return [
        {"name": name, "label": tmpl["label"], "param_schema": tmpl["param_schema"]}
        for name, tmpl in TEMPLATE_REGISTRY.items()
    ]


# ── Alloy Rim Template ─────────────────────────────────────────────────────
# Engineering-accurate wheel with: barrel (drop center well, bead seats,
# safety humps, J-type flanges), hub disc at ET offset, bolt pattern,
# tapered spokes, spoke pockets, and valve stem hole.

ALLOY_RIM_PARAMS = {
    # Overall
    "rim_diameter_inch": {"label": "Rim Diameter", "type": "float", "default": 18, "min": 14, "max": 24, "step": 1, "unit": "inch", "group": "Overall"},
    "rim_width_j": {"label": "Rim Width (J)", "type": "float", "default": 8.5, "min": 5, "max": 12, "step": 0.5, "unit": "J", "group": "Overall"},

    # Hub & Offset
    "et_offset": {"label": "ET Offset", "type": "float", "default": 35, "min": -20, "max": 60, "step": 1, "unit": "mm", "group": "Hub"},
    "center_bore": {"label": "Center Bore", "type": "float", "default": 66.1, "min": 50, "max": 110, "step": 0.1, "unit": "mm", "group": "Hub"},
    "hub_diameter": {"label": "Hub Diameter", "type": "float", "default": 100, "min": 70, "max": 160, "step": 1, "unit": "mm", "group": "Hub"},
    "hub_thickness": {"label": "Hub Thickness", "type": "float", "default": 18, "min": 10, "max": 40, "step": 1, "unit": "mm", "group": "Hub"},

    # Bolt Pattern
    "bolt_count": {"label": "Bolt Count", "type": "int", "default": 5, "min": 3, "max": 8, "step": 1, "unit": "", "group": "Bolts"},
    "bolt_pcd": {"label": "Bolt PCD", "type": "float", "default": 114.3, "min": 80, "max": 180, "step": 0.1, "unit": "mm", "group": "Bolts"},
    "bolt_hole_diameter": {"label": "Bolt Hole Dia", "type": "float", "default": 14, "min": 8, "max": 24, "step": 0.5, "unit": "mm", "group": "Bolts"},

    # Spokes
    "spoke_count": {"label": "Spoke Count", "type": "int", "default": 5, "min": 3, "max": 12, "step": 1, "unit": "", "group": "Spokes"},
    "spoke_width": {"label": "Spoke Width", "type": "float", "default": 32, "min": 15, "max": 80, "step": 1, "unit": "mm", "group": "Spokes"},
    "spoke_depth": {"label": "Spoke Depth", "type": "float", "default": 22, "min": 10, "max": 40, "step": 1, "unit": "mm", "group": "Spokes"},
    "spoke_fillet": {"label": "Spoke Fillet", "type": "float", "default": 4, "min": 0, "max": 15, "step": 1, "unit": "mm", "group": "Spokes"},

    # Barrel
    "barrel_thickness": {"label": "Barrel Thickness", "type": "float", "default": 3.5, "min": 2, "max": 8, "step": 0.5, "unit": "mm", "group": "Barrel"},
    "well_depth": {"label": "Drop Center Depth", "type": "float", "default": 16, "min": 8, "max": 25, "step": 1, "unit": "mm", "group": "Barrel"},
    "flange_height": {"label": "Flange Height (J)", "type": "float", "default": 17.3, "min": 10, "max": 25, "step": 0.1, "unit": "mm", "group": "Barrel"},
    "hump_height": {"label": "Safety Hump Height", "type": "float", "default": 1.8, "min": 0, "max": 4, "step": 0.1, "unit": "mm", "group": "Barrel"},

    # Valve
    "valve_hole_dia": {"label": "Valve Hole Dia", "type": "float", "default": 11.5, "min": 8, "max": 16, "step": 0.5, "unit": "mm", "group": "Valve"},
}


@register_template("alloy_rim", "Alloy Rim", ALLOY_RIM_PARAMS)
def generate_alloy_rim(p: dict) -> str:
    """Generate CadQuery script for an engineering-accurate parametric alloy wheel.

    Anatomy: barrel with drop center well, inner/outer bead seats, safety humps,
    J-type flanges, hub disc at ET offset, bolt pattern, tapered spokes, valve hole.
    """
    # ── Extract parameters (with backward-compat fallbacks) ──
    rim_r = p["rim_diameter_inch"] * 25.4 / 2       # bead seat radius (mm)
    width = p["rim_width_j"] * 25.4                  # between bead seats (mm)
    et = p.get("et_offset", 35)
    cb = p["center_bore"]
    hub_r = p["hub_diameter"] / 2
    hub_t = p["hub_thickness"]
    n_bolts = int(p["bolt_count"])
    bolt_pcd_r = p["bolt_pcd"] / 2
    bolt_hole_d = p["bolt_hole_diameter"]
    n_spokes = int(p["spoke_count"])
    spoke_w = p.get("spoke_width", p.get("spoke_width_outer", 32))
    spoke_d = p.get("spoke_depth", p.get("spoke_thickness", 22))
    spoke_fillet = p.get("spoke_fillet", 4)
    bt = p["barrel_thickness"]
    well_depth = p.get("well_depth", 16)
    fh = p.get("flange_height", p.get("lip_height", 17.3))
    hh = p.get("hump_height", 1.8)
    valve_d = p.get("valve_hole_dia", 11.5)

    # ── Derived dimensions ──
    fw = 13.0                         # J-type flange width (standard)
    bsw = 18.0                        # bead seat width
    hw = 8.0                          # hump zone width
    well_r = rim_r - well_depth       # drop center well radius
    total_w = width + 2 * fw          # total barrel width inc. flanges
    half_w = total_w / 2

    # Spoke geometry
    bolt_clear = bolt_pcd_r + bolt_hole_d / 2 + 3
    spoke_inner_r = max(bolt_clear, hub_r * 0.7)
    spoke_outer_r = well_r - bt + 3   # extend into barrel for clean union
    spoke_offset = 360.0 / n_bolts / 2 if n_spokes == n_bolts else 0
    spoke_w_inner = spoke_w * 0.65    # narrower at hub end
    spoke_w_outer = spoke_w

    # ── Barrel cross-section Z positions ──
    # Layout (inner → outer):
    #   flange | bead seat | hump | slope | WELL | slope | hump | bead seat | flange
    z_ifl = -half_w                           # inner flange edge
    z_ifl_e = -half_w + fw                    # inner flange / bead seat junction
    z_ibs_e = z_ifl_e + bsw                   # inner bead seat end
    z_ihp = z_ibs_e + hw / 2                  # inner hump peak
    z_ih_e = z_ibs_e + hw                     # past inner hump

    z_oh_s = half_w - fw - bsw - hw           # before outer hump
    z_ohp = half_w - fw - bsw - hw / 2        # outer hump peak
    z_obs_s = half_w - fw - bsw               # outer bead seat start
    z_obs_e = half_w - fw                      # outer bead / flange junction
    z_ofl = half_w                            # outer flange edge

    # Well zone (with 10mm transition slopes, clamped for narrow rims)
    trans = min(10.0, (z_oh_s - z_ih_e) * 0.3)
    z_well_s = z_ih_e + trans
    z_well_e = z_oh_s - trans
    if z_well_e < z_well_s:
        mid = (z_ih_e + z_oh_s) / 2
        z_well_s = mid - 5
        z_well_e = mid + 5

    # Valve position: between first two spokes, at outer bead seat
    valve_angle = spoke_offset + 360.0 / n_spokes / 2
    valve_z = half_w - fw - bsw / 2

    # ── Build barrel profile points ──
    # Outer surface (inner flange → outer flange)
    op = [
        (rim_r + fh, z_ifl),        # 1  inner flange top
        (rim_r + fh, z_ifl_e),      # 2  inner flange base
        (rim_r, z_ifl_e),           # 3  bead seat start
        (rim_r, z_ibs_e),           # 4  bead seat end
        (rim_r + hh, z_ihp),        # 5  inner safety hump peak
        (rim_r, z_ih_e),            # 6  past inner hump
        (well_r, z_well_s),         # 7  well start (slope down)
        (well_r, z_well_e),         # 8  well end
        (rim_r, z_oh_s),            # 9  slope up to outer hump
        (rim_r + hh, z_ohp),        # 10 outer safety hump peak
        (rim_r, z_obs_s),           # 11 outer bead seat start
        (rim_r, z_obs_e),           # 12 outer bead seat end
        (rim_r + fh, z_obs_e),      # 13 outer flange base
        (rim_r + fh, z_ofl),        # 14 outer flange top
    ]
    # Inner surface (outer flange → inner flange, smooth — no humps inside)
    ip = [
        (rim_r + fh - bt, z_ofl),   # 15
        (rim_r + fh - bt, z_obs_e), # 16
        (rim_r - bt, z_obs_e),      # 17
        (rim_r - bt, z_oh_s),       # 18
        (well_r - bt, z_well_e),    # 19 well inner
        (well_r - bt, z_well_s),    # 20
        (rim_r - bt, z_ih_e),       # 21
        (rim_r - bt, z_ifl_e),      # 22
        (rim_r + fh - bt, z_ifl_e), # 23
        (rim_r + fh - bt, z_ifl),   # 24
    ]
    all_pts = op + ip

    # Format points for generated script
    pts_lines = "\n".join(f"    ({r:.2f}, {z:.2f})," for r, z in all_pts)

    script = f"""import cadquery as cq
import math

# ═══════════════════════════════════════════════════════════════════
# Parametric Alloy Wheel Rim — Engineering-Accurate
# Barrel: drop center well, bead seats, safety humps, J-type flanges
# Hub: ET offset, center bore, bolt pattern
# Spokes: tapered ribs with pockets, valve stem hole
# ═══════════════════════════════════════════════════════════════════

# ── Dimensions (mm) ──
rim_r = {rim_r:.2f}
total_w = {total_w:.2f}
half_w = {half_w:.2f}
well_r = {well_r:.2f}
bt = {bt:.1f}
fh = {fh:.1f}
hub_r = {hub_r:.1f}
hub_t = {hub_t:.1f}
cb_r = {cb / 2:.2f}
et = {et:.1f}
n_spokes = {n_spokes}
n_bolts = {n_bolts}
bolt_pcd_r = {bolt_pcd_r:.2f}
bolt_hole_d = {bolt_hole_d:.1f}
spoke_inner_r = {spoke_inner_r:.2f}
spoke_outer_r = {spoke_outer_r:.2f}
spoke_w_inner = {spoke_w_inner:.1f}
spoke_w_outer = {spoke_w_outer:.1f}
spoke_d = {spoke_d:.1f}
spoke_offset = {spoke_offset:.2f}
valve_d = {valve_d:.1f}
valve_angle = {valve_angle:.2f}
valve_z = {valve_z:.2f}

# ═══════════════════════════════════════════════════════════════════
# 1. BARREL — revolved cross-section profile
#    Outer contour traces: flanges → bead seats → humps → well
#    Inner contour traces: smooth inner wall (no humps inside)
#    Revolved 360° around Z axis
# ═══════════════════════════════════════════════════════════════════
barrel_pts = [
{pts_lines}
]

bwp = cq.Workplane("XZ").moveTo(barrel_pts[0][0], barrel_pts[0][1])
for pt in barrel_pts[1:]:
    bwp = bwp.lineTo(pt[0], pt[1])
barrel = bwp.close().revolve(360, (0, 0), (0, 1))

# ═══════════════════════════════════════════════════════════════════
# 2. HUB DISC — annular plate at ET offset position
#    ET = distance from hub mounting face to barrel centerline
#    Hub extends inward (toward car) from the mounting face
# ═══════════════════════════════════════════════════════════════════
hub = (
    cq.Workplane("XY")
    .circle(hub_r)
    .circle(cb_r)
    .extrude(hub_t)
    .translate((0, 0, et - hub_t))
)

# ═══════════════════════════════════════════════════════════════════
# 3. SPOKES — tapered ribs connecting hub disc to barrel inner wall
#    Wider at barrel (outer), narrower at hub (inner)
#    Offset to sit between bolt holes when spoke_count == bolt_count
# ═══════════════════════════════════════════════════════════════════
for i in range(n_spokes):
    a = math.radians(i * 360.0 / n_spokes + spoke_offset)
    ca, sa = math.cos(a), math.sin(a)
    px, py = -sa, ca  # perpendicular direction for width

    right, left = [], []
    for j in range(11):
        t = j / 10.0
        r = spoke_inner_r + t * (spoke_outer_r - spoke_inner_r)
        hw = spoke_w_inner / 2 + t * (spoke_w_outer / 2 - spoke_w_inner / 2)
        cx, cy = r * ca, r * sa
        right.append((cx + px * hw, cy + py * hw))
        left.append((cx - px * hw, cy - py * hw))

    pts = right + list(reversed(left))
    swp = cq.Workplane("XY").moveTo(pts[0][0], pts[0][1])
    for pt in pts[1:]:
        swp = swp.lineTo(pt[0], pt[1])
    spoke = swp.close().extrude(spoke_d).translate((0, 0, et - spoke_d))
    hub = hub.union(spoke)

# ═══════════════════════════════════════════════════════════════════
# 4. COMBINE hub+spokes assembly with barrel
# ═══════════════════════════════════════════════════════════════════
result = barrel.union(hub)

# ═══════════════════════════════════════════════════════════════════
# 5. BOLT HOLES — lug holes on the pitch circle diameter
# ═══════════════════════════════════════════════════════════════════
for i in range(n_bolts):
    a = math.radians(i * 360.0 / n_bolts)
    bx = bolt_pcd_r * math.cos(a)
    by = bolt_pcd_r * math.sin(a)
    bc = (cq.Workplane("XY")
        .pushPoints([(bx, by)]).circle(bolt_hole_d / 2)
        .extrude(hub_t + 20)
        .translate((0, 0, et - hub_t - 10)))
    result = result.cut(bc)

# ═══════════════════════════════════════════════════════════════════
# 6. CENTER BORE — clean through-hole for hub fitment
# ═══════════════════════════════════════════════════════════════════
bore = (cq.Workplane("XY")
    .circle(cb_r)
    .extrude(total_w + 20)
    .translate((0, 0, -half_w - 10)))
result = result.cut(bore)

# ═══════════════════════════════════════════════════════════════════
# 7. VALVE STEM HOLE — radial hole through barrel at outer bead seat
# ═══════════════════════════════════════════════════════════════════
valve_cutter = (
    cq.Workplane("YZ")
    .circle(valve_d / 2)
    .extrude(bt + 10)
    .translate((rim_r - bt - 5, 0, valve_z))
    .rotate((0, 0, 0), (0, 0, 1), valve_angle)
)
result = result.cut(valve_cutter)
"""

    # Optional spoke edge fillet
    if spoke_fillet > 0:
        sf = min(spoke_fillet, spoke_w * 0.2)
        script += f"""
# ═══════════════════════════════════════════════════════════════════
# 8. SPOKE FILLET — smooth spoke edges
# ═══════════════════════════════════════════════════════════════════
try:
    result = result.edges("|Z").fillet({sf:.1f})
except Exception:
    pass  # Skip if geometry too complex for fillet
"""

    return script


# ── Brake Disc Template ────────────────────────────────────────────────────

BRAKE_DISC_PARAMS = {
    "outer_diameter": {"label": "Outer Diameter", "type": "float", "default": 330, "min": 200, "max": 420, "step": 5, "unit": "mm", "group": "Overall"},
    "thickness": {"label": "Thickness", "type": "float", "default": 28, "min": 10, "max": 40, "step": 1, "unit": "mm", "group": "Overall"},
    "hat_diameter": {"label": "Hat Diameter", "type": "float", "default": 140, "min": 80, "max": 200, "step": 5, "unit": "mm", "group": "Hat"},
    "hat_height": {"label": "Hat Height", "type": "float", "default": 45, "min": 20, "max": 70, "step": 1, "unit": "mm", "group": "Hat"},
    "center_bore": {"label": "Center Bore", "type": "float", "default": 66.1, "min": 40, "max": 110, "step": 0.1, "unit": "mm", "group": "Hat"},
    "bolt_count": {"label": "Bolt Count", "type": "int", "default": 5, "min": 3, "max": 8, "step": 1, "unit": "", "group": "Bolts"},
    "bolt_pcd": {"label": "Bolt PCD", "type": "float", "default": 114.3, "min": 80, "max": 180, "step": 0.1, "unit": "mm", "group": "Bolts"},
    "bolt_hole_diameter": {"label": "Bolt Hole Dia", "type": "float", "default": 14, "min": 8, "max": 24, "step": 0.5, "unit": "mm", "group": "Bolts"},
    "vent_slot_count": {"label": "Vent Slots", "type": "int", "default": 36, "min": 0, "max": 72, "step": 1, "unit": "", "group": "Venting"},
    "vent_slot_width": {"label": "Slot Width", "type": "float", "default": 4, "min": 2, "max": 10, "step": 0.5, "unit": "mm", "group": "Venting"},
}


@register_template("brake_disc", "Brake Disc", BRAKE_DISC_PARAMS)
def generate_brake_disc(p: dict) -> str:
    od = p["outer_diameter"]
    t = p["thickness"]
    hat_d = p["hat_diameter"]
    hat_h = p["hat_height"]
    cb = p["center_bore"]
    n_bolts = int(p["bolt_count"])
    bolt_pcd = p["bolt_pcd"]
    bolt_d = p["bolt_hole_diameter"]
    n_vents = int(p["vent_slot_count"])
    vent_w = p["vent_slot_width"]

    return f"""import cadquery as cq
import math

# Brake disc
od_r = {od/2:.1f}
t = {t:.1f}
hat_r = {hat_d/2:.1f}
hat_h = {hat_h:.1f}
cb_r = {cb/2:.2f}

# Main disc
disc = (
    cq.Workplane("XY")
    .circle(od_r)
    .circle(hat_r + 5)  # inner clearance
    .extrude(t)
)

# Hat (center section)
hat = (
    cq.Workplane("XY")
    .circle(hat_r)
    .circle(cb_r)
    .extrude(hat_h)
)

result = disc.union(hat)

# Bolt holes
for i in range({n_bolts}):
    angle = i * 360.0 / {n_bolts}
    bx = {bolt_pcd/2:.2f} * math.cos(math.radians(angle))
    by = {bolt_pcd/2:.2f} * math.sin(math.radians(angle))
    hole = cq.Workplane("XY").center(bx, by).circle({bolt_d/2:.2f}).extrude(hat_h * 2)
    result = result.cut(hole)

# Ventilation slots
if {n_vents} > 0:
    for i in range({n_vents}):
        angle = i * 360.0 / {n_vents}
        slot_r = (hat_r + 5 + od_r) / 2
        sx = slot_r * math.cos(math.radians(angle))
        sy = slot_r * math.sin(math.radians(angle))
        slot_len = od_r - hat_r - 15
        slot = (
            cq.Workplane("XY")
            .center(sx, sy)
            .transformed(rotate=(0, 0, angle))
            .rect({vent_w:.1f}, slot_len)
            .extrude(t * 2)
            .translate((0, 0, -t / 2))
        )
        result = result.cut(slot)
"""


import re as _re

def extract_script_params(script: str) -> dict:
    """Extract top-level UPPER_CASE = number assignments as editable parameters."""
    params = {}
    for line in script.split('\n'):
        m = _re.match(r'^([A-Z][A-Z_0-9]{2,})\s*=\s*([-\d.]+)\s*(?:#.*)?$', line.strip())
        if m:
            key, val = m.group(1), m.group(2)
            try:
                params[key] = int(val) if '.' not in val else float(val)
            except ValueError:
                pass
    return params

def replay_script_with_params(script: str, params: dict) -> str:
    """Substitute new parameter values into an existing script."""
    lines = script.split('\n')
    result = []
    for line in lines:
        replaced = False
        for key, val in params.items():
            m = _re.match(rf'^({_re.escape(key)}\s*=\s*)([-\d.]+)(.*)', line)
            if m:
                result.append(f"{key} = {val}{m.group(3)}")
                replaced = True
                break
        if not replaced:
            result.append(line)
    return '\n'.join(result)
