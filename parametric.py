"""Parametric engine: builds geometry from operation trees, replays on parameter change."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import cadquery as cq
from OCP.gp import gp_Pnt

from operations import Operation, OPERATION_REGISTRY
from shapes import round_tube, rect_tube, flat_plate, l_bracket, gusset, mounting_boss
from executor import execute_script


def _get_shape(wp: cq.Workplane) -> cq.Shape:
    """Extract the underlying Shape from a Workplane."""
    from cadquery import Compound
    shapes = wp.vals()
    if len(shapes) == 1:
        return shapes[0]
    return Compound.makeCompound(shapes)


def _find_nearest_face(wp: cq.Workplane, point: list):
    """Find the CQ Face object nearest to the given 3D point."""
    shape = _get_shape(wp)
    target = gp_Pnt(point[0], point[1], point[2])
    best_face = None
    best_dist = float("inf")
    for face in shape.Faces():
        c = face.Center()
        dist = gp_Pnt(c.x, c.y, c.z).Distance(target)
        if dist < best_dist:
            best_dist = dist
            best_face = face
    return best_face


def _find_nearest_edge(wp: cq.Workplane, point: list):
    """Find the CQ Edge object nearest to the given 3D point."""
    shape = _get_shape(wp)
    target = gp_Pnt(point[0], point[1], point[2])
    best_edge = None
    best_dist = float("inf")
    for edge in shape.Edges():
        c = edge.Center()
        dist = gp_Pnt(c.x, c.y, c.z).Distance(target)
        if dist < best_dist:
            best_dist = dist
            best_edge = edge
    return best_edge


def _select_face_by_id(wp: cq.Workplane, face_id: int):
    """Select a BREP face by its topology index."""
    shape = _get_shape(wp)
    faces = list(shape.Faces())
    if 0 <= face_id < len(faces):
        return faces[face_id]
    return None


def _select_edge_by_id(wp: cq.Workplane, edge_id: int):
    """Select a BREP edge by its topology index."""
    shape = _get_shape(wp)
    edges = list(shape.Edges())
    if 0 <= edge_id < len(edges):
        return edges[edge_id]
    return None


def _resolve_face(wp: cq.Workplane, p: dict):
    """Resolve a face from parameters — prefer face_id, fall back to point, then selector."""
    if p.get("face_id") is not None:
        face = _select_face_by_id(wp, p["face_id"])
        if face:
            return face
    if p.get("point"):
        face = _find_nearest_face(wp, p["point"])
        if face:
            return face
    return None


def _resolve_edge(wp: cq.Workplane, p: dict):
    """Resolve an edge from parameters — prefer edge_id, fall back to point, then selector."""
    if p.get("edge_id") is not None:
        edge = _select_edge_by_id(wp, p["edge_id"])
        if edge:
            return edge
    if p.get("point"):
        edge = _find_nearest_edge(wp, p["point"])
        if edge:
            return edge
    return None


class ParametricEngine:

    def build(self, operations: List[Operation]) -> cq.Workplane:
        """Execute a sequence of operations and return the final Workplane."""
        wp = None
        for op in sorted(operations, key=lambda o: o.sequence):
            wp = self._execute_op(op, wp)
        if wp is None:
            raise ValueError("No operations to build")
        return wp

    def _execute_op(self, op: Operation, current_wp: Optional[cq.Workplane]) -> cq.Workplane:
        p = op.parameters
        name = op.operation

        if name == "box":
            return cq.Workplane("XY").box(p["width"], p["height"], p["depth"])
        elif name == "cylinder":
            return cq.Workplane("XY").cylinder(p["height"], p["radius"])
        elif name == "sphere":
            return cq.Workplane("XY").sphere(p["radius"])

        elif name == "round_tube":
            return round_tube(p["length"], p.get("od", 30), p.get("wall", 2), p.get("axis", "Z"))
        elif name == "rect_tube":
            return rect_tube(p["length"], p.get("width", 40), p.get("height", 25),
                             p.get("wall", 2), p.get("axis", "Z"))

        elif name == "fillet" and current_wp:
            edge = _resolve_edge(current_wp, p)
            if edge:
                return current_wp.newObject([edge]).fillet(p["radius"])
            return current_wp.edges(p.get("edge_selector", "|Z")).fillet(p["radius"])

        elif name == "chamfer" and current_wp:
            edge = _resolve_edge(current_wp, p)
            if edge:
                return current_wp.newObject([edge]).chamfer(p["distance"])
            return current_wp.edges(p.get("edge_selector", ">Z")).chamfer(p["distance"])

        elif name == "hole" and current_wp:
            face = _resolve_face(current_wp, p)
            if face:
                # Get normal from the face itself for accuracy
                try:
                    fn = face.normalAt()
                    nrm = [fn.x, fn.y, fn.z]
                except Exception:
                    nrm = p.get("normal", [0, 0, 1])
                pt = p.get("point") or [face.Center().x, face.Center().y, face.Center().z]
                wp_face = (
                    cq.Workplane(cq.Plane(
                        origin=cq.Vector(pt[0], pt[1], pt[2]),
                        normal=cq.Vector(nrm[0], nrm[1], nrm[2]),
                    ))
                    .add(current_wp.val())
                )
                return wp_face.hole(p["diameter"])
            return current_wp.faces(p.get("face_selector", ">Z")).workplane().hole(p["diameter"])

        elif name == "shell" and current_wp:
            face = _resolve_face(current_wp, p)
            if face:
                return current_wp.newObject([face]).shell(-p["thickness"])
            return current_wp.faces(p.get("face_selector", ">Z")).shell(-p["thickness"])
        elif name == "translate" and current_wp:
            return current_wp.translate((p["x"], p["y"], p["z"]))

        elif name == "draft" and current_wp:
            face = _resolve_face(current_wp, p)
            angle_deg = float(p.get("angle", 5))
            pull_dir = p.get("pull_direction", "Z")
            dir_map = {
                "X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1),
                "-X": (-1, 0, 0), "-Y": (0, -1, 0), "-Z": (0, 0, -1),
            }
            pull_vec = dir_map.get(pull_dir, (0, 0, 1))

            if face:
                try:
                    from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
                    from OCP.gp import gp_Dir, gp_Pln
                    from OCP.TopAbs import TopAbs_FACE
                    from OCP.TopExp import TopExp_Explorer
                    import math

                    shape = _get_shape(current_wp)
                    drafter = BRepOffsetAPI_DraftAngle(shape.wrapped)
                    direction = gp_Dir(*pull_vec)
                    angle_rad = math.radians(angle_deg)

                    # Draft the specific face
                    neutral_plane = gp_Pln(gp_Pnt(0, 0, 0), direction)
                    drafter.Add(face.wrapped, direction, angle_rad, neutral_plane)
                    drafter.Build()
                    if drafter.IsDone():
                        from cadquery import Shape
                        return cq.Workplane("XY").add(Shape(drafter.Shape()))
                except Exception:
                    pass
            # Fallback: no-op if draft fails (complex geometry)
            return current_wp

        elif name == "extrude" and current_wp:
            face = _resolve_face(current_wp, p)
            depth = float(p.get("depth", 10))
            if face:
                try:
                    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
                    from OCP.gp import gp_Vec as ocp_gp_Vec
                    from cadquery import Shape

                    fn = face.normalAt()
                    prism_vec = ocp_gp_Vec(fn.x * depth, fn.y * depth, fn.z * depth)
                    prism = BRepPrimAPI_MakePrism(face.wrapped, prism_vec)
                    prism.Build()
                    if prism.IsDone():
                        extrusion = Shape(prism.Shape())
                        return current_wp.union(cq.Workplane("XY").add(extrusion))
                except Exception:
                    pass
            # Fallback: can't extrude without a face
            return current_wp

        elif name == "resize_hole" and current_wp:
            face = _resolve_face(current_wp, p)
            new_diameter = float(p.get("new_diameter", 10))
            if face:
                try:
                    from OCP.BRepAdaptor import BRepAdaptor_Surface
                    from OCP.GeomAbs import GeomAbs_Cylinder
                    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
                    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
                    from OCP.gp import gp_Ax2, gp_Dir
                    from cadquery import Shape

                    adaptor = BRepAdaptor_Surface(face.wrapped)
                    if adaptor.GetType() == GeomAbs_Cylinder:
                        cyl = adaptor.Cylinder()
                        axis = cyl.Axis()
                        loc = axis.Location()
                        direction = axis.Direction()
                        old_radius = cyl.Radius()

                        # Get face height (bounding box along axis)
                        from OCP.Bnd import Bnd_Box
                        from OCP.BRepBndLib import BRepBndLib_AddClose
                        bb = Bnd_Box()
                        BRepBndLib_AddClose(face.wrapped, bb)
                        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
                        height = max(xmax - xmin, ymax - ymin, zmax - zmin) * 1.5  # extra for safety

                        new_radius = new_diameter / 2.0

                        # Create old cylinder to fill hole, then new cylinder to cut new hole
                        ax2 = gp_Ax2(loc, direction)

                        # Step 1: Fill the old hole by creating a cylinder and unioning it
                        fill_cyl = BRepPrimAPI_MakeCylinder(ax2, old_radius, height).Shape()
                        filled = BRepAlgoAPI_Cut(current_wp.val().wrapped, fill_cyl)  # No-op to get base
                        # Actually: union the old hole fill, then cut new hole
                        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
                        fused = BRepAlgoAPI_Fuse(current_wp.val().wrapped, fill_cyl)
                        fused.Build()
                        if fused.IsDone():
                            # Step 2: Cut the new hole
                            new_cyl = BRepPrimAPI_MakeCylinder(ax2, new_radius, height).Shape()
                            result = BRepAlgoAPI_Cut(fused.Shape(), new_cyl)
                            result.Build()
                            if result.IsDone():
                                return cq.Workplane("XY").add(Shape(result.Shape()))
                except Exception:
                    pass
            return current_wp

        elif name == "offset_face" and current_wp:
            face = _resolve_face(current_wp, p)
            distance = float(p.get("distance", 2))
            if face:
                try:
                    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
                    from OCP.BRepOffset import BRepOffset_Skin
                    from OCP.GeomAbs import GeomAbs_Intersection
                    from cadquery import Shape

                    offset = BRepOffsetAPI_MakeOffsetShape()
                    offset.PerformBySimple(current_wp.val().wrapped, distance)
                    if offset.IsDone():
                        return cq.Workplane("XY").add(Shape(offset.Shape()))
                except Exception:
                    pass
            return current_wp

        elif name == "delete_face" and current_wp:
            face = _resolve_face(current_wp, p)
            if face:
                try:
                    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
                    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
                    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
                    from cadquery import Shape

                    defeature = BRepAlgoAPI_Defeaturing()
                    defeature.SetShape(current_wp.val().wrapped)
                    defeature.AddFaceToRemove(face.wrapped)
                    defeature.Build()
                    if defeature.IsDone():
                        return cq.Workplane("XY").add(Shape(defeature.Shape()))
                except Exception:
                    pass
            return current_wp

        elif name == "sketch_extrude":
            script = sketch_to_cadquery_script(p.get("sketch", "{}"), "extrude", p)
            return execute_script(script)

        elif name == "sketch_revolve":
            script = sketch_to_cadquery_script(p.get("sketch", "{}"), "revolve", p)
            return execute_script(script)

        elif name == "sketch_cut" and current_wp:
            script = sketch_to_cadquery_script(p.get("sketch", "{}"), "cut", p)
            cut_shape = execute_script(script)
            return current_wp.cut(cut_shape.val())

        elif name == "linear_pattern" and current_wp:
            dx = float(p.get("direction_x", 1))
            dy = float(p.get("direction_y", 0))
            dz = float(p.get("direction_z", 0))
            count = int(p.get("count", 3))
            spacing = float(p.get("spacing", 20))
            base_solid = current_wp.val()
            result_wp = current_wp
            for i in range(1, count):
                offset = (dx * spacing * i, dy * spacing * i, dz * spacing * i)
                copy = cq.Workplane("XY").add(base_solid).translate(offset)
                result_wp = result_wp.union(copy)
            return result_wp

        elif name == "circular_pattern" and current_wp:
            axis_name = p.get("axis", "Z")
            count = int(p.get("count", 6))
            total_angle = float(p.get("angle", 360))
            angle_step = total_angle / count
            axis_vec = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(axis_name, (0, 0, 1))
            base_solid = current_wp.val()
            result_wp = current_wp
            for i in range(1, count):
                angle = angle_step * i
                rotated = base_solid.Rotate((0, 0, 0), axis_vec, angle)
                result_wp = result_wp.union(cq.Workplane("XY").add(rotated))
            return result_wp

        elif name == "mirror" and current_wp:
            plane = p.get("plane", "YZ")
            return current_wp.mirror(plane)

        elif name == "counterbore" and current_wp:
            face = _resolve_face(current_wp, p)
            if face:
                try:
                    fn = face.normalAt()
                    nrm = [fn.x, fn.y, fn.z]
                except Exception:
                    nrm = p.get("normal", [0, 0, 1])
                pt = p.get("point") or [face.Center().x, face.Center().y, face.Center().z]
                wp_face = (
                    cq.Workplane(cq.Plane(
                        origin=cq.Vector(pt[0], pt[1], pt[2]),
                        normal=cq.Vector(nrm[0], nrm[1], nrm[2]),
                    ))
                    .add(current_wp.val())
                )
                return wp_face.cboreHole(p["diameter"], p["cbore_diameter"], p["cbore_depth"])
            return current_wp.faces(p.get("face_selector", ">Z")).workplane().cboreHole(
                p["diameter"], p["cbore_diameter"], p["cbore_depth"]
            )

        elif name == "countersink" and current_wp:
            face = _resolve_face(current_wp, p)
            if face:
                try:
                    fn = face.normalAt()
                    nrm = [fn.x, fn.y, fn.z]
                except Exception:
                    nrm = p.get("normal", [0, 0, 1])
                pt = p.get("point") or [face.Center().x, face.Center().y, face.Center().z]
                wp_face = (
                    cq.Workplane(cq.Plane(
                        origin=cq.Vector(pt[0], pt[1], pt[2]),
                        normal=cq.Vector(nrm[0], nrm[1], nrm[2]),
                    ))
                    .add(current_wp.val())
                )
                return wp_face.cskHole(p["diameter"], p["csk_diameter"], p["csk_angle"])
            return current_wp.faces(p.get("face_selector", ">Z")).workplane().cskHole(
                p["diameter"], p["csk_diameter"], p["csk_angle"]
            )

        elif name == "boolean_union" and current_wp:
            target = execute_script(p["target_part_script"])
            return current_wp.union(target)

        elif name == "boolean_subtract" and current_wp:
            target = execute_script(p["target_part_script"])
            return current_wp.cut(target)

        elif name == "boolean_intersect" and current_wp:
            target = execute_script(p["target_part_script"])
            return current_wp.intersect(target)

        elif name == "loft":
            wp = cq.Workplane("XY")
            if p.get("bottom_shape") == "circle":
                wp = wp.circle(float(p.get("bottom_w", 50)) / 2)
            else:
                wp = wp.rect(float(p.get("bottom_w", 50)), float(p.get("bottom_h", 30)))
            wp = wp.workplane(offset=float(p.get("height", 40)))
            if p.get("top_shape") == "circle":
                wp = wp.circle(float(p.get("top_w", 25)) / 2)
            else:
                wp = wp.rect(float(p.get("top_w", 25)), float(p.get("top_h", 15)))
            return wp.loft(ruled=(p.get("ruled") == "true"))

        elif name == "sweep":
            import math
            profile_w = float(p.get("profile_w", 10))
            profile_h = float(p.get("profile_h", 10))
            path_length = float(p.get("path_length", 50))
            path_radius = float(p.get("path_radius", 30))
            path_angle = float(p.get("path_angle", 90))

            if p.get("path_type") == "arc":
                rad = math.radians(path_angle)
                dx = path_radius * math.sin(rad)
                dy = path_radius * (1 - math.cos(rad))
                path = cq.Workplane("XZ").moveTo(0, 0).radiusArc((dx, dy), path_radius)
            else:
                path = cq.Workplane("XZ").moveTo(0, 0).lineTo(0, path_length)

            profile = cq.Workplane("XY")
            if p.get("profile_shape") == "rect":
                profile = profile.rect(profile_w, profile_h)
            else:
                profile = profile.circle(profile_w / 2)

            return profile.sweep(path)

        elif name == "thicken" and current_wp:
            t = float(p.get("thickness", 3))
            direction = p.get("direction", "outward")
            if direction == "inward":
                return current_wp.shell(-t)
            else:
                return current_wp.shell(t)

        elif name == "split_body" and current_wp:
            plane = p.get("plane", "XY")
            offset = float(p.get("offset", 0))
            keep = p.get("keep", "top")
            big = 10000
            if plane == "XY":
                cut_z = offset - big / 2 if keep == "top" else offset + big / 2
                cutter = cq.Workplane("XY").transformed(offset=(0, 0, cut_z)).box(big, big, big)
            elif plane == "XZ":
                cut_y = offset - big / 2 if keep == "top" else offset + big / 2
                cutter = cq.Workplane("XY").transformed(offset=(0, cut_y, 0)).box(big, big, big)
            else:
                cut_x = offset - big / 2 if keep == "top" else offset + big / 2
                cutter = cq.Workplane("XY").transformed(offset=(cut_x, 0, 0)).box(big, big, big)
            return current_wp.cut(cutter)

        elif name == "offset_surface" and current_wp:
            dist = float(p.get("distance", 2))
            try:
                from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
                from cadquery import Shape
                maker = BRepOffsetAPI_MakeOffsetShape()
                maker.PerformBySimple(current_wp.val().wrapped, dist)
                if maker.IsDone():
                    return cq.Workplane("XY").add(Shape(maker.Shape()))
            except Exception:
                pass
            return current_wp

        elif name == "raw_script":
            return execute_script(p["script"])

        else:
            raise ValueError(f"Cannot execute '{name}' — unknown or missing input geometry")

    def update_parameter(self, operations: List[Operation], op_id: int,
                         param_name: str, value: Any) -> cq.Workplane:
        for op in operations:
            if op.id == op_id:
                op.parameters[param_name] = value
                break
        return self.build(operations)

    def insert_after(self, operations: List[Operation], after_sequence: int,
                     new_op: Operation) -> List[Operation]:
        result = []
        inserted = False
        for op in sorted(operations, key=lambda o: o.sequence):
            result.append(op)
            if op.sequence == after_sequence and not inserted:
                new_op.sequence = op.sequence + 1
                result.append(new_op)
                inserted = True
        if not inserted:
            new_op.sequence = (result[-1].sequence + 1) if result else 1
            result.append(new_op)
        for i, op in enumerate(result):
            op.sequence = i + 1
        return result

    def delete_op(self, operations: List[Operation], op_id: int) -> List[Operation]:
        result = [op for op in operations if op.id != op_id]
        for i, op in enumerate(sorted(result, key=lambda o: o.sequence)):
            op.sequence = i + 1
        return result


def parse_script_to_operations(script: str) -> List[Dict[str, Any]]:
    """
    Best-effort parser that converts a CadQuery script to a list of operations.
    Falls back to a single 'raw_script' operation if parsing fails.
    """
    ops: List[Dict[str, Any]] = []

    try:
        _parse_chain(script, ops)
    except Exception:
        pass

    if not ops:
        return [{"operation": "raw_script", "parameters": {"script": script}}]
    return ops


def _parse_chain(script: str, ops: List[Dict[str, Any]]):
    """Extract common CadQuery patterns from a script."""

    # Primitives: .box(w, h, d)
    for m in re.finditer(r'\.box\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)', script):
        ops.append({
            "operation": "box",
            "parameters": {
                "width": float(m.group(1)),
                "height": float(m.group(2)),
                "depth": float(m.group(3)),
            },
        })

    # .cylinder(height, radius)
    for m in re.finditer(r'\.cylinder\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)', script):
        ops.append({
            "operation": "cylinder",
            "parameters": {
                "height": float(m.group(1)),
                "radius": float(m.group(2)),
            },
        })

    # .sphere(radius)
    for m in re.finditer(r'\.sphere\(\s*([0-9.]+)\s*\)', script):
        ops.append({
            "operation": "sphere",
            "parameters": {"radius": float(m.group(1))},
        })

    # .hole(diameter)
    face_hole = re.finditer(
        r'\.faces\(\s*["\']([^"\']+)["\']\s*\)\.workplane\(\)\.hole\(\s*([0-9.]+)\s*\)', script
    )
    for m in face_hole:
        ops.append({
            "operation": "hole",
            "parameters": {"face_selector": m.group(1), "diameter": float(m.group(2))},
        })

    # .fillet(r) with edge selector
    for m in re.finditer(r'\.edges\(\s*["\']([^"\']+)["\']\s*\)\.fillet\(\s*([0-9.]+)\s*\)', script):
        ops.append({
            "operation": "fillet",
            "parameters": {"edge_selector": m.group(1), "radius": float(m.group(2))},
        })

    # .chamfer(d) with edge selector
    for m in re.finditer(r'\.edges\(\s*["\']([^"\']+)["\']\s*\)\.chamfer\(\s*([0-9.]+)\s*\)', script):
        ops.append({
            "operation": "chamfer",
            "parameters": {"edge_selector": m.group(1), "distance": float(m.group(2))},
        })

    # .shell(-thickness)
    for m in re.finditer(r'\.shell\(\s*-?\s*([0-9.]+)\s*\)', script):
        ops.append({
            "operation": "shell",
            "parameters": {"thickness": float(m.group(1)), "face_selector": ">Z"},
        })

    # .translate((x, y, z))
    for m in re.finditer(r'\.translate\(\s*\(\s*([0-9.\-]+)\s*,\s*([0-9.\-]+)\s*,\s*([0-9.\-]+)\s*\)\s*\)', script):
        ops.append({
            "operation": "translate",
            "parameters": {"x": float(m.group(1)), "y": float(m.group(2)), "z": float(m.group(3))},
        })

    # round_tube(length, ...) — function call style
    for m in re.finditer(r'round_tube\(\s*([0-9.]+)', script):
        params = {"length": float(m.group(1)), "od": 30, "wall": 2, "axis": "Z"}
        od_m = re.search(r'round_tube\([^)]*od\s*=\s*([0-9.]+)', script)
        wall_m = re.search(r'round_tube\([^)]*wall\s*=\s*([0-9.]+)', script)
        axis_m = re.search(r'round_tube\([^)]*axis\s*=\s*["\']([XYZ])["\']', script)
        if od_m:
            params["od"] = float(od_m.group(1))
        if wall_m:
            params["wall"] = float(wall_m.group(1))
        if axis_m:
            params["axis"] = axis_m.group(1)
        ops.append({"operation": "round_tube", "parameters": params})

    # rect_tube(length, ...) — function call style
    for m in re.finditer(r'rect_tube\(\s*([0-9.]+)', script):
        params = {"length": float(m.group(1)), "width": 40, "height": 25, "wall": 2, "axis": "Z"}
        w_m = re.search(r'rect_tube\([^)]*width\s*=\s*([0-9.]+)', script)
        h_m = re.search(r'rect_tube\([^)]*height\s*=\s*([0-9.]+)', script)
        wall_m = re.search(r'rect_tube\([^)]*wall\s*=\s*([0-9.]+)', script)
        axis_m = re.search(r'rect_tube\([^)]*axis\s*=\s*["\']([XYZ])["\']', script)
        if w_m:
            params["width"] = float(w_m.group(1))
        if h_m:
            params["height"] = float(h_m.group(1))
        if wall_m:
            params["wall"] = float(wall_m.group(1))
        if axis_m:
            params["axis"] = axis_m.group(1)
        ops.append({"operation": "rect_tube", "parameters": params})


def sketch_to_cadquery_script(sketch_json_str: str, feature_type: str, params: dict) -> str:
    """Convert a sketch JSON + feature type into a CadQuery Python script."""
    import json as _json

    try:
        sketch = _json.loads(sketch_json_str) if isinstance(sketch_json_str, str) else sketch_json_str
    except Exception:
        sketch = {}

    entities = sketch.get("entities", [])
    lines = [e for e in entities if e.get("type") == "line" and not e.get("construction")]
    circles = [e for e in entities if e.get("type") == "circle" and not e.get("construction")]

    script_parts = ["import cadquery as cq", ""]

    if circles and not lines:
        c = circles[0]
        cx, cy = c.get("center", [0, 0])
        r = c.get("radius", 10)
        script_parts.append(f"sketch_wp = cq.Workplane('XY').center({cx}, {cy}).circle({r})")
    elif lines:
        first = lines[0]
        start = first.get("start", [0, 0])
        script_parts.append(f"sketch_wp = (cq.Workplane('XY')")
        script_parts.append(f"  .moveTo({start[0]}, {start[1]})")
        for line in lines:
            end = line.get("end", [0, 0])
            script_parts.append(f"  .lineTo({end[0]}, {end[1]})")
        script_parts.append("  .close()")
        script_parts.append(")")
    else:
        script_parts.append("sketch_wp = cq.Workplane('XY').rect(50, 30)")

    depth = params.get("depth", 10)
    angle = params.get("angle", 360)
    mode = params.get("mode", "add")
    symmetric = params.get("symmetric", "false") == "true"

    if feature_type == "extrude":
        if symmetric:
            script_parts.append(f"result = sketch_wp.extrude({depth}, both=True)")
        else:
            script_parts.append(f"result = sketch_wp.extrude({depth})")
    elif feature_type == "revolve":
        axis = params.get("axis", "X")
        axis_vec = "(1, 0, 0)" if axis == "X" else "(0, 1, 0)"
        script_parts.append(f"result = sketch_wp.revolve({angle}, (0, 0, 0), {axis_vec})")
    elif feature_type == "cut":
        script_parts.append(f"result = sketch_wp.extrude({depth})")
    else:
        script_parts.append(f"result = sketch_wp.extrude({depth})")

    return "\n".join(script_parts) + "\n"
