"""Operation definitions, registry, and serialization for the parametric engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


OPERATION_REGISTRY: Dict[str, type] = {}


def register_op(name: str):
    def decorator(cls):
        OPERATION_REGISTRY[name] = cls
        return cls
    return decorator


@dataclass
class Operation:
    id: Optional[int]
    part_id: int
    sequence: int
    operation: str
    parameters: Dict[str, Any]
    parent_op_id: Optional[int] = None


@register_op("box")
class BoxOp:
    param_schema = {
        "width":  {"type": "float", "default": 50, "min": 0.1, "unit": "mm"},
        "height": {"type": "float", "default": 30, "min": 0.1, "unit": "mm"},
        "depth":  {"type": "float", "default": 20, "min": 0.1, "unit": "mm"},
    }


@register_op("cylinder")
class CylinderOp:
    param_schema = {
        "height": {"type": "float", "default": 40, "min": 0.1, "unit": "mm"},
        "radius": {"type": "float", "default": 15, "min": 0.1, "unit": "mm"},
    }


@register_op("sphere")
class SphereOp:
    param_schema = {
        "radius": {"type": "float", "default": 25, "min": 0.1, "unit": "mm"},
    }


@register_op("fillet")
class FilletOp:
    param_schema = {
        "edge_selector": {"type": "string", "default": "|Z"},
        "radius": {"type": "float", "default": 3, "min": 0.1, "unit": "mm"},
        "point": {"type": "list", "default": None, "optional": True, "description": "3D click point [x,y,z] for precise edge selection"},
        "normal": {"type": "list", "default": None, "optional": True, "description": "Face normal [nx,ny,nz] at click point"},
    }


@register_op("chamfer")
class ChamferOp:
    param_schema = {
        "edge_selector": {"type": "string", "default": ">Z"},
        "distance": {"type": "float", "default": 2, "min": 0.1, "unit": "mm"},
        "point": {"type": "list", "default": None, "optional": True, "description": "3D click point [x,y,z] for precise edge selection"},
        "normal": {"type": "list", "default": None, "optional": True, "description": "Face normal [nx,ny,nz] at click point"},
    }


@register_op("hole")
class HoleOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "diameter": {"type": "float", "default": 10, "min": 0.1, "unit": "mm"},
        "point": {"type": "list", "default": None, "optional": True, "description": "3D click point [x,y,z] for precise face selection"},
        "normal": {"type": "list", "default": None, "optional": True, "description": "Face normal [nx,ny,nz] at click point"},
    }


@register_op("shell")
class ShellOp:
    param_schema = {
        "thickness": {"type": "float", "default": 3, "min": 0.1, "unit": "mm"},
        "face_selector": {"type": "string", "default": ">Z"},
        "point": {"type": "list", "default": None, "optional": True, "description": "3D click point [x,y,z] for precise face selection"},
        "normal": {"type": "list", "default": None, "optional": True, "description": "Face normal [nx,ny,nz] at click point"},
    }


@register_op("translate")
class TranslateOp:
    param_schema = {
        "x": {"type": "float", "default": 0, "unit": "mm"},
        "y": {"type": "float", "default": 0, "unit": "mm"},
        "z": {"type": "float", "default": 0, "unit": "mm"},
    }


@register_op("union")
class UnionOp:
    param_schema = {
        "target_part_id": {"type": "int", "description": "Part to union with"},
    }


@register_op("cut")
class CutOp:
    param_schema = {
        "target_part_id": {"type": "int", "description": "Part to cut with"},
    }


@register_op("round_tube")
class RoundTubeOp:
    param_schema = {
        "length": {"type": "float", "default": 100, "min": 0.1, "unit": "mm"},
        "od":     {"type": "float", "default": 30, "min": 0.1, "unit": "mm"},
        "wall":   {"type": "float", "default": 2, "min": 0.1, "unit": "mm"},
        "axis":   {"type": "string", "default": "Z", "options": ["X", "Y", "Z"]},
    }


@register_op("rect_tube")
class RectTubeOp:
    param_schema = {
        "length": {"type": "float", "default": 100, "min": 0.1, "unit": "mm"},
        "width":  {"type": "float", "default": 40, "min": 0.1, "unit": "mm"},
        "height": {"type": "float", "default": 25, "min": 0.1, "unit": "mm"},
        "wall":   {"type": "float", "default": 2, "min": 0.1, "unit": "mm"},
        "axis":   {"type": "string", "default": "Z", "options": ["X", "Y", "Z"]},
    }


@register_op("draft")
class DraftOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "angle": {"type": "float", "default": 5, "min": 0.1, "max": 45, "unit": "deg"},
        "pull_direction": {"type": "string", "default": "Z", "options": ["X", "Y", "Z", "-X", "-Y", "-Z"]},
    }


@register_op("extrude")
class ExtrudeOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "distance": {"type": "float", "default": 10, "min": 0.1, "unit": "mm"},
    }


@register_op("revolve")
class RevolveOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "axis_selector": {"type": "string", "default": "|Z"},
        "angle": {"type": "float", "default": 360, "min": 1, "max": 360, "unit": "deg"},
    }


@register_op("sketch_extrude")
class SketchExtrudeOp:
    param_schema = {
        "sketch": {"type": "string", "default": "{}", "description": "Sketch JSON"},
        "depth": {"type": "float", "default": 10, "min": 0.1, "unit": "mm"},
        "mode": {"type": "string", "default": "add", "options": ["add", "cut"]},
        "symmetric": {"type": "string", "default": "false", "options": ["true", "false"]},
    }


@register_op("sketch_revolve")
class SketchRevolveOp:
    param_schema = {
        "sketch": {"type": "string", "default": "{}", "description": "Sketch JSON"},
        "angle": {"type": "float", "default": 360, "min": 1, "max": 360, "unit": "deg"},
        "axis": {"type": "string", "default": "X", "options": ["X", "Y"]},
        "mode": {"type": "string", "default": "add", "options": ["add", "cut"]},
    }


@register_op("sketch_cut")
class SketchCutOp:
    param_schema = {
        "sketch": {"type": "string", "default": "{}", "description": "Sketch JSON"},
        "depth": {"type": "float", "default": 10, "min": 0.1, "unit": "mm"},
    }


@register_op("linear_pattern")
class LinearPatternOp:
    param_schema = {
        "direction_x": {"type": "float", "default": 1, "min": -1, "max": 1},
        "direction_y": {"type": "float", "default": 0, "min": -1, "max": 1},
        "direction_z": {"type": "float", "default": 0, "min": -1, "max": 1},
        "count": {"type": "int", "default": 3, "min": 2, "max": 50},
        "spacing": {"type": "float", "default": 20, "min": 0.1, "unit": "mm"},
    }


@register_op("circular_pattern")
class CircularPatternOp:
    param_schema = {
        "axis": {"type": "string", "default": "Z", "options": ["X", "Y", "Z"]},
        "count": {"type": "int", "default": 6, "min": 2, "max": 72},
        "angle": {"type": "float", "default": 360, "min": 1, "max": 360, "unit": "deg"},
    }


@register_op("mirror")
class MirrorOp:
    param_schema = {
        "plane": {"type": "string", "default": "YZ", "options": ["XY", "YZ", "XZ"]},
    }


@register_op("counterbore")
class CounterboreOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "diameter": {"type": "float", "default": 6, "min": 0.5, "unit": "mm"},
        "cbore_diameter": {"type": "float", "default": 11, "min": 0.5, "unit": "mm"},
        "cbore_depth": {"type": "float", "default": 5, "min": 0.1, "unit": "mm"},
    }


@register_op("countersink")
class CountersinkOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "diameter": {"type": "float", "default": 6, "min": 0.5, "unit": "mm"},
        "csk_diameter": {"type": "float", "default": 12, "min": 0.5, "unit": "mm"},
        "csk_angle": {"type": "float", "default": 82, "min": 60, "max": 120, "unit": "deg"},
    }


@register_op("boolean_union")
class BooleanUnionOp:
    param_schema = {
        "target_part_script": {"type": "string", "default": "", "description": "CadQuery script of part to union"},
    }


@register_op("boolean_subtract")
class BooleanSubtractOp:
    param_schema = {
        "target_part_script": {"type": "string", "default": "", "description": "CadQuery script of part to subtract"},
    }


@register_op("boolean_intersect")
class BooleanIntersectOp:
    param_schema = {
        "target_part_script": {"type": "string", "default": "", "description": "CadQuery script of part to intersect"},
    }


@register_op("resize_hole")
class ResizeHoleOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "new_diameter": {"type": "float", "default": 10, "min": 0.5, "unit": "mm"},
        "face_id": {"type": "int", "default": None, "optional": True},
        "point": {"type": "list", "default": None, "optional": True},
        "normal": {"type": "list", "default": None, "optional": True},
    }


@register_op("offset_face")
class OffsetFaceOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "distance": {"type": "float", "default": 2, "unit": "mm"},
        "face_id": {"type": "int", "default": None, "optional": True},
        "point": {"type": "list", "default": None, "optional": True},
        "normal": {"type": "list", "default": None, "optional": True},
    }


@register_op("delete_face")
class DeleteFaceOp:
    param_schema = {
        "face_selector": {"type": "string", "default": ">Z"},
        "face_id": {"type": "int", "default": None, "optional": True},
        "point": {"type": "list", "default": None, "optional": True},
        "normal": {"type": "list", "default": None, "optional": True},
    }


@register_op("loft")
class LoftOp:
    param_schema = {
        "bottom_shape": {"type": "string", "default": "rect", "options": ["rect", "circle"]},
        "bottom_w": {"type": "float", "default": 50, "min": 1, "unit": "mm"},
        "bottom_h": {"type": "float", "default": 30, "min": 1, "unit": "mm"},
        "top_shape": {"type": "string", "default": "rect", "options": ["rect", "circle"]},
        "top_w": {"type": "float", "default": 25, "min": 1, "unit": "mm"},
        "top_h": {"type": "float", "default": 15, "min": 1, "unit": "mm"},
        "height": {"type": "float", "default": 40, "min": 1, "unit": "mm"},
        "ruled": {"type": "string", "default": "false", "options": ["true", "false"]},
    }


@register_op("sweep")
class SweepOp:
    param_schema = {
        "profile_shape": {"type": "string", "default": "circle", "options": ["rect", "circle"]},
        "profile_w": {"type": "float", "default": 10, "min": 0.5, "unit": "mm"},
        "profile_h": {"type": "float", "default": 10, "min": 0.5, "unit": "mm"},
        "path_type": {"type": "string", "default": "line", "options": ["line", "arc"]},
        "path_length": {"type": "float", "default": 50, "min": 1, "unit": "mm"},
        "path_radius": {"type": "float", "default": 30, "min": 1, "unit": "mm"},
        "path_angle": {"type": "float", "default": 90, "min": 1, "max": 359, "unit": "deg"},
    }


@register_op("thicken")
class ThickenOp:
    param_schema = {
        "thickness": {"type": "float", "default": 3, "min": 0.1, "unit": "mm"},
        "direction": {"type": "string", "default": "outward", "options": ["outward", "inward", "both"]},
    }


@register_op("split_body")
class SplitBodyOp:
    param_schema = {
        "plane": {"type": "string", "default": "XY", "options": ["XY", "XZ", "YZ"]},
        "offset": {"type": "float", "default": 0, "unit": "mm"},
        "keep": {"type": "string", "default": "top", "options": ["top", "bottom"]},
    }


@register_op("offset_surface")
class OffsetSurfaceOp:
    param_schema = {
        "distance": {"type": "float", "default": 2, "min": -100, "unit": "mm"},
    }


@register_op("raw_script")
class RawScriptOp:
    """Fallback for scripts that can't be parsed into operations."""
    param_schema = {
        "script": {"type": "string", "default": ""},
    }


def get_param_schema(operation_name: str) -> dict:
    cls = OPERATION_REGISTRY.get(operation_name)
    if not cls:
        raise ValueError(f"Unknown operation: {operation_name}")
    return cls.param_schema
