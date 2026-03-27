"""Modal.com serverless functions for CadQuery execution.

Deploy with: modal deploy modal_functions.py
Test with:   modal run modal_functions.py

These functions run in an isolated container with CadQuery + OCP installed.
The FastAPI server calls them via the Modal client SDK.
"""
import modal

# ── Modal app + image ─────────────────────────────────────────────────────────

app = modal.App("aleksma-cad")

cadquery_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "libgl1-mesa-glx",
        "libglib2.0-0",
        "libsm6",
        "libxrender1",
        "libxext6",
    )
    .run_commands(
        # Install miniforge for conda-forge access (CadQuery's OCP isn't on PyPI)
        "apt-get update && apt-get install -y curl bzip2",
        "curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh",
        "bash /tmp/miniforge.sh -b -p /opt/conda",
        "rm /tmp/miniforge.sh",
        # Create env with Python 3.11 — OCP has no builds for 3.13
        '/opt/conda/bin/conda create -y -n cad python=3.11 -c conda-forge',
        '/opt/conda/bin/conda install -y -n cad -c conda-forge cadquery=2.4.0',
        "/opt/conda/bin/conda clean -afy",
    )
    .env({"PATH": "/opt/conda/envs/cad/bin:/opt/conda/bin:$PATH"})
    .add_local_file("validators.py", "/app/validators.py")
    .add_local_file("geometry_checks.py", "/app/geometry_checks.py")
    .add_local_file("shapes.py", "/app/shapes.py")
)


# ── Helper: execute script in sandbox ─────────────────────────────────────────

def _exec_script(script: str):
    """Execute a CadQuery script and return the result Workplane.

    Mirrors executor.py logic but runs inside Modal container.
    """
    import sys
    sys.path.insert(0, "/app")

    import math
    import traceback
    import cadquery as cq
    from cadquery import Compound
    from validators import validate_script, has_blocking_errors

    # Validate
    warnings = validate_script(script)
    warn_msgs = [w.message for w in warnings if w.severity == "warning"]
    if has_blocking_errors(warnings):
        errors = [w.message for w in warnings if w.severity == "error"]
        raise ValueError("Script validation failed:\n" + "\n".join(errors))

    # Load shape helpers (optional — don't fail if not available)
    shape_helpers = {}
    try:
        from shapes import (
            round_tube, rect_tube, flat_plate, plate_with_bolt_holes,
            l_bracket, gusset, mounting_boss,
        )
        shape_helpers = {
            "round_tube": round_tube,
            "rect_tube": rect_tube,
            "flat_plate": flat_plate,
            "plate_with_bolt_holes": plate_with_bolt_holes,
            "l_bracket": l_bracket,
            "gusset": gusset,
            "mounting_boss": mounting_boss,
        }
    except ImportError:
        pass

    captured = {}

    def _show_object(obj, name=None, options=None):
        captured["result"] = obj

    namespace = {
        "__builtins__": __builtins__,
        "cq": cq,
        "math": math,
        "show_object": _show_object,
        **shape_helpers,
    }

    try:
        exec(compile(script, "<cad_script>", "exec"), namespace)
    except Exception:
        raise ValueError(f"Script execution failed:\n{traceback.format_exc()}")

    if "result" not in namespace and "result" in captured:
        namespace["result"] = captured["result"]

    result = namespace.get("result")

    # Handle parts dict
    if result is None:
        parts_dict = namespace.get("parts")
        if parts_dict and isinstance(parts_dict, dict) and len(parts_dict) > 0:
            workplanes = []
            for val in parts_dict.values():
                if isinstance(val, cq.Workplane):
                    workplanes.append(val)
                elif isinstance(val, cq.Shape):
                    workplanes.append(cq.Workplane().add(val))
            if workplanes:
                result = workplanes[0]
                for wp in workplanes[1:]:
                    try:
                        result = result.union(wp)
                    except Exception:
                        result = cq.Workplane().add(
                            Compound.makeCompound([result.val()] + [wp.val()])
                        )

    if result is None:
        raise ValueError("Script did not assign to 'result' or 'parts'")

    # Normalize
    if isinstance(result, cq.Assembly):
        result = cq.Workplane().add(result.toCompound())
    elif isinstance(result, cq.Shape):
        result = cq.Workplane().add(result)

    return result, warn_msgs


def _z_up_to_y_up(x, y, z):
    return [x, z, -y]


def _z_up_to_y_up_normal(nx, ny, nz):
    return [nx, nz, -ny]


def _topo_mesh(workplane, tolerance=0.02, angular_tolerance=0.05, quality=None):
    """Tessellate with full BREP topology — mirrors executor.shape_to_topo_mesh."""
    import cadquery as cq
    from cadquery import Compound

    if quality:
        q_map = {"draft": (0.5, 0.3), "preview": (0.1, 0.1), "precise": (0.02, 0.05)}
        tolerance, angular_tolerance = q_map.get(quality, (tolerance, angular_tolerance))

    shapes = workplane.vals()
    shape = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)

    all_verts, all_tris, face_ranges, edge_polylines = [], [], [], []
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
            all_tris.append([int(t[0]) + vert_offset, int(t[2]) + vert_offset, int(t[1]) + vert_offset])

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
            "id": face_idx, "triStart": tri_start, "triCount": len(tris),
            "type": "planar", "normal": face_normal, "center": face_center, "area": face_area,
        })
        vert_offset += len(verts)

    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    for edge_idx, edge in enumerate(shape.Edges()):
        try:
            adaptor = BRepAdaptor_Curve(edge.wrapped)
            discretizer = GCPnts_QuasiUniformDeflection(adaptor, 0.1)
            if discretizer.IsDone():
                points = []
                for i in range(1, discretizer.NbPoints() + 1):
                    p = discretizer.Value(i)
                    points.append(_z_up_to_y_up(p.X(), p.Y(), p.Z()))
            else:
                sp, ep = edge.startPoint(), edge.endPoint()
                points = [_z_up_to_y_up(sp.x, sp.y, sp.z), _z_up_to_y_up(ep.x, ep.y, ep.z)]
        except Exception:
            continue

        try:
            edge_length = edge.Length()
        except Exception:
            edge_length = 0.0

        edge_polylines.append({"id": edge_idx, "points": points, "type": "line", "length": edge_length})

    return {
        "vertices": all_verts, "faces": all_tris,
        "vertex_count": len(all_verts), "face_count": len(all_tris),
        "topo_faces": face_ranges, "topo_edges": edge_polylines,
    }


def _extract_bbox(workplane):
    """Extract bounding box in Y-up coords."""
    from cadquery import Compound
    shapes = workplane.vals()
    if not shapes:
        return {}
    shape = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)
    bb = shape.BoundingBox()
    mn = _z_up_to_y_up(bb.xmin, bb.ymin, bb.zmin)
    mx = _z_up_to_y_up(bb.xmax, bb.ymax, bb.zmax)
    return {
        "bbox_min_x": min(mn[0], mx[0]), "bbox_min_y": min(mn[1], mx[1]), "bbox_min_z": min(mn[2], mx[2]),
        "bbox_max_x": max(mn[0], mx[0]), "bbox_max_y": max(mn[1], mx[1]), "bbox_max_z": max(mn[2], mx[2]),
    }


def _geometry_checks(workplane):
    """Run geometry sanity checks — returns (warnings, errors, face_count, edge_count, volume)."""
    import sys
    sys.path.insert(0, "/app")
    from geometry_checks import check_geometry
    result = check_geometry(workplane)
    return result.warnings, result.errors, result.face_count, result.edge_count, result.volume


# ── Modal functions ───────────────────────────────────────────────────────────

@app.function(image=cadquery_image, timeout=120)
def execute_and_mesh(script: str, quality: str = "preview") -> dict:
    """Execute CadQuery script, validate geometry, return mesh + bbox.

    This is the primary function called by the FastAPI backend.
    """
    try:
        wp, warn_msgs = _exec_script(script)
        geo_warnings, geo_errors, face_count, edge_count, volume = _geometry_checks(wp)

        all_warnings = warn_msgs + geo_warnings
        if geo_errors:
            return {
                "error": "Geometry validation failed: " + "; ".join(geo_errors),
                "warnings": all_warnings,
            }

        mesh = _topo_mesh(wp, quality=quality)
        bbox = _extract_bbox(wp)

        return {
            "mesh": mesh,
            "bbox": bbox,
            "volume": volume,
            "face_count": face_count,
            "edge_count": edge_count,
            "warnings": all_warnings,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "mesh": None, "bbox": None, "warnings": []}


@app.function(image=cadquery_image, timeout=120)
def execute_only(script: str) -> dict:
    """Execute script, return bbox + volume only (no mesh tessellation)."""
    try:
        wp, warn_msgs = _exec_script(script)
        bbox = _extract_bbox(wp)
        volume = wp.vals()[0].Volume() if wp.vals() else 0.0
        geo_warnings, geo_errors, _, _, _ = _geometry_checks(wp)

        return {
            "bbox": bbox,
            "volume": volume,
            "warnings": warn_msgs + geo_warnings,
            "error": "; ".join(geo_errors) if geo_errors else None,
        }
    except Exception as e:
        return {"error": str(e), "bbox": None, "volume": 0.0, "warnings": []}


@app.function(image=cadquery_image, timeout=180)
def execute_and_export(script: str, export_format: str = "stl") -> dict:
    """Execute script and export to STL/STEP/BREP, returning base64-encoded bytes."""
    import base64
    import tempfile
    import os
    import cadquery as cq

    try:
        wp, _ = _exec_script(script)

        with tempfile.NamedTemporaryFile(suffix=f".{export_format}", delete=False) as f:
            tmp = f.name
        try:
            fmt_map = {"stl": "STL", "step": "STEP", "brep": "BREP"}
            cq.exporters.export(wp, tmp, exportType=fmt_map.get(export_format, "STL"))
            with open(tmp, "rb") as f:
                raw = f.read()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

        return {
            "export_bytes": base64.b64encode(raw).decode("ascii"),
            "format": export_format,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "export_bytes": None}


# ── Local testing ─────────────────────────────────────────────────────────────

@app.local_entrypoint()
def main():
    """Test the Modal functions locally."""
    test_script = """
import cadquery as cq
result = cq.Workplane("XY").box(50, 30, 20).edges("|Z").fillet(3)
"""
    print("Testing execute_and_mesh...")
    result = execute_and_mesh.remote(script=test_script, quality="preview")
    if result["error"]:
        print(f"ERROR: {result['error']}")
    else:
        print(f"OK: {result['mesh']['vertex_count']} vertices, {result['mesh']['face_count']} faces")
        print(f"    Volume: {result['volume']:.1f} mm³")
        print(f"    Warnings: {result['warnings']}")

    print("\nTesting execute_and_export (STL)...")
    export_result = execute_and_export.remote(script=test_script, export_format="stl")
    if export_result["error"]:
        print(f"ERROR: {export_result['error']}")
    else:
        import base64
        stl_bytes = base64.b64decode(export_result["export_bytes"])
        print(f"OK: STL export {len(stl_bytes)} bytes")
