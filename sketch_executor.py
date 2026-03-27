"""
Sketch Executor — converts 2D sketch entity lists into CadQuery 3D geometry.

Sketch entities are plain dicts stored in the `sketches.entities` JSONB column.
Supported entity types:
    line      {type, x1, y1, x2, y2}
    circle    {type, cx, cy, r}
    arc       {type, cx, cy, r, start_angle, end_angle, clockwise=False}
    rect      {type, x, y, width, height}   (expanded to 4 lines)
    polyline  {type, points: [[x,y],...], closed: bool}
"""
from __future__ import annotations

import math
from typing import List, Dict, Any, Optional, Tuple

import cadquery as cq
from cadquery import Edge, Wire, Vector


TOLERANCE = 0.5  # mm — snap tolerance for loop closure


# ---------------------------------------------------------------------------
# Entity normalisation — expand rects / polylines to primitives
# ---------------------------------------------------------------------------

def _normalise_entities(entities: List[Dict]) -> List[Dict]:
    """Expand rect and polyline shortcuts into line / arc primitives."""
    out = []
    for e in entities:
        t = e.get("type")
        if t == "rect":
            x, y, w, h = e["x"], e["y"], e["width"], e["height"]
            out += [
                {"type": "line", "x1": x,   "y1": y,   "x2": x+w, "y2": y},
                {"type": "line", "x1": x+w, "y1": y,   "x2": x+w, "y2": y+h},
                {"type": "line", "x1": x+w, "y1": y+h, "x2": x,   "y2": y+h},
                {"type": "line", "x1": x,   "y1": y+h, "x2": x,   "y2": y},
            ]
        elif t == "polyline":
            pts = e["points"]
            closed = e.get("closed", False)
            for i in range(len(pts) - 1):
                out.append({"type": "line",
                            "x1": pts[i][0], "y1": pts[i][1],
                            "x2": pts[i+1][0], "y2": pts[i+1][1]})
            if closed and len(pts) > 2:
                out.append({"type": "line",
                            "x1": pts[-1][0], "y1": pts[-1][1],
                            "x2": pts[0][0],  "y2": pts[0][1]})
        else:
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------

def _endpoints(e: Dict) -> Tuple[Optional[Tuple], Optional[Tuple]]:
    t = e["type"]
    if t == "line":
        return (e["x1"], e["y1"]), (e["x2"], e["y2"])
    if t == "arc":
        cx, cy, r = e["cx"], e["cy"], e["r"]
        sa = math.radians(e["start_angle"])
        ea = math.radians(e["end_angle"])
        return (cx + r * math.cos(sa), cy + r * math.sin(sa)), \
               (cx + r * math.cos(ea), cy + r * math.sin(ea))
    if t == "circle":
        return None, None   # circles are self-contained loops
    return None, None


def _dist(a: Tuple, b: Tuple) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _reverse(e: Dict) -> Dict:
    t = e["type"]
    if t == "line":
        return {**e, "x1": e["x2"], "y1": e["y2"], "x2": e["x1"], "y2": e["y1"]}
    if t == "arc":
        return {**e, "start_angle": e["end_angle"], "end_angle": e["start_angle"],
                "clockwise": not e.get("clockwise", False)}
    return e


# ---------------------------------------------------------------------------
# Loop detection — chain segments into closed wires
# ---------------------------------------------------------------------------

def _find_loops(entities: List[Dict]) -> List[List[Dict]]:
    """
    Group entities into closed loops by chaining endpoints.
    Circles become single-entity loops.
    """
    circles = [e for e in entities if e["type"] == "circle"]
    segs    = [e for e in entities if e["type"] != "circle"]

    loops = [[c] for c in circles]   # each circle is its own loop

    remaining = list(segs)
    while remaining:
        loop = [remaining.pop(0)]
        while True:
            _, tail = _endpoints(loop[-1])
            head, _ = _endpoints(loop[0])
            if tail is None:
                break
            # Check closure
            if len(loop) > 1 and head is not None and _dist(tail, head) < TOLERANCE:
                break
            # Find next segment
            found = False
            for i, ent in enumerate(remaining):
                ep_s, ep_e = _endpoints(ent)
                if ep_s is None:
                    continue
                if _dist(tail, ep_s) < TOLERANCE:
                    loop.append(remaining.pop(i))
                    found = True
                    break
                if _dist(tail, ep_e) < TOLERANCE:
                    loop.append(_reverse(remaining.pop(i)))
                    found = True
                    break
            if not found:
                break
        loops.append(loop)

    return loops


# ---------------------------------------------------------------------------
# Loop → CadQuery Wire
# ---------------------------------------------------------------------------

def _loop_to_wire(loop: List[Dict]) -> Optional[Wire]:
    """Convert one closed loop into a CadQuery Wire."""
    if len(loop) == 1 and loop[0]["type"] == "circle":
        e = loop[0]
        center = Vector(e["cx"], e["cy"], 0)
        normal = Vector(0, 0, 1)
        circle = Edge.makeCircle(e["r"], center, normal)
        return Wire.assembleEdges([circle])

    edges = []
    for e in loop:
        t = e["type"]
        if t == "line":
            p1 = Vector(e["x1"], e["y1"], 0)
            p2 = Vector(e["x2"], e["y2"], 0)
            if _dist((e["x1"], e["y1"]), (e["x2"], e["y2"])) < 1e-6:
                continue
            edges.append(Edge.makeLine(p1, p2))

        elif t == "arc":
            cx, cy, r = e["cx"], e["cy"], e["r"]
            sa_deg = e["start_angle"]
            ea_deg = e["end_angle"]
            clockwise = e.get("clockwise", False)

            sa = math.radians(sa_deg)
            ea = math.radians(ea_deg)

            # Midpoint angle
            if clockwise:
                if ea_deg > sa_deg:
                    mid_deg = sa_deg - (360 - ea_deg + sa_deg) / 2
                else:
                    mid_deg = sa_deg - (sa_deg - ea_deg) / 2
            else:
                if ea_deg < sa_deg:
                    mid_deg = sa_deg + (360 - sa_deg + ea_deg) / 2
                else:
                    mid_deg = sa_deg + (ea_deg - sa_deg) / 2

            mid = math.radians(mid_deg)
            p_start = Vector(cx + r * math.cos(sa),  cy + r * math.sin(sa),  0)
            p_mid   = Vector(cx + r * math.cos(mid), cy + r * math.sin(mid), 0)
            p_end   = Vector(cx + r * math.cos(ea),  cy + r * math.sin(ea),  0)
            edges.append(Edge.makeThreePointArc(p_start, p_mid, p_end))

    if not edges:
        return None
    try:
        return Wire.assembleEdges(edges)
    except Exception:
        # Fall back: try closing manually
        try:
            return Wire.assembleEdges(edges + [Edge.makeLine(
                edges[-1].endPoint(), edges[0].startPoint()
            )])
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Loop area + containment (to distinguish outer from inner loops)
# ---------------------------------------------------------------------------

def _loop_area(loop: List[Dict]) -> float:
    """Approximate signed area via shoelace on sampled points."""
    pts = _sample_loop(loop, samples_per_seg=8)
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return area / 2.0


def _sample_loop(loop: List[Dict], samples_per_seg: int = 8) -> List[Tuple[float, float]]:
    pts = []
    for e in loop:
        t = e["type"]
        if t == "line":
            pts.append((e["x1"], e["y1"]))
        elif t == "circle":
            cx, cy, r = e["cx"], e["cy"], e["r"]
            for i in range(samples_per_seg * 4):
                a = 2 * math.pi * i / (samples_per_seg * 4)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        elif t == "arc":
            cx, cy, r = e["cx"], e["cy"], e["r"]
            sa = math.radians(e["start_angle"])
            ea = math.radians(e["end_angle"])
            if not e.get("clockwise", False) and ea < sa:
                ea += 2 * math.pi
            for i in range(samples_per_seg):
                a = sa + (ea - sa) * i / max(1, samples_per_seg - 1)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _point_in_loop(pt: Tuple[float, float], loop: List[Dict]) -> bool:
    """Ray-cast point-in-polygon test using sampled loop points."""
    pts = _sample_loop(loop)
    x, y = pt
    inside = False
    n = len(pts)
    for i in range(n):
        j = (i - 1) % n
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
    return inside


def _loop_centroid(loop: List[Dict]) -> Tuple[float, float]:
    pts = _sample_loop(loop)
    if not pts:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

PLANE_NAMES = {
    "XY": "XY", "XZ": "XZ", "YZ": "YZ",
    "xy": "XY", "xz": "XZ", "yz": "YZ",
}

# Extrusion direction vectors per plane
_PLANE_EXTRUDE_DIR = {
    "XY": Vector(0, 0, 1),
    "XZ": Vector(0, 1, 0),
    "YZ": Vector(1, 0, 0),
}


def sketch_extrude(
    entities: List[Dict],
    depth: float,
    plane: str = "XY",
    operation: str = "boss",
    existing_wp: Optional[cq.Workplane] = None,
    symmetric: bool = False,
) -> cq.Workplane:
    """
    Extrude a 2D sketch into a 3D solid.

    Parameters
    ----------
    entities  : list of sketch entity dicts
    depth     : extrusion depth in mm (always positive)
    plane     : "XY" | "XZ" | "YZ"
    operation : "boss" (add material) | "cut" (remove material)
    existing_wp : if given and operation=="cut", cut from this shape
    symmetric : extrude in both directions
    """
    plane_name = PLANE_NAMES.get(plane, "XY")
    normalised = _normalise_entities(entities)
    loops = _find_loops(normalised)

    if not loops:
        raise ValueError("Sketch has no closed profiles to extrude")

    # Sort by absolute area descending — largest = outer loop
    loops_with_area = [(abs(_loop_area(lp)), lp) for lp in loops]
    loops_with_area.sort(key=lambda x: x[0], reverse=True)
    outer_loop = loops_with_area[0][1]
    inner_loops = [lp for _, lp in loops_with_area[1:]]

    outer_wire = _loop_to_wire(outer_loop)
    if outer_wire is None:
        raise ValueError("Could not build outer wire from sketch entities")

    # Build face from outer wire, punching holes for inner loops
    inner_wires = []
    for ilp in inner_loops:
        centroid = _loop_centroid(ilp)
        if _point_in_loop(centroid, outer_loop):
            wire = _loop_to_wire(ilp)
            if wire:
                inner_wires.append(wire)

    # Correct API: Solid.extrudeLinear(outerWire, innerWires, vecNormal)
    # (NOT face — that overload only takes (face, vecNormal) with no hole wires)
    extrude_dir = _PLANE_EXTRUDE_DIR.get(plane_name, Vector(0, 0, 1))

    if symmetric:
        solid_pos = cq.Solid.extrudeLinear(outer_wire, inner_wires, extrude_dir * (depth / 2))
        solid_neg = cq.Solid.extrudeLinear(outer_wire, inner_wires, extrude_dir * (-depth / 2))
        solid = solid_pos.fuse(solid_neg)
    else:
        solid = cq.Solid.extrudeLinear(outer_wire, inner_wires, extrude_dir * depth)

    result_wp = cq.Workplane(plane_name).newObject([solid])

    if operation == "cut" and existing_wp is not None:
        existing_shapes = existing_wp.vals()
        cut_shapes = result_wp.vals()
        if existing_shapes and cut_shapes:
            base = existing_shapes[0]
            cutter = cut_shapes[0]
            return cq.Workplane(plane_name).newObject([base.cut(cutter)])

    if operation == "boss" and existing_wp is not None:
        existing_shapes = existing_wp.vals()
        new_shapes = result_wp.vals()
        if existing_shapes and new_shapes:
            base = existing_shapes[0]
            addition = new_shapes[0]
            return cq.Workplane(plane_name).newObject([base.fuse(addition)])

    return result_wp


def sketch_revolve(
    entities: List[Dict],
    angle: float = 360.0,
    axis: str = "Y",
    plane: str = "XZ",
    operation: str = "boss",
    existing_wp: Optional[cq.Workplane] = None,
) -> cq.Workplane:
    """
    Revolve a 2D sketch profile around an axis.

    The profile should be on one side of the axis (positive X typically).
    """
    plane_name = PLANE_NAMES.get(plane, "XZ")
    normalised = _normalise_entities(entities)
    loops = _find_loops(normalised)

    if not loops:
        raise ValueError("Sketch has no profiles to revolve")

    loops_with_area = [(abs(_loop_area(lp)), lp) for lp in loops]
    loops_with_area.sort(key=lambda x: x[0], reverse=True)
    outer_loop = loops_with_area[0][1]

    outer_wire = _loop_to_wire(outer_loop)
    if outer_wire is None:
        raise ValueError("Could not build wire for revolve")

    # Solid.revolve(outerWire, innerWires, angleDegrees, axisStart, axisEnd)
    # axisStart / axisEnd are two POINTS defining the axis line (not origin + direction)
    axis_end_map = {
        "X": Vector(1, 0, 0),
        "Y": Vector(0, 1, 0),
        "Z": Vector(0, 0, 1),
    }
    ax_end = axis_end_map.get(axis.upper(), Vector(0, 1, 0))
    ax_start = Vector(0, 0, 0)

    solid = cq.Solid.revolve(outer_wire, [], angle, ax_start, ax_end)
    result_wp = cq.Workplane(plane_name).newObject([solid])

    if operation == "cut" and existing_wp is not None:
        existing_shapes = existing_wp.vals()
        cut_shapes = result_wp.vals()
        if existing_shapes and cut_shapes:
            return cq.Workplane(plane_name).newObject([existing_shapes[0].cut(cut_shapes[0])])

    if operation == "boss" and existing_wp is not None:
        existing_shapes = existing_wp.vals()
        new_shapes = result_wp.vals()
        if existing_shapes and new_shapes:
            return cq.Workplane(plane_name).newObject([existing_shapes[0].fuse(new_shapes[0])])

    return result_wp
