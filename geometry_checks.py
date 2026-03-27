"""Post-execution geometry sanity checks.

Validates that a CadQuery shape is geometrically sound before
tessellation or storage. Catches degenerate geometry, invalid shapes,
and absurd dimensions that would produce garbage in the viewport.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import cadquery as cq
from cadquery import Compound


MAX_DIMENSION_MM = 10_000  # 10 meters
MIN_VOLUME_MM3 = 1e-6       # effectively zero
MIN_FACE_AREA_MM2 = 1e-6    # degenerate face threshold


@dataclass
class GeometryCheckResult:
    """Result of geometry validation checks."""
    valid: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    face_count: int = 0
    edge_count: int = 0
    volume: float = 0.0
    bbox: dict = field(default_factory=dict)


def check_geometry(workplane: cq.Workplane) -> GeometryCheckResult:
    """Run all sanity checks on a CadQuery Workplane.

    Returns a GeometryCheckResult. If result.errors is non-empty,
    the geometry should be rejected.
    """
    result = GeometryCheckResult()

    shapes = workplane.vals()
    if not shapes:
        result.valid = False
        result.errors.append("Workplane contains no shapes")
        return result

    shape: cq.Shape = shapes[0] if len(shapes) == 1 else Compound.makeCompound(shapes)

    # 1. OCC shape validity check
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer
        analyzer = BRepCheck_Analyzer(shape.wrapped)
        if not analyzer.IsValid():
            result.warnings.append("BRepCheck_Analyzer reports shape is not valid (may still be usable)")
    except Exception as e:
        result.warnings.append(f"Could not run BRepCheck: {e}")

    # 2. Bounding box checks
    try:
        bb = shape.BoundingBox()
        extents = [bb.xmax - bb.xmin, bb.ymax - bb.ymin, bb.zmax - bb.zmin]

        result.bbox = {
            "min": [bb.xmin, bb.ymin, bb.zmin],
            "max": [bb.xmax, bb.ymax, bb.zmax],
            "extents": extents,
        }

        # Check for NaN/Inf
        all_vals = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
        if any(v != v for v in all_vals):  # NaN check
            result.valid = False
            result.errors.append("Bounding box contains NaN values")
            return result
        if any(abs(v) == float("inf") for v in all_vals):
            result.valid = False
            result.errors.append("Bounding box contains infinite values")
            return result

        # Check for zero-extent (degenerate)
        zero_axes = sum(1 for e in extents if e < 1e-6)
        if zero_axes >= 3:
            result.valid = False
            result.errors.append("Shape has zero volume (all extents are zero)")
        elif zero_axes >= 2:
            result.warnings.append(f"Shape appears to be 1D (line-like): extents={[round(e, 3) for e in extents]}")
        elif zero_axes >= 1:
            result.warnings.append(f"Shape appears to be 2D (flat): extents={[round(e, 3) for e in extents]}")

        # Check for absurdly large dimensions
        for i, (axis, ext) in enumerate(zip(["X", "Y", "Z"], extents)):
            if ext > MAX_DIMENSION_MM:
                result.warnings.append(f"{axis} extent is {ext:.1f}mm (> {MAX_DIMENSION_MM}mm)")

    except Exception as e:
        result.valid = False
        result.errors.append(f"Failed to compute bounding box: {e}")
        return result

    # 3. Face count
    try:
        faces = shape.Faces()
        result.face_count = len(faces)
        if result.face_count == 0:
            result.valid = False
            result.errors.append("Shape has no faces")
        else:
            # Check for degenerate faces
            degenerate_count = 0
            for face in faces:
                try:
                    area = face.Area()
                    if area < MIN_FACE_AREA_MM2:
                        degenerate_count += 1
                except Exception:
                    degenerate_count += 1

            if degenerate_count > 0:
                result.warnings.append(
                    f"{degenerate_count} of {result.face_count} faces have near-zero area"
                )
    except Exception as e:
        result.warnings.append(f"Could not count faces: {e}")

    # 4. Edge count
    try:
        result.edge_count = len(shape.Edges())
    except Exception:
        pass

    # 5. Volume check
    try:
        result.volume = shape.Volume()
        if result.volume < MIN_VOLUME_MM3:
            result.warnings.append(f"Shape volume is near-zero: {result.volume:.6f} mm³")
    except Exception as e:
        result.warnings.append(f"Could not compute volume: {e}")

    return result


def require_valid_geometry(workplane: cq.Workplane, context: str = "Script") -> GeometryCheckResult:
    """Run geometry checks and raise ValueError if there are errors.

    Use this as a drop-in validation step after script execution.
    Returns the check result if valid, raises on errors.
    """
    result = check_geometry(workplane)

    if not result.valid or result.errors:
        error_msg = f"{context} produced invalid geometry:\n"
        error_msg += "\n".join(f"  - {e}" for e in result.errors)
        if result.warnings:
            error_msg += "\nWarnings:\n"
            error_msg += "\n".join(f"  - {w}" for w in result.warnings)
        raise ValueError(error_msg)

    if result.warnings:
        import sys
        print(f"[GEOMETRY] {context} warnings:", file=sys.stderr)
        for w in result.warnings:
            print(f"  - {w}", file=sys.stderr)

    return result
