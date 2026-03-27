"""
parametric_ws.py — Real-time parametric WebSocket channel.

Provides incremental rebuild of feature trees when parameters change,
sending tessellated mesh updates back over WebSocket for live preview.
"""
from __future__ import annotations

import json
import time
import asyncio
import logging
from typing import Optional

import cadquery as cq
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from db import get_connection, put_connection
from feature_engine import ParameterResolver, FeatureExecutor, FEATURE_SCHEMAS
from executor import shape_to_topo_mesh, extract_bounding_box
from face_param_mapper import map_faces_to_features

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Incremental Rebuilder — caches intermediate shapes for fast partial rebuilds
# ---------------------------------------------------------------------------

class IncrementalRebuilder:
    """Caches intermediate OCC shapes at each feature step for fast partial rebuilds."""

    def __init__(self):
        self.shape_cache: dict[int, cq.Workplane] = {}  # sequence -> cq.Workplane
        self.last_full_features: list[dict] = []  # snapshot of features used in last full build

    def full_build(
        self,
        features: list[dict],
        resolver: ParameterResolver,
    ) -> dict:
        """Full rebuild, populating shape cache at every step.

        Mirrors FeatureExecutor.build() but caches wp after each handler call.
        Returns dict with shape, last_good_sequence, feature_errors, rebuild_status.
        """
        executor = FeatureExecutor(resolver)

        active = [f for f in features if not f.get("suppressed", False)]
        active.sort(key=lambda f: f["sequence"])

        wp: Optional[cq.Workplane] = None
        last_good = 0
        errors: dict[int, str] = {}

        self.shape_cache.clear()

        for feat in active:
            resolved = resolver.resolve_params(feat["params"])
            handler = executor._handlers.get(feat["feature_type"])
            if not handler:
                errors[feat["sequence"]] = f"Unknown feature type: {feat['feature_type']}"
                continue
            try:
                wp = handler(wp, resolved)
                last_good = feat["sequence"]
                # Cache the workplane state after this feature
                self.shape_cache[feat["sequence"]] = wp
            except Exception as e:
                errors[feat["sequence"]] = str(e)

        self.last_full_features = [dict(f) for f in features]

        status = "ok" if not errors else ("partial" if wp is not None else "failed")
        return {
            "shape": wp,
            "last_good_sequence": last_good,
            "feature_errors": errors,
            "rebuild_status": status,
        }

    def incremental_build(
        self,
        features: list[dict],
        resolver: ParameterResolver,
        dirty_from_seq: int,
    ) -> dict:
        """Rebuild only from dirty_from_seq forward, using cached shapes before it.

        Returns dict with shape, last_good_sequence, feature_errors, rebuild_status.
        """
        active = [f for f in features if not f.get("suppressed", False)]
        active.sort(key=lambda f: f["sequence"])

        if not active:
            return {
                "shape": None,
                "last_good_sequence": 0,
                "feature_errors": {},
                "rebuild_status": "failed",
            }

        # Find the cached shape just before dirty_from_seq
        wp: Optional[cq.Workplane] = None
        last_good = 0
        cached_seqs = sorted(self.shape_cache.keys())

        for seq in cached_seqs:
            if seq < dirty_from_seq:
                wp = self.shape_cache[seq]
                last_good = seq
            else:
                break

        # Invalidate cache entries at or after dirty_from_seq
        for seq in list(self.shape_cache.keys()):
            if seq >= dirty_from_seq:
                del self.shape_cache[seq]

        # Replay only features from dirty_from_seq onward
        executor = FeatureExecutor(resolver)
        errors: dict[int, str] = {}

        for feat in active:
            if feat["sequence"] < dirty_from_seq:
                continue
            resolved = resolver.resolve_params(feat["params"])
            handler = executor._handlers.get(feat["feature_type"])
            if not handler:
                errors[feat["sequence"]] = f"Unknown feature type: {feat['feature_type']}"
                continue
            try:
                wp = handler(wp, resolved)
                last_good = feat["sequence"]
                self.shape_cache[feat["sequence"]] = wp
            except Exception as e:
                errors[feat["sequence"]] = str(e)

        self.last_full_features = [dict(f) for f in features]

        status = "ok" if not errors else ("partial" if wp is not None else "failed")
        return {
            "shape": wp,
            "last_good_sequence": last_good,
            "feature_errors": errors,
            "rebuild_status": status,
        }

    def find_dirty_index(
        self,
        features: list[dict],
        changed_param_name: str | None = None,
        changed_feature_id: int | None = None,
    ) -> int:
        """Find the earliest sequence that needs rebuilding.

        If a named param changed: scan features for any that reference it in
        string-valued params (e.g. {"height": "H * 2"} when param "H" changed).
        If a feature's own params changed: return that feature's sequence.
        Returns the sequence number of the earliest dirty feature.
        """
        active = [f for f in features if not f.get("suppressed", False)]
        active.sort(key=lambda f: f["sequence"])

        if not active:
            return 0

        if changed_feature_id is not None:
            for feat in active:
                if feat.get("id") == changed_feature_id:
                    return feat["sequence"]
            # Fallback: rebuild everything
            return active[0]["sequence"]

        if changed_param_name is not None:
            for feat in active:
                # Check if any param value references the changed parameter name
                for _key, val in feat.get("params", {}).items():
                    if isinstance(val, str) and changed_param_name in val:
                        return feat["sequence"]
            # If no feature references it, still rebuild from the start
            # (the param might affect evaluation indirectly)
            return active[0]["sequence"]

        # Default: rebuild everything
        return active[0]["sequence"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_part_data(part_id: int) -> dict:
    """Load features and named parameters for a part from the database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Load features
            cur.execute(
                """
                SELECT id, feature_type, params, sequence, suppressed, name
                FROM features
                WHERE part_id = %s
                ORDER BY sequence
                """,
                (part_id,),
            )
            columns = [desc[0] for desc in cur.description]
            features = []
            for row in cur.fetchall():
                feat = dict(zip(columns, row))
                if isinstance(feat["params"], str):
                    feat["params"] = json.loads(feat["params"])
                features.append(feat)

            # Load named parameters
            cur.execute(
                """
                SELECT name, value
                FROM parameters
                WHERE part_id = %s
                """,
                (part_id,),
            )
            named_params = {}
            for row in cur.fetchall():
                named_params[row[0]] = float(row[1])

        return {"features": features, "named_params": named_params}
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/parametric/{part_id}")
async def parametric_ws(websocket: WebSocket, part_id: int):
    await websocket.accept()

    loop = asyncio.get_event_loop()

    # Load initial data from DB
    try:
        part_data = await loop.run_in_executor(None, _load_part_data, part_id)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Failed to load part {part_id}: {e}"})
        await websocket.close()
        return

    features = part_data["features"]
    named_params = part_data["named_params"]
    rebuilder = IncrementalRebuilder()

    # Do an initial full build
    try:
        resolver = ParameterResolver(named_params)

        def _initial_build():
            return rebuilder.full_build(features, resolver)

        result = await loop.run_in_executor(None, _initial_build)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Initial build failed: {e}"})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")
            quality = msg.get("quality")

            try:
                if msg_type == "param_update":
                    # Update a named parameter value
                    param_name = msg.get("param")
                    param_value = float(msg.get("value", 0))
                    named_params[param_name] = param_value

                    resolver = ParameterResolver(named_params)
                    dirty_seq = rebuilder.find_dirty_index(
                        features, changed_param_name=param_name
                    )

                    def _rebuild_param():
                        t0 = time.perf_counter()
                        result = rebuilder.incremental_build(features, resolver, dirty_seq)
                        rebuild_ms = (time.perf_counter() - t0) * 1000
                        return result, rebuild_ms

                    result, rebuild_ms = await loop.run_in_executor(None, _rebuild_param)
                    await _send_mesh_response(websocket, result, quality, rebuild_ms, loop, features, rebuilder.shape_cache)

                elif msg_type == "feature_param_update":
                    # Update a specific feature's params in memory
                    feature_id = msg.get("feature_id")
                    new_params = msg.get("params", {})

                    # Find and update the feature in our local list
                    for feat in features:
                        if feat.get("id") == feature_id:
                            feat["params"].update(new_params)
                            break

                    resolver = ParameterResolver(named_params)
                    dirty_seq = rebuilder.find_dirty_index(
                        features, changed_feature_id=feature_id
                    )

                    def _rebuild_feature():
                        t0 = time.perf_counter()
                        result = rebuilder.incremental_build(features, resolver, dirty_seq)
                        rebuild_ms = (time.perf_counter() - t0) * 1000
                        return result, rebuild_ms

                    result, rebuild_ms = await loop.run_in_executor(None, _rebuild_feature)
                    await _send_mesh_response(websocket, result, quality, rebuild_ms, loop, features, rebuilder.shape_cache)

                elif msg_type == "full_rebuild":
                    resolver = ParameterResolver(named_params)

                    def _full_rebuild():
                        t0 = time.perf_counter()
                        result = rebuilder.full_build(features, resolver)
                        rebuild_ms = (time.perf_counter() - t0) * 1000
                        return result, rebuild_ms

                    result, rebuild_ms = await loop.run_in_executor(None, _full_rebuild)
                    await _send_mesh_response(websocket, result, quality, rebuild_ms, loop, features, rebuilder.shape_cache)

                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}",
                    })

            except Exception as e:
                logger.exception("Error handling parametric WS message")
                await websocket.send_json({
                    "type": "error",
                    "message": str(e),
                })

    except WebSocketDisconnect:
        logger.info("Parametric WS disconnected for part %s", part_id)
    except Exception as e:
        logger.exception("Parametric WS unexpected error for part %s", part_id)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


async def _send_mesh_response(
    websocket: WebSocket,
    result: dict,
    quality: str | None,
    rebuild_ms: float,
    loop: asyncio.AbstractEventLoop,
    features: list[dict] | None = None,
    shape_cache: dict | None = None,
):
    """Tessellate the result shape and send the mesh over the WebSocket."""
    if result["shape"] is None:
        await websocket.send_json({
            "type": "error",
            "message": "Build produced no geometry",
            "feature_errors": result.get("feature_errors", {}),
            "rebuild_status": result.get("rebuild_status", "failed"),
        })
        return

    wp = result["shape"]
    features_snapshot = list(features) if features else []
    cache_snapshot = dict(shape_cache) if shape_cache else {}

    def _tessellate():
        t0 = time.perf_counter()
        mesh = shape_to_topo_mesh(wp, quality=quality)
        tess_ms = (time.perf_counter() - t0) * 1000

        # Enrich topo_faces with parametric bindings
        if features_snapshot:
            face_bindings = map_faces_to_features(features_snapshot, cache_snapshot, wp)
            if mesh.get("topo_faces") and face_bindings:
                for i, tf in enumerate(mesh["topo_faces"]):
                    if i < len(face_bindings):
                        tf["feature_id"] = face_bindings[i].get("feature_id")
                        tf["feature_type"] = face_bindings[i].get("feature_type")
                        tf["drag_param"] = face_bindings[i].get("drag_param")
                        tf["drag_axis"] = face_bindings[i].get("drag_axis")
                        tf["drag_scale"] = face_bindings[i].get("drag_scale", 1.0)

        return mesh, tess_ms

    try:
        mesh, tess_ms = await loop.run_in_executor(None, _tessellate)
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": f"Tessellation failed: {e}",
        })
        return

    await websocket.send_json({
        "type": "mesh_update",
        "vertices": mesh.get("vertices", []),
        "faces": mesh.get("faces", []),
        "topo_faces": mesh.get("topo_faces", []),
        "quality": quality or "preview",
        "rebuild_ms": round(rebuild_ms, 1),
        "tess_ms": round(tess_ms, 1),
        "rebuild_status": result.get("rebuild_status", "ok"),
        "feature_errors": result.get("feature_errors", {}),
    })
