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

ALLOY_RIM_PARAMS = {
    # Overall
    "rim_diameter_inch": {"label": "Rim Diameter", "type": "float", "default": 18, "min": 14, "max": 24, "step": 1, "unit": "inch", "group": "Overall"},
    "rim_width_j": {"label": "Rim Width (J)", "type": "float", "default": 8.5, "min": 5, "max": 12, "step": 0.5, "unit": "J", "group": "Overall"},

    # Hub
    "center_bore": {"label": "Center Bore", "type": "float", "default": 66.1, "min": 50, "max": 110, "step": 0.1, "unit": "mm", "group": "Hub"},
    "hub_diameter": {"label": "Hub Diameter", "type": "float", "default": 100, "min": 70, "max": 160, "step": 1, "unit": "mm", "group": "Hub"},
    "hub_thickness": {"label": "Hub Thickness", "type": "float", "default": 20, "min": 10, "max": 40, "step": 1, "unit": "mm", "group": "Hub"},

    # Spokes
    "spoke_count": {"label": "Spoke Count", "type": "int", "default": 5, "min": 3, "max": 12, "step": 1, "unit": "", "group": "Spokes"},
    "spoke_width_inner": {"label": "Spoke Width (inner)", "type": "float", "default": 35, "min": 15, "max": 80, "step": 1, "unit": "mm", "group": "Spokes"},
    "spoke_width_outer": {"label": "Spoke Width (outer)", "type": "float", "default": 45, "min": 15, "max": 100, "step": 1, "unit": "mm", "group": "Spokes"},
    "spoke_thickness": {"label": "Spoke Thickness", "type": "float", "default": 18, "min": 8, "max": 35, "step": 1, "unit": "mm", "group": "Spokes"},
    "spoke_fillet": {"label": "Spoke Fillet", "type": "float", "default": 5, "min": 0, "max": 20, "step": 1, "unit": "mm", "group": "Spokes"},

    # Bolt Pattern
    "bolt_count": {"label": "Bolt Count", "type": "int", "default": 5, "min": 3, "max": 8, "step": 1, "unit": "", "group": "Bolts"},
    "bolt_pcd": {"label": "Bolt PCD", "type": "float", "default": 114.3, "min": 80, "max": 180, "step": 0.1, "unit": "mm", "group": "Bolts"},
    "bolt_hole_diameter": {"label": "Bolt Hole Dia", "type": "float", "default": 14, "min": 8, "max": 24, "step": 0.5, "unit": "mm", "group": "Bolts"},

    # Lip
    "lip_height": {"label": "Lip Height", "type": "float", "default": 12, "min": 5, "max": 30, "step": 1, "unit": "mm", "group": "Lip"},
    "lip_thickness": {"label": "Lip Thickness", "type": "float", "default": 4, "min": 2, "max": 10, "step": 0.5, "unit": "mm", "group": "Lip"},

    # Barrel
    "barrel_thickness": {"label": "Barrel Thickness", "type": "float", "default": 3.5, "min": 2, "max": 8, "step": 0.5, "unit": "mm", "group": "Barrel"},
}


@register_template("alloy_rim", "Alloy Rim", ALLOY_RIM_PARAMS)
def generate_alloy_rim(p: dict) -> str:
    """Generate a CadQuery script for a parametric alloy rim."""

    rim_r = p["rim_diameter_inch"] * 25.4 / 2  # outer radius in mm
    width = p["rim_width_j"] * 25.4  # width in mm
    cb = p["center_bore"]
    hub_r = p["hub_diameter"] / 2
    hub_t = p["hub_thickness"]
    n_spokes = int(p["spoke_count"])
    sw_inner = p["spoke_width_inner"]
    sw_outer = p["spoke_width_outer"]
    s_thick = p["spoke_thickness"]
    s_fillet = p["spoke_fillet"]
    n_bolts = int(p["bolt_count"])
    bolt_pcd_r = p["bolt_pcd"] / 2
    bolt_hole_d = p["bolt_hole_diameter"]
    lip_h = p["lip_height"]
    lip_t = p["lip_thickness"]
    barrel_t = p["barrel_thickness"]

    # Spokes must connect to hub but NOT cover bolt holes
    # spoke_start = just outside the bolt circle (PCD + hole radius + clearance)
    bolt_clearance = bolt_pcd_r + bolt_hole_d / 2 + 2  # 2mm clearance beyond bolt holes
    spoke_start = max(bolt_clearance, hub_r * 0.6)  # never closer than bolt clearance
    spoke_end = rim_r - barrel_t  # end at inner barrel wall

    # Offset spokes to sit BETWEEN bolt holes (not on top of them)
    spoke_angle_offset = 360.0 / n_bolts / 2 if n_spokes == n_bolts else 0

    script = f"""import cadquery as cq
import math

# ── Parameters ──
rim_r = {rim_r:.2f}       # outer radius (mm)
width = {width:.2f}       # rim width (mm)
cb_r = {cb/2:.2f}         # center bore radius
hub_r = {hub_r:.2f}       # hub outer radius
hub_t = {hub_t:.2f}       # hub thickness
n_spokes = {n_spokes}
sw_inner = {sw_inner:.1f}  # spoke width at hub
sw_outer = {sw_outer:.1f}  # spoke width at rim
s_thick = {s_thick:.1f}    # spoke thickness
n_bolts = {n_bolts}
bolt_pcd_r = {bolt_pcd_r:.2f}
bolt_hole_d = {bolt_hole_d:.2f}
lip_h = {lip_h:.1f}
lip_t = {lip_t:.1f}
barrel_t = {barrel_t:.1f}

spoke_start = {spoke_start:.2f}   # starts outside bolt circle for clearance
spoke_end = {spoke_end:.2f}       # ends at inner barrel wall
spoke_angle_offset = {spoke_angle_offset:.2f}  # offset to sit between bolt holes

# ── Hub ──
hub = (
    cq.Workplane("XY")
    .circle(hub_r)
    .circle(cb_r)
    .extrude(hub_t)
    .translate((0, 0, -hub_t / 2))
)

# ── Bolt holes ──
bolt_holes = cq.Workplane("XY")
for i in range(n_bolts):
    angle = i * 360 / n_bolts
    bx = bolt_pcd_r * math.cos(math.radians(angle))
    by = bolt_pcd_r * math.sin(math.radians(angle))
    bolt_holes = bolt_holes.pushPoints([(bx, by)]).circle(bolt_hole_d / 2)
bolt_cut = bolt_holes.extrude(hub_t * 2).translate((0, 0, -hub_t))
hub = hub.cut(bolt_cut)

# ── Spokes (positioned between bolt holes, curved edges) ──
for i in range(n_spokes):
    angle_deg = i * 360.0 / n_spokes + spoke_angle_offset
    angle_rad = math.radians(angle_deg)

    # Spoke spans from spoke_start (inside hub) to spoke_end (at barrel)
    x0 = spoke_start
    x1 = spoke_end
    spoke_len = x1 - x0

    half_w0 = sw_inner / 2
    half_w1 = sw_outer / 2

    # Number of points for curved inner/outer arcs
    n_curve = 8

    # Build spoke profile with curved edges that follow hub and barrel circles
    # Inner curve: arc following hub_r circle
    # Outer curve: arc following (rim_r - barrel_t) circle
    pts = []

    # Right side: from inner-right to outer-right (straight taper)
    pts.append((x0, -half_w0))

    # Use a slight curve along the spoke length for natural look
    for j in range(1, n_curve):
        t = j / n_curve
        x = x0 + t * spoke_len
        half_w = half_w0 + t * (half_w1 - half_w0)
        pts.append((x, -half_w))

    pts.append((x1, -half_w1))

    # Left side: from outer-left back to inner-left (reverse)
    pts.append((x1, half_w1))

    for j in range(n_curve - 1, 0, -1):
        t = j / n_curve
        x = x0 + t * spoke_len
        half_w = half_w0 + t * (half_w1 - half_w0)
        pts.append((x, half_w))

    pts.append((x0, half_w0))

    # Build the spoke as a closed wire + extrude
    wp = cq.Workplane("XY").transformed(rotate=(0, 0, angle_deg))
    wp = wp.moveTo(pts[0][0], pts[0][1])
    for pt in pts[1:]:
        wp = wp.lineTo(pt[0], pt[1])
    wp = wp.close()

    spoke = wp.extrude(s_thick).translate((0, 0, -s_thick / 2))
    hub = hub.union(spoke)

# ── Barrel with lips (single solid to avoid z-fighting) ──
barrel = (
    cq.Workplane("XY")
    .circle(rim_r)
    .circle(rim_r - barrel_t)
    .extrude(width)
    .translate((0, 0, -width / 2))
)
front_lip = (
    cq.Workplane("XY")
    .circle(rim_r)
    .circle(rim_r - lip_t)
    .extrude(lip_h)
    .translate((0, 0, width / 2 - lip_h))
)
back_lip = (
    cq.Workplane("XY")
    .circle(rim_r)
    .circle(rim_r - lip_t)
    .extrude(lip_h)
    .translate((0, 0, -width / 2))
)
barrel = barrel.union(front_lip).union(back_lip)

# ── Combine everything ──
result = hub.union(barrel)

# ── Trim inside: cut away anything inside center bore ──
bore_cut = (
    cq.Workplane("XY")
    .circle(cb_r)
    .extrude(width * 2)
    .translate((0, 0, -width))
)
result = result.cut(bore_cut)
"""

    # Add fillet if requested
    if s_fillet > 0:
        script += f"""
# Optional fillet on spoke edges (may fail on complex geometry)
try:
    result = result.edges("|Z").fillet({min(s_fillet, sw_inner * 0.3):.1f})
except Exception:
    pass  # Skip fillet if geometry too complex
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
