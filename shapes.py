"""
Tested CadQuery shape generators for common mechanical components.

Every function returns a cq.Workplane centered at origin.
Use .translate((x, y, z)) to position after calling.
"""

import cadquery as cq
import math


def _validate_positive(**kwargs):
    for name, val in kwargs.items():
        if val <= 0:
            raise ValueError(f"{name} must be positive (got {val})")


def _rotate_to_axis(wp: cq.Workplane, axis: str) -> cq.Workplane:
    axis = axis.upper()
    if axis == "X":
        return wp.rotate((0, 0, 0), (0, 1, 0), 90)
    elif axis == "Y":
        return wp.rotate((0, 0, 0), (1, 0, 0), 90)
    elif axis == "Z":
        return wp
    raise ValueError(f"axis must be 'X', 'Y', or 'Z' (got '{axis}')")


def round_tube(length: float, od: float = 30, wall: float = 2, axis: str = "Z") -> cq.Workplane:
    """Hollow round tube."""
    _validate_positive(length=length, od=od, wall=wall)
    if wall >= od / 2:
        raise ValueError(f"Wall thickness ({wall}) must be less than radius ({od/2})")
    outer = cq.Workplane("XY").cylinder(length, od / 2)
    inner = cq.Workplane("XY").cylinder(length + 2, od / 2 - wall)
    tube = outer.cut(inner)
    return _rotate_to_axis(tube, axis)


def rect_tube(length: float, width: float = 40, height: float = 25,
              wall: float = 2, axis: str = "Z") -> cq.Workplane:
    """Hollow rectangular tube."""
    _validate_positive(length=length, width=width, height=height, wall=wall)
    if wall >= width / 2 or wall >= height / 2:
        raise ValueError(f"Wall ({wall}) must be less than half of width ({width}) and height ({height})")
    outer = cq.Workplane("XY").box(width, height, length)
    inner = cq.Workplane("XY").box(width - 2 * wall, height - 2 * wall, length + 2)
    tube = outer.cut(inner)
    return _rotate_to_axis(tube, axis)


def flat_plate(width: float, length: float, thickness: float = 4) -> cq.Workplane:
    """Solid flat plate on XY plane."""
    _validate_positive(width=width, length=length, thickness=thickness)
    return cq.Workplane("XY").box(width, length, thickness)


def plate_with_bolt_holes(width: float, length: float, thickness: float = 4,
                          hole_d: float = 8.5, margin: float = 15) -> cq.Workplane:
    """Flat plate with bolt holes at corners."""
    _validate_positive(width=width, length=length, thickness=thickness, hole_d=hole_d, margin=margin)
    if margin * 2 >= width or margin * 2 >= length:
        raise ValueError(f"Margin ({margin}) too large for plate dimensions ({width}x{length})")
    plate = cq.Workplane("XY").box(width, length, thickness)
    hx = width / 2 - margin
    hy = length / 2 - margin
    for x, y in [(hx, hy), (-hx, hy), (hx, -hy), (-hx, -hy)]:
        plate = (
            plate.faces(">Z").workplane()
            .center(x, y)
            .hole(hole_d)
        )
    return plate


def l_bracket(flange_w: float, flange_h: float, length: float,
              thickness: float = 4, fillet_r: float = 3) -> cq.Workplane:
    """L-shaped bracket, extruded along X."""
    _validate_positive(flange_w=flange_w, flange_h=flange_h, length=length, thickness=thickness)
    if fillet_r < 0:
        raise ValueError("fillet_r must be non-negative")
    profile = (
        cq.Workplane("YZ")
        .moveTo(0, 0)
        .lineTo(flange_w, 0)
        .lineTo(flange_w, thickness)
        .lineTo(thickness, thickness)
        .lineTo(thickness, flange_h)
        .lineTo(0, flange_h)
        .close()
    )
    bracket = profile.extrude(length)
    if fillet_r > 0 and fillet_r < thickness:
        try:
            bracket = bracket.edges("|X").fillet(fillet_r)
        except Exception:
            pass
    return bracket


def gusset(width: float, height: float, thickness: float = 3) -> cq.Workplane:
    """Triangular gusset plate for joint reinforcement."""
    _validate_positive(width=width, height=height, thickness=thickness)
    profile = (
        cq.Workplane("XY")
        .moveTo(0, 0)
        .lineTo(width, 0)
        .lineTo(0, height)
        .close()
    )
    return profile.extrude(thickness)


def mounting_boss(height: float, od: float = 12, bore_d: float = 6) -> cq.Workplane:
    """Cylindrical mounting boss with center bore hole."""
    _validate_positive(height=height, od=od, bore_d=bore_d)
    if bore_d >= od:
        raise ValueError(f"Bore diameter ({bore_d}) must be less than outer diameter ({od})")
    boss = cq.Workplane("XY").cylinder(height, od / 2)
    return boss.faces(">Z").workplane().hole(bore_d)
