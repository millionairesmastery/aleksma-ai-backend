"""Execution backend abstraction.

Routes CadQuery script execution to either local (exec) or Modal (remote)
based on the EXECUTION_BACKEND environment variable.

Usage:
    from execution_backend import get_backend
    backend = get_backend()
    result = backend.execute_and_mesh(script)
    # result.mesh, result.bbox, result.volume, result.warnings, result.errors
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ExecutionResult:
    """Unified result from script execution, regardless of backend."""
    success: bool = True
    mesh: Optional[dict] = None       # topo_mesh dict (vertices, faces, topo_faces, topo_edges)
    bbox: Optional[dict] = None       # bbox_min_x/y/z, bbox_max_x/y/z
    volume: float = 0.0
    workplane: Any = None             # cq.Workplane for local backend, None for Modal
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    face_count: int = 0
    edge_count: int = 0


class ExecutionBackend(Protocol):
    """Protocol for execution backends."""
    def execute_and_mesh(self, script: str, quality: str = "preview") -> ExecutionResult: ...
    def execute_only(self, script: str) -> ExecutionResult: ...
    def export(self, script: str, fmt: str) -> bytes: ...


class LocalBackend:
    """Execute CadQuery scripts locally via exec().

    This is the current behavior — wraps executor.py functions.
    """

    def execute_and_mesh(self, script: str, quality: str = "preview") -> ExecutionResult:
        from executor import execute_script, shape_to_topo_mesh, extract_bounding_box, compute_volume
        from geometry_checks import check_geometry

        result = ExecutionResult()
        try:
            wp = execute_script(script, skip_geometry_check=True)
            result.workplane = wp

            # Geometry checks
            geo_check = check_geometry(wp)
            result.warnings = geo_check.warnings
            if geo_check.errors:
                result.errors = geo_check.errors
                result.success = False
                return result

            result.mesh = shape_to_topo_mesh(wp, quality=quality)
            result.bbox = extract_bounding_box(wp)
            result.volume = compute_volume(wp)
            result.face_count = geo_check.face_count
            result.edge_count = geo_check.edge_count

        except Exception as e:
            result.success = False
            result.errors.append(str(e))

        return result

    def execute_only(self, script: str) -> ExecutionResult:
        from executor import execute_script, extract_bounding_box, compute_volume
        from geometry_checks import check_geometry

        result = ExecutionResult()
        try:
            wp = execute_script(script, skip_geometry_check=True)
            result.workplane = wp
            result.bbox = extract_bounding_box(wp)
            result.volume = compute_volume(wp)

            geo_check = check_geometry(wp)
            result.warnings = geo_check.warnings
            if geo_check.errors:
                result.errors = geo_check.errors
                result.success = False
        except Exception as e:
            result.success = False
            result.errors.append(str(e))

        return result

    def export(self, script: str, fmt: str) -> bytes:
        from executor import execute_script, export_stl, export_step, export_brep

        wp = execute_script(script)
        exporters = {"stl": export_stl, "step": export_step, "brep": export_brep}
        exporter = exporters.get(fmt)
        if not exporter:
            raise ValueError(f"Unknown export format: {fmt}")
        return exporter(wp)


class ModalBackend:
    """Execute CadQuery scripts remotely via Modal.com serverless functions.

    Requires:
        - modal package installed: pip install modal
        - Modal auth configured: modal token set
        - Modal app deployed: modal deploy modal_functions.py
    """

    def __init__(self):
        self._fn_cache = {}

    def _get_function(self, name: str):
        """Lazily look up a deployed Modal function."""
        if name not in self._fn_cache:
            import modal
            self._fn_cache[name] = modal.Function.from_name("aleksma-cad", name)
        return self._fn_cache[name]

    def execute_and_mesh(self, script: str, quality: str = "preview") -> ExecutionResult:
        result = ExecutionResult()
        try:
            fn = self._get_function("execute_and_mesh")
            remote_result = fn.remote(script=script, quality=quality)

            if remote_result.get("error"):
                result.success = False
                result.errors.append(remote_result["error"])
                return result

            result.mesh = remote_result.get("mesh")
            result.bbox = remote_result.get("bbox")
            result.volume = remote_result.get("volume", 0.0)
            result.warnings = remote_result.get("warnings", [])
            result.face_count = remote_result.get("face_count", 0)
            result.edge_count = remote_result.get("edge_count", 0)

        except Exception as e:
            result.success = False
            result.errors.append(f"Modal execution failed: {e}")

        return result

    def execute_only(self, script: str) -> ExecutionResult:
        result = ExecutionResult()
        try:
            fn = self._get_function("execute_only")
            remote_result = fn.remote(script=script)

            if remote_result.get("error"):
                result.success = False
                result.errors.append(remote_result["error"])
                return result

            result.bbox = remote_result.get("bbox")
            result.volume = remote_result.get("volume", 0.0)
            result.warnings = remote_result.get("warnings", [])

        except Exception as e:
            result.success = False
            result.errors.append(f"Modal execution failed: {e}")

        return result

    def export(self, script: str, fmt: str) -> bytes:
        import base64
        fn = self._get_function("execute_and_export")
        remote_result = fn.remote(script=script, export_format=fmt)
        if remote_result.get("error"):
            raise ValueError(remote_result["error"])
        return base64.b64decode(remote_result["export_bytes"])


# ── Singleton ─────────────────────────────────────────────────────────────────

_backend: Optional[ExecutionBackend] = None


def get_backend() -> ExecutionBackend:
    """Get the configured execution backend (cached singleton)."""
    global _backend
    if _backend is None:
        backend_type = os.environ.get("EXECUTION_BACKEND", "local").lower()
        if backend_type == "modal":
            print("[EXEC] Using Modal backend for CadQuery execution")
            _backend = ModalBackend()
        else:
            print("[EXEC] Using local backend for CadQuery execution")
            _backend = LocalBackend()
    return _backend


def reset_backend():
    """Reset the cached backend (useful for testing/config changes)."""
    global _backend
    _backend = None
