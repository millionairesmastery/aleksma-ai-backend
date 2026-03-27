"""
face_param_mapper.py — Maps B-Rep faces to their creating features and draggable parameters.

After a feature tree rebuild, we know which features produced the final shape.
This module analyses the shape's faces to determine:
1. Which feature created each face (by replaying features and tracking face creation)
2. Which parameter controls a face's position/size (inferring drag behavior)
3. What drag axis/direction makes sense for each face
"""
from __future__ import annotations
import cadquery as cq
from typing import Optional

# Face type -> likely drag parameter mapping
# When a user drags a face, what parameter should change?
DRAG_RULES = {
    # For extrude-like features (box, cylinder, sketch_extrude):
    #   - Top/bottom planar face -> "height" or "depth" param (drag along normal)
    #   - Side cylindrical face -> "radius" param (drag radially)
    #   - Side planar face -> "width" or "depth" (drag along normal)

    # For hole features:
    #   - Cylindrical inner face -> "diameter" or "radius"
    #   - Bottom planar face -> "depth"

    # For fillet/chamfer:
    #   - Toroidal/cylindrical blend face -> "radius"
}


def map_faces_to_features(
    features: list[dict],
    feature_shapes: dict[int, object],  # sequence -> cq.Workplane at that step
    final_shape: object,  # final cq.Workplane
) -> list[dict]:
    """
    Analyse the final shape's faces and map each to its creating feature.

    Returns a list of face_bindings, one per topo_face, each containing:
    {
        "feature_id": int or None,        # which feature created this face
        "feature_type": str or None,      # e.g. "box", "sketch_extrude"
        "drag_param": str or None,        # which param to change on drag (e.g. "height")
        "drag_axis": [x, y, z] or None,   # world-space drag direction
        "drag_scale": float,              # multiplier: how much param changes per mm drag
    }

    Strategy:
    We use a heuristic approach based on face geometry + the last feature applied:

    1. For each face in the final shape, classify it (planar, cylindrical, etc.)
    2. Get its normal/center
    3. Try to match it to a feature based on:
       - Face type + feature type (e.g., planar top face of a box -> height param)
       - Geometric position relative to feature params
    """
    if final_shape is None:
        return []

    try:
        shape = final_shape.val() if hasattr(final_shape, 'val') else final_shape
        faces = shape.Faces()
    except Exception:
        return []

    if not features:
        return [_empty_binding() for _ in faces]

    # Build a sorted list of non-suppressed features
    active = sorted(
        [f for f in features if not f.get("suppressed", False)],
        key=lambda f: f["sequence"]
    )

    bindings = []
    for face_idx, face in enumerate(faces):
        binding = _analyse_face(face, face_idx, active, feature_shapes)
        bindings.append(binding)

    return bindings


def _empty_binding():
    return {
        "feature_id": None,
        "feature_type": None,
        "drag_param": None,
        "drag_axis": None,
        "drag_scale": 1.0,
    }


def _analyse_face(face, face_idx, features, feature_shapes):
    """Analyse a single face and try to bind it to a feature + parameter."""
    binding = _empty_binding()

    try:
        face_type = _classify_face_type(face)
        normal = _get_face_normal(face)
        center = _get_face_center(face)
    except Exception:
        return binding

    # Work backwards from the last feature -- most faces belong to the last few features
    for feat in reversed(features):
        ft = feat.get("feature_type", "")
        params = feat.get("params", {})
        seq = feat["sequence"]

        # Try to match this face to this feature based on type + geometry
        match = _try_match_feature(face, face_type, normal, center, ft, params, seq, feature_shapes)
        if match:
            binding["feature_id"] = feat.get("id")
            binding["feature_type"] = ft
            binding["drag_param"] = match.get("drag_param")
            binding["drag_axis"] = match.get("drag_axis")
            binding["drag_scale"] = match.get("drag_scale", 1.0)
            break

    # If no specific match, assign to the last feature as a fallback
    if binding["feature_id"] is None and features:
        last = features[-1]
        binding["feature_id"] = last.get("id")
        binding["feature_type"] = last.get("feature_type")

    return binding


def _try_match_feature(face, face_type, normal, center, feat_type, params, seq, feature_shapes):
    """Try to match a face to a specific feature. Returns match dict or None."""

    # --- BOX ---
    if feat_type == "box":
        h = params.get("height", 20)
        w = params.get("width", 50)
        d = params.get("depth", 30)
        centered = params.get("centered", True)

        if face_type == "planar":
            # Top face (normal pointing up, at z = h or h/2)
            if abs(normal[2]) > 0.9:
                if normal[2] > 0:  # top face
                    return {"drag_param": "height", "drag_axis": [0, 0, 1], "drag_scale": 1.0}
                else:  # bottom face -- don't drag
                    return None
            # Front/back face
            if abs(normal[1]) > 0.9:
                return {"drag_param": "depth", "drag_axis": [0, normal[1], 0], "drag_scale": 1.0 if not centered else 2.0}
            # Left/right face
            if abs(normal[0]) > 0.9:
                return {"drag_param": "width", "drag_axis": [normal[0], 0, 0], "drag_scale": 1.0 if not centered else 2.0}
        return None

    # --- CYLINDER ---
    if feat_type == "cylinder":
        r = params.get("radius", 15)
        h = params.get("height", 40)

        if face_type == "cylindrical":
            # Side wall -- drag changes radius
            return {"drag_param": "radius", "drag_axis": _radial_axis(center), "drag_scale": 1.0}
        if face_type == "planar" and abs(normal[2]) > 0.9:
            if normal[2] > 0:
                return {"drag_param": "height", "drag_axis": [0, 0, 1], "drag_scale": 1.0}
        return None

    # --- SPHERE ---
    if feat_type == "sphere":
        if face_type == "spherical":
            return {"drag_param": "radius", "drag_axis": _radial_axis(center), "drag_scale": 1.0}
        return None

    # --- SKETCH_EXTRUDE ---
    if feat_type in ("sketch_extrude", "sketch_cut"):
        depth = params.get("depth", 20)
        plane = params.get("plane", "XY")

        if face_type == "planar":
            # Top/bottom face of extrusion
            extrude_normals = {"XY": [0, 0, 1], "XZ": [0, 1, 0], "YZ": [1, 0, 0]}
            en = extrude_normals.get(plane, [0, 0, 1])
            dot = sum(a * b for a, b in zip(normal, en))
            if abs(dot) > 0.9:
                if dot > 0:
                    return {"drag_param": "depth", "drag_axis": en, "drag_scale": 1.0}
        return None

    # --- FILLET ---
    if feat_type == "fillet":
        if face_type in ("cylindrical", "toroidal"):
            return {"drag_param": "radius", "drag_axis": _radial_axis(center), "drag_scale": 1.0}
        return None

    # --- CHAMFER ---
    if feat_type == "chamfer":
        if face_type == "planar":
            return {"drag_param": "distance", "drag_axis": list(normal), "drag_scale": 1.0}
        return None

    # --- HOLE ---
    if feat_type == "hole":
        if face_type == "cylindrical":
            return {"drag_param": "diameter", "drag_axis": _radial_axis(center), "drag_scale": 2.0}
        if face_type == "planar" and abs(normal[2]) > 0.5:
            return {"drag_param": "depth", "drag_axis": [0, 0, -1], "drag_scale": 1.0}
        return None

    # --- CONE ---
    if feat_type == "cone":
        if face_type == "conical":
            return {"drag_param": "radius1", "drag_axis": _radial_axis(center), "drag_scale": 1.0}
        if face_type == "planar" and abs(normal[2]) > 0.9:
            if normal[2] > 0:
                return {"drag_param": "height", "drag_axis": [0, 0, 1], "drag_scale": 1.0}
        return None

    # --- TORUS ---
    if feat_type == "torus":
        if face_type == "toroidal":
            return {"drag_param": "major_radius", "drag_axis": _radial_axis(center), "drag_scale": 1.0}
        return None

    # --- SHELL ---
    if feat_type == "shell":
        return {"drag_param": "thickness", "drag_axis": list(normal), "drag_scale": 1.0}

    return None


def _classify_face_type(face) -> str:
    """Classify a CadQuery face by its surface type."""
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import (
            GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone,
            GeomAbs_Sphere, GeomAbs_Torus, GeomAbs_BSplineSurface,
        )
        adaptor = BRepAdaptor_Surface(face.wrapped)
        stype = adaptor.GetType()
        return {
            GeomAbs_Plane: "planar",
            GeomAbs_Cylinder: "cylindrical",
            GeomAbs_Cone: "conical",
            GeomAbs_Sphere: "spherical",
            GeomAbs_Torus: "toroidal",
            GeomAbs_BSplineSurface: "bspline",
        }.get(stype, "other")
    except Exception:
        return "other"


def _get_face_normal(face) -> list:
    """Get the face's average normal as [x, y, z]."""
    try:
        n = face.normalAt()
        return [round(n.x, 6), round(n.y, 6), round(n.z, 6)]
    except Exception:
        return [0, 0, 1]


def _get_face_center(face) -> list:
    """Get the face's center point as [x, y, z]."""
    try:
        c = face.Center()
        return [round(c.x, 6), round(c.y, 6), round(c.z, 6)]
    except Exception:
        return [0, 0, 0]


def _radial_axis(center: list) -> list:
    """Compute a radial direction from origin to the face center (in XY plane)."""
    x, y = center[0], center[1]
    length = (x * x + y * y) ** 0.5
    if length < 1e-6:
        return [1, 0, 0]
    return [round(x / length, 6), round(y / length, 6), 0]
