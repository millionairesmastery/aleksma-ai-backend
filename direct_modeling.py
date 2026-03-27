"""
direct_modeling.py — Direct B-Rep modification operations using OpenCascade.

These operations modify solid geometry directly without a feature tree.
They work on any solid: imported STEP files, AI-generated parts, feature-tree parts.

After each operation, the result shape is cleaned up with ShapeUpgrade_UnifySameDomain
to merge coplanar/co-cylindrical faces — so push/pull EXTENDS existing faces
rather than creating new ones.

Operations:
  push_pull_face  — Move a face along its normal (extrude/indent)
  move_face       — Translate a face by a vector (adjacent faces stretch)
  offset_face     — Grow or shrink a face by a distance
  delete_face     — Remove a face and heal the solid
  shape_to_step   — Serialize a CadQuery shape to STEP string for persistence
  step_to_shape   — Deserialize a STEP string back to CadQuery workplane
"""
from __future__ import annotations
import cadquery as cq
from typing import Optional, Tuple
import math
import tempfile
import os


def _unify_shape(occ_shape):
    """
    Merge coplanar/co-cylindrical/co-spherical adjacent faces into single faces.
    This is critical after boolean operations — without it, push/pull creates
    extra edges and faces instead of cleanly extending existing geometry.
    """
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    unifier = ShapeUpgrade_UnifySameDomain(occ_shape, True, True, True)
    unifier.Build()
    return unifier.Shape()


def push_pull_face(
    workplane: cq.Workplane,
    face_index: int,
    distance: float,
    direction_vec: tuple | None = None,
) -> cq.Workplane:
    """
    Push or pull a face along a direction by the given distance.
    Positive distance = push outward, negative = pull inward.
    Result is cleaned with UnifySameDomain so faces merge cleanly.
    """
    shape = workplane.val() if hasattr(workplane, 'val') else workplane
    faces = shape.Faces()

    if face_index < 0 or face_index >= len(faces):
        raise ValueError(f"Face index {face_index} out of range (0-{len(faces)-1})")

    target_face = faces[face_index]

    # Direction from frontend gizmo (guaranteed to match visual)
    if direction_vec and len(direction_vec) == 3:
        nx, ny, nz = direction_vec
        length = math.sqrt(nx*nx + ny*ny + nz*nz)
        if length > 1e-10:
            nx, ny, nz = nx/length, ny/length, nz/length
        else:
            normal = target_face.normalAt()
            nx, ny, nz = normal.x, normal.y, normal.z
    else:
        normal = target_face.normalAt()
        nx, ny, nz = normal.x, normal.y, normal.z

    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut
    from OCP.gp import gp_Vec

    try:
        direction = gp_Vec(nx * distance, ny * distance, nz * distance)

        prism_maker = BRepPrimAPI_MakePrism(target_face.wrapped, direction)
        prism_maker.Build()
        if not prism_maker.IsDone():
            raise ValueError("Failed to create face extrusion prism")
        prism = prism_maker.Shape()

        if distance > 0:
            fuser = BRepAlgoAPI_Fuse(shape.wrapped, prism)
            fuser.Build()
            if not fuser.IsDone():
                raise ValueError("Boolean fuse failed")
            result_shape = fuser.Shape()
        else:
            cutter = BRepAlgoAPI_Cut(shape.wrapped, prism)
            cutter.Build()
            if not cutter.IsDone():
                raise ValueError("Boolean cut failed")
            result_shape = cutter.Shape()

        # Clean up: merge coplanar faces so it looks like one extended face
        result_shape = _unify_shape(result_shape)

        return cq.Workplane("XY").newObject([cq.Shape.cast(result_shape)])

    except Exception as e:
        raise ValueError(f"Push/pull failed: {e}")


def move_face(
    workplane: cq.Workplane,
    face_index: int,
    direction: Tuple[float, float, float],
    distance: float,
) -> cq.Workplane:
    """Move a face in an arbitrary direction. Adjacent faces stretch to follow."""
    shape = workplane.val() if hasattr(workplane, 'val') else workplane
    faces = shape.Faces()

    if face_index < 0 or face_index >= len(faces):
        raise ValueError(f"Face index {face_index} out of range")

    target_face = faces[face_index]

    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut
    from OCP.gp import gp_Vec

    dx, dy, dz = direction
    length = math.sqrt(dx*dx + dy*dy + dz*dz)
    if length < 1e-10:
        return workplane

    vec = gp_Vec(dx/length * distance, dy/length * distance, dz/length * distance)

    try:
        prism_maker = BRepPrimAPI_MakePrism(target_face.wrapped, vec)
        prism_maker.Build()
        if not prism_maker.IsDone():
            raise ValueError("Failed to create prism")
        prism = prism_maker.Shape()

        if distance > 0:
            fuser = BRepAlgoAPI_Fuse(shape.wrapped, prism)
            fuser.Build()
            if not fuser.IsDone():
                raise ValueError("Fuse failed")
            result_shape = _unify_shape(fuser.Shape())
            return cq.Workplane("XY").newObject([cq.Shape.cast(result_shape)])
        else:
            cutter = BRepAlgoAPI_Cut(shape.wrapped, prism)
            cutter.Build()
            if not cutter.IsDone():
                raise ValueError("Cut failed")
            result_shape = _unify_shape(cutter.Shape())
            return cq.Workplane("XY").newObject([cq.Shape.cast(result_shape)])
    except Exception as e:
        raise ValueError(f"Move face failed: {e}")


def offset_face(
    workplane: cq.Workplane,
    face_index: int,
    offset: float,
) -> cq.Workplane:
    """Offset (grow/shrink) a face. Uses BRepOffsetAPI_MakeOffsetShape."""
    shape = workplane.val() if hasattr(workplane, 'val') else workplane

    try:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
        from OCP.BRepOffset import BRepOffset_Skin
        from OCP.GeomAbs import GeomAbs_Intersection

        offset_maker = BRepOffsetAPI_MakeOffsetShape()
        offset_maker.PerformByJoin(
            shape.wrapped, offset, 1e-3,
            BRepOffset_Skin, False, False, GeomAbs_Intersection,
        )
        if not offset_maker.IsDone():
            raise ValueError("Offset operation failed")

        return cq.Workplane("XY").newObject([cq.Shape.cast(offset_maker.Shape())])

    except Exception as e:
        raise ValueError(f"Offset face failed: {e}")


def delete_face(
    workplane: cq.Workplane,
    face_index: int,
) -> cq.Workplane:
    """Delete a face and heal the gap. Uses BRepAlgoAPI_Defeaturing."""
    shape = workplane.val() if hasattr(workplane, 'val') else workplane
    faces = shape.Faces()

    if face_index < 0 or face_index >= len(faces):
        raise ValueError(f"Face index {face_index} out of range")

    target_face = faces[face_index]

    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape

        faces_to_remove = TopTools_ListOfShape()
        faces_to_remove.Append(target_face.wrapped)

        defeaturer = BRepAlgoAPI_Defeaturing()
        defeaturer.SetShape(shape.wrapped)
        defeaturer.AddFacesToRemove(faces_to_remove)
        defeaturer.Build()

        if not defeaturer.IsDone():
            raise ValueError("Defeaturing failed — face may not be removable")

        return cq.Workplane("XY").newObject([cq.Shape.cast(defeaturer.Shape())])

    except ImportError:
        raise ValueError("BRepAlgoAPI_Defeaturing not available in this OCC version")
    except Exception as e:
        raise ValueError(f"Delete face failed: {e}")


# ---------------------------------------------------------------------------
# Shape persistence — serialize/deserialize for DB storage
# ---------------------------------------------------------------------------

def shape_to_step_bytes(workplane: cq.Workplane) -> bytes:
    """Export a CadQuery workplane to STEP format bytes for DB storage."""
    shape = workplane.val() if hasattr(workplane, 'val') else workplane
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        cq.exporters.export(cq.Workplane("XY").newObject([shape]), tmp_path, "STEP")
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def step_bytes_to_shape(step_data: bytes) -> cq.Workplane:
    """Import STEP bytes back to a CadQuery workplane."""
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        tmp.write(step_data if isinstance(step_data, bytes) else bytes(step_data))
        tmp_path = tmp.name
    try:
        return cq.importers.importStep(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
