"""
CadQuery script executor and mesh/export utilities.

Security note: exec() runs with full builtins so that user scripts can use
standard Python (import math, list comprehensions, etc.). This is appropriate
for a single-user local tool. Do not expose to untrusted users without adding
sandboxing (e.g. RestrictedPython or a subprocess jail).
"""
from __future__ import annotations

import io
import math
import os
import tempfile
import traceback
import zipfile

import cadquery as cq
from cadquery import Compound

from shapes import (
    round_tube, rect_tube, flat_plate, plate_with_bolt_holes,
    l_bracket, gusset, mounting_boss,
)
from validators import validate_script, has_blocking_errors
from geometry_checks import check_geometry, require_valid_geometry


# ── Coordinate-system conversion ──────────────────────────────────────────
# CadQuery / OpenCascade uses Z-up (right-handed).
# Three.js / the frontend viewport uses Y-up (right-handed).
# Conversion: (x, y, z)_ZUp → (x, z, -y)_YUp
def _z_up_to_y_up(x, y, z):
    """Convert a single point from CadQuery Z-up to Three.js Y-up."""
    return [x, z, -y]


def _z_up_to_y_up_normal(nx, ny, nz):
    """Convert a normal vector from Z-up to Y-up."""
    return [nx, nz, -ny]


def _y_up_to_z_up(x, y, z):
    """Convert a point from Three.js Y-up back to CadQuery Z-up."""
    return [x, -z, y]


def _y_up_to_z_up_normal(nx, ny, nz):
    """Convert a normal from Y-up back to Z-up."""
    return [nx, -nz, ny]


def execute_script(script: str, skip_geometry_check: bool = False) -> cq.Workplane:
    """
    Execute a CadQuery Python script and return the resulting Workplane.

    The script must assign its final shape to a variable named ``result``.
    Accepted types for ``result``: cq.Workplane, cq.Assembly, cq.Shape.
    """
    warnings = validate_script(script)
    if has_blocking_errors(warnings):
        errors = [w.message for w in warnings if w.severity == "error"]
        raise ValueError("Script validation failed:\n" + "\n".join(errors))

    captured: dict = {}

    def _show_object(obj, name=None, options=None):
        """CQ-Editor compat: capture the last shown object as result."""
        captured["result"] = obj

    namespace: dict = {
        "__builtins__": __builtins__,
        "cq": cq,
        "math": math,
        "round_tube": round_tube,
        "rect_tube": rect_tube,
        "flat_plate": flat_plate,
        "plate_with_bolt_holes": plate_with_bolt_holes,
        "l_bracket": l_bracket,
        "gusset": gusset,
        "mounting_boss": mounting_boss,
        "show_object": _show_object,
    }

    try:
        exec(compile(script, "<cad_script>", "exec"), namespace)
    except Exception:
        raise ValueError(f"Script execution failed:\n{traceback.format_exc()}")

    # If show_object was called, use its argument as result
    if "result" not in namespace and "result" in captured:
        namespace["result"] = captured["result"]

    result = namespace.get("result")

    # If no result but parts dict exists, combine all parts into one compound
    if result is None:
        parts_dict = namespace.get("parts")
        if parts_dict and isinstance(parts_dict, dict) and len(parts_dict) > 0:
            workplanes = []
            for val in parts_dict.values():
                if isinstance(val, cq.Workplane):
                    workplanes.append(val)
                elif isinstance(val, cq.Shape):
                    workplanes.append(cq.Workplane().add(val))
                elif isinstance(val, cq.Assembly):
                    workplanes.append(cq.Workplane().add(val.toCompound()))
            if workplanes:
                result = workplanes[0]
                for wp in workplanes[1:]:
                    try:
                        result = result.union(wp)
                    except Exception:
                        result = cq.Workplane().add(
                            Compound.makeCompound(
                                [result.val()] + [wp.val()]
                            )
                        )

    if result is None:
        raise ValueError(
            "The script did not assign anything to 'result' or 'parts'. "
            "Use result = ... for single parts, or parts = {} for multi-part assemblies."
        )

    # Normalise to Workplane
    if isinstance(result, cq.Assembly):
        compound = result.toCompound()
        result = cq.Workplane().add(compound)
    elif isinstance(result, cq.Shape):
        result = cq.Workplane().add(result)
    elif not isinstance(result, cq.Workplane):
        raise ValueError(
            f"'result' must be a cq.Workplane (got {type(result).__name__}). "
            "Build your model with cq.Workplane() and assign it to 'result'."
        )

    # Post-execution geometry sanity check
    if not skip_geometry_check:
        require_valid_geometry(result, context="Script execution")

    return result


def execute_script_multi(script: str) -> dict:
    """
    Execute a CadQuery script and return a dict of named parts.

    If the script defines a `parts` dict, each entry becomes a separate part.
    If only `result` is defined, returns {"Part": result} for backward compat.
    Each value is normalized to a cq.Workplane.
    """
    warnings = validate_script(script)
    if has_blocking_errors(warnings):
        errors = [w.message for w in warnings if w.severity == "error"]
        raise ValueError("Script validation failed:\n" + "\n".join(errors))

    captured: dict = {}

    def _show_object_multi(obj, name=None, options=None):
        captured["result"] = obj

    namespace: dict = {
        "__builtins__": __builtins__,
        "cq": cq,
        "math": math,
        "round_tube": round_tube,
        "rect_tube": rect_tube,
        "flat_plate": flat_plate,
        "plate_with_bolt_holes": plate_with_bolt_holes,
        "l_bracket": l_bracket,
        "gusset": gusset,
        "mounting_boss": mounting_boss,
        "show_object": _show_object_multi,
    }

    try:
        exec(compile(script, "<cad_script>", "exec"), namespace)
    except Exception:
        raise ValueError(f"Script execution failed:\n{traceback.format_exc()}")

    if "result" not in namespace and "result" in captured:
        namespace["result"] = captured["result"]

    parts_dict = namespace.get("parts")
    result = namespace.get("result")

    if parts_dict and isinstance(parts_dict, dict) and len(parts_dict) > 0:
        normalized = {}
        for name, val in parts_dict.items():
            if isinstance(val, cq.Assembly):
                compound = val.toCompound()
                val = cq.Workplane().add(compound)
            elif isinstance(val, cq.Shape):
                val = cq.Workplane().add(val)
            elif not isinstance(val, cq.Workplane):
                continue
            normalized[str(name)] = val
        if normalized:
            return normalized

    if result is not None:
        if isinstance(result, cq.Assembly):
            compound = result.toCompound()
            result = cq.Workplane().add(compound)
        elif isinstance(result, cq.Shape):
            result = cq.Workplane().add(result)
        elif not isinstance(result, cq.Workplane):
            raise ValueError(
                f"'result' must be a cq.Workplane (got {type(result).__name__})."
            )
        return {"Part": result}

    raise ValueError(
        "Script must define either a 'parts' dict or a 'result' variable."
    )


def shape_to_mesh(
    workplane: cq.Workplane,
    tolerance: float = 0.02,
    angular_tolerance: float = 0.05,
) -> dict:
    """
    Tessellate a Workplane into a Three.js-compatible mesh dict.

    Returns:
        {
            "vertices": [[x, y, z], ...],   # one entry per vertex
            "faces":    [[i, j, k], ...],   # indices into vertices
            "vertex_count": int,
            "face_count":   int,
        }
    """
    shapes = workplane.vals()
    if not shapes:
        raise ValueError("The workplane contains no shapes.")

    shape: cq.Shape = (
        shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)
    )

    vertices, triangles = shape.tessellate(tolerance, angular_tolerance)

    if not vertices or not triangles:
        raise ValueError(
            "Tessellation produced no geometry. "
            "The shape may be degenerate or have zero volume."
        )

    verts = [_z_up_to_y_up(v.x, v.y, v.z) for v in vertices]
    # Swap triangle winding order to preserve face orientation after axis swap
    faces = [[int(t[0]), int(t[2]), int(t[1])] for t in triangles]

    return {
        "vertices": verts,
        "faces": faces,
        "vertex_count": len(verts),
        "face_count": len(faces),
    }


def _classify_face(face) -> str:
    """Classify a CadQuery Face by its underlying surface type."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone,
        GeomAbs_Sphere, GeomAbs_Torus, GeomAbs_BSplineSurface,
    )
    try:
        adaptor = BRepAdaptor_Surface(face.wrapped)
        stype = adaptor.GetType()
        return {
            GeomAbs_Plane: "planar",
            GeomAbs_Cylinder: "cylindrical",
            GeomAbs_Cone: "conical",
            GeomAbs_Sphere: "spherical",
            GeomAbs_Torus: "toroidal",
            GeomAbs_BSplineSurface: "bspline",
        }.get(stype, "freeform")
    except Exception:
        return "unknown"


def _classify_edge(edge) -> str:
    """Classify a CadQuery Edge by its underlying curve type."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import (
        GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse,
        GeomAbs_BSplineCurve,
    )
    try:
        adaptor = BRepAdaptor_Curve(edge.wrapped)
        ctype = adaptor.GetType()
        return {
            GeomAbs_Line: "line",
            GeomAbs_Circle: "circle",
            GeomAbs_Ellipse: "ellipse",
            GeomAbs_BSplineCurve: "bspline",
        }.get(ctype, "other")
    except Exception:
        return "unknown"


def _discretize_edge(edge, deflection: float = 0.1) -> list:
    """Discretize an edge into a polyline of [x, y, z] points (Y-up)."""
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    try:
        adaptor = BRepAdaptor_Curve(edge.wrapped)
        discretizer = GCPnts_QuasiUniformDeflection(adaptor, deflection)
        if not discretizer.IsDone():
            # Fallback: start + end
            sp = edge.startPoint()
            ep = edge.endPoint()
            return [_z_up_to_y_up(sp.x, sp.y, sp.z), _z_up_to_y_up(ep.x, ep.y, ep.z)]
        points = []
        for i in range(1, discretizer.NbPoints() + 1):
            p = discretizer.Value(i)
            points.append(_z_up_to_y_up(p.X(), p.Y(), p.Z()))
        return points
    except Exception:
        try:
            sp = edge.startPoint()
            ep = edge.endPoint()
            return [_z_up_to_y_up(sp.x, sp.y, sp.z), _z_up_to_y_up(ep.x, ep.y, ep.z)]
        except Exception:
            return []


def shape_to_topo_mesh(
    workplane: cq.Workplane,
    tolerance: float = 0.02,
    angular_tolerance: float = 0.05,
    quality: str | None = None,
) -> dict:
    """
    Tessellate a Workplane with full BREP topology preserved.

    Returns per-face triangle ranges and real edge polylines so the frontend
    can highlight/select actual BREP faces and edges instead of guessing.

    quality: if provided, overrides tolerance/angular_tolerance.
             'draft' = fast/coarse (0.5mm), 'preview' = normal (0.1mm), 'precise' = fine (0.02mm)
    """
    if quality:
        _QUALITY_MAP = {
            "draft":   (0.5,  0.3),
            "preview": (0.1,  0.1),
            "precise": (0.02, 0.05),
        }
        tolerance, angular_tolerance = _QUALITY_MAP.get(quality, (tolerance, angular_tolerance))
    shapes = workplane.vals()
    if not shapes:
        raise ValueError("The workplane contains no shapes.")

    shape: cq.Shape = (
        shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)
    )

    all_verts = []
    all_tris = []
    face_ranges = []
    edge_polylines = []
    vert_offset = 0

    for face_idx, face in enumerate(shape.Faces()):
        try:
            verts, tris = face.tessellate(tolerance, angular_tolerance)
        except Exception:
            continue
        if not verts or not tris:
            continue

        tri_start = len(all_tris)

        for v in verts:
            all_verts.append(_z_up_to_y_up(v.x, v.y, v.z))
        for t in tris:
            # Swap winding order to preserve face orientation after axis swap
            all_tris.append([int(t[0]) + vert_offset, int(t[2]) + vert_offset, int(t[1]) + vert_offset])

        # Face metadata
        try:
            n = face.normalAt()
            face_normal = _z_up_to_y_up_normal(n.x, n.y, n.z)
        except Exception:
            face_normal = [0, 1, 0]
        try:
            c = face.Center()
            face_center = _z_up_to_y_up(c.x, c.y, c.z)
        except Exception:
            face_center = [0, 0, 0]
        try:
            face_area = face.Area()
        except Exception:
            face_area = 0.0

        face_ranges.append({
            "id": face_idx,
            "triStart": tri_start,
            "triCount": len(tris),
            "type": _classify_face(face),
            "normal": face_normal,
            "center": face_center,
            "area": face_area,
        })

        vert_offset += len(verts)

    for edge_idx, edge in enumerate(shape.Edges()):
        points = _discretize_edge(edge)
        if not points:
            continue
        try:
            edge_length = edge.Length()
        except Exception:
            edge_length = 0.0
        edge_polylines.append({
            "id": edge_idx,
            "points": points,
            "type": _classify_edge(edge),
            "length": edge_length,
        })

    if not all_verts or not all_tris:
        raise ValueError("Tessellation produced no geometry.")

    return {
        "vertices": all_verts,
        "faces": all_tris,
        "vertex_count": len(all_verts),
        "face_count": len(all_tris),
        "topo_faces": face_ranges,
        "topo_edges": edge_polylines,
    }


def find_face_at_point(workplane: cq.Workplane, point: list, normal: list) -> dict:
    """
    Find the face on a workplane's shape closest to the given point+normal.
    Returns a dict with face_index, face_selector (CadQuery string), and face_type.
    """
    from OCP.BRep import BRep_Tool
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere

    shapes = workplane.vals()
    if not shapes:
        return {"face_index": -1, "face_selector": ">Y", "face_type": "unknown"}

    shape = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)
    # Convert input from Y-up (frontend) to Z-up (CadQuery)
    pt_z = _y_up_to_z_up(point[0], point[1], point[2])
    nm_z = _y_up_to_z_up_normal(normal[0], normal[1], normal[2])
    target_pt = gp_Pnt(pt_z[0], pt_z[1], pt_z[2])
    target_normal = gp_Vec(nm_z[0], nm_z[1], nm_z[2])

    best_idx = -1
    best_dist = float("inf")
    best_face_normal = None
    best_face_type = "unknown"

    faces = shape.Faces()
    for idx, face in enumerate(faces):
        # Get center of mass of the face
        props = face.Center()
        center = gp_Pnt(props.x, props.y, props.z)
        dist = center.Distance(target_pt)

        if dist < best_dist:
            best_dist = dist
            best_idx = idx

            # Determine face type
            try:
                adaptor = BRepAdaptor_Surface(face.wrapped)
                stype = adaptor.GetType()
                if stype == GeomAbs_Plane:
                    best_face_type = "planar"
                elif stype == GeomAbs_Cylinder:
                    best_face_type = "cylindrical"
                elif stype == GeomAbs_Cone:
                    best_face_type = "conical"
                elif stype == GeomAbs_Sphere:
                    best_face_type = "spherical"
                else:
                    best_face_type = "freeform"
            except Exception:
                best_face_type = "unknown"

            # Get face normal at center (keep in Z-up for selector generation)
            try:
                face_normal = face.normalAt()
                best_face_normal = [face_normal.x, face_normal.y, face_normal.z]
            except Exception:
                best_face_normal = nm_z  # Use Z-up converted normal

    # Generate CadQuery string selector from Z-up normal
    if best_face_normal:
        abs_vals = [abs(best_face_normal[0]), abs(best_face_normal[1]), abs(best_face_normal[2])]
        max_idx = abs_vals.index(max(abs_vals))
        axes = ["X", "Y", "Z"]
        direction = ">" if best_face_normal[max_idx] >= 0 else "<"
        face_selector = f"{direction}{axes[max_idx]}"
    else:
        face_selector = ">Z"

    # Compute area and center for the best face
    best_area = 0.0
    best_center = [0, 0, 0]
    if best_idx >= 0:
        try:
            best_face = faces[best_idx]
            best_area = best_face.Area()
            c = best_face.Center()
            best_center = _z_up_to_y_up(c.x, c.y, c.z)
        except Exception:
            pass

    # Convert face_normal to Y-up for the frontend
    face_normal_yup = _z_up_to_y_up_normal(*best_face_normal) if best_face_normal else normal

    return {
        "face_index": best_idx,
        "face_selector": face_selector,
        "face_type": best_face_type,
        "face_normal": face_normal_yup,
        "face_area": best_area,
        "face_center": best_center,
    }


def extract_bounding_box(workplane: cq.Workplane) -> dict:
    """Extract bounding box from a Workplane for storage in the parts table (Y-up)."""
    shapes = workplane.vals()
    if not shapes:
        return {}
    shape = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)
    bb = shape.BoundingBox()
    # Convert from Z-up to Y-up: (x, y, z) → (x, z, -y)
    # min/max swap on the -y axis since negation flips ordering
    mn = _z_up_to_y_up(bb.xmin, bb.ymin, bb.zmin)
    mx = _z_up_to_y_up(bb.xmax, bb.ymax, bb.zmax)
    return {
        "bbox_min_x": min(mn[0], mx[0]),
        "bbox_min_y": min(mn[1], mx[1]),
        "bbox_min_z": min(mn[2], mx[2]),
        "bbox_max_x": max(mn[0], mx[0]),
        "bbox_max_y": max(mn[1], mx[1]),
        "bbox_max_z": max(mn[2], mx[2]),
    }


def compute_volume(workplane: cq.Workplane) -> float:
    """Return the volume of the workplane's shape in mm^3."""
    shapes = workplane.vals()
    if not shapes:
        return 0.0
    shape = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)
    return shape.Volume()


def export_stl(workplane: cq.Workplane) -> bytes:
    """Return binary STL bytes for the workplane."""
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
        tmp = f.name
    try:
        cq.exporters.export(workplane, tmp, exportType="STL")
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export_step(workplane: cq.Workplane) -> bytes:
    """Return STEP bytes for the workplane."""
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
        tmp = f.name
    try:
        cq.exporters.export(workplane, tmp, exportType="STEP")
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export_brep(workplane: cq.Workplane) -> bytes:
    """Return B-Rep bytes for client-side tessellation."""
    with tempfile.NamedTemporaryFile(suffix=".brep", delete=False) as f:
        tmp = f.name
    try:
        cq.exporters.export(workplane, tmp, exportType="BREP")
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export_parts_stl_zip(parts_data: list) -> bytes:
    """
    Export multiple parts as individual STL files in a ZIP.
    parts_data: [{"name": str, "workplane": cq.Workplane}, ...]
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in parts_data:
            stl_bytes = export_stl(part["workplane"])
            safe_name = part["name"].replace("/", "_").replace("\\", "_")
            zf.writestr(f"{safe_name}.stl", stl_bytes)
    return buffer.getvalue()


def export_assembly_step(parts_data: list) -> bytes:
    """Export all parts as a single STEP file with separate bodies."""
    assembly = cq.Assembly()
    for part in parts_data:
        assembly.add(part["workplane"], name=part["name"])
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
        tmp = f.name
    try:
        assembly.save(tmp, exportType="STEP")
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
