"""
Parametric Feature Engine — Fusion 360-style feature tree executor.

Replays a sequence of structured features (Box, Cylinder, Fillet, etc.)
to build geometry using CadQuery, instead of raw Python scripts.
"""
from __future__ import annotations

import math
import json
import hashlib
from typing import Optional, Dict, List

import cadquery as cq
from cadquery import Compound


# ---------------------------------------------------------------------------
# Parameter Resolver — evaluates named-parameter expressions safely
# ---------------------------------------------------------------------------

class ParameterResolver:
    """Evaluates math expressions against a named variable context."""

    SAFE_MATH = {
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "atan2": math.atan2,
        "sqrt": math.sqrt, "pi": math.pi, "abs": abs,
        "min": min, "max": max,
        "floor": math.floor, "ceil": math.ceil,
        "radians": math.radians, "degrees": math.degrees,
        "pow": pow, "log": math.log, "log10": math.log10,
    }

    # Param keys that are always strings, never evaluated
    STRING_FIELDS = frozenset({
        "axis", "direction", "operation", "plane", "plane_type",
        "type", "selector", "face_selector", "mode",
    })

    def __init__(self, named_params: dict[str, float] | None = None):
        self.vars: dict[str, float] = dict(named_params or {})

    def evaluate(self, expression) -> float:
        """Safe eval of a math expression string with named params."""
        if isinstance(expression, (int, float)):
            return float(expression)
        if expression is None:
            return 0.0
        s = str(expression).strip()
        try:
            return float(
                eval(s, {"__builtins__": {}}, {**self.SAFE_MATH, **self.vars})
            )
        except Exception:
            return float(s)  # plain number

    def resolve_params(self, params: dict) -> dict:
        """Walk a params dict; evaluate numeric expressions, pass strings through."""
        result = {}
        for k, v in params.items():
            if k in self.STRING_FIELDS or k.endswith("_id") or k.endswith("_ids") or k.endswith("_refs"):
                result[k] = v
            elif isinstance(v, str):
                try:
                    result[k] = self.evaluate(v)
                except Exception:
                    result[k] = v
            elif isinstance(v, list):
                result[k] = v
            elif isinstance(v, dict):
                result[k] = v
            elif isinstance(v, bool):
                result[k] = v
            else:
                result[k] = v
        return result


# ---------------------------------------------------------------------------
# Feature Executor — replays feature list to build geometry
# ---------------------------------------------------------------------------

class FeatureExecutor:
    """Replays an ordered feature tree to build a CadQuery shape."""

    def __init__(self, resolver: ParameterResolver):
        self.resolver = resolver
        self._handlers = {
            "box": self._box,
            "cylinder": self._cylinder,
            "sphere": self._sphere,
            "cone": self._cone,
            "torus": self._torus,
            "wedge": self._wedge,
            "hemisphere": self._hemisphere,
            "capsule": self._capsule,
            "pipe": self._pipe,
            "slot": self._slot,
            "sweep": self._sweep,
            "loft": self._loft,
            "revolve": self._revolve,
            "fillet": self._fillet,
            "chamfer": self._chamfer,
            "shell": self._shell,
            "hole": self._hole,
            "translate": self._translate,
            "rotate": self._rotate,
            "scale": self._scale,
            "linear_pattern": self._linear_pattern,
            "circular_pattern": self._circular_pattern,
            "mirror": self._mirror,
            "union": self._union,
            "cut": self._cut,
            "draft": self._draft,
            "extrude_face": self._extrude_face,
            "offset_face": self._offset_face,
            "delete_face": self._delete_face,
            "sketch_extrude": self._sketch_extrude,
            "sketch_revolve": self._sketch_revolve,
            "sketch_cut": self._sketch_cut,
        }

    def build(self, features: list[dict], up_to_seq: int | None = None) -> dict:
        """
        Replay features in sequence order.

        Returns dict with:
            shape: cq.Workplane | None
            last_good_sequence: int
            feature_errors: {seq: error_msg}
            rebuild_status: 'ok' | 'partial' | 'failed'
        """
        active = [f for f in features if not f.get("suppressed", False)]
        active.sort(key=lambda f: f["sequence"])
        if up_to_seq is not None:
            active = [f for f in active if f["sequence"] <= up_to_seq]

        wp: Optional[cq.Workplane] = None
        last_good = 0
        errors: dict[int, str] = {}
        shape_cache: dict[int, cq.Workplane] = {}

        for feat in active:
            resolved = self.resolver.resolve_params(feat["params"])
            # Validate params against schema constraints
            try:
                from feature_models import validate_feature_params
                resolved = validate_feature_params(feat["feature_type"], resolved, FEATURE_SCHEMAS)
            except (ValueError, ImportError) as ve:
                errors[feat["sequence"]] = f"Param validation: {ve}"
                continue
            handler = self._handlers.get(feat["feature_type"])
            if not handler:
                errors[feat["sequence"]] = f"Unknown feature type: {feat['feature_type']}"
                continue
            try:
                wp = handler(wp, resolved)
                last_good = feat["sequence"]
                shape_cache[feat["sequence"]] = wp
            except Exception as e:
                errors[feat["sequence"]] = str(e)

        status = "ok" if not errors else ("partial" if wp is not None else "failed")
        return {
            "shape": wp,
            "shape_cache": shape_cache,
            "last_good_sequence": last_good,
            "feature_errors": errors,
            "rebuild_status": status,
        }

    # --- Primitive creators (wp can be None) ---

    def _position_primitive(self, new_wp, p):
        """Apply optional rotation [rx, ry, rz] then position offset [x, y, z] to a primitive before combining."""
        # 1. Rotation (applied FIRST, around origin, before translation)
        rot = p.get("rotation")
        if rot and isinstance(rot, (list, tuple)) and len(rot) >= 3:
            rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
            if rx != 0:
                new_wp = new_wp.rotate((0, 0, 0), (1, 0, 0), rx)
            if ry != 0:
                new_wp = new_wp.rotate((0, 0, 0), (0, 1, 0), ry)
            if rz != 0:
                new_wp = new_wp.rotate((0, 0, 0), (0, 0, 1), rz)
        # 2. Translation
        pos = p.get("position")
        if pos and isinstance(pos, (list, tuple)) and len(pos) >= 3:
            x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
            if x != 0 or y != 0 or z != 0:
                new_wp = new_wp.translate((x, y, z))
        return new_wp

    def _box(self, wp, p):
        w = p.get("width", 50)
        d = p.get("depth", 30)
        h = p.get("height", 20)
        centered = p.get("centered", True)
        new_wp = cq.Workplane("XY").box(w, d, h, centered=(centered, centered, centered))
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _cylinder(self, wp, p):
        r = p.get("radius", 15)
        h = p.get("height", 40)
        axis = p.get("axis", "Z").upper()
        if axis == "X":
            new_wp = cq.Workplane("YZ").cylinder(h, r)
        elif axis == "Y":
            new_wp = cq.Workplane("XZ").cylinder(h, r)
        else:
            new_wp = cq.Workplane("XY").cylinder(h, r)
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _sphere(self, wp, p):
        r = p.get("radius", 20)
        new_wp = cq.Workplane("XY").sphere(r)
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _cone(self, wp, p):
        r1 = p.get("radius1", 20)
        r2 = p.get("radius2", 0)
        h = p.get("height", 40)
        solid = cq.Solid.makeCone(r1, r2, h)
        new_wp = cq.Workplane("XY").newObject([solid])
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _torus(self, wp, p):
        major = p.get("major_radius", 50)
        minor = p.get("minor_radius", 10)
        axis = p.get("axis", "Z").upper()
        solid = cq.Solid.makeTorus(major, minor)
        new_wp = cq.Workplane("XY").newObject([solid])
        # Rotate torus so it stands in the correct plane
        if axis == "X":
            # Wheel stands upright, axle along X (left-right)
            new_wp = new_wp.rotate((0, 0, 0), (0, 1, 0), 90)
        elif axis == "Y":
            # Wheel stands upright, axle along Y (front-back) — bicycles, cars
            new_wp = new_wp.rotate((0, 0, 0), (1, 0, 0), 90)
        # axis == "Z" → default flat (donut lying on ground)
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    # --- Advanced primitives ---

    def _wedge(self, wp, p):
        """Tapered box — useful for ramps, tapers, angled surfaces."""
        dx = p.get("width", 50)
        dy = p.get("depth", 30)
        dz = p.get("height", 20)
        # Top face dimensions (narrower than base)
        top_width = p.get("top_width", dx * 0.5)
        top_depth = p.get("top_depth", dy)
        # Calculate min/max for top face centering
        xmin = (dx - top_width) / 2
        xmax = xmin + top_width
        zmin = (dy - top_depth) / 2
        zmax = zmin + top_depth
        solid = cq.Solid.makeWedge(dx, dz, dy, xmin, zmin, xmax, zmax)
        new_wp = cq.Workplane("XY").newObject([solid])
        # Center if requested
        if p.get("centered", True):
            new_wp = new_wp.translate((-dx / 2, -dy / 2, -dz / 2))
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _hemisphere(self, wp, p):
        """Half sphere — dome shape. Plane selects which half: top (default), bottom."""
        r = p.get("radius", 30)
        half = p.get("half", "top")  # "top" or "bottom"
        sphere_wp = cq.Workplane("XY").sphere(r)
        # Cut away one half
        cut_box = cq.Workplane("XY").box(r * 3, r * 3, r, centered=(True, True, False))
        if half == "top":
            cut_box = cut_box.translate((0, 0, -r))
        # else bottom: cut_box stays at z=0..r
        solid_a = sphere_wp.val()
        solid_b = cut_box.val()
        result_shape = solid_a.cut(solid_b)
        new_wp = cq.Workplane("XY").newObject([result_shape])
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _capsule(self, wp, p):
        """Capsule/stadium solid — cylinder with hemisphere caps. Good for handles, pills."""
        r = p.get("radius", 10)
        length = p.get("length", 40)  # total length including caps
        cyl_h = max(length - 2 * r, 0.01)
        # Build: hemisphere bottom + cylinder + hemisphere top
        result = cq.Workplane("XY").cylinder(cyl_h, r)
        # Add top hemisphere
        top_sphere = cq.Workplane("XY").sphere(r).translate((0, 0, cyl_h / 2))
        result = cq.Workplane("XY").newObject([result.val().fuse(top_sphere.val())])
        # Add bottom hemisphere
        bot_sphere = cq.Workplane("XY").sphere(r).translate((0, 0, -cyl_h / 2))
        result = cq.Workplane("XY").newObject([result.val().fuse(bot_sphere.val())])
        new_wp = result
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _pipe(self, wp, p):
        """Hollow tube/pipe — cylinder with through hole. Outer radius and wall thickness."""
        outer_r = p.get("outer_radius", 20)
        wall = p.get("wall_thickness", 3)
        h = p.get("height", 40)
        inner_r = outer_r - wall
        if inner_r <= 0:
            inner_r = outer_r * 0.5
        outer = cq.Workplane("XY").cylinder(h, outer_r)
        inner = cq.Workplane("XY").cylinder(h + 1, inner_r)
        result = cq.Workplane("XY").newObject([outer.val().cut(inner.val())])
        new_wp = result
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _slot(self, wp, p):
        """Elongated slot/rounded rectangle solid — like two semicircles + rectangle. Good for keyways, slots."""
        length = p.get("length", 30)
        width = p.get("width", 10)
        height = p.get("height", 5)
        r = width / 2
        slot_wp = (
            cq.Workplane("XY")
            .slot2D(length, width)
            .extrude(height)
        )
        if p.get("centered", True):
            slot_wp = slot_wp.translate((0, 0, -height / 2))
        new_wp = slot_wp
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _sweep(self, wp, p):
        """Sweep a circle along a straight angled path — creates curved tubes, rails."""
        radius = p.get("radius", 5)
        path_points = p.get("path_points", [[0, 0, 0], [0, 0, 50]])
        if len(path_points) < 2:
            raise ValueError("Sweep needs at least 2 path points")
        # Build the path as a spline
        pts = [cq.Vector(*pt) for pt in path_points]
        path_wp = cq.Workplane("XY").spline(pts)
        # Create a circle profile at the start
        profile = cq.Workplane("XY").circle(radius)
        result = profile.sweep(path_wp)
        new_wp = result
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _loft(self, wp, p):
        """Loft between two circles at different heights — transition pieces, nozzles."""
        bottom_radius = p.get("bottom_radius", 20)
        top_radius = p.get("top_radius", 10)
        height = p.get("height", 40)
        bottom_shape = p.get("bottom_shape", "circle")  # "circle" or "rect"
        top_shape = p.get("top_shape", "circle")
        # Build bottom wire
        bottom_wp = cq.Workplane("XY")
        if bottom_shape == "rect":
            bottom_wp = bottom_wp.rect(bottom_radius * 2, bottom_radius * 2)
        else:
            bottom_wp = bottom_wp.circle(bottom_radius)
        # Build top wire
        top_wp = cq.Workplane("XY").workplane(offset=height)
        if top_shape == "rect":
            top_wp = top_wp.rect(top_radius * 2, top_radius * 2)
        else:
            top_wp = top_wp.circle(top_radius)
        result = bottom_wp.add(top_wp).loft()
        new_wp = result
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    def _revolve(self, wp, p):
        """Revolve a rectangle profile around Y axis — bowls, bottles, turned parts."""
        profile_width = p.get("profile_width", 5)  # radial thickness
        profile_height = p.get("profile_height", 20)  # height of profile
        inner_radius = p.get("inner_radius", 0)  # distance from axis
        angle = p.get("angle", 360)
        # Create a rectangular profile to revolve
        x_start = inner_radius
        x_end = inner_radius + profile_width
        x_center = (x_start + x_end) / 2
        # Build a 2D rectangle profile and revolve around Z axis
        result = (
            cq.Workplane("XZ")
            .center(x_center, profile_height / 2)
            .rect(profile_width, profile_height)
            .revolve(angle, (0, 0, 0), (0, 0, 1))
        )
        new_wp = result
        new_wp = self._position_primitive(new_wp, p)
        return self._combine(wp, new_wp, p.get("operation", "boss"))

    # --- Modification features (wp must exist) ---

    def _require_shape(self, wp, name):
        if wp is None:
            raise ValueError(f"{name} requires existing geometry — add a primitive first")
        return wp

    def _fillet(self, wp, p):
        wp = self._require_shape(wp, "Fillet")
        radius = p.get("radius", 3)
        edge_refs = p.get("edge_refs", [])
        if edge_refs:
            selector = self._build_edge_selector(edge_refs)
            return wp.edges(selector).fillet(radius)
        return wp.edges().fillet(radius)

    def _chamfer(self, wp, p):
        wp = self._require_shape(wp, "Chamfer")
        dist = p.get("distance", 2)
        edge_refs = p.get("edge_refs", [])
        if edge_refs:
            selector = self._build_edge_selector(edge_refs)
            return wp.edges(selector).chamfer(dist)
        return wp.edges().chamfer(dist)

    def _shell(self, wp, p):
        wp = self._require_shape(wp, "Shell")
        thickness = p.get("thickness", 2)
        faces = p.get("faces_to_remove", [])
        if faces and isinstance(faces, list) and len(faces) > 0:
            selector = faces[0] if isinstance(faces[0], str) else ">Z"
            return wp.faces(selector).shell(-thickness)
        return wp.shell(-thickness)

    def _hole(self, wp, p):
        wp = self._require_shape(wp, "Hole")
        diameter = p.get("diameter", 10)
        depth = p.get("depth")
        face_sel = p.get("face_selector", ">Z")
        click_point = p.get("click_point")

        face_wp = wp.faces(face_sel).workplane()

        # If user clicked a specific point, offset the workplane to that position
        if click_point and isinstance(click_point, (list, tuple)) and len(click_point) >= 3:
            # Transform world-space click point to face workplane local coordinates
            plane = face_wp.plane
            local = plane.toLocalCoords(cq.Vector(*click_point))
            face_wp = face_wp.center(local.x, local.y)

        if depth:
            return face_wp.hole(diameter, depth)
        return face_wp.hole(diameter)

    def _draft(self, wp, p):
        """Add draft angle to faces using OCC BRepOffsetAPI_DraftAngle."""
        wp = self._require_shape(wp, "Draft")
        angle_deg = p.get("angle", 5)
        pull_dir = p.get("pull_direction", "Z")
        face_sel = p.get("face_selector", ">Z")
        dir_map = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1),
                   "-X": (-1, 0, 0), "-Y": (0, -1, 0), "-Z": (0, 0, -1)}
        pull = dir_map.get(pull_dir.upper(), (0, 0, 1))

        from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        import math as _math

        shape = wp.val()
        drafter = BRepOffsetAPI_DraftAngle(shape.wrapped)
        direction = gp_Dir(*pull)
        angle_rad = _math.radians(angle_deg)
        neutral_plane = gp_Pln(gp_Pnt(0, 0, 0), direction)

        selected = wp.faces(face_sel).vals()
        if not selected:
            raise ValueError(f"No faces matching '{face_sel}'")
        for face in selected:
            drafter.Add(face.wrapped, direction, angle_rad, neutral_plane)

        drafter.Build()
        if not drafter.IsDone():
            raise ValueError("Draft angle operation failed")
        return cq.Workplane("XY").newObject([cq.Shape.cast(drafter.Shape())])

    def _extrude_face(self, wp, p):
        """Extrude (push/pull) a face along its normal using OCC prism + boolean."""
        wp = self._require_shape(wp, "Extrude Face")
        depth = p.get("depth", 10)
        face_sel = p.get("face_selector", ">Z")

        shape = wp.val()
        selected = wp.faces(face_sel).vals()
        if not selected:
            raise ValueError(f"No faces matching '{face_sel}'")
        target_face = selected[0]
        normal = target_face.normalAt()

        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut
        from OCP.gp import gp_Vec
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        direction = gp_Vec(normal.x * depth, normal.y * depth, normal.z * depth)
        prism = BRepPrimAPI_MakePrism(target_face.wrapped, direction)
        prism.Build()
        if not prism.IsDone():
            raise ValueError("Failed to create face extrusion")

        if depth > 0:
            op = BRepAlgoAPI_Fuse(shape.wrapped, prism.Shape())
        else:
            op = BRepAlgoAPI_Cut(shape.wrapped, prism.Shape())
        op.Build()
        if not op.IsDone():
            raise ValueError("Boolean operation failed")

        # Merge coplanar faces
        unifier = ShapeUpgrade_UnifySameDomain(op.Shape(), True, True, True)
        unifier.Build()
        return cq.Workplane("XY").newObject([cq.Shape.cast(unifier.Shape())])

    def _offset_face(self, wp, p):
        """Offset a face (grow/shrink) by a distance."""
        wp = self._require_shape(wp, "Offset Face")
        distance = p.get("distance", 2)
        face_sel = p.get("face_selector", ">Z")
        # Use shell with single face offset
        return wp.shell(distance)

    def _delete_face(self, wp, p):
        """Delete a face from the solid."""
        wp = self._require_shape(wp, "Delete Face")
        face_sel = p.get("face_selector", ">Z")
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape
        shape = wp.val()
        # Find the face matching the selector
        selected_wp = wp.faces(face_sel)
        face_vals = selected_wp.vals()
        if not face_vals:
            raise ValueError(f"No face matching selector '{face_sel}'")
        faces_to_remove = TopTools_ListOfShape()
        for fv in face_vals:
            faces_to_remove.Append(fv.wrapped)
        defeaturer = BRepAlgoAPI_Defeaturing()
        defeaturer.SetShape(shape.wrapped)
        defeaturer.AddFacesToRemove(faces_to_remove)
        defeaturer.Build()
        if not defeaturer.IsDone():
            raise ValueError("Defeaturing failed — face may not be removable")
        return cq.Workplane("XY").newObject([cq.Shape.cast(defeaturer.Shape())])

    def _translate(self, wp, p):
        wp = self._require_shape(wp, "Move")
        x = p.get("x", 0)
        y = p.get("y", 0)
        z = p.get("z", 0)
        return wp.translate((x, y, z))

    def _rotate(self, wp, p):
        wp = self._require_shape(wp, "Rotate")
        axis_str = p.get("axis", "Z")
        angle = p.get("angle", 90)
        axis_map = {
            "X": (0, 0, 0, 1, 0, 0),
            "Y": (0, 0, 0, 0, 1, 0),
            "Z": (0, 0, 0, 0, 0, 1),
        }
        ax = axis_map.get(axis_str.upper(), (0, 0, 0, 0, 0, 1))
        return wp.rotate(ax[:3], ax[3:], angle)

    def _scale(self, wp, p):
        """Non-uniform scale — creates elongated/squashed shapes from spheres/cylinders."""
        wp = self._require_shape(wp, "Scale")
        sx = p.get("x", 1.0)
        sy = p.get("y", 1.0)
        sz = p.get("z", 1.0)
        import OCP.gp as gp
        import OCP.BRepBuilderAPI as BRepBuilderAPI
        # Build a non-uniform scale transform
        trsf = gp.gp_GTrsf()
        mat = gp.gp_Mat(sx, 0, 0, 0, sy, 0, 0, 0, sz)
        trsf.SetVectorialPart(mat)
        transformer = BRepBuilderAPI.BRepBuilderAPI_GTransform(wp.val().wrapped, trsf, True)
        scaled_shape = transformer.Shape()
        return cq.Workplane("XY").newObject([cq.Shape(scaled_shape)])

    # --- Pattern features ---

    def _linear_pattern(self, wp, p):
        wp = self._require_shape(wp, "Linear Pattern")
        direction = p.get("direction", [1, 0, 0])
        count = int(p.get("count", 3))
        spacing = p.get("spacing", 20)

        shapes = wp.vals()
        if not shapes:
            return wp
        base_solid = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)

        result = base_solid
        dx, dy, dz = direction[0], direction[1], direction[2]
        for i in range(1, count):
            offset = (dx * spacing * i, dy * spacing * i, dz * spacing * i)
            moved = base_solid.moved(cq.Location(cq.Vector(*offset)))
            result = result.fuse(moved)
        return cq.Workplane("XY").newObject([result])

    def _circular_pattern(self, wp, p):
        wp = self._require_shape(wp, "Circular Pattern")
        axis_raw = p.get("axis", [0, 0, 1])
        _axis_map = {"X": [1,0,0], "Y": [0,1,0], "Z": [0,0,1]}
        axis = _axis_map.get(axis_raw, axis_raw) if isinstance(axis_raw, str) else axis_raw
        count = int(p.get("count", 6))
        total_angle = p.get("angle", 360)

        shapes = wp.vals()
        if not shapes:
            return wp
        base_solid = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)

        result = base_solid
        angle_step = total_angle / count
        ax_vec = cq.Vector(*axis)
        center = cq.Vector(0, 0, 0)
        for i in range(1, count):
            angle = angle_step * i
            rotated = base_solid.rotate(center, ax_vec, angle)
            result = result.fuse(rotated)
        return cq.Workplane("XY").newObject([result])

    def _mirror(self, wp, p):
        wp = self._require_shape(wp, "Mirror")
        plane = p.get("plane", "XZ")
        shapes = wp.vals()
        if not shapes:
            return wp
        base_solid = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)

        plane_map = {
            "XY": "XY",
            "XZ": "XZ",
            "YZ": "YZ",
        }
        mirror_plane = plane_map.get(plane, "XZ")
        mirrored = base_solid.mirror(mirror_plane)
        fused = base_solid.fuse(mirrored)
        return cq.Workplane("XY").newObject([fused])

    # --- Boolean operations ---

    def _union(self, wp, p):
        """Union: add a primitive to the existing shape."""
        wp = self._require_shape(wp, "Union")
        prim_type = p.get("type", "box")
        prim_handler = self._handlers.get(prim_type)
        if not prim_handler:
            raise ValueError(f"Unknown primitive type for union: {prim_type}")
        new_wp = prim_handler(None, p)
        return self._boolean(wp, new_wp, "fuse")

    def _cut(self, wp, p):
        """Cut: subtract a primitive from the existing shape."""
        wp = self._require_shape(wp, "Cut")
        prim_type = p.get("type", "box")
        prim_handler = self._handlers.get(prim_type)
        if not prim_handler:
            raise ValueError(f"Unknown primitive type for cut: {prim_type}")
        new_wp = prim_handler(None, p)
        return self._boolean(wp, new_wp, "cut")

    # --- Internal helpers ---

    def _combine(self, existing_wp, new_wp, operation="boss"):
        """Combine new geometry with existing: boss = union, cut = subtract."""
        if existing_wp is None:
            return new_wp
        if operation == "cut":
            return self._boolean(existing_wp, new_wp, "cut")
        return self._boolean(existing_wp, new_wp, "fuse")

    def _boolean(self, wp_a, wp_b, op):
        shapes_a = wp_a.vals()
        shapes_b = wp_b.vals()
        if not shapes_a or not shapes_b:
            return wp_a
        solid_a = shapes_a[0] if len(shapes_a) == 1 else Compound.makeCompound(shapes_a)
        solid_b = shapes_b[0] if len(shapes_b) == 1 else Compound.makeCompound(shapes_b)
        if op == "fuse":
            result = solid_a.fuse(solid_b)
        elif op == "cut":
            result = solid_a.cut(solid_b)
        else:
            result = solid_a.fuse(solid_b)
        return cq.Workplane("XY").newObject([result])

    def _sketch_extrude(self, wp, p):
        """Extrude a stored sketch profile."""
        from sketch_executor import sketch_extrude
        sketch_id = p.get("sketch_id")
        if not sketch_id:
            raise ValueError("sketch_extrude requires sketch_id param")
        # Load sketch entities from DB
        entities = self._load_sketch_entities(sketch_id)
        depth = p.get("depth", 20)
        plane = p.get("plane", "XY")
        operation = p.get("operation", "boss")
        symmetric = p.get("symmetric", False)
        return sketch_extrude(entities, depth, plane, operation, wp, symmetric)

    def _sketch_revolve(self, wp, p):
        """Revolve a stored sketch profile."""
        from sketch_executor import sketch_revolve
        sketch_id = p.get("sketch_id")
        if not sketch_id:
            raise ValueError("sketch_revolve requires sketch_id param")
        entities = self._load_sketch_entities(sketch_id)
        angle = p.get("angle", 360)
        axis = p.get("axis", "Y")
        plane = p.get("plane", "XZ")
        operation = p.get("operation", "boss")
        return sketch_revolve(entities, angle, axis, plane, operation, wp)

    def _sketch_cut(self, wp, p):
        """Cut using a sketch profile (extrude-cut)."""
        p2 = {**p, "operation": "cut"}
        return self._sketch_extrude(wp, p2)

    def _load_sketch_entities(self, sketch_id: int) -> list:
        """Load sketch entities from DB."""
        from db import get_connection, put_connection
        import json
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT entities FROM sketches WHERE id = %s", (sketch_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Sketch {sketch_id} not found")
                ents = row[0]
                return ents if isinstance(ents, list) else json.loads(ents)
        finally:
            put_connection(conn)

    def _build_edge_selector(self, edge_refs):
        """Convert edge_refs list to a CadQuery selector string."""
        if isinstance(edge_refs, str):
            return edge_refs
        if isinstance(edge_refs, list) and len(edge_refs) > 0:
            first = edge_refs[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict) and "value" in first:
                return first["value"]
        return None


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def compute_feature_hash(features: list[dict]) -> str:
    """Compute a deterministic hash of the entire feature chain."""
    content = json.dumps(
        [
            {
                "type": f["feature_type"],
                "params": f["params"],
                "seq": f["sequence"],
                "suppressed": f.get("suppressed", False),
            }
            for f in sorted(features, key=lambda x: x["sequence"])
        ],
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# High-level rebuild function
# ---------------------------------------------------------------------------

def rebuild_part_from_features(
    features: list[dict],
    named_params: dict | None = None,
    up_to_seq: int | None = None,
) -> dict:
    """
    High-level: takes feature list + optional named params,
    returns {shape, mesh, bbox, rebuild_status, feature_errors, last_good_sequence}
    """
    from executor import shape_to_topo_mesh, extract_bounding_box

    resolver = ParameterResolver(named_params)
    executor = FeatureExecutor(resolver)
    result = executor.build(features, up_to_seq)

    if result["shape"] is not None:
        mesh = shape_to_topo_mesh(result["shape"])
        bbox = extract_bounding_box(result["shape"])

        # Enrich topo_faces with parametric bindings
        from face_param_mapper import map_faces_to_features
        face_bindings = map_faces_to_features(features, result.get("shape_cache", {}), result["shape"])
        if mesh.get("topo_faces") and face_bindings:
            for i, tf in enumerate(mesh["topo_faces"]):
                if i < len(face_bindings):
                    tf.update(face_bindings[i])

        result["mesh"] = mesh
        result["bbox"] = bbox
    else:
        result["mesh"] = None
        result["bbox"] = None

    return result


# ---------------------------------------------------------------------------
# Feature type catalog — schemas for frontend UI
# ---------------------------------------------------------------------------

FEATURE_SCHEMAS = {
    "box": {
        "label": "Box",
        "icon": "\u25ad",
        "category": "primitive",
        "params": {
            "width":     {"type": "float", "default": 50,   "min": 0.01, "unit": "mm", "label": "Width"},
            "depth":     {"type": "float", "default": 30,   "min": 0.01, "unit": "mm", "label": "Depth"},
            "height":    {"type": "float", "default": 20,   "min": 0.01, "unit": "mm", "label": "Height"},
            "centered":  {"type": "bool",  "default": True, "label": "Centered"},
            "position":  {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]", "description": "Offset the primitive before combining"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°", "description": "Rotate around X,Y,Z axes in degrees (applied before position)"},
            "operation": {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation", "description": "boss=union, cut=subtract"},
        },
    },
    "cylinder": {
        "label": "Cylinder",
        "icon": "\u25cb",
        "category": "primitive",
        "params": {
            "radius":    {"type": "float", "default": 15, "min": 0.01, "unit": "mm", "label": "Radius"},
            "height":    {"type": "float", "default": 40, "min": 0.01, "unit": "mm", "label": "Height"},
            "axis":      {"type": "enum",  "default": "Z", "options": ["X", "Y", "Z"], "label": "Axis", "description": "Cylinder axis direction (Z=vertical, X=left-right, Y=front-back)"},
            "centered":  {"type": "bool",  "default": True, "label": "Centered"},
            "position":  {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation": {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "sphere": {
        "label": "Sphere",
        "icon": "\u25c9",
        "category": "primitive",
        "params": {
            "radius":    {"type": "float", "default": 20, "min": 0.01, "unit": "mm", "label": "Radius"},
            "position":  {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation": {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "cone": {
        "label": "Cone",
        "icon": "\u25b3",
        "category": "primitive",
        "params": {
            "radius1":   {"type": "float", "default": 20, "min": 0,    "unit": "mm", "label": "Base Radius"},
            "radius2":   {"type": "float", "default": 0,  "min": 0,    "unit": "mm", "label": "Top Radius"},
            "height":    {"type": "float", "default": 40, "min": 0.01, "unit": "mm", "label": "Height"},
            "position":  {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation": {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "torus": {
        "label": "Torus",
        "icon": "\u25ce",
        "category": "primitive",
        "params": {
            "major_radius": {"type": "float", "default": 50, "min": 0.01, "unit": "mm", "label": "Major Radius"},
            "minor_radius": {"type": "float", "default": 10, "min": 0.01, "unit": "mm", "label": "Minor Radius"},
            "axis":         {"type": "enum",  "default": "Z", "options": ["X", "Y", "Z"], "label": "Axis", "description": "Wheel plane: Z=flat/horizontal, X=upright axle-left-right, Y=upright axle-front-back"},
            "position":     {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":     {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation":    {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "wedge": {
        "label": "Wedge",
        "icon": "\u25e3",
        "category": "primitive",
        "params": {
            "width":      {"type": "float", "default": 50,   "min": 0.01, "unit": "mm", "label": "Width (base)"},
            "depth":      {"type": "float", "default": 30,   "min": 0.01, "unit": "mm", "label": "Depth"},
            "height":     {"type": "float", "default": 20,   "min": 0.01, "unit": "mm", "label": "Height"},
            "top_width":  {"type": "float", "default": 25,   "min": 0.01, "unit": "mm", "label": "Top Width"},
            "top_depth":  {"type": "float", "default": 30,   "min": 0.01, "unit": "mm", "label": "Top Depth"},
            "centered":   {"type": "bool",  "default": True, "label": "Centered"},
            "position":   {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation":  {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "hemisphere": {
        "label": "Hemisphere",
        "icon": "\u25e0",
        "category": "primitive",
        "params": {
            "radius":    {"type": "float",  "default": 30,    "min": 0.01, "unit": "mm", "label": "Radius"},
            "half":      {"type": "enum",   "default": "top",  "options": ["top", "bottom"], "label": "Which Half"},
            "position":  {"type": "vec3",   "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation": {"type": "enum",   "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "capsule": {
        "label": "Capsule",
        "icon": "\u2b2d",
        "category": "primitive",
        "params": {
            "radius":    {"type": "float", "default": 10, "min": 0.01, "unit": "mm", "label": "Radius"},
            "length":    {"type": "float", "default": 40, "min": 0.01, "unit": "mm", "label": "Total Length"},
            "position":  {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation": {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "pipe": {
        "label": "Pipe/Tube",
        "icon": "\u25ef",
        "category": "primitive",
        "params": {
            "outer_radius":   {"type": "float", "default": 20, "min": 0.01, "unit": "mm", "label": "Outer Radius"},
            "wall_thickness": {"type": "float", "default": 3,  "min": 0.1,  "unit": "mm", "label": "Wall Thickness"},
            "height":         {"type": "float", "default": 40, "min": 0.01, "unit": "mm", "label": "Height"},
            "position":       {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation":      {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "slot": {
        "label": "Slot",
        "icon": "\u2b2c",
        "category": "primitive",
        "params": {
            "length":    {"type": "float", "default": 30, "min": 0.01, "unit": "mm", "label": "Length"},
            "width":     {"type": "float", "default": 10, "min": 0.01, "unit": "mm", "label": "Width"},
            "height":    {"type": "float", "default": 5,  "min": 0.01, "unit": "mm", "label": "Height"},
            "centered":  {"type": "bool",  "default": True, "label": "Centered"},
            "position":  {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation": {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "sweep": {
        "label": "Sweep",
        "icon": "\u21dd",
        "category": "primitive",
        "params": {
            "radius":      {"type": "float", "default": 5,  "min": 0.01, "unit": "mm", "label": "Profile Radius"},
            "path_points": {"type": "json",  "default": [[0,0,0],[0,0,50]], "label": "Path Points [[x,y,z],...]"},
            "position":    {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation":   {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "loft": {
        "label": "Loft",
        "icon": "\u25b5",
        "category": "primitive",
        "params": {
            "bottom_radius": {"type": "float", "default": 20, "min": 0.01, "unit": "mm", "label": "Bottom Radius"},
            "top_radius":    {"type": "float", "default": 10, "min": 0.01, "unit": "mm", "label": "Top Radius"},
            "height":        {"type": "float", "default": 40, "min": 0.01, "unit": "mm", "label": "Height"},
            "bottom_shape":  {"type": "enum",  "default": "circle", "options": ["circle", "rect"], "label": "Bottom Shape"},
            "top_shape":     {"type": "enum",  "default": "circle", "options": ["circle", "rect"], "label": "Top Shape"},
            "position":      {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation":     {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "revolve": {
        "label": "Revolve",
        "icon": "\u21ba",
        "category": "primitive",
        "params": {
            "profile_width":  {"type": "float", "default": 5,   "min": 0.01, "unit": "mm", "label": "Profile Width"},
            "profile_height": {"type": "float", "default": 20,  "min": 0.01, "unit": "mm", "label": "Profile Height"},
            "inner_radius":   {"type": "float", "default": 0,   "min": 0,    "unit": "mm", "label": "Inner Radius"},
            "angle":          {"type": "float", "default": 360, "min": 1, "max": 360, "unit": "°", "label": "Angle"},
            "position":       {"type": "vec3",  "default": [0,0,0], "label": "Position [x,y,z]"},
            "rotation":  {"type": "vec3",  "default": [0,0,0], "label": "Rotation [rx,ry,rz]°"},
            "operation":      {"type": "enum",  "default": "boss", "options": ["boss", "cut"], "label": "Operation"},
        },
    },
    "fillet": {
        "label": "Fillet",
        "icon": "\u2312",
        "category": "modify",
        "params": {
            "radius":    {"type": "float",         "default": 3, "min": 0.01, "unit": "mm", "label": "Radius"},
            "edge_refs": {"type": "edge_selector",  "default": [], "label": "Edges"},
        },
    },
    "chamfer": {
        "label": "Chamfer",
        "icon": "\u22bf",
        "category": "modify",
        "params": {
            "distance":  {"type": "float",         "default": 2, "min": 0.01, "unit": "mm", "label": "Distance"},
            "edge_refs": {"type": "edge_selector",  "default": [], "label": "Edges"},
        },
    },
    "shell": {
        "label": "Shell",
        "icon": "\u25fb",
        "category": "modify",
        "params": {
            "thickness":       {"type": "float",         "default": 2,  "min": 0.01, "unit": "mm", "label": "Thickness"},
            "faces_to_remove": {"type": "face_selector",  "default": [], "label": "Faces to Remove"},
        },
    },
    "hole": {
        "label": "Hole",
        "icon": "\u2299",
        "category": "modify",
        "params": {
            "diameter":      {"type": "float",               "default": 10, "min": 0.01, "unit": "mm", "label": "Diameter"},
            "depth":         {"type": "float",               "default": None, "unit": "mm", "label": "Depth (blank=through)"},
            "face_selector": {"type": "face_selector_string", "default": ">Z", "label": "Face"},
        },
    },
    "draft": {
        "label": "Draft",
        "icon": "\u22bf",
        "category": "modify",
        "params": {
            "angle":          {"type": "float",  "default": 5,   "min": 0.1, "max": 45, "unit": "\u00b0", "label": "Draft Angle"},
            "pull_direction": {"type": "select", "options": ["X", "Y", "Z", "-X", "-Y", "-Z"], "default": "Z", "label": "Pull Direction"},
            "face_selector":  {"type": "face_selector_string", "default": ">Z", "label": "Face"},
        },
    },
    "extrude_face": {
        "label": "Extrude Face",
        "icon": "\u2b06",
        "category": "modify",
        "params": {
            "depth":          {"type": "float", "default": 10, "unit": "mm", "label": "Depth (negative = cut)"},
            "face_selector":  {"type": "face_selector_string", "default": ">Z", "label": "Face"},
        },
    },
    "offset_face": {
        "label": "Offset Face",
        "icon": "\u21d5",
        "category": "modify",
        "params": {
            "distance":       {"type": "float", "default": 2, "unit": "mm", "label": "Offset Distance"},
            "face_selector":  {"type": "face_selector_string", "default": ">Z", "label": "Face"},
        },
    },
    "delete_face": {
        "label": "Delete Face",
        "icon": "\u2297",
        "category": "modify",
        "params": {
            "face_selector":  {"type": "face_selector_string", "default": ">Z", "label": "Face"},
        },
    },
    "translate": {
        "label": "Move",
        "icon": "\u2197",
        "category": "modify",
        "params": {
            "x": {"type": "float", "default": 0, "unit": "mm", "label": "X"},
            "y": {"type": "float", "default": 0, "unit": "mm", "label": "Y"},
            "z": {"type": "float", "default": 0, "unit": "mm", "label": "Z"},
        },
    },
    "rotate": {
        "label": "Rotate",
        "icon": "\u21bb",
        "category": "modify",
        "params": {
            "axis":  {"type": "select", "options": ["X", "Y", "Z"], "default": "Z", "label": "Axis"},
            "angle": {"type": "float",  "default": 90, "unit": "\u00b0", "label": "Angle"},
        },
    },
    "scale": {
        "label": "Scale",
        "icon": "\u2922",
        "category": "modify",
        "params": {
            "x": {"type": "float", "default": 1.0, "min": 0.01, "label": "Scale X"},
            "y": {"type": "float", "default": 1.0, "min": 0.01, "label": "Scale Y"},
            "z": {"type": "float", "default": 1.0, "min": 0.01, "label": "Scale Z"},
        },
    },
    "linear_pattern": {
        "label": "Linear Pattern",
        "icon": "\u2afc",
        "category": "pattern",
        "params": {
            "direction": {"type": "vector", "default": [1, 0, 0], "label": "Direction"},
            "count":     {"type": "int",    "default": 3, "min": 2, "label": "Count"},
            "spacing":   {"type": "float",  "default": 20, "min": 0.01, "unit": "mm", "label": "Spacing"},
        },
    },
    "circular_pattern": {
        "label": "Circular Pattern",
        "icon": "\u27f3",
        "category": "pattern",
        "params": {
            "axis":  {"type": "vector", "default": [0, 0, 1], "label": "Axis"},
            "count": {"type": "int",    "default": 6, "min": 2, "label": "Count"},
            "angle": {"type": "float",  "default": 360, "min": 1, "max": 360, "unit": "\u00b0", "label": "Total Angle"},
        },
    },
    "mirror": {
        "label": "Mirror",
        "icon": "\u229b",
        "category": "pattern",
        "params": {
            "plane": {"type": "select", "options": ["XY", "XZ", "YZ"], "default": "XZ", "label": "Mirror Plane"},
        },
    },
    "sketch_extrude": {
        "label": "Extrude",
        "icon": "\u2b06",
        "category": "sketch",
        "params": {
            "sketch_id": {"type": "sketch_ref", "label": "Sketch", "default": None},
            "depth": {"type": "float", "default": 20, "min": 0.01, "unit": "mm", "label": "Depth"},
            "direction": {"type": "select", "options": ["normal", "symmetric"], "default": "normal", "label": "Direction"},
            "operation": {"type": "select", "options": ["boss", "cut"], "default": "boss", "label": "Operation"},
        },
    },
    "sketch_revolve": {
        "label": "Revolve",
        "icon": "\u27f2",
        "category": "sketch",
        "params": {
            "sketch_id": {"type": "sketch_ref", "label": "Sketch", "default": None},
            "angle": {"type": "float", "default": 360, "min": 1, "max": 360, "unit": "\u00b0", "label": "Angle"},
            "axis": {"type": "select", "options": ["X", "Y", "Z"], "default": "Y", "label": "Axis"},
            "operation": {"type": "select", "options": ["boss", "cut"], "default": "boss", "label": "Operation"},
        },
    },
    "sketch_cut": {
        "label": "Extrude Cut",
        "icon": "\u2b07",
        "category": "sketch",
        "params": {
            "sketch_id": {"type": "sketch_ref", "label": "Sketch", "default": None},
            "depth": {"type": "float", "default": 20, "min": 0.01, "unit": "mm", "label": "Depth"},
            "operation": {"type": "select", "options": ["cut", "boss"], "default": "cut", "label": "Operation"},
        },
    },
}
